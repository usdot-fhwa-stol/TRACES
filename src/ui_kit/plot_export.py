"""Save plots as self-contained, interactive HTML files.

The page draws each plot on a <canvas> with plot_view.js -- a small viewer
written for this application (no third-party libraries) that sits beside
this module in ui_kit. It is read with importlib.resources and
inlined, so the saved file works offline and on its own.

Data travel in one block that the browser does not execute:

    <script type="application/json" id="plot-data"> { ... } </script>

Schema (Python dicts / lists; the helpers below build the pieces)
------------------------------------------------------------------
    document  {title, columns, cellHeight, plots: [plot | {"spacer": True}]}
    plot      {title, xLabel, yLabel, xName, xUnit, yUnit, showXAxis,
               grid, xLink, yFit, xRange, yRange, legend, cursor, items}
    items     curve  {type, name, x, y, pen, shadow, z, legend, visible, link}
              vline  {type, x, pen, z}
              region {type, x0, x1, brush, z}
              box    {type, x, q1, median, q3, lo, hi, outliers, boxWidth,
                      whiskerWidth, pen, brush, z}
    pen       {color, width, dash: [dash_px, gap_px] | None}

Plots sharing an xLink key share their time range and hover cursor; curves
sharing a link key toggle together from any legend. NaN is written as null
and breaks a line, like connect="finite".

Viewer controls (pyqtgraph conventions): wheel zooms (over an axis: that
axis only), drag pans, double-click resets, legend entries show/hide
series, hovering shows the cursor and per-plot values or box statistics.

Current consumers
-----------------
  ui_kit/kinematic_stacked_plot_widget.py
                                          KinematicStackedPlotWidget.export_spec
  analysis/consistency/internal_consistency_result_dialog.py
  analysis/spline/spline_testing_dialog.py
  analysis/jerk/jerk_box_plot_dialog.py
  analysis/missing_data/missing_data_box_plot_dialog.py
"""

import html
import json
import math
from importlib.resources import files
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

_ASSET_PACKAGE = "ui_kit"
_VIEWER_SCRIPT = "plot_view.js"

# Significant digits kept for plotted values: ample for display and tooltips,
# and it keeps long trajectories' files compact.
_SIG_DIGITS = 7

_PAGE_CSS = """
body { font-family: sans-serif; margin: 16px; color: #000; background: #fff; }
h2 { margin: 0 0 4px 0; font-size: 18px; }
.pv-hint { color: #555; font-size: 12px; margin-bottom: 12px; }
#plots { display: grid; gap: 12px; }
.pv-cell { position: relative; border: 1px solid #ddd; overflow: hidden;
           user-select: none; }
.pv-cell canvas { display: block; }
.pv-legend { position: absolute; background: rgba(255,255,255,0.82);
             border: 1px solid #999; padding: 3px 6px; font-size: 12px; }
.pv-entry { display: flex; align-items: center; gap: 6px; cursor: pointer;
            padding: 1px 0; white-space: nowrap; }
.pv-entry.pv-hidden { opacity: 0.35; }
.pv-tip { position: absolute; display: none; pointer-events: none;
          background: #ffffe1; border: 1px solid #767676; padding: 2px 5px;
          font-size: 11px; white-space: pre; color: #000; }
"""

_HINT = ("Wheel: zoom (over an axis: that axis only) · Drag: pan · "
         "Double-click: reset · Click a legend entry to show/hide it · "
         "Hover near a curve for values")


# ── Assets ─────────────────────────────────────────────────────────────────

def export_plot_to_svg(plot_widget) -> str:
    """Return a pyqtgraph PlotWidget as SVG for spectral plot downloads."""
    from pyqtgraph.exporters import SVGExporter
    svg = SVGExporter(plot_widget.getPlotItem()).export(toBytes=True)
    return svg.decode("utf-8") if isinstance(svg, (bytes, bytearray)) else svg


def load_asset(name: str) -> str:
    """Text of a file in the ui_kit package (e.g. plot_view.js)."""
    return files(_ASSET_PACKAGE).joinpath(name).read_text(encoding="utf-8")


# ── Value conversion ───────────────────────────────────────────────────────

