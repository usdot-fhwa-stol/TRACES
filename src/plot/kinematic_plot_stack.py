"""Distance / speed / acceleration plots stacked vertically on one linked
time axis. Plots for columns absent from the dataset are kept (so the
layout doesn't jump) but titled "not available" and receive no curves."""

from typing import Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QVBoxLayout, QWidget


class KinematicPlotStack(QWidget):

    KEYS = ("dist", "speed", "accel")
    _LABELS = {
        "dist":  ("Distance traveled", "m",    "veh_dist_trav"),
        "speed": ("Speed",             "m/s",  "veh_speed"),
        "accel": ("Acceleration",      "m/s²", "veh_accel"),
    }

    def __init__(self, parent=None, has_speed: bool = True, has_accel: bool = True):
        super().__init__(parent)
        self._available = {"dist": True, "speed": has_speed, "accel": has_accel}
        self.plots: Dict[str, pg.PlotWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        first = None
        for key in self.KEYS:
            title, unit, col = self._LABELS[key]
            pw = pg.PlotWidget(background="w")
            for axis in ("left", "bottom"):
                ax = pw.getAxis(axis)
                ax.setPen("k")
                ax.setTextPen("k")
                ax.enableAutoSIPrefix(False)
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.setLabel("left", title, units=unit)
            if not self._available[key]:
                pw.setTitle(f"{title} ({col} not available in this dataset)", size="9pt")
            pw.getViewBox().setAutoVisible(y=True)   # y fits what's in view
            if first is None:
                first = pw
            else:
                pw.setXLink(first)
            layout.addWidget(pw, stretch=1)
            self.plots[key] = pw

        self.plots["accel"].setLabel("bottom", "Time", units="s")

    def available(self, key: str) -> bool:
        return self._available[key]

    # ── Curves ─────────────────────────────────────────────────────────

    def add_curves(self, pen, z: float = 0.0) -> Dict[str, pg.PlotDataItem]:
        """One empty curve per available plot; fill with set_curves()."""
        curves = {}
        for key in self.KEYS:
            if self._available[key]:
                item = pg.PlotDataItem(pen=pen)
                item.setZValue(z)
                self.plots[key].addItem(item)
                curves[key] = item
        return curves

    @staticmethod
    def set_curves(curves: Dict[str, pg.PlotDataItem], time: np.ndarray,
                   values: Dict[str, Optional[np.ndarray]]) -> None:
        for key, item in curves.items():
            y = values.get(key)
            if y is None:
                item.setData([], [])
            else:
                item.setData(time, y, connect="finite")

    @staticmethod
    def set_visible(curves: Dict[str, pg.PlotDataItem], visible: bool) -> None:
        for item in curves.values():
            item.setVisible(visible)

    # ── Markers / view ─────────────────────────────────────────────────

    def add_vlines(self, xs, pen, z: float = 5.0) -> List[pg.InfiniteLine]:
        """A vertical line at each x on every plot. Returns all lines."""
        lines = []
        for x in xs:
            for pw in self.plots.values():
                line = pg.InfiniteLine(pos=x, angle=90, pen=pen, movable=False)
                line.setZValue(z)
                pw.addItem(line)
                lines.append(line)
        return lines

    def set_x_range(self, lo: float, hi: float) -> None:
        self.plots["dist"].setXRange(lo, hi, padding=0)   # others follow via XLink