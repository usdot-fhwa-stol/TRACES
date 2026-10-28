"""Lane-Level Anomalies review panel -- the spec's Figure 3.

Left: one count label per anomaly type ("Proximity anomalies detected:
N"), then the selected type's view of the selected anomaly (each type's
AnomalyView; the built-in ones are kinematic stacks whose distance plot is
the time-space diagram).

Right: one button per type ("Proximity Anomalies", "Rapid Lane Change
Anomaly") acting as tabs, the Selected Anomaly list, and Previous Anomaly
/ Fix / Undo / Next Anomaly.

    Fix   runs the type's fix flow (AnomalyTypeUI.fix); enabled for an
          anomaly with no fix in effect
    Undo  reverses the fix in effect (lane_correction.undo_correction),
          after confirmation; enabled for a fixed anomaly

Anomalies are read from the lane_anomalies table, so the panel shows
exactly what is stored -- including anomalies of types not rerun this
time. Anomalies on trajectories consumed by a stitch are hidden.

data_changed(event_id) is emitted once, when the panel closes, if a Fix
or Undo changed the events table; MainWindow reloads the time-space plot
on it.
"""

import logging
import sqlite3
from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .anomaly_registry import anomaly_types
from .lane_anomaly_store import LaneAnomaly, read_anomalies
from .lane_correction import undo_correction

logger = logging.getLogger(__name__)

_TITLE = "Lane-Level Anomalies"


