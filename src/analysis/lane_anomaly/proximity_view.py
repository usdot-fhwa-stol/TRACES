"""Qt side of the Proximity anomaly: Figure 2 row, review panel text, and
fix panel.

Fix panel: the shared VehicleLanesFixPanel -- the segment (Start / End,
default the anomaly span) and one row per vehicle, follower (amber) and
leader (green), each with a "Move to" combo, Keep by default, offering
every lane of the event. The review panel's Apply moves every vehicle not
left on Keep, together, as one fix (one Undo reverses all of it).

Task 9.3 has Fix first ask whether a lane assignment error is suspected
and answer "Fix Under Development." otherwise. Per customer direction
(2026-10-29) that question is dropped: the fix panel always offers the
lane reassignment.
"""

from PyQt6.QtWidgets import QDoubleSpinBox, QWidget

from .anomaly_ui import AnomalyTypeUI, ConfigRow
from .lane_anomaly_store import LaneAnomaly
from .proximity import PROXIMITY_KEY


class ProximityUI(AnomalyTypeUI):
    BUTTON_TEXT = "Proximity Anomalies"

    def create_config_row(self, parent: QWidget) -> ConfigRow:
        spin = QDoubleSpinBox()
        spin.setRange(0.1, 1000.0)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setValue(float(self.type.OPTIONS_SCHEMA["threshold_m"]))
        spin.setToolTip("Two vehicles in the same lane closer than this, center to "
                        "center (difference in distance traveled), are reported.")
        return ConfigRow(PROXIMITY_KEY, "Proximity", "threshold_m",
                         "Proximity Threshold", "m", spin, parent)

    def title(self, anomaly: LaneAnomaly) -> str:
        spacing = anomaly.metrics.get("min_spacing_m")
        return (f"Anomaly #{anomaly.anomaly_id} -- vehicles {anomaly.veh_id} and "
                f"{anomaly.other_veh_id} in lane {anomaly.lane_ids[0]}"
                + (f", minimum spacing {spacing:.2f} m" if spacing is not None else "")
                + (" -- fixed" if anomaly.is_fixed else ""))

    def vehicle_label(self, anomaly: LaneAnomaly, veh_id: int, index: int) -> str:
        return f"Veh {veh_id} ({'follower' if index == 0 else 'leader'})"


ANOMALY_UI = ProximityUI(PROXIMITY_KEY)