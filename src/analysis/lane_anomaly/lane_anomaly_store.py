"""Persistence for Lane-Level Anomaly Detection and Correction (Task 9.3).

Qt-free. Two tables, created on the first write (ensure_schema), never on
a read -- opening the module never modifies the database before the user
has chosen where changes are written:

    lane_anomalies    one row per detected anomaly (the spec's unique
                      anomaly ID is anomaly_id; AUTOINCREMENT, so an ID is
                      never reused, even after a rerun replaces it)
    lane_corrections  one row per vehicle moved by an applied fix (a fix
                      may move several vehicles; its rows share the
                      anomaly_id and are undone together), kept after an
                      Undo (undone_at set) as history, like the stitches
                      table

Together with analysis.base_comp's superseded_points (the lane and status
each corrected row held before the fix) they give the spec's
traceability: original -> detected -> reviewed -> corrected.

Anomaly status (lane_anomalies.status):
    ANOMALY_DETECTED  detected, no fix in effect
    ANOMALY_FIXED     a fix is in effect (its lane_corrections row has
                      undone_at NULL); Undo returns it to DETECTED

Rerun policy (replace_detected): a new detection run for some anomaly
types deletes that event's DETECTED anomalies of those types and inserts
the new ones, except new anomalies overlapping a FIXED anomaly of the
same type on the same vehicle(s) -- the fixed anomaly and its fix win.
FIXED anomalies are always kept.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from utils import events_columns, TRAJ_STATUS_COL, TRAJ_REMOVED
from ..base_comp import (
    ensure_imputed_status_column, ensure_superseded_table, table_exists,
)

ANOMALIES_TABLE = "lane_anomalies"
CORRECTIONS_TABLE = "lane_corrections"

ANOMALY_DETECTED = "D"
ANOMALY_FIXED = "F"

_ANOMALIES_DDL = f"""
CREATE TABLE IF NOT EXISTS {ANOMALIES_TABLE} (
    anomaly_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL,
    anomaly_type  TEXT    NOT NULL,   -- AnomalyType.KEY
    veh_id        INTEGER NOT NULL,
    other_veh_id  INTEGER,            -- second vehicle (Proximity), else NULL
    start_time    REAL    NOT NULL,
    end_time      REAL    NOT NULL,
    lane_ids_json TEXT    NOT NULL,   -- JSON list of lane_id values involved
    params_json   TEXT    NOT NULL,   -- detection parameters used
    metrics_json  TEXT    NOT NULL,   -- type-specific measurements
    status        TEXT    NOT NULL,   -- ANOMALY_DETECTED | ANOMALY_FIXED
    created_at    TEXT    NOT NULL    -- local ISO-8601
)"""

_ANOMALIES_INDEX = (f"CREATE INDEX IF NOT EXISTS {ANOMALIES_TABLE}_event "
                    f"ON {ANOMALIES_TABLE} (event_id, anomaly_type)")

_CORRECTIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {CORRECTIONS_TABLE} (
    correction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_id      INTEGER NOT NULL,
    event_id        INTEGER NOT NULL,
    veh_id          INTEGER NOT NULL,
    fix_type        TEXT    NOT NULL,   -- e.g. 'lane_reassign'
    start_time      REAL    NOT NULL,   -- first corrected sample
    end_time        REAL    NOT NULL,   -- last corrected sample
    from_lanes_json TEXT    NOT NULL,   -- JSON list of the lanes the rows had
    to_lane         INTEGER,
    rows_changed    INTEGER NOT NULL,
    applied_at      TEXT    NOT NULL,
    undone_at       TEXT                -- NULL = in effect
)"""


# ── Value types ─────────────────────────────────────────────────────────────

@dataclass
class LaneAnomaly:
    """One detected anomaly. anomaly_id is None until it is stored."""
    event_id: int
    anomaly_type: str
    veh_id: int
    start_time: float
    end_time: float
    lane_ids: List[int]
    other_veh_id: Optional[int] = None
    params: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    status: str = ANOMALY_DETECTED
    anomaly_id: Optional[int] = None
    created_at: Optional[str] = None

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def veh_ids(self) -> Tuple[int, ...]:
        return (self.veh_id,) if self.other_veh_id is None else (self.veh_id, self.other_veh_id)

    @property
    def is_fixed(self) -> bool:
        return self.status == ANOMALY_FIXED

    def same_subject(self, other: "LaneAnomaly") -> bool:
        """Same type and the same vehicle(s), in either order."""
        return (self.anomaly_type == other.anomaly_type
                and set(self.veh_ids) == set(other.veh_ids))

    def overlaps(self, other: "LaneAnomaly") -> bool:
        """Time spans share any instant (closed intervals)."""
        return self.start_time <= other.end_time and other.start_time <= self.end_time


