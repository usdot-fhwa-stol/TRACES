"""
ViewBox whose visible range is owned by a pair of range sliders.

Plots in this app keep their visible range in superqt range sliders, which
are the single source of truth: each slider's valueChanged handler calls
ViewBox.setRange. SliderViewBox turns mouse gestures into slider changes
instead of moving the view itself, so the sliders and the view can never
disagree and the owner's slider handler (min-span enforcement, logging,
etc.) runs for every gesture:

- Left-drag in the plot area pans both axes. Left-drag over an axis pans
  only that axis (pyqtgraph's AxisItem.mouseDragEvent forwards the drag
  here with axis=0 for the bottom axis, axis=1 for the left axis).
- The scroll wheel zooms both axes by the same factor about the cursor.
  Over an axis only that axis zooms (AxisItem.wheelEvent forwards the
  event here with axis=0/1).

Pans and zooms keep the window inside the slider's hard limits; pans never
change the window span and zooms never go below the minimum span.

The window helpers (zoom_slider, pan_slider, enforce_min_span) are also
used by CustomViewBox, which drives TimeSpacePlotWidget's sliders.
"""

import pyqtgraph as pg
from PyQt6.QtCore import Qt

ZOOM_FACTOR = 0.9   # < 1 -> scroll-up zooms in


def _clamp_window(lo, span, s_min, s_max):
    """Shift a window of fixed *span* starting at *lo* so it lies inside
    [s_min, s_max]. A window wider than the limits becomes the full range.

    Returns:
        tuple[float, float]: (lo, hi)
    """
    if span >= s_max - s_min:
        return s_min, s_max
    lo = min(max(lo, s_min), s_max - span)
    return lo, lo + span


def zoom_slider(slider, anchor, scale, min_span):
    """Scale a range slider's window about *anchor*, keeping the anchor at
    the same fractional position, clamped to *min_span* and to the
    slider's hard limits."""
    lo, hi = slider.value()
    span = hi - lo
    frac = max(0.0, min(1.0, (anchor - lo) / span if span else 0.5))
    new_span = max(span * scale, min_span)
    slider.setValue(_clamp_window(anchor - frac * new_span, new_span,
                                  slider.minimum(), slider.maximum()))


def pan_slider(slider, start, delta):
    """Set a range slider's window to the *start* window shifted by
    *delta*, keeping its span and clamping to the slider's hard limits.

    Panning from a fixed start window (rather than from the current value)
    means rounding in the slider never accumulates over a long drag.
    """
    lo0, hi0 = start
    slider.setValue(_clamp_window(lo0 + delta, hi0 - lo0,
                                  slider.minimum(), slider.maximum()))


def enforce_min_span(slider, min_span):
    """Widen a range slider's window to *min_span* about its center if it
    is narrower, clamped to the slider's hard limits. The widened value is
    written back with signals blocked so the caller's handler does not
    re-enter.

    Returns:
        tuple[float, float]: The (possibly widened) window.
    """
    lo, hi = slider.value()
    if hi - lo >= min_span:
        return lo, hi
    lo, hi = _clamp_window((lo + hi) / 2.0 - min_span / 2.0, min_span,
                           slider.minimum(), slider.maximum())
    slider.blockSignals(True)
    slider.setValue((lo, hi))
    slider.blockSignals(False)
    return lo, hi


class SliderViewBox(pg.ViewBox):
    """ViewBox that pans and zooms by driving an x and a y range slider.

    Call bind_sliders() once the sliders exist, and set_min_spans() whenever
    the slider limits change. The owner connects the sliders' valueChanged
    signals to a handler that calls setRange on this view box.

    Attributes:
        min_x_span (float): Smallest x window a zoom may produce.
        min_y_span (float): Smallest y window a zoom may produce.
        is_panning (bool): True while a left-drag pan is in progress, so the
            owner can suppress hover tooltips.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The default pyqtgraph handlers must never move the view directly.
        self.setMouseEnabled(x=False, y=False)
        self.setMenuEnabled(False)
        self._x_slider = None
        self._y_slider = None
        self.min_x_span = 0.0
        self.min_y_span = 0.0
        self.is_panning = False
        self._drag_start = None   # (x window, y window, (px, py) view units per pixel)

    def bind_sliders(self, x_slider, y_slider):
        """Attach the horizontal (x) and vertical (y) range sliders."""
        self._x_slider = x_slider
        self._y_slider = y_slider

    def set_min_spans(self, min_x_span, min_y_span):
        """Set the zoom floors for each axis."""
        self.min_x_span = min_x_span
        self.min_y_span = min_y_span

    def mouseDragEvent(self, ev, axis=None):
        """Left-drag pans by moving the slider windows.

        Args:
            ev:   pyqtgraph MouseDragEvent.
            axis: None for both axes, 0 for x only, 1 for y only.
        """
        if ev.button() != Qt.MouseButton.LeftButton or self._x_slider is None:
            ev.ignore()
            return
        ev.accept()

        if ev.isStart():
            self._drag_start = (self._x_slider.value(), self._y_slider.value(),
                                self.viewPixelSize())
            self.is_panning = True
        if self._drag_start is None:
            return

        x_start, y_start, (px, py) = self._drag_start
        moved = ev.scenePos() - ev.buttonDownScenePos()
        # Content follows the cursor: dragging right moves the window left;
        # scene y grows downward, so dragging down moves the window up.
        if axis in (None, 0):
            pan_slider(self._x_slider, x_start, -moved.x() * px)
        if axis in (None, 1):
            pan_slider(self._y_slider, y_start, moved.y() * py)

        if ev.isFinish():
            self._drag_start = None
            self.is_panning = False

    def wheelEvent(self, ev, axis=None):
        """Zoom about the cursor by scaling the slider windows.

        Args:
            ev:   QGraphicsSceneWheelEvent.
            axis: None for both axes, 0 for x only, 1 for y only.
        """
        delta = ev.delta()
        if delta == 0 or self._x_slider is None:
            ev.accept()
            return

        scale = ZOOM_FACTOR if delta > 0 else 1.0 / ZOOM_FACTOR
        # Over an axis only the coordinate along that axis is meaningful,
        # and only that one is used.
        point = self.mapSceneToView(ev.scenePos())
        if axis in (None, 0):
            zoom_slider(self._x_slider, point.x(), scale, self.min_x_span)
        if axis in (None, 1):
            zoom_slider(self._y_slider, point.y(), scale, self.min_y_span)
        ev.accept()