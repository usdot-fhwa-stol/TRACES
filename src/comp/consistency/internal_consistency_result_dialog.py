"""Right-click result dialog for Module 4 — Internal Consistency.

Layout (horizontal thirds in a QSplitter):
  Left   — three vertically stacked pyqtgraph subplots (speed, accel, jerk)
  Center — scrollable raw data table (Figure 12 layout)
  Right  — scrollable statistics summary table

Footer spanning all three sections:
  [← Prev]  [Trajectory dropdown]  [Next →]  [Save Plots]  [Close]

Changing trajectory via dropdown or arrows reloads all three sections.
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
    QHeaderView, QFileDialog, QMessageBox, QWidget
)

from .internal_consistency_comp import InternalConsistencyResult
from .internal_consistency_stats_dialog import (
    _compute_stats, _StatRow, _COLUMNS as _STAT_COLUMNS, _isnan, NAN,
)

logger = logging.getLogger(__name__)

# Raw data table columns: (header, attribute, format)
_RAW_COLUMNS = [
    ("Event ID",       "event_id",      "{:d}"),
    ("Veh ID",         "veh_id",        "{:d}"),
    ("Time (s)",       "veh_time",      "{:.4f}"),
    ("Dist (m)",       "veh_dist_trav", "{:.4f}"),
    ("Speed",          "veh_speed",     "{:.4f}"),
    ("Speed Cal",      "veh_speed_cal", "{:.4f}"),
    ("Speed Err",      "speed_error",   "{:.4f}"),
    ("Accel",          "veh_accel",     "{:.4f}"),
    ("Accel Cal",      "veh_accel_cal", "{:.4f}"),
    ("Accel Err",      "accel_error",   "{:.4f}"),
    ("Jerk",           "veh_jerk",      "{:.4f}"),
    ("Jerk Cal",       "veh_jerk_cal",  "{:.4f}"),
    ("Jerk Err",       "jerk_error",    "{:.4f}"),
]

# Plot configuration: (title, orig_attr, cal_attr, y_label)
_PLOTS = [
    ("Speed",        "veh_speed", "veh_speed_cal", "Speed (m/s)"),
    ("Acceleration", "veh_accel", "veh_accel_cal", "Accel (m/s²)"),
    ("Jerk",         "veh_jerk",  "veh_jerk_cal",  "Jerk (m/s³)"),
]

# One colour per trajectory index (cycles if > 8 trajectories selected).
_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
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

        # Assign a consistent colour to each trajectory.
        self._traj_color: Dict[Tuple[int, int], str] = {
            key: _PALETTE[i % len(_PALETTE)]
            for i, key in enumerate(self._traj_keys)
        }

        self._current_idx: int = 0
        self._plot_widgets: List[pg.PlotWidget] = []

        self._setup_ui()
        self._load_trajectory(0)
        QTimer.singleShot(0, self._center)

    # ── UI construction ───────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        # Left: plot section
        splitter.addWidget(self._build_plot_panel())

        # Centre: raw data table
        splitter.addWidget(self._build_raw_table())

        # Right: stats table
        splitter.addWidget(self._build_stats_table())

        splitter.setSizes([460, 560, 380])

        # Footer
        root.addLayout(self._build_footer())

    def _build_plot_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._plot_widgets = []
        for title, _orig, _cal, ylabel in _PLOTS:
            pw = pg.PlotWidget()
            pw.setBackground("w")
            pw.setTitle(title)
            pw.setLabel("left", ylabel)
            pw.setLabel("bottom", "Time (s)")
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.addLegend(offset=(5, 5))
            self._plot_widgets.append(pw)
            layout.addWidget(pw)
        return container

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
        for ci, (hdr, _, _fmt) in enumerate(_RAW_COLUMNS):
            self._raw_table.setHorizontalHeaderItem(ci, QTableWidgetItem(hdr))
        return self._raw_table

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

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.clicked.connect(self._on_prev)
        row.addWidget(self._prev_btn)

        self._traj_combo = QComboBox()
        for event_id, veh_id in self._traj_keys:
            self._traj_combo.addItem(f"Event {event_id} — Veh {veh_id}")
        self._traj_combo.currentIndexChanged.connect(self._on_combo_changed)
        row.addWidget(self._traj_combo)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._on_next)
        row.addWidget(self._next_btn)

        row.addSpacing(20)

        save_btn = QPushButton("Save Plots…")
        save_btn.clicked.connect(self._on_save_plots)
        row.addWidget(save_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)

        row.addStretch()
        return row

    # ── Trajectory loading ────────────────────────────────────────────────

    def _load_trajectory(self, idx: int):
        if not self._traj_keys:
            return
        self._current_idx = idx
        key = self._traj_keys[idx]
        rows = self._traj_data[key]
        color = self._traj_color[key]

        self._refresh_plots(rows, color)
        self._refresh_raw_table(rows)
        self._refresh_stats_table(key)

        # Keep combo in sync without triggering _on_combo_changed recursion.
        self._traj_combo.blockSignals(True)
        self._traj_combo.setCurrentIndex(idx)
        self._traj_combo.blockSignals(False)

        n = len(self._traj_keys)
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < n - 1)

    def _refresh_plots(self, rows: List[InternalConsistencyResult], color: str):
        t = np.asarray([r.veh_time for r in rows], dtype=np.float64)
        for pi, (pw, (_title, orig_attr, cal_attr, _ylabel)) in enumerate(
                zip(self._plot_widgets, _PLOTS)):
            pw.clear()
            pw.addLegend(offset=(5, 5))

            orig = np.asarray([getattr(r, orig_attr) for r in rows], dtype=np.float64)
            cal  = np.asarray([getattr(r, cal_attr)  for r in rows], dtype=np.float64)

            # Original — solid line
            if not np.all(np.isnan(orig)):
                pen_solid = pg.mkPen(color=color, width=1.5)
                pw.plot(t, orig, pen=pen_solid,
                        name=orig_attr.replace("_", " "))

            # Reconstructed — dashed, same colour
            if not np.all(np.isnan(cal)):
                pen_dash = pg.mkPen(color=color, width=1.5,
                                    style=Qt.PenStyle.DashLine)
                pw.plot(t, cal, pen=pen_dash,
                        name=cal_attr.replace("_", " "))

    def _refresh_raw_table(self, rows: List[InternalConsistencyResult]):
        self._raw_table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            for ci, (hdr, attr, fmt) in enumerate(_RAW_COLUMNS):
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
        key = self._traj_keys[self._current_idx]
        default = f"internal_consistency_e{key[0]}_v{key[1]}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plots", default,
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        try:
            svg_blocks = []
            for pw, (title, _o, _c, _y) in zip(self._plot_widgets, _PLOTS):
                svg_blocks.append((title, self._export_svg(pw)))
            Path(path).write_text(self._build_html(svg_blocks), encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save plots")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")

    @staticmethod
    def _export_svg(pw: pg.PlotWidget) -> str:
        from pyqtgraph.exporters import SVGExporter
        exp = SVGExporter(pw.getPlotItem())
        svg = exp.export(toBytes=True)
        return svg.decode("utf-8") if isinstance(svg, (bytes, bytearray)) else svg

    @staticmethod
    def _build_html(svg_blocks: list) -> str:
        cells = "".join(
            f'<div class="cell"><div class="label">{lbl}</div>'
            f'<div class="svgwrap">{svg}</div></div>'
            for lbl, svg in svg_blocks
        )
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<style>
  body{{font-family:sans-serif;margin:16px}}
  .cell{{border:1px solid #ddd;padding:8px;margin-bottom:8px}}
  .label{{font-weight:bold;margin-bottom:4px}}
  .svgwrap{{width:100%;height:260px;overflow:hidden}}
  svg{{display:block;width:100%;height:100%}}
</style></head><body>
<h2>Internal Consistency — Plots</h2>
{cells}
</body></html>"""

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