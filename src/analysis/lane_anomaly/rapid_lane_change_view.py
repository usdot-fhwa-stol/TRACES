"""Qt side of the Rapid Lane Change anomaly: Figure 2 row, review panel
text, and fix panel.

Fix panel: the segment (Start / End, default the anomaly span) and the
lane to put the vehicle in for it -- the anomaly's two lanes as radio
buttons, each with its sample count in the span, the one holding more
preselected; or "Other lane" with a combo of the event's remaining lanes.
Apply moves every sample of the segment not already in that lane. Lane
only; positions are unchanged (rapid_lane_change's module docstring).
"""

from typing import List

from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDoubleSpinBox, QHBoxLayout, QRadioButton,
    QVBoxLayout, QWidget,
)

from .anomaly_ui import AnomalyTypeUI, ConfigRow, FixPanel, SegmentFixPanel
from .lane_anomaly_store import LaneAnomaly
from .lane_correction import LaneMove
from .rapid_lane_change import RAPID_LANE_CHANGE_KEY


class RapidLaneChangeFixPanel(SegmentFixPanel):

    def _build_choices(self, layout: QVBoxLayout) -> None:
        self._group = QButtonGroup(self)
        self._lane_a = QRadioButton()
        self._lane_b = QRadioButton()
        self._other = QRadioButton("Other lane:")
        self._other_combo = QComboBox()
        for button in (self._lane_a, self._lane_b, self._other):
            self._group.addButton(button)
            button.toggled.connect(self._changed)
        layout.addWidget(self._lane_a)
        layout.addWidget(self._lane_b)
        row = QHBoxLayout()
        row.addWidget(self._other)
        row.addWidget(self._other_combo, stretch=1)
        layout.addLayout(row)
        self._other.toggled.connect(self._other_combo.setEnabled)
        self._other_combo.currentIndexChanged.connect(self._changed)

    def _load_choices(self, anomaly: LaneAnomaly) -> None:
        lane_a, lane_b = (int(l) for l in anomaly.lane_ids[:2])
        n_a = anomaly.metrics.get("samples_lane_a")
        n_b = anomaly.metrics.get("samples_lane_b")
        self._lane_a.setText(f"Lane {lane_a}" + (f"  ({n_a} samples)" if n_a is not None else ""))
        self._lane_b.setText(f"Lane {lane_b}" + (f"  ({n_b} samples)" if n_b is not None else ""))
        self._lane_a.setProperty("lane", lane_a)
        self._lane_b.setProperty("lane", lane_b)

        self._other_combo.clear()
        for lane in self._type.fix_target_lanes(self._conn, anomaly):
            if int(lane) not in (lane_a, lane_b):
                self._other_combo.addItem(f"Lane {lane}", int(lane))
        self._other.setEnabled(self._other_combo.count() > 0)

        default = self._type.default_target_lane(self._conn, anomaly, anomaly.veh_id)
        (self._lane_b if default == lane_b else self._lane_a).setChecked(True)
        self._other_combo.setEnabled(False)

    def _target(self):
        if self._other.isChecked():
            return self._other_combo.currentData()
        for button in (self._lane_a, self._lane_b):
            if button.isChecked():
                return button.property("lane")
        return None

    def _moves(self) -> List[LaneMove]:
        lane = self._target()
        if lane is None:
            return []
        lo, hi = self._seg
        return [LaneMove(int(self._anomaly.veh_id), lo, hi, int(lane))]


class RapidLaneChangeUI(AnomalyTypeUI):
    BUTTON_TEXT = "Rapid Lane Change Anomalies"

    def create_config_row(self, parent: QWidget) -> ConfigRow:
        spin = QDoubleSpinBox()
        spin.setRange(0.1, 60.0)
        spin.setDecimals(1)
        spin.setSingleStep(0.1)
        spin.setValue(float(self.type.OPTIONS_SCHEMA["max_interval_s"]))
        min_changes = self.type.OPTIONS_SCHEMA["min_changes"]
        spin.setToolTip(
            f"A vehicle switching back and forth between two neighboring lanes at "
            f"least {min_changes} times, with no more than this long between "
            f"consecutive switches, is reported.")
        return ConfigRow(RAPID_LANE_CHANGE_KEY, "Rapid Lane Change", "max_interval_s",
                         "Max Time Between Changes", "s", spin, parent)

    def create_fix_panel(self, parent: QWidget, conn, event_id: int) -> FixPanel:
        return RapidLaneChangeFixPanel(self, conn, event_id, parent)

    def title(self, anomaly: LaneAnomaly) -> str:
        changes = anomaly.metrics.get("changes")
        lane_a, lane_b = anomaly.lane_ids[0], anomaly.lane_ids[1]
        return (f"Anomaly #{anomaly.anomaly_id} -- vehicle {anomaly.veh_id}"
                + (f", {changes} lane changes" if changes else "")
                + f" between lanes {lane_a} and {lane_b}"
                + (" -- fixed" if anomaly.is_fixed else ""))


ANOMALY_UI = RapidLaneChangeUI(RAPID_LANE_CHANGE_KEY)
