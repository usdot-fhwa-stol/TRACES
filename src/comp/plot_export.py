"""Shared helpers for exporting pyqtgraph plots to self-contained HTML files.

The SVG_PAN_ZOOM_JS constant and build_svg_html() are used by any dialog
that saves one or more pyqtgraph PlotWidgets as an HTML file with interactive
pan and zoom.

Current consumers
-----------------
  comp/jerk/jerk_box_plot_dialog.py       — JerkBoxPlotDialog._build_html
  comp/consistency/internal_consistency_result_dialog.py
                                          — InternalConsistencyResultDialog._build_html
"""

# ---------------------------------------------------------------------------
# Shared pan/zoom JavaScript
# ---------------------------------------------------------------------------
# Wheel to zoom (cursor-centred), mouse-drag to pan.  Works independently on
# each .svgwrap element, so a page with multiple SVG blocks behaves correctly.
# The viewBox is initialised from width/height attributes when absent so that
# svg.viewBox.baseVal manipulation is always valid.

SVG_PAN_ZOOM_JS: str = r"""
document.querySelectorAll('.svgwrap').forEach(function(wrap) {
  const svg = wrap.querySelector('svg');
  if (!svg) return;
  if (!svg.getAttribute('viewBox')) {
    const w = svg.getAttribute('width')  || svg.clientWidth  || 600;
    const h = svg.getAttribute('height') || svg.clientHeight || 400;
    svg.setAttribute('viewBox', '0 0 ' + parseFloat(w) + ' ' + parseFloat(h));
  }
  svg.setAttribute('width',  '100%');
  svg.setAttribute('height', '100%');
  let vb   = svg.viewBox.baseVal;
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


# ---------------------------------------------------------------------------
# SVG export helper
# ---------------------------------------------------------------------------

def export_plot_to_svg(plot_widget) -> str:
    """Return the SVG representation of a pyqtgraph PlotWidget as a string."""
    from pyqtgraph.exporters import SVGExporter
    exporter = SVGExporter(plot_widget.getPlotItem())
    svg = exporter.export(toBytes=True)
    return svg.decode("utf-8") if isinstance(svg, (bytes, bytearray)) else svg


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_svg_html(
    title: str,
    svg_blocks: list,
    *,
    grid_columns: int = 1,
    cell_height_px: int = 320,
) -> str:
    """Return a complete HTML document embedding the supplied SVG blocks.

    Parameters
    ----------
    title:
        ``<title>`` and ``<h2>`` text.
    svg_blocks:
        Sequence of ``(label, svg_text)`` pairs.
    grid_columns:
        Number of CSS grid columns (1 = single stacked column, 2 = 2-up grid).
    cell_height_px:
        Height of each ``.svgwrap`` container in pixels.
    """
    cells = "".join(
        f'<div class="cell">'
        f'<div class="label">{label}</div>'
        f'<div class="svgwrap" id="wrap{i}">{svg}</div>'
        f'</div>'
        for i, (label, svg) in enumerate(svg_blocks)
    )

    grid_css = (
        f"display:grid;grid-template-columns:repeat({grid_columns},1fr);gap:12px;"
        if grid_columns > 1
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <style>
    body {{ font-family: sans-serif; margin: 16px; }}
    .grid {{ {grid_css} }}
    .cell {{ border: 1px solid #ddd; padding: 8px; margin-bottom: 12px; }}
    .label {{ font-weight: bold; margin-bottom: 4px; }}
    .svgwrap {{ width: 100%; height: {cell_height_px}px; overflow: hidden;
                cursor: grab; }}
    .svgwrap:active {{ cursor: grabbing; }}
    svg {{ display: block; }}
  </style>
</head>
<body>
  <h2>{title}</h2>
  <div class="grid">
    {cells}
  </div>
  <script>{SVG_PAN_ZOOM_JS}</script>
</body>
</html>"""