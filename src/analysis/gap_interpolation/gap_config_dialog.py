"""Top-level Data > Gap and Interpolation parameter dialog --
the spec's "First Pop-Up Window" (Task9.1_v2.1, Figure 2).

Always runs on the single currently-open event, same invocation
convention as Jerk/Internal Consistency -- the spec is explicit the
module operates on "the entire event currently loaded in the
application," not a multi-event selection like Spline Fitting.

Buttons
-------
Delete  Deletes gap records (and their filled events rows) for this
        event. Scope follows "Include manual edits": unchecked deletes
        automatic and unfilled gaps only; checked deletes every gap,
        manually filled and removed ones included. The dialog stays open
        and its status message and button states are refreshed.
Skip    Opens the review window on the gaps already recorded, without
        re-running automatic detection and fill.
Apply   Detects every gap across every trajectory in the event
        (GapInterpolationComp.comp_event, original measurements only),
        fits a spline per trajectory using the chosen default parameters
        and evaluates it at every missing timestamp (compute_fill), then
        writes the gaps table (one row per gap) and the events table (one
        row per missing timestamp, imputed_status=IMPUTED_AUTO). With
        "Include manual edits" unchecked, manually filled and removed
        gaps are kept as they are and only the rest are refilled;
        checked, everything is replaced.

"Include manual edits" is disabled when the event has no gap records
and is reset to unchecked whenever the dialog opens or Skip becomes
available, so the default rerun preserves manual work.

Change notification
-------------------
data_changed(event_id) tells other views of the event (MainWindow
reloads the time-space plot on it) that the events table changed. This
dialog's own writes (Apply's fills, Delete) are handed to the review
window when it opens -- it reports them, together with its own, on its
own data_changed when it closes. review_opened(dialog) hands listeners
that window as it is created so they connect to it directly: it usually
closes long after this dialog has. Writes that never reach a review
window (Delete, then Close) are reported on this dialog's data_changed
when it closes. Either way the signal comes once per batch of changes,
after the user is done with the window that made them. Both dialogs
report from a slot on QDialog.finished, which every close emits.

Write target
------------
Apply, Delete and Skip all resolve the "Write changes to" path through
the resolver (normally DatabaseSession.ensure_write_target) before
touching the database -- Skip too, since the review window it opens
writes to the connection chosen here and has no path field of its own.
Choosing another file copies the database and switches the session to
the copy; this dialog then continues on the new connection
(KEEPS_OPEN_ON_DB_SWITCH tells MainWindow not to close it). Nothing is
written before that -- not even the gaps table or imputed_status column,
which are created on the first write.

No CSV export path -- same reasoning as SplineCompDialog: output flows
into the events table and out through Export All / Export Selected.
"""

import logging
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QMessageBox, QPushButton,
)

from ui_kit.db_target_row import DbTargetRow, TargetResolver, db_file_path, in_place_resolver
from ..base_analysis_dialog import BaseAnalysisDialog
from ..base_comp import IMPUTED_AUTO, IMPUTED_MANUAL
from ..spline.spline_comp import BASE_LABELS, FITTING_BASES, allowed_k_options
from .gap_interpolation_comp import (
    GapInterpolationComp, GapResult, FillRow,
    GAP_STATUS_REMOVED, GAP_STATUS_UNFILLED, MANUAL_STATUSES,
    count_valid_points_by_vehicle, delete_gaps, read_gap_rows, replaceable_statuses,
)

logger = logging.getLogger(__name__)

_TITLE = "Gap Interpolation"

_MEASURED_COL_LABEL: Dict[str, str] = {
    "position":     "veh_dist_trav",
    "speed":        "veh_speed",
    "acceleration": "veh_accel",
    "jerk":         "veh_jerk",
}


def _status_counts(gaps: List[GapResult]) -> Dict[str, int]:
    counts = {IMPUTED_AUTO: 0, IMPUTED_MANUAL: 0, GAP_STATUS_REMOVED: 0, GAP_STATUS_UNFILLED: 0}
    for g in gaps:
        counts[g.status] = counts.get(g.status, 0) + 1
    return counts


