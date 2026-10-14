import logging
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QToolTip
)
from ui_kit.box_plot_item import BoxPlotItem
from ui_kit.plot_export import SPACER, box_item, build_plot_html, plot_spec

logger = logging.getLogger(__name__)


class JerkBoxPlotDialog(QDialog):
    """Displays a 3x2 grid of box plots summarizing per-trajectory metrics.

    Save writes an interactive HTML page (ui_kit.plot_export.build_plot_html)
    with the same 3x2 layout: canvas box plots with hover statistics and
    outlier vehicle IDs, zoom and pan.

    Args:
        parent: parent widget
        title (str): dialog title
        metrics (dict[str, list[tuple[int, float]]]): mapping of metric key to value list.
            Expected keys: 'min_jerk', 'mean_jerk', 'max_jerk',
            'jerk_violation_pct', 'sign_inversion_excess_pct'.
        dropped (int): number of trajectories excluded due to missing data.
    """

    # Grid position and label for each metric. None key = empty spacer cell.
    _LAYOUT = [
        ("min_jerk",                  (0, 0), "Minimum Jerk (m/s³)"),
        ("mean_jerk",                 (0, 1), "Mean Jerk (m/s³)"),
        ("max_jerk",                  (0, 2), "Maximum Jerk (m/s³)"),
        ("jerk_violation_pct",        (1, 0), "Jerk Violation (%)"),
        (None,                        (1, 1), None),               # spacer cell
        ("sign_inversion_excess_pct", (1, 2), "Sign Inversion Excess (%)"),
    ]
    _COLUMNS = 3

    def __init__(self, parent, title, metrics, dropped=0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._title = title
        self.resize(1350, 700)           # wider to accommodate 3 columns

        self._plots = {}
        self._export_specs = {}          # key -> plot_spec for the HTML page
        self._setup_ui(metrics, dropped)

    def _setup_ui(self, metrics, dropped):
        layout = QVBoxLayout(self)

        if dropped > 0:
            note = QLabel(f"{dropped} trajector{'y' if dropped == 1 else 'ies'} "
                          f"excluded due to missing data.")
            note.setStyleSheet("color: #555; font-style: italic;")
            layout.addWidget(note)

        grid = QGridLayout()
        # Equal stretch on all three columns so the empty cell doesn't collapse.
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        for key, (row, col), label in self._LAYOUT:
            if key is None:
                continue                 # leave the spacer cell empty
            plot = self._build_box_plot(key, metrics.get(key, []), label)
            self._plots[key] = plot
            grid.addWidget(plot, row, col)
        layout.addLayout(grid, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _build_box_plot(self, key, values, label):
        plot = pg.PlotWidget()
        plot.setBackground('w')
        plot.setTitle(label)
        plot.showGrid(x=False, y=True)
        plot.getPlotItem().hideAxis('bottom')

        # Filter NaNs (trajectories where the metric couldn't be derived).
        pairs = [(vid, v) for vid, v in values
                 if v is not None and not np.isnan(v)]
        veh_ids_arr = [vid for vid, v in pairs]
        arr = np.asarray([v for vid, v in pairs], dtype=np.float64)

        if arr.size == 0:
            txt = pg.TextItem("No data", color=(80, 80, 80), anchor=(0.5, 0.5))
            plot.addItem(txt)
            txt.setPos(1, 0)
            self._export_specs[key] = self._export_spec(f"{label} — no data", [])
            return plot

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

        # Identify which indices in arr are outliers, keeping their veh_ids
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
                data=outlier_vids,   # parallel list — index i → veh_id
            )
            scatter.sigHovered.connect(lambda item, pts, ev, pw=plot: self._on_outlier_hovered(pts, ev, pw))
            plot.addItem(scatter)

        # Pad the X-range so the box is centered and not hugged by the edges.
        plot.setXRange(0.0, 2.0, padding=0)

        # Disable auto-range button
        plot.getPlotItem().hideButtons()
        plot.getViewBox().setMenuEnabled(False)

        self._export_specs[key] = self._export_spec(label, [box_item(
            1, q1, median, q3, whisker_low, whisker_high,
            outliers=[(y, f"veh_id: {vid}") for y, vid in zip(outlier_y, outlier_vids)])])
        return plot

    @staticmethod
    def _export_spec(title, items):
        """HTML plot for one metric: box only, no x axis, y grid, fixed x
        range like the dialog's."""
        return plot_spec(items, title=title, show_x_axis=False, grid=0.3,
                         y_fit="all", x_range=(0.0, 2.0), cursor=False, legend=False)

    def _on_outlier_hovered(self, points, ev, plot_widget):
        """Show a QToolTip with the vehicle ID(s) under the cursor."""
        if len(points) > 0:
            labels = "\n".join(f"veh_id: {p.data()}  y: {p.pos().y():.4g}" for p in points)
            scene_pos = ev.scenePos()
            screen_pos = plot_widget.mapToGlobal(
                plot_widget.mapFromScene(scene_pos)
            )
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
        # Convert the centre of the plot widget to a global screen position.
        screen_pos = plot_widget.mapToGlobal(plot_widget.rect().center())
        QToolTip.showText(screen_pos, text)

    # ── Save: interactive HTML ─────────────────────────────────────────────

    def _on_save(self):
        from utils import default_save_stem, last_dialog_dir, remember_dialog_path
        import re
        slug = re.sub(r"[^\w]+", "_", self._title).strip("_").lower()
        default = str(Path(last_dialog_dir()) / f"{default_save_stem(slug)}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Box Plots", default,
            "HTML Files (*.html);;All Files (*)"
        )
        if not path:
            return
        remember_dialog_path(path)
        try:
            # Same grid as the dialog: row-major, spacer cell included.
            ordered = sorted(self._LAYOUT, key=lambda entry: entry[1])
            plots = [SPACER if key is None else self._export_specs[key]
                     for key, _pos, _label in ordered]
            html = build_plot_html("Jerk Analysis Box Plots", plots,
                                   columns=self._COLUMNS, cell_height_px=320)
            Path(path).write_text(html, encoding="utf-8")
        except Exception as e:
            logger.exception("Failed to save HTML output")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")
