"""Qt side of the Proximity anomaly: Figure 2 row, Figure 3 view, Fix flow.

View: both vehicles on the kinematic stack -- the distance plot is the
time-space diagram the spec asks for -- the follower in the plot color,
the leader in the same color with a black outline (plot_style's "second
object of the same kind"), the anomaly span shaded.

Fix: asks whether a lane assignment error is suspected. No -> "Fix Under
Development." Yes -> ProximityFixDialog (proximity_fix_dialog.py).
"""

from typing import Optional

from PyQt6.QtWidgets import QDialog, QDoubleSpinBox, QMessageBox, QWidget

from ui_kit.plot_style import Role
from .anomaly_ui import AnomalyTypeUI, AnomalyView, ConfigRow, IntervalPlotView
from .lane_anomaly_store import LaneAnomaly
from .lane_correction import neighbor_lanes, vehicle_rows
from .proximity import PROXIMITY_KEY


class ProximityAnomalyView(IntervalPlotView):

    def __init__(self, conn, event_id: int, parent: Optional[QWidget] = None):
        super().__init__(conn, event_id, parent)
        self._first = self.plots.add_series("Follower", Role.MEASURED)
        self._second = self.plots.add_series("Leader", Role.MEASURED, outline=True)

    def show_anomaly(self, anomaly: Optional[LaneAnomaly]) -> None:
        if anomaly is None:
            self.plots.clear_data(self._first)
            self.plots.clear_data(self._second)
            self.show_empty()
            return
        self.show_plots()
        t0, t1 = self.window(anomaly)
        for series, veh in ((self._first, anomaly.veh_id), (self._second, anomaly.other_veh_id)):
            rows = vehicle_rows(self._conn, anomaly.event_id, veh, t0, t1)
            self.plots.set_data(series, rows["time"],
                                {"dist": rows["dist"], "speed": rows["speed"],
                                 "accel": rows["accel"]})
        self.plots.rename_series(self._first, f"Veh {anomaly.veh_id} (follower)")
        self.plots.rename_series(self._second, f"Veh {anomaly.other_veh_id} (leader)")

        spacing = anomaly.metrics.get("min_spacing_m")
        title = (f"Anomaly #{anomaly.anomaly_id} -- lane {anomaly.lane_ids[0]}"
                 + (f", minimum spacing {spacing:.2f} m" if spacing is not None else "")
                 + (" -- fixed" if anomaly.is_fixed else ""))
        self.plots.set_title("dist", title)
        self.shade(anomaly)
        self.zoom(anomaly)


class ProximityUI(AnomalyTypeUI):
    BUTTON_TEXT = "Proximity Anomalies"
    COUNT_TEXT = "Proximity anomalies detected: {n}"

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

    def create_view(self, parent: QWidget, conn, event_id: int) -> AnomalyView:
        return ProximityAnomalyView(conn, event_id, parent)

    def fix(self, parent: QWidget, conn, anomaly: LaneAnomaly) -> bool:
        answer = QMessageBox.question(
            parent, "Proximity Fix",
            f"Is a lane assignment error suspected for anomaly #{anomaly.anomaly_id} "
            f"(vehicles {anomaly.veh_id} and {anomaly.other_veh_id})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.No:
            QMessageBox.information(parent, "Proximity Fix", "Fix Under Development.")
            return False
        if answer != QMessageBox.StandardButton.Yes:
            return False

        if not neighbor_lanes(conn, anomaly.event_id, int(anomaly.lane_ids[0])):
            QMessageBox.information(
                parent, "Proximity Fix",
                f"Lane {anomaly.lane_ids[0]} has no neighboring lane in this event, "
                f"so the vehicles can't be reassigned.")
            return False

        from .proximity_fix_dialog import ProximityFixDialog
        dlg = ProximityFixDialog(parent, conn, anomaly)
        return dlg.exec() == QDialog.DialogCode.Accepted


ANOMALY_UI = ProximityUI(PROXIMITY_KEY)
