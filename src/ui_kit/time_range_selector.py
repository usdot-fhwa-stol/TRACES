"""Draggable time range on a pyqtgraph plot that snaps to sample times.

A movable LinearRegionItem plus the bookkeeping every "pick a segment of
this trajectory" control needs: snapping both edges to the nearest
available sample when a drag ends, programmatic set_range() without
feedback loops, and one range_changed(lo, hi) signal for both.

Used by the Lane-Level Anomalies Proximity fix window; generic, so
Trajectory Stitching or Gap Interpolation can use it too.

    sel = TimeRangeSelector(plot_widget, 5.0, 7.0, samples=times)
    sel.range_changed.connect(on_range)
    sel.set_samples(other_times)      # re-snaps the current range
"""

from typing import Optional, Sequence, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, pyqtSignal

from .plot_style import region_brush


class TimeRangeSelector(QObject):

    range_changed = pyqtSignal(float, float)

    def __init__(self, plot_widget: pg.PlotWidget, lo: float, hi: float,
                 samples: Optional[Sequence[float]] = None, brush=None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._plot = plot_widget
        self._samples = np.array([], dtype=np.float64)
        self._updating = False
        self._region = pg.LinearRegionItem(
            values=(lo, hi), movable=True,
            brush=brush if brush is not None else region_brush("auto"))
        self._region.setZValue(-5)
        plot_widget.addItem(self._region, ignoreBounds=True)
        self._region.sigRegionChangeFinished.connect(self._on_change_finished)
        if samples is not None:
            self.set_samples(samples, emit=False)

    # ── Public ─────────────────────────────────────────────────────────

    def range(self) -> Tuple[float, float]:
        lo, hi = self._region.getRegion()
        return float(min(lo, hi)), float(max(lo, hi))

    def set_samples(self, samples: Sequence[float], emit: bool = True) -> None:
        """Times the edges snap to; re-snaps the current range."""
        self._samples = np.unique(np.asarray(samples, dtype=np.float64))
        self.set_range(*self.range(), emit=emit)

    def set_range(self, lo: float, hi: float, emit: bool = True) -> None:
        lo, hi = self._snap(lo, hi)
        self._updating = True
        try:
            self._region.setRegion((lo, hi))
        finally:
            self._updating = False
        if emit:
            self.range_changed.emit(lo, hi)

    def set_enabled(self, enabled: bool) -> None:
        self._region.setMovable(enabled)

    def remove(self) -> None:
        self._plot.removeItem(self._region)

    # ── Internal ───────────────────────────────────────────────────────

    def _nearest(self, x: float) -> float:
        s = self._samples
        i = int(np.clip(np.searchsorted(s, x), 1, s.size - 1)) if s.size > 1 else 0
        if s.size > 1 and abs(s[i - 1] - x) <= abs(s[i] - x):
            i -= 1
        return float(s[i])

    def _snap(self, lo: float, hi: float) -> Tuple[float, float]:
        lo, hi = min(lo, hi), max(lo, hi)
        if self._samples.size == 0:
            return float(lo), float(hi)
        return self._nearest(lo), self._nearest(hi)

    def _on_change_finished(self, *_args) -> None:
        if self._updating:
            return
        self.set_range(*self.range())
