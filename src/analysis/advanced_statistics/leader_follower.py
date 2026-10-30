"""Leader / follower identification for Advanced Statistics (Task 9.4).
Qt-free and read-only.

Task 9.4: "Leader-follower relationships should be determined at each
valid timestamp using the lane assignment, direction of travel, and
longitudinal position of all available trajectories." Every vehicle of an
event travels in the same direction (increasing veh_dist_trav), so:

    leader    the nearest vehicle ahead in the same lane at that moment
    follower  the nearest vehicle behind in the same lane at that moment

and a gap is center to center: the difference in veh_dist_trav.

Time alignment
--------------
Trajectories are sampled on (roughly) the event's common time grid, but
clocks may be offset by a fraction of a step. Each trajectory is
therefore resampled onto the grid ticks k * step that fall inside its
gap-free segments (utils.segment_bounds), interpolating its position
linearly -- the same "interpolate within gap-free segments" rule Gap
Headway mode uses. A tick's lane is the lane of the last sample at or
before it. A trajectory has no tick inside a data gap, so it is neither
a leader nor a follower there.

At each tick, vehicles are grouped by lane and sorted by position; each
vehicle's leader is the next one up the sorted group and its follower the
next one down. Equal positions are ordered by vehicle ID.

The index is built from every active trajectory in the event, whatever
subset is later reported (Task 9.4 N.B.). It can also answer "who is
ahead of / behind position x in lane L at tick k" for a vehicle not in
that lane (target_lane_neighbors), which the lane-change gaps use.
"""

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from utils import (
    active_rows_clause, events_columns, event_time_step, measured_rows_clause,
    segment_bounds,
)

NO_VEHICLE = -1            # leader_id / follower_id where there is none


@dataclass
class Trajectory:
    """One vehicle's measured samples (placeholders excluded), by time."""
    veh_id: int
    time: np.ndarray
    dist: np.ndarray
    lane: np.ndarray        # int64; samples with a NULL lane are dropped
    speed: Optional[np.ndarray]   # None when events has no veh_speed


@dataclass
class TickSeries:
    """One vehicle on the event's time grid (module docstring)."""
    veh_id: int
    tick: np.ndarray          # int64 grid indices, ascending
    pos: np.ndarray           # interpolated position at each tick
    lane: np.ndarray          # int64
    leader_id: np.ndarray     # int64, NO_VEHICLE where none
    leader_gap: np.ndarray    # float, NaN where none
    follower_id: np.ndarray
    follower_gap: np.ndarray

    def times(self, step: float) -> np.ndarray:
        return self.tick * step


def load_trajectories(conn: sqlite3.Connection, event_id: int) -> Dict[int, Trajectory]:
    """Every active trajectory of the event: measured rows (gap fills and
    lane-corrected rows included -- they are the corrected data), without
    SplineComp placeholder rows or rows missing a position or lane."""
    cols = events_columns(conn)
    has_speed = "veh_speed" in cols
    speed = "veh_speed" if has_speed else "NULL"
    rows = conn.execute(
        f"SELECT veh_id, veh_time, veh_dist_trav, lane_id, {speed} FROM events "
        f"WHERE event_id = ? AND veh_dist_trav IS NOT NULL AND lane_id IS NOT NULL"
        f"{active_rows_clause(cols)}{measured_rows_clause(cols)} "
        f"ORDER BY veh_id, veh_time", (event_id,)).fetchall()
    out: Dict[int, Trajectory] = {}
    start = 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or rows[i][0] != rows[start][0]:
            chunk = rows[start:i]
            veh = int(chunk[0][0])
            out[veh] = Trajectory(
                veh_id=veh,
                time=np.array([r[1] for r in chunk], dtype=np.float64),
                dist=np.array([r[2] for r in chunk], dtype=np.float64),
                lane=np.array([int(r[3]) for r in chunk], dtype=np.int64),
                speed=(np.array([np.nan if r[4] is None else r[4] for r in chunk],
                                dtype=np.float64) if has_speed else None))
            start = i
    return out


