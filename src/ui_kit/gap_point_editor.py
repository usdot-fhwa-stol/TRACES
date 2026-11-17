"""Self-contained click/Ctrl-click/drag point editor for one pyqtgraph
plot -- the "Add Points" interaction from Task9.1_v2.1 (Figure 3): click
adds a point, Ctrl+click removes one, drag moves one, Enter commits,
Escape discards. Used by GapReviewDialog on whichever of its three plots
matches the gap's current fitting base.

Runs as a Qt event filter installed on the target PlotWidget for the
duration of the edit session, rather than a CustomViewBox subclass --
GapReviewDialog's three plots are plain pg.PlotWidget()s built once in
_build_ui, and installing/removing a filter on demand avoids replacing
their ViewBox just for the occasional "Add Points" session. Normal pan/
zoom on the target plot is disabled while active (setMouseEnabled(False,
False)) so a left-click is unambiguous -- point interaction, not panning
-- and restored when the session ends either way.

Deliberately not built on plot/handlers/interaction_mode.py's InteractionModeHandler:
that framework is wired to the main TimeSpacePlotWidget/CustomViewBox mode
system (right-click menu, mode switching across the whole app), and this
editor only ever touches one dialog-local plot for as long as "Add Points"
is active. See point_edit_utils.py for the two pieces of logic this does
share with ShockwaveModeHandler's similar (but main-plot-bound) editing.
"""

import logging
from typing import List, Optional, Tuple

import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, pyqtSignal

from .point_edit_utils import find_nearest_point, is_click, now

logger = logging.getLogger(__name__)

_PIXEL_HIT_RADIUS = 12.0


