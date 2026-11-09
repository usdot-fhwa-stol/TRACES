"""Grid of box plots, one per metric, with hover statistics, outlier
labels and an interactive HTML save.

Shared by Jerk Analysis, Missing Data and Advanced Statistics, which each
used to carry their own copy of this code. A caller describes the plots
as BoxPlotPanel entries (None leaves a grid cell empty) and the grid's
column count:

    panels = [
        BoxPlotPanel("min_jerk", "Minimum Jerk (m/s³)", [(veh_id, value), ...]),
        None,                                            # empty cell
        ...
    ]
    BoxPlotGridDialog(parent, "Jerk Analysis — Event 3", panels, columns=3,
                      note="2 trajectories excluded due to missing data.").exec()

Each panel's values are (label, value) pairs; the label identifies the
sample an outlier came from (a vehicle ID, or "veh 12 @ 31.4 s"), shown
on hover as "<label_prefix><label>". NaN and None values are ignored.

Box statistics (box_stats): Q1, median and Q3 are numpy percentiles; the
whiskers reach the furthest samples within 1.5 IQR of the box; samples
beyond them are outliers.

Save writes an interactive HTML page (ui_kit.plot_export.build_plot_html)
with the same grid: canvas box plots with hover statistics and outlier
labels, zoom and pan.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QToolTip, QVBoxLayout,
)

from .box_plot_item import BoxPlotItem
from .plot_export import SPACER, box_item, build_plot_html, plot_spec

logger = logging.getLogger(__name__)

_X = 1.0                    # every box sits at x = 1 in a 0..2 view
_X_RANGE = (0.0, 2.0)
_IQR_FENCE = 1.5


@dataclass
class BoxPlotPanel:
    """One box plot: *values* are (label, value) pairs."""
    key: str
    title: str
    values: Sequence[Tuple[Any, Optional[float]]] = field(default_factory=list)


@dataclass
class BoxStats:
    q1: float
    median: float
    q3: float
    whisker_low: float
    whisker_high: float
    outlier_values: List[float]
    outlier_labels: List[Any]
    count: int


def box_stats(values: Sequence[Tuple[Any, Optional[float]]]) -> Optional[BoxStats]:
    """Box statistics of *values* ((label, value) pairs; NaN / None
    ignored), or None when no value is left (module docstring)."""
    pairs = [(lab, float(v)) for lab, v in values
             if v is not None and np.isfinite(v)]
    if not pairs:
        return None
    arr = np.asarray([v for _, v in pairs], dtype=np.float64)
    q1, median, q3 = (float(x) for x in np.percentile(arr, [25, 50, 75]))
    iqr = q3 - q1
    inside = arr[(arr >= q1 - _IQR_FENCE * iqr) & (arr <= q3 + _IQR_FENCE * iqr)]
    low = float(inside.min()) if inside.size else float(arr.min())
    high = float(inside.max()) if inside.size else float(arr.max())
    mask = (arr < low) | (arr > high)
    return BoxStats(q1, median, q3, low, high,
                    arr[mask].tolist(), [pairs[i][0] for i in np.flatnonzero(mask)],
                    int(arr.size))


class BoxPlotGridDialog(QDialog):
    """Box plots in a grid (module docstring)."""

    def __init__(self, parent, title: str, panels: Sequence[Optional[BoxPlotPanel]],
                 columns: int = 2, note: str = "", html_title: Optional[str] = None,
                 save_stem: Optional[str] = None, label_prefix: str = "veh_id: ",
                 cell_height_px: int = 320):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._title = title
        self._html_title = html_title or title
        self._save_stem = save_stem
        self._label_prefix = label_prefix
        self._columns = max(1, int(columns))
        self._cell_height = cell_height_px
        self._panels = list(panels)
        self._plots: Dict[str, pg.PlotWidget] = {}
        self._export_specs: Dict[str, dict] = {}
        rows = max(1, -(-len(self._panels) // self._columns))
        self.resize(min(1500, 450 * self._columns), min(900, 120 + 330 * rows))
        self._setup_ui(note)

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self, note: str) -> None:
        layout = QVBoxLayout(self)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet("color: #555; font-style: italic;")
            layout.addWidget(label)

        grid = QGridLayout()
        for c in range(self._columns):
            grid.setColumnStretch(c, 1)   # an empty cell keeps its width
        for i, panel in enumerate(self._panels):
            if panel is None:
                continue
            plot = self._build_box_plot(panel)
            self._plots[panel.key] = plot
            grid.addWidget(plot, i // self._columns, i % self._columns)
        layout.addLayout(grid, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save the box plots as an interactive HTML page")
        self._save_btn.clicked.connect(self._on_save)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        buttons.addWidget(self._save_btn)
        buttons.addWidget(self._close_btn)
        layout.addLayout(buttons)

    def _build_box_plot(self, panel: BoxPlotPanel) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot.setTitle(panel.title)
        plot.showGrid(x=False, y=True)
        plot.getPlotItem().hideAxis("bottom")
        plot.getPlotItem().hideButtons()
        plot.getViewBox().setMenuEnabled(False)

        stats = box_stats(panel.values)
        if stats is None:
            text = pg.TextItem("No data", color=(80, 80, 80), anchor=(0.5, 0.5))
            plot.addItem(text)
            text.setPos(_X, 0)
            self._export_specs[panel.key] = self._export_spec(f"{panel.title} — no data", [])
            return plot

        item = BoxPlotItem(x=_X, q1=stats.q1, median=stats.median, q3=stats.q3,
                           whisker_low=stats.whisker_low, whisker_high=stats.whisker_high,
                           outliers=stats.outlier_values)
        plot.addItem(item)
        item.sigHovered.connect(lambda s, pw=plot, n=stats.count: self._on_box_hovered(s, pw, n))

        if stats.outlier_values:
            scatter = pg.ScatterPlotItem(
                x=[_X] * len(stats.outlier_values), y=stats.outlier_values,
                pen=pg.mkPen("k"), brush=pg.mkBrush(0, 0, 0, 0), size=6,
                hoverable=True, tip=None, data=stats.outlier_labels)
            scatter.sigHovered.connect(
                lambda _item, pts, ev, pw=plot: self._on_outlier_hovered(pts, ev, pw))
            plot.addItem(scatter)
        plot.setXRange(*_X_RANGE, padding=0)

        prefix = self._label_prefix
        self._export_specs[panel.key] = self._export_spec(panel.title, [box_item(
            _X, stats.q1, stats.median, stats.q3, stats.whisker_low, stats.whisker_high,
            outliers=[(y, f"{prefix}{lab}") for y, lab in
                      zip(stats.outlier_values, stats.outlier_labels)])])
        return plot

    @staticmethod
    def _export_spec(title: str, items: list) -> dict:
        """HTML plot for one metric: box only, no x axis, y grid, fixed x
        range like the dialog's."""
        return plot_spec(items, title=title, show_x_axis=False, grid=0.3,
                         y_fit="all", x_range=_X_RANGE, cursor=False, legend=False)

    # ── Hover ─────────────────────────────────────────────────────────

    def _on_outlier_hovered(self, points, ev, plot_widget) -> None:
        if len(points) > 0:
            text = "\n".join(f"{self._label_prefix}{p.data()}  value: {p.pos().y():.4g}"
                             for p in points)
            screen = plot_widget.mapToGlobal(plot_widget.mapFromScene(ev.scenePos()))
            QToolTip.showText(screen, text)
        else:
            QToolTip.hideText()

    @staticmethod
    def _on_box_hovered(stats, plot_widget, count: int) -> None:
        if stats is None:
            QToolTip.hideText()
            return
        low, q1, median, q3, high = stats
        text = (f"Upper:   {high:.4g}\n"
                f"Q3:      {q3:.4g}\n"
                f"Median:  {median:.4g}\n"
                f"Q1:      {q1:.4g}\n"
                f"Lower:   {low:.4g}\n"
                f"Samples: {count}")
        QToolTip.showText(plot_widget.mapToGlobal(plot_widget.rect().center()), text)

    # ── Save: interactive HTML ────────────────────────────────────────

    def _default_save_path(self) -> str:
        from utils import default_save_stem, last_dialog_dir
        stem = self._save_stem or re.sub(r"[^\w]+", "_", self._title).strip("_").lower()
        return str(Path(last_dialog_dir()) / f"{default_save_stem(stem)}.html")

    def _on_save(self) -> None:
        from utils import remember_dialog_path
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Box Plots", self._default_save_path(),
            "HTML Files (*.html);;All Files (*)")
        if not path:
            return
        remember_dialog_path(path)
        try:
            plots = [SPACER if p is None else self._export_specs[p.key] for p in self._panels]
            html = build_plot_html(self._html_title, plots, columns=self._columns,
                                   cell_height_px=self._cell_height)
            Path(path).write_text(html, encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to save box plot HTML")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{exc}")
