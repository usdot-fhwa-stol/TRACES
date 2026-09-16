"""Top-level Data > Missing Data and Interpolation parameter dialog --
the spec's "First Pop-Up Window" (Task9.1_v2.1, Figure 2).

Always runs on the single currently-open event, same invocation
convention as Jerk/Internal Consistency -- the spec is explicit the
module operates on "the entire event currently loaded in the
application," not a multi-event selection like Spline Fitting.

On Apply: detects every gap across every trajectory in the event
(MissingDataInterpolationComp.comp_series), fits a spline per trajectory
using the chosen default parameters and evaluates it at every missing
timestamp (MissingDataInterpolationComp.compute_fill), then writes both
the gaps table (one row per detected gap) and the events table (one row
per missing timestamp, imputed_status=IMPUTED_AUTO).

No CSV export path -- same reasoning as SplineCompDialog: output flows
into the events table and out through Export All / Export Selected.
"""

import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Dict, List

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QMessageBox, QPushButton

from comp.base_analysis_dialog import BaseAnalysisDialog
from comp.base_comp import IMPUTED_AUTO
from comp.spline.spline_comp import BASE_LABELS, FITTING_BASES, allowed_k_options
from .missing_data_interpolation_comp import (
    MissingDataInterpolationComp, GapResult, FillRow,
    GAP_STATUS_UNFILLED, count_valid_points_by_vehicle,
    event_has_gap_records, delete_gap_records,
)

logger = logging.getLogger(__name__)

_MEASURED_COL_LABEL: Dict[str, str] = {
    "position":     "veh_dist_trav",
    "speed":        "veh_speed",
    "acceleration": "veh_accel",
    "jerk":         "veh_jerk",
}

_GAPS_INSERT_COLUMNS = [
    "event_id", "veh_id", "gap_id", "start_time", "end_time", "missing_rows",
    "spline_fitting_base", "spline_s", "spline_k", "added_points_json", "status",
]


def _db_val(x):
    """NaN -> NULL for sqlite storage; passes everything else through."""
    return None if isinstance(x, float) and math.isnan(x) else x


@dataclass
class GapFillSummary:
    """Aggregated detect+fill output for one event -- the unit
    BaseAnalysisWorker.run()'s all_results.extend(...) aggregates,
    since gaps and fill_rows need to stay grouped per event for the
    write-back rather than flattened together."""
    event_id: int
    gaps: List[GapResult]
    fill_rows: List[FillRow]


