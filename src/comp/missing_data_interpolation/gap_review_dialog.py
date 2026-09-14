"""Top-level Data > Missing Data and Interpolation gap review window --
the spec's "Second Pop-Up Window".

Per-gap navigation within one trajectory at a time: gaps are sorted
largest-to-smallest via gap_sort.sort_gaps() and driven by a single
"Selected Gap" combo plus Prev/Next -- selecting a gap on a different
vehicle reloads that vehicle's plots automatically. The
current vehicle's gaps are drawn at once (colored band + gap-number
label on all three plots); only the currently selected gap additionally
gets a black border. Selecting a gap never recomputes or rewrites any
gap's values -- it only moves the selection and loads that gap's
last-saved spline parameters into the side controls. Values only change
on View (preview, not saved) or Apply (saved), and only for the
currently selected gap.

Gap status colors (white/yellow/green from the spec, plus a light red
added for GAP_STATUS_UNFILLED:
    IMPUTED_AUTO         -- yellow region; rendered dashed until
                             manually reviewed
    IMPUTED_MANUAL       -- green region, solid (already merged into
                             the base trace, since Apply writes values 
                             into events)
    GAP_STATUS_UNFILLED  -- light red, needs attention

Add Points: click adds a point inside the
selected gap, Ctrl+click removes one, drag moves one, Enter commits and
returns here, Escape discards and returns here. Implemented via
plot/gap_point_editor.py's GapPointEditor, installed as an event filter
on whichever of the three plots matches the gap's current fitting base,
using plot/point_edit_utils.py's shared hit-testing/click-vs-drag
helpers. Only available when the selected gap's fitting base is
position, speed, or acceleration. Per spec the plots should also enlarge 
while Add Points is active.
"""

import json
import logging
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QMessageBox,
    QPushButton, QSplitter, QVBoxLayout, QDialog,
)

from comp.base_comp import IMPUTED_AUTO, IMPUTED_MANUAL
from comp.spline.spline_comp import BASE_LABELS, FITTING_BASES, allowed_k_options
from .gap_sort import sort_gaps
from .missing_data_interpolation_comp import (
    MissingDataInterpolationComp, GapResult, FillRow,
    GAP_STATUS_UNFILLED, write_fill_rows, write_gap_rows,
)

logger = logging.getLogger(__name__)

_STATUS_LABELS: Dict[str, str] = {
    IMPUTED_AUTO: "auto",
    IMPUTED_MANUAL: "manual",
    GAP_STATUS_UNFILLED: "unfilled",
}

_STATUS_COLORS: Dict[str, tuple] = {
    IMPUTED_AUTO:        (255, 255, 0, 80),   # yellow
    IMPUTED_MANUAL:      (0, 200, 0, 80),     # green
    GAP_STATUS_UNFILLED: (255, 100, 100, 80), # light red -- not in the spec's tri-color
                                               # scheme; flags a gap with no events row yet.
}