def _describe_counts(gaps: List[GapResult]) -> str:
    """e.g. '3 automatic, 1 manual, 2 removed' -- zero counts after the
    first two are left out."""
    c = _status_counts(gaps)
    parts = [f"{c[IMPUTED_AUTO]} automatic", f"{c[IMPUTED_MANUAL]} manual"]
    if c[GAP_STATUS_REMOVED]:
        parts.append(f"{c[GAP_STATUS_REMOVED]} removed")
    if c[GAP_STATUS_UNFILLED]:
        parts.append(f"{c[GAP_STATUS_UNFILLED]} unfilled")
    return ", ".join(parts)


@dataclass
class GapFillSummary:
    """Aggregated detect+fill output for one event -- the unit
    BaseAnalysisWorker.run()'s all_results.extend(...) aggregates,
    since gaps and fill_rows need to stay grouped per event for the
    write-back rather than flattened together.

    gaps holds only the gaps this run filled (or tried to); *kept* counts
    the previously recorded gaps it left untouched (manual and removed
    gaps when "Include manual edits" was unchecked)."""
    event_id: int
    gaps: List[GapResult]
    fill_rows: List[FillRow]
    kept: int = 0


class GapConfigDialog(BaseAnalysisDialog):
    """Gap Interpolation parameter dialog -- top-level only.

    No right-click counterpart — per-gap refinement lives in
    gap_review_dialog.py, opened from _show_results (and from Skip),
    not behind a context-menu entry.
    """

    RIGHT_CLICK_ENABLED = False  # documentation only -- see module docstring

    # MainWindow._close_dialogs_using leaves this dialog open when the
    # database is copied and switched: the switch is one this dialog
    # requested, and it moves itself onto the new connection.
    KEEPS_OPEN_ON_DB_SWITCH = True

    # The events table of this event changed (see module docstring).
    data_changed = pyqtSignal(int)
    # A GapReviewDialog was just created, before it is shown. Listeners
    # connect to its own data_changed: it usually outlives this dialog.
    review_opened = pyqtSignal(object)

    # Unreported writes to the events table. A class default so it exists
    # even for hooks super().__init__() calls before __init__ resumes.
    _wrote = False

    def __init__(self, parent, db_connection, event_ids=None, export_mode=False,
                 resolve_target: Optional[TargetResolver] = None):
        if export_mode:
            raise ValueError(
                "GapConfigDialog does not support export mode -- Gap "
                "Interpolation always writes to the database; "
                "results are exported via the existing Export All / "
                "Export Selected paths once written.")
        super().__init__(parent, db_connection,
                         event_ids=event_ids, veh_ids=None,
                         export_mode=False)
        self._resolve_target = resolve_target or in_place_resolver(db_connection, _TITLE)
        self._configure_fitting_base_options()

        # Detection doesn't depend on fitting_base/k (or on fill state --
        # it reads original rows only), so it's safe to run once here for
        # the live viability label below. The worker thread (started on
        # Apply) redoes detection on its own connection when actually
        # filling -- sqlite3 connections aren't shared across threads --
        # so this duplicates a cheap query, not the fitting.
        self._event_id = self._export_event_ids[0]
        comp = GapInterpolationComp()
        self._detected_gaps: List[GapResult] = comp.comp_event(self._db_connection, self._event_id)
        self._gapped_veh_ids = sorted({g.veh_id for g in self._detected_gaps})

        self._existing: List[GapResult] = []
        self._note = ""   # result of the last Delete, shown above the summary
        self._refresh_state(reset_include_manual=True)
        self.finished.connect(self._report_writes)

    def _report_writes(self, _result: int):
        """On finished (every close): report writes no review window took
        over (e.g. Delete, then Close), once."""
        if self._wrote:
            self._wrote = False
            logger.info("GapConfigDialog: event %s data changed; notifying", self._event_id)
            self.data_changed.emit(self._event_id)

    def _setup_ui(self):
        super()._setup_ui()
        self._primary_btn.setText("Apply")   # matches the spec's/docstrings' naming
        layout = self.layout()

        # Full-width status line -- a top-level row of its own rather than a
        # form field, so it can wrap across the whole dialog instead of being
        # squeezed into the form's narrow field column.
        self._viability_label = QLabel()
        self._viability_label.setWordWrap(True)
        layout.insertWidget(1, self._viability_label)

        self._target_row = DbTargetRow(
            self._db_connection, browse_title="Write Gap Interpolation Changes To")
        layout.insertWidget(layout.count() - 1, self._target_row)

        self._include_manual_check = QCheckBox(
            "Include manual edits (manually filled and removed gaps)")
        self._include_manual_check.setToolTip(
            "Unchecked: Apply refills only automatic and unfilled gaps, and "
            "Delete deletes only those -- manually filled and removed gaps "
            "are kept.\nChecked: Apply replaces and Delete deletes every gap.")
        self._include_manual_check.toggled.connect(self._on_include_manual_toggled)
        layout.insertWidget(layout.count() - 1, self._include_manual_check)

        # The button row is the last item of BaseAnalysisDialog's layout.
        btn_row = layout.itemAt(layout.count() - 1).layout()

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setAutoDefault(False)
        self._delete_btn.setToolTip(
            "Delete gap records for this event, with their filled values, so "
            "those gaps can be filled again. Follows “Include manual edits”.")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.insertWidget(0, self._delete_btn)   # left of the stretch, apart from Apply

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setAutoDefault(False)
        self._skip_btn.setToolTip(
            "Continue reviewing gaps previously recorded for this event "
            "without re-running automatic detection and fill.")
        self._skip_btn.clicked.connect(self._on_skip)
        btn_row.insertWidget(btn_row.indexOf(self._primary_btn), self._skip_btn)

    # ── State ───────────────────────────────────────────────────────────

    def _include_manual(self) -> bool:
        return self._include_manual_check.isChecked()

    def _gaps_in_scope(self) -> List[GapResult]:
        """Recorded gaps that Apply would replace and Delete would delete."""
        statuses = replaceable_statuses(self._include_manual())
        return [g for g in self._existing if g.status in statuses]

    def _refresh_state(self, reset_include_manual: bool = False):
        """Re-read the recorded gaps and update the checkbox, Skip, Delete
        and the status message to match."""
        self._existing = read_gap_rows(self._db_connection, self._event_id)
        has_records = bool(self._existing)

        self._include_manual_check.blockSignals(True)
        if reset_include_manual or not has_records:
            self._include_manual_check.setChecked(False)
        self._include_manual_check.setEnabled(has_records)
        self._include_manual_check.blockSignals(False)

        self._skip_btn.setEnabled(has_records)
        self._delete_btn.setEnabled(bool(self._gaps_in_scope()))
        self._update_viability_label()

    def _on_include_manual_toggled(self, _checked: bool):
        self._delete_btn.setEnabled(bool(self._gaps_in_scope()))
        self._update_viability_label()

    # ── Write target ─────────────────────────────────────────────────────

    def _resolve_write_target(self) -> bool:
        """Resolve the path field to a writable connection (first-write
        warning, or copy-and-switch). False if the user cancelled or the
        target couldn't be prepared -- the resolver has said why."""
        target = self._resolve_target(self, self._target_row.path())
        if target is None:
            return False
        if target is not self._db_connection:
            self._db_connection = target
            self._db_path = db_file_path(target)   # the worker opens this path
            logger.info("GapConfigDialog: now writing to %s", self._db_path)
        self._target_row.set_connection(target)
        return True

    # ── Buttons ──────────────────────────────────────────────────────────

    def _on_skip(self):
        if not self._resolve_write_target():
            return
        self._open_review_dialog(self._event_id)
        self.accept()

    def _open_review_dialog(self, event_id: int) -> None:
        """Open the review window, handing it this dialog's unreported
        writes so it reports them with its own when it closes."""
        from .gap_review_dialog import GapReviewDialog
        dlg = GapReviewDialog(self.parent(), self._db_connection, event_id,
                              changed=self._wrote)
        self._wrote = False
        self.review_opened.emit(dlg)   # listeners connect to dlg.data_changed
        dlg.show()

    def _on_delete(self):
        to_delete = self._gaps_in_scope()
        if not to_delete:
            return
        scope = ("all recorded gaps, including manually filled and removed gaps"
                 if self._include_manual() else
                 "the automatically filled and unfilled gaps (manually filled "
                 "and removed gaps are kept)")
        reply = QMessageBox.question(
            self, "Delete Gap Records?",
            f"Delete {len(to_delete)} gap record(s) ({_describe_counts(to_delete)}) "
            f"-- {scope} -- together with their filled values?\n\n"
            f"Deleted gaps are filled again by the next Apply.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._resolve_write_target():
            return

        # Re-read on the resolved connection (it may be a fresh copy).
        statuses = replaceable_statuses(self._include_manual())
        to_delete = [g for g in read_gap_rows(self._db_connection, self._event_id)
                     if g.status in statuses]
        try:
            delete_gaps(self._db_connection, to_delete)
            self._db_connection.commit()
        except sqlite3.Error as exc:
            self._db_connection.rollback()
            logger.exception("GapConfigDialog: failed to delete gap records")
            QMessageBox.critical(self, "Error", f"Deleting the gap records failed:\n{exc}")
            return

        self._wrote = self._wrote or bool(to_delete)
        logger.info("GapConfigDialog: deleted %d gap record(s) for event %s",
                    len(to_delete), self._event_id)
        self._note = f"Deleted {len(to_delete)} gap record(s)."
        self._refresh_state()

    def _on_ok(self):
        """Apply."""
        if self._include_manual():
            n_manual = sum(1 for g in self._existing if g.status in MANUAL_STATUSES)
            if n_manual:
                reply = QMessageBox.question(
                    self, "Replace Manual Edits?",
                    f"“Include manual edits” is checked, so the {n_manual} "
                    f"manually filled or removed gap(s) recorded for this event "
                    f"will be refilled automatically with the parameters set "
                    f"above, and their manual changes lost.\n\n"
                    f"Uncheck it to keep them.\n\nDo you want to replace them?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        if not self._resolve_write_target():
            return

        # Clear what this run replaces; what's left in the gaps table is
        # kept, and the worker skips those gaps.
        try:
            GapInterpolationComp.ensure_schema(self._db_connection)
            statuses = replaceable_statuses(self._include_manual())
            to_replace = [g for g in read_gap_rows(self._db_connection, self._event_id)
                          if g.status in statuses]
            delete_gaps(self._db_connection, to_replace)
            self._db_connection.commit()
        except sqlite3.Error as exc:
            self._db_connection.rollback()
            logger.exception("GapConfigDialog: failed to clear gaps before Apply")
            QMessageBox.critical(self, "Error",
                                 f"Preparing the database for Apply failed:\n{exc}")
            return
        # Old fills are gone even if the worker then fails.
        self._wrote = self._wrote or bool(to_replace)
        super()._on_ok()

    # ── Hook overrides ──────────────────────────────────────────────────

    def _csv_export_label(self) -> str:
        return _TITLE

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

    def _set_label(self, text: str, warn: bool = False):
        if self._note:
            text = f"{self._note} {text}"
        self._viability_label.setText(text)
        self._viability_label.setStyleSheet(
            "color: #a05a00; font-style: italic;" if warn else "font-style: italic;")

    def _update_viability_label(self):
        if not hasattr(self, "_viability_label") or not hasattr(self, "_existing"):
            return   # still inside super().__init__() -- form/detection not ready yet

        if self._existing:
            self._show_previous_run_summary()
            return

        if not self._detected_gaps:
            self._set_label("No gaps detected in this event.")
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
            self._set_label(
                f"All {total} detected gap(s) will be auto-filled with these settings.")
        else:
            self._set_label(
                f"{len(unfillable)} of {total} gap(s), on {len(unviable_veh_ids)} "
                f"vehicle(s), cannot be auto-filled with k={k} (fewer than {k + 1} "
                f"valid points) -- lower k, or leave them for manual refinement "
                f"in the review window.", warn=True)

    def _show_previous_run_summary(self):
        """Skip is available -- show what's recorded in the gaps table and
        what Apply / Delete would do with it under the current "Include
        manual edits" setting."""
        total = len(self._existing)
        if self._include_manual():
            action = ("Apply replaces all of them, manually filled and removed "
                      "gaps included; Delete deletes all of them.")
        else:
            action = ("Apply refills the automatic and unfilled gaps and keeps "
                      "manually filled and removed gaps; Delete deletes only the "
                      "automatic and unfilled gaps.")
        self._set_label(
            f"This event has {total} gap(s) recorded from a previous run "
            f"({_describe_counts(self._existing)}). Click Skip to continue "
            f"reviewing them. {action}")

    def _configure_fitting_base_options(self):
        """Disable bases whose required column is absent -- same pattern
        as SplineCompDialog._configure_fitting_base_options."""
        from ..base_comp import BaseComp
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
        for w in (self._base_combo, self._s_spin, self._k_combo, self._target_row):
            w.setEnabled(enabled)
        if enabled:
            self._refresh_state()   # Skip / Delete / checkbox follow the records
        else:
            for w in (self._include_manual_check, self._skip_btn, self._delete_btn):
                w.setEnabled(False)

    def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path):
        from ..base_analysis_worker import BaseAnalysisWorker
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
        GapInterpolationComp.ensure_schema(conn)
        comp = GapInterpolationComp()

        # Gaps still recorded at this point are the ones _on_ok kept
        # (manual and removed, unless "Include manual edits"): leave them
        # and their events rows alone. Detection reads original rows only,
        # so a kept gap is detected again under the same (veh_id, gap_id).
        kept = {(g.veh_id, g.gap_id): g for g in read_gap_rows(conn, event_id)}

        gaps: List[GapResult] = []
        for gap in comp.comp_event(conn, event_id, options=params):
            prev = kept.get((gap.veh_id, gap.gap_id))
            if prev is None:
                gaps.append(gap)
            elif (prev.start_time, prev.end_time) != (gap.start_time, gap.end_time):
                logger.warning(
                    "GapConfigDialog: kept gap veh=%s gap=%s spans %s-%s but "
                    "detection now finds %s-%s; leaving the kept record as is.",
                    gap.veh_id, gap.gap_id, prev.start_time, prev.end_time,
                    gap.start_time, gap.end_time)
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
                # Record the parameters this run used (or tried, for gaps it
                # couldn't fill), so Gap Review opens each gap on the
                # settings that produced its current fill.
                gap.spline_fitting_base = params["fitting_base"]
                gap.spline_s = params["s"]
                gap.spline_k = params["k"]
                gap.status = IMPUTED_AUTO if gap.gap_id in filled_gap_ids else GAP_STATUS_UNFILLED
            fill_rows.extend(veh_fill_rows)

        if progress_callback is not None:
            progress_callback()

        return [GapFillSummary(event_id=event_id, gaps=gaps, fill_rows=fill_rows,
                               kept=len(kept))]

    # ── DB write-back ─────────────────────────────────────────────────────

    def _persist_summaries(self, summaries: List[GapFillSummary]) -> None:
        from .gap_interpolation_comp import write_fill_rows, write_gap_rows
        try:
            for summary in summaries:
                write_gap_rows(self._db_connection, summary.gaps)
                write_fill_rows(self._db_connection, summary.fill_rows, IMPUTED_AUTO)
            self._db_connection.commit()
        except sqlite3.Error:
            self._db_connection.rollback()
            raise
        if any(s.fill_rows for s in summaries):
            self._wrote = True

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
        kept_gaps     = sum(s.kept for s in results)
        if total_gaps == 0 and kept_gaps == 0:
            QMessageBox.information(
                self, _TITLE,
                "No gaps were detected in this event.")
            return

        kept_msg = (f"{kept_gaps} previously recorded gap(s) (manually filled or "
                    f"removed) were kept unchanged.")
        if total_gaps == 0:
            msg = f"No other gaps to fill. {kept_msg}"
        else:
            msg = f"Detected {total_gaps} gap(s) to fill and filled {total_rows} row(s) automatically."
            if kept_gaps:
                msg += f"\n\n{kept_msg}"
            if unfilled_gaps:
                msg += (
                    f"\n\n{unfilled_gaps} gap(s) could not be filled automatically "
                    f"(fewer than k+1 valid points for the chosen spline degree) "
                    f"and will need manual attention in the review window."
                )
        QMessageBox.information(self, _TITLE, msg)

        self._open_review_dialog(self._event_id)
