"""Safety rules for Manual Trajectory Stitching (Task 9.2, "Safety Check").

Rules WARN, they never block: the preview dialog shows any returned
message in red on the window itself, and Apply stays enabled.

Adding a rule
-------------
Write a function that takes a SafetyContext and returns a message string
(warn) or None (fine), then append it to SAFETY_RULES. Rules should be
pure -- no database access, no Qt -- so they can be tested and copied on
their own. Rules don't depend on the overlap slider, so the dialog
evaluates them once per selected pair.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

import numpy as np

from comp.trajectory_utils import TrajectoryData
from .stitching_comp import SeamInfo, TemporalCase

SAFETY_MESSAGE = "The selected Trajectories Should Not Be Selected Together"

# Spec threshold: more than 5 m apart within a 1 s span.
SAFETY_DISTANCE_THRESHOLD_M = 5.0
SAFETY_TIME_WINDOW_S = 1.0


@dataclass
class SafetyContext:
    first: TrajectoryData      # chronologically first
    second: TrajectoryData
    seam: SeamInfo


SafetyRule = Callable[[SafetyContext], Optional[str]]


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


SAFETY_RULES: List[SafetyRule] = [
    rule_overlap_dominant_and_distant,
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