class GapPointEditor(QObject):
    """Point editor for one plot, restricted to an x-range.

    points_changed(list) fires after every add/delete/drag-in-progress
    update, with the current [(x, y), ...] list, so a caller can redraw a
    live dashed-preview without waiting for the session to end.

    finished(bool) fires once, when the session ends -- True via Enter
    (commit), False via Escape (discard). This class doesn't remember its
    starting points, so "discard" is the caller's responsibility: it
    should keep whatever it had before calling GapPointEditor rather than
    reading .points when finished(False) arrives.
    """

    points_changed = pyqtSignal(list)
    finished = pyqtSignal(bool)

    def __init__(
        self, plot_widget: pg.PlotWidget, x_min: float, x_max: float,
        initial_points: Optional[List[Tuple[float, float]]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._plot_widget = plot_widget
        self._view_box = plot_widget.getPlotItem().vb
        self._x_min = x_min
        self._x_max = x_max
        self._points: List[Tuple[float, float]] = list(initial_points or [])

        self._drag_index: Optional[int] = None
        self._press_scene_pos: Optional[QPointF] = None
        self._press_time: float = 0.0
        self._press_had_ctrl: bool = False
        self._panning: bool = False
        self._pan_last_scene_pos: Optional[QPointF] = None
        self._active = True
        self._mouse_grabbed = False

        # setMouseEnabled stays False here -- pan/zoom are now driven manually
        # below (see _on_move / _on_wheel) instead of through pyqtgraph's own
        # mouse handling, so a left-click on a point stays unambiguous and no
        # built-in gesture (rect-zoom, middle-drag, etc.) can sneak in.
        self._was_mouse_enabled = list(self._view_box.state.get("mouseEnabled", [True, True]))
        self._view_box.setMouseEnabled(x=False, y=False)
    
        self._was_auto_range = self._view_box.autoRangeEnabled()
        self._view_box.enableAutoRange(x=False, y=False)

        self._scatter = pg.ScatterPlotItem(
            size=10, brush=pg.mkBrush(255, 140, 0, 200), pen=pg.mkPen('k', width=1),
        )
        self._plot_widget.addItem(self._scatter)
        self._redraw_points()

        self._plot_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._plot_widget.setFocus()
        self._plot_widget.installEventFilter(self)
        self._plot_widget.viewport().installEventFilter(self)

    @property
    def points(self) -> List[Tuple[float, float]]:
        return list(self._points)

    # ── Session end ──────────────────────────────────────────────────────

    def finish(self, commit: bool):
        if not self._active:
            return
        self._active = False
        if self._mouse_grabbed:
            self._plot_widget.releaseMouse()
            self._mouse_grabbed = False
        self._drag_index = None
        self._panning = False

        self._pan_last_scene_pos = None
        self._plot_widget.removeEventFilter(self)
        self._plot_widget.viewport().removeEventFilter(self)
        self._plot_widget.removeItem(self._scatter)
        self._view_box.setMouseEnabled(*self._was_mouse_enabled)
        x_auto, y_auto = self._was_auto_range
        self._view_box.enableAutoRange(x=x_auto, y=y_auto)
        self.finished.emit(commit)

    # ── Rendering ────────────────────────────────────────────────────────

    def _redraw_points(self):
        self._scatter.setData(
            [p[0] for p in self._points], [p[1] for p in self._points],
        )

    # ── Event filter ─────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        if not self._active or obj not in (self._plot_widget, self._plot_widget.viewport()):
            return False

        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            return self._on_press(event)
        elif et == QEvent.Type.MouseMove:
            return self._on_move(event)
        elif et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            return self._on_release(event)
        elif et == QEvent.Type.Wheel:                       # NEW
            return self._on_wheel(event)
        elif et == QEvent.Type.KeyPress:
            return self._on_key(event)
        return False

    def _scene_pos(self, event) -> QPointF:
        return self._plot_widget.mapToScene(event.position().toPoint())

    def _data_pos(self, scene_pos: QPointF) -> QPointF:
        return self._view_box.mapSceneToView(scene_pos)

    def _on_press(self, event) -> bool:
        scene_pos = self._scene_pos(event)
        self._press_scene_pos = scene_pos
        self._pan_last_scene_pos = scene_pos
        self._press_time = now()
        self._press_had_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
    
        idx = find_nearest_point(scene_pos, self._points, self._view_box, _PIXEL_HIT_RADIUS)
        if idx is not None and self._press_had_ctrl:
            del self._points[idx]
            self._redraw_points()
            self.points_changed.emit(self.points)
            self._press_scene_pos = None
            return True
        if idx is not None:
            self._drag_index = idx
        self._plot_widget.grabMouse() # force all moves/release to this widget
        self._mouse_grabbed = True    # also cover the pan case
        return True

    def _on_move(self, event) -> bool:
        if self._drag_index is not None:
            data_pos = self._data_pos(self._scene_pos(event))
            x = min(max(data_pos.x(), self._x_min), self._x_max)
            self._points[self._drag_index] = (x, data_pos.y())
            self._redraw_points()
            self.points_changed.emit(self.points)
            return True
    
        if self._press_scene_pos is None:
            return False   # no button down (a Ctrl+click delete already resolved this press)
    
        scene_pos = self._scene_pos(event)
        if not self._panning and not is_click(self._press_scene_pos, scene_pos, self._press_time, now()):
            self._panning = True   # crossed the click threshold -- this is a drag, not a click
    
        if self._panning:
            last_data = self._data_pos(self._pan_last_scene_pos)
            cur_data = self._data_pos(scene_pos)
            self._view_box.translateBy(x=last_data.x() - cur_data.x(), y=last_data.y() - cur_data.y())
    
        self._pan_last_scene_pos = scene_pos
        return True

    def _on_release(self, event) -> bool:
        if self._mouse_grabbed:
            self._plot_widget.releaseMouse()
            self._mouse_grabbed = False
        if self._drag_index is not None:
            self._drag_index = None
            return True

        if self._press_scene_pos is None:
            return True   # a Ctrl+click delete already handled this press
    
        scene_pos = self._scene_pos(event)
        if not self._press_had_ctrl and not self._panning and is_click(
            self._press_scene_pos, scene_pos, self._press_time, now()
        ):
            data_pos = self._data_pos(scene_pos)
            if self._x_min <= data_pos.x() <= self._x_max:
                self._points.append((data_pos.x(), data_pos.y()))
                self._redraw_points()
                self.points_changed.emit(self.points)
    
        self._press_scene_pos = None
        self._pan_last_scene_pos = None
        self._panning = False
        return True

    def _on_key(self, event) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(commit=True)
            return True
        if key == Qt.Key.Key_Escape:
            self.finish(commit=False)
            return True
        return False

    def _on_wheel(self, event) -> bool:
        delta = event.angleDelta().y()
        if delta == 0:
            return True
        scale = 0.999 ** delta   # tune sensitivity to taste
        center = self._data_pos(self._plot_widget.mapToScene(event.position().toPoint()))
        self._view_box.scaleBy((scale, scale), center=(center.x(), center.y()))
        return True