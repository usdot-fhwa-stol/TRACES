"""Right-click result dialog for Module 4 — Internal Consistency.

Layout (two horizontal sections in a QSplitter):
  Left   — three vertically stacked pyqtgraph subplots (speed, accel, jerk)
  Right  — vertical QSplitter:
               Top    — single-row statistics summary table (current trajectory)
               Bottom — scrollable raw data table (Figure 12 layout)

Footer spanning both sections:
  [← Prev]  [Trajectory dropdown]  [Next →]  [Save Plots]  [Close]

Changing trajectory via dropdown or arrows reloads all sections and
resets each plot's view range.

Plot colours are fixed:
  Speed        — blue  (#1f77b4)
  Acceleration — red   (#d62728)
  Jerk         — green (#2ca02c)

HTML export uses comp.plot_export.build_svg_html / export_plot_to_svg,
which are shared with JerkBoxPlotDialog.
"""

import logging
from pathlib import Path
from typing import List, Dict, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QWidget, QToolTip,
)

from .internal_consistency_comp import InternalConsistencyResult
from .internal_consistency_stats_dialog import (
    _compute_stats, _StatRow, _COLUMNS as _STAT_COLUMNS, _isnan, NAN,
)
from comp.plot_export import export_plot_to_svg, build_svg_html

logger = logging.getLogger(__name__)

# ── Column definitions ─────────────────────────────────────────────────────

# Raw data table columns: (header_abbrev, full_name, attribute, format)
_RAW_COLUMNS = [
    ("Event ID",   "Event ID",                              "event_id",      "{:d}"),
    ("Veh ID",     "Vehicle ID",                            "veh_id",        "{:d}"),
    ("Time",   "Time (s)",                              "veh_time",      "{:.4f}"),
    ("Dist",   "Distance Traveled (m)",                 "veh_dist_trav", "{:.4f}"),
    ("Speed",  "Speed (m/s)",                           "veh_speed",     "{:.4f}"),
    ("Speed Cal",    "Calculated Speed (m/s)",             "veh_speed_cal", "{:.4f}"),
    ("Speed Err",    "Speed Error (m/s)", "speed_error",   "{:.4f}"),
    ("Accel", "Acceleration (m/s²)",                 "veh_accel",     "{:.4f}"),
    ("Accel Cal",  "Calculated Acceleration (m/s²)",     "veh_accel_cal", "{:.4f}"),
    ("Accel Err",  "Acceleration Error (m/s²)", "accel_error",   "{:.4f}"),
    ("Jerk", "Jerk (m/s³)",                         "veh_jerk",      "{:.4f}"),
    ("Jerk Cal",   "Calculated Jerk (m/s³)",             "veh_jerk_cal",  "{:.4f}"),
    ("Jerk Err",   "Jerk Error (m/s³)", "jerk_error",    "{:.4f}"),
]

# Plot configuration: (title, orig_attr, cal_attr, y_label, hex_color)
_PLOTS = [
    ("Speed",        "veh_speed", "veh_speed_cal", "Speed (m/s)",   "#1f77b4"),
    ("Acceleration", "veh_accel", "veh_accel_cal", "Accel (m/s²)",  "#d62728"),
    ("Jerk",         "veh_jerk",  "veh_jerk_cal",  "Jerk (m/s³)",   "#2ca02c"),
]


