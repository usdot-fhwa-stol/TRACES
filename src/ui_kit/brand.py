"""TRACES branding: name, colors, logo and icon, background gradient.

The one place the application's look is defined -- dialogs import from
here instead of hard-coding colors or asset paths.

Assets live in the assets/ folder next to config/ (src/assets in the
repository) and are found through utils.get_resource_path, so they work
both from source and from the PyInstaller build (the two SVGs are
bundled with --add-data "src/assets/<name>;assets"):

    traces_logo.svg   full logo with the TRACES lettering (About dialog);
                      the letters are grouped as <g id="lettering"> so
                      logo_pixmap can recolor them (LOGO_LETTERING)
    traces_icon.svg   magnifying glass only (window and taskbar icon)
    traces.ico        Windows icon for the .exe and installer; generated
                      from traces_icon.svg by assets/make_icons.py, not
                      used at run time

SVG is drawn with PyQt6.QtSvg's renderer at the screen's actual pixel
density, so the logo stays sharp at any display scaling. A missing asset
is logged and drawn as nothing; the application still runs.
"""

import logging
import math
import os
import re
import sys
from functools import lru_cache
from typing import List, Tuple

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from utils import get_resource_path

logger = logging.getLogger(__name__)

# ── Name ────────────────────────────────────────────────────────────────────

APP_NAME = "TRACES"
APP_VERSION = "1.0"          # keep in step with APP_VERSION in .github/workflows/build.yml

# ── Colors ──────────────────────────────────────────────────────────────────

EISENHOWER_BLUE = "#1C6EA8"  # logo ring and handle; the accent color
CARMA_YELLOW = "#EFBD27"     # logo trace lines
SAXTON_BLUE = "#001E51"      # logo lens and lettering

ACCENT = SAXTON_BLUE     # hover borders, links, highlighted text

# Color of the TRACES lettering when the logo is drawn in the application.
# The logo file's own Saxton blue nearly vanishes on a dark theme (contrast
# about 1:1); Eisenhower blue reads on both light (about 5:1) and dark
# (about 3:1) backgrounds. Only the letters change -- the lens keeps its
# Saxton blue, framed by the Eisenhower-blue ring.
LOGO_LETTERING = EISENHOWER_BLUE

# Text drawn directly on the window background (About dialog headings) --
# the same reasoning as LOGO_LETTERING.
HEADING_COLOR = EISENHOWER_BLUE

# ── Assets ──────────────────────────────────────────────────────────────────

LOGO_SVG = "traces_logo.svg"
ICON_SVG = "traces_icon.svg"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def asset_path(name: str) -> str:
    """Absolute path of a file in the assets folder."""
    return get_resource_path(os.path.join("assets", name))


@lru_cache(maxsize=None)
def _renderer(name: str) -> QSvgRenderer:
    renderer = QSvgRenderer(asset_path(name))
    if not renderer.isValid():
        logger.warning("Brand asset missing or unreadable: %s", asset_path(name))
    return renderer


_LETTERING_GROUP = re.compile(r'(<g id="lettering"[^>]*>)(.*?)(</g>)', re.DOTALL)
_FILL = re.compile(r'fill="#[0-9A-Fa-f]{3,8}"')


@lru_cache(maxsize=None)
def _logo_renderer(lettering_color: str) -> QSvgRenderer:
    """The logo with its <g id="lettering"> paths filled *lettering_color*.
    Falls back to the file's own colors (with a warning) if the group is
    missing, e.g. after the logo file is replaced by a fresh export."""
    path = asset_path(LOGO_SVG)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        logger.warning("Brand asset missing or unreadable: %s", path)
        return QSvgRenderer()

    def recolor(match):
        return match.group(1) + _FILL.sub(f'fill="{lettering_color}"', match.group(2)) + match.group(3)

    recolored, count = _LETTERING_GROUP.subn(recolor, text, count=1)
    if not count:
        logger.warning('%s has no <g id="lettering"> group; drawing the lettering '
                       'in the file\'s own color', path)
    renderer = QSvgRenderer(QByteArray(recolored.encode("utf-8")))
    if not renderer.isValid():
        logger.warning("Brand asset unreadable: %s", path)
    return renderer


