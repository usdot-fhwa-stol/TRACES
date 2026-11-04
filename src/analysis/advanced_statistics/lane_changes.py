"""Lane-change detection and lane-change gaps for Advanced Statistics
(Task 9.4). Qt-free and read-only.

A valid lane change
-------------------
A trajectory's samples are split into lane runs (consecutive samples in
one lane; lane-corrected rows count with their corrected lane). A run is
*held* when the vehicle stays in that lane at least *min_dwell_s* --
from the run's first sample to the first sample of the next run -- or
when it is the trajectory's first run (its start is cut by the
observation, not by a lane change). A trajectory's last run is held only
if it lasts min_dwell_s before the data ends.

A valid lane change is a move from one held run to the next held run in
a different lane. Runs too short to be held in between -- a brief flip
and back -- are ignored, so a vehicle flickering between two lanes and
settling where it started has made no lane change.

Gaps around a lane change (center to center)
--------------------------------------------
    before  the last sample before the first sample of the new run (the
            grid tick at or before it)
    after   the first sample of the new run (the grid tick at or after it)

    accepted gap   at *before*, the space in the target lane between the
                   nearest vehicle ahead of the subject's position and the
                   nearest behind it (leader position - follower position)
    front gap      at *after*, the distance to the vehicle's leader in
                   its new lane
    rear gap       at *after*, the distance to its follower there

A gap with no vehicle on one side, or no grid tick at that moment (the
sample falls in a data gap), is NaN and left out of the statistics.
"""

from dataclasses import dataclass
from typing import List

import numpy as np

from .leader_follower import LeaderFollowerIndex, Trajectory

DEFAULT_MIN_DWELL_S = 1.0


@dataclass
class LaneChange:
    veh_id: int
    time: float                  # first sample in the new lane
    from_lane: int
    to_lane: int
    accepted_gap: float = np.nan
    front_gap: float = np.nan
    rear_gap: float = np.nan


def lane_runs(lanes: np.ndarray) -> List[tuple]:
    """(start, end) index pairs, end exclusive, of consecutive equal lanes."""
    if lanes.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(lanes)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [lanes.size]))
    return list(zip(starts.tolist(), ends.tolist()))


def detect_lane_changes(traj: Trajectory,
                        min_dwell_s: float = DEFAULT_MIN_DWELL_S) -> List[LaneChange]:
    """Valid lane changes of one trajectory (module docstring), without
    gaps."""
    runs = lane_runs(traj.lane)
    if len(runs) < 2:
        return []
    t = traj.time
    held = []
    for i, (s, e) in enumerate(runs):
        if i == 0:
            held.append(True)
            continue
        until = t[runs[i + 1][0]] if i + 1 < len(runs) else t[e - 1]
        held.append(until - t[s] >= min_dwell_s - 1e-9)

    changes: List[LaneChange] = []
    prev = 0                      # the first run is always held
    for i in range(1, len(runs)):
        if not held[i]:
            continue
        a, b = int(traj.lane[runs[prev][0]]), int(traj.lane[runs[i][0]])
        if a != b:
            changes.append(LaneChange(traj.veh_id, float(t[runs[i][0]]), a, b))
        prev = i
    return changes


def _tick_at(index: LeaderFollowerIndex, veh_id: int, time: float, after: bool):
    """*veh_id*'s series at the grid tick at or after *time* (*after*) or
    at or before it, as (series, i), or None when it has no tick there
    (a data gap). At or after the first new-lane sample, the tick's lane
    is already the new lane; at or before the last old one, still the
    old."""
    series = index.series.get(veh_id)
    if series is None or series.tick.size == 0:
        return None
    x = time / index.step
    k = int(np.ceil(x - 1e-9)) if after else int(np.floor(x + 1e-9))
    i = int(np.searchsorted(series.tick, k))
    if i < series.tick.size and series.tick[i] == k:
        return series, i
    return None


def measure_gaps(change: LaneChange, traj: Trajectory, index: LeaderFollowerIndex) -> LaneChange:
    """Fill in *change*'s accepted, front and rear gaps (module docstring)."""
    first_new = int(np.searchsorted(traj.time, change.time))
    if first_new > 0:
        found = _tick_at(index, traj.veh_id, float(traj.time[first_new - 1]), after=False)
        if found is not None:
            series, i = found
            ahead, behind = index.target_lane_neighbors(
                int(series.tick[i]), change.to_lane, float(series.pos[i]), traj.veh_id)
            if ahead is not None and behind is not None:
                change.accepted_gap = ahead[1] - behind[1]

    found = _tick_at(index, traj.veh_id, change.time, after=True)
    if found is not None:
        series, i = found
        if series.lane[i] == change.to_lane:
            change.front_gap = float(series.leader_gap[i])
            change.rear_gap = float(series.follower_gap[i])
    return change
