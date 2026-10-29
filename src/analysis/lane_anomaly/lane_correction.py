"""Shared, Qt-free reads and writes for Lane-Level Anomaly fixes, and
their Undo.

Every fix is a set of lane reassignments (LaneMove): for each vehicle it
moves, the samples in a time segment get a new lane_id; position, speed
and acceleration are never changed. Every fix goes through
apply_lane_reassignments(), so every fix is undoable the same way
(undo_correction()):

    1. the rows to change must be active, measured rows -- IMPUTED_ORIGINAL
       only (see "What a fix may touch" below)
    2. their current lane and status are saved to superseded_points under
       OWNER_LANE_ANOMALY (analysis.base_comp.supersede_rows)
    3. the rows get the new lane_id and IMPUTED_LANE
    4. one lane_corrections row per vehicle moved records the fix, the
       anomaly becomes FIXED, and ranges.summary_json is refreshed (a
       lane's membership changed)

all in one transaction, for every vehicle of the fix together. Undo
restores the saved lanes and statuses of all of them, marks their
corrections undone and the anomaly DETECTED again -- also in one
transaction.

Because kinematics are untouched, spline fits and internal consistency
results computed before a fix stay valid; nothing is cleared.

What a fix may touch
--------------------
Only IMPUTED_ORIGINAL rows. A gap fill (IMPUTED_AUTO / IMPUTED_MANUAL) is
owned by Gap Interpolation, which would delete or overwrite it on its next
run; a row already corrected by another fix in effect would make undo
order ambiguous. In both cases the fix is refused with a message naming
what to undo first. SplineComp placeholder rows are not trajectory data
and are left as they are.
"""

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from utils import (
    events_columns, active_rows_clause, measured_rows_clause, status_codes,
    imputed_label, IMPUTED_ORIGINAL, IMPUTED_LANE, IMPUTED_STATUS_COL,
    OWNER_GAP, OWNER_LANE_ANOMALY,
    TRAJ_STATUS_COL, TRAJ_REMOVED,
)
from ..base_comp import restore_superseded, supersede_rows
from .lane_anomaly_store import (
    ANOMALY_DETECTED, ANOMALY_FIXED, CorrectionRecord, LaneAnomaly,
    active_corrections, corrections_in_span, insert_correction,
    mark_correction_undone, set_anomaly_status,
)

logger = logging.getLogger(__name__)

LANE_CORRECTION_CODES = status_codes(owner=OWNER_LANE_ANOMALY)

# lane_corrections.fix_type of the built-in fix. A new anomaly type may
# pass its own fix_type to apply_lane_reassignment to label its records.
FIX_LANE_REASSIGN = "lane_reassign"
_GAP_CODES = status_codes(owner=OWNER_GAP)

# Margin around a correction's [start, end] when restoring it --
# restore_superseded takes an open interval.
_TIME_EPS = 1e-6


# ── Reading ─────────────────────────────────────────────────────────────────

def vehicle_rows(conn: sqlite3.Connection, event_id: int, veh_id: int,
                 start_time: Optional[float] = None,
                 end_time: Optional[float] = None) -> Dict[str, np.ndarray]:
    """One vehicle's trajectory rows, placeholders excluded, ordered by
    time, optionally limited to [start_time, end_time]. Keys: time, dist
    (float), lane (object; None where NULL), status (object). Read-only."""
    cols = events_columns(conn)
    status = IMPUTED_STATUS_COL if IMPUTED_STATUS_COL in cols else f"'{IMPUTED_ORIGINAL}'"
    where, params = "event_id = ? AND veh_id = ?", [event_id, veh_id]
    if start_time is not None:
        where += " AND veh_time >= ?"
        params.append(float(start_time))
    if end_time is not None:
        where += " AND veh_time <= ?"
        params.append(float(end_time))
    rows = conn.execute(
        f"SELECT veh_time, veh_dist_trav, lane_id, {status} FROM events WHERE {where}"
        f"{measured_rows_clause(cols)} ORDER BY veh_time", params).fetchall()
    return {
        "time": np.array([r[0] for r in rows], dtype=np.float64),
        "dist": np.array([np.nan if r[1] is None else r[1] for r in rows], dtype=np.float64),
        "lane": np.array([r[2] for r in rows], dtype=object),
        "status": np.array([r[3] for r in rows], dtype=object),
    }