class GapReviewDialog(QDialog):
    """Per-gap review/refinement window -- top-level only, opened from
    GapConfigDialog._show_results."""

    def __init__(self, parent, db_connection: sqlite3.Connection, event_id: int,
                 gaps: List[GapResult]):
        super().__init__(parent)
        self.setWindowTitle("Missing Data and Interpolation — Gap Review")
        self.setModal(False)
        self.resize(900, 700)

        self._db_connection = db_connection
        self._event_id = event_id
        self._gaps: List[GapResult] = sort_gaps(gaps)
        self._gap_index = 0
        self._current_veh_id: Optional[int] = None
        self._has_speed = False
        self._has_accel = False

        self._traj_time = np.array([])
        self._traj_dist = np.array([])
        self._traj_speed: Optional[np.ndarray] = None
        self._traj_accel: Optional[np.ndarray] = None

        self._dist_regions: List[dict] = []
        self._speed_regions: List[dict] = []
        self._accel_regions: List[dict] = []

        self._preview_dist_curve = None
        self._preview_speed_curve = None
        self._preview_accel_curve = None

        self._added_points: List[tuple] = []
        self._point_editor = None   # GapPointEditor instance while "Add Points" is active

        self._build_ui()

        if self._gaps:
            self._load_gap(0)
        else:
            self._gap_combo.setEnabled(False)
            for w in (self._prev_btn, self._next_btn, self._add_points_btn,
                      self._view_btn, self._apply_btn):
                w.setEnabled(False)

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._gap_combo = QComboBox()
        self._gap_combo.currentIndexChanged.connect(self._on_gap_combo_changed)
        layout.addWidget(self._gap_combo)
        self._populate_gap_combo()

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._dist_plot = pg.PlotWidget(title="Distance Traveled (veh_dist_trav)")
        self._speed_plot = pg.PlotWidget(title="Speed (veh_speed)")
        self._accel_plot = pg.PlotWidget(title="Acceleration (veh_accel)")
        for plot in (self._dist_plot, self._speed_plot, self._accel_plot):
            plot.setBackground('w')
            splitter.addWidget(plot)
        self._speed_plot.setXLink(self._dist_plot)
        self._accel_plot.setXLink(self._dist_plot)
        layout.addWidget(splitter, stretch=1)

        self._dist_curve = self._dist_plot.plot(pen=pg.mkPen('b', width=1.5))
        self._speed_curve = self._speed_plot.plot(pen=pg.mkPen('b', width=1.5))
        self._accel_curve = self._accel_plot.plot(pen=pg.mkPen('b', width=1.5))

        controls = QHBoxLayout()
        controls.addLayout(self._build_param_controls())

        btn_col = QVBoxLayout()
        self._prev_btn = QPushButton("< Prev Gap")
        self._prev_btn.clicked.connect(self._on_prev)
        self._next_btn = QPushButton("Next Gap >")
        self._next_btn.clicked.connect(self._on_next)
        self._add_points_btn = QPushButton("Add Points...")
        self._add_points_btn.clicked.connect(self._on_add_points)
        self._view_btn = QPushButton("View")
        self._view_btn.clicked.connect(self._on_view)
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        for w in (self._prev_btn, self._next_btn, self._add_points_btn,
                  self._view_btn, self._apply_btn, close_btn):
            btn_col.addWidget(w)
        controls.addLayout(btn_col)

        layout.addLayout(controls)

    def _build_param_controls(self) -> QFormLayout:
        form = QFormLayout()

        self._gap_base_combo = QComboBox()
        for key in FITTING_BASES:
            self._gap_base_combo.addItem(BASE_LABELS[key], key)
        self._gap_base_combo.currentIndexChanged.connect(self._on_gap_base_changed)
        form.addRow("Fitting base:", self._gap_base_combo)

        self._gap_s_spin = QDoubleSpinBox()
        self._gap_s_spin.setRange(0.0, 1_000_000.0)
        self._gap_s_spin.setDecimals(4)
        form.addRow("Smoothness (s):", self._gap_s_spin)

        self._gap_k_combo = QComboBox()
        form.addRow("Degree (k):", self._gap_k_combo)

        return form

    def _on_gap_base_changed(self):
        key = self._gap_base_combo.currentData()
        if key is None:
            return
        options = allowed_k_options(key)
        prev_k = self._gap_k_combo.currentData()
        self._gap_k_combo.blockSignals(True)
        self._gap_k_combo.clear()
        for k in options:
            self._gap_k_combo.addItem(str(k), k)
        if prev_k in options:
            self._gap_k_combo.setCurrentIndex(options.index(prev_k))
        else:
            self._gap_k_combo.setCurrentIndex(0)
        self._gap_k_combo.blockSignals(False)

    # ── Gap combo / navigation ──────────────────────────────────────────

    def _populate_gap_combo(self):
        self._gap_combo.blockSignals(True)
        self._gap_combo.clear()
        for gap in self._gaps:
            self._gap_combo.addItem(
                f"Veh {gap.veh_id} — Gap {gap.gap_id} "
                f"({gap.duration:.2f}s, {gap.missing_rows} rows) "
                f"[{_STATUS_LABELS.get(gap.status, gap.status)}]"
            )
        if self._gaps:
            self._gap_combo.setCurrentIndex(self._gap_index)
        self._gap_combo.blockSignals(False)

    def _on_gap_combo_changed(self, index: int):
        if index >= 0 and index != self._gap_index:
            self._load_gap(index)

    def _on_prev(self):
        if self._gap_index > 0:
            self._load_gap(self._gap_index - 1)

    def _on_next(self):
        if self._gap_index < len(self._gaps) - 1:
            self._load_gap(self._gap_index + 1)

    def _update_nav_buttons(self):
        self._prev_btn.setEnabled(self._gap_index > 0)
        self._next_btn.setEnabled(self._gap_index < len(self._gaps) - 1)

    def _load_gap(self, index: int):
        self._gap_index = index
        gap = self._gaps[index]

        self._close_point_editor(commit=False)   # discard any in-progress edit for the old gap

        self._gap_combo.blockSignals(True)
        self._gap_combo.setCurrentIndex(index)
        self._gap_combo.blockSignals(False)

        self._load_trajectory(gap.veh_id)
        self._populate_param_controls(gap)
        self._added_points = self._decode_added_points(gap.added_points_json)
        self._clear_preview()
        self._redraw_regions()
        self._update_nav_buttons()

    @staticmethod
    def _decode_added_points(added_points_json: Optional[str]) -> List[tuple]:
        if not added_points_json:
            return []
        try:
            return [tuple(p) for p in json.loads(added_points_json)]
        except (ValueError, TypeError):
            logger.warning("GapReviewDialog: failed to parse added_points_json %r", added_points_json)
            return []

    def _populate_param_controls(self, gap: GapResult):
        self._gap_base_combo.blockSignals(True)
        idx = self._gap_base_combo.findData(gap.spline_fitting_base or "position")
        self._gap_base_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._gap_base_combo.blockSignals(False)
        self._on_gap_base_changed()   # rebuild k options for this base

        self._gap_s_spin.setValue(gap.spline_s if gap.spline_s is not None else 1.0)

        if gap.spline_k is not None:
            k_idx = self._gap_k_combo.findData(gap.spline_k)
            if k_idx >= 0:
                self._gap_k_combo.setCurrentIndex(k_idx)

    # ── Trajectory loading ───────────────────────────────────────────────

    def _load_trajectory(self, veh_id: int, force: bool = False):
        if not force and self._current_veh_id == veh_id:
            return
        self._current_veh_id = veh_id

        from utils import events_columns
        cols = events_columns(self._db_connection)
        self._has_speed = "veh_speed" in cols
        self._has_accel = "veh_accel" in cols

        select_cols = ["veh_time", "veh_dist_trav"]
        if self._has_speed:
            select_cols.append("veh_speed")
        if self._has_accel:
            select_cols.append("veh_accel")

        cursor = self._db_connection.cursor()
        cursor.execute(
            f"SELECT {', '.join(select_cols)} FROM events "
            f"WHERE event_id = ? AND veh_id = ? ORDER BY veh_time",
            (self._event_id, veh_id),
        )
        rows = cursor.fetchall()
        idx = {name: i for i, name in enumerate(select_cols)}

        def col(name):
            return np.array(
                [np.nan if r[idx[name]] is None else r[idx[name]] for r in rows],
                dtype=np.float64,
            )

        self._traj_time = col("veh_time")
        self._traj_dist = col("veh_dist_trav")
        self._traj_speed = col("veh_speed") if self._has_speed else None
        self._traj_accel = col("veh_accel") if self._has_accel else None

        # connect="finite" breaks the line at NaNs -- the only remaining
        # NaNs after a fill are GAP_STATUS_UNFILLED gaps (real gaps);
        # IMPUTED_AUTO/IMPUTED_MANUAL rows have real values now, so they
        # already render as a continuous line through those spans.
        self._dist_curve.setData(self._traj_time, self._traj_dist, connect="finite")
        if self._has_speed:
            self._speed_curve.setData(self._traj_time, self._traj_speed, connect="finite")
        else:
            self._speed_curve.setData([], [])
            self._speed_plot.setTitle("Speed (veh_speed not available in this dataset)")
        if self._has_accel:
            self._accel_curve.setData(self._traj_time, self._traj_accel, connect="finite")
        else:
            self._accel_curve.setData([], [])
            self._accel_plot.setTitle("Acceleration (veh_accel not available in this dataset)")

    # ── Gap region shading ───────────────────────────────────────────────

    def _redraw_regions(self):
        for plot, region_list in (
            (self._dist_plot, self._dist_regions),
            (self._speed_plot, self._speed_regions),
            (self._accel_plot, self._accel_regions),
        ):
            for item in region_list:
                plot.removeItem(item["region"])
                plot.removeItem(item["label"])
            region_list.clear()

        veh_gaps = [g for g in self._gaps if g.veh_id == self._current_veh_id]
        if not veh_gaps:
            return
        current_gap = self._gaps[self._gap_index]

        plots_and_data = [(self._dist_plot, self._dist_regions, self._traj_dist)]
        if self._has_speed:
            plots_and_data.append((self._speed_plot, self._speed_regions, self._traj_speed))
        if self._has_accel:
            plots_and_data.append((self._accel_plot, self._accel_regions, self._traj_accel))

        for plot, region_list, y_data in plots_and_data:
            finite = y_data[np.isfinite(y_data)]
            y_top = float(np.max(finite)) if finite.size else 0.0
            for gap in veh_gaps:
                is_selected = gap.gap_id == current_gap.gap_id
                region = pg.LinearRegionItem(
                    values=(gap.start_time, gap.end_time),
                    brush=_STATUS_COLORS.get(gap.status, (200, 200, 200, 80)),
                    pen=pg.mkPen('k', width=2) if is_selected else pg.mkPen(None),
                    movable=False,
                )
                region.setZValue(-10)
                plot.addItem(region)
                label = pg.TextItem(str(gap.gap_id), anchor=(0.5, 1.0), color='k')
                label.setPos((gap.start_time + gap.end_time) / 2, y_top)
                plot.addItem(label)
                region_list.append({"region": region, "label": label})

    # ── Preview (dashed line) ───────────────────────────────────────────

    def _clear_preview(self):
        for attr, plot in (
            ("_preview_dist_curve", self._dist_plot),
            ("_preview_speed_curve", self._speed_plot),
            ("_preview_accel_curve", self._accel_plot),
        ):
            item = getattr(self, attr)
            if item is not None:
                plot.removeItem(item)
                setattr(self, attr, None)

    def _draw_preview(self, fill_rows: List[FillRow]):
        self._clear_preview()
        times = [r.veh_time for r in fill_rows]
        dashed_pen = pg.mkPen('b', width=2, style=Qt.PenStyle.DashLine)

        self._preview_dist_curve = self._dist_plot.plot(
            times, [r.veh_dist_trav for r in fill_rows], pen=dashed_pen)
        if self._has_speed:
            self._preview_speed_curve = self._speed_plot.plot(
                times, [r.veh_speed for r in fill_rows], pen=dashed_pen)
        if self._has_accel:
            self._preview_accel_curve = self._accel_plot.plot(
                times, [r.veh_accel for r in fill_rows], pen=dashed_pen)

    def _compute_preview(self) -> Optional[List[FillRow]]:
        gap = self._gaps[self._gap_index]
        fitting_base = self._gap_base_combo.currentData()
        s = self._gap_s_spin.value()
        k = self._gap_k_combo.currentData()

        comp = MissingDataInterpolationComp()
        fill_rows = comp.compute_fill(
            self._db_connection, gap.event_id, gap.veh_id, [gap],
            fitting_base=fitting_base, s=s, k=k,
            added_points={gap.gap_id: self._added_points} if self._added_points else None,
        )
        return fill_rows or None

    # ── View / Apply ─────────────────────────────────────────────────────

    def _on_view(self):
        fill_rows = self._compute_preview()
        if not fill_rows:
            k = self._gap_k_combo.currentData()
            base_label = BASE_LABELS[self._gap_base_combo.currentData()]
            QMessageBox.warning(
                self, "Preview Failed",
                f"Not enough valid '{base_label}' points on this trajectory "
                f"to fit degree k={k}. Try a lower k or a different base.")
            return
        self._draw_preview(fill_rows)

    def _on_apply(self):
        fill_rows = self._compute_preview()
        if not fill_rows:
            k = self._gap_k_combo.currentData()
            base_label = BASE_LABELS[self._gap_base_combo.currentData()]
            QMessageBox.warning(
                self, "Apply Failed",
                f"Not enough valid '{base_label}' points on this trajectory "
                f"to fit degree k={k}. Try a lower k or a different base.")
            return

        gap = self._gaps[self._gap_index]
        gap.spline_fitting_base = self._gap_base_combo.currentData()
        gap.spline_s = self._gap_s_spin.value()
        gap.spline_k = self._gap_k_combo.currentData()
        gap.added_points_json = json.dumps(self._added_points) if self._added_points else None
        gap.status = IMPUTED_MANUAL

        try:
            write_fill_rows(self._db_connection, fill_rows, IMPUTED_MANUAL)
            write_gap_rows(self._db_connection, [gap])
            self._db_connection.commit()
        except sqlite3.Error as exc:
            self._db_connection.rollback()
            logger.exception("GapReviewDialog: failed to save gap %s", gap.gap_id)
            QMessageBox.critical(self, "Error", f"Failed to save this gap's fill:\n{exc}")
            return

        self._clear_preview()
        self._load_trajectory(gap.veh_id, force=True)   # solid line now includes the new values
        self._populate_gap_combo()                       # refresh this gap's status label
        self._redraw_regions()                            # region turns green

    # ── Add Points (stub) ───────────────────────────────────────────────

    _FITTING_BASE_PLOT_ATTR = {
        "position":     "_dist_plot",
        "speed":        "_speed_plot",
        "acceleration": "_accel_plot",
    }

    def _on_add_points(self):
        if self._point_editor is not None:
            return   # already editing

        gap = self._gaps[self._gap_index]
        fitting_base = self._gap_base_combo.currentData()
        plot_attr = self._FITTING_BASE_PLOT_ATTR.get(fitting_base)
        available = {"position": True, "speed": self._has_speed, "acceleration": self._has_accel}
        if plot_attr is None or not available.get(fitting_base, False):
            QMessageBox.information(
                self, "Add Points",
                f"Point editing isn't available for fitting base "
                f"'{BASE_LABELS.get(fitting_base, fitting_base)}' -- this "
                f"window only plots distance, speed, and acceleration. "
                f"Choose one of those as the fitting base to add points.")
            return

        from plot.gap_point_editor import GapPointEditor
        target_plot = getattr(self, plot_attr)
        self._point_editor = GapPointEditor(
            target_plot, x_min=gap.start_time, x_max=gap.end_time,
            initial_points=list(self._added_points),
        )
        self._point_editor.points_changed.connect(self._on_editor_points_changed)
        self._point_editor.finished.connect(self._on_editor_finished)
        self._set_editing_enabled(False)

    def _on_editor_points_changed(self, points: list):
        self._added_points = points
        fill_rows = self._compute_preview()
        if fill_rows:
            self._draw_preview(fill_rows)

    def _on_editor_finished(self, committed: bool):
        if committed:
            self._added_points = self._point_editor.points
        # else: leave self._added_points as it was before this session started
        self._point_editor.deleteLater()
        self._point_editor = None
        self._set_editing_enabled(True)
        self._on_editor_points_changed(self._added_points)   # redraw preview with final state

    def _set_editing_enabled(self, enabled: bool):
        for w in (self._gap_combo, self._prev_btn, self._next_btn, self._add_points_btn,
                  self._gap_base_combo, self._gap_s_spin, self._gap_k_combo,
                  self._view_btn, self._apply_btn):
            w.setEnabled(enabled)

    def _close_point_editor(self, commit: bool):
        if self._point_editor is not None:
            self._point_editor.finish(commit=commit)   # triggers _on_editor_finished