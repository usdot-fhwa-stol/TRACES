"""Top-level Data > Gap and Interpolation parameter dialog --
the spec's "First Pop-Up Window" (Task9.1_v2.1, Figure 2).

Always runs on the single currently-open event, same invocation
convention as Jerk/Internal Consistency -- the spec is explicit the
module operates on "the entire event currently loaded in the
application," not a multi-event selection like Spline Fitting.

Gap definition
--------------
Two spinboxes define what counts as a gap (GapInterpolationComp's
min_missing / max_bridge options; see gap_interpolation_comp.find_gaps):
    Minimum missing points per gap        (default 3, minimum 1)
    Maximum existing points to merge gaps (default 3, minimum 0)
Changing either re-runs detection immediately, in memory, and updates
the status line -- the number of gaps can change. Nothing is written
until Apply.

Buttons
-------
Delete  Deletes gap records (and their filled events rows) for this
        event. Scope follows "Include manual edits": unchecked deletes
        automatic and unfilled gaps only; checked deletes every gap,
        manually filled and removed ones included. Measured points a
        deleted fill had superseded get their measured values back. The
        dialog stays open and its status message and button states are
        refreshed.
Skip    Opens the review window on the gaps already recorded, without
        re-running automatic detection and fill.
Apply   Detects every gap across every trajectory in the event with the
        gap definition above (GapInterpolationComp.comp_event, measured
        data only), fits a spline per trajectory using the chosen default
        parameters and evaluates it at every fill timestamp (compute_fill),
        then writes the gaps table (one row per gap) and the events table
        (one row per fill timestamp, imputed_status=IMPUTED_AUTO). A
        merged gap's fill replaces the measured points it bridges; their
        measured values are saved in gap_superseded_points. With "Include
        manual edits" unchecked, manually filled and removed gaps are kept
        as they are and only the rest are refilled; checked, everything is
        replaced.

Kept gaps and a changed gap definition. A kept (manual or removed) gap
was detected under whatever definition was in effect then. A newly
detected gap that overlaps a kept gap is left out -- the kept gap and
its fill win -- and all of a vehicle's gaps, kept and new, are then
renumbered in chronological order so gap numbers stay 1, 2, 3, ...

"Include manual edits" is disabled when the event has no gap records
and is reset to unchecked whenever the dialog opens or Skip becomes
available, so the default rerun preserves manual work. Beside it, a
color swatch and "(manually filled and removed gaps)" form a separate
legend -- the color of manually filled gaps in the review window. The
legend is not part of the checkbox: clicking it does nothing, and it
stays in full color whether or not the checkbox is enabled.

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
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox,
)

from ui_kit.db_target_row import DbTargetRow, TargetResolver, db_file_path, in_place_resolver
from ui_kit.plot_style import REGION_RGBA, WHITE
from ..base_analysis_dialog import BaseAnalysisDialog
from ..base_comp import IMPUTED_AUTO, IMPUTED_MANUAL
from ..spline.spline_comp import BASE_LABELS, FITTING_BASES, allowed_k_options
from .gap_interpolation_comp import (
    GapInterpolationComp, GapResult, FillRow,
    DEFAULT_MAX_BRIDGE, DEFAULT_MIN_MISSING, MAX_BRIDGE_LOWEST, MIN_MISSING_LOWEST,
    GAP_STATUS_REMOVED, GAP_STATUS_UNFILLED, MANUAL_STATUSES,
    count_valid_points_by_vehicle, delete_gaps, read_gap_rows, replaceable_statuses,
)

logger = logging.getLogger(__name__)

_TITLE = "Gap Interpolation"

# Upper bound for both gap definition spinboxes -- far beyond any useful
# value, just keeps the fields a sensible width.
_GAP_DEFINITION_MAX = 9999

_MEASURED_COL_LABEL: Dict[str, str] = {
    "position":     "veh_dist_trav",
    "speed":        "veh_speed",
    "acceleration": "veh_accel",
    "jerk":         "veh_jerk",
}


def _region_swatch(name: str, size: int) -> QPixmap:
    """*size* x *size* square in the color GapReviewDialog's *name* gap band
    shows on the plot's white background (plot_style.REGION_RGBA composited
    over white, so the swatch follows the band if its color changes), with
    a 1 px black outline."""
    r, g, b, a = REGION_RGBA[name]
    bg = QColor(WHITE)
    alpha = a / 255.0
    fill = QColor(*(round(c * alpha + base * (1.0 - alpha))
                    for c, base in ((r, bg.red()), (g, bg.green()), (b, bg.blue()))))
    pixmap = QPixmap(size, size)
    pixmap.fill(fill)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor("black"), 1))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return pixmap


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


def _describe_detected(gaps: List[GapResult]) -> str:
    """e.g. '12 gap(s), 3 of them merged (7 existing points bridged)'."""
    merged = [g for g in gaps if g.bridged_rows]
    text = f"{len(gaps)} gap(s)"
    if merged:
        bridged = sum(g.bridged_rows for g in merged)
        text += (f", {len(merged)} of them merged across {bridged} existing "
                 f"point(s) that the fill replaces")
    return text


@dataclass
class GapFillSummary:
    """Aggregated detect+fill output for one event -- the unit
    BaseAnalysisWorker.run()'s all_results.extend(...) aggregates,
    since gaps and fill_rows need to stay grouped per event for the
    write-back rather than flattened together.

    gaps holds only the gaps this run filled (or tried to); *kept* counts
    the previously recorded gaps it left untouched (manual and removed
    gaps when "Include manual edits" was unchecked). renumbered holds
    (old gap_id, gap) for kept gaps whose gap_id changed, so their gaps
    records can be rewritten under the new number; covered counts newly
    detected gaps left out because they overlap a kept gap."""
    event_id: int
    gaps: List[GapResult]
    fill_rows: List[FillRow]
    kept: int = 0
    renumbered: List[Tuple[int, GapResult]] = field(default_factory=list)
    covered: int = 0


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
        # it reads measured data only), so it runs here, and again
        # whenever the gap definition changes, for the live status line
        # below. In memory only -- nothing is written until Apply. The
        # worker thread (started on Apply) redoes detection on its own
        # connection when actually filling -- sqlite3 connections aren't
        # shared across threads -- so this duplicates a cheap query, not
        # the fitting.
        self._event_id = self._export_event_ids[0]
        self._detected_gaps: List[GapResult] = []
        self._gapped_veh_ids: List[int] = []
        self._detect_gaps()

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

        # "Include manual edits   ■ (manually filled and removed gaps)": the
        # checkbox, then a separate legend -- the swatch is the manual gap
        # band's color in the review window. Only the checkbox is enabled /
        # disabled (_refresh_state); the legend is plain labels, not part of
        # the control, so it keeps its color and isn't clickable.
        self._include_manual_check = QCheckBox("Include manual edits")
        self._include_manual_check.setToolTip(
            "Unchecked: Apply refills only automatic and unfilled gaps, and "
            "Delete deletes only those -- manually filled and removed gaps "
            "are kept.\nChecked: Apply replaces and Delete deletes every gap.")
        self._include_manual_check.toggled.connect(self._on_include_manual_toggled)

        swatch_size = max(10, self.fontMetrics().ascent())
        legend_tip = "Color of manually filled gaps in the review window."
        self._manual_swatch = QLabel()
        self._manual_swatch.setPixmap(_region_swatch("manual", swatch_size))
        self._manual_swatch.setFixedSize(swatch_size, swatch_size)
        self._manual_caption = QLabel("(manually filled and removed gaps)")
        for w in (self._manual_swatch, self._manual_caption):
            w.setToolTip(legend_tip)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self._include_manual_check)
        row.addSpacing(8)
        row.addWidget(self._manual_swatch)
        row.addWidget(self._manual_caption)
        row.addStretch(1)
        layout.insertLayout(layout.count() - 1, row)

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

    # ── Gap detection (in memory) ─────────────────────────────────────────

    def _gap_definition(self) -> dict:
        return {
            "min_missing": self._min_missing_spin.value(),
            "max_bridge":  self._max_bridge_spin.value(),
        }

    def _detect_gaps(self):
        """Re-run detection with the current gap definition. Reads only."""
        comp = GapInterpolationComp()
        self._detected_gaps = comp.comp_event(
            self._db_connection, self._event_id, options=self._gap_definition())
        self._gapped_veh_ids = sorted({g.veh_id for g in self._detected_gaps})

    def _on_gap_definition_changed(self, _value: int):
        if not hasattr(self, "_existing"):
            return   # still inside __init__ -- detection runs there
        self._detect_gaps()
        self._update_viability_label()

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
        self._include_manual_check.blockSignals(False)
        self._include_manual_check.setEnabled(has_records)

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
            f"Measured points replaced by those fills get their measured "
            f"values back. Deleted gaps are filled again by the next Apply.",
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

        # Gap definition. Values are set before the signals are connected,
        # and keyboard tracking is off, so detection reruns once per
        # committed value (arrow step, Enter, focus out), not per keystroke.
        self._min_missing_spin = QSpinBox()
        self._min_missing_spin.setRange(MIN_MISSING_LOWEST, _GAP_DEFINITION_MAX)
        self._min_missing_spin.setValue(DEFAULT_MIN_MISSING)
        self._min_missing_spin.setKeyboardTracking(False)
        self._min_missing_spin.setToolTip(
            "A run of consecutive missing points is a gap only if it has at "
            "least this many. Shorter runs are left as they are, unless they "
            "fall inside a merged gap.")
        self._min_missing_spin.valueChanged.connect(self._on_gap_definition_changed)
        form.addRow("Minimum missing points per gap:", self._min_missing_spin)

        self._max_bridge_spin = QSpinBox()
        self._max_bridge_spin.setRange(MAX_BRIDGE_LOWEST, _GAP_DEFINITION_MAX)
        self._max_bridge_spin.setValue(DEFAULT_MAX_BRIDGE)
        self._max_bridge_spin.setKeyboardTracking(False)
        self._max_bridge_spin.setToolTip(
            "Gaps separated by at most this many existing (measured) points "
            "are merged into one gap. Those points still shape the spline fit, "
            "but the fill replaces their values; the measured values are kept "
            "and come back if the fill is removed or deleted. 0 never merges.")
        self._max_bridge_spin.valueChanged.connect(self._on_gap_definition_changed)
        form.addRow("Maximum existing points to merge gaps:", self._max_bridge_spin)

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
            self._set_label("No gaps detected in this event with this gap definition.")
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

        detected = f"Detected {_describe_detected(self._detected_gaps)}."
        total = len(self._detected_gaps)
        if not unfillable:
            self._set_label(f"{detected} All will be auto-filled with these settings.")
        else:
            self._set_label(
                f"{detected} {len(unfillable)} of {total} gap(s), on "
                f"{len(unviable_veh_ids)} vehicle(s), cannot be auto-filled with "
                f"k={k} (fewer than {k + 1} valid points) -- lower k, or leave "
                f"them for manual refinement in the review window.", warn=True)

    def _show_previous_run_summary(self):
        """Skip is available -- show what's recorded in the gaps table and
        what Apply / Delete would do with it under the current "Include
        manual edits" setting, plus what the current gap definition
        detects."""
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
            f"reviewing them. {action} The gap definition above detects "
            f"{_describe_detected(self._detected_gaps)}.")

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
            **self._gap_definition(),
            "fitting_base": self._base_combo.currentData(),
            "s":            self._s_spin.value(),
            "k":            self._k_combo.currentData(),
        }

    def _set_controls_enabled(self, enabled: bool):
        super()._set_controls_enabled(enabled)
        for w in (self._min_missing_spin, self._max_bridge_spin,
                  self._base_combo, self._s_spin, self._k_combo, self._target_row):
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

    @staticmethod
    def _merge_with_kept(detected: List[GapResult], kept: List[GapResult]
                         ) -> Tuple[List[GapResult], List[Tuple[int, GapResult]], int]:
        """Combine newly detected gaps with the kept ones, per vehicle.

        A detected gap overlapping a kept gap is dropped -- the kept gap
        and its fill win. The rest, together with the kept gaps, are
        numbered 1, 2, 3, ... in chronological order per vehicle.

        Returns (new gaps to fill, [(old gap_id, kept gap) whose gap_id
        changed], number of detected gaps dropped)."""
        kept_by_veh: Dict[int, List[GapResult]] = {}
        for gap in kept:
            kept_by_veh.setdefault(gap.veh_id, []).append(gap)

        new_by_veh: Dict[int, List[GapResult]] = {}
        covered = 0
        for gap in detected:
            if any(gap.overlaps(k) for k in kept_by_veh.get(gap.veh_id, [])):
                covered += 1
                continue
            new_by_veh.setdefault(gap.veh_id, []).append(gap)

        renumbered: List[Tuple[int, GapResult]] = []
        new_gaps: List[GapResult] = []
        for veh_id in sorted(set(kept_by_veh) | set(new_by_veh)):
            kept_ids = {id(g) for g in kept_by_veh.get(veh_id, [])}
            ordered = sorted(kept_by_veh.get(veh_id, []) + new_by_veh.get(veh_id, []),
                             key=lambda g: g.start_time)
            for gap_id, gap in enumerate(ordered, start=1):
                if id(gap) in kept_ids:
                    if gap.gap_id != gap_id:
                        renumbered.append((gap.gap_id, gap))
                        gap.gap_id = gap_id
                else:
                    gap.gap_id = gap_id
                    new_gaps.append(gap)
        return new_gaps, renumbered, covered

    def _run_for_event(self, conn, event_id, veh_ids, params, progress_callback=None):
        GapInterpolationComp.ensure_schema(conn)
        comp = GapInterpolationComp()

        # Gaps still recorded at this point are the ones _on_ok kept
        # (manual and removed, unless "Include manual edits"): leave them
        # and their events rows alone. They may have been detected under a
        # different gap definition, so they're matched to the new
        # detection by time span, not gap_id (_merge_with_kept).
        kept = read_gap_rows(conn, event_id)
        detected = comp.comp_event(conn, event_id, options=params)
        gaps, renumbered, covered = self._merge_with_kept(detected, kept)
        if covered:
            logger.info(
                "GapConfigDialog: event %s -- %d detected gap(s) overlap kept "
                "gaps and were left out.", event_id, covered)
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
                               kept=len(kept), renumbered=renumbered, covered=covered)]

    # ── DB write-back ─────────────────────────────────────────────────────

    def _persist_summaries(self, summaries: List[GapFillSummary]) -> None:
        from .gap_interpolation_comp import delete_gap_row, write_fill_rows, write_gap_rows
        try:
            for summary in summaries:
                # Renumbered kept gaps: drop every old record first, so no
                # new number collides with an old one not yet rewritten.
                for old_gap_id, gap in summary.renumbered:
                    delete_gap_row(self._db_connection, gap.event_id, gap.veh_id, old_gap_id)
                write_gap_rows(self._db_connection,
                               [gap for _old, gap in summary.renumbered] + summary.gaps)
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
        replaced_rows = sum(g.bridged_rows for s in results for g in s.gaps
                            if g.status == IMPUTED_AUTO)
        kept_gaps     = sum(s.kept for s in results)
        covered_gaps  = sum(s.covered for s in results)
        if total_gaps == 0 and kept_gaps == 0:
            QMessageBox.information(
                self, _TITLE,
                "No gaps were detected in this event with this gap definition.")
            return

        kept_msg = (f"{kept_gaps} previously recorded gap(s) (manually filled or "
                    f"removed) were kept unchanged.")
        if covered_gaps:
            kept_msg += (f" {covered_gaps} detected gap(s) overlapping them were "
                         f"left out.")
        if total_gaps == 0:
            msg = f"No other gaps to fill. {kept_msg}"
        else:
            msg = f"Detected {total_gaps} gap(s) to fill and filled {total_rows} row(s) automatically."
            if replaced_rows:
                msg += (f" {replaced_rows} of those rows replace existing points "
                        f"inside merged gaps; their measured values are kept and "
                        f"come back if the fill is removed or deleted.")
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
