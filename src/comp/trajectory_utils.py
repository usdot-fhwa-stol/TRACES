"""Shared, Qt-free trajectory helpers used by more than one module.

generate_complete_timestamps moved here from comp/spline/spline_comp.py
(still re-exported there, so existing imports keep working).

Two notions of "time step" coexist deliberately:
    - generate_complete_timestamps infers the step from the SMALLEST
      positive gap within one trajectory (completing a timing grid).
    - estimate_time_step / event_time_step use the MODE across many
      trajectories -- the "expected dataset sampling interval" that
      Missing Data analysis and Trajectory Stitching compare against.
"""

import sqlite3
import math
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from utils import (
    events_columns, active_rows_clause, measured_rows_clause, IMPUTED_STATUS_COL,
)

DEFAULT_TIME_STEP = 0.1   # seconds; fallback when no step can be inferred


# ── Timestamp grid ──────────────────────────────────────────────────────────

def generate_complete_timestamps(time_array: np.ndarray,
                                 time_step: Optional[float] = None) -> np.ndarray:
    """Return *time_array*'s timestamps plus synthetic ones filling each gap.

    The expected step is the trajectory's most common inter-sample interval
    (estimate_time_step), unless *time_step* is given. A gap is any
    consecutive pair more than 1.5 steps apart -- the same threshold
    Trajectory Stitching uses for "continuous". Each gap is filled at
    start + k*step (k = 1, 2, ...) while the point lies more than half a
    step before the gap's end, so filled points continue the timing phase
    of the samples before the gap.

    Phase-independent: a trajectory whose samples shift phase partway
    (e.g. one stitched from segments on offset clocks) has no false gaps.
    The previous implementation inferred the step from the SMALLEST
    interval and filled one global grid, so a single 0.05 s interval in
    0.1 s data produced a synthetic point between every pair of samples.

    Every original timestamp is returned unchanged; synthetic points always
    fall strictly inside a gap, never on or next to an original.
    """
    t = np.unique(np.asarray(time_array, dtype=np.float64))
    if t.size < 2:
        return t

    step = float(time_step) if time_step else estimate_time_step([t])
    if not step > 0:
        return t

    gap_idx = np.nonzero(np.diff(t) > 1.5 * step)[0]
    if gap_idx.size == 0:
        return t

    fills = []
    for i in gap_idx:
        start, end = t[i], t[i + 1]
        # count of k >= 1 with start + k*step < end - 0.5*step
        n = int(math.ceil((end - start) / step - 0.5 - 1e-6)) - 1
        if n > 0:
            fills.append(start + step * np.arange(1, n + 1))

    return np.sort(np.concatenate([t, *fills])) if fills else t

# ── Sampling interval ──────────────────────────────────────────────────────

def _step_mode(diffs: np.ndarray, decimals: int, default: float) -> float:
    """Most frequent positive, finite inter-sample difference. Zero diffs
    (duplicate timestamps) are excluded -- a zero step would otherwise
    make every downstream round(dt / step) divide by zero."""
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return default
    # Round before counting to absorb float jitter (0.1000001 vs 0.0999999).
    vals, counts = np.unique(np.round(diffs, decimals), return_counts=True)
    return float(vals[np.argmax(counts)])


def estimate_time_step(time_arrays: Iterable[np.ndarray],
                       decimals: int = 3,
                       default: float = DEFAULT_TIME_STEP) -> float:
    """Mode of consecutive time differences across several trajectories.
    Each array must be one trajectory's times in ascending order."""
    diffs = [np.diff(np.asarray(t, dtype=np.float64))
             for t in time_arrays if len(t) > 1]
    if not diffs:
        return default
    return _step_mode(np.concatenate(diffs), decimals, default)


def event_time_step(conn: sqlite3.Connection, event_id: int,
                    decimals: int = 3,
                    default: float = DEFAULT_TIME_STEP) -> float:
    """Dataset sampling interval for one event: the mode of consecutive
    time differences within each active trajectory, using measured rows
    only (SplineComp synthetic rows excluded)."""
    cols = events_columns(conn)
    rows = conn.execute(
        f"SELECT veh_id, veh_time FROM events WHERE event_id = ?"
        f"{active_rows_clause(cols)}{measured_rows_clause(cols)} "
        f"ORDER BY veh_id, veh_time",
        (event_id,),
    ).fetchall()
    if len(rows) < 2:
        return default
    arr = np.asarray(rows, dtype=np.float64)
    same_vehicle = np.diff(arr[:, 0]) == 0
    return _step_mode(np.diff(arr[:, 1])[same_vehicle], decimals, default)


# ── Single-trajectory loading ──────────────────────────────────────────────

@dataclass
class TrajectoryData:
    """One trajectory's kinematic arrays, ordered by time.

    speed / accel are None when the column is absent from the dataset
    (distinct from present-but-all-NaN). lane_id and imputed_status are
    object arrays because they may hold None.
    """
    event_id: int
    veh_id: int
    time: np.ndarray
    dist: np.ndarray
    speed: Optional[np.ndarray]
    accel: Optional[np.ndarray]
    lane_id: np.ndarray
    imputed_status: np.ndarray

    @property
    def n(self) -> int:
        return int(self.time.size)

    @property
    def start_time(self) -> float:
        return float(self.time[0]) if self.n else float("nan")

    @property
    def end_time(self) -> float:
        return float(self.time[-1]) if self.n else float("nan")

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.n else float("nan")


def load_trajectory(conn: sqlite3.Connection, event_id: int, veh_id: int,
                    include_synthetic: bool = False) -> TrajectoryData:
    """Load one trajectory. SplineComp synthetic rows (no measurement)
    are excluded unless include_synthetic is True.

    Deliberately NOT filtered by traj_status: callers ask for a specific
    veh_id and decide themselves whether a removed one is acceptable.
    """
    cols = events_columns(conn)
    has_speed = "veh_speed" in cols
    has_accel = "veh_accel" in cols
    has_status = IMPUTED_STATUS_COL in cols

    select = [
        "veh_time", "veh_dist_trav", "lane_id",
        "veh_speed" if has_speed else "NULL",
        "veh_accel" if has_accel else "NULL",
        IMPUTED_STATUS_COL if has_status else "NULL",
    ]
    where = "" if include_synthetic else measured_rows_clause(cols)
    rows = conn.execute(
        f"SELECT {', '.join(select)} FROM events "
        f"WHERE event_id = ? AND veh_id = ?{where} ORDER BY veh_time",
        (event_id, veh_id),
    ).fetchall()

    def num(i: int) -> np.ndarray:
        return np.array([np.nan if r[i] is None else r[i] for r in rows],
                        dtype=np.float64)

    return TrajectoryData(
        event_id=event_id,
        veh_id=veh_id,
        time=num(0),
        dist=num(1),
        speed=num(3) if has_speed else None,
        accel=num(4) if has_accel else None,
        lane_id=np.array([r[2] for r in rows], dtype=object),
        imputed_status=np.array([r[5] for r in rows], dtype=object),
    )