def resample(traj: Trajectory, step: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ticks, positions, lanes) of *traj* on the grid (module docstring)."""
    ticks, pos, lanes = [], [], []
    for s, e in segment_bounds(traj.time, step):
        t, d, l = traj.time[s:e], traj.dist[s:e], traj.lane[s:e]
        k0 = int(np.ceil(t[0] / step - 1e-9))
        k1 = int(np.floor(t[-1] / step + 1e-9))
        if k1 < k0:
            continue
        k = np.arange(k0, k1 + 1, dtype=np.int64)
        tk = k * step
        ticks.append(k)
        pos.append(np.interp(tk, t, d))
        idx = np.clip(np.searchsorted(t, tk + 1e-9, side="right") - 1, 0, t.size - 1)
        lanes.append(l[idx])
    if not ticks:
        empty = np.array([], dtype=np.int64)
        return empty, np.array([], dtype=np.float64), empty
    return np.concatenate(ticks), np.concatenate(pos), np.concatenate(lanes)


class LeaderFollowerIndex:
    """Leaders and followers of every vehicle of one event at every tick
    (module docstring)."""

    def __init__(self, trajectories: Dict[int, Trajectory], step: float,
                 max_gap: Optional[float] = None):
        """*max_gap* (m): a vehicle further than this ahead / behind is not
        a leader / follower. None or <= 0 = no limit."""
        self.step = float(step)
        self.max_gap = float(max_gap) if max_gap and max_gap > 0 else None
        self.trajectories = trajectories

        parts = [(veh, *resample(tr, self.step)) for veh, tr in trajectories.items()]
        n = sum(p[1].size for p in parts)
        veh = np.concatenate([np.full(p[1].size, p[0], dtype=np.int64) for p in parts]) \
            if n else np.array([], dtype=np.int64)
        tick = np.concatenate([p[1] for p in parts]) if n else np.array([], dtype=np.int64)
        pos = np.concatenate([p[2] for p in parts]) if n else np.array([], dtype=np.float64)
        lane = np.concatenate([p[3] for p in parts]) if n else np.array([], dtype=np.int64)

        # Sort by (tick, lane, position, vehicle): neighbors in a group are
        # adjacent.
        order = np.lexsort((veh, pos, lane, tick))
        self._veh, self._tick = veh[order], tick[order]
        self._pos, self._lane = pos[order], lane[order]

        same = np.zeros(max(n - 1, 0), dtype=bool)
        if n > 1:
            same = (self._tick[1:] == self._tick[:-1]) & (self._lane[1:] == self._lane[:-1])
        gap = self._pos[1:] - self._pos[:-1] if n > 1 else np.array([])
        if self.max_gap is not None:
            same = same & (gap <= self.max_gap)

        leader_id = np.full(n, NO_VEHICLE, dtype=np.int64)
        follower_id = np.full(n, NO_VEHICLE, dtype=np.int64)
        leader_gap = np.full(n, np.nan)
        follower_gap = np.full(n, np.nan)
        hit = np.flatnonzero(same)
        leader_id[hit] = self._veh[hit + 1]
        leader_gap[hit] = gap[hit]
        follower_id[hit + 1] = self._veh[hit]
        follower_gap[hit + 1] = gap[hit]

        # Per-vehicle series, ordered by tick.
        self.series: Dict[int, TickSeries] = {}
        by_veh = np.lexsort((self._tick, self._veh))
        bounds = np.flatnonzero(np.diff(self._veh[by_veh])) + 1
        for chunk in np.split(by_veh, bounds) if n else []:
            v = int(self._veh[chunk[0]])
            self.series[v] = TickSeries(
                veh_id=v, tick=self._tick[chunk], pos=self._pos[chunk],
                lane=self._lane[chunk],
                leader_id=leader_id[chunk], leader_gap=leader_gap[chunk],
                follower_id=follower_id[chunk], follower_gap=follower_gap[chunk])

        # (tick, lane) -> slice of the sorted arrays, for target-lane lookups.
        self._groups: Dict[Tuple[int, int], Tuple[int, int]] = {}
        starts =np.flatnonzero(np.concatenate(
            ([True], (self._tick[1:] != self._tick[:-1]) | (self._lane[1:] != self._lane[:-1])))) \
            if n else np.array([], dtype=np.int64)
        ends = np.concatenate((starts[1:], [n])) if n else starts
        for s, e in zip(starts.tolist(), ends.tolist()):
            self._groups[(int(self._tick[s]), int(self._lane[s]))] = (s, e)

    @classmethod
    def for_event(cls, conn: sqlite3.Connection, event_id: int,
                  max_gap: Optional[float] = None) -> "LeaderFollowerIndex":
        return cls(load_trajectories(conn, event_id), event_time_step(conn, event_id), max_gap)

    def target_lane_neighbors(self, tick: int, lane: int, position: float,
                              exclude: int) -> Tuple[Optional[Tuple[int, float]],
                                                     Optional[Tuple[int, float]]]:
        """((leader_id, leader_pos) | None, (follower_id, follower_pos) | None)
        in *lane* at *tick* around *position*, ignoring vehicle *exclude*:
        the nearest vehicle at or ahead of *position* and the nearest one
        behind it, each within max_gap when one is set."""
        span = self._groups.get((int(tick), int(lane)))
        if span is None:
            return None, None
        s, e = span
        keep = self._veh[s:e] != exclude
        pos, veh = self._pos[s:e][keep], self._veh[s:e][keep]
        i = int(np.searchsorted(pos, position, side="left"))
        ahead = (int(veh[i]), float(pos[i])) if i < pos.size else None
        behind = (int(veh[i - 1]), float(pos[i - 1])) if i > 0 else None
        if self.max_gap is not None:
            if ahead is not None and ahead[1] - position > self.max_gap:
                ahead = None
            if behind is not None and position - behind[1] > self.max_gap:
                behind = None
        return ahead, behind
