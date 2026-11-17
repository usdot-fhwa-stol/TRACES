"""Trajectory Stitching preview window (Task 9.2, Figures 2 and 4).

One dialog, two states: the overlap split slider (Figure 4) is shown only
when the pair overlaps. Left: distance/speed/acceleration on a shared time
axis (KinematicStackedPlotWidget). Right: a status line (review / undo
only), transposed info table (Traj 1 / Traj 2 / Stitched -- the same
names as the plot legends) with the editable new ID, relationship and
seam summary, the overlap slider, and the red inline safety warnings.
Bottom: output database path, Undo, Stitch, Cancel.

Traj 1 / Traj 2 are chronological order (not click order). This dialog
compares two objects, so it deliberately does not use plot_style's
per-kinematic colors: on every plot Traj 1 is amber and Traj 2 green --
solid where the stitch keeps the samples, dashed where it drops them. The
split line is purple dashed; overlap and gap bounds are black dashed
lines, on all three plots.

Stitch line. Only a gap leaves a span where the stitched trajectory has
no observations, so only a gap gets a stitch line: a dark blue dashed
line ("Stitched" in the legends) from Traj 1's last sample to Traj 2's
first, across the gap. Dashed because nothing is written there -- the
stitch keeps both trajectories' rows and adds none in between. An
overlap or a continuous pair has no stitch line: the trajectories meet
one sample apart.

Overlap. Each trajectory is drawn solid over the time ranges it
contributes at the current split (StitchPlan.ranges) and dashed, in its
own color, over the overlap samples the stitch drops ("Traj 1 dropped" /
"Traj 2 dropped" in the legends): Traj 1's dashed tail runs right of the
split line, Traj 2's dashed head left of it. Each dashed part starts on
the trajectory's last kept sample next to it, so solid and dashed join
without a break. Moving the slider moves where each trajectory turns from
solid to dashed -- the solid lines always show exactly what Stitch will
write, and the dashed ones what it leaves out.

The split slider steps through samples, not percentages: one position per
Traj 2 sample in the overlap (StitchInputs.split_candidates), passed to the
plan as an exact split_time, plus a last position that keeps all of Traj
1's overlap. The split line therefore always sits on a real sample: Traj 2
starts exactly on it (or, at the last position, Traj 1 ends exactly on
it). No timestamp can come from both trajectories, so the other one stops
one sample short; that one-step seam is what the stitch writes.

Series are shown/hidden by clicking their legend entries. The stitch
line is linked -- hiding it on one plot hides it on all three -- while
Traj 1 and Traj 2 (kept and dropped) toggle per plot.

Review and Undo. Given a stitch_record (ManualStitchingComp.prepare_existing),
the dialog opens in review: it replays that stitch from its recorded split,
with the ID and slider locked and Stitch disabled, and Undo enabled. Undo
reverses the stitch (ManualStitchingComp.undo) and switches the dialog to
the normal state with the sources active again, so they can be stitched
with different settings right away (under a new ID; the undone one is
never reused) or the dialog closed. undo_info records the undo whatever
happens next, so the caller refreshes the plot even on Cancel / Close.

Writing goes through a resolver callable(parent, requested_path) -> connection
or None, normally DatabaseSession.ensure_write_target; in_place_resolver() is
the fallback when none is supplied. The path row and resolver helpers live in
ui_kit.db_target_row (shared with GapConfigDialog); TargetResolver,
db_file_path and in_place_resolver are re-exported here for existing imports.
"""

import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui_kit.db_target_row import (  # noqa: F401 -- TargetResolver re-exported
    DbTargetRow, TargetResolver, db_file_path, in_place_resolver,
)
from ui_kit.kinematic_stacked_plot_widget import KinematicStackedPlotWidget, Series
from ui_kit.plot_style import Role
from .safety_rules import SafetyContext, evaluate_safety
from .stitching_comp import (
    ManualStitchingComp, StitchInputs, StitchPlan, StitchRecord, TemporalCase,
)