class InternalConsistencyResultDialog(QDialog):
    """Combined right-click output dialog for Module 4."""

    def __init__(self, parent, results: List[InternalConsistencyResult]):
        super().__init__(parent)
        self.setWindowTitle("Internal Consistency — Results")
        self.setModal(True)
        self.resize(1400, 700)

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
        self._plot_widgets: List[pg.PlotWidget] = []
        # Parallel lists populated in _build_plot_panel; one entry per plot.
        self._plot_curves:  List[List[pg.PlotDataItem]] = []  # [plot_idx][curve_idx]
        self._vlines:       List[pg.InfiniteLine] = []        # vertical crosshair per plot

        self._setup_ui()
        self._load_trajectory(0)
        QTimer.singleShot(0, self._center)

    # ── UI construction ───────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)

        # Outer horizontal splitter: plots (left) | tables (right)
        outer = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(outer, stretch=1)

        outer.addWidget(self._build_plot_panel())
        outer.addWidget(self._build_right_panel())
        outer.setSizes([520, 880])

        root.addLayout(self._build_footer())

    def _build_plot_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._plot_widgets = []
        self._plot_curves  = []
        self._vlines       = []
        for title, _orig, _cal, ylabel, _color in _PLOTS:
            pw = pg.PlotWidget()
            pw.setBackground("w")
            pw.setTitle(title)
            pw.setLabel("left", ylabel)
            pw.setLabel("bottom", "Time (s)")
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.addLegend(offset=(5, 5))

            # Vertical crosshair — hidden until the mouse enters a plot.
            vline = pg.InfiniteLine(angle=90, movable=False,
                                    pen=pg.mkPen("#888888", width=1,
                                                 style=Qt.PenStyle.DashLine))
            vline.setVisible(False)
            pw.addItem(vline)
            self._vlines.append(vline)

            # Wire mouse-move on every plot scene to the shared handler.
            pw.scene().sigMouseMoved.connect(
                lambda pos, _pw=pw: self._on_mouse_moved(pos, _pw)
            )

            self._plot_widgets.append(pw)
            self._plot_curves.append([])   # filled in _refresh_plots
            layout.addWidget(pw)
        return container

    def _build_right_panel(self) -> QWidget:
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
        t = np.asarray([r.veh_time for r in rows], dtype=np.float64)

        for pi, (pw, (_title, orig_attr, cal_attr, _ylabel, color)) in enumerate(
                zip(self._plot_widgets, _PLOTS)):
            pw.clear()
            pw.addLegend(offset=(5, 5))
            # Re-add the crosshair (clear() removes all items including it).
            vline = self._vlines[pi]
            vline.setVisible(False)
            pw.addItem(vline)

            curves: List[pg.PlotDataItem] = []

            orig = np.asarray([getattr(r, orig_attr) for r in rows], dtype=np.float64)
            cal  = np.asarray([getattr(r, cal_attr)  for r in rows], dtype=np.float64)

            # Original — solid line
            if not np.all(np.isnan(orig)):
                c = pw.plot(t, orig,
                            pen=pg.mkPen(color=color, width=1.5),
                            name=orig_attr.replace("_", " "))
                curves.append(c)

            # Reconstructed — dashed, same colour
            if not np.all(np.isnan(cal)):
                c = pw.plot(t, cal,
                            pen=pg.mkPen(color=color, width=1.5,
                                         style=Qt.PenStyle.DashLine),
                            name=cal_attr.replace("_", " "))
                curves.append(c)

            self._plot_curves[pi] = curves

            # Reset view so the new data fills the viewport.
            pw.enableAutoRange()

    def _refresh_raw_table(self, rows: List[InternalConsistencyResult]):
        self._raw_table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            for ci, (abbrev, full, attr, fmt) in enumerate(_RAW_COLUMNS):
                val = getattr(r, attr)
                if attr in ("event_id", "veh_id"):
                    text = str(val)
                else:
                    text = "" if _isnan(val) else fmt.format(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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

    # ── Tooltip / crosshair ───────────────────────────────────────────────

    # Snap tolerance in data units (seconds along the x/time axis).
    _TOOLTIP_T_TOL: float = 0.1

    def _on_mouse_moved(self, pos, source_pw: pg.PlotWidget):
        """Show a QToolTip and move the crosshair when the mouse is near a curve.

        ``pos`` is in scene coordinates of ``source_pw``'s scene.  We move the
        vertical crosshair on *all* plots to the same x value so they stay in
        sync, but only search for the nearest point in ``source_pw``.
        """
        pi = self._plot_widgets.index(source_pw)
        vb = source_pw.getViewBox()

        if not source_pw.sceneBoundingRect().contains(pos):
            # Mouse has left this plot — hide its crosshair and tooltip.
            self._vlines[pi].setVisible(False)
            QToolTip.hideText()
            return

        data_pt = vb.mapSceneToView(pos)
        mx = data_pt.x()

        # Move only this plot's crosshair.
        self._vlines[pi].setValue(mx)
        self._vlines[pi].setVisible(True)

        # Search the curves in the source plot for the closest y value.
        curves = self._plot_curves[pi]
        best_dist = float("inf")
        best_label: str = ""
        best_y: float = float("nan")

        for curve in curves:
            xdata, ydata = curve.getData()
            if xdata is None or len(xdata) == 0:
                continue
            # Restrict search to a narrow time window for performance.
            mask = np.abs(xdata - mx) <= self._TOOLTIP_T_TOL
            if not mask.any():
                continue
            xs = xdata[mask]
            ys = ydata[mask]
            # Find index closest to mx in the x direction.
            idx = int(np.argmin(np.abs(xs - mx)))
            # Convert y-distance to view pixels for a display-size-independent threshold.
            y_view  = ys[idx]
            my_view = data_pt.y()
            # Transform both to pixel space to compute pixel distance.
            pt_scene  = vb.mapViewToScene(pg.Point(xs[idx], y_view))
            cur_scene = vb.mapViewToScene(pg.Point(mx,       my_view))
            pixel_dist = (pt_scene - cur_scene).manhattanLength()
            if pixel_dist < best_dist:
                best_dist  = pixel_dist
                best_label = curve.name() or ""
                best_y     = float(y_view)
                best_x     = float(xs[idx])

        _PIXEL_THRESHOLD = 12
        if best_dist <= _PIXEL_THRESHOLD and not np.isnan(best_y):
            _title, _o, _c, ylabel, _col = _PLOTS[pi]
            tip = f"t = {best_x:.4f} s\n{best_label}: {best_y:.4f}"
            screen_pos = source_pw.mapToGlobal(
                source_pw.mapFromScene(pos).toPointF().toPoint()
            )
            QToolTip.showText(screen_pos, tip)
        else:
            QToolTip.hideText()

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
        from utils import default_save_stem
        key     = self._traj_keys[self._current_idx]
        stem    = default_save_stem(f"internal_consistency_e{key[0]}_v{key[1]}")
        default = str(Path.home() / f"{stem}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plots", default,
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        try:
            svg_blocks = [
                (title, export_plot_to_svg(pw))
                for pw, (title, _o, _c, _y, _col) in zip(self._plot_widgets, _PLOTS)
            ]
            html = build_svg_html(
                "Internal Consistency — Plots",
                svg_blocks,
                grid_columns=1,
                cell_height_px=300,
            )
            Path(path).write_text(html, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save plots")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")

    # ── Centering ─────────────────────────────────────────────────────────

    def _center(self):
        parent = self.parent()
        if parent:
            pg_ = parent.geometry()
            dg  = self.geometry()
            self.move(
                pg_.x() + (pg_.width()  - dg.width())  // 2,
                pg_.y() + (pg_.height() - dg.height()) // 2,
            )