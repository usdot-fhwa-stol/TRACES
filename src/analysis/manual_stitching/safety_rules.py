"""Safety rules for Manual Trajectory Stitching (Task 9.2, "Safety Check").

Rules WARN, they never block: the preview dialog shows any returned
message in red on the window itself, and Stitch stays enabled.

Adding a rule
-------------
Write a function that takes a SafetyContext and returns a message string
(warn) or None (fine), then append it to SAFETY_RULES. Rules should be
pure -- no database access, no Qt -- so they can be tested and copied on
their own. Rules don't depend on the overlap slider, so the dialog
evaluates them once per selected pair.

Rules
-----
rule_overlap_dominant_and_distant  the spec rule (overlap only)
rule_no_lane_skip                  the stitched trajectory must not jump
                                   over a lane where it passes from one
                                   trajectory to the other
"""

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from ..trajectory_utils import TrajectoryData
from .stitching_comp import SeamInfo, TemporalCase

SAFETY_MESSAGE = "The selected Trajectories Should Not Be Selected Together"

# Spec threshold: more than 5 m apart within a 1 s span.
SAFETY_DISTANCE_THRESHOLD_M = 5.0
SAFETY_TIME_WINDOW_S = 1.0

# Lane rule: a stitch may move to an adjacent lane, not across one.
MAX_LANE_CHANGE = 1
LANE_SKIP_MESSAGE = ("Trajectories That Skip A Lane Should Not Be Stitched "
                     "(lane {from_lane} to lane {to_lane})")


@dataclass
class SafetyContext:
    first: TrajectoryData      # chronologically first
    second: TrajectoryData
    seam: SeamInfo


SafetyRule = Callable[[SafetyContext], Optional[str]]


# ── Spec rule ──────────────────────────────────────────────────────────────

def closest_pair_distance(a: TrajectoryData, b: TrajectoryData,
                          time_window: float = SAFETY_TIME_WINDOW_S) -> Optional[float]:
    """Smallest |distance difference| over all point pairs (one from each
    trajectory) whose timestamps are within *time_window* seconds.
    None if no such pair exists."""
    ok_a = np.isfinite(a.time) & np.isfinite(a.dist)
    ok_b = np.isfinite(b.time) & np.isfinite(b.dist)
    ta, da = a.time[ok_a], a.dist[ok_a]
    order = np.argsort(b.time[ok_b], kind="stable")
    tb, db = b.time[ok_b][order], b.dist[ok_b][order]
    if ta.size == 0 or tb.size == 0:
        return None

    lo = np.searchsorted(tb, ta - time_window, side="left")
    hi = np.searchsorted(tb, ta + time_window, side="right")
    best = np.inf
    for i in np.nonzero(hi > lo)[0]:
        best = min(best, float(np.min(np.abs(db[lo[i]:hi[i]] - da[i]))))
    return None if not np.isfinite(best) else best


def rule_overlap_dominant_and_distant(
        ctx: SafetyContext,
        dist_threshold: float = SAFETY_DISTANCE_THRESHOLD_M,
        time_window: float = SAFETY_TIME_WINDOW_S) -> Optional[str]:
    """Spec rule. Applies to overlapping pairs only. Warns when BOTH
    trajectories have more points inside the overlap region than outside
    it, AND the closest pair of points (one from each trajectory, within
    time_window seconds of each other) is more than dist_threshold apart.

    If no pair lies within time_window, closeness can't be confirmed, so
    the rule treats the threshold as exceeded and warns.
    """
    seam = ctx.seam
    if seam.case is not TemporalCase.OVERLAP:
        return None
    if not (seam.n_overlap_first > seam.n_nonoverlap_first
            and seam.n_overlap_second > seam.n_nonoverlap_second):
        return None
    gap = closest_pair_distance(ctx.first, ctx.second, time_window)
    if gap is None or gap > dist_threshold:
        return SAFETY_MESSAGE
    return None


# ── Lane rule ──────────────────────────────────────────────────────────────

def _lane_number(value) -> Optional[int]:
    """Integer lane number, or None for a missing / non-integer lane_id."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f != int(f):
        return None
    return int(f)


def _lane_at_or_before(tr: TrajectoryData, t: float) -> Optional[int]:
    """Lane of *tr*'s last sample at or before time *t* (its first sample
    if all are later)."""
    if tr.n == 0:
        return None
    i = int(np.searchsorted(tr.time, t, side="right")) - 1
    return _lane_number(tr.lane_id[max(i, 0)])


def _lane_after(tr: TrajectoryData, t: float) -> Optional[int]:
    """Lane of *tr*'s first sample strictly after time *t*, or None."""
    i = int(np.searchsorted(tr.time, t, side="right"))
    return _lane_number(tr.lane_id[i]) if i < tr.n else None


def junction_lanes(ctx: SafetyContext) -> List[Tuple[int, int]]:
    """(from_lane, to_lane) at each point where the stitched trajectory
    passes from one trajectory to the other.

    The junction is taken where Traj 2 begins: Traj 1's lane at its last
    sample at or before Traj 2's first sample (with a gap, simply Traj 1's
    last sample), against Traj 2's first lane. For an overlap the exact
    switch depends on the split slider; using Traj 2's start keeps the
    rule independent of it. When Traj 2 lies inside Traj 1's span, the
    stitch returns to Traj 1 after Traj 2 ends, which is checked too.
    Junctions with a missing lane are skipped.
    """
    first, second = ctx.first, ctx.second
    if first.n == 0 or second.n == 0:
        return []
    pairs = [(_lane_at_or_before(first, second.start_time),
              _lane_number(second.lane_id[0]))]
    if ctx.seam.contained:
        pairs.append((_lane_number(second.lane_id[-1]),
                      _lane_after(first, second.end_time)))
    return [(a, b) for a, b in pairs if a is not None and b is not None]


def rule_no_lane_skip(ctx: SafetyContext,
                      max_change: int = MAX_LANE_CHANGE) -> Optional[str]:
    """Warns when the stitched trajectory would jump more than
    *max_change* lanes at a junction: lane 1 may be stitched to lane 2,
    but not to lane 3. Applies to every temporal case.

    Assumes lane_id numbers lanes consecutively across the roadway (the
    import's standardized lane_id; orig_lane_id keeps the source labels).
    """
    for from_lane, to_lane in junction_lanes(ctx):
        if abs(to_lane - from_lane) > max_change:
            return LANE_SKIP_MESSAGE.format(from_lane=from_lane, to_lane=to_lane)
    return None


SAFETY_RULES: List[SafetyRule] = [
    rule_overlap_dominant_and_distant,
    rule_no_lane_skip,
]


def evaluate_safety(ctx: SafetyContext,
                    rules: Optional[Sequence[SafetyRule]] = None) -> List[str]:
    """Run every rule; returns distinct messages in rule order."""
    messages: List[str] = []
    for rule in (SAFETY_RULES if rules is None else rules):
        msg = rule(ctx)
        if msg and msg not in messages:
            messages.append(msg)
    return messages