logger = logging.getLogger(__name__)

_TITLE = "Trajectory Stitching"

_ROWS = [
    "Vehicle ID", "Start (s)", "End (s)", "Duration (s)", "Observations",
    "Initial position (m)", "Final position (m)",
    "Initial speed (m/s)", "Final speed (m/s)",
]
_COL_FIRST, _COL_SECOND, _COL_STITCHED = 0, 1, 2
_COLUMN_HEADERS = ["Traj 1", "Traj 2", "Stitched"]   # same names as the legends

# Series colors, the same on every plot (an exception to plot_style's
# per-kinematic colors: here color identifies the trajectory).
FIRST_COLOR = "#E69F00"      # amber
SECOND_COLOR = "#009E73"     # green
STITCHED_COLOR = "#1F3A93"   # dark blue (dashed stitch line across a gap)

# Stitch line: Role.PREVIEW (dashed, drawn on top), a little wider than the
# role default so the dashes read clearly against the trajectories' ends.
_STITCH_LINE_WIDTH = 3.0

# Dropped overlap samples: Role.DERIVED's dash pattern in the trajectory's
# own color, thinner than the kept (solid, 4 px) line so what the stitch
# keeps stays the dominant line.
_DROPPED_LINE_WIDTH = 2.5

_STATUS_STYLE = {
    "review": "background: #EEF3FB; border: 1px solid #9DB1D6; padding: 4px;",
    "undone": "background: #EAF6EE; border: 1px solid #8CC5A0; padding: 4px;",
}


@dataclass
class StitchResult:
    stitch_id: int
    new_veh_id: int
    source_veh_ids: Tuple[int, int]
    connection: sqlite3.Connection
    db_path: str


@dataclass
class UndoResult:
    stitch_id: int
    removed_veh_id: int                 # the stitched trajectory, now deleted
    restored_veh_ids: Tuple[int, int]   # its sources, active again
    connection: sqlite3.Connection
    db_path: str


# ── Formatting helpers ─────────────────────────────────────────────────────

def _fmt(x: Optional[float], digits: int = 2) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}f}"


def _first_finite(a: Optional[np.ndarray]) -> Optional[float]:
    if a is None:
        return None
    idx = np.flatnonzero(np.isfinite(a))
    return float(a[idx[0]]) if idx.size else None


def _last_finite(a: Optional[np.ndarray]) -> Optional[float]:
    if a is None:
        return None
    idx = np.flatnonzero(np.isfinite(a))
    return float(a[idx[-1]]) if idx.size else None


def _traj_values(tr) -> list:
    """Table rows 1.. (everything but Vehicle ID) for one trajectory."""
    speed_missing = tr.speed is None
    return [
        _fmt(tr.start_time), _fmt(tr.end_time), _fmt(tr.duration), str(tr.n),
        _fmt(_first_finite(tr.dist)), _fmt(_last_finite(tr.dist)),
        "n/a" if speed_missing else _fmt(_first_finite(tr.speed)),
        "n/a" if speed_missing else _fmt(_last_finite(tr.speed)),
    ]


def _with_neighbors(mask: np.ndarray) -> np.ndarray:
    """*mask* widened by one sample on each side, so a dashed dropped
    section starts and ends on the kept samples next to it and joins the
    solid line instead of leaving a one-step hole."""
    out = mask.copy()
    out[:-1] |= mask[1:]
    out[1:] |= mask[:-1]
    return out


def _masked(values: dict, mask: np.ndarray) -> dict:
    return {k: (None if v is None else np.where(mask, v, np.nan))
            for k, v in values.items()}


# ── Dialog ─────────────────────────────────────────────────────────────────

