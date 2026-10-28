"""Shared, Qt-free writes for Lane-Level Anomaly fixes, and their Undo.

Every fix goes through apply_correction(), so every fix is undoable the
same way (undo_correction()):

    1. the rows to change must be active, measured rows of one vehicle --
       IMPUTED_ORIGINAL only (see "What a fix may touch" below)
    2. their current values and status are saved to superseded_points
       under OWNER_LANE_ANOMALY (analysis.base_comp.supersede_rows)
    3. the rows get their new values and an IMPUTED_LANE / IMPUTED_CORRECTED
       code; for a fix that changes kinematics, DERIVED_EVENTS_COLUMNS are
       set to NULL on those rows (spline fits and consistency errors were
       computed from the old values)
    4. a lane_corrections row records the fix, the anomaly becomes FIXED,
       and ranges.summary_json is refreshed (a lane's membership changed)

all in one transaction. Undo restores the saved values and statuses,
marks the correction undone and the anomaly DETECTED again. NULLed derived
columns are not restored -- rerun Spline Fitting / Internal Consistency.

What a fix may touch
--------------------
Only IMPUTED_ORIGINAL rows. A gap fill (IMPUTED_AUTO / IMPUTED_MANUAL) is
owned by Gap Interpolation, which would delete or overwrite it on its next
run; a row already corrected by another fix in effect would make undo
order ambiguous. In both cases apply_correction refuses with a message
naming what to undo first. SplineComp placeholder rows are not trajectory
data and are left as they are.
"""

import logging
import sqlite3
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from utils import (
    events_columns, active_rows_clause, measured_rows_clause, status_codes,
    imputed_label, IMPUTED_ORIGINAL, IMPUTED_LANE, IMPUTED_STATUS_COL,
    OWNER_GAP, OWNER_LANE_ANOMALY,
    TRAJ_STATUS_COL, TRAJ_REMOVED,
)
from ..base_comp import (
    DERIVED_EVENTS_COLUMNS, restore_superseded, supersede_rows,
)
from .lane_anomaly_store import (
    ANOMALY_DETECTED, ANOMALY_FIXED, CorrectionRecord, LaneAnomaly,
    active_correction, corrections_in_span, insert_correction,
    mark_correction_undone, set_anomaly_status,
)

logger = logging.getLogger(__name__)

LANE_CORRECTION_CODES = status_codes(owner=OWNER_LANE_ANOMALY)

# lane_corrections.fix_type values of the built-in fixes.
FIX_LANE_REASSIGN = "lane_reassign"
FIX_MIDLINE = "midline"
_GAP_CODES = status_codes(owner=OWNER_GAP)

# Margin around a correction's [start, end] when restoring it --
# restore_superseded takes an open interval.
_TIME_EPS = 1e-6


# ── Reading ─────────────────────────────────────────────────────────────────

def vehicle_rows(conn: sqlite3.Connection, event_id: int, veh_id: int,
                 start_time: Optional[float] = None,
                 end_time: Optional[float] = None) -> Dict[str, np.ndarray]:
    """One vehicle's trajectory rows, placeholders excluded, ordered by
    time, optionally limited to [start_time, end_time]. Keys: time, dist,
    speed, accel (NaN where the column is absent), lane (object), status
    (object). Read-only."""
    cols = events_columns(conn)
    select = ["veh_time", "veh_dist_trav",
              "veh_speed" if "veh_speed" in cols else "NULL",
              "veh_accel" if "veh_accel" in cols else "NULL",
              "lane_id",
              IMPUTED_STATUS_COL if IMPUTED_STATUS_COL in cols else f"'{IMPUTED_ORIGINAL}'"]
    where, params = "event_id = ? AND veh_id = ?", [event_id, veh_id]
    if start_time is not None:
        where += " AND veh_time >= ?"
        params.append(float(start_time))
    if end_time is not None:
        where += " AND veh_time <= ?"
        params.append(float(end_time))
    rows = conn.execute(
        f"SELECT {', '.join(select)} FROM events WHERE {where}"
        f"{measured_rows_clause(cols)} ORDER BY veh_time", params).fetchall()

    def num(i):
        return np.array([np.nan if r[i] is None else r[i] for r in rows], dtype=np.float64)

    return {
        "time": num(0), "dist": num(1), "speed": num(2), "accel": num(3),
        "lane": np.array([r[4] for r in rows], dtype=object),
        "status": np.array([r[5] for r in rows], dtype=object),
    }


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

def apply_correction(conn: sqlite3.Connection, anomaly: LaneAnomaly, veh_id: int,
                     fix_type: str, times: Sequence[float], status: str,
                     lanes: Sequence[int],
                     values: Optional[Dict[str, Sequence[float]]] = None,
                     to_lane: Optional[int] = None,
                     weight: Optional[float] = None) -> int:
    """Write one fix atomically (module docstring). *times* are the rows
    of *veh_id* to change, *lanes* their new lane_id (one per time),
    *values* optional {events column: new value per time} for kinematic
    columns that exist in events. Returns the correction_id. Raises
    ValueError (user-facing) or sqlite3.Error; on any failure nothing is
    committed. Requires lane_anomaly_store.ensure_schema to have run."""
    from data_import.db_utils import update_event_summary

    if status not in LANE_CORRECTION_CODES:
        raise ValueError(f"apply_correction: {status!r} is not a lane correction code")
    if anomaly.anomaly_id is None:
        raise ValueError("apply_correction: the anomaly has not been stored")
    if active_correction(conn, anomaly.anomaly_id) is not None:
        raise ValueError(f"Anomaly #{anomaly.anomaly_id} already has a fix in effect. "
                         f"Undo it first.")
    times = [float(t) for t in times]
    lanes = [int(l) for l in lanes]
    if len(lanes) != len(times):
        raise ValueError("apply_correction: one lane per time is required")
    values = dict(values or {})
    for col, vals in values.items():
        if len(vals) != len(times):
            raise ValueError(f"apply_correction: {col} needs one value per time")

    check_correctable(conn, anomaly.event_id, veh_id, times)

    cols = events_columns(conn)
    values = {c: v for c, v in values.items() if c in cols}
    derived = [c for c in DERIVED_EVENTS_COLUMNS if c in cols] if values else []
    before = vehicle_rows(conn, anomaly.event_id, veh_id, min(times), max(times))
    from_lanes = {int(l) for l in before["lane"] if l is not None}

    set_cols = ["lane_id = ?", f"{IMPUTED_STATUS_COL} = ?",
                *[f"{c} = ?" for c in values], *[f"{c} = NULL" for c in derived]]
    params = [
        (lanes[i], status, *[None if not np.isfinite(values[c][i]) else float(values[c][i])
                             for c in values],
         anomaly.event_id, int(veh_id), times[i])
        for i in range(len(times))
    ]
    keys = [(anomaly.event_id, int(veh_id), t) for t in times]

    try:
        with conn:
            supersede_rows(conn, OWNER_LANE_ANOMALY, keys)
            cur = conn.executemany(
                f"UPDATE events SET {', '.join(set_cols)} "
                f"WHERE event_id = ? AND veh_id = ? AND veh_time = ?", params)
            if cur.rowcount != len(times):
                raise RuntimeError(
                    f"The fix changed {cur.rowcount} rows but {len(times)} were "
                    f"expected; nothing was saved.")
            correction_id = insert_correction(
                conn, anomaly, veh_id, fix_type, min(times), max(times),
                from_lanes, to_lane, weight, len(times))
            set_anomaly_status(conn, anomaly.anomaly_id, ANOMALY_FIXED)
            update_event_summary(conn, anomaly.event_id)
    except Exception:
        logger.exception("Lane anomaly fix failed; rolled back (anomaly %s, veh %s)",
                         anomaly.anomaly_id, veh_id)
        raise

    anomaly.status = ANOMALY_FIXED
    logger.info("Lane anomaly #%s fixed (%s): veh %s, %d rows, %.3f-%.3f s",
                anomaly.anomaly_id, fix_type, veh_id, len(times), min(times), max(times))
    return correction_id


def apply_lane_reassignment(conn: sqlite3.Connection, anomaly: LaneAnomaly,
                            veh_id: int, start_time: float, end_time: float,
                            to_lane: int) -> int:
    """Move every sample of *veh_id* in [start_time, end_time] that isn't
    already in *to_lane* to *to_lane* (IMPUTED_LANE; kinematics
    unchanged). Returns the correction_id."""
    rows = vehicle_rows(conn, anomaly.event_id, veh_id, start_time, end_time)
    move = [i for i, lane in enumerate(rows["lane"]) if lane != to_lane]
    if not rows["time"].size:
        raise ValueError(f"Vehicle {veh_id} has no samples between "
                         f"{start_time:.2f} s and {end_time:.2f} s.")
    if not move:
        raise ValueError(f"Vehicle {veh_id} is already in lane {to_lane} "
                         f"for the whole selected segment.")
    return apply_correction(
        conn, anomaly, veh_id, FIX_LANE_REASSIGN,
        times=rows["time"][move].tolist(), status=IMPUTED_LANE,
        lanes=[to_lane] * len(move), to_lane=to_lane)


# ── Undo ────────────────────────────────────────────────────────────────────

def undo_correction(conn: sqlite3.Connection, anomaly: LaneAnomaly) -> CorrectionRecord:
    """Reverse the fix in effect for *anomaly* atomically: restore the
    saved values and statuses, mark the correction undone and the anomaly
    DETECTED, refresh ranges.summary_json. Raises ValueError (user-facing)
    if it can't be undone cleanly; on any failure nothing is committed."""
    from data_import.db_utils import update_event_summary

    rec = active_correction(conn, anomaly.anomaly_id) if anomaly.anomaly_id else None
    if rec is None:
        raise ValueError(f"Anomaly #{anomaly.anomaly_id} has no fix in effect.")
    if not is_vehicle_active(conn, rec.event_id, rec.veh_id):
        raise ValueError(f"Vehicle {rec.veh_id} was used in a stitch after this fix. "
                         f"Undo the stitch first.")

    try:
        with conn:
            restored = restore_superseded(
                conn, OWNER_LANE_ANOMALY, rec.event_id, rec.veh_id,
                rec.start_time - _TIME_EPS, rec.end_time + _TIME_EPS)
            if restored != rec.rows_changed:
                raise ValueError(
                    f"Only {restored} of the {rec.rows_changed} rows this fix changed "
                    f"could be restored -- another module (e.g. Gap Interpolation) has "
                    f"changed the others since. Undo that change first.")
            mark_correction_undone(conn, rec.correction_id)
            set_anomaly_status(conn, anomaly.anomaly_id, ANOMALY_DETECTED)
            update_event_summary(conn, rec.event_id)
    except ValueError:
        raise
    except Exception:
        logger.exception("Undo of lane anomaly fix %s failed; rolled back", rec.correction_id)
        raise

    anomaly.status = ANOMALY_DETECTED
    logger.info("Lane anomaly #%s fix undone: veh %s, %d rows restored",
                anomaly.anomaly_id, rec.veh_id, restored)
    return rec
