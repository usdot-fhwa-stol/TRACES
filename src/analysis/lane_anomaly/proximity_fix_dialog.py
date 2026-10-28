"""Proximity fix window (Task 9.3, Proximity: "If a lane change is
suspected").

Shows the anomaly's two trajectories on a time-space diagram together with
every trajectory in the neighboring lanes over the same period (each lane
in its own color), and lets the user:

    1. choose which of the two vehicles to reassign,
    2. select the start and end of the segment to move -- by dragging the
       shaded range on the plot or with the two spinboxes; both snap to
       that vehicle's samples,
    3. choose the neighboring lane to move it to, and
    4. Apply.

The selected segment is drawn as a dashed preview (plot_style's proposal
color). Apply writes through ProximityType.apply_fix (IMPUTED_LANE,
kinematics unchanged); refusals -- gap-filled rows, rows already
corrected -- are shown as messages and nothing is written.
"""

import logging
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ui_kit.plot_style import (
    KINEMATIC_COLORS, OUTLINE_COLOR, Role, apply_plot_style, outline_pen,
    role_pen, role_z,
)
from ui_kit.time_range_selector import TimeRangeSelector
from .anomaly_ui import lane_color
from .lane_anomaly_store import LaneAnomaly
from .lane_correction import neighbor_lanes, vehicle_rows
from .proximity import ProximityType, load_lane_window

logger = logging.getLogger(__name__)

_CONTEXT_WIDTH = 1.5
_PAIR_WIDTH = 4.0
_MIN_PAD_S = 5.0


