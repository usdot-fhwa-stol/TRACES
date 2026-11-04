"""Top-level Data > Lane-Level Anomalies -- the spec's Figure 2,
"Select Anomalies to Identify".

One row per registered anomaly type (anomaly_registry), each a checkbox
with its parameter spinbox and unit right-justified on the same row:

    [x] Proximity                    Proximity Threshold [ 5.0 ] m
    [x] Rapid Lane Change     Max Time Between Changes [ 2.0 ] s

plus the "Write changes to" row, the progress bar, and Confirm / Cancel
(BaseAnalysisDialog). Confirm is disabled while no type is checked.

Confirm resolves the write target first (normally DatabaseSession.
ensure_write_target: the first-write warning, or copy-and-switch when
another file is chosen -- KEEPS_OPEN_ON_DB_SWITCH tells MainWindow to
leave this dialog open while that happens), creates the module's tables,
then runs detection for the checked types on the worker thread over the
whole event currently loaded. Results are stored on the main thread with
the rerun policy (lane_anomaly_store.replace_detected): the checked
types' unfixed anomalies are replaced, fixed ones are kept. Unchecked
types keep whatever was stored before. The review panel (Figure 3,
LaneAnomalyReviewDialog) then opens; review_opened hands it to listeners
(MainWindow connects its data_changed) since it outlives this dialog.

Detection itself never changes the events table, so this dialog has no
data_changed signal of its own.
"""

import logging
import sqlite3
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFormLayout, QLabel, QMessageBox

from ui_kit.db_target_row import DbTargetRow, TargetResolver, db_file_path, in_place_resolver
from ..base_analysis_dialog import BaseAnalysisDialog
from .anomaly_registry import anomaly_types
from .anomaly_ui import ConfigRow
from .lane_anomaly_comp import DetectionSummary, LaneAnomalyComp
from .lane_anomaly_store import ensure_schema, replace_detected

logger = logging.getLogger(__name__)

_TITLE = "Lane-Level Anomalies"


class LaneAnomalyConfigDialog(BaseAnalysisDialog):

    RIGHT_CLICK_ENABLED = False      # top-level only
    KEEPS_OPEN_ON_DB_SWITCH = True   # see module docstring

    # A LaneAnomalyReviewDialog was just created, before it is shown.
    review_opened = pyqtSignal(object)

    def __init__(self, parent, db_connection, event_ids=None, export_mode=False,
                 resolve_target: Optional[TargetResolver] = None):
        if export_mode:
            raise ValueError("LaneAnomalyConfigDialog has no export mode.")
        super().__init__(parent, db_connection, event_ids=event_ids, veh_ids=None,
                         export_mode=False)
        self._resolve_target = resolve_target or in_place_resolver(db_connection, _TITLE)
        self._event_id = self._export_event_ids[0]
        self._update_confirm_enabled()

    # ── UI ────────────────────────────────────────────────────────────

    def _csv_export_label(self) -> str:
        return _TITLE

    def _setup_ui(self):
        super()._setup_ui()
        self._primary_btn.setText("Confirm")
        layout = self.layout()
        self._target_row = DbTargetRow(
            self._db_connection, browse_title="Write Lane-Level Anomaly Changes To")
        layout.insertWidget(layout.count() - 1, self._target_row)   # above the buttons

    def _build_parameter_form(self) -> QFormLayout:
        # Called from BaseAnalysisDialog.__init__ (via _setup_ui), so the
        # rows list is created here rather than in our __init__.
        self._rows: List[ConfigRow] = []
        form = QFormLayout()
        title = QLabel("Select Anomalies to Identify")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        form.addRow(title)
        for t in anomaly_types():
            row = t.ui().create_config_row(self)
            row.checkbox.setChecked(True)
            row.checkbox.toggled.connect(self._update_confirm_enabled)
            form.addRow(row)
            self._rows.append(row)
        return form

    def _update_confirm_enabled(self, *_args):
        if hasattr(self, "_primary_btn") and hasattr(self, "_rows"):
            self._primary_btn.setEnabled(any(r.is_checked() for r in self._rows))

    def _current_params(self) -> dict:
        checked = [r for r in self._rows if r.is_checked()]
        return {"enabled": [r.key for r in checked],
                "types": {r.key: r.options() for r in checked}}

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        for row in self._rows:
            row.set_controls_enabled(enabled)
        self._target_row.setEnabled(enabled)
        if enabled:
            self._update_confirm_enabled()

    def _progress_maximum(self, event_ids: list, veh_ids, params: dict) -> int:
        return max(1, len(event_ids) * len(params["enabled"]))

    # ── Confirm ───────────────────────────────────────────────────────

    def _resolve_write_target(self) -> bool:
        target = self._resolve_target(self, self._target_row.path())
        if target is None:
            return False
        if target is not self._db_connection:
            self._db_connection = target
            self._db_path = db_file_path(target)   # the worker opens this path
            logger.info("LaneAnomalyConfigDialog: now writing to %s", self._db_path)
        self._target_row.set_connection(target)
        return True

    def _on_ok(self):
        if not self._current_params()["enabled"]:
            return
        if not self._resolve_write_target():
            return
        try:
            ensure_schema(self._db_connection)
        except sqlite3.Error as exc:
            logger.exception("LaneAnomalyConfigDialog: schema setup failed")
            QMessageBox.critical(self, _TITLE, f"Preparing the database failed:\n{exc}")
            return
        super()._on_ok()

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from ..base_analysis_worker import BaseAnalysisWorker
        return BaseAnalysisWorker(db_path, event_ids, veh_ids, params, dest_path,
                                  run_for_event=self._run_for_event, write_csv=None)

    @staticmethod
    def _run_for_event(conn, event_id, veh_ids, params, progress_callback=None):
        """Worker thread: detection only (read-only)."""
        return LaneAnomalyComp().comp_series(conn, event_id, None, params,
                                             progress_callback=progress_callback)

    def _on_worker_finished(self, results: List[DetectionSummary]):
        try:
            for summary in results:
                stats = replace_detected(self._db_connection, summary.event_id,
                                         summary.type_keys, summary.anomalies)
                logger.info("Lane anomalies, event %s: %s", summary.event_id, stats)
            self._db_connection.commit()
        except sqlite3.Error as exc:
            self._db_connection.rollback()
            logger.exception("LaneAnomalyConfigDialog: storing anomalies failed")
            self._set_controls_enabled(True)
            self._progress_bar.setValue(0)
            QMessageBox.critical(
                self, _TITLE,
                f"Detection succeeded but storing the anomalies failed:\n{exc}\n\n"
                f"No changes were committed.")
            return
        super()._on_worker_finished(results)

    def _show_results(self, results: List[DetectionSummary]) -> None:
        from .anomaly_review_dialog import LaneAnomalyReviewDialog
        first = next((s.type_keys[0] for s in results if s.type_keys), None)
        dlg = LaneAnomalyReviewDialog(self.parent(), self._db_connection, self._event_id,
                                      initial_type=first)
        self.review_opened.emit(dlg)   # listeners connect to dlg.data_changed
        dlg.show()
