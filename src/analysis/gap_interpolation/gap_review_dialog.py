"""Top-level Data > Gap Interpolation gap review window --
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
on View (preview, not saved), Apply (saved), Remove or Delete, and only
for the currently selected gap.

Remove and Delete (both ask for confirmation):
    Remove  deletes the gap's filled events rows and keeps its gaps
            record with status GAP_STATUS_REMOVED, so automatic reruns
            leave it unfilled (unless the config dialog's "Include
            manual edits" is checked). It stays in the list, labeled
            [removed] with a grey band, and can still be filled here
            with Apply, which makes it a manual gap.
    Delete  deletes the gap's gaps record and its filled events rows, so
            the next automatic run detects and fills it as a new gap.
            It leaves the list and the next gap is selected.

All writes go to the connection this window was opened with -- the
write target GapConfigDialog resolved -- so there is no path field here.

data_changed(event_id) is emitted once, when the window closes, if the
events table changed: by Apply, Remove or Delete here, or (changed=True
at construction) by GapConfigDialog just before opening this window.
Plots of the event elsewhere (TimeSpacePlotWidget, via MainWindow)
reload on it; this module can't reach them directly.

Plots are a KinematicStackedPlotWidget (distance, speed, acceleration),
so number keys set how many plots are in view, the legend toggles
series, and every plot has its own time axis. y fits each plot's full
data range (y_fit="all"), so zooming time to a gap never rescales y.

Selecting a gap -- on first open, with Prev/Next, from the Selected Gap
list, or after Delete moves to the next one -- zooms to it: its region
is centered and spans 50% of the plot width (25% margin each side). Only
x is zoomed; each plot's y-axis stays auto-ranged to its full data range.
Apply, View and Remove keep the current view.

Gap status colors (white/yellow/green from the spec, plus a light red
added for GAP_STATUS_UNFILLED and grey for GAP_STATUS_REMOVED), from
plot_style.region_brush:
    IMPUTED_AUTO         -- yellow region; its values are drawn as the
                             short-dashed "Auto fill (unreviewed)" series
                             until manually reviewed

Series colors follow plot_style's design language: measured and auto-fill
data in each plot's kinematic color, the View/Add Points preview purple
dashed (a proposal, not yet saved). All three get a thin white halo
(plot_style.HALO_COLOR) so they stay readable over the yellow / green /
red gap bands; on the white background the halo is invisible.
    IMPUTED_MANUAL       -- green region, solid (already merged into
                             the measured trace, since Apply writes
                             values into events)
    GAP_STATUS_UNFILLED  -- light red, needs attention
    GAP_STATUS_REMOVED   -- grey, fill removed on purpose; the trace
                             breaks across it like any unfilled gap

Add Points: click adds a point inside the selected gap, Ctrl+click
removes one, drag moves one, Enter commits and returns here, Escape
discards and returns here. Implemented via ui_kit/gap_point_editor.py's
GapPointEditor, installed as an event filter on whichever of the three
plots matches the gap's current fitting base. Only available when the
selected gap's fitting base is position, speed, or acceleration.

While Add Points is active the stack is locked (lock_to) to the
fitting-base plot and the next derived plot (distance -> speed,
speed -> acceleration), so the effect of added points on the derived
parameter is visible live; acceleration shows alone since there is no
jerk plot. Points can only be added on the fitting-base plot -- the
derived plot is view-only. Leaving Add Points restores the previous
view.
"""

import json
import logging
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from ..base_comp import IMPUTED_AUTO, IMPUTED_MANUAL
from ..spline.spline_comp import BASE_LABELS, FITTING_BASES, allowed_k_options
from ui_kit.kinematic_stacked_plot_widget import KinematicStackedPlotWidget
from ui_kit.plot_style import HALO_COLOR, Role, region_brush
from utils import events_columns, event_time_step
from .gap_sort import sort_gaps
from .gap_interpolation_comp import (
    GapInterpolationComp, GapResult, FillRow,
    GAP_STATUS_REMOVED, GAP_STATUS_UNFILLED,
    clear_gap_fill, delete_gaps, write_fill_rows, write_gap_rows, read_gap_rows,
)

logger = logging.getLogger(__name__)

