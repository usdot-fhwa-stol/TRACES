"""Top-level Data > Spline Fitting parameter dialog (Module: Spline Fitting).

Always runs in "Normal" mode (export_mode is never set True by any UI entry
point — there is intentionally no separate CSV export for Spline Fitting;
the calc_* / imputed_status columns it writes flow through the existing
Export All / Export Selected paths once present on the events table).

Invocation
----------
gui/main_window.py's Data > Spline Fitting handler runs EventSelectionDialog
first (multiple events may be selected), then constructs this dialog with
that event_id list and veh_ids=None. Unlike Jerk/Internal Consistency,
which restrict their top-level "Normal" path to the single open event by
convention (not by anything in BaseAnalysisDialog itself), Spline Fitting's
top-level path is always the one that may span several events at once —
the worker thread / per-event progress bar already supports this without
any base-class changes.

There is no right-click counterpart built on this class. Right-click
"Filter > Spline Fitting" opens SplineTestingDialog instead (a separate,
non-threaded preview-only window — see spline_testing_dialog.py) because
its synchronous live-preview / Update-Plot / Close interaction doesn't fit
the OK-or-Save worker-thread orchestration this base class provides.
"""

import logging
import sqlite3
from typing import List

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QMessageBox,
)

from ..base_comp import IMPUTED_SPLINE
from ..base_analysis_dialog import BaseAnalysisDialog
from .spline_comp import (
    BASE_LABELS, FITTING_BASES, SplineFitResult, SplineRowResult,
    allowed_k_options, summarize_fit,
)

logger = logging.getLogger(__name__)

_INSERT_COLUMNS = [
    "event_id", "veh_id", "veh_time", "lane_id",
    "calc_position", "calc_speed", "calc_acceleration", "calc_jerk",
    "imputed_status",
]

_MEASURED_COL_LABEL: dict = {
    "position":     "veh_dist_trav",
    "speed":        "veh_speed",
    "acceleration": "veh_accel",
    "jerk":         "veh_jerk",
}