class ProximityFixDialog(QDialog):

    def __init__(self, parent: Optional[QWidget], conn: sqlite3.Connection,
                 anomaly: LaneAnomaly):
        super().__init__(parent)
        self.setWindowTitle(f"Proximity Fix -- Anomaly #{anomaly.anomaly_id}")
        self.setModal(True)
        self._conn = conn
        self._anomaly = anomaly
        self._lane = int(anomaly.lane_ids[0])
        self._neighbors = neighbor_lanes(conn, anomaly.event_id, self._lane)
        pad = max(_MIN_PAD_S, anomaly.duration)
        self._t0, self._t1 = anomaly.start_time - pad, anomaly.end_time + pad
        self._pair: Dict[int, Dict[str, np.ndarray]] = {
            v: vehicle_rows(conn, anomaly.event_id, v, self._t0, self._t1)
            for v in anomaly.veh_ids}

        self._size_and_center()
        self._build_ui()
        self._draw_context()
        self._on_vehicle_changed()

    # ── UI ────────────────────────────────────────────────────────────

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

        self._plot = pg.PlotWidget()
        apply_plot_style(self._plot)
        self._plot.setLabel("left", "Distance traveled", units="m")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setTitle(
            f"Lane {self._lane} and neighboring lane(s) "
            f"{', '.join(str(l) for l in self._neighbors)}")
        self._legend = self._plot.addLegend(offset=(10, 10), labelTextColor="k",
                                            brush=pg.mkBrush(255, 255, 255, 210))
        main.addWidget(self._plot, stretch=1)

        right = QVBoxLayout()
        form = QFormLayout()
        self._veh_combo = QComboBox()
        for v in self._anomaly.veh_ids:
            self._veh_combo.addItem(f"Vehicle {v}", v)
        self._veh_combo.currentIndexChanged.connect(self._on_vehicle_changed)
        form.addRow("Vehicle to reassign:", self._veh_combo)

        self._start_spin = self._time_spin()
        self._end_spin = self._time_spin()
        self._start_spin.valueChanged.connect(self._on_spin_changed)
        self._end_spin.valueChanged.connect(self._on_spin_changed)
        form.addRow("Segment start:", self._start_spin)
        form.addRow("Segment end:", self._end_spin)

        self._lane_combo = QComboBox()
        for lane in self._neighbors:
            self._lane_combo.addItem(f"Lane {lane}", lane)
        form.addRow("Move to lane:", self._lane_combo)
        right.addLayout(form)

        self._info = QLabel()
        self._info.setWordWrap(True)
        right.addWidget(self._info)
        hint = QLabel("Drag the shaded range or its edges to select the segment.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-style: italic;")
        right.addWidget(hint)
        right.addStretch(1)

        buttons = QHBoxLayout()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self._apply_btn)
        buttons.addWidget(cancel)
        right.addLayout(buttons)

        panel = QWidget()
        panel.setLayout(right)
        panel.setMinimumWidth(260)
        main.addWidget(panel)

        self._selector = TimeRangeSelector(
            self._plot, self._anomaly.start_time, self._anomaly.end_time, parent=self)
        self._selector.range_changed.connect(self._on_selector_changed)

        self._preview = pg.PlotDataItem(pen=role_pen(Role.PREVIEW))
        self._preview.setZValue(role_z(Role.PREVIEW))
        self._plot.addItem(self._preview)
        self._legend.addItem(self._preview, "Segment to move")

    def _time_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix(" s")
        spin.setKeyboardTracking(False)
        spin.setRange(self._t0, self._t1)
        return spin

    def _draw_context(self):
        """Neighbor-lane traffic per lane color, then the anomaly pair on top."""
        lanes = [self._lane, *self._neighbors]
        data = load_lane_window(self._conn, self._anomaly.event_id, lanes, self._t0, self._t1)
        for index, lane in enumerate(lanes):
            pen = pg.mkPen(lane_color(index), width=_CONTEXT_WIDTH)
            first = True
            for veh, rows in data.items():
                if veh in self._anomaly.veh_ids:
                    continue
                mask = rows["lane"] == lane
                if not mask.any():
                    continue
                y = np.where(mask, rows["dist"], np.nan)
                item = pg.PlotDataItem(rows["time"], y, pen=pen, connect="finite")
                self._plot.addItem(item)
                if first:
                    self._legend.addItem(item, f"Lane {lane}")
                    first = False

        color = KINEMATIC_COLORS["dist"]
        for i, veh in enumerate(self._anomaly.veh_ids):
            rows = self._pair[veh]
            kwargs = {"shadowPen": outline_pen(_PAIR_WIDTH, OUTLINE_COLOR)} if i else {}
            item = pg.PlotDataItem(rows["time"], rows["dist"],
                                   pen=pg.mkPen(color, width=_PAIR_WIDTH),
                                   connect="finite", **kwargs)
            item.setZValue(role_z(Role.MEASURED) + 1)
            self._plot.addItem(item)
            role = "follower" if i == 0 else "leader"
            self._legend.addItem(item, f"Veh {veh} ({role})")
        self._plot.setXRange(self._t0, self._t1, padding=0)

    # ── Selection ─────────────────────────────────────────────────────

    def _selected_vehicle(self) -> int:
        return int(self._veh_combo.currentData())

    def _on_vehicle_changed(self, *_args):
        times = self._pair[self._selected_vehicle()]["time"]
        if times.size == 0:
            self._info.setText("This vehicle has no samples in the window.")
            self._apply_btn.setEnabled(False)
            return
        lo = max(self._anomaly.start_time, float(times[0]))
        hi = min(self._anomaly.end_time, float(times[-1]))
        self._selector.set_samples(times, emit=False)
        self._selector.set_range(lo, hi)

    def _on_spin_changed(self, *_args):
        self._selector.set_range(self._start_spin.value(), self._end_spin.value())

    def _on_selector_changed(self, lo: float, hi: float):
        for spin, value in ((self._start_spin, lo), (self._end_spin, hi)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._update_preview(lo, hi)

    def _update_preview(self, lo: float, hi: float):
        rows = self._pair[self._selected_vehicle()]
        mask = (rows["time"] >= lo) & (rows["time"] <= hi)
        self._preview.setData(rows["time"][mask], rows["dist"][mask])
        n = int(mask.sum())
        lanes = sorted({l for l in rows["lane"][mask] if l is not None})
        self._info.setText(
            f"{n} sample(s) from {lo:.2f} s to {hi:.2f} s, currently in lane(s) "
            f"{', '.join(str(l) for l in lanes) or '-'}.")
        self._apply_btn.setEnabled(n > 0)

    # ── Apply ─────────────────────────────────────────────────────────

    def _on_apply(self):
        veh = self._selected_vehicle()
        lo, hi = self._selector.range()
        to_lane = self._lane_combo.currentData()
        try:
            ProximityType.apply_fix(self._conn, self._anomaly, veh, lo, hi, int(to_lane))
        except ValueError as exc:
            QMessageBox.warning(self, "Proximity Fix", str(exc))
            return
        except (sqlite3.Error, RuntimeError) as exc:
            logger.exception("ProximityFixDialog: apply failed")
            QMessageBox.critical(self, "Proximity Fix",
                                 f"The fix could not be saved:\n{exc}\n\nNothing was changed.")
            return
        self.accept()
