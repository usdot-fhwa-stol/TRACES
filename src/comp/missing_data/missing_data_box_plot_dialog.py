import logging
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QToolTip
)
from plot.box_plot_item import BoxPlotItem
from comp.plot_export import export_plot_to_svg, build_svg_html

logger = logging.getLogger(__name__)

class MissingDataBoxPlotDialog(QDialog):
    """Displays a 1x2 grid of box plots summarizing missing data metrics.
    
    Args:
        parent: parent widget
        title (str): dialog title
        metrics dict[str, list[tuple[int, float]]]: mapping of metric key to value list.
            Expected keys: 'missing_record_pct', 'num_gaps'.
        dropped (int): number of trajectories excluded due to missing data.
    """

    # Grid position and label for each metric (Side-by-side)
    _TITLES = {
        "missing_record_pct": "Missing Record (%)",
        "num_gaps":           "Number of Gaps",
        "gap_lengths":        "Gap Length (points)"
    }

    def __init__(self, parent, title, metrics, dropped=0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 500) # Slightly shorter since it's only 1 row now

        self._plots = {}  # key -> pg.PlotWidget
        self._setup_ui(metrics, dropped)

    def _setup_ui(self, metrics, dropped):
        layout = QVBoxLayout(self)

        if dropped > 0:
            note = QLabel(f"{dropped} trajector{'y' if dropped == 1 else 'ies'} "
                          f"excluded due to missing or invalid data.")
            note.setStyleSheet("color: #555; font-style: italic;")
            layout.addWidget(note)

        # Dynamic Grid Layout
        grid = QGridLayout()
        
        # Loop through whatever metrics were passed in (2 for Top-Level, 3 for Right-Click)
        for i, (key, values) in enumerate(metrics.items()):
            row = i // 2
            col = i % 2
            label = self._TITLES.get(key, key)
            
            plot = self._build_box_plot(values, label)
            self._plots[key] = plot
            grid.addWidget(plot, row, col)
            
        layout.addLayout(grid, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("Save…")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _build_box_plot(self, values, label):
        """Builds a statistical box plot using pyqtgraph."""
        plot = pg.PlotWidget()
        plot.setBackground('w')
        plot.setTitle(label)
        plot.showGrid(x=False, y=True)
        plot.getPlotItem().hideAxis('bottom')

        # values expected as list of tuples: (veh_id, value)
        pairs = [(vid, v) for vid, v in values if v is not None and not np.isnan(v)]
        
        veh_ids_arr = [vid for vid, v in pairs]
        arr = np.asarray([v for vid, v in pairs], dtype=np.float64)

        if arr.size == 0:
            txt = pg.TextItem("No data", color=(80, 80, 80), anchor=(0.5, 0.5))
            plot.addItem(txt)
            txt.setPos(1, 0)
            return plot

        # Box plot math
        q1, median, q3 = np.percentile(arr, [25, 50, 75])
        iqr = q3 - q1
        lo_fence, hi_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        non_outliers = arr[(arr >= lo_fence) & (arr <= hi_fence)]
        whisker_low  = float(non_outliers.min()) if non_outliers.size else float(arr.min())
        whisker_high = float(non_outliers.max()) if non_outliers.size else float(arr.max())
        outliers = arr[(arr < whisker_low) | (arr > whisker_high)].tolist()

        item = BoxPlotItem(x=1, q1=q1, median=median, q3=q3,
                           whisker_low=whisker_low, whisker_high=whisker_high,
                           outliers=outliers)
        plot.addItem(item)
        item.sigHovered.connect(lambda stats, pw=plot: self._on_box_hovered(stats, pw))

        # Outlier hover logic
        outlier_mask = (arr < whisker_low) | (arr > whisker_high)
        outlier_y    = arr[outlier_mask].tolist()
        outlier_vids = [veh_ids_arr[i] for i, m in enumerate(outlier_mask) if m]
        
        if outlier_y:
            scatter = pg.ScatterPlotItem(
                x=[1] * len(outlier_y),
                y=outlier_y,
                pen=pg.mkPen('k'),
                brush=pg.mkBrush(0, 0, 0, 0),
                size=6,
                hoverable=True,
                tip=None,
                data=outlier_vids,
            )
            scatter.sigHovered.connect(
                lambda item, pts, ev, pw=plot: self._on_outlier_hovered(pts, ev, pw)
            )
            plot.addItem(scatter)

        plot.setXRange(0.0, 2.0, padding=0)
        plot.getPlotItem().hideButtons()
        plot.getViewBox().setMenuEnabled(False)
        
        return plot

    def _on_outlier_hovered(self, points, ev, plot_widget):
        """Show a QToolTip with the vehicle ID(s) under the cursor."""
        if len(points) > 0:
            labels = "\n".join(f"veh_id: {p.data()}  value: {p.pos().y():.4g}" for p in points)
            scene_pos = ev.scenePos()
            screen_pos = plot_widget.mapToGlobal(plot_widget.mapFromScene(scene_pos))
            QToolTip.showText(screen_pos, labels)
        else:
            QToolTip.hideText()

    def _on_box_hovered(self, stats, plot_widget):
        """Show a QToolTip with the five-number summary on box hover, hide on leave."""
        if stats is None:
            QToolTip.hideText()
            return
        whisker_low, q1, median, q3, whisker_high = stats
        text = (
            f"Upper: {whisker_high:.4g}\n"
            f"Q3:            {q3:.4g}\n"
            f"Median:        {median:.4g}\n"
            f"Q1:            {q1:.4g}\n"
            f"Lower: {whisker_low:.4g}"
        )
        screen_pos = plot_widget.mapToGlobal(plot_widget.rect().center())
        QToolTip.showText(screen_pos, text)

    # ── Save: HTML with embedded SVG and pan/zoom JS ──────────────────────
    
    def _on_save(self):
        from utils import last_dialog_dir, remember_dialog_path
        default = str(Path(last_dialog_dir()) / "missing_data_box_plots.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Box Plots", default,
            "HTML Files (*.html);;All Files (*)"
        )
        if not path:
            return
        remember_dialog_path(path)

        try:
            svg_blocks = [
                (self._TITLES.get(key, key), export_plot_to_svg(plot))
                for key, plot in self._plots.items()
            ]
            html = build_svg_html(
                "Missing Data Analysis Box Plots",
                svg_blocks,
                grid_columns=2,
                cell_height_px=320,
            )
            Path(path).write_text(html, encoding="utf-8")
        except Exception as e:
            logger.exception("Failed to save HTML output")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")