_STATUS_LABELS: Dict[str, str] = {
    IMPUTED_AUTO: "auto",
    IMPUTED_MANUAL: "manual",
    GAP_STATUS_UNFILLED: "unfilled",
    GAP_STATUS_REMOVED: "removed",
}

# Gap status -> plot_style region brush name. Light red for UNFILLED is
# not in the spec's tri-color scheme; it flags a gap with no events row yet.
# "removed" is grey (plot_style falls back to its grey "default" brush
# until it defines a "removed" entry of its own).
_STATUS_REGION: Dict[str, str] = {
    IMPUTED_AUTO: "auto",
    IMPUTED_MANUAL: "manual",
    GAP_STATUS_UNFILLED: "unfilled",
    GAP_STATUS_REMOVED: "removed",
}

_KEYS = ("dist", "speed", "accel")

# Plot key -> events column (also the FillRow attribute of the same name).
_COLUMN: Dict[str, str] = {
    "dist":  "veh_dist_trav",
    "speed": "veh_speed",
    "accel": "veh_accel",
}

# Spline fitting base -> plot key. Jerk has no plot here.
_FITTING_BASE_KEY: Dict[str, str] = {
    "position":     "dist",
    "speed":        "speed",
    "acceleration": "accel",
}

# The next derived parameter's plot, shown read-only beneath the
# fitting-base plot while Add Points is active. Acceleration has none.
_DERIVED_KEY: Dict[str, Optional[str]] = {
    "dist":  "speed",
    "speed": "accel",
    "accel": None,
}

