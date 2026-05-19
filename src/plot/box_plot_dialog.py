import logging
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox
)
from .box_plot_item import BoxPlotItem

logger = logging.getLogger(__name__)


class BoxPlotDialog(QDialog):
    """Displays a 2x2 grid of box plots summarizing per-trajectory metrics.

    Args:
        parent: parent widget
        title (str): dialog title
        metrics (dict[str, list[float]]): mapping of metric key to value list.
            Expected keys: 'min_jerk', 'max_jerk', 'jerk_violation_pct',
            'sign_inversion_excess_pct'.
        dropped (int): number of trajectories excluded due to missing data.
    """

    # Grid position and label for each metric.
    _LAYOUT = [
        ("min_jerk",                  (0, 0), "Min Jerk (m/s³)"),
        ("max_jerk",                  (0, 1), "Max Jerk (m/s³)"),
        ("jerk_violation_pct",        (1, 0), "Jerk Violation (%)"),
        ("sign_inversion_excess_pct", (1, 1), "Sign Inversion Excess (%)"),
    ]

    def __init__(self, parent, title, metrics, dropped=0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 700)

        self._plots = {}      # key -> pg.PlotWidget
        self._setup_ui(metrics, dropped)

    def _setup_ui(self, metrics, dropped):
        layout = QVBoxLayout(self)

        if dropped > 0:
            note = QLabel(f"{dropped} trajector{'y' if dropped == 1 else 'ies'} "
                          f"excluded due to missing data.")
            note.setStyleSheet("color: #555; font-style: italic;")
            layout.addWidget(note)

        grid = QGridLayout()
        for key, (row, col), label in self._LAYOUT:
            plot = self._build_box_plot(metrics.get(key, []), label)
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
        plot = pg.PlotWidget()
        plot.setBackground('w')
        plot.setTitle(label)
        plot.showGrid(x=False, y=True)
        plot.getPlotItem().hideAxis('bottom')

        # Filter NaNs (trajectories where the metric couldn't be derived).
        arr = np.asarray([v for v in values if v is not None and not np.isnan(v)],
                         dtype=np.float64)
        if arr.size == 0:
            txt = pg.TextItem("No data", color=(80, 80, 80), anchor=(0.5, 0.5))
            plot.addItem(txt)
            txt.setPos(1, 0)
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

        if outliers:
            scatter = pg.ScatterPlotItem(
                x=[1] * len(outliers), y=outliers,
                pen=pg.mkPen('k'), brush=pg.mkBrush(0, 0, 0, 0), size=6
            )
            plot.addItem(scatter)

        # Pad the X-range so the box is centered and not hugged by the edges.
        plot.setXRange(0.0, 2.0, padding=0)
        
        # Disable auto-range button
        plot.getPlotItem().hideButtons()
        plot.getViewBox().setMenuEnabled(False)
        
        return plot

    # ── Save: HTML with embedded SVG and pan/zoom JS ──────────────────────

    def _on_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Box Plots", "jerk_box_plots.html",
            "HTML Files (*.html);;All Files (*)"
        )
        if not path:
            return

        try:
            svg_blocks = []
            for key, (_row, _col), label in self._LAYOUT:
                plot = self._plots[key]
                svg_text = self._export_plot_to_svg(plot)
                svg_blocks.append((label, svg_text))

            html = self._build_html(svg_blocks)
            Path(path).write_text(html, encoding="utf-8")
        except Exception as e:
            logger.exception("Failed to save HTML output")
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n{e}")

    @staticmethod
    def _export_plot_to_svg(plot_widget: pg.PlotWidget) -> str:
        """Use pyqtgraph's built-in SVG exporter to grab the plot SVG as text."""
        from pyqtgraph.exporters import SVGExporter
        exporter = SVGExporter(plot_widget.getPlotItem())
        # SVGExporter.export(toBytes=True) returns an XML string in pyqtgraph.
        svg = exporter.export(toBytes=True)
        if isinstance(svg, (bytes, bytearray)):
            svg = svg.decode("utf-8")
        return svg

    @staticmethod
    def _build_html(svg_blocks):
        # Minimal HTML with a 2x2 grid of SVG containers and a tiny pan/zoom script.
        cells = []
        for i, (label, svg) in enumerate(svg_blocks):
            cells.append(f"""
              <div class="cell">
                <div class="label">{label}</div>
                <div class="svgwrap" id="wrap{i}">{svg}</div>
              </div>
            """)

        script = r"""
        // Minimal SVG pan/zoom: wheel to zoom, drag to pan. Per-cell, independent.
        document.querySelectorAll('.svgwrap').forEach(function(wrap) {
          const svg = wrap.querySelector('svg');
          if (!svg) return;
          // Ensure a viewBox exists so transforms behave predictably.
          if (!svg.getAttribute('viewBox')) {
            const w = svg.getAttribute('width')  || svg.clientWidth  || 600;
            const h = svg.getAttribute('height') || svg.clientHeight || 400;
            svg.setAttribute('viewBox', '0 0 ' + parseFloat(w) + ' ' + parseFloat(h));
          }
          svg.setAttribute('width', '100%');
          svg.setAttribute('height', '100%');
          let vb = svg.viewBox.baseVal;
          let drag = null;

          svg.addEventListener('wheel', function(e) {
            e.preventDefault();
            const scale = e.deltaY < 0 ? 0.9 : 1.1;
            const pt = svg.createSVGPoint();
            pt.x = e.clientX; pt.y = e.clientY;
            const cursor = pt.matrixTransform(svg.getScreenCTM().inverse());
            vb.x = cursor.x - (cursor.x - vb.x) * scale;
            vb.y = cursor.y - (cursor.y - vb.y) * scale;
            vb.width  *= scale;
            vb.height *= scale;
          }, { passive: false });

          svg.addEventListener('mousedown', function(e) {
            drag = { x: e.clientX, y: e.clientY, vbx: vb.x, vby: vb.y };
          });
          window.addEventListener('mousemove', function(e) {
            if (!drag) return;
            const ctm = svg.getScreenCTM();
            const dx = (e.clientX - drag.x) / ctm.a;
            const dy = (e.clientY - drag.y) / ctm.d;
            vb.x = drag.vbx - dx;
            vb.y = drag.vby - dy;
          });
          window.addEventListener('mouseup', function() { drag = null; });
        });
        """

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Jerk Analysis Box Plots</title>
  <style>
    body {{ font-family: sans-serif; margin: 16px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .cell {{ border: 1px solid #ddd; padding: 8px; }}
    .label {{ font-weight: bold; margin-bottom: 4px; }}
    .svgwrap {{ width: 100%; height: 320px; overflow: hidden; cursor: grab; }}
    .svgwrap:active {{ cursor: grabbing; }}
    svg {{ display: block; }}
  </style>
</head>
<body>
  <h2>Jerk Analysis Box Plots</h2>
  <div class="grid">
    {''.join(cells)}
  </div>
  <script>{script}</script>
</body>
</html>
"""