@dataclass
class CorrectionRecord:
    correction_id: int
    anomaly_id: int
    event_id: int
    veh_id: int
    fix_type: str
    start_time: float
    end_time: float
    from_lanes: List[int]
    to_lane: Optional[int]
    rows_changed: int
    applied_at: str
    undone_at: Optional[str]


@dataclass
class ReplaceSummary:
    """What replace_detected did for one event."""
    inserted: int = 0      # new DETECTED anomalies stored
    replaced: int = 0      # previous DETECTED anomalies deleted
    kept_fixed: int = 0    # FIXED anomalies of the rerun types left in place
    covered: int = 0       # new anomalies dropped for overlapping a FIXED one


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Schema ──────────────────────────────────────────────────────────────────

def lane_tables_exist(conn: sqlite3.Connection) -> bool:
    return table_exists(conn, ANOMALIES_TABLE)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create both tables, the shared imputed_status column and the shared
    superseded_points table, if absent. Commits -- call only after the
    write target has been resolved, before any data transaction."""
    ensure_imputed_status_column(conn)
    ensure_superseded_table(conn)
    conn.execute(_ANOMALIES_DDL)
    conn.execute(_ANOMALIES_INDEX)
    conn.execute(_CORRECTIONS_DDL)
    conn.commit()


# ── Anomalies ───────────────────────────────────────────────────────────────

_ANOMALY_COLS = ("anomaly_id", "event_id", "anomaly_type", "veh_id", "other_veh_id",
                 "start_time", "end_time", "lane_ids_json", "params_json",
                 "metrics_json", "status", "created_at")


def _from_row(row) -> LaneAnomaly:
    (aid, ev, typ, veh, other, t0, t1, lanes, params, metrics, status, created) = row
    return LaneAnomaly(
        anomaly_id=aid, event_id=ev, anomaly_type=typ, veh_id=veh, other_veh_id=other,
        start_time=t0, end_time=t1, lane_ids=list(json.loads(lanes)),
        params=json.loads(params), metrics=json.loads(metrics),
        status=status, created_at=created)


def _exclude_removed(conn: sqlite3.Connection, event_id: int) -> Tuple[str, list]:
    """Hide anomalies on trajectories consumed by a stitch."""
    if TRAJ_STATUS_COL not in events_columns(conn):
        return "", []
    removed = (f"(SELECT DISTINCT veh_id FROM events "
               f"WHERE event_id = ? AND {TRAJ_STATUS_COL} = ?)")
    return (f" AND veh_id NOT IN {removed}"
            f" AND (other_veh_id IS NULL OR other_veh_id NOT IN {removed})",
            [event_id, TRAJ_REMOVED, event_id, TRAJ_REMOVED])


def read_anomalies(conn: sqlite3.Connection, event_id: int,
                   anomaly_type: Optional[str] = None) -> List[LaneAnomaly]:
    """Stored anomalies of one event (optionally one type), chronological,
    excluding those on removed trajectories. Empty when the table doesn't
    exist yet. Read-only."""
    if not lane_tables_exist(conn):
        return []
    where, params = "event_id = ?", [event_id]
    if anomaly_type is not None:
        where += " AND anomaly_type = ?"
        params.append(anomaly_type)
    extra, extra_params = _exclude_removed(conn, event_id)
    rows = conn.execute(
        f"SELECT {', '.join(_ANOMALY_COLS)} FROM {ANOMALIES_TABLE} "
        f"WHERE {where}{extra} ORDER BY anomaly_type, start_time, anomaly_id",
        [*params, *extra_params]).fetchall()
    return [_from_row(r) for r in rows]


def read_anomaly(conn: sqlite3.Connection, anomaly_id: int) -> Optional[LaneAnomaly]:
    if not lane_tables_exist(conn):
        return None
    row = conn.execute(
        f"SELECT {', '.join(_ANOMALY_COLS)} FROM {ANOMALIES_TABLE} WHERE anomaly_id = ?",
        (anomaly_id,)).fetchone()
    return _from_row(row) if row else None


def _insert_anomaly(conn: sqlite3.Connection, a: LaneAnomaly) -> int:
    a.created_at = a.created_at or _now()
    cur = conn.execute(
        f"INSERT INTO {ANOMALIES_TABLE} (event_id, anomaly_type, veh_id, other_veh_id, "
        f"start_time, end_time, lane_ids_json, params_json, metrics_json, status, created_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (a.event_id, a.anomaly_type, int(a.veh_id),
         None if a.other_veh_id is None else int(a.other_veh_id),
         float(a.start_time), float(a.end_time),
         json.dumps([int(v) for v in a.lane_ids]), json.dumps(a.params),
         json.dumps(a.metrics), a.status, a.created_at))
    a.anomaly_id = cur.lastrowid
    return a.anomaly_id


def replace_detected(conn: sqlite3.Connection, event_id: int,
                     anomaly_types: Iterable[str],
                     detected: Sequence[LaneAnomaly]) -> ReplaceSummary:
    """Apply the rerun policy (module docstring) for *anomaly_types* in one
    event: delete their DETECTED anomalies, keep their FIXED ones, store
    *detected* except those overlapping a FIXED anomaly of the same
    subject. Assigns anomaly_id on the stored objects. Does not commit."""
    types = sorted(set(anomaly_types))
    summary = ReplaceSummary()
    if not types:
        return summary
    marks = ",".join("?" for _ in types)

    fixed = [_from_row(r) for r in conn.execute(
        f"SELECT {', '.join(_ANOMALY_COLS)} FROM {ANOMALIES_TABLE} "
        f"WHERE event_id = ? AND anomaly_type IN ({marks}) AND status = ?",
        (event_id, *types, ANOMALY_FIXED)).fetchall()]
    summary.kept_fixed = len(fixed)

    summary.replaced = conn.execute(
        f"DELETE FROM {ANOMALIES_TABLE} "
        f"WHERE event_id = ? AND anomaly_type IN ({marks}) AND status = ?",
        (event_id, *types, ANOMALY_DETECTED)).rowcount

    for a in detected:
        if a.event_id != event_id or a.anomaly_type not in types:
            raise ValueError(f"replace_detected: anomaly {a} is outside event "
                             f"{event_id} / types {types}")
        if any(a.same_subject(f) and a.overlaps(f) for f in fixed):
            summary.covered += 1
            continue
        a.status = ANOMALY_DETECTED
        _insert_anomaly(conn, a)
        summary.inserted += 1
    return summary


def set_anomaly_status(conn: sqlite3.Connection, anomaly_id: int, status: str) -> None:
    """Does not commit."""
    conn.execute(f"UPDATE {ANOMALIES_TABLE} SET status = ? WHERE anomaly_id = ?",
                 (status, anomaly_id))


# ── Corrections ─────────────────────────────────────────────────────────────

_CORRECTION_COLS = ("correction_id", "anomaly_id", "event_id", "veh_id", "fix_type",
                    "start_time", "end_time", "from_lanes_json", "to_lane",
                    "rows_changed", "applied_at", "undone_at")


def _correction_from_row(row) -> CorrectionRecord:
    values = dict(zip(_CORRECTION_COLS, row))
    values["from_lanes"] = json.loads(values.pop("from_lanes_json"))
    return CorrectionRecord(**values)


def insert_correction(conn: sqlite3.Connection, anomaly: LaneAnomaly, veh_id: int,
                      fix_type: str, start_time: float, end_time: float,
                      from_lanes: Iterable[int], to_lane: Optional[int],
                      rows_changed: int) -> int:
    """Record an applied fix. Does not commit."""
    cur = conn.execute(
        f"INSERT INTO {CORRECTIONS_TABLE} (anomaly_id, event_id, veh_id, fix_type, "
        f"start_time, end_time, from_lanes_json, to_lane, rows_changed, applied_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (anomaly.anomaly_id, anomaly.event_id, int(veh_id), fix_type,
         float(start_time), float(end_time),
         json.dumps(sorted({int(v) for v in from_lanes})),
         None if to_lane is None else int(to_lane), int(rows_changed), _now()))
    return cur.lastrowid


def active_corrections(conn: sqlite3.Connection, anomaly_id: int) -> List[CorrectionRecord]:
    """The fix in effect for *anomaly_id*: one record per vehicle it moved
    (a fix may move several), oldest first; empty when none. Read-only."""
    if not table_exists(conn, CORRECTIONS_TABLE):
        return []
    rows = conn.execute(
        f"SELECT {', '.join(_CORRECTION_COLS)} FROM {CORRECTIONS_TABLE} "
        f"WHERE anomaly_id = ? AND undone_at IS NULL "
        f"ORDER BY correction_id", (anomaly_id,)).fetchall()
    return [_correction_from_row(r) for r in rows]


def corrections_in_span(conn: sqlite3.Connection, event_id: int, veh_id: int,
                        start_time: float, end_time: float) -> List[CorrectionRecord]:
    """Corrections in effect on *veh_id* whose span overlaps [start, end]."""
    if not table_exists(conn, CORRECTIONS_TABLE):
        return []
    rows = conn.execute(
        f"SELECT {', '.join(_CORRECTION_COLS)} FROM {CORRECTIONS_TABLE} "
        f"WHERE event_id = ? AND veh_id = ? AND undone_at IS NULL "
        f"AND start_time <= ? AND end_time >= ? ORDER BY correction_id",
        (event_id, veh_id, end_time, start_time)).fetchall()
    return [_correction_from_row(r) for r in rows]


def mark_correction_undone(conn: sqlite3.Connection, correction_id: int) -> None:
    """Does not commit."""
    conn.execute(f"UPDATE {CORRECTIONS_TABLE} SET undone_at = ? WHERE correction_id = ?",
                 (_now(), correction_id))
