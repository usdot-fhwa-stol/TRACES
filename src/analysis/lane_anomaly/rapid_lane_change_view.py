"""Qt side of the Rapid Lane Change anomaly: Figure 2 row, Figure 3 view,
Fix flow.

View: the vehicle on the kinematic stack (distance = time-space diagram),
its samples split into one series per lane of the anomaly, each lane in
its own color with sample markers -- so every point where the lane
assignment switches is visible, even one-sample runs.

Fix: fits the midline model (rapid_lane_change.fit_midline) and opens
MidlineFixDialog (midline_fix_dialog.py).
"""

from typing import Optional

import numpy as np
from PyQt6.QtWidgets import QDialog, QDoubleSpinBox, QMessageBox, QWidget

from ui_kit.plot_style import Role
from .anomaly_ui import (
    AnomalyTypeUI, AnomalyView, ConfigRow, IntervalPlotView, lane_color,
)
from .lane_anomaly_store import LaneAnomaly
from .lane_correction import vehicle_rows
from .rapid_lane_change import RAPID_LANE_CHANGE_KEY, fit_midline


def add_lane_series(plots, name: str, color):
    """A per-lane series drawn with sample markers in *color*."""
    series = plots.add_series(name, Role.MEASURED, color=color, width=2.0)
    for item in series.items.values():
        item.setSymbol("o")
        item.setSymbolSize(5)
        item.setSymbolBrush(color)
        item.setSymbolPen(None)
    return series


def lane_values(rows, lane) -> dict:
    """rows' kinematics with every sample outside *lane* set to NaN."""
    mask = np.array([l == lane for l in rows["lane"]], dtype=bool)
    return {k: np.where(mask, rows[src], np.nan)
            for k, src in (("dist", "dist"), ("speed", "speed"), ("accel", "accel"))}


class RapidLaneChangeView(IntervalPlotView):

    def __init__(self, conn, event_id: int, parent: Optional[QWidget] = None):
        super().__init__(conn, event_id, parent)
        self._lane_a = add_lane_series(self.plots, "Lane A", lane_color(0))
        self._lane_b = add_lane_series(self.plots, "Lane B", lane_color(1))

    def show_anomaly(self, anomaly: Optional[LaneAnomaly]) -> None:
        if anomaly is None:
            self.plots.clear_data(self._lane_a)
            self.plots.clear_data(self._lane_b)
            self.show_empty()
            return
        self.show_plots()
        t0, t1 = self.window(anomaly)
        rows = vehicle_rows(self._conn, anomaly.event_id, anomaly.veh_id, t0, t1)
        lane_a, lane_b = anomaly.lane_ids[0], anomaly.lane_ids[1]
        self.plots.set_data(self._lane_a, rows["time"], lane_values(rows, lane_a))
        self.plots.set_data(self._lane_b, rows["time"], lane_values(rows, lane_b))
        self.plots.rename_series(self._lane_a, f"Lane {lane_a}")
        self.plots.rename_series(self._lane_b, f"Lane {lane_b}")

        changes = anomaly.metrics.get("changes")
        title = (f"Anomaly #{anomaly.anomaly_id} -- vehicle {anomaly.veh_id}"
                 + (f", {changes} lane changes" if changes else "")
                 + f" between lanes {lane_a} and {lane_b}"
                 + (" -- fixed" if anomaly.is_fixed else ""))
        self.plots.set_title("dist", title)
        self.shade(anomaly)
        self.zoom(anomaly)


class RapidLaneChangeUI(AnomalyTypeUI):
    BUTTON_TEXT = "Rapid Lane Change Anomaly"
    COUNT_TEXT = "Rapid lane change anomalies detected: {n}"

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

    def create_view(self, parent: QWidget, conn, event_id: int) -> AnomalyView:
        return RapidLaneChangeView(conn, event_id, parent)

    def fix(self, parent: QWidget, conn, anomaly: LaneAnomaly) -> bool:
        try:
            model = fit_midline(conn, anomaly)
        except ValueError as exc:
            QMessageBox.warning(parent, "Rapid Lane Change Fix", str(exc))
            return False
        from .midline_fix_dialog import MidlineFixDialog
        dlg = MidlineFixDialog(parent, conn, anomaly, model)
        return dlg.exec() == QDialog.DialogCode.Accepted


ANOMALY_UI = RapidLaneChangeUI(RAPID_LANE_CHANGE_KEY)