class LaneAnomalyReviewDialog(QDialog):

    data_changed = pyqtSignal(int)

    def __init__(self, parent, db_connection: sqlite3.Connection, event_id: int,
                 initial_type: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(f"{_TITLE} -- Review")
        self.setModal(True)
        self._size_and_center()

        self._db_connection = db_connection
        self._event_id = event_id
        self._changed = False
        self._types = anomaly_types()
        self._uis = {t.KEY: t.ui() for t in self._types}
        self._anomalies: Dict[str, List[LaneAnomaly]] = {t.KEY: [] for t in self._types}
        self._index: Dict[str, int] = {t.KEY: 0 for t in self._types}
        self._current_key: Optional[str] = None

        self._build_ui()
        self.finished.connect(self._on_finished)

        self._reload_all()
        keys = [t.KEY for t in self._types]
        if initial_type not in keys:
            initial_type = next((k for k in keys if self._anomalies[k]), keys[0] if keys else None)
        if initial_type is not None:
            self._select_type(initial_type)

    def _on_finished(self, _result: int):
        if self._changed:
            self._changed = False
            logger.info("LaneAnomalyReviewDialog: event %s data changed; notifying",
                        self._event_id)
            self.data_changed.emit(self._event_id)

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
            self.resize(1200, 800)

    def _build_ui(self):
        main = QHBoxLayout(self)

        left = QVBoxLayout()
        self._count_labels: Dict[str, QLabel] = {}
        for t in self._types:
            label = QLabel()
            left.addWidget(label)
            self._count_labels[t.KEY] = label
        self._stack = QStackedWidget()
        self._views = {}
        for t in self._types:
            view = self._uis[t.KEY].create_view(self, self._db_connection, self._event_id)
            self._stack.addWidget(view)
            self._views[t.KEY] = view
        left.addWidget(self._stack, stretch=1)
        main.addLayout(left, stretch=1)

        right = QVBoxLayout()
        type_row = QHBoxLayout()
        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        self._type_buttons: Dict[str, QPushButton] = {}
        for t in self._types:
            button = QPushButton(self._uis[t.KEY].BUTTON_TEXT or f"{t.LABEL} Anomalies")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, k=t.KEY: self._select_type(k))
            self._type_group.addButton(button)
            type_row.addWidget(button)
            self._type_buttons[t.KEY] = button
        right.addLayout(type_row)

        right.addWidget(QLabel("Selected Anomaly"))
        self._combo = QComboBox()
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        right.addWidget(self._combo)

        nav = QHBoxLayout()
        self._prev_btn = QPushButton("Previous Anomaly")
        self._prev_btn.clicked.connect(lambda: self._step(-1))
        self._fix_btn = QPushButton("Fix")
        self._fix_btn.clicked.connect(self._on_fix)
        self._undo_btn = QPushButton("Undo")
        self._undo_btn.setToolTip("Reverse the fix applied to this anomaly.")
        self._undo_btn.clicked.connect(self._on_undo)
        self._next_btn = QPushButton("Next Anomaly")
        self._next_btn.clicked.connect(lambda: self._step(1))
        for b in (self._prev_btn, self._fix_btn, self._undo_btn, self._next_btn):
            nav.addWidget(b)
        right.addLayout(nav)
        right.addStretch(1)

        panel = QWidget()
        panel.setLayout(right)
        main.addWidget(panel)

    # ── State ─────────────────────────────────────────────────────────

    def _reload(self, key: str):
        self._anomalies[key] = read_anomalies(self._db_connection, self._event_id, key)
        anomalies = self._anomalies[key]
        n_fixed = sum(1 for a in anomalies if a.is_fixed)
        self._count_labels[key].setText(self._uis[key].count_text(len(anomalies), n_fixed))

    def _reload_all(self):
        for t in self._types:
            self._reload(t.KEY)

    def _current_list(self) -> List[LaneAnomaly]:
        return self._anomalies.get(self._current_key, []) if self._current_key else []

    def _current(self) -> Optional[LaneAnomaly]:
        items = self._current_list()
        if not items:
            return None
        return items[min(self._index[self._current_key], len(items) - 1)]

    def _select_type(self, key: str):
        self._current_key = key
        self._type_buttons[key].setChecked(True)
        self._stack.setCurrentWidget(self._views[key])
        self._populate_combo()
        self._load(self._index[key])

    def _populate_combo(self):
        t = next(t for t in self._types if t.KEY == self._current_key)
        self._combo.blockSignals(True)
        self._combo.clear()
        for a in self._current_list():
            self._combo.addItem(t.describe(a), a.anomaly_id)
        self._combo.blockSignals(False)

    def _load(self, index: int):
        items = self._current_list()
        view = self._views[self._current_key]
        if not items:
            self._index[self._current_key] = 0
            view.show_anomaly(None)
            self._update_buttons()
            return
        index = max(0, min(index, len(items) - 1))
        self._index[self._current_key] = index
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)
        view.show_anomaly(items[index])
        self._update_buttons()

    def _update_buttons(self):
        items = self._current_list()
        current = self._current()
        index = self._index.get(self._current_key, 0)
        self._combo.setEnabled(bool(items))
        self._prev_btn.setEnabled(bool(items) and index > 0)
        self._next_btn.setEnabled(bool(items) and index < len(items) - 1)
        self._fix_btn.setEnabled(current is not None and not current.is_fixed)
        self._undo_btn.setEnabled(current is not None and current.is_fixed)

    def _on_combo_changed(self, index: int):
        if index >= 0:
            self._load(index)

    def _step(self, delta: int):
        self._load(self._index[self._current_key] + delta)

    def _refresh_after_change(self, anomaly_id: Optional[int]):
        """Re-read the current type's anomalies (statuses changed) and keep
        the same anomaly selected."""
        self._reload(self._current_key)
        ids = [a.anomaly_id for a in self._current_list()]
        if anomaly_id in ids:
            self._index[self._current_key] = ids.index(anomaly_id)
        self._populate_combo()
        self._load(self._index[self._current_key])

    # ── Fix / Undo ────────────────────────────────────────────────────

    def _on_fix(self):
        anomaly = self._current()
        if anomaly is None or anomaly.is_fixed:
            return
        try:
            applied = self._uis[self._current_key].fix(self, self._db_connection, anomaly)
        except sqlite3.Error as exc:
            logger.exception("LaneAnomalyReviewDialog: fix failed")
            QMessageBox.critical(self, _TITLE, f"The fix failed:\n{exc}")
            applied = False
        if applied:
            self._changed = True
            self._refresh_after_change(anomaly.anomaly_id)

    def _on_undo(self):
        anomaly = self._current()
        if anomaly is None or not anomaly.is_fixed:
            return
        reply = QMessageBox.question(
            self, "Undo Fix?",
            f"Undo the fix applied to anomaly #{anomaly.anomaly_id}?\n\n"
            f"The corrected samples get their previous values and lanes back. "
            f"If the fix changed positions, the spline fit and internal "
            f"consistency values it cleared are not restored; rerun those "
            f"analyses if you need them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            undo_correction(self._db_connection, anomaly)
        except ValueError as exc:
            QMessageBox.warning(self, "Undo Fix", str(exc))
            return
        except sqlite3.Error as exc:
            logger.exception("LaneAnomalyReviewDialog: undo failed")
            QMessageBox.critical(self, "Undo Fix",
                                 f"The undo failed:\n{exc}\n\nNothing was changed.")
            return
        self._changed = True
        self._refresh_after_change(anomaly.anomaly_id)
