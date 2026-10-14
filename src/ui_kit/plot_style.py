"""Shared pens, brushes and plot appearance for pyqtgraph plots.

One place for the graphic attributes that were previously repeated in each
dialog (the CustomDashLine [4, 4] pen in StitchingPreviewDialog,
SplineTestingDialog and InternalConsistencyResultDialog; the white
background / black axis setup in KinematicPlotStack; GapReviewDialog's gap
status brushes).

Design language
---------------
Each visual channel has one job, in every plot dialog:

    color      which kinematic -- KINEMATIC_COLORS (Okabe-Ito, colorblind-safe):
                   distance vermillion, speed bluish green,
                   acceleration blue, jerk amber
               plus PROPOSAL_COLOR (reddish purple) for anything not yet saved
    line style data status -- solid = measured, long dashes = derived
               (computed from other data), short dashes = auto-filled
    width      emphasis -- 4 px for data, thinner for overlays and markers
    outline    second object of the same kind, e.g. Traj 2 in Trajectory
               Stitching: same color with a black outline (outline_pen);
               a white outline (HALO_COLOR) is contrast only, for lines
               crossing shaded regions
    black/grey axes, grid, labels and reference markers only

Roles
-----
A series is drawn according to its *role* -- what the line means. Each role
supplies a width, a dash style, a default color, a z-value and whether it
belongs in a legend. A default color of None means "the plot's kinematic
color" (see resolve_color):

    MEASURED   measured / original data              solid, plot color
    DERIVED    calculated values, spline fit          dashed, plot color
    PREVIEW    unsaved result (stitched line, View)   dashed, proposal purple, on top
    SEAM       one-step join inside a proposal        solid, proposal purple, not in
               (too short to show a dash pattern)     legend
    AUTO_FILL  provisional auto-imputed segments      short dashes, plot color
    BOUNDARY   overlap / gap bounds                   black dashed, not in legend
    SPLIT      stitching split position (proposal)    purple dashed, not in legend
    CROSSHAIR  hover line                             thin grey dashes, not in legend

Width-aware dashes
------------------
Qt measures a dash pattern in multiples of the pen width, so a fixed
[4, 4] pattern gives 16 px dashes on a width-4 line but 8 px on width 2.
dashed_pen() takes the dash and gap in *pixels* and divides by the width,
so every dashed line has the same on-screen rhythm whatever its width.

Typical use
-----------
    from ui_kit.plot_style import Role, resolve_color, role_pen, role_z, apply_plot_style

    apply_plot_style(pw)
    color = resolve_color(Role.DERIVED, "speed")       # speed's bluish green
    item = pg.PlotDataItem(pen=role_pen(Role.DERIVED, color))
    item.setZValue(role_z(Role.DERIVED))

KinematicStackedPlotWidget.add_series() does this for you.

Run this module directly for a visual check of every role.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Sequence, Tuple, Union

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen

ColorLike = Union[str, tuple, "pg.QtGui.QColor"]

# ── Colors ─────────────────────────────────────────────────────────────────

RED =      "#e6194B"
GREEN =    "#3cb44b"
YELLOW =   "#ffe119"
BLUE =     "#4363d8"
ORANGE =   "#f58231"
PURPLE =   "#911eb4"
CYAN =     "#42d4f4"
MAGENTA =  "#f032e6"
LIME =     "#bfef45"
PINK =     "#fabed4"
TEAL =     "#469990"
LAVENDER = "#dcbeff"
BROWN =    "#9a6324"
BEIGE =    "#fffac8"
MAROON =   "#800000"
MINT =     "#aaffc3"
OLIVE =    "#808000"
APRICOT =  "#ffd8b1"
NAVY =     "#000075"
GREY =     "#a9a9a9"
WHITE =    "#ffffff"
BLACK =    "#000000"

# One color per kinematic, the same in every dialog (Okabe-Ito palette).
KINEMATIC_COLORS: Dict[str, str] = {
    "dist":  RED,
    "speed": GREEN,
    "accel": BLUE,
    "jerk":  ORANGE,
}

# Anything proposed but not yet saved (stitch preview, gap fill preview,
# stitching split line). Not used by any kinematic.
PROPOSAL_COLOR = MAGENTA

FALLBACK_COLOR = BLACK

# Outline for a second object of the same kind (e.g. Traj 2): a black pen
# drawn under the colored line, OUTLINE_PX wider on each side.
OUTLINE_COLOR = BLACK
OUTLINE_PX = 1.5

# Halo: the same outline in white. Invisible on the white background, but
# keeps a line readable where it crosses shaded regions (GapReview's gap
# status bands). Contrast only -- unlike the black outline it carries no
# meaning.
HALO_COLOR = WHITE

# ── Dash patterns (on-screen pixels: dash, gap) ────────────────────────────

DASH_PX: Tuple[float, float] = (16.0, 16.0)        # matches the old [4, 4] at width 4
SHORT_DASH_PX: Tuple[float, float] = (6.0, 6.0)    # provisional / auto-filled data
FINE_DASH_PX: Tuple[float, float] = (4.0, 4.0)     # thin helper lines (crosshair)


# ── Roles ──────────────────────────────────────────────────────────────────

class Role(Enum):
    MEASURED = "measured"
    DERIVED = "derived"
    PREVIEW = "preview"
    SEAM = "seam"
    AUTO_FILL = "auto_fill"
    BOUNDARY = "boundary"
    SPLIT = "split"
    CROSSHAIR = "crosshair"


@dataclass(frozen=True)
class RoleStyle:
    width: float
    dash_px: Optional[Tuple[float, float]]   # None = solid
    color: Optional[ColorLike]               # None = the plot's kinematic color
    z: float
    in_legend: bool


ROLE_STYLES: Dict[Role, RoleStyle] = {
    Role.MEASURED:  RoleStyle(width=4.0, dash_px=None,          color=None,           z=0.0,  in_legend=True),
    Role.DERIVED:   RoleStyle(width=4.0, dash_px=DASH_PX,       color=None,           z=1.0,  in_legend=True),
    Role.PREVIEW:   RoleStyle(width=2.0, dash_px=DASH_PX,       color=PROPOSAL_COLOR, z=10.0, in_legend=True),
    Role.SEAM:      RoleStyle(width=4.0, dash_px=None,          color=PROPOSAL_COLOR, z=0.5,  in_legend=False),
    Role.AUTO_FILL: RoleStyle(width=2.0, dash_px=SHORT_DASH_PX, color=None,           z=2.0,  in_legend=True),
    Role.BOUNDARY:  RoleStyle(width=3.0, dash_px=DASH_PX,       color="k",            z=5.0,  in_legend=False),
    Role.SPLIT:     RoleStyle(width=3.0, dash_px=DASH_PX,       color=PROPOSAL_COLOR, z=6.0,  in_legend=False),
    Role.CROSSHAIR: RoleStyle(width=1.0, dash_px=FINE_DASH_PX,  color="#888888",      z=20.0, in_legend=False),
}


def resolve_color(role: Role, key: Optional[str] = None,
                  color: Optional[ColorLike] = None) -> ColorLike:
    """Color for a *role* line on plot *key*: an explicit *color* wins,
    then the role's own color (proposal purple, marker black), then the
    plot's kinematic color, then black."""
    if color is not None:
        return color
    if ROLE_STYLES[role].color is not None:
        return ROLE_STYLES[role].color
    return KINEMATIC_COLORS.get(key, FALLBACK_COLOR)