class GapConfigDialog(BaseAnalysisDialog):
    """Missing Data and Interpolation parameter dialog -- top-level only.

    No right-click counterpart — per-gap refinement lives in 
    gap_review_dialog.py, opened from _show_results (and from Skip), 
    not behind a context-menu entry.
    """

    RIGHT_CLICK_ENABLED = False  # documentation only -- see module docstring

    def __init__(self, parent, db_connection, event_ids=None, export_mode=False):
        if export_mode:
            raise ValueError(
                "GapConfigDialog does not support export mode -- Missing "
                "Data and Interpolation always writes to the database; "
                "results are exported via the existing Export All / "
                "Export Selected paths once written.")
        super().__init__(parent, db_connection,
                         event_ids=event_ids, veh_ids=None,
                         export_mode=False)
        MissingDataInterpolationComp.ensure_schema(self._db_connection)
        self._configure_fitting_base_options()

        # Detection doesn't depend on fitting_base/k, so it's safe to run
        # once here for the live viability label below. The worker thread
        # (started on Apply) redoes detection on its own connection when
        # actually filling -- sqlite3 connections aren't shared across
        # threads -- so this duplicates a cheap query, not the fitting.
        self._event_id = self._export_event_ids[0]
        comp = MissingDataInterpolationComp()
        self._detected_gaps: List[GapResult] = comp.comp_event(self._db_connection, self._event_id)
        self._gapped_veh_ids = sorted({g.veh_id for g in self._detected_gaps})

        self._update_viability_label()
        self._update_viability_label()
        self._update_skip_enabled()

    def _setup_ui(self):
        super()._setup_ui()
        self._primary_btn.setText("Apply")   # matches the spec's/docstrings' naming

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setEnabled(False)   # updated in __init__ once self._event_id exists
        self._skip_btn.setToolTip(
            "Continue reviewing gaps already recorded for this event "
            "without re-running automatic detection and fill.")
        self._skip_btn.clicked.connect(self._on_skip)

        # btn_row is the QHBoxLayout BaseAnalysisDialog._setup_ui built as
        # this dialog's 3rd top-level layout item (form, progress bar, buttons).
        btn_row = self.layout().itemAt(2).layout()
        btn_row.insertWidget(btn_row.indexOf(self._primary_btn), self._skip_btn)

    def _update_skip_enabled(self):
        self._skip_btn.setEnabled(
            event_has_gap_records(self._db_connection, self._event_id))

    def _on_skip(self):
        self._open_review_dialog(self._event_id)
        self.accept()

    def _open_review_dialog(self, event_id: int) -> None:
        from .gap_review_dialog import GapReviewDialog
        dlg = GapReviewDialog(self.parent(), self._db_connection, event_id)
        dlg.show()

    def _on_ok(self):
        if event_has_gap_records(self._db_connection, self._event_id):
            reply = QMessageBox.question(
                self, "Start Over?",
                "This event already has gaps recorded from a previous run "
                "(automatically and/or manually filled).\n\n"
                "Clicking \u201cYes\u201d will delete those records and "
                "detect gaps from scratch using the parameters set above.\n\n"
                "Click \u201cCancel\u201d and use \u201cSkip\u201d instead "
                "to continue reviewing the existing gaps without starting "
                "over.\n\nDo you want to start over?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            delete_gap_records(self._db_connection, self._event_id)
        super()._on_ok()

    # ── Hook overrides ──────────────────────────────────────────────────

    def _csv_export_label(self) -> str:
        return "Missing Data Interpolation"

    def _build_parameter_form(self) -> QFormLayout:
        form = QFormLayout()

        self._base_combo = QComboBox()
        for key in FITTING_BASES:
            self._base_combo.addItem(BASE_LABELS[key], key)
        self._base_combo.currentIndexChanged.connect(self._on_base_changed)
        form.addRow("Default fitting base:", self._base_combo)

        self._s_spin = QDoubleSpinBox()
        self._s_spin.setRange(0.0, 1_000_000.0)
        self._s_spin.setDecimals(4)
        self._s_spin.setValue(1.0)
        form.addRow("Default smoothness level (s):", self._s_spin)

        self._k_combo = QComboBox()
        self._k_combo.currentIndexChanged.connect(self._update_viability_label)
        form.addRow("Default spline degree (k):", self._k_combo)

        self._viability_label = QLabel()
        self._viability_label.setWordWrap(True)
        form.addRow("", self._viability_label)

        self._on_base_changed()   # populate k options for the initial selection
        return form

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
            self._k_combo.setCurrentIndex(0)

        self._k_combo.blockSignals(False)
        self._update_viability_label()

    def _update_viability_label(self):
        if not hasattr(self, "_viability_label") or not hasattr(self, "_detected_gaps"):
            return   # still inside super().__init__() -- form/detection not ready yet

        if not self._detected_gaps:
            self._viability_label.setText("No gaps detected in this event.")
            self._viability_label.setStyleSheet("color: #555; font-style: italic;")
            return

        fitting_base = self._base_combo.currentData()
        k = self._k_combo.currentData()
        if fitting_base is None or k is None:
            return

        counts = count_valid_points_by_vehicle(
            self._db_connection, self._event_id, self._gapped_veh_ids, fitting_base,
        )
        unviable_veh_ids = {v for v, n in counts.items() if n < k + 1}
        unfillable = [g for g in self._detected_gaps if g.veh_id in unviable_veh_ids]

        total = len(self._detected_gaps)
        if not unfillable:
            self._viability_label.setText(
                f"All {total} detected gap(s) can be auto-filled with these settings.")
            self._viability_label.setStyleSheet("color: #555; font-style: italic;")
        else:
            self._viability_label.setText(
                f"{len(unfillable)} of {total} gap(s), on {len(unviable_veh_ids)} "
                f"vehicle(s), cannot be auto-filled with k={k} (fewer than {k + 1} "
                f"valid points) -- lower k, or leave them for manual refinement "
                f"in the review window.")
            self._viability_label.setStyleSheet("color: #a05a00; font-style: italic;")

    def _configure_fitting_base_options(self):
        """Disable bases whose required column is absent -- same pattern
        as SplineCompDialog._configure_fitting_base_options."""
        from comp.base_comp import BaseComp
        cols = BaseComp._events_columns(self._db_connection)
        availability = {
            "position":     True,   # veh_dist_trav is a required schema column
            "speed":        "veh_speed" in cols,
            "acceleration": "veh_accel" in cols,
            "jerk":         "veh_jerk"  in cols,
        }
        for i in range(self._base_combo.count()):
            key = self._base_combo.itemData(i)
            enabled = availability.get(key, False)
            item = self._base_combo.model().item(i)
            item.setEnabled(enabled)
            if not enabled:
                item.setToolTip(
                    f"required column ({_MEASURED_COL_LABEL[key]}) "
                    f"is absent from this dataset")
        for i in range(self._base_combo.count()):
            if self._base_combo.model().item(i).isEnabled():
                self._base_combo.setCurrentIndex(i)
                break

    def _current_params(self) -> dict:
        return {
            "fitting_base": self._base_combo.currentData(),
            "s":            self._s_spin.value(),
            "k":            self._k_combo.currentData(),
        }

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        self._base_combo.setEnabled(enabled)
        self._s_spin.setEnabled(enabled)
        self._k_combo.setEnabled(enabled)

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from comp.base_comp import BaseAnalysisWorker
        return BaseAnalysisWorker(
            db_path, event_ids, veh_ids, params, dest_path,
            run_for_event=self._run_for_event,
            write_csv=None,   # no CSV export path -- see module docstring
        )

    def _progress_maximum(self, event_ids: list, veh_ids, params: dict) -> int:
        """One detection tick per event plus one fill tick per event --
        an underestimate (exact per-vehicle counts aren't known until
        detection runs), same tradeoff SplineCompDialog's estimate makes."""
        return len(event_ids) * 2

    # ── Worker callable ───────────────────────────────────────────────────

    def _run_for_event(self, conn, event_id, veh_ids, params, progress_callback=None):
        MissingDataInterpolationComp.ensure_schema(conn)
        comp = MissingDataInterpolationComp()

        gaps = comp.comp_event(conn, event_id, options=params)
        if progress_callback is not None:
            progress_callback()

        gaps_by_veh: Dict[int, List[GapResult]] = {}
        for gap in gaps:
            gaps_by_veh.setdefault(gap.veh_id, []).append(gap)

        fill_rows: List[FillRow] = []
        for veh_id, veh_gaps in gaps_by_veh.items():
            veh_fill_rows = comp.compute_fill(
                conn, event_id, veh_id, veh_gaps,
                fitting_base=params["fitting_base"],
                s=params["s"], k=params["k"],
            )
            filled_gap_ids = {r.gap_id for r in veh_fill_rows}
            for gap in veh_gaps:
                gap.status = IMPUTED_AUTO if gap.gap_id in filled_gap_ids else GAP_STATUS_UNFILLED
            fill_rows.extend(veh_fill_rows)

        if progress_callback is not None:
            progress_callback()

        return [GapFillSummary(event_id=event_id, gaps=gaps, fill_rows=fill_rows)]

    # ── DB write-back ─────────────────────────────────────────────────────

    def _persist_summaries(self, summaries: List[GapFillSummary]) -> None:
        from .missing_data_interpolation_comp import write_fill_rows, write_gap_rows
        try:
            for summary in summaries:
                write_gap_rows(self._db_connection, summary.gaps)
                write_fill_rows(self._db_connection, summary.fill_rows, IMPUTED_AUTO)
            self._db_connection.commit()
        except sqlite3.Error:
            self._db_connection.rollback()
            raise

    def _on_worker_finished(self, results: List[GapFillSummary]) -> None:
        try:
            self._persist_summaries(results)
        except sqlite3.Error as exc:
            logger.exception("GapConfigDialog: failed to write results to database")
            self._set_controls_enabled(True)
            self._progress_bar.setValue(0)
            QMessageBox.critical(
                self, "Error",
                f"Gap detection succeeded but writing results to the "
                f"database failed:\n{exc}\n\nNo changes were committed. "
                f"You may adjust parameters and try again.")
            return
        super()._on_worker_finished(results)

    def _show_results(self, results: List[GapFillSummary]) -> None:
        total_gaps    = sum(len(s.gaps) for s in results)
        unfilled_gaps = sum(1 for s in results for g in s.gaps if g.status == GAP_STATUS_UNFILLED)
        total_rows    = sum(len(s.fill_rows) for s in results)
        if total_gaps == 0:
            QMessageBox.information(
                self, "Missing Data and Interpolation",
                "No gaps were detected in this event.")
            return

        msg = f"Detected {total_gaps} gap(s) and filled {total_rows} row(s) automatically."
        if unfilled_gaps:
            msg += (
                f"\n\n{unfilled_gaps} gap(s) could not be filled automatically "
                f"(fewer than k+1 valid points for the chosen spline degree) "
                f"and will need manual attention in the review window."
            )
        QMessageBox.information(self, "Missing Data and Interpolation", msg)

        self._open_review_dialog(self._event_id)