"""Lane-Level Anomalies review panel -- the spec's Figure 3.

Left, filling most of the width: the time-space diagram of the selected
anomaly (AnomalyPlot -- a scatter plot, vehicles by color, lanes by
symbol).

Right, one column, top to bottom:

    counts             one line per anomaly type: "N detected (M fixed)"
    type buttons       one per type, acting as tabs
    Selected Anomaly   the selected type's anomalies
    Previous | Next
    fix panel          the selected type's FixPanel: the controls that
                       build its fix or, for a fixed anomaly, the fix
                       applied
    Apply | Undo       Apply passes the panel's pending_fix() to the type's
                       apply_fix; Undo reverses the selected anomaly's fix
                       through the type's undo_fix, after confirmation
    Close

The column is as wide as the widest entry the Selected Anomaly list can
show -- across every type, each measured both unfixed and with its
"[fixed]" mark, so the column doesn't move when a type is switched or a
fix applied -- or as its other controls need, whichever is wider, and at
most half the dialog (_fit_column_width). The plot takes the rest.

Choosing another anomaly or type, or closing, silently discards a fix
panel's unapplied choices.

Anomalies are read from the lane_anomalies table, so the panel shows
exactly what is stored -- including anomalies of types not rerun this
time. Anomalies on trajectories consumed by a stitch are hidden.

data_changed(event_id) is emitted once, when the panel closes, if an
Apply or Undo changed the events table; MainWindow reloads the time-space
plot on it.
"""

import dataclasses
import logging
import sqlite3
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QStackedWidget, QStyle, QStyleOptionComboBox,
    QVBoxLayout, QWidget,
)

from .anomaly_registry import anomaly_types
from .anomaly_ui import AnomalyPlot, anomaly_window
from .lane_anomaly_store import ANOMALY_FIXED, LaneAnomaly, read_anomalies
from .lane_correction import vehicle_rows

logger = logging.getLogger(__name__)

