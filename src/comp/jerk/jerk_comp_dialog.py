# comp/jerk/jerk_comp_dialog.py  — FULL REPLACEMENT

import csv
import logging
import math

from PyQt6.QtWidgets import QDoubleSpinBox, QComboBox, QFormLayout
from comp.base_analysis_dialog import BaseAnalysisDialog

logger = logging.getLogger(__name__)


# ── Dialog ────────────────────────────────────────────────────────────────
class JerkCompDialog(BaseAnalysisDialog):
    """Jerk-analysis parameter dialog.

    Inherits all thread orchestration from BaseAnalysisDialog.
    Only jerk-specific concerns live here.
    """

    # ── Jerk-specific init ────────────────────────────────────────────────
    def __init__(self, parent, db_connection,
                 event_ids=None, veh_ids=None, export_mode=False):
        super().__init__(parent, db_connection,
                         event_ids=event_ids,
                         veh_ids=veh_ids,
                         export_mode=export_mode)
        self._configure_method_options()

    # ── Hook overrides ────────────────────────────────────────────────────
    def _csv_export_label(self) -> str:
        return "Jerk Analysis"

    def _build_parameter_form(self) -> QFormLayout:
        form = QFormLayout()

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.1, 1000.0)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setValue(15.0)
        self._threshold_spin.setSuffix(" m/s³")
        form.addRow("Jerk threshold:", self._threshold_spin)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(0.01, 60.0)
        self._gap_spin.setDecimals(2)
        self._gap_spin.setValue(1.0)
        self._gap_spin.setSuffix(" s")
        form.addRow("Missing value gap threshold:", self._gap_spin)

        self._method_combo = QComboBox()
        form.addRow("Jerk calculation method:", self._method_combo)

        # Must be called after widgets exist; called from __init__ via
        # _post_setup() hook (see below).
        return form

    def _current_params(self) -> dict:
        return {
            "jerk_threshold":      self._threshold_spin.value(),
            "missing_gap_seconds": self._gap_spin.value(),
            "jerk_method":         self._method_combo.currentData(),
        }

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from comp.base_comp import BaseAnalysisWorker
        return BaseAnalysisWorker(
            db_path, event_ids, veh_ids, params, dest_path,
            run_for_event=self._run_for_event,
            write_csv=self._write_csv,          # module-level function, unchanged
        )
    
    def _run_for_event(self, conn, event_id, veh_ids, params):
        from .jerk_comp import JerkComp
        comp = JerkComp()
        if veh_ids is not None:
            return comp.comp_series(conn, event_id, veh_ids, options=params)
        return comp.comp_event(conn, event_id, options=params)

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)          # handles buttons
        self._threshold_spin.setEnabled(enabled)
        self._gap_spin.setEnabled(enabled)
        self._method_combo.setEnabled(enabled)

    def _configure_method_options(self):
        # Query the correct table name used throughout the application.
        cur = self._db_connection.execute("PRAGMA table_info(events)")
        cols = {row[1] for row in cur.fetchall()}
    
        entries = [
            ("Use jerk column from dataset",               "dataset",    "veh_jerk"  in cols),
            ("Calculate jerk from acceleration",            "from_accel", "veh_accel" in cols),
            ("Calculate acceleration and jerk from speed",  "from_speed", "veh_speed" in cols),
        ]
        self._method_combo.clear()
        for label, key, enabled in entries:
            self._method_combo.addItem(label, key)
            idx = self._method_combo.count() - 1
            if not enabled:
                self._method_combo.model().item(idx).setEnabled(False)
    
        for i in range(self._method_combo.count()):
            if self._method_combo.model().item(i).isEnabled():
                self._method_combo.setCurrentIndex(i)
                return          # ← return instead of break so the else never fires incorrectly
    
        # Only reached if ALL three columns are absent.
        self._method_combo.setEnabled(False)
        self._primary_btn.setEnabled(False)

    @staticmethod
    def _write_csv(results: list, dest: str):
        fieldnames = [
            "event_id", "veh_id",
            "jerk_violation_pct", "max_jerk", "min_jerk",
            "sign_inversion_excess_pct",
        ]
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "event_id":                 r.event_id,
                    "veh_id":                   r.veh_id,
                    "jerk_violation_pct":        JerkCompDialog._fmt(r.jerk_violation_pct),
                    "max_jerk":                  JerkCompDialog._fmt(r.max_jerk),
                    "min_jerk":                  JerkCompDialog._fmt(r.min_jerk),
                    "sign_inversion_excess_pct": JerkCompDialog._fmt(r.sign_inversion_excess_pct),
                })
    
    @staticmethod    
    def _fmt(v) -> str:
        try:
            return "" if math.isnan(v) else f"{v:.4f}"
        except (TypeError, ValueError):
            return ""