# ── Pens ───────────────────────────────────────────────────────────────────

def dashed_pen(color: ColorLike, width: float,
               dash_px: Sequence[float] = DASH_PX) -> QPen:
    """Cosmetic dashed pen whose dash and gap are *dash_px* screen pixels
    regardless of *width*."""
    pen = pg.mkPen(color, width=width)
    unit = max(float(width), 1.0)   # a cosmetic width-0 pen is drawn 1 px wide
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern([max(px / unit, 0.01) for px in dash_px])
    return pen


def solid_pen(color: ColorLike, width: float) -> QPen:
    return pg.mkPen(color, width=width)


def role_pen(role: Role, color: Optional[ColorLike] = None,
             width: Optional[float] = None) -> QPen:
    """Pen for *role*. *color* and *width* override the role's defaults;
    with neither a *color* nor a role color, the line is black -- pass
    resolve_color(role, key) to get the plot's kinematic color."""
    style = ROLE_STYLES[role]
    c = resolve_color(role, None, color)
    w = style.width if width is None else width
    if style.dash_px is None:
        return solid_pen(c, w)
    return dashed_pen(c, w, style.dash_px)


def outline_pen(line_width: float, color: ColorLike = OUTLINE_COLOR,
                dash_px: Optional[Sequence[float]] = None) -> QPen:
    """Pen to draw *under* a line of *line_width*, showing as an
    OUTLINE_PX border on each side. Use as a curve's shadowPen.

    color: OUTLINE_COLOR (black) marks a second object of the same kind;
        HALO_COLOR (white) just separates a line from shaded regions.
    dash_px: the line's own dash pattern, so a dashed line gets a dashed
        outline whose dashes line up (both are in screen pixels)."""
    width = line_width + 2 * OUTLINE_PX
    if dash_px is None:
        return solid_pen(color, width)
    return dashed_pen(color, width, dash_px)