def json_values(values) -> List[Optional[float]]:
    """Array-like -> list of floats for JSON; NaN/inf -> None (a line break)."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    out: List[Optional[float]] = []
    for v in arr.tolist():
        out.append(float(f"{v:.{_SIG_DIGITS}g}") if math.isfinite(v) else None)
    return out


def color_css(color) -> str:
    """QColor (or anything pg.mkColor accepts) -> CSS rgba() string."""
    import pyqtgraph as pg
    c = pg.mkColor(color)
    a = c.alpha() / 255.0
    return f"rgba({c.red()},{c.green()},{c.blue()},{a:.3g})"


def pen_spec(pen) -> Optional[Dict[str, Any]]:
    """QPen -> {color, width, dash}, with the dash in screen pixels (Qt
    stores a custom dash in multiples of the pen width). None for no pen."""
    from PyQt6.QtCore import Qt
    if pen is None or pen.style() == Qt.PenStyle.NoPen:
        return None
    width = max(pen.widthF(), 1.0)
    dash = None
    if pen.style() == Qt.PenStyle.CustomDashLine:
        dash = [round(d * width, 2) for d in pen.dashPattern()]
    elif pen.style() == Qt.PenStyle.DashLine:
        dash = [4 * width, 2 * width]
    elif pen.style() == Qt.PenStyle.DotLine:
        dash = [1 * width, 2 * width]
    return {"color": color_css(pen.color()), "width": pen.widthF() or 1.0, "dash": dash}


# ── Item and plot builders ─────────────────────────────────────────────────

def curve_item(name: str, x, y, pen: Dict[str, Any], *,
               shadow: Optional[Dict[str, Any]] = None, z: float = 0.0,
               legend: bool = True, visible: bool = True,
               link: Optional[str] = None) -> Dict[str, Any]:
    return {"type": "curve", "name": name, "x": json_values(x), "y": json_values(y),
            "pen": pen, "shadow": shadow, "z": z, "legend": legend,
            "visible": visible, "link": link}


def vline_item(x: float, pen: Dict[str, Any], z: float = 5.0) -> Dict[str, Any]:
    return {"type": "vline", "x": float(x), "pen": pen, "z": z}


def region_item(x0: float, x1: float, brush: str, z: float = -10.0) -> Dict[str, Any]:
    return {"type": "region", "x0": float(x0), "x1": float(x1), "brush": brush, "z": z}


def box_item(x: float, q1: float, median: float, q3: float, lo: float, hi: float,
             outliers: Iterable = (), *, box_width: float = 0.5,
             whisker_width: float = 0.3, z: float = 0.0) -> Dict[str, Any]:
    """Box-and-whisker glyph drawn like ui_kit/box_plot_item.BoxPlotItem.
    outliers: (y, label) pairs -- the label (e.g. "veh_id: 12") shows on
    hover -- or bare y values."""
    outs = []
    for o in outliers:
        if isinstance(o, (tuple, list)):
            outs.append({"y": float(o[0]), "label": None if o[1] is None else str(o[1])})
        else:
            outs.append({"y": float(o), "label": None})
    return {"type": "box", "x": float(x), "q1": float(q1), "median": float(median),
            "q3": float(q3), "lo": float(lo), "hi": float(hi), "outliers": outs,
            "boxWidth": box_width, "whiskerWidth": whisker_width, "z": z}


def plot_spec(items: Sequence[Dict[str, Any]], *, title: str = "",
              x_label: str = "", y_label: str = "", x_name: str = "x",
              x_unit: str = "", y_unit: str = "", show_x_axis: bool = True,
              grid: Optional[float] = 0.3, x_link: Optional[str] = None,
              y_fit: str = "visible", x_range=None, y_range=None,
              legend: Optional[bool] = None, cursor: bool = True) -> Dict[str, Any]:
    """One plot. legend None = automatic (shown with 2+ legend curves)."""
    spec = {"title": title, "xLabel": x_label, "yLabel": y_label, "xName": x_name,
            "xUnit": x_unit, "yUnit": y_unit, "showXAxis": show_x_axis,
            "grid": grid, "xLink": x_link, "yFit": y_fit, "cursor": cursor,
            "items": list(items)}
    if x_range is not None:
        spec["xRange"] = [float(x_range[0]), float(x_range[1])]
    if y_range is not None:
        spec["yRange"] = [float(y_range[0]), float(y_range[1])]
    if legend is not None:
        spec["legend"] = bool(legend)
    return spec


SPACER = {"spacer": True}


# ── Page ───────────────────────────────────────────────────────────────────

def build_plot_html(title: str, plots: Sequence[Dict[str, Any]], *,
                    columns: int = 1, cell_height_px: int = 320) -> str:
    """Complete HTML document showing *plots* (plot_spec dicts or SPACER)
    in a grid of *columns*, each cell *cell_height_px* tall."""
    document = {"title": title, "columns": max(1, int(columns)),
                "cellHeight": int(cell_height_px), "plots": list(plots)}
    # Escape "</" so the data can never close its <script> tag early.
    data = json.dumps(document, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    script = load_asset(_VIEWER_SCRIPT).replace("</script", "<\\/script")
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{safe_title}</title>
  <style>{_PAGE_CSS}</style>
</head>
<body>
  <h2>{safe_title}</h2>
  <div class="pv-hint">{html.escape(_HINT)}</div>
  <div id="plots"></div>
  <script type="application/json" id="plot-data">{data}</script>
  <script>{script}</script>
</body>
</html>"""
