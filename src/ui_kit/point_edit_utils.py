"""Shared point-interaction primitives for click/drag point editing on a
pyqtgraph plot -- click-vs-drag disambiguation and nearest-point search.

Introduced for GapPointEditor (ui_kit/gap_point_editor.py). ShockwaveModeHandler
(plot/handlers/shockwave_handler.py) reuses is_click() for its own click-vs-drag check
-- see on_mouse_release. Its nearest-point search is *not* shared here: it
measures distance in data-space (time/distance plot units, already tuned via
_CLICK_RADIUS) rather than the pixel/scene-space find_nearest_point() uses,
and layers in domain-specific filtering find_nearest_point() has no notion
of -- merging them would change real click tolerances, not just internal
structure, so it stays separate.
"""

import time
from typing import Optional, Sequence, Tuple

from PyQt6.QtCore import QPointF


def is_click(
    press_pos: QPointF, release_pos: QPointF,
    press_time: float, release_time: float,
    move_tol: float = 5.0, time_tol: float = 0.5,
) -> bool:
    """True if a press/release pair reads as a click rather than a drag --
    little enough on-screen movement, and quick enough, that it's more
    likely to be an intended click than a brief drag. Positions are
    expected in the same coordinate space (scene or pixel); move_tol is
    in that space's units.
    """
    moved = (release_pos - press_pos).manhattanLength()
    elapsed = release_time - press_time
    return moved <= move_tol and elapsed <= time_tol


def find_nearest_point(
    scene_pos: QPointF,
    points: Sequence[Tuple[float, float]],
    view_box,
    pixel_radius: float = 12.0,
) -> Optional[int]:
    """Index of the point in *points* (data coordinates) closest to
    scene_pos (scene/pixel coordinates) in on-screen distance, or None if
    the nearest one is farther than pixel_radius pixels away. Distance is
    computed in scene space (via view_box.mapViewToScene) rather than
    data space, so the pixel_radius hit-test stays consistent regardless
    of zoom level or the two axes' differing units/scale.
    """
    if not points:
        return None

    best_idx: Optional[int] = None
    best_dist = pixel_radius
    for i, (x, y) in enumerate(points):
        p_scene = view_box.mapViewToScene(QPointF(x, y))
        dist = (p_scene - scene_pos).manhattanLength()
        if dist <= best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def now() -> float:
    """Monotonic clock for press/release timing -- wraps time.monotonic()
    so callers (and tests) have one place to swap the clock source."""
    return time.monotonic()