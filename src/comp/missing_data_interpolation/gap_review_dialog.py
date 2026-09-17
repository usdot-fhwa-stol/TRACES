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
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSplitter, QVBoxLayout, QDialog,
)

from comp.base_comp import IMPUTED_AUTO, IMPUTED_MANUAL
from comp.spline.spline_comp import BASE_LABELS, FITTING_BASES, allowed_k_options
from .gap_sort import sort_gaps
from .missing_data_interpolation_comp import (
    MissingDataInterpolationComp, GapResult, FillRow,
    GAP_STATUS_UNFILLED, write_fill_rows, write_gap_rows, read_gap_rows,
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

_PLOT_TITLES: Dict[str, str] = {
    "dist":  "Distance Traveled (m)",
    "speed": "Speed (m/s)",
    "accel": "Acceleration (m/s²)",
}


class GapReviewDialog(QDialog):
    """Per-gap review/refinement window -- top-level only, opened from
    GapConfigDialog._show_results."""

    def __init__(self, parent, db_connection: sqlite3.Connection, event_id: int):
        super().__init__(parent)
        self.setWindowTitle("Missing Data and Interpolation — Gap Review")
        self.setModal(True)
        self._size_and_center_dialog()

        self._db_connection = db_connection
        self._event_id = event_id
        self._gaps: List[GapResult] = sort_gaps(read_gap_rows(db_connection, event_id))
        self._gap_index = 0
        self._current_veh_id: Optional[int] = None
        self._has_speed = False
        self._has_accel = False

        self._traj_time = np.array([])
        self._traj_dist = np.array([])
        self._traj_speed: Optional[np.ndarray] = None
        self._traj_accel: Optional[np.ndarray] = None

        # Raw (unmasked) values -- source data for the per-gap dashed
        # "auto fill" overlay drawn by _redraw_auto_segments.
        self._traj_dist_raw = np.array([])
        self._traj_speed_raw: Optional[np.ndarray] = None
        self._traj_accel_raw: Optional[np.ndarray] = None

        self._dist_regions: Dict[int, dict] = {}
        self._speed_regions: Dict[int, dict] = {}
        self._accel_regions: Dict[int, dict] = {}
        self._selected_region_gap_id: Optional[int] = None

        self._preview_dist_curve = None
        self._preview_speed_curve = None
        self._preview_accel_curve = None

        self._auto_segment_items: Dict[int, dict] = {}   # gap_id -> {"dist":item, ...}

        self._added_points: List[tuple] = []
        self._point_editor = None
        self._pre_edit_splitter_sizes: Optional[List[int]] = None

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
        main_layout = QHBoxLayout(self)
    
        # ── Left side: three stacked plots, filling all available
        # horizontal space ────────────────────────────────────────────────
        left_layout = QVBoxLayout()
    
        self._plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self._dist_plot = pg.PlotWidget(title=_PLOT_TITLES["dist"])
        self._speed_plot = pg.PlotWidget(title=_PLOT_TITLES["speed"])
        self._accel_plot = pg.PlotWidget(title=_PLOT_TITLES["accel"])
        for plot in (self._dist_plot, self._speed_plot, self._accel_plot):
            plot.setBackground('w')
            self._plot_splitter.addWidget(plot)
        self._speed_plot.setXLink(self._dist_plot)
        self._accel_plot.setXLink(self._dist_plot)

        for i in range(1, self._plot_splitter.count()):
            self._plot_splitter.handle(i).setEnabled(False)

        left_layout.addWidget(self._plot_splitter, stretch=1)
    
        self._dist_curve = self._dist_plot.plot(pen=pg.mkPen('b', width=1.5))
        self._speed_curve = self._speed_plot.plot(pen=pg.mkPen('b', width=1.5))
        self._accel_curve = self._accel_plot.plot(pen=pg.mkPen('b', width=1.5))
    
        main_layout.addLayout(left_layout, stretch=1)
    
        # ── Right side: control panel, sized to its own contents ───────────
        right_layout = QVBoxLayout()
    
        right_layout.addWidget(QLabel("Selected Gap:"))
        self._gap_combo = QComboBox()
        self._gap_combo.currentIndexChanged.connect(self._on_gap_combo_changed)
        right_layout.addWidget(self._gap_combo)
        self._populate_gap_combo()
    
        right_layout.addLayout(self._build_param_controls())
    
        prev_next_row = QHBoxLayout()
        self._prev_btn = QPushButton("< Prev Gap")
        self._prev_btn.clicked.connect(self._on_prev)
        self._next_btn = QPushButton("Next Gap >")
        self._next_btn.clicked.connect(self._on_next)
        prev_next_row.addWidget(self._prev_btn, stretch=1)
        prev_next_row.addWidget(self._next_btn, stretch=1)
        right_layout.addLayout(prev_next_row)
    
        add_view_row = QHBoxLayout()
        self._add_points_btn = QPushButton("Add Points...")
        self._add_points_btn.clicked.connect(self._on_add_points)
        self._view_btn = QPushButton("View")
        self._view_btn.clicked.connect(self._on_view)
        add_view_row.addWidget(self._add_points_btn, stretch=1)
        add_view_row.addWidget(self._view_btn, stretch=1)
        right_layout.addLayout(add_view_row)
    
        apply_close_row = QHBoxLayout()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        apply_close_row.addWidget(self._apply_btn, stretch=1)
        apply_close_row.addWidget(close_btn, stretch=1)
        right_layout.addLayout(apply_close_row)
    
        right_layout.addStretch(1)
        main_layout.addLayout(right_layout)

    def _build_param_controls(self) -> QVBoxLayout:
        col = QVBoxLayout()
    
        col.addWidget(QLabel("Fitting base:"))
        self._gap_base_combo = QComboBox()
        for key in FITTING_BASES:
            self._gap_base_combo.addItem(BASE_LABELS[key], key)
            if key == "jerk":
                item = self._gap_base_combo.model().item(self._gap_base_combo.count() - 1)
                item.setEnabled(False)
                item.setToolTip(
                    "Jerk has no corresponding plot in this dialog and can't "
                    "be used as a fitting base here.")
        self._gap_base_combo.currentIndexChanged.connect(self._on_gap_base_changed)
        col.addWidget(self._gap_base_combo)
    
        col.addWidget(QLabel("Smoothness (s):"))
        self._gap_s_spin = QDoubleSpinBox()
        self._gap_s_spin.setRange(0.0, 1_000_000.0)
        self._gap_s_spin.setDecimals(4)
        col.addWidget(self._gap_s_spin)
    
        col.addWidget(QLabel("Degree (k):"))
        self._gap_k_combo = QComboBox()
        col.addWidget(self._gap_k_combo)
    
        return col

    def _size_and_center_dialog(self):
        """Size the dialog to 80% of the parent's width/height and center it
        over the parent, matching TimeSpeedDialog's sizing convention."""
        parent = self.parent()
        if parent:
            parent_geom = parent.frameGeometry()
            dialog_width = int(parent_geom.width() * 0.8)
            dialog_height = int(parent_geom.height() * 0.8)
    
            self.resize(dialog_width, dialog_height)
    
            parent_center = parent_geom.center()
            self_geom = self.frameGeometry()
            self_geom.moveCenter(parent_center)
            self.move(self_geom.topLeft())
        else:
            self.resize(900, 700)  # fallback, same as the old fixed default

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

    def _center_view_on_gap(self, gap: GapResult):
        """If gap isn't fully within the plot's current x-range, re-center
        the view on it at the same zoom level. Only the dist plot needs
        this -- speed/accel are XLinked to it (see _build_ui) and follow
        automatically. Left alone (no range change at all) when the gap is
        already visible, so ordinary navigation doesn't disturb a manual
        zoom/pan the user is mid-way through."""
        (x_min, x_max), _ = self._dist_plot.viewRange()
        if gap.start_time >= x_min and gap.end_time <= x_max:
            return
    
        half_width = (x_max - x_min) / 2
        gap_center = (gap.start_time + gap.end_time) / 2
        self._dist_plot.setXRange(gap_center - half_width, gap_center + half_width, padding=0)

    def _load_gap(self, index: int):
        self._gap_index = index
        gap = self._gaps[index]
    
        self._close_point_editor(commit=False)
    
        self._gap_combo.blockSignals(True)
        self._gap_combo.setCurrentIndex(index)
        self._gap_combo.blockSignals(False)
    
        self._load_trajectory(gap.veh_id)   # no-op if same vehicle; rebuilds internally if not
        self._populate_param_controls(gap)
        self._added_points = self._decode_added_points(gap.added_points_json)
        self._clear_preview()
        self._update_region_selection()      # cheap: just moves the highlight
        self._center_view_on_gap(gap)        # scroll into view if the gap isn't visible
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

        select_cols = ["veh_time", "veh_dist_trav", "imputed_status"]
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
        statuses = [r[idx["imputed_status"]] for r in rows]
        auto_mask = np.array([s == IMPUTED_AUTO for s in statuses], dtype=bool)

        self._traj_dist_raw = col("veh_dist_trav")
        self._traj_speed_raw = col("veh_speed") if self._has_speed else None
        self._traj_accel_raw = col("veh_accel") if self._has_accel else None

        # IMPUTED_AUTO rows are masked to NaN so the solid trace breaks
        # through them -- they render only via the dashed overlay
        # (_redraw_auto_segments) until a manual Apply makes them
        # IMPUTED_MANUAL, at which point this mask no longer hides them.
        self._traj_dist = np.where(auto_mask, np.nan, self._traj_dist_raw)
        self._traj_speed = (np.where(auto_mask, np.nan, self._traj_speed_raw)
                             if self._traj_speed_raw is not None else None)
        self._traj_accel = (np.where(auto_mask, np.nan, self._traj_accel_raw)
                             if self._traj_accel_raw is not None else None)

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

        self._rebuild_regions()
        self._redraw_auto_segments()

    # ── Gap region shading ───────────────────────────────────────────────

    def _rebuild_regions(self):
        """Full rebuild of every gap's shaded region + number label for the
        current vehicle. Expensive -- removes/recreates a pg item pair per
        gap, per plot, and re-scans the trajectory for each gap's local max
        -- so only call this when the underlying data actually changed (a
        new vehicle loaded, or Apply rewrote this vehicle's fill). Plain
        gap-to-gap navigation should use the cheap _update_region_selection()
        instead."""
        for plot, region_map in (
            (self._dist_plot, self._dist_regions),
            (self._speed_plot, self._speed_regions),
            (self._accel_plot, self._accel_regions),
        ):
            for item in region_map.values():
                plot.removeItem(item["region"])
                plot.removeItem(item["label"])
            region_map.clear()
    
        veh_gaps = [g for g in self._gaps if g.veh_id == self._current_veh_id]
        if not veh_gaps:
            return
    
        plots_and_data = [(self._dist_plot, self._dist_regions, self._traj_dist_raw)]
        if self._has_speed:
            plots_and_data.append((self._speed_plot, self._speed_regions, self._traj_speed_raw))
        if self._has_accel:
            plots_and_data.append((self._accel_plot, self._accel_regions, self._traj_accel_raw))
    
        for plot, region_map, y_data in plots_and_data:
            finite = y_data[np.isfinite(y_data)]
            y_fallback = float(np.max(finite)) if finite.size else 0.0
            for gap in veh_gaps:
                region = pg.LinearRegionItem(
                    values=(gap.start_time, gap.end_time),
                    brush=_STATUS_COLORS.get(gap.status, (200, 200, 200, 80)),
                    pen=pg.mkPen(None),   # selection border applied separately below
                    movable=False,
                )
                region.setZValue(-10)
                plot.addItem(region)
    
                gap_mask = (self._traj_time >= gap.start_time) & (self._traj_time <= gap.end_time)
                gap_finite = y_data[gap_mask & np.isfinite(y_data)]
                y_top = float(np.max(gap_finite)) if gap_finite.size else y_fallback
    
                label = pg.TextItem(str(gap.gap_id), anchor=(0.5, 1.0), color='k')
                label.setPos((gap.start_time + gap.end_time) / 2, y_top)
                plot.addItem(label)
                region_map[gap.gap_id] = {"region": region, "label": label}

    def _update_region_selection(self):
        """Move the highlighted border from the previously selected gap's
        region to the newly selected one. O(1) per plot -- no rebuild, no
        re-scanning the trajectory -- for use on ordinary gap navigation
        within the same vehicle."""
        new_gap = self._gaps[self._gap_index]
        prev_id = self._selected_region_gap_id
        for region_map in (self._dist_regions, self._speed_regions, self._accel_regions):
            if prev_id is not None and prev_id in region_map:
                self._set_region_pen(region_map[prev_id]["region"], pg.mkPen(None))
            if new_gap.gap_id in region_map:
                self._set_region_pen(region_map[new_gap.gap_id]["region"], pg.mkPen('k', width=2))
        self._selected_region_gap_id = new_gap.gap_id
    
    @staticmethod
    def _set_region_pen(region: pg.LinearRegionItem, pen):
        """LinearRegionItem has no setPen() of its own -- its visible border
        is its two boundary InfiniteLines, so the pen has to be set on each."""
        for line in region.lines:
            line.setPen(pen)

    def _clear_auto_segments(self):
        for items in self._auto_segment_items.values():
            for attr, plot in (("dist", self._dist_plot),
                                ("speed", self._speed_plot),
                                ("accel", self._accel_plot)):
                item = items.get(attr)
                if item is not None:
                    plot.removeItem(item)
        self._auto_segment_items = {}

    def _redraw_auto_segments(self):
        """Draw a dashed curve through every IMPUTED_AUTO gap on the
        current vehicle. Auto-filled spans are provisional by definition
        -- Task9.1_v2.1's N.B. ties dashed/yellow to 'interpolated
        automatically' and solid/green to a confirmed manual Apply -- so
        they render this way until the user reviews and applies them."""
        self._clear_auto_segments()
        if self._current_veh_id is None:
            return
        dashed_pen = pg.mkPen('b', width=2, style=Qt.PenStyle.DashLine)
        for gap in self._gaps:
            if gap.veh_id != self._current_veh_id or gap.status != IMPUTED_AUTO:
                continue
            mask = (self._traj_time >= gap.start_time) & (self._traj_time <= gap.end_time)
            if not np.any(mask):
                continue
            times = self._traj_time[mask]
            items = {"dist": self._dist_plot.plot(times, self._traj_dist_raw[mask], pen=dashed_pen)}
            if self._has_speed:
                items["speed"] = self._speed_plot.plot(times, self._traj_speed_raw[mask], pen=dashed_pen)
            if self._has_accel:
                items["accel"] = self._accel_plot.plot(times, self._traj_accel_raw[mask], pen=dashed_pen)
            self._auto_segment_items[gap.gap_id] = items

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

    def _boundary_value(self, raw_array: np.ndarray, t: float) -> Optional[float]:
        """Value of raw_array at the real trajectory timestamp t (a gap's
        start_time or end_time), or None if t isn't found (shouldn't happen
        for a valid gap)."""
        idx = np.searchsorted(self._traj_time, t)
        if idx < len(self._traj_time) and self._traj_time[idx] == t:
            return float(raw_array[idx])
        return None
    
    def _draw_preview(self, fill_rows: List[FillRow]):
        self._clear_preview()
        gap = self._gaps[self._gap_index]
        dashed_pen = pg.mkPen('r', width=2, style=Qt.PenStyle.DashLine)
    
        def build_series(fill_attr, raw_array):
            pts = [(r.veh_time, getattr(r, fill_attr)) for r in fill_rows]
            start_v = self._boundary_value(raw_array, gap.start_time)
            end_v = self._boundary_value(raw_array, gap.end_time)
            if start_v is not None:
                pts.insert(0, (gap.start_time, start_v))
            if end_v is not None:
                pts.append((gap.end_time, end_v))
            pts.sort(key=lambda p: p[0])
            return pts
    
        dist_pts = build_series("veh_dist_trav", self._traj_dist_raw)
        self._preview_dist_curve = self._dist_plot.plot(
            [p[0] for p in dist_pts], [p[1] for p in dist_pts], pen=dashed_pen)
        if self._has_speed:
            speed_pts = build_series("veh_speed", self._traj_speed_raw)
            self._preview_speed_curve = self._speed_plot.plot(
                [p[0] for p in speed_pts], [p[1] for p in speed_pts], pen=dashed_pen)
        if self._has_accel:
            accel_pts = build_series("veh_accel", self._traj_accel_raw)
            self._preview_accel_curve = self._accel_plot.plot(
                [p[0] for p in accel_pts], [p[1] for p in accel_pts], pen=dashed_pen)

    def _compute_preview(self, added_points: Optional[list] = None) -> Optional[List[FillRow]]:
        gap = self._gaps[self._gap_index]
        fitting_base = self._gap_base_combo.currentData()
        s = self._gap_s_spin.value()
        k = self._gap_k_combo.currentData()
        points = self._added_points if added_points is None else added_points
    
        comp = MissingDataInterpolationComp()
        fill_rows = comp.compute_fill(
            self._db_connection, gap.event_id, gap.veh_id, [gap],
            fitting_base=fitting_base, s=s, k=k,
            added_points={gap.gap_id: points} if points else None,
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
        self._update_region_selection()

    # ── Add Points ───────────────────────────────────────────────

    _FITTING_BASE_PLOT_ATTR = {
        "position":     "_dist_plot",
        "speed":        "_speed_plot",
        "acceleration": "_accel_plot",
    }

    def _expand_plot_for_editing(self, plot_attr: str):
        """Give the plot being edited most of the splitter's space and
        collapse the other two -- the spec's 'the graphs should be
        enlarged' when Add Points is active. Undone by _restore_plot_layout."""
        self._pre_edit_splitter_sizes = self._plot_splitter.sizes()
        order = ["_dist_plot", "_speed_plot", "_accel_plot"]
        total = sum(self._pre_edit_splitter_sizes) or 1
        self._plot_splitter.setSizes(
            [total if attr == plot_attr else 0 for attr in order])

    def _restore_plot_layout(self):
        """Undo _expand_plot_for_editing, restoring the normal three-plot view."""
        if self._pre_edit_splitter_sizes is not None:
            self._plot_splitter.setSizes(self._pre_edit_splitter_sizes)
            self._pre_edit_splitter_sizes = None

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

        self._expand_plot_for_editing(plot_attr)
        self._pre_edit_added_points = list(self._added_points)   # snapshot for Escape
    
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
        """Live preview only -- never writes self._added_points, so Escape
        can still roll back to the pre-session snapshot."""
        fill_rows = self._compute_preview(added_points=points)
        if fill_rows:
            self._draw_preview(fill_rows)
    
    def _on_editor_finished(self, committed: bool):
        self._added_points = self._point_editor.points if committed else self._pre_edit_added_points
        self._pre_edit_added_points = None
        self._point_editor.deleteLater()
        self._point_editor = None
        self._set_editing_enabled(True)
        self._restore_plot_layout()
        self._on_editor_points_changed(self._added_points)

    def _set_editing_enabled(self, enabled: bool):
        for w in (self._gap_combo, self._add_points_btn,
                  self._gap_base_combo, self._gap_s_spin, self._gap_k_combo,
                  self._view_btn, self._apply_btn):
            w.setEnabled(enabled)
        if enabled:
            self._update_nav_buttons()   # respects gap_index boundaries instead of forcing True
        else:
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)

    def _close_point_editor(self, commit: bool):
        if self._point_editor is not None:
            self._point_editor.finish(commit=commit)   # triggers _on_editor_finished