class GapReviewDialog(QDialog):
    """Per-gap review/refinement window -- top-level only, opened from
    GapConfigDialog._show_results (or its Skip button)."""

    # Emitted once on close, with the event ID, if the events table
    # changed (see module docstring).
    data_changed = pyqtSignal(int)

    def __init__(self, parent, db_connection: sqlite3.Connection, event_id: int,
                 changed: bool = False):
        """changed: the opener already wrote to this event (e.g. Apply's
        automatic fills), so data_changed is due on close regardless."""
        super().__init__(parent)
        self.setWindowTitle("Gap Interpolation — Gap Review")
        self.setModal(True)
        self._size_and_center_dialog()

        self._db_connection = db_connection
        self._event_id = event_id
        self._changed = changed
        self._gaps: List[GapResult] = sort_gaps(read_gap_rows(db_connection, event_id))
        self._gap_index = 0
        self._current_veh_id: Optional[int] = None

        cols = events_columns(db_connection)
        self._time_step = event_time_step(db_connection, event_id)
        self._has_speed = "veh_speed" in cols
        self._has_accel = "veh_accel" in cols

        self._traj_time = np.array([])
        # Raw (unmasked) values per plot key; None for absent columns.
        # Source for gap labels, the auto-fill series and preview boundaries.
        self._raw: Dict[str, Optional[np.ndarray]] = {k: None for k in _KEYS}

        # plot key -> gap_id -> {"region": LinearRegionItem, "label": TextItem}
        self._regions: Dict[str, Dict[int, dict]] = {k: {} for k in _KEYS}
        self._selected_region_gap_id: Optional[int] = None

        self._added_points: List[tuple] = []
        self._pre_edit_added_points: Optional[List[tuple]] = None
        self._point_editor = None
        self._companion_key: Optional[str] = None
        self._companion_mouse_state: Optional[List[bool]] = None
        self._initial_zoom_done = False

        self._build_ui()
        self.finished.connect(self._on_finished)

        if self._gaps:
            self._load_gap(0)
        else:
            self._set_no_gaps_state()

    def _on_finished(self, _result: int):
        """Every way of closing (Close, Escape, the title bar) ends in
        QDialog.done(), which emits finished: report changes to the
        event's other views, once. A slot on finished rather than a
        Python done() override, so it can't be bypassed by C++ dispatch."""
        self._close_point_editor(commit=False)
        if self._changed:
            self._changed = False
            logger.info("GapReviewDialog: event %s data changed; notifying", self._event_id)
            self.data_changed.emit(self._event_id)

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QHBoxLayout(self)

        # ── Left side: stacked plots, filling all available width ──────
        self._plots = KinematicStackedPlotWidget(
            kinematics=_KEYS,
            available={"speed": self._has_speed, "accel": self._has_accel},
            y_fit="all", time_step=self._time_step)
        main_layout.addWidget(self._plots, stretch=1)

        self._measured = self._plots.add_series(
            "Measured", Role.MEASURED, outline=HALO_COLOR)
        self._auto_fill = self._plots.add_series(
            "Auto fill (unreviewed)", Role.AUTO_FILL, outline=HALO_COLOR)
        self._preview = self._plots.add_series(
            "Preview", Role.PREVIEW, outline=HALO_COLOR)

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

        remove_delete_row = QHBoxLayout()
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setToolTip(
            "Remove this gap's filled values and keep it unfilled: automatic "
            "reruns leave it alone unless “Include manual edits” is "
            "checked. It can still be filled here with Apply.")
        self._remove_btn.clicked.connect(self._on_remove)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setToolTip(
            "Delete this gap's record and filled values, so the next automatic "
            "run fills it again as a new gap.")
        self._delete_btn.clicked.connect(self._on_delete)
        remove_delete_row.addWidget(self._remove_btn, stretch=1)
        remove_delete_row.addWidget(self._delete_btn, stretch=1)
        right_layout.addLayout(remove_delete_row)

        apply_close_row = QHBoxLayout()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        apply_close_row.addWidget(self._apply_btn, stretch=1)
        apply_close_row.addWidget(close_btn, stretch=1)
        right_layout.addLayout(apply_close_row)

        right_layout.addStretch(1)
        self._right_panel = QWidget()
        self._right_panel.setLayout(right_layout)
        main_layout.addWidget(self._right_panel)

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

    def _update_action_buttons(self):
        """Remove only applies to a gap that isn't already removed; Delete
        to any gap."""
        has_gap = bool(self._gaps)
        self._delete_btn.setEnabled(has_gap)
        self._remove_btn.setEnabled(
            has_gap and self._gaps[self._gap_index].status != GAP_STATUS_REMOVED)

    def _set_no_gaps_state(self):
        """No gaps recorded (none detected, or the last one was deleted)."""
        self._gap_combo.setEnabled(False)
        for w in (self._prev_btn, self._next_btn, self._add_points_btn,
                  self._view_btn, self._apply_btn, self._remove_btn, self._delete_btn,
                  self._gap_base_combo, self._gap_s_spin, self._gap_k_combo):
            w.setEnabled(False)

    def showEvent(self, event):
        """Zoom to the first selected gap the first time the dialog is
        shown. Done here rather than in __init__ so the plots have real
        geometry: the linked x-axes map ranges through screen geometry,
        and the y auto-range is otherwise still pending."""
        super().showEvent(event)
        if not self._initial_zoom_done and self._gaps:
            self._initial_zoom_done = True
            self._zoom_to_gap(self._gaps[self._gap_index])

    def _zoom_to_gap(self, gap: GapResult):
        """Set the x-range so the gap's highlighted region is centered and
        occupies 50% of the plot width, leaving a 25% margin on each side.
        Only x is changed; y remains in full-range auto-range.

        With gap width w, the total span is 2w, so each margin is w/2."""
        width = gap.end_time - gap.start_time
        if width <= 0:
            width = 1.0   # defensive fallback; a valid gap always has width > 0
        margin = width / 2.0
        self._plots.set_x_range(gap.start_time - margin, gap.end_time + margin)
        # setXRange disables only x auto-range; re-assert y for safety.
        for pw in self._plots.plots.values():
            pw.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

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
        self._zoom_to_gap(gap)               # centered, 50% of the width, like on open
        self._update_nav_buttons()
        self._update_action_buttons()

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
            self._gap_k_combo.setCurrentIndex(k_idx if k_idx >= 0 else 0)

    # ── Trajectory loading ───────────────────────────────────────────────

    def _load_trajectory(self, veh_id: int, force: bool = False):
        if not force and self._current_veh_id == veh_id:
            return
        self._current_veh_id = veh_id

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

        self._raw = {
            "dist":  col("veh_dist_trav"),
            "speed": col("veh_speed") if self._has_speed else None,
            "accel": col("veh_accel") if self._has_accel else None,
        }

        # IMPUTED_AUTO rows are masked to NaN so the measured trace breaks
        # through them -- they render only as the auto-fill series
        # (_redraw_auto_segments) until a manual Apply makes them
        # IMPUTED_MANUAL, at which point this mask no longer hides them.
        masked = {k: (None if v is None else np.where(auto_mask, np.nan, v))
                  for k, v in self._raw.items()}
        self._plots.set_data(self._measured, self._traj_time, masked)

        self._rebuild_regions()
        self._redraw_auto_segments()

    # ── Gap region shading ───────────────────────────────────────────────

    def _rebuild_regions(self):
        """Full rebuild of every gap's shaded region + number label for the
        current vehicle. Expensive -- removes/recreates a pg item pair per
        gap, per plot, and re-scans the trajectory for each gap's local max
        -- so only call this when the underlying data actually changed (a
        new vehicle loaded, or Apply / Remove / Delete rewrote this
        vehicle's gaps). Plain gap-to-gap navigation should use the cheap
        _update_region_selection() instead. Rebuilt regions carry no
        selection border, so the next _update_region_selection() starts
        from scratch."""
        for key, region_map in self._regions.items():
            for item in region_map.values():
                self._plots.remove_item(key, item["region"])
                self._plots.remove_item(key, item["label"])
            region_map.clear()
        self._selected_region_gap_id = None

        veh_gaps = [g for g in self._gaps if g.veh_id == self._current_veh_id]
        if not veh_gaps:
            return

        for key in _KEYS:
            y_data = self._raw.get(key)
            if y_data is None or not self._plots.available(key):
                continue
            region_map = self._regions[key]
            finite = y_data[np.isfinite(y_data)]
            y_fallback = float(np.max(finite)) if finite.size else 0.0
            for gap in veh_gaps:
                region = pg.LinearRegionItem(
                    values=(gap.start_time, gap.end_time),
                    brush=region_brush(_STATUS_REGION.get(gap.status, "default")),
                    pen=pg.mkPen(None),   # selection border applied separately below
                    movable=False,
                )
                region.setZValue(-10)
                self._plots.add_item(key, region)

                gap_mask = (self._traj_time >= gap.start_time) & (self._traj_time <= gap.end_time)
                gap_finite = y_data[gap_mask & np.isfinite(y_data)]
                y_top = float(np.max(gap_finite)) if gap_finite.size else y_fallback

                label = pg.TextItem(str(gap.gap_id), anchor=(0.5, 1.0), color='k')
                label.setPos((gap.start_time + gap.end_time) / 2, y_top)
                self._plots.add_item(key, label)
                region_map[gap.gap_id] = {"region": region, "label": label}

    def _update_region_selection(self):
        """Move the highlighted border from the previously selected gap's
        region to the newly selected one. O(1) per plot -- no rebuild, no
        re-scanning the trajectory -- for use on ordinary gap navigation
        within the same vehicle."""
        new_gap = self._gaps[self._gap_index]
        prev_id = self._selected_region_gap_id
        for region_map in self._regions.values():
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

    def _redraw_auto_segments(self):
        """Draw every IMPUTED_AUTO gap on the current vehicle as the
        short-dashed auto-fill series. Auto-filled spans are provisional
        by definition -- Task9.1_v2.1's N.B. ties dashed/yellow to
        'interpolated automatically' and solid/green to a confirmed manual
        Apply -- so they render this way until the user reviews and
        applies them. Each span includes its two measured boundary points
        so the dashes join the measured trace."""
        if self._current_veh_id is None or self._traj_time.size == 0:
            self._plots.clear_data(self._auto_fill)
            return
        mask = np.zeros(self._traj_time.shape, dtype=bool)
        for gap in self._gaps:
            if gap.veh_id == self._current_veh_id and gap.status == IMPUTED_AUTO:
                mask |= (self._traj_time >= gap.start_time) & (self._traj_time <= gap.end_time)
        if not mask.any():
            self._plots.clear_data(self._auto_fill)
            return
        values = {k: (None if v is None else np.where(mask, v, np.nan))
                  for k, v in self._raw.items()}
        self._plots.set_data(self._auto_fill, self._traj_time, values)

    # ── Preview (dashed line) ───────────────────────────────────────────

    def _clear_preview(self):
        self._plots.clear_data(self._preview)

    def _boundary_value(self, raw_array: np.ndarray, t: float) -> Optional[float]:
        """Value of raw_array at the real trajectory timestamp t (a gap's
        start_time or end_time), or None if t isn't found (shouldn't happen
        for a valid gap)."""
        idx = np.searchsorted(self._traj_time, t)
        if idx < len(self._traj_time) and self._traj_time[idx] == t:
            return float(raw_array[idx])
        return None

    def _draw_preview(self, fill_rows: List[FillRow]):
        """Preview series through the selected gap: its start boundary,
        the fill rows, and its end boundary, on every plot. A missing
        boundary or value is NaN, which just shortens the dashed line."""
        gap = self._gaps[self._gap_index]
        times = np.array(
            [gap.start_time] + [r.veh_time for r in fill_rows] + [gap.end_time],
            dtype=np.float64)
        order = np.argsort(times, kind="stable")

        values: Dict[str, Optional[np.ndarray]] = {}
        for key in _KEYS:
            raw = self._raw.get(key)
            if raw is None:
                values[key] = None
                continue
            attr = _COLUMN[key]
            ys = ([self._boundary_value(raw, gap.start_time)]
                  + [getattr(r, attr, None) for r in fill_rows]
                  + [self._boundary_value(raw, gap.end_time)])
            values[key] = np.array(
                [np.nan if y is None else y for y in ys], dtype=np.float64)[order]
        self._plots.set_data(self._preview, times[order], values)

    def _compute_preview(self, added_points: Optional[list] = None) -> Optional[List[FillRow]]:
        gap = self._gaps[self._gap_index]
        fitting_base = self._gap_base_combo.currentData()
        s = self._gap_s_spin.value()
        k = self._gap_k_combo.currentData()
        points = self._added_points if added_points is None else added_points

        comp = GapInterpolationComp()
        fill_rows = comp.compute_fill(
            self._db_connection, gap.event_id, gap.veh_id, [gap],
            fitting_base=fitting_base, s=s, k=k,
            added_points={gap.gap_id: points} if points else None,
            time_step=self._time_step,
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

        self._changed = True
        self._after_gap_changed(gap.veh_id)

    # ── Remove / Delete ──────────────────────────────────────────────────

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _on_remove(self):
        gap = self._gaps[self._gap_index]
        if gap.status == GAP_STATUS_REMOVED:
            return
        if not self._confirm(
                "Remove Gap Fill?",
                f"Remove the fill for gap {gap.gap_id} on vehicle {gap.veh_id}?\n\n"
                f"Its filled values are deleted and the gap is kept unfilled: "
                f"automatic reruns leave it alone unless “Include manual "
                f"edits” is checked. You can still fill it here with Apply."):
            return

        # The spline parameters and added points are kept, so a later
        # manual Apply starts from where this gap was left.
        previous_status = gap.status
        gap.status = GAP_STATUS_REMOVED
        try:
            clear_gap_fill(self._db_connection, gap)
            write_gap_rows(self._db_connection, [gap])
            self._db_connection.commit()
        except sqlite3.Error as exc:
            self._db_connection.rollback()
            gap.status = previous_status
            logger.exception("GapReviewDialog: failed to remove gap %s", gap.gap_id)
            QMessageBox.critical(self, "Error", f"Failed to remove this gap's fill:\n{exc}")
            return

        logger.info("GapReviewDialog: removed fill of veh=%s gap=%s", gap.veh_id, gap.gap_id)
        self._changed = True
        self._after_gap_changed(gap.veh_id)

    def _on_delete(self):
        gap = self._gaps[self._gap_index]
        if not self._confirm(
                "Delete Gap?",
                f"Delete gap {gap.gap_id} on vehicle {gap.veh_id}?\n\n"
                f"Its record and any filled values are deleted, so the next "
                f"automatic run fills it again as a new gap."):
            return

        try:
            delete_gaps(self._db_connection, [gap])
            self._db_connection.commit()
        except sqlite3.Error as exc:
            self._db_connection.rollback()
            logger.exception("GapReviewDialog: failed to delete gap %s", gap.gap_id)
            QMessageBox.critical(self, "Error", f"Failed to delete this gap:\n{exc}")
            return

        logger.info("GapReviewDialog: deleted veh=%s gap=%s", gap.veh_id, gap.gap_id)
        self._changed = True
        self._close_point_editor(commit=False)
        del self._gaps[self._gap_index]
        self._clear_preview()

        # Reload the vehicle the deleted gap was on -- its fill and region
        # are gone -- then select the gap that took its place in the list.
        self._load_trajectory(gap.veh_id, force=True)
        if not self._gaps:
            self._gap_index = 0
            self._populate_gap_combo()
            self._set_no_gaps_state()
            return
        self._gap_index = min(self._gap_index, len(self._gaps) - 1)
        self._populate_gap_combo()
        self._load_gap(self._gap_index)

    def _after_gap_changed(self, veh_id: int):
        """Refresh after the selected gap's fill or status was rewritten."""
        self._clear_preview()
        self._load_trajectory(veh_id, force=True)   # trace, regions and auto-fill series
        self._populate_gap_combo()                  # refresh this gap's status label
        self._update_region_selection()
        self._update_action_buttons()

    # ── Add Points ───────────────────────────────────────────────────────

    def _editing_keys(self, plot_key: str) -> List[str]:
        """[fitting-base plot, derived plot] -- or just the fitting-base
        plot when there's no derived plot or its column isn't in the
        dataset (e.g. no veh_speed)."""
        keys = [plot_key]
        derived = _DERIVED_KEY.get(plot_key)
        if derived is not None and self._plots.available(derived):
            keys.append(derived)
        return keys

    def _expand_plot_for_editing(self, plot_key: str):
        """Lock the stack to the fitting-base plot plus the next derived
        plot -- the spec's 'the graphs should be enlarged' when Add Points
        is active -- and hide the control panel. The derived plot is
        view-only: GapPointEditor is installed only on the fitting-base
        plot, and the derived plot's pan/zoom is disabled here so it can't
        shift the shared (linked) time axis mid-edit. Undone by
        _restore_plot_layout."""
        keys = self._editing_keys(plot_key)
        self._plots.lock_to(keys)
        self._right_panel.setVisible(False)

        self._companion_key = keys[1] if len(keys) > 1 else None
        if self._companion_key is not None:
            vb = self._plots.plot(self._companion_key).getViewBox()
            self._companion_mouse_state = list(vb.state['mouseEnabled'])
            vb.setMouseEnabled(x=False, y=False)

    def _restore_plot_layout(self):
        """Undo _expand_plot_for_editing: restore the previous view and
        the derived plot's pan/zoom."""
        self._plots.unlock()
        if self._companion_key is not None:
            vb = self._plots.plot(self._companion_key).getViewBox()
            if self._companion_mouse_state is not None:
                vb.setMouseEnabled(x=self._companion_mouse_state[0],
                                   y=self._companion_mouse_state[1])
            self._companion_key = None
            self._companion_mouse_state = None
        self._right_panel.setVisible(True)

    def _on_add_points(self):
        if self._point_editor is not None:
            return   # already editing

        gap = self._gaps[self._gap_index]
        fitting_base = self._gap_base_combo.currentData()
        plot_key = _FITTING_BASE_KEY.get(fitting_base)
        if plot_key is None or not self._plots.available(plot_key):
            QMessageBox.information(
                self, "Add Points",
                f"Point editing isn't available for fitting base "
                f"'{BASE_LABELS.get(fitting_base, fitting_base)}' -- this "
                f"window only plots distance, speed, and acceleration. "
                f"Choose one of those as the fitting base to add points.")
            return

        self._expand_plot_for_editing(plot_key)
        self._pre_edit_added_points = list(self._added_points)   # snapshot for Escape

        from ui_kit.gap_point_editor import GapPointEditor
        self._point_editor = GapPointEditor(
            self._plots.plot(plot_key), x_min=gap.start_time, x_max=gap.end_time,
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
            self._update_nav_buttons()     # respects gap_index boundaries instead of forcing True
            self._update_action_buttons()  # Remove depends on the gap's status
        else:
            for w in (self._prev_btn, self._next_btn, self._remove_btn, self._delete_btn):
                w.setEnabled(False)

    def _close_point_editor(self, commit: bool):
        if self._point_editor is not None:
            self._point_editor.finish(commit=commit)   # triggers _on_editor_finished