def role_z(role: Role) -> float:
    return ROLE_STYLES[role].z


def role_in_legend(role: Role) -> bool:
    return ROLE_STYLES[role].in_legend


# ── Region brushes ─────────────────────────────────────────────────────────
# Named rather than keyed by imputed-status constants so this module doesn't
# depend on analysis/; GapReviewDialog maps its statuses onto these names.

REGION_RGBA: Dict[str, Tuple[int, int, int, int]] = {
    "auto":     (255, 255, 0, 80),     # yellow -- auto-imputed, awaiting review
    "manual":   (0, 200, 0, 80),       # green  -- manually applied
    "unfilled": (255, 100, 100, 80),   # light red -- needs attention
    "default":  (200, 200, 200, 80),   # grey -- unknown status
}


def region_brush(name: str):
    return pg.mkBrush(REGION_RGBA.get(name, REGION_RGBA["default"]))


# ── Plot appearance ────────────────────────────────────────────────────────

GRID_ALPHA = 0.3
BACKGROUND = "w"
AXIS_COLOR = "k"
TITLE_SIZE = "9pt"


def apply_plot_style(pw: pg.PlotWidget, grid: bool = True) -> None:
    """White background, black axes without SI-prefix rescaling, light grid.
    Same look as KinematicPlotStack, now shared."""
    pw.setBackground(BACKGROUND)
    for axis in ("left", "bottom"):
        ax = pw.getAxis(axis)
        ax.setPen(AXIS_COLOR)
        ax.setTextPen(AXIS_COLOR)
        ax.enableAutoSIPrefix(False)
    if grid:
        pw.showGrid(x=True, y=True, alpha=GRID_ALPHA)


# ── Visual check ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import numpy as np
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    pw = pg.PlotWidget(title="plot_style roles")
    apply_plot_style(pw)
    legend = pw.addLegend(offset=(5, 5))
    t = np.linspace(0, 10, 200)

    # (label, role, plot key for color, width override, outlined)
    rows = [
        ("distance measured",       Role.MEASURED,  "dist",  None, False),
        ("speed measured",          Role.MEASURED,  "speed", None, False),
        ("speed, 2nd trajectory",   Role.MEASURED,  "speed", None, True),
        ("acceleration derived",    Role.DERIVED,   "accel", None, False),
        ("jerk measured",           Role.MEASURED,  "jerk",  None, False),
        ("speed auto-fill",         Role.AUTO_FILL, "speed", None, False),
        ("proposal (preview)",      Role.PREVIEW,   None,    None, False),
        ("derived at width 2",      Role.DERIVED,   "dist",  2.0,  False),
    ]
    for i, (name, role, key, width, outlined) in enumerate(rows):
        pen = role_pen(role, resolve_color(role, key), width)
        kwargs = {"shadowPen": outline_pen(pen.widthF())} if outlined else {}
        item = pg.PlotDataItem(t, np.full_like(t, len(rows) - i), pen=pen, **kwargs)
        item.setZValue(role_z(role))
        pw.addItem(item)
        legend.addItem(item, name)

    for role, x in ((Role.BOUNDARY, 3), (Role.SPLIT, 5), (Role.CROSSHAIR, 7)):
        line = pg.InfiniteLine(pos=x, angle=90, pen=role_pen(role))
        line.setZValue(role_z(role))
        pw.addItem(line, ignoreBounds=True)

    region = pg.LinearRegionItem(values=(8, 9.5), brush=region_brush("auto"),
                                 pen=pg.mkPen(None), movable=False)
    region.setZValue(-10)
    pw.addItem(region, ignoreBounds=True)

    pw.resize(900, 500)
    pw.show()
    sys.exit(app.exec())