def lane_traffic(conn: sqlite3.Connection, event_id: int, lanes: Iterable[int],
                 start_time: float, end_time: float,
                 exclude: Iterable[int] = ()) -> Dict[int, Dict[str, np.ndarray]]:
    """Every active trajectory sample in *lanes* within [start, end], per
    vehicle, except vehicles in *exclude*: {veh_id: {"time", "dist",
    "lane"}}. The surrounding traffic drawn behind an anomaly. Read-only."""
    lanes = [int(l) for l in lanes]
    if not lanes:
        return {}
    skip = {int(v) for v in exclude}
    cols = events_columns(conn)
    marks = ",".join("?" for _ in lanes)
    rows = conn.execute(
        f"SELECT veh_id, veh_time, veh_dist_trav, lane_id FROM events "
        f"WHERE event_id = ? AND lane_id IN ({marks}) AND veh_time BETWEEN ? AND ? "
        f"AND veh_dist_trav IS NOT NULL{active_rows_clause(cols)}{measured_rows_clause(cols)} "
        f"ORDER BY veh_id, veh_time",
        (event_id, *lanes, float(start_time), float(end_time))).fetchall()
    out: Dict[int, Dict[str, list]] = defaultdict(lambda: {"time": [], "dist": [], "lane": []})
    for v, t, d, l in rows:
        if v in skip:
            continue
        out[v]["time"].append(t)
        out[v]["dist"].append(d)
        out[v]["lane"].append(l)
    return {v: {"time": np.asarray(x["time"], dtype=np.float64),
                "dist": np.asarray(x["dist"], dtype=np.float64),
                "lane": np.asarray(x["lane"], dtype=object)}
            for v, x in out.items()}


def event_lanes(conn: sqlite3.Connection, event_id: int) -> List[int]:
    """Distinct lane_id values of the event's active trajectories."""
    cols = events_columns(conn)
    rows = conn.execute(
        f"SELECT DISTINCT lane_id FROM events WHERE event_id = ? AND lane_id IS NOT NULL"
        f"{active_rows_clause(cols)} ORDER BY lane_id", (event_id,)).fetchall()
    return [r[0] for r in rows]


def neighbor_lanes(conn: sqlite3.Connection, event_id: int, lane_id: int) -> List[int]:
    """lane_id - 1 and lane_id + 1 where the event has them. Assumes the
    import's standardized lane_id numbers lanes consecutively across the
    roadway (same assumption as manual_stitching.safety_rules)."""
    lanes = set(event_lanes(conn, event_id))
    return [l for l in (lane_id - 1, lane_id + 1) if l in lanes]


def is_vehicle_active(conn: sqlite3.Connection, event_id: int, veh_id: int) -> bool:
    status = (f" AND {TRAJ_STATUS_COL} <> '{TRAJ_REMOVED}'"
              if TRAJ_STATUS_COL in events_columns(conn) else "")
    return conn.execute(
        f"SELECT 1 FROM events WHERE event_id = ? AND veh_id = ?{status} LIMIT 1",
        (event_id, veh_id)).fetchone() is not None


# ── Validation ──────────────────────────────────────────────────────────────

def check_correctable(conn: sqlite3.Connection, event_id: int, veh_id: int,
                      times: Sequence[float]) -> None:
    """Raise ValueError (user-facing message) unless every timestamp in
    *times* is an active, IMPUTED_ORIGINAL row of *veh_id* (module
    docstring, "What a fix may touch")."""
    if not len(times):
        raise ValueError("The selected segment has no samples to correct.")
    if not is_vehicle_active(conn, event_id, veh_id):
        raise ValueError(f"Vehicle {veh_id} is no longer an active trajectory "
                         f"(it was used in a stitch).")
    t0, t1 = float(min(times)), float(max(times))
    rows = vehicle_rows(conn, event_id, veh_id, t0, t1)
    by_time = dict(zip(rows["time"].tolist(), rows["status"].tolist()))
    missing = [t for t in times if float(t) not in by_time]
    if missing:
        raise ValueError(f"Vehicle {veh_id} has no sample at {missing[0]:.3f} s; "
                         f"the data may have changed since this fix was prepared.")

    statuses = {by_time[float(t)] for t in times}
    if statuses & _GAP_CODES:
        raise ValueError(
            f"Part of this segment of vehicle {veh_id} is gap-filled. Remove or "
            f"delete those gap fills in Data > Gap Interpolation first, then fix "
            f"the anomaly.")
    if statuses & LANE_CORRECTION_CODES:
        overlapping = corrections_in_span(conn, event_id, veh_id, t0, t1)
        ids = ", ".join(f"#{c.anomaly_id}" for c in overlapping) or "another anomaly"
        raise ValueError(
            f"Part of this segment of vehicle {veh_id} was already corrected "
            f"(anomaly {ids}). Undo that fix first.")
    other = statuses - {IMPUTED_ORIGINAL}
    if other:
        names = ", ".join(sorted(imputed_label(s) for s in other))
        raise ValueError(f"Part of this segment of vehicle {veh_id} holds "
                         f"{names} rows, which this fix can't change.")


