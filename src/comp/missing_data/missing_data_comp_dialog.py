import csv
import logging
import math
import numpy as np

from PyQt6.QtWidgets import QDoubleSpinBox, QFormLayout, QSpinBox, QCheckBox
from comp.base_analysis_dialog import BaseAnalysisDialog

logger = logging.getLogger(__name__)

class MissingDataCompDialog(BaseAnalysisDialog):
    """MissingData-analysis parameter dialog.

    Inherits all thread orchestration from BaseAnalysisDialog.
    Only MissingData-specific concerns live here.
    """

    # ── MissingData-specific init ────────────────────────────────────────────────
    def __init__(self, parent, db_connection,
                 event_ids=None, veh_ids=None, export_mode=False):
        super().__init__(parent, db_connection,
                         event_ids=event_ids,
                         veh_ids=veh_ids,
                         export_mode=export_mode)
        
    # ── Hook overrides ────────────────────────────────────────────────────
    def _csv_export_label(self) -> str:
        return "Missing Data Analysis"
    
    def _show_results(self, results: list) -> None:
        """Intercepts processing thread completion to route and display data UI elements."""
        if not results:
            return

        if self._veh_ids is not None:
            # ── Right-Click Path: Show Table ──
            from .missing_data_results_table_dialog import MissingDataResultsTableDialog
            dlg = MissingDataResultsTableDialog(self.parent(), results)
            dlg.show()
        else:
            # ── Top-Level Path: Modern List Comprehension for Box Plots ──
            from .missing_data_box_plot_dialog import MissingDataBoxPlotDialog
            
            # Flatten out separate gap items while preserving the track mapping structure
            extracted_gap_lengths = [
                (r.veh_id, length)
                for r in results if len(r.gap_lengths) > 0
                for length in r.gap_lengths
            ]

            metrics = {
                "missing_record_pct": [(r.veh_id, r.missing_record_pct) for r in results],
                "num_gaps":           [(r.veh_id, r.num_gaps) for r in results],
                "gap_lengths":        extracted_gap_lengths 
            }
            
            event_title = f"Event {self._export_event_ids[0]}" if self._export_event_ids else "Summary"
            dlg = MissingDataBoxPlotDialog(self.parent(), f"{event_title} Missing Data Summary", metrics)
            dlg.resize(900, 700)
            dlg.show()

    def _build_parameter_form(self) -> QFormLayout:
        form = QFormLayout()

        # 1. Missing Value Gap Threshold (Integer)
        self._gap_threshold_spin = QSpinBox()
        self._gap_threshold_spin.setRange(1, 10000)
        self._gap_threshold_spin.setValue(10)
        self._gap_threshold_spin.setSuffix(" points")
        form.addRow("Missing value gap threshold:", self._gap_threshold_spin)

        # 2. Time Step Input (Double)
        self._time_step_spin = QDoubleSpinBox()
        self._time_step_spin.setRange(0.01, 60.0)
        self._time_step_spin.setDecimals(3)
        self._time_step_spin.setValue(0.1)
        self._time_step_spin.setSingleStep(0.01) # Makes the arrows click by 0.01 cleanly
        self._time_step_spin.setKeyboardTracking(False) # Prevents it from reverting while typing
        self._time_step_spin.setSuffix(" s")
        self._time_step_spin.setEnabled(False) # Disabled by default (Auto mode)

        # 3. Auto-Calculate Checkbox
        self._auto_time_check = QCheckBox("Auto-calculate Dataset Time Increment")
        self._auto_time_check.setChecked(True)
        # When unchecked, enable the manual spin box
        self._auto_time_check.toggled.connect(
            lambda checked: self._time_step_spin.setEnabled(not checked)
        )

        form.addRow("", self._auto_time_check)
        form.addRow("Manual time increment:", self._time_step_spin)

        return form
    
    def _current_params(self) -> dict:
        """Packages UI inputs to match the backend MissingDataComp OPTIONS_SCHEMA."""
        return {
            "gap_threshold": self._gap_threshold_spin.value(),
            # Send None if Auto is checked, otherwise send the explicit UI value
            "time_step": None if self._auto_time_check.isChecked() else self._time_step_spin.value(),
        }
    
    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from comp.base_comp import BaseAnalysisWorker
        return BaseAnalysisWorker(
            db_path, event_ids, veh_ids, params, dest_path,
            run_for_event=self._run_for_event,
            write_csv=self._write_csv,
        )
    
    def _run_for_event(self, conn, event_id, veh_ids, params):
        from .missing_data_comp import MissingDataComp
        comp = MissingDataComp()
        if veh_ids is not None:
            return comp.comp_series(conn, event_id, veh_ids, options=params)
        return comp.comp_event(conn, event_id, options=params)
    
    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        self._gap_threshold_spin.setEnabled(enabled)
        self._auto_time_check.setEnabled(enabled)
        # Only enable the spin box if the parent UI is enabled AND auto is turned off
        self._time_step_spin.setEnabled(enabled and not self._auto_time_check.isChecked())

    @staticmethod
    def _write_csv(results: list, dest: str):
        """Flattens MissingDataResult for CSV export."""
        fieldnames = [
            "event_id", "veh_id",
            "missing_record_pct", "num_gaps", 
            "missing_count", "expected_count", "gap_lengths"
        ]
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                # Convert the numpy array of gap lengths into a readable string list: "[10, 15]"
                lengths_str = np.array2string(r.gap_lengths, separator=', ') if len(r.gap_lengths) > 0 else "[]"
                
                writer.writerow({
                    "event_id":           r.event_id,
                    "veh_id":             r.veh_id,
                    "missing_record_pct": MissingDataCompDialog._fmt(r.missing_record_pct),
                    "num_gaps":           r.num_gaps,
                    "missing_count":      r.missing_count,
                    "expected_count":     r.expected_count,
                    "gap_lengths":        lengths_str
                })

    @staticmethod    
    def _fmt(v) -> str:
        try:
            return "" if math.isnan(v) else f"{v:.4f}"
        except (TypeError, ValueError):
            return ""