"""Qt half of the Lane-Level Anomaly extension point (see anomaly_registry).

AnomalyTypeUI supplies, for one AnomalyType:

    create_config_row()  its row in Select Anomalies to Identify (Figure 2):
                         a checkbox, plus a right-justified parameter
                         spinbox and unit when the type has one (ConfigRow)
    BUTTON_TEXT          its button in the review panel (Figure 3)
    COUNT_TEXT           its count label at the top left of Figure 3
    create_view()        the widget shown under the count labels for its
                         selected anomaly (an AnomalyView)
    fix()                the Fix flow; returns True if a fix was applied

Undo is generic (the review dialog calls lane_correction.undo_correction).

IntervalPlotView is the shared base of the built-in views: a
KinematicStackedPlotWidget (distance -- the time-space diagram -- speed,
acceleration) with the anomaly's span shaded and zoomed to.
"""

from abc import abstractmethod
from typing import Dict, Optional, Tuple

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from utils import events_columns, event_time_step
from ui_kit.kinematic_stacked_plot_widget import KinematicStackedPlotWidget
from ui_kit.plot_style import BLUE, BROWN, NAVY, OLIVE, ORANGE, PURPLE, TEAL, region_brush
from .anomaly_registry import anomaly_type
from .lane_anomaly_store import LaneAnomaly

# Distinct colors for lanes in anomaly views. Not KINEMATIC_COLORS' meaning
# (each plot's own kinematic), so passed as explicit series colors.
LANE_PALETTE = (BLUE, ORANGE, TEAL, PURPLE, BROWN, NAVY, OLIVE)

# Shading of the selected anomaly's span.
SPAN_REGION = "unfilled"   # light red -- "needs attention" in plot_style
FIXED_REGION = "manual"    # green -- a fix is in effect

_VIEW_KINEMATICS = ("dist", "speed", "accel")
_MIN_PAD_S = 3.0


def lane_color(index: int):
    return LANE_PALETTE[index % len(LANE_PALETTE)]


class ConfigRow(QWidget):
    """One Figure 2 row: [x] Label ............ Param label [spin] unit"""

    def __init__(self, key: str, checkbox_text: str,
                 param_key: Optional[str] = None, param_label: str = "",
                 unit: str = "", spin: Optional[QAbstractSpinBox] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.key = key
        self._param_key = param_key
        self._spin = spin
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.checkbox = QCheckBox(checkbox_text)
        row.addWidget(self.checkbox)
        row.addStretch(1)
        if spin is not None:
            label = QLabel(param_label)
            label.setBuddy(spin)
            row.addWidget(label)
            row.addWidget(spin)
            row.addWidget(QLabel(unit))
            self.checkbox.toggled.connect(spin.setEnabled)
            self.checkbox.toggled.connect(label.setEnabled)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def options(self) -> Dict[str, float]:
        if self._spin is None or self._param_key is None:
            return {}
        return {self._param_key: self._spin.value()}

    def set_controls_enabled(self, enabled: bool) -> None:
        self.checkbox.setEnabled(enabled)
        if self._spin is not None:
            self._spin.setEnabled(enabled and self.checkbox.isChecked())


class AnomalyView(QWidget):
    """Left-side content of Figure 3 for one anomaly type."""

    def show_anomaly(self, anomaly: Optional[LaneAnomaly]) -> None:
        raise NotImplementedError


class IntervalPlotView(AnomalyView):
    """Kinematic stack with the anomaly span shaded (green once fixed),
    zoomed so the span fills the middle of the view."""

    def __init__(self, conn, event_id: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._conn = conn
        self._event_id = event_id
        cols = events_columns(conn)
        self._plots = KinematicStackedPlotWidget(
            kinematics=_VIEW_KINEMATICS,
            available={"speed": "veh_speed" in cols, "accel": "veh_accel" in cols},
            y_fit="visible", time_step=event_time_step(conn, event_id))
        self._placeholder = QLabel("No anomaly selected.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plots, stretch=1)
        layout.addWidget(self._placeholder, stretch=1)
        self._placeholder.hide()
        self._regions: Dict[str, pg.LinearRegionItem] = {}

    @property
    def plots(self) -> KinematicStackedPlotWidget:
        return self._plots

    @staticmethod
    def window(anomaly: LaneAnomaly) -> Tuple[float, float]:
        """Time window loaded around an anomaly."""
        pad = max(_MIN_PAD_S, anomaly.duration)
        return anomaly.start_time - pad, anomaly.end_time + pad

    def show_empty(self) -> None:
        self._plots.hide()
        self._placeholder.show()

    def show_plots(self) -> None:
        self._placeholder.hide()
        self._plots.show()

    def shade(self, anomaly: LaneAnomaly) -> None:
        brush = region_brush(FIXED_REGION if anomaly.is_fixed else SPAN_REGION)
        for key in self._plots.keys:
            if not self._plots.available(key):
                continue
            region = self._regions.get(key)
            if region is None:
                region = pg.LinearRegionItem(movable=False, pen=pg.mkPen(None))
                region.setZValue(-10)
                self._plots.add_item(key, region)
                self._regions[key] = region
            region.setBrush(brush)
            region.setRegion((anomaly.start_time, anomaly.end_time))
            region.update()

    def zoom(self, anomaly: LaneAnomaly) -> None:
        """Span centered at half the plot width, like Gap Review."""
        width = max(anomaly.duration, 0.5)
        self._plots.set_x_range(anomaly.start_time - width / 2, anomaly.end_time + width / 2)
        for pw in self._plots.plots.values():
            pw.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)


class AnomalyTypeUI:
    """Qt side of one anomaly type. Subclasses set BUTTON_TEXT/COUNT_TEXT."""

    BUTTON_TEXT = ""
    COUNT_TEXT = "{label} anomalies detected: {n}"

    def __init__(self, type_key: str):
        self._type_key = type_key

    @property
    def type(self):
        return anomaly_type(self._type_key)

    def count_text(self, n: int, n_fixed: int = 0) -> str:
        text = self.COUNT_TEXT.format(label=self.type.LABEL, n=n)
        return f"{text} ({n_fixed} fixed)" if n_fixed else text

    @abstractmethod
    def create_config_row(self, parent: QWidget) -> ConfigRow: ...

    @abstractmethod
    def create_view(self, parent: QWidget, conn, event_id: int) -> AnomalyView: ...

    @abstractmethod
    def fix(self, parent: QWidget, conn, anomaly: LaneAnomaly) -> bool: ...