class SplineCompDialog(BaseAnalysisDialog):
    """Spline Fitting parameter dialog — top-level only.

    Inherits all thread orchestration from BaseAnalysisDialog. On
    completion, writes the four calc_* columns (UPDATE for rows that
    already existed, INSERT for synthetic fill rows) via the main
    connection, then opens SplineStatsDialog with per-trajectory
    goodness-of-fit metrics.
    """

    RIGHT_CLICK_ENABLED = False  # documentation only — see module docstring

    def __init__(self, parent, db_connection, event_ids=None, export_mode=False):
            if export_mode:
                raise ValueError(
                    "SplineCompDialog does not support export mode — Spline "
                    "Fitting always writes to the database, and its calc_* / "
                    "imputed_status columns are exported via the existing "
                    "Export All / Export Selected paths, not a dedicated "
                    "Export Analysis entry.")
            super().__init__(parent, db_connection,
                             event_ids=event_ids, veh_ids=None,
                             export_mode=False)
            from .spline_comp import SplineComp
            SplineComp.ensure_schema(self._db_connection)
            self._configure_fitting_base_options()

    # ── Hook overrides ──────────────────────────────────────────────────

    def _csv_export_label(self) -> str:
        return "Spline Fitting"

    def _build_parameter_form(self) -> QFormLayout:
        form = QFormLayout()

        self._base_combo = QComboBox()
        for key in FITTING_BASES:
            self._base_combo.addItem(BASE_LABELS[key], key)
        form.addRow("Fitting base:", self._base_combo)

        self._s_spin = QDoubleSpinBox()
        self._s_spin.setRange(0.0, 1_000_000.0)
        self._s_spin.setDecimals(4)
        self._s_spin.setValue(1.0)
        form.addRow("Smoothness level (s):", self._s_spin)

        self._k_combo = QComboBox()
        form.addRow("Spline degree (k):", self._k_combo)

        self._fill_missing_check = QCheckBox(
            "Fill missing rows (resample at uniform timestep)")
        form.addRow("", self._fill_missing_check)

        return form

    def _current_params(self) -> dict:
        return {
            "fitting_base":      self._base_combo.currentData(),
            "s":                 self._s_spin.value(),
            "k":                 self._k_combo.currentData(),
            "fill_missing_rows": self._fill_missing_check.isChecked(),
        }

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from ..base_analysis_worker import BaseAnalysisWorker
        return BaseAnalysisWorker(
            db_path, event_ids, veh_ids, params, dest_path,
            run_for_event=self._run_for_event,
            write_csv=None,   # Spline Fitting has no CSV export path
        )

    def _progress_maximum(self, event_ids: list, veh_ids, params: dict) -> int:
        """One tick per event (added in _run_for_event below) plus one
        tick per vehicle within each event, since SplineComp.comp_series
        ticks once per vehicle as it's processed."""
        total_veh = 0
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            cursor = self._db_connection.cursor()
            cursor.execute(
                f"SELECT COUNT(*) FROM "
                f"(SELECT DISTINCT event_id, veh_id FROM events "
                f"WHERE event_id IN ({placeholders}))",
                event_ids,
            )
            total_veh = cursor.fetchone()[0] or 0
        return len(event_ids) + total_veh

    def _run_for_event(self, conn, event_id, veh_ids, params, progress_callback=None):
        from .spline_comp import SplineComp
        comp = SplineComp()
        results = comp.comp_series(
            conn, event_id, veh_ids, options=params,
            progress_callback=progress_callback,
        )
        if progress_callback is not None:
            progress_callback()   # event-level tick, on top of per-vehicle ticks
        return results

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        self._base_combo.setEnabled(enabled)
        self._s_spin.setEnabled(enabled)
        self._k_combo.setEnabled(enabled)
        self._fill_missing_check.setEnabled(enabled)

    def _on_worker_finished(self, results: List[SplineRowResult]) -> None:
            """Persist rows to the events table, then hand a per-trajectory
            goodness-of-fit summary to the base class's normal completion flow
            (which calls _show_results(...) with whatever we pass here, then
            accepts the dialog)."""
            try:
                self._persist_rows(results)
            except sqlite3.Error as exc:
                logger.exception("SplineCompDialog: failed to write results to database")
                self._set_controls_enabled(True)
                self._progress_bar.setValue(0)
                QMessageBox.critical(
                    self, "Error",
                    f"Spline fitting succeeded but writing results to the "
                    f"database failed:\n{exc}\n\nNo changes were committed. "
                    f"You may adjust parameters and try again.")
                return   # dialog stays open — does NOT call super()/accept()
    
            stats = self._build_stats(results)
            super()._on_worker_finished(stats)

    def _show_results(self, results: list) -> None:
        if not results:
            return
        from .spline_stats_dialog import SplineStatsDialog
        dlg = SplineStatsDialog(self.parent(), results, self._current_params())
        dlg.show()

    # ── Fitting-base / k gating ──────────────────────────────────────────

    def _configure_fitting_base_options(self):
        from ..base_comp import BaseComp
        cols = BaseComp._events_columns(self._db_connection)
        availability = {
            "position":     True,   # veh_dist_trav is a required schema column
            "speed":        "veh_speed" in cols,
            "acceleration": "veh_accel" in cols,
            "jerk":         "veh_jerk"  in cols,
        }

        any_enabled = False
        for i in range(self._base_combo.count()):
            key = self._base_combo.itemData(i)
            enabled = availability.get(key, False)
            item = self._base_combo.model().item(i)
            item.setEnabled(enabled)
            if not enabled:
                item.setToolTip(
                    f"required column ({_MEASURED_COL_LABEL[key]}) "
                    f"is absent from this dataset")
            any_enabled = any_enabled or enabled

        selected = False
        for i in range(self._base_combo.count()):
            if self._base_combo.model().item(i).isEnabled():
                self._base_combo.setCurrentIndex(i)
                selected = True
                break

        if not any_enabled or not selected:
            # Unreachable in practice (Position/veh_dist_trav is always
            # present) but kept for symmetry with JerkCompDialog/
            # InternalConsistencyDialog's all-columns-absent handling.
            self._base_combo.setEnabled(False)
            self._primary_btn.setEnabled(False)
            self._primary_btn.setToolTip(
                "No spline-fittable column (veh_dist_trav, veh_speed, "
                "veh_accel, veh_jerk) is available in this dataset.")

        self._base_combo.currentIndexChanged.connect(self._on_base_changed)
        self._on_base_changed()   # populate k options for the initial selection

    def _on_base_changed(self):
        key = self._base_combo.currentData()
        if key is None:
            return
        options = allowed_k_options(key)
        prev_k = self._k_combo.currentData()

        self._k_combo.blockSignals(True)
        self._k_combo.clear()
        for k in options:
            self._k_combo.addItem(str(k), k)
        if prev_k in options:
            self._k_combo.setCurrentIndex(options.index(prev_k))
        else:
            self._k_combo.setCurrentIndex(0)   # smallest allowed k for this base
        self._k_combo.blockSignals(False)

    # ── DB write-back ─────────────────────────────────────────────────────

    def _persist_rows(self, rows: List[SplineRowResult]) -> None:
        measured_rows = [r for r in rows if r.imputed_status != IMPUTED_SPLINE]
        imputed_rows  = [r for r in rows if r.imputed_status == IMPUTED_SPLINE]

        cursor = self._db_connection.cursor()
        try:
            if measured_rows:
                cursor.executemany(
                    """
                    UPDATE events
                    SET calc_position = ?, calc_speed = ?,
                        calc_acceleration = ?, calc_jerk = ?
                    WHERE event_id = ? AND veh_id = ? AND veh_time = ?
                    """,
                    [
                        (r.calc_position, r.calc_speed, r.calc_acceleration,
                         r.calc_jerk, r.event_id, r.veh_id, r.veh_time)
                        for r in measured_rows
                    ],
                )
            if imputed_rows:
                placeholders = ",".join("?" for _ in _INSERT_COLUMNS)
                cursor.executemany(
                    f"INSERT INTO events ({', '.join(_INSERT_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    [
                        (r.event_id, r.veh_id, r.veh_time, r.lane_id,
                         r.calc_position, r.calc_speed, r.calc_acceleration,
                         r.calc_jerk, IMPUTED_SPLINE)
                        for r in imputed_rows
                    ],
                )
            self._db_connection.commit()
        except sqlite3.Error:
            self._db_connection.rollback()
            raise

        logger.info(
            "SplineCompDialog: wrote %d updated row(s), inserted %d imputed row(s).",
            len(measured_rows), len(imputed_rows),
        )

    # ── Goodness-of-fit summary, including skipped trajectories ─────────

    def _build_stats(self, results: List[SplineRowResult]) -> List[SplineFitResult]:
        from ..base_comp import BaseComp
        params = self._current_params()

        ok_stats = summarize_fit(results, params["fitting_base"], params["s"], params["k"])

        produced = {(r.event_id, r.veh_id) for r in results}
        skipped: List[SplineFitResult] = []
        for event_id in self._export_event_ids:
            for veh_id in BaseComp._event_veh_ids(self._db_connection, event_id):
                if (event_id, veh_id) not in produced:
                    skipped.append(SplineFitResult(
                        event_id=event_id, veh_id=veh_id,
                        fitting_base=params["fitting_base"],
                        s=params["s"], k=params["k"],
                        status="skipped",
                        message=(
                            f"Fewer than k+1={params['k'] + 1} valid "
                            f"{params['fitting_base']} points available."
                        ),
                    ))

        return sorted(ok_stats + skipped, key=lambda r: (r.event_id, r.veh_id))