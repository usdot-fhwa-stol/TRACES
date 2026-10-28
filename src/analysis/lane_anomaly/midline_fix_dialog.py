"""Rapid Lane Change fix window (Task 9.3: "generate a midline trajectory
... A slider should be provided to allow the user to adjust the corrected
trajectory position, moving the midline closer to either the first or
second lane").

Plots the vehicle's samples per lane (same colors as the review view) and
the corrected trajectory as a dashed preview (plot_style's proposal
color) on distance, speed and acceleration. The slider runs from the
first lane (left) to the second (right); the preview's distance moves
with it, while its speed and acceleration -- recomputed from the fitted
curve -- don't depend on it (rapid_lane_change module docstring). The
label under the slider names the lane the corrected rows will get: the
one the slider leans toward.

Apply writes through RapidLaneChangeType.apply_fix (IMPUTED_CORRECTED).
"""

import logging
import sqlite3
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from utils import events_columns, event_time_step
from ui_kit.kinematic_stacked_plot_widget import KinematicStackedPlotWidget
from ui_kit.plot_style import Role, region_brush
from .anomaly_ui import SPAN_REGION, lane_color
from .lane_anomaly_store import LaneAnomaly
from .rapid_lane_change import MidlineModel, RapidLaneChangeType
from .rapid_lane_change_view import add_lane_series

logger = logging.getLogger(__name__)

_SLIDER_STEPS = 100


class MidlineFixDialog(QDialog):

    def __init__(self, parent: Optional[QWidget], conn: sqlite3.Connection,
                 anomaly: LaneAnomaly, model: MidlineModel):
        super().__init__(parent)
        self.setWindowTitle(f"Rapid Lane Change Fix -- Anomaly #{anomaly.anomaly_id}")
        self.setModal(True)
        self._conn = conn
        self._anomaly = anomaly
        self._model = model
        self._plan = None
        self._size_and_center()
        self._build_ui()
        self._draw_samples()
        self._on_slider(self._slider.value())

    def _size_and_center(self):
        parent = self.parent()
        if parent is not None:
            geom = parent.frameGeometry()
            self.resize(int(geom.width() * 0.8), int(geom.height() * 0.8))
            frame = self.frameGeometry()
            frame.moveCenter(geom.center())
            self.move(frame.topLeft())
        else:
            self.resize(1100, 700)

    def _build_ui(self):
        main = QHBoxLayout(self)
        cols = events_columns(self._conn)
        self._plots = KinematicStackedPlotWidget(
            kinematics=("dist", "speed", "accel"),
            available={"speed": "veh_speed" in cols, "accel": "veh_accel" in cols},
            y_fit="visible", time_step=event_time_step(self._conn, self._anomaly.event_id))
        main.addWidget(self._plots, stretch=1)

        m = self._model
        self._lane_a = add_lane_series(self._plots, f"Lane {m.lane_a}", lane_color(0))
        self._lane_b = add_lane_series(self._plots, f"Lane {m.lane_b}", lane_color(1))
        self._preview = self._plots.add_series("Corrected (preview)", Role.PREVIEW)
        for key in self._plots.keys:
            if self._plots.available(key):
                region = pg.LinearRegionItem(
                    values=(self._anomaly.start_time, self._anomaly.end_time),
                    movable=False, brush=region_brush(SPAN_REGION), pen=pg.mkPen(None))
                region.setZValue(-10)
                self._plots.add_item(key, region)

        right = QVBoxLayout()
        right.addWidget(QLabel("Corrected position:"))
        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel(f"Lane {m.lane_a}"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, _SLIDER_STEPS)
        self._slider.setValue(_SLIDER_STEPS // 2)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(_SLIDER_STEPS // 10)
        self._slider.valueChanged.connect(self._on_slider)
        slider_row.addWidget(self._slider, stretch=1)
        slider_row.addWidget(QLabel(f"Lane {m.lane_b}"))
        right.addLayout(slider_row)

        self._info = QLabel()
        self._info.setWordWrap(True)
        right.addWidget(self._info)
        note = QLabel("Speed and acceleration of the corrected samples are "
                      "recomputed from the corrected position.")
        note.setWordWrap(True)
        note.setStyleSheet("font-style: italic;")
        right.addWidget(note)
        right.addStretch(1)

        buttons = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(apply_btn)
        buttons.addWidget(cancel)
        right.addLayout(buttons)

        panel = QWidget()
        panel.setLayout(right)
        panel.setMinimumWidth(280)
        main.addWidget(panel)

    def _draw_samples(self):
        m = self._model
        for series, lane in ((self._lane_a, m.lane_a), (self._lane_b, m.lane_b)):
            mask = m.fit_lanes == lane
            self._plots.set_data(series, m.fit_times, {
                "dist": np.where(mask, m.fit_dist, np.nan),
                "speed": np.where(mask, m.fit_speed, np.nan),
                "accel": np.where(mask, m.fit_accel, np.nan)})
        if m.fit_times.size:
            self._plots.set_x_range(float(m.fit_times[0]), float(m.fit_times[-1]))

    def _on_slider(self, value: int):
        weight = value / _SLIDER_STEPS
        self._plan = self._model.plan(weight)
        p = self._plan
        self._plots.set_data(self._preview, p.times,
                             {"dist": p.dist, "speed": p.speed, "accel": p.accel})
        m = self._model
        self._info.setText(
            f"{weight:.0%} of the way from lane {m.lane_a} to lane {m.lane_b} "
            f"(lane offset {m.delta:.2f} m).\n"
            f"{p.times.size} sample(s) from {self._anomaly.start_time:.2f} s to "
            f"{self._anomaly.end_time:.2f} s will be assigned to lane {p.lane}.")

    def _on_apply(self):
        try:
            RapidLaneChangeType.apply_fix(self._conn, self._anomaly, self._plan)
        except ValueError as exc:
            QMessageBox.warning(self, "Rapid Lane Change Fix", str(exc))
            return
        except (sqlite3.Error, RuntimeError) as exc:
            logger.exception("MidlineFixDialog: apply failed")
            QMessageBox.critical(self, "Rapid Lane Change Fix",
                                 f"The fix could not be saved:\n{exc}\n\nNothing was changed.")
            return
        self.accept()