_TITLE = "Lane-Level Anomalies"
_MAX_COLUMN_FRACTION = 0.2    # of the dialog's width


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
        self._plot = AnomalyPlot(self._db_connection, self._event_id)
        main.addWidget(self._plot, stretch=1)

        column = QWidget()
        column.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._column = column
        right = QVBoxLayout(column)
        right.setContentsMargins(0, 0, 0, 0)

        self._count_labels: Dict[str, QLabel] = {}
        for t in self._types:
            label = QLabel()
            right.addWidget(label)
            self._count_labels[t.KEY] = label
        right.addSpacing(8)

        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        self._type_buttons: Dict[str, QPushButton] = {}
        for t in self._types:
            button = QPushButton(self._uis[t.KEY].BUTTON_TEXT or f"{t.LABEL} Anomalies")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, k=t.KEY: self._select_type(k))
            self._type_group.addButton(button)
            right.addWidget(button)
            self._type_buttons[t.KEY] = button
        right.addSpacing(8)

        right.addWidget(QLabel("Selected Anomaly"))
        self._combo = QComboBox()
        # Width comes from _fit_column_width, not from the current items,
        # so it doesn't change with the type shown.
        self._combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._combo.setMinimumContentsLength(1)
        self._combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        right.addWidget(self._combo)

        nav = QHBoxLayout()
        self._prev_btn = QPushButton("Previous")
        self._prev_btn.clicked.connect(lambda: self._step(-1))
        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(lambda: self._step(1))
        for b in (self._prev_btn, self._next_btn):
            b.setAutoDefault(False)
            nav.addWidget(b)
        right.addLayout(nav)

        self._panels = QStackedWidget()
        self._fix_panels = {}
        for t in self._types:
            panel = self._uis[t.KEY].create_fix_panel(self, self._db_connection, self._event_id)
            panel.preview_changed.connect(
                lambda preview, k=t.KEY: self._on_preview(k, preview))
            panel.pending_changed.connect(lambda _pending: self._update_buttons())
            self._panels.addWidget(panel)
            self._fix_panels[t.KEY] = panel
        right.addWidget(self._panels, stretch=1)

        actions = QHBoxLayout()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setToolTip("Apply the fix set up in the fix panel.")
        self._apply_btn.clicked.connect(self._on_apply)
        self._undo_btn = QPushButton("Undo")
        self._undo_btn.setToolTip("Reverse the fix applied to this anomaly.")
        self._undo_btn.clicked.connect(self._on_undo)
        for b in (self._apply_btn, self._undo_btn):
            b.setAutoDefault(False)
            actions.addWidget(b)
        right.addLayout(actions)

        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        right.addWidget(close)

        main.addWidget(column)

    # ── State ─────────────────────────────────────────────────────────

    def _reload(self, key: str):
        self._anomalies[key] = read_anomalies(self._db_connection, self._event_id, key)
        anomalies = self._anomalies[key]
        n_fixed = sum(1 for a in anomalies if a.is_fixed)
        self._count_labels[key].setText(self._uis[key].count_text(len(anomalies), n_fixed))

    def _reload_all(self):
        for t in self._types:
            self._reload(t.KEY)
        self._fit_column_width()

    def _combo_width_for(self, texts: List[str]) -> int:
        """Width a QComboBox needs to show the widest of *texts* in full:
        the text plus the style's frame, padding and drop-down arrow."""
        fm = self._combo.fontMetrics()
        text_w = max((fm.horizontalAdvance(t) for t in texts), default=0)
        option = QStyleOptionComboBox()
        self._combo.initStyleOption(option)
        size = self._combo.style().sizeFromContents(
            QStyle.ContentsType.CT_ComboBox, option, QSize(text_w, fm.height()), self._combo)
        return size.width()

    def _fit_column_width(self):
        """Size the right column to the widest Selected Anomaly entry of
        any type -- fixed or not -- or to what its other controls need,
        whichever is wider; at most _MAX_COLUMN_FRACTION of the dialog."""
        texts = []
        for t in self._types:
            for a in self._anomalies[t.KEY]:
                texts.append(t.describe(a))
                texts.append(t.describe(dataclasses.replace(a, status=ANOMALY_FIXED)))
        margins = self._column.layout().contentsMargins()
        needed = max(self._combo_width_for(texts) + margins.left() + margins.right(),
                     self._column.minimumSizeHint().width())
        self._column.setFixedWidth(min(needed, int(self.width() * _MAX_COLUMN_FRACTION)))

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
        self._panels.setCurrentWidget(self._fix_panels[key])
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
        """Show anomaly *index* of the current type on the plot and in its
        fix panel (discarding any unapplied choices)."""
        items = self._current_list()
        key = self._current_key
        anomaly = None
        if items:
            index = max(0, min(index, len(items) - 1))
            anomaly = items[index]
            self._combo.blockSignals(True)
            self._combo.setCurrentIndex(index)
            self._combo.blockSignals(False)
        self._index[key] = index if items else 0

        rows = {}
        if anomaly is not None:
            t0, t1 = anomaly_window(anomaly)
            rows = {v: vehicle_rows(self._db_connection, anomaly.event_id, v, t0, t1)
                    for v in anomaly.veh_ids}
        self._plot.show_anomaly(anomaly, self._uis[key], rows)
        self._fix_panels[key].show_anomaly(anomaly, rows)
        self._update_buttons()

    def _update_buttons(self):
        items = self._current_list()
        current = self._current()
        index = self._index.get(self._current_key, 0)
        self._combo.setEnabled(bool(items))
        self._prev_btn.setEnabled(bool(items) and index > 0)
        self._next_btn.setEnabled(bool(items) and index < len(items) - 1)
        self._undo_btn.setEnabled(current is not None and current.is_fixed)
        panel = self._fix_panels.get(self._current_key)
        self._apply_btn.setEnabled(current is not None and not current.is_fixed
                                   and panel is not None and panel.has_pending_fix())

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

    def _on_preview(self, key: str, preview):
        if key == self._current_key:
            self._plot.set_preview(preview)

    def _on_apply(self):
        anomaly = self._current()
        if anomaly is None or anomaly.is_fixed:
            return
        fix = self._fix_panels[self._current_key].pending_fix()
        if not fix:
            self._load(self._index[self._current_key])   # the data changed; refresh
            return
        try:
            self._uis[self._current_key].type.apply_fix(self._db_connection, anomaly, fix)
        except ValueError as exc:
            QMessageBox.warning(self, "Apply Fix", str(exc))
            return
        except (sqlite3.Error, RuntimeError) as exc:
            logger.exception("LaneAnomalyReviewDialog: apply failed")
            QMessageBox.critical(self, "Apply Fix",
                                 f"The fix could not be saved:\n{exc}\n\nNothing was changed.")
            return
        self._changed = True
        self._refresh_after_change(anomaly.anomaly_id)

    def _on_undo(self):
        anomaly = self._current()
        if anomaly is None or not anomaly.is_fixed:
            return
        reply = QMessageBox.question(
            self, "Undo Fix?",
            f"Undo the fix applied to anomaly #{anomaly.anomaly_id}?\n\n"
            f"Every vehicle it moved gets its previous lanes back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._uis[self._current_key].type.undo_fix(self._db_connection, anomaly)
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
