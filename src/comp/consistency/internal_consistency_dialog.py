import csv
import logging
import math

from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox, QFormLayout, QMessageBox

from comp.base_analysis_dialog import BaseAnalysisDialog

logger = logging.getLogger(__name__)


class InternalConsistencyDialog(BaseAnalysisDialog):
    """Internal consistency analysis parameter dialog — Module 4.

    Inherits all thread orchestration from BaseAnalysisDialog.

    Invocation paths
    ----------------
    Normal — top-level menu (event_ids=[single id], veh_ids=None):
        _on_worker_finished override
        writes the six computed columns to the events table via the main
        connection, then opens InternalConsistencyStatsDialog.

    Normal — right-click (event_ids=[single id], veh_ids=list):
        Same thread path; _on_worker_finished opens
        InternalConsistencyResultDialog (combined modal dialog).

    Export (event_ids=multiple ids, export_mode=True):
        Save streams Figure-12 layout CSV.  No DB write occurs.
    """

    # ── Construction ──────────────────────────────────────────────────────
    def __init__(self, parent, db_connection,
                 event_ids=None, veh_ids=None, export_mode=False):
        # Store connection before super().__init__ so _build_parameter_form
        # can reference it if needed in future; currently not required there.
        super().__init__(parent, db_connection,
                         event_ids=event_ids,
                         veh_ids=veh_ids,
                         export_mode=export_mode)
        self._check_required_columns()

    def _check_required_columns(self):
        from comp.base_comp import BaseComp
        cols = BaseComp._events_columns(self._db_connection)
        if "veh_dist_trav" not in cols:
            self._primary_btn.setEnabled(False)
            self._primary_btn.setToolTip(
                "veh_dist_trav is required for internal consistency analysis "
                "but is absent from this dataset.")

    # ── Hook overrides ────────────────────────────────────────────────────
    def _csv_export_label(self) -> str:
        return "Internal Consistency"

    def _show_results(self, results: list) -> None:
        if results:
            self._write_db(results)
    
        if self._veh_ids is not None:
            # Right-click path — combined plot + table dialog
            from .internal_consistency_result_dialog import InternalConsistencyResultDialog
            dlg = InternalConsistencyResultDialog(self.parent(), results)
            dlg.exec()
        else:
            # Top-level menu path — statistics summary table
            from .internal_consistency_stats_dialog import InternalConsistencyStatsDialog
            dlg = InternalConsistencyStatsDialog(self.parent(), results)
            dlg.exec()

    def _show_results_old(self, results: list) -> None:
        from .internal_consistency_stats_dialog import InternalConsistencyStatsDialog
        dlg = InternalConsistencyStatsDialog(self, results)
        dlg.exec()

    def _build_parameter_form(self) -> QFormLayout:
        form = QFormLayout()

        self._time_step_spin = QDoubleSpinBox()
        self._time_step_spin.setRange(0.001, 10.0)
        self._time_step_spin.setDecimals(3)
        self._time_step_spin.setValue(0.1)
        self._time_step_spin.setSuffix(" s")
        form.addRow("Dataset time increment:", self._time_step_spin)

        self._gap_spin = QSpinBox()
        self._gap_spin.setRange(1, 10000)
        self._gap_spin.setValue(10)
        self._gap_spin.setSuffix(" points")
        form.addRow("Missing value gap threshold:", self._gap_spin)

        return form

    def _current_params(self) -> dict:
        return {
            "time_step":          self._time_step_spin.value(),
            "missing_gap_points": self._gap_spin.value(),
        }

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from comp.base_comp import BaseAnalysisWorker
        return BaseAnalysisWorker(
            db_path, event_ids, veh_ids, params, dest_path,
            run_for_event=self._run_for_event,
            write_csv=self._write_csv if dest_path else None,
        )

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        self._time_step_spin.setEnabled(enabled)
        self._gap_spin.setEnabled(enabled)

    def _default_export_stem(self) -> str:
        from utils import default_save_stem
        eids   = "_".join(str(e) for e in self._export_event_ids) if self._export_event_ids else "eall"
        tstep  = f"dt{self._time_step_spin.value():.3f}s"
        gap    = f"gap{self._gap_spin.value()}pts"
        return default_save_stem(f"internal_consistency_e{eids}", f"{tstep}_{gap}")

    # ── Worker callable ───────────────────────────────────────────────────

    def _run_for_event(self, conn, event_id, veh_ids, params, progress_callback=None):
        """Purely computational — no DB writes. Schema ALTER happens first."""
        from .internal_consistency_comp import InternalConsistencyComp
        InternalConsistencyComp.ensure_schema(conn)
        comp = InternalConsistencyComp()
        if veh_ids is not None:
            results = comp.comp_series(conn, event_id, veh_ids, options=params)
        else:
            results = comp.comp_event(conn, event_id, options=params)
        if progress_callback is not None:
            progress_callback()
        return results

    # ── Result handling ───────────────────────────────────────────────────

    def _write_db(self, results: list) -> None:
        """Batch-UPDATE the six computed columns into the events table."""
        sql = """
            UPDATE events
               SET veh_speed_cal = ?,
                   veh_accel_cal = ?,
                   veh_jerk_cal  = ?,
                   speed_error   = ?,
                   accel_error   = ?,
                   jerk_error    = ?
             WHERE event_id = ? AND veh_id = ? AND veh_time = ?
        """
        rows = [
            (
                _db_val(r.veh_speed_cal),
                _db_val(r.veh_accel_cal),
                _db_val(r.veh_jerk_cal),
                _db_val(r.speed_error),
                _db_val(r.accel_error),
                _db_val(r.jerk_error),
                r.event_id,
                r.veh_id,
                r.veh_time,
            )
            for r in results
        ]
        try:
            self._db_connection.executemany(sql, rows)
            self._db_connection.commit()
        except Exception as exc:
            logger.exception("Internal consistency DB write failed")
            QMessageBox.warning(
                self, "Database Write Error",
                f"Results computed but could not be saved to the database:\n{exc}",
            )

    # ── CSV writer (export mode) ──────────────────────────────────────────

    @staticmethod
    def _write_csv(results: list, dest: str) -> None:
        """Figure 12 layout: one row per (event_id, veh_id, veh_time)."""
        fieldnames = [
            "event_id", "veh_id", "veh_time", "veh_dist_trav",
            "veh_speed",     "veh_speed_cal",  "speed_error",
            "veh_accel",     "veh_accel_cal",  "accel_error",
            "veh_jerk",      "veh_jerk_cal",   "jerk_error",
        ]
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "event_id":      r.event_id,
                    "veh_id":        r.veh_id,
                    "veh_time":      _fmt(r.veh_time,      ".4f"),
                    "veh_dist_trav": _fmt(r.veh_dist_trav, ".4f"),
                    "veh_speed":     _fmt(r.veh_speed,     ".4f"),
                    "veh_speed_cal": _fmt(r.veh_speed_cal, ".4f"),
                    "speed_error":   _fmt(r.speed_error,   ".4f"),
                    "veh_accel":     _fmt(r.veh_accel,     ".4f"),
                    "veh_accel_cal": _fmt(r.veh_accel_cal, ".4f"),
                    "accel_error":   _fmt(r.accel_error,   ".4f"),
                    "veh_jerk":      _fmt(r.veh_jerk,      ".4f"),
                    "veh_jerk_cal":  _fmt(r.veh_jerk_cal,  ".4f"),
                    "jerk_error":    _fmt(r.jerk_error,    ".4f"),
                })


# ── Module helpers ────────────────────────────────────────────────────────

def _db_val(v: float):
    """Convert NaN to None (SQL NULL) for DB storage."""
    try:
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _fmt(v: float, spec: str) -> str:
    try:
        return "" if math.isnan(v) else format(v, spec)
    except (TypeError, ValueError):
        return ""