class StitchingPreviewDialog(QDialog):

    def __init__(self, parent, db_connection: sqlite3.Connection,
                 inputs: StitchInputs, resolve_target: Optional[TargetResolver] = None,
                 stitch_record: Optional[StitchRecord] = None):
        super().__init__(parent)
        self._conn = db_connection
        self._inputs = inputs
        self._resolve_target = resolve_target or in_place_resolver(db_connection, _TITLE)
        self._record = stitch_record          # set: reviewing an existing stitch
        self._undone: Optional[StitchRecord] = None
        self._plan: Optional[StitchPlan] = None
        self._id_ok = True
        self._series: Dict[str, Series] = {}
        self._split_lines = []
        self._split_slider: Optional[QSlider] = None
        self._safety = evaluate_safety(SafetyContext(inputs.first, inputs.second, inputs.seam))
        self.result_info: Optional[StitchResult] = None
        self.undo_info: Optional[UndoResult] = None

        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setModal(True)

        self._setup_ui()
        self._draw_sources()
        self._draw_markers()
        self._on_id_changed(self._id_spin.value())   # validates, builds plan
        self._apply_state()
        self._fit_table_width()
        self._set_initial_view()
        self._size_and_center()

    # ── UI construction ────────────────────────────────────────────────

    def _setup_ui(self):
        is_overlap = self._inputs.seam.case is TemporalCase.OVERLAP

        self._plots = KinematicStackedPlotWidget(
            kinematics=("dist", "speed", "accel"),
            available={"speed": self._inputs.first.speed is not None,
                       "accel": self._inputs.first.accel is not None},
            y_fit="visible", time_step=self._inputs.time_step,)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._plots)
        splitter.addWidget(self._build_right_panel(is_overlap))
        splitter.setStretchFactor(0, 1)   # plots take all spare width
        splitter.setStretchFactor(1, 0)
        splitter.setChildrenCollapsible(False)

        root = QVBoxLayout(self)
        root.addWidget(splitter, stretch=1)
        root.addLayout(self._build_bottom_row())

    def _build_right_panel(self, is_overlap: bool) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)

        # Review / undo status. Word-wrapped, so it takes the table's width
        # instead of widening the panel.
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setTextFormat(Qt.TextFormat.RichText)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Minimum)
        self._status_label.hide()
        lay.addWidget(self._status_label)

        self._table = self._build_table()
        lay.addWidget(self._table, stretch=1)     # only the table grows / scrolls

        self._id_label = self._fixed_label()
        self._id_label.setStyleSheet("color: #C00000;")
        self._id_label.hide()
        lay.addWidget(self._id_label)

        self._relation_label = self._fixed_label()
        self._relation_label.setText(self._relationship_text())
        lay.addWidget(self._relation_label)

        self._seam_label = self._fixed_label()
        lay.addWidget(self._seam_label)

        if is_overlap:
            lay.addLayout(self._build_split_group())

        self._safety_label = self._fixed_label()
        self._safety_label.setStyleSheet("color: #D00000; font-weight: bold;")
        self._safety_label.setText("<br>".join(f"⚠ {m}" for m in self._safety))
        self._safety_label.setVisible(bool(self._safety))
        lay.addWidget(self._safety_label)

        return panel

    @staticmethod
    def _fixed_label(wrap: bool = False) -> QLabel:
        lbl = QLabel()
        lbl.setWordWrap(wrap)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return lbl

    def _compact_spin_width(self) -> int:
        """Width for the proposed ID plus two more digits, instead of the
        10 digits QSpinBox reserves for its 2,147,483,647 maximum."""
        s = self._id_spin
        fm = s.fontMetrics()
        digits = len(str(self._inputs.default_new_veh_id))
        return (s.sizeHint().width()
                - fm.horizontalAdvance(str(s.maximum()))
                + fm.horizontalAdvance("9" * digits))

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(len(_ROWS), 3)
        t.setVerticalHeaderLabels(_ROWS)

        t.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        t.setMinimumHeight(110)

        self._id_spin = QSpinBox()
        self._id_spin.setRange(0, 2_147_483_647)
        self._id_spin.setValue(self._inputs.default_new_veh_id)
        self._id_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._id_spin.setToolTip("Proposed vehicle ID for the stitched trajectory")
        self._id_spin.setFixedWidth(self._compact_spin_width())
        self._id_spin.valueChanged.connect(self._on_id_changed)

        # Container so the table stretches the cell, not the spinbox;
        # right-aligned to match the other columns' numbers.
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 2, 0)
        holder_layout.addStretch()
        holder_layout.addWidget(self._id_spin)
        t.setCellWidget(0, _COL_STITCHED, holder)

        self._table = t
        self._set_column(_COL_FIRST, self._inputs.first, include_id=True)
        self._set_column(_COL_SECOND, self._inputs.second, include_id=True)
        return t

    def _build_split_group(self) -> QVBoxLayout:
        """Slider with one position per Traj 2 sample in the overlap
        (position k: Traj 2 takes over at its k-th overlap sample) plus a
        last position keeping all of Traj 1's overlap. Starts at the sample
        nearest the middle of the overlap, like the former 50% default,
        or, when reviewing, at the recorded split. Arrow keys step one
        sample."""
        seam = self._inputs.seam
        self._split_times = self._inputs.split_candidates()
        n = len(self._split_times)             # position n = keep all of Traj 1
        start = self._recorded_split_position(n)
        if start is None:
            mid = 0.5 * (seam.overlap_start + seam.overlap_end)
            start = int(np.argmin(np.abs(self._split_times - mid))) if n else 0

        box = QVBoxLayout()
        box.addWidget(QLabel("Select where Traj 2 takes over (one step per sample):"))
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, n)
        s.setValue(start)
        s.setSingleStep(1)
        s.setPageStep(max(1, n // 10))
        s.setTickPosition(QSlider.TickPosition.TicksBelow)
        s.setTickInterval(max(1, n // 10))
        s.setMinimumWidth(220)
        s.valueChanged.connect(self._on_split_changed)
        box.addWidget(s)
        self._split_slider = s

        # Three labels under the slider, read left to right: Traj 1 runs
        # from the overlap start to the divider, Traj 2 from the divider to
        # the overlap end. Three equal grid columns keep the divider time
        # centered under the slider whatever the outer labels' widths.
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        start_label = QLabel(f"Traj 1: {seam.overlap_start:.2f} s")
        self._split_label = QLabel()                  # divider time, per position
        self._split_label.setStyleSheet("font-weight: bold;")
        end_label = QLabel(f"Traj 2: {seam.overlap_end:.2f} s")
        row.addWidget(start_label, 0, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(self._split_label, 0, 1, Qt.AlignmentFlag.AlignHCenter)
        row.addWidget(end_label, 0, 2, Qt.AlignmentFlag.AlignRight)
        for col in range(3):
            row.setColumnStretch(col, 1)
        box.addLayout(row)
        return box   # the divider time is filled by the first _rebuild_plan

    def _recorded_split_position(self, n: int) -> Optional[int]:
        """Slider position of the reviewed stitch's split, or None when not
        reviewing. Records made before keep_all_first was stored can't
        tell "all of Traj 1" from "Traj 2 starts at the overlap end" when
        Traj 2 has a sample there; the latter is assumed (they differ in
        that one sample only)."""
        rec = self._record
        if rec is None or rec.split_time is None:
            return None
        on_sample = n > 0 and bool(np.any(np.abs(self._split_times - rec.split_time) < 1e-9))
        keep_all = rec.keep_all_first
        if keep_all is None:
            keep_all = (rec.split_fraction or 0.0) >= 1.0 and not on_sample
        if keep_all or n == 0:
            return n
        return int(np.argmin(np.abs(self._split_times - rec.split_time)))

    def _build_bottom_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._target_row = DbTargetRow(
            self._conn, browse_title="Write Stitching Changes To")
        row.addWidget(self._target_row, stretch=1)
        row.addSpacing(24)

        self._undo_btn = QPushButton("Undo")
        self._undo_btn.setAutoDefault(False)
        self._undo_btn.clicked.connect(self._on_undo)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self.reject)
        self._stitch_btn = QPushButton("Stitch")
        self._stitch_btn.setAutoDefault(False)   # Enter must not stitch
        self._stitch_btn.clicked.connect(self._on_stitch)
        row.addWidget(self._undo_btn)
        row.addSpacing(12)
        row.addWidget(self._stitch_btn)
        row.addWidget(self._cancel_btn)
        return row

    # ── Drawing ────────────────────────────────────────────────────────

    @staticmethod
    def _traj_series(tr) -> dict:
        return {"dist": tr.dist, "speed": tr.speed, "accel": tr.accel}

    def _draw_sources(self):
        # The stitch line exists only for a gap (see module docstring).
        # Added first so it is also first in each legend; break_gaps=False
        # because its whole point is to span the gap.
        if self._inputs.seam.case is TemporalCase.GAP:
            self._series["stitched"] = self._plots.add_series(
                "Stitched", Role.PREVIEW, color=STITCHED_COLOR, linked=True,
                width=_STITCH_LINE_WIDTH, break_gaps=False)
        for key, name, color in (("first", "Traj 1", FIRST_COLOR),
                                 ("second", "Traj 2", SECOND_COLOR)):
            self._series[key] = self._plots.add_series(
                name, Role.MEASURED, color=color, linked=False)
        # Only an overlap drops samples (see module docstring).
        if self._inputs.seam.case is TemporalCase.OVERLAP:
            for key, name, color in (("first_dropped", "Traj 1 dropped", FIRST_COLOR),
                                     ("second_dropped", "Traj 2 dropped", SECOND_COLOR)):
                self._series[key] = self._plots.add_series(
                    name, Role.DERIVED, color=color, linked=False,
                    width=_DROPPED_LINE_WIDTH)
        self._draw_trajectories(None)

    def _draw_trajectories(self, plan: Optional[StitchPlan]):
        """Draw each trajectory solid over the time ranges it contributes
        to *plan* -- only narrower than the whole trajectory when the pair
        overlaps -- and dashed over the samples *plan* drops. With no plan
        (or no split) both are drawn solid in full."""
        for key, tr in (("first", self._inputs.first), ("second", self._inputs.second)):
            values = self._traj_series(tr)
            dropped_series = self._series.get(f"{key}_dropped")
            if plan is None or plan.split_time is None:
                self._plots.set_data(self._series[key], tr.time, values)
                if dropped_series is not None:
                    self._plots.clear_data(dropped_series)
                continue
            keep = self._kept_mask(plan, tr)
            self._plots.set_data(self._series[key], tr.time, _masked(values, keep))
            if dropped_series is not None:
                drop = ~keep
                if drop.any():
                    self._plots.set_data(dropped_series, tr.time,
                                         _masked(values, _with_neighbors(drop)))
                else:
                    self._plots.clear_data(dropped_series)

    @staticmethod
    def _kept_mask(plan: StitchPlan, tr) -> np.ndarray:
        """Samples of *tr* that *plan* keeps -- the same TimeRanges Stitch
        uses to write the rows."""
        keep = np.zeros(tr.time.shape, dtype=bool)
        for time_range in plan.ranges[tr.veh_id]:
            keep |= time_range.mask(tr.time)
        return keep

    def _draw_markers(self):
        seam = self._inputs.seam
        if seam.case is TemporalCase.OVERLAP:
            self._plots.add_vlines([seam.overlap_start, seam.overlap_end], Role.BOUNDARY)
            self._split_lines = self._plots.add_vlines([seam.overlap_start], Role.SPLIT)
        elif seam.case is TemporalCase.GAP:
            self._plots.add_vlines([seam.first_end, seam.second_start], Role.BOUNDARY)

    def _set_initial_view(self):
        """Center the seam region so it fills about half the width (like
        GapReviewDialog); y auto-fits the visible data."""
        seam, dt = self._inputs.seam, self._inputs.time_step
        if seam.case is TemporalCase.OVERLAP:
            lo, hi = seam.overlap_start, seam.overlap_end
        else:
            lo, hi = seam.first_end, seam.second_start
        half = max(hi - lo, 10 * dt)
        mid = 0.5 * (lo + hi)
        self._plots.set_x_range(mid - half, mid + half)

    def _draw_stitch_line(self, plan: StitchPlan):
        """Gap only: one dashed segment from Traj 1's last sample to Traj
        2's first, on every plot -- the point where the stitched
        trajectory switches source."""
        series = self._series.get("stitched")
        if series is None:
            return
        tr, src = plan.preview.trajectory, plan.preview.source
        idx = np.nonzero(src[1:] != src[:-1])[0]
        if idx.size == 0:
            self._plots.clear_data(series)
            return
        pick = np.column_stack([idx, idx + 1, idx + 1]).ravel()
        is_break = np.zeros(pick.shape, dtype=bool)
        is_break[2::3] = True                    # NaN after each segment
        values = {}
        for key, arr in self._traj_series(tr).items():
            if arr is None:
                values[key] = None
                continue
            y = np.asarray(arr, dtype=np.float64)[pick]
            y[is_break] = np.nan
            values[key] = y
        self._plots.set_data(series, tr.time[pick], values)

    # ── Plan / state updates ──────────────────────────────────────────

    def _plan_for_current_split(self) -> StitchPlan:
        """Plan at the slider's position: an exact Traj 2 sample time, or
        (last position) all of Traj 1's overlap. No slider: not an overlap,
        so the split doesn't apply."""
        new_id = self._id_spin.value()
        if self._split_slider is None:
            return self._inputs.plan(new_id)
        k = self._split_slider.value()
        if k >= len(self._split_times):
            return self._inputs.plan(new_id, split_fraction=1.0)
        return self._inputs.plan(new_id, split_time=float(self._split_times[k]))

    def _rebuild_plan(self):
        try:
            plan = self._plan_for_current_split()
        except ValueError as exc:
            self._plan = None
            if "stitched" in self._series:
                self._plots.clear_data(self._series["stitched"])
            self._draw_trajectories(None)
            self._seam_label.setText(f"<span style='color:#C00000'>{exc}</span>")
            self._update_stitch_enabled()
            return

        self._plan = plan
        self._draw_trajectories(plan)
        self._draw_stitch_line(plan)
        if plan.split_time is not None:
            for line in self._split_lines:
                line.setPos(plan.split_time)
        self._set_column(_COL_STITCHED, plan.preview.trajectory, include_id=False)
        self._seam_label.setText(self._seam_text(plan))
        self._update_split_labels(plan)
        self._update_stitch_enabled()

    def _on_split_changed(self, _value: int):
        self._rebuild_plan()

    def _update_split_labels(self, plan: StitchPlan):
        """Divider time under the middle of the slider: where Traj 2
        starts or, at the last position, where Traj 1 ends -- the split
        line's position either way."""
        if self._split_slider is None or plan.split_time is None:
            return
        self._split_label.setText(f"{plan.split_time:.2f} s")

    def _on_id_changed(self, value: int):
        # Reviewing: the ID is the recorded stitch's own, in use by design.
        self._id_ok = self._record is not None or ManualStitchingComp.is_veh_id_available(
            self._conn, self._inputs.event_id, value)
        self._id_spin.setStyleSheet("" if self._id_ok else "QSpinBox { color: #C00000; }")
        self._id_label.setText(
            "" if self._id_ok else f"Vehicle ID {value} is already used in this event.")
        self._id_label.setVisible(not self._id_ok)
        self._rebuild_plan()

    def _update_stitch_enabled(self):
        if self._record is not None:
            self._stitch_btn.setEnabled(False)
            self._stitch_btn.setToolTip("Already stitched. Undo it first to stitch differently.")
            return
        ok = self._plan is not None and self._id_ok
        self._stitch_btn.setEnabled(ok)
        self._stitch_btn.setToolTip("" if ok else "Resolve the issue shown on the right first.")

    def _apply_state(self):
        """Enable / label everything for the current state: reviewing an
        existing stitch, after undoing it, or a new stitch."""
        reviewing = self._record is not None
        event_id = self._inputs.event_id
        a, b = self._inputs.first.veh_id, self._inputs.second.veh_id

        self._id_spin.setEnabled(not reviewing)
        if self._split_slider is not None:
            self._split_slider.setEnabled(not reviewing)
        self._undo_btn.setEnabled(reviewing)
        self._undo_btn.setToolTip(
            f"Reverse this stitch: delete vehicle {self._record.new_veh_id} and "
            f"restore vehicles {a} and {b}." if reviewing else
            "Nothing to undo here. To undo a stitch, select the stitched "
            "trajectory, choose Trajectory Stitching and press Enter.")
        self._cancel_btn.setText("Close" if reviewing or self._undone else "Cancel")
        self._update_stitch_enabled()

        if reviewing:
            rec = self._record
            created = rec.created_at.replace("T", " ")
            self.setWindowTitle(
                f"Trajectory Stitching — Event {event_id} — Vehicle {rec.new_veh_id}")
            self._show_status(
                "review",
                f"<b>Vehicle {rec.new_veh_id}</b> was stitched from vehicles {a} and {b} "
                f"on {created}. <b>Undo</b> deletes vehicle {rec.new_veh_id} and "
                f"restores {a} and {b}.")
        elif self._undone is not None:
            self.setWindowTitle(f"Trajectory Stitching — Event {event_id}")
            self._show_status(
                "undone",
                f"<b>Stitch undone.</b> Vehicle {self._undone.new_veh_id} was deleted; "
                f"vehicles {a} and {b} are active again. Stitch them with new "
                f"settings, or Close.")
        else:
            self.setWindowTitle(f"Trajectory Stitching — Event {event_id}")
            self._status_label.hide()

    def _show_status(self, kind: str, html: str):
        self._status_label.setStyleSheet(f"QLabel {{ {_STATUS_STYLE[kind]} }}")
        self._status_label.setText(html)
        self._status_label.show()

    # ── Table / text ──────────────────────────────────────────────────

    def _set_column(self, col: int, tr, include_id: bool):
        values = [str(tr.veh_id)] + _traj_values(tr)
        for row in range(0 if include_id else 1, len(_ROWS)):
            item = QTableWidgetItem(values[row])
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, col, item)

    def _fit_table_width(self):
        """Minimum width = row labels + three equal columns, each wide
        enough for the widest content in any column (including the ID
        spinbox). The splitter gives every other pixel to the plots."""
        t = self._table
        header = t.horizontalHeader()
        col_w = max(max(t.sizeHintForColumn(c), header.sectionSizeHint(c))
                    for c in range(t.columnCount()))
        col_w = max(col_w, self._id_spin.minimumWidth() + 2)
        width = (t.verticalHeader().sizeHint().width()
                 + 3 * col_w
                 + 2 * t.frameWidth()
                 + t.verticalScrollBar().sizeHint().width())
        t.setMinimumWidth(width)

    def _relationship_text(self) -> str:
        s = self._inputs.seam
        if s.case is TemporalCase.OVERLAP:
            txt = (f"<b>Overlap:</b> {s.overlap_duration:.2f} s "
                   f"(Traj 1: {s.n_overlap_first} pts, Traj 2: {s.n_overlap_second} pts)")
            if s.contained:
                txt += "<br>Traj 2 lies entirely within Traj 1's time span."
            return txt
        if s.case is TemporalCase.GAP:
            return (f"<b>Gap:</b> {s.gap_duration:.2f} s "
                    f"({s.missing_samples} sample(s) missing at {s.time_step:g} s)")
        return (f"<b>Continuous:</b> Traj 2 begins {s.gap_duration:.2f} s after "
                f"Traj 1 ends (sampling interval {s.time_step:g} s)")

    def _seam_text(self, plan: StitchPlan) -> str:
        if not plan.joins:
            return "No seam: the stitched trajectory is Traj 1 unchanged."
        lines = []
        for i, j in enumerate(plan.joins, 1):
            dv = f", Δv {j.d_speed:+.2f} m/s" if math.isfinite(j.d_speed) else ""
            v = f", implied {j.implied_speed:.1f} m/s" if math.isfinite(j.implied_speed) else ""
            lines.append(f"Seam {i} at {j.t_after:.2f} s: Δt {j.dt:.2f} s, "
                         f"Δd {j.d_dist:+.2f} m{dv}{v}")
        return "<br>".join(lines)

    # ── Actions ────────────────────────────────────────────────────────

    def _resolve(self) -> Optional[sqlite3.Connection]:
        """Write target for Stitch / Undo, or None if the user cancelled."""
        target = self._resolve_target(self, self._target_row.path())
        if target is not None:
            self._conn = target
            self._target_row.set_connection(target)
        return target

    def _on_stitch(self):
        if self._plan is None or not self._id_ok or self._record is not None:
            return
        target = self._resolve()
        if target is None:
            return

        try:
            stitch_id = ManualStitchingComp.apply(target, self._plan, self._safety)
        except (ValueError, RuntimeError, sqlite3.Error) as exc:
            QMessageBox.critical(self, _TITLE,
                                 f"The stitch was not saved:\n{exc}")
            self._on_id_changed(self._id_spin.value())   # e.g. ID taken meanwhile
            return

        self.result_info = StitchResult(
            stitch_id=stitch_id, new_veh_id=self._plan.new_veh_id,
            source_veh_ids=self._plan.source_veh_ids,
            connection=target, db_path=db_file_path(target))
        self.accept()

    def _on_undo(self):
        rec = self._record
        if rec is None:
            return
        a, b = rec.source_veh_ids
        answer = QMessageBox.question(
            self, _TITLE,
            f"Undo the stitch that created vehicle {rec.new_veh_id}?\n\n"
            f"Vehicle {rec.new_veh_id} is deleted, including any analysis results "
            f"computed on it, and vehicles {a} and {b} become active again. "
            f"ID {rec.new_veh_id} is not reused.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        target = self._resolve()
        if target is None:
            return

        try:
            ManualStitchingComp.undo(target, rec.stitch_id)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, _TITLE, f"The stitch was not undone:\n{exc}")
            return

        self.undo_info = UndoResult(
            stitch_id=rec.stitch_id, removed_veh_id=rec.new_veh_id,
            restored_veh_ids=rec.source_veh_ids,
            connection=target, db_path=db_file_path(target))
        self._undone, self._record = rec, None

        # Now an ordinary stitch of the restored pair, under a fresh ID.
        new_id = ManualStitchingComp.next_available_veh_id(target, self._inputs.event_id)
        self._id_spin.blockSignals(True)
        self._id_spin.setValue(new_id)
        self._id_spin.blockSignals(False)
        self._on_id_changed(new_id)
        self._apply_state()

    # ── Geometry ───────────────────────────────────────────────────────

    def _size_and_center(self):
        top = self.parent().window() if self.parent() else None
        if top is not None:
            w = min(max(int(top.width() * 0.85), 900), 1500)
            h = min(max(int(top.height() * 0.85), 600), 950)
        else:
            w, h = 1200, 800
        self.resize(w, h)
        if top is not None:
            g = top.geometry()
            self.move(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2)