# ── Apply ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LaneMove:
    """One vehicle's part of a fix: its samples in [start_time, end_time]
    that aren't already in *to_lane* move to *to_lane*."""
    veh_id: int
    start_time: float
    end_time: float
    to_lane: int


def move_times(conn: sqlite3.Connection, event_id: int, move: LaneMove) -> np.ndarray:
    """Times of the samples *move* would change. Read-only."""
    rows = vehicle_rows(conn, event_id, move.veh_id, move.start_time, move.end_time)
    keep = np.array([lane != move.to_lane for lane in rows["lane"]], dtype=bool)
    return rows["time"][keep]


def apply_lane_reassignments(conn: sqlite3.Connection, anomaly: LaneAnomaly,
                             moves: Sequence[LaneMove],
                             fix_type: str = FIX_LANE_REASSIGN) -> List[int]:
    """Apply every move of one fix atomically (module docstring): all of
    them or none. At most one move per vehicle. Returns the correction_ids,
    one per move. Raises ValueError (user-facing) or sqlite3.Error; on any
    failure nothing is committed. Requires lane_anomaly_store.ensure_schema
    to have run."""
    from data_import.db_utils import update_event_summary

    if anomaly.anomaly_id is None:
        raise ValueError("apply_lane_reassignments: the anomaly has not been stored")
    if not moves:
        raise ValueError("Choose a lane to move at least one vehicle to.")
    if len({m.veh_id for m in moves}) != len(moves):
        raise ValueError("A fix can move each vehicle only once.")
    if active_corrections(conn, anomaly.anomaly_id):
        raise ValueError(f"Anomaly #{anomaly.anomaly_id} already has a fix in effect. "
                         f"Undo it first.")

    planned = []   # (move, times, from_lanes)
    for m in moves:
        rows = vehicle_rows(conn, anomaly.event_id, m.veh_id, m.start_time, m.end_time)
        if not rows["time"].size:
            raise ValueError(f"Vehicle {m.veh_id} has no samples between "
                             f"{m.start_time:.2f} s and {m.end_time:.2f} s.")
        change = np.array([lane != m.to_lane for lane in rows["lane"]], dtype=bool)
        if not change.any():
            raise ValueError(f"Vehicle {m.veh_id} is already in lane {m.to_lane} "
                             f"for the whole selected segment.")
        times = rows["time"][change].tolist()
        check_correctable(conn, anomaly.event_id, m.veh_id, times)
        planned.append((m, times, {int(l) for l in rows["lane"][change] if l is not None}))

    correction_ids: List[int] = []
    try:
        with conn:
            for m, times, from_lanes in planned:
                keys = [(anomaly.event_id, int(m.veh_id), t) for t in times]
                supersede_rows(conn, OWNER_LANE_ANOMALY, keys)
                cur = conn.executemany(
                    f"UPDATE events SET lane_id = ?, {IMPUTED_STATUS_COL} = ? "
                    f"WHERE event_id = ? AND veh_id = ? AND veh_time = ?",
                    [(int(m.to_lane), IMPUTED_LANE, e, v, t) for e, v, t in keys])
                if cur.rowcount != len(times):
                    raise RuntimeError(
                        f"The fix changed {cur.rowcount} rows of vehicle {m.veh_id} but "
                        f"{len(times)} were expected; nothing was saved.")
                correction_ids.append(insert_correction(
                    conn, anomaly, m.veh_id, fix_type, min(times), max(times),
                    from_lanes, int(m.to_lane), len(times)))
            set_anomaly_status(conn, anomaly.anomaly_id, ANOMALY_FIXED)
            update_event_summary(conn, anomaly.event_id)
    except Exception:
        logger.exception("Lane anomaly fix failed; rolled back (anomaly %s)",
                         anomaly.anomaly_id)
        raise

    anomaly.status = ANOMALY_FIXED
    for m, times, _ in planned:
        logger.info("Lane anomaly #%s fixed: veh %s, %d rows to lane %s, %.3f-%.3f s",
                    anomaly.anomaly_id, m.veh_id, len(times), m.to_lane,
                    min(times), max(times))
    return correction_ids


