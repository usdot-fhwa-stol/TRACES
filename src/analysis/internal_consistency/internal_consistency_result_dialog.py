"""Right-click result dialog for Module 4 — Internal Consistency.

Layout (two horizontal sections in a QSplitter, split 50 / 50):
  Left   — KinematicStackedPlotWidget: speed, acceleration, jerk
  Right  — vertical QSplitter:
               Top    — single-row statistics summary table (current trajectory)
               Bottom — scrollable raw data table (Figure 12 layout)

Footer spanning both sections:
  [← Prev]  [Trajectory dropdown]  [Next →]  [Save Plots]  [Close]

The dialog opens at 80% of the main window's width and height, centered
over it. Changing trajectory via dropdown or arrows reloads all sections
and resets each plot's view range.

Each plot uses its kinematic colour from plot_style (speed bluish green,
acceleration blue, jerk amber); on each plot the measured value is solid
and the value calculated from the next-lower kinematic is dashed in the
same colour.

"Measured" and "Calculated" are two linked series spanning all three
plots, so clicking either in any legend shows/hides it on all three.

Everything else -- linked time axis, y auto-fit to the visible window,
single-axis wheel zoom, the "A" button resetting all plots, number keys
and scrolling for plots in view, the shared hover cursor with per-plot
value labels -- comes from KinematicStackedPlotWidget. Clicking a raw
table row places the cursor at that row's time on all three plots.

Save Plots writes an interactive HTML page (ui_kit.plot_export.build_plot_html
with the widget's export_spec()): canvas plots with the same series,
colours, legend toggles, linked time axis, zoom and hover values.
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ui_kit.plot_export import build_plot_html
from ui_kit.kinematic_stacked_plot_widget import KinematicStackedPlotWidget
from ui_kit.plot_style import Role
from .internal_consistency_comp import InternalConsistencyResult
from .internal_consistency_stats_dialog import (
    _compute_stats, _StatRow, _COLUMNS as _STAT_COLUMNS, _isnan, NAN,
)

logger = logging.getLogger(__name__)

# ── Column definitions ─────────────────────────────────────────────────────

# Raw data table columns: (header_abbrev, full_name, attribute, format)
_RAW_COLUMNS = [
    ("Event ID",   "Event ID",                        "event_id",      "{:d}"),
    ("Veh ID",     "Vehicle ID",                      "veh_id",        "{:d}"),
    ("Time",       "Time (s)",                        "veh_time",      "{:.4f}"),
    ("Dist",       "Distance Traveled (m)",           "veh_dist_trav", "{:.4f}"),
    ("Speed",      "Speed (m/s)",                     "veh_speed",     "{:.4f}"),
    ("Speed Cal",  "Calculated Speed (m/s)",          "veh_speed_cal", "{:.4f}"),
    ("Speed Err",  "Speed Error (m/s)",               "speed_error",   "{:.4f}"),
    ("Accel",      "Acceleration (m/s²)",             "veh_accel",     "{:.4f}"),
    ("Accel Cal",  "Calculated Acceleration (m/s²)",  "veh_accel_cal", "{:.4f}"),
    ("Accel Err",  "Acceleration Error (m/s²)",       "accel_error",   "{:.4f}"),
    ("Jerk",       "Jerk (m/s³)",                     "veh_jerk",      "{:.4f}"),
    ("Jerk Cal",   "Calculated Jerk (m/s³)",          "veh_jerk_cal",  "{:.4f}"),
    ("Jerk Err",   "Jerk Error (m/s³)",               "jerk_error",    "{:.4f}"),
]

_PLOT_KEYS = ("speed", "accel", "jerk")

# Plot key -> (measured attribute, calculated attribute)
_PLOTS: Dict[str, Tuple[str, str]] = {
    "speed": ("veh_speed", "veh_speed_cal"),
    "accel": ("veh_accel", "veh_accel_cal"),
    "jerk":  ("veh_jerk",  "veh_jerk_cal"),
}


class InternalConsistencyResultDialog(QDialog):
    """Combined right-click output dialog for Module 4."""

    def __init__(self, parent, results: List[InternalConsistencyResult]):
        super().__init__(parent)
        self.setWindowTitle("Internal Consistency — Results")
        self.setModal(True)

        # Group results by (event_id, veh_id) preserving insertion order.
        self._traj_keys: List[Tuple[int, int]] = []
        self._traj_data: Dict[Tuple[int, int], List[InternalConsistencyResult]] = {}
        for r in results:
            key = (r.event_id, r.veh_id)
            if key not in self._traj_data:
                self._traj_keys.append(key)
                self._traj_data[key] = []
            self._traj_data[key].append(r)

        # Pre-compute stats for the stats panel.
        self._stats: Dict[Tuple[int, int], _StatRow] = {
            (s.event_id, s.veh_id): s
            for s in _compute_stats(results)
        }

        self._current_idx: int = 0

        self._setup_ui()
        self._size_and_center()
        self._load_trajectory(0)

    # ── UI construction ───────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)

        # Outer horizontal splitter: plots (left) | tables (right), 50/50.
        outer = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(outer, stretch=1)

        from utils import estimate_time_step
        step = estimate_time_step(
            np.asarray([r.veh_time for r in rows], dtype=np.float64)
            for rows in self._traj_data.values())
        self._plots = KinematicStackedPlotWidget(
            kinematics=_PLOT_KEYS, y_fit="visible", time_step=step)
        self._measured = self._plots.add_series(
            "Measured", Role.MEASURED, linked=True)
        self._calculated = self._plots.add_series(
            "Calculated", Role.DERIVED, linked=True)

        outer.addWidget(self._plots)
        outer.addWidget(self._build_right_panel())
        outer.setStretchFactor(0, 1)
        outer.setStretchFactor(1, 1)
        outer.setSizes([1000, 1000])     # equal shares, scaled to the real width

        root.addLayout(self._build_footer())

    def _build_right_panel(self) -> QSplitter:
        """Vertical splitter: single-row stats table (top) + raw data table (bottom)."""
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_stats_table())
        splitter.addWidget(self._build_raw_table())
        # Give the stats table a compact fixed share; raw table gets the rest.
        splitter.setSizes([80, 600])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _build_stats_table(self) -> QTableWidget:
        self._stats_table = QTableWidget()
        self._stats_table.setColumnCount(len(_STAT_COLUMNS))
        self._stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._stats_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._stats_table.setAlternatingRowColors(True)
        self._stats_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._stats_table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self._stats_table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        for ci, (abbrev, full, _, _) in enumerate(_STAT_COLUMNS):
            item = QTableWidgetItem(abbrev)
            item.setToolTip(full)
            self._stats_table.setHorizontalHeaderItem(ci, item)
        return self._stats_table

    def _build_raw_table(self) -> QTableWidget:
        self._raw_table = QTableWidget()
        self._raw_table.setColumnCount(len(_RAW_COLUMNS))
        self._raw_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._raw_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._raw_table.setAlternatingRowColors(True)
        self._raw_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._raw_table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self._raw_table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        for ci, (abbrev, full, _, _fmt) in enumerate(_RAW_COLUMNS):
            item = QTableWidgetItem(abbrev)
            item.setToolTip(full)
            self._raw_table.setHorizontalHeaderItem(ci, item)

        self._raw_table.cellClicked.connect(self._on_raw_row_clicked)
        return self._raw_table

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.clicked.connect(self._on_prev)
        row.addWidget(self._prev_btn)

        self._traj_combo = QComboBox()
        for eid, vid in self._traj_keys:
            self._traj_combo.addItem(f"Event {eid} — Veh {vid}")
        self._traj_combo.currentIndexChanged.connect(self._on_combo_changed)
        row.addWidget(self._traj_combo)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._on_next)
        row.addWidget(self._next_btn)

        save_btn = QPushButton("Save Plots")
        save_btn.clicked.connect(self._on_save_plots)
        row.addWidget(save_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)

        row.addStretch()

        return row

    # ── Trajectory loading ────────────────────────────────────────────────

    def _load_trajectory(self, idx: int):
        self._current_idx = idx
        key  = self._traj_keys[idx]
        rows = self._traj_data[key]

        self._refresh_plots(rows)
        self._refresh_raw_table(rows)
        self._refresh_stats_table(key)

        self._traj_combo.blockSignals(True)
        self._traj_combo.setCurrentIndex(idx)
        self._traj_combo.blockSignals(False)

        n = len(self._traj_keys)
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < n - 1)

    def _refresh_plots(self, rows: List[InternalConsistencyResult]):
        # Hide any cursor/labels from the previous trajectory before the
        # curve data changes out from under them.
        self._plots.hide_cursor()

        t = np.asarray([r.veh_time for r in rows], dtype=np.float64)

        def column(attr: str) -> np.ndarray:
            return np.asarray([getattr(r, attr) for r in rows], dtype=np.float64)

        self._plots.set_data(self._measured, t,
                             {key: column(m) for key, (m, _c) in _PLOTS.items()})
        self._plots.set_data(self._calculated, t,
                             {key: column(c) for key, (_m, c) in _PLOTS.items()})

        # Reset view so the new trajectory's data fills the viewport.
        self._plots.reset_all()

    def _refresh_raw_table(self, rows):
        self._raw_table.setRowCount(len(rows))
        last_idx = len(rows) - 1
        for ri, r in enumerate(rows):
            for ci, (abbrev, full, attr, fmt) in enumerate(_RAW_COLUMNS):
                val = getattr(r, attr)
                text = "" if _isnan(val) else fmt.format(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if ri == last_idx and attr.endswith("_cal"):
                    item.setToolTip("No following record to derive this value")
                    item.setBackground(QColor("#f0f0f0"))
                self._raw_table.setItem(ri, ci, item)

    def _refresh_stats_table(self, key: Tuple[int, int]):
        """Show only the stats row for the currently selected trajectory."""
        stat = self._stats.get(key)
        self._stats_table.setRowCount(1 if stat else 0)
        if not stat:
            return
        for ci, (abbrev, full, attr, fmt) in enumerate(_STAT_COLUMNS):
            val = getattr(stat, attr, NAN)
            if attr in ("event_id", "veh_id"):
                text = str(val)
            else:
                text = "" if _isnan(val) else fmt.format(val)
            item = QTableWidgetItem(text)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._stats_table.setItem(0, ci, item)

    def _on_raw_row_clicked(self, row: int, _col: int):
        rows = self._traj_data[self._traj_keys[self._current_idx]]
        self._plots.set_cursor(rows[row].veh_time)

    # ── Footer slots ──────────────────────────────────────────────────────

    def _on_prev(self):
        if self._current_idx > 0:
            self._load_trajectory(self._current_idx - 1)

    def _on_next(self):
        if self._current_idx < len(self._traj_keys) - 1:
            self._load_trajectory(self._current_idx + 1)

    def _on_combo_changed(self, idx: int):
        if idx != self._current_idx:
            self._load_trajectory(idx)

    # ── Save plots ────────────────────────────────────────────────────────

    def _on_save_plots(self):
        from utils import default_save_stem, last_dialog_dir, remember_dialog_path
        key     = self._traj_keys[self._current_idx]
        stem    = default_save_stem(f"internal_consistency_e{key[0]}_v{key[1]}")
        default = str(Path(last_dialog_dir()) / f"{stem}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plots", default,
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        remember_dialog_path(path)
        try:
            html = build_plot_html(
                f"Internal Consistency — Event {key[0]}, Vehicle {key[1]}",
                self._plots.export_spec(),
                columns=1,
                cell_height_px=300,
            )
            Path(path).write_text(html, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save plots")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")

    # ── Geometry ──────────────────────────────────────────────────────────

    def _size_and_center(self):
        """80% of the main window's width and height, centered over it."""
        top = self.parent().window() if self.parent() else None
        if top is None:
            self.resize(1400, 700)
            return
        g = top.frameGeometry()
        self.resize(int(g.width() * 0.8), int(g.height() * 0.8))
        own = self.frameGeometry()
        own.moveCenter(g.center())
        self.move(own.topLeft())
