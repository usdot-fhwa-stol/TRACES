"""Advanced Statistics -- right-click Statistics > Advanced Statistics
(Task 9.4).

AdvancedStatisticsDialog is the parameter dialog (BaseAnalysisDialog):

    Minimum time in new lane   s a vehicle must stay in a new lane for the
                               move to count as a lane change (default 1)
    Maximum leader / follower gap
                               m beyond which a vehicle ahead / behind is
                               not a leader / follower; "No limit" (0) by
                               default

OK runs AdvancedStatisticsComp on the worker thread over the event in
view. veh_ids None (the right-click "All" context) reports every active
vehicle of the event, across all lanes; otherwise the vehicles in
context. Leaders and followers always come from every active vehicle of
the event. The results then open in AdvancedStatisticsResultsDialog.

The module only reads: nothing is written to the database.
"""

from PyQt6.QtWidgets import QDoubleSpinBox, QFormLayout

from ..base_analysis_dialog import BaseAnalysisDialog
from ..base_comp import BaseComp
from .advanced_stats_comp import AdvancedStatisticsComp
from .lane_changes import DEFAULT_MIN_DWELL_S


class AdvancedStatisticsDialog(BaseAnalysisDialog):

    def __init__(self, parent, db_connection, event_ids=None, veh_ids=None,
                 export_mode=False, mode: str = "All"):
        if export_mode:
            raise ValueError("AdvancedStatisticsDialog has no export mode; "
                             "the results table has its own Save.")
        self._mode = mode
        super().__init__(parent, db_connection, event_ids=event_ids, veh_ids=veh_ids,
                         export_mode=False)

    # ── Hooks ─────────────────────────────────────────────────────────

    def _csv_export_label(self) -> str:
        return "Advanced Statistics"

    def _build_parameter_form(self) -> QFormLayout:
        form = QFormLayout()
        self._dwell_spin = QDoubleSpinBox()
        self._dwell_spin.setRange(0.1, 60.0)
        self._dwell_spin.setDecimals(1)
        self._dwell_spin.setSingleStep(0.1)
        self._dwell_spin.setValue(DEFAULT_MIN_DWELL_S)
        self._dwell_spin.setSuffix(" s")
        self._dwell_spin.setToolTip(
            "A move to another lane counts as a lane change only when the vehicle "
            "stays in the new lane at least this long. Shorter flips are ignored.")
        form.addRow("Minimum time in new lane:", self._dwell_spin)

        self._max_gap_spin = QDoubleSpinBox()
        self._max_gap_spin.setRange(0.0, 10000.0)
        self._max_gap_spin.setDecimals(1)
        self._max_gap_spin.setSingleStep(10.0)
        self._max_gap_spin.setSpecialValueText("No limit")
        self._max_gap_spin.setValue(0.0)
        self._max_gap_spin.setSuffix(" m")
        self._max_gap_spin.setToolTip(
            "A vehicle further than this ahead (behind) in the same lane is not "
            "counted as a leader (follower). Gaps are center to center.")
        form.addRow("Maximum leader / follower gap:", self._max_gap_spin)
        return form

    def _current_params(self) -> dict:
        return {"min_dwell_s": self._dwell_spin.value(),
                "max_gap_m": self._max_gap_spin.value()}

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        self._dwell_spin.setEnabled(enabled)
        self._max_gap_spin.setEnabled(enabled)

    def _progress_maximum(self, event_ids, veh_ids, params) -> int:
        # One tick for the leader/follower index, one per reported vehicle.
        total = 0
        for event_id in event_ids:
            n = (len(veh_ids) if veh_ids is not None
                 else len(BaseComp._event_veh_ids(self._db_connection, event_id)))
            total += 1 + n
        return max(1, total)

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from ..base_analysis_worker import BaseAnalysisWorker
        return BaseAnalysisWorker(db_path, event_ids, veh_ids, params, dest_path,
                                  run_for_event=self._run_for_event, write_csv=None)

    @staticmethod
    def _run_for_event(conn, event_id, veh_ids, params, progress_callback=None):
        return AdvancedStatisticsComp().comp_series(conn, event_id, veh_ids, params,
                                                    progress_callback=progress_callback)

    def _show_results(self, results: list) -> None:
        from .advanced_stats_results_dialog import AdvancedStatisticsResultsDialog
        event_id = self._export_event_ids[0] if self._export_event_ids else None
        dlg = AdvancedStatisticsResultsDialog(self.parent(), results, event_id,
                                              self._mode, self._current_params())
        dlg.exec()