def _render(renderer: QSvgRenderer, width: int, device_pixel_ratio: float) -> QPixmap:
    if not renderer.isValid():
        return QPixmap()
    view = renderer.viewBoxF()
    aspect = view.height() / view.width() if view.width() > 0 else 1.0
    dpr = max(float(device_pixel_ratio), 1.0)
    pixmap = QPixmap(QSize(round(width * dpr), round(width * aspect * dpr)))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def svg_pixmap(name: str, width: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """*name* drawn *width* logical pixels wide, height from its aspect
    ratio, rendered at *device_pixel_ratio* so it is sharp on high-DPI
    screens, in the file's own colors. A null pixmap if the asset is
    missing."""
    return _render(_renderer(name), width, device_pixel_ratio)


def logo_pixmap(width: int, device_pixel_ratio: float = 1.0,
                lettering_color: str = LOGO_LETTERING) -> QPixmap:
    """The full logo, with the TRACES lettering in *lettering_color*
    (LOGO_LETTERING by default; pass SAXTON_BLUE for the logo exactly as
    in the file, e.g. on a white background)."""
    return _render(_logo_renderer(lettering_color), width, device_pixel_ratio)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """The magnifying-glass icon at every standard size. Built from
    pixmaps rather than QIcon(svg_path), which would need Qt's SVG icon
    engine plugin in the build."""
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = svg_pixmap(ICON_SVG, size)
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    return icon


def apply_application_branding(app) -> None:
    """Call once, right after creating the QApplication: application name
    and the window icon every window inherits. When run from Python on
    Windows (not the built .exe), also gives the process its own taskbar
    identity, so the taskbar shows the TRACES icon instead of Python's."""
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(app_icon())
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.app")
        except (AttributeError, OSError):
            logger.debug("Could not set the Windows AppUserModelID", exc_info=True)


# ── Background gradient ─────────────────────────────────────────────────────
# Vertical: Eisenhower blue at the top, CARMA yellow at the bottom. Blending
# blue straight into yellow passes through a muddy grey-green, so the two
# halves fade toward white instead -- but only GRADIENT_TINT of the way:
#
#     top     Eisenhower blue
#             pale blue    (blue taken GRADIENT_TINT of the way to white)
#             -- smooth blend over the middle GRADIENT_BLEND of the height --
#             pale yellow  (yellow taken GRADIENT_TINT of the way to white)
#     bottom  CARMA yellow
#
# Never reaching white avoids the bright flare a white midpoint shows,
# which the white home-screen card overlapping it makes worse. The colors
# are blended in OKLab (even perceived steps) along a smooth, non-
# overshooting curve through those four colors, so there is no visible
# seam or crease where one blend hands over to the next.

GRADIENT_TINT = 0.80     # how far toward white the two pale middle colors are
GRADIENT_BLEND = 0.10    # fraction of the height over which pale blue becomes pale yellow
_GRADIENT_STOPS = 97     # stops given to Qt, which blends linearly between them


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _to_oklab(hex_color: str) -> Tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(int(hex_color[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _from_oklab(L: float, a: float, b: float) -> QColor:
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return QColor(*(round(_linear_to_srgb(v) * 255) for v in rgb))


def _pchip(xs: List[float], ys: List[float], x: float) -> float:
    """Shape-preserving cubic through (xs, ys) (Fritsch-Carlson): smooth,
    and never overshoots between knots, so no band gets lighter or darker
    than the colors either side of it."""
    h = [b - a for a, b in zip(xs, xs[1:])]
    d = [(ys[k + 1] - ys[k]) / h[k] for k in range(len(h))]
    m = [d[0]] + [0.0] * (len(xs) - 2) + [d[-1]]
    for k in range(1, len(xs) - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    i = min(max(sum(1 for xk in xs[1:-1] if xk < x), 0), len(h) - 1)
    t = (x - xs[i]) / h[i]
    return ((2 * t ** 3 - 3 * t ** 2 + 1) * ys[i] + (t ** 3 - 2 * t ** 2 + t) * h[i] * m[i]
            + (-2 * t ** 3 + 3 * t ** 2) * ys[i + 1] + (t ** 3 - t ** 2) * h[i] * m[i + 1])


@lru_cache(maxsize=1)
def background_gradient_stops() -> List[Tuple[float, QColor]]:
    """[(position 0..1, color), ...] from the top of the area to the bottom."""
    top, bottom, white = _to_oklab(EISENHOWER_BLUE), _to_oklab(CARMA_YELLOW), _to_oklab("#FFFFFF")

    def toward_white(color):
        return tuple(c + (wc - c) * GRADIENT_TINT for c, wc in zip(color, white))

    knots = [0.0, 0.5 - GRADIENT_BLEND / 2, 0.5 + GRADIENT_BLEND / 2, 1.0]
    colors = [top, toward_white(top), toward_white(bottom), bottom]
    stops = []
    for i in range(_GRADIENT_STOPS):
        t = i / (_GRADIENT_STOPS - 1)
        lab = [_pchip(knots, [c[k] for c in colors], t) for k in range(3)]
        stops.append((t, _from_oklab(*lab)))
    return stops


def background_gradient(height: float) -> QLinearGradient:
    """The vertical brand gradient for an area *height* pixels tall."""
    gradient = QLinearGradient(0, 0, 0, max(float(height), 1.0))
    for position, color in background_gradient_stops():
        gradient.setColorAt(position, color)
    return gradient