def apply_lane_reassignment(conn: sqlite3.Connection, anomaly: LaneAnomaly,
                            veh_id: int, start_time: float, end_time: float,
                            to_lane: int, fix_type: str = FIX_LANE_REASSIGN) -> int:
    """One-vehicle form of apply_lane_reassignments. Returns the
    correction_id."""
    return apply_lane_reassignments(
        conn, anomaly, [LaneMove(int(veh_id), float(start_time), float(end_time),
                                 int(to_lane))], fix_type)[0]


def describe_corrections(conn: sqlite3.Connection, anomaly: LaneAnomaly) -> str:
    """User-facing text of the fix in effect for *anomaly*, one line per
    vehicle moved; "" when there is none. Read-only."""
    lines = []
    for c in active_corrections(conn, anomaly.anomaly_id) if anomaly.anomaly_id else []:
        source = ", ".join(str(l) for l in c.from_lanes) or "?"
        lines.append(f"Vehicle {c.veh_id}: {c.rows_changed} sample(s) from "
                     f"{c.start_time:.2f} s to {c.end_time:.2f} s moved from lane "
                     f"{source} to lane {c.to_lane}.")
    if lines:
        applied = active_corrections(conn, anomaly.anomaly_id)[0].applied_at.replace("T", " ")
        lines.insert(0, f"Fix applied {applied}:")
    return "\n".join(lines)


# ── Undo ────────────────────────────────────────────────────────────────────

def undo_correction(conn: sqlite3.Connection, anomaly: LaneAnomaly) -> List[CorrectionRecord]:
    """Reverse the fix in effect for *anomaly* atomically -- every vehicle
    it moved: restore the saved lanes and statuses, mark the corrections
    undone and the anomaly DETECTED, refresh ranges.summary_json. Raises
    ValueError (user-facing) if it can't be undone cleanly; on any failure
    nothing is committed. Returns the corrections undone."""
    from data_import.db_utils import update_event_summary

    records = active_corrections(conn, anomaly.anomaly_id) if anomaly.anomaly_id else []
    if not records:
        raise ValueError(f"Anomaly #{anomaly.anomaly_id} has no fix in effect.")
    for rec in records:
        if not is_vehicle_active(conn, rec.event_id, rec.veh_id):
            raise ValueError(f"Vehicle {rec.veh_id} was used in a stitch after this fix. "
                             f"Undo the stitch first.")

    try:
        with conn:
            for rec in records:
                restored = restore_superseded(
                    conn, OWNER_LANE_ANOMALY, rec.event_id, rec.veh_id,
                    rec.start_time - _TIME_EPS, rec.end_time + _TIME_EPS)
                if restored != rec.rows_changed:
                    raise ValueError(
                        f"Only {restored} of the {rec.rows_changed} rows this fix changed "
                        f"on vehicle {rec.veh_id} could be restored -- another module "
                        f"(e.g. Gap Interpolation) has changed the others since. Undo "
                        f"that change first.")
                mark_correction_undone(conn, rec.correction_id)
            set_anomaly_status(conn, anomaly.anomaly_id, ANOMALY_DETECTED)
            update_event_summary(conn, anomaly.event_id)
    except ValueError:
        raise
    except Exception:
        logger.exception("Undo of lane anomaly #%s failed; rolled back", anomaly.anomaly_id)
        raise

    anomaly.status = ANOMALY_DETECTED
    logger.info("Lane anomaly #%s fix undone: %d vehicle(s) restored",
                anomaly.anomaly_id, len(records))
    return records
