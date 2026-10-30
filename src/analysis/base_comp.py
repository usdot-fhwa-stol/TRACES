import sqlite3
from abc import ABC
from typing import Iterable, Optional, List, Any, Dict, Tuple

# Sentinel for "no options provided" — distinguishable from an explicit empty dict.
_UNSET: Dict[str, Any] = {}

# Column vocabulary lives in utils (importable by plot/gui without
# analysis); re-exported here so `from analysis.base_comp import
# IMPUTED_*` and `from ..base_comp import IMPUTED_*` keep working.
from utils import (  # noqa: F401
    events_columns,
    IMPUTED_ORIGINAL, IMPUTED_SPLINE, IMPUTED_AUTO, IMPUTED_MANUAL,
    IMPUTED_LANE, IMPUTED_STATUS_COL,
    IMPUTED_CODES, ImputedCode, status_codes, status_in_clause, imputed_label,
    OWNER_IMPORT, OWNER_SPLINE, OWNER_GAP, OWNER_LANE_ANOMALY,
    TRAJ_KEPT, TRAJ_REMOVED, TRAJ_STITCHED, TRAJ_STATUS_COL,
    active_rows_clause, measured_rows_clause,
)

# Per-row columns written by analysis modules whose values are only valid
# for the trajectory they were computed on (derivatives, spline fits,
# errors). Trajectory Stitching writes NULL into these on stitched rows,
# since values computed per segment are wrong across the seam; the user
# reruns the analysis on the stitched trajectory. Add to this list when a
# new module writes per-row results into events.
DERIVED_EVENTS_COLUMNS = (
    # SplineComp
    "calc_position", "calc_speed", "calc_acceleration", "calc_jerk",
    # InternalConsistencyComp
    "veh_speed_cal", "veh_accel_cal", "veh_jerk_cal",
    "speed_error", "accel_error", "jerk_error",
)

def ensure_imputed_status_column(conn: sqlite3.Connection) -> None:
    """Add the shared imputed_status TEXT column to events, if not already
    present. Safe to call repeatedly (checked via PRAGMA table_info rather
    than the try/ALTER/except-duplicate-column pattern used elsewhere,
    since the migration step below needs that column list anyway).

    Migrates any pre-existing is_spline_imputed INTEGER column (databases
    created before this column was unified): 1 -> IMPUTED_SPLINE, 0/NULL
    left at the new column's default (IMPUTED_ORIGINAL). The old column
    is left in place rather than dropped — SQLite's DROP COLUMN support
    is version-dependent and an unused column is harmless.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(events)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    if IMPUTED_STATUS_COL not in existing_cols:
        conn.execute(
            f"ALTER TABLE events ADD COLUMN {IMPUTED_STATUS_COL} "
            f"TEXT NOT NULL DEFAULT '{IMPUTED_ORIGINAL}'"
        )
        conn.commit()

    if "is_spline_imputed" in existing_cols:
        conn.execute(
            f"UPDATE events SET {IMPUTED_STATUS_COL} = '{IMPUTED_SPLINE}' "
            f"WHERE is_spline_imputed = 1 AND {IMPUTED_STATUS_COL} = '{IMPUTED_ORIGINAL}'"
        )
        conn.commit()

_TRAJ_STATUS_TRIGGER = "events_traj_status_inherit"


def ensure_traj_status_column(conn: sqlite3.Connection) -> None:
    """Add events.traj_status (default TRAJ_KEPT) and its inherit trigger,
    if not already present. Safe to call repeatedly.

    Invariant: traj_status is uniform per (event_id, veh_id). That's what
    lets analyses exclude removed trajectories purely by vehicle ID.
    Rows inserted later into an existing trajectory -- gap fills,
    SplineComp synthetic rows -- would otherwise get the default 'K'
    even inside a stitched or removed trajectory, so an AFTER INSERT
    trigger copies the vehicle's existing non-K status onto any new K
    row. Both lookups use the (event_id, veh_id) prefix of the primary
    key. UPSERTs that take the ON CONFLICT path don't fire it, which is
    correct: that row already has the right status.
    """
    col, kept = TRAJ_STATUS_COL, TRAJ_KEPT
    if col not in events_columns(conn):
        conn.execute(
            f"ALTER TABLE events ADD COLUMN {col} TEXT NOT NULL DEFAULT '{kept}'")
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS {_TRAJ_STATUS_TRIGGER}
        AFTER INSERT ON events
        WHEN NEW.{col} = '{kept}'
        BEGIN
            UPDATE events
               SET {col} = (SELECT e.{col} FROM events e
                             WHERE e.event_id = NEW.event_id
                               AND e.veh_id   = NEW.veh_id
                               AND e.{col}   <> '{kept}'
                             LIMIT 1)
             WHERE event_id = NEW.event_id
               AND veh_id   = NEW.veh_id
               AND veh_time = NEW.veh_time
               AND EXISTS (SELECT 1 FROM events e
                            WHERE e.event_id = NEW.event_id
                              AND e.veh_id   = NEW.veh_id
                              AND e.{col}   <> '{kept}');
        END
    """)
    conn.commit()


# ── Superseded points ──────────────────────────────────────────────────────
# events holds one row per (event_id, veh_id, veh_time). When a module
# overwrites the values of an existing row -- a gap fill bridging a measured
# point, a lane correction -- the row's previous values and imputed_status
# are saved here first, under that module's owner name (utils.OWNER_*), and
# written back when the module undoes its change. No values are lost and no
# per-module column is added to events.
#
# Records stack: the primary key includes owner, so if the lane module
# corrects a point and the gap module later bridges it, each keeps its own
# record of what the row held before *its* change, and undoing them in
# reverse order walks the row back to the original.
#
# Ownership rule: a module overwrites a row only if the row is
# IMPUTED_ORIGINAL or carries a code from another module that it has
# explicitly agreed to handle. restore_superseded() never writes into a row
# whose current code belongs to a different owner -- that record stays
# until the row is handed back.

SUPERSEDED_TABLE = "superseded_points"
LEGACY_GAP_SUPERSEDED_TABLE = "gap_superseded_points"   # pre-registry, gap module only

SUPERSEDED_VALUE_COLS = ("lane_id", "veh_dist_trav", "veh_speed", "veh_accel", "veh_jerk")
OPTIONAL_KINEMATIC_COLS = ("veh_speed", "veh_accel", "veh_jerk")

_SUPERSEDED_DDL = f"""
CREATE TABLE IF NOT EXISTS {SUPERSEDED_TABLE} (
    event_id      INTEGER NOT NULL,
    veh_id        INTEGER NOT NULL,
    veh_time      REAL    NOT NULL,
    owner         TEXT    NOT NULL,   -- utils.OWNER_* of the module that overwrote the row
    prior_status  TEXT    NOT NULL,   -- events.imputed_status before that change
    lane_id,                          -- untyped: stored exactly as events holds it
    veh_dist_trav REAL,
    veh_speed     REAL,
    veh_accel     REAL,
    veh_jerk      REAL,
    PRIMARY KEY (event_id, veh_id, veh_time, owner)
) WITHOUT ROWID"""


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def ensure_superseded_table(conn: sqlite3.Connection) -> None:
    """Create superseded_points if absent and migrate a legacy
    gap_superseded_points table into it (owner OWNER_GAP, prior_status
    IMPUTED_ORIGINAL -- the legacy table only ever saved original rows),
    then drop the legacy table. Does not commit: called on write paths,
    inside the caller's transaction, after the write target is resolved."""
    conn.execute(_SUPERSEDED_DDL)
    if table_exists(conn, LEGACY_GAP_SUPERSEDED_TABLE):
        values = ", ".join(SUPERSEDED_VALUE_COLS)
        conn.execute(
            f"INSERT OR IGNORE INTO {SUPERSEDED_TABLE} "
            f"(event_id, veh_id, veh_time, owner, prior_status, {values}) "
            f"SELECT event_id, veh_id, veh_time, ?, ?, {values} "
            f"FROM {LEGACY_GAP_SUPERSEDED_TABLE}",
            (OWNER_GAP, IMPUTED_ORIGINAL))
        conn.execute(f"DROP TABLE {LEGACY_GAP_SUPERSEDED_TABLE}")


def _kinematic_select(cols: set, alias: str = "") -> List[str]:
    p = f"{alias}." if alias else ""
    return [f"{p}{c}" if c in cols else "NULL" for c in OPTIONAL_KINEMATIC_COLS]


def supersede_rows(conn: sqlite3.Connection, owner: str,
                   keys: Iterable[Tuple[int, int, float]]) -> None:
    """Save the current values and imputed_status of every existing events
    row at *keys* ((event_id, veh_id, veh_time) tuples) that *owner* is
    about to overwrite. Rows that are placeholders, or already carry one
    of *owner*'s own codes (a refill), hold nothing to preserve and are
    skipped; keys with no events row are skipped too. INSERT OR IGNORE
    keeps the first saved values if a point is saved twice. Does not
    commit."""
    keys = [(int(e), int(v), float(t)) for e, v, t in keys]
    if not keys:
        return
    ensure_superseded_table(conn)
    cols = events_columns(conn)
    if IMPUTED_STATUS_COL in cols:
        prior = IMPUTED_STATUS_COL
        skip = status_in_clause(
            status_codes(is_placeholder=True) | status_codes(owner=owner), negate=True)
    else:
        prior, skip = f"'{IMPUTED_ORIGINAL}'", ""
    values = ", ".join(SUPERSEDED_VALUE_COLS)
    conn.executemany(
        f"INSERT OR IGNORE INTO {SUPERSEDED_TABLE} "
        f"(event_id, veh_id, veh_time, owner, prior_status, {values}) "
        f"SELECT event_id, veh_id, veh_time, ?, {prior}, lane_id, veh_dist_trav, "
        f"{', '.join(_kinematic_select(cols))} "
        f"FROM events WHERE event_id = ? AND veh_id = ? AND veh_time = ?{skip}",
        [(owner, e, v, t) for e, v, t in keys],
    )


def restore_superseded(conn: sqlite3.Connection, owner: str, event_id: int,
                       veh_id: Optional[int] = None,
                       start_time: Optional[float] = None,
                       end_time: Optional[float] = None) -> int:
    """Write *owner*'s superseded records back into their events rows --
    values and prior imputed_status -- and drop those records, for one
    event, optionally narrowed to one vehicle and the open interval
    (start_time, end_time). Returns the number of points restored. Does
    not commit.

    A record is restored only while its events row still carries one of
    *owner*'s codes (or has gone missing); a row another module has since
    taken over is left alone, and its record kept, so one module's undo
    never overwrites another's change.

    UPDATE-in-place (via UPSERT) rather than delete-and-insert, so the
    row's other columns -- import columns like veh_length, analysis
    columns -- survive; the UPSERT's INSERT branch only runs if the row
    has gone missing, which shouldn't happen."""
    if not (table_exists(conn, SUPERSEDED_TABLE)
            or table_exists(conn, LEGACY_GAP_SUPERSEDED_TABLE)):
        return 0
    ensure_superseded_table(conn)

    where = "s.owner = ? AND s.event_id = ?"
    params: list = [owner, event_id]
    if veh_id is not None:
        where += " AND s.veh_id = ?"
        params.append(veh_id)
    if start_time is not None and end_time is not None:
        where += " AND s.veh_time > ? AND s.veh_time < ?"
        params += [start_time, end_time]

    cols = events_columns(conn)
    has_status = IMPUTED_STATUS_COL in cols
    if has_status:
        held = status_in_clause(status_codes(owner=owner), alias="e")
        where += f" AND (e.event_id IS NULL OR (1 = 1{held}))"

    saved = conn.execute(
        f"SELECT s.event_id, s.veh_id, s.veh_time, s.prior_status, "
        f"{', '.join('s.' + c for c in SUPERSEDED_VALUE_COLS)} "
        f"FROM {SUPERSEDED_TABLE} s LEFT JOIN events e "
        f"ON e.event_id = s.event_id AND e.veh_id = s.veh_id AND e.veh_time = s.veh_time "
        f"WHERE {where}", params).fetchall()
    if not saved:
        return 0

    kinematics = [c for c in OPTIONAL_KINEMATIC_COLS if c in cols]
    insert_cols = ["event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav", *kinematics]
    if has_status:
        insert_cols.append(IMPUTED_STATUS_COL)
    update_cols = insert_cols[3:]
    value_index = {name: 4 + i for i, name in enumerate(SUPERSEDED_VALUE_COLS)}

    def row_values(row):
        values = [row[0], row[1], row[2], row[value_index["lane_id"]],
                  row[value_index["veh_dist_trav"]],
                  *[row[value_index[c]] for c in kinematics]]
        if has_status:
            values.append(row[3])
        return values

    conn.executemany(
        f"INSERT INTO events ({', '.join(insert_cols)}) "
        f"VALUES ({','.join('?' for _ in insert_cols)}) "
        f"ON CONFLICT(event_id, veh_id, veh_time) DO UPDATE SET "
        f"{', '.join(f'{c} = excluded.{c}' for c in update_cols)}",
        [row_values(row) for row in saved],
    )
    conn.executemany(
        f"DELETE FROM {SUPERSEDED_TABLE} "
        f"WHERE owner = ? AND event_id = ? AND veh_id = ? AND veh_time = ?",
        [(owner, row[0], row[1], row[2]) for row in saved],
    )
    return len(saved)


def layer_source(conn: sqlite3.Connection, owner: str) -> Tuple[str, list]:
    """(sql, params): a parenthesised derived table of the trajectory data
    as it reads with every change made by *owner* undone -- events rows
    whose code is neither a placeholder nor *owner*'s, plus *owner*'s
    superseded records (the values those rows held before *owner*
    overwrote them). Columns: event_id, veh_id, veh_time, lane_id,
    veh_dist_trav, veh_speed, veh_accel, veh_jerk (NULL where events lacks
    the column). Use as ``SELECT ... FROM {sql} WHERE ...`` with *params*
    placed before the outer query's own parameters.

    For the gap module this is its "measured data": its own fills never
    change what it detects or fits. Other modules' changes (lane
    corrections) are part of the data it sees. Read-only: a legacy
    gap_superseded_points table is read in place, not migrated.
    """
    cols = events_columns(conn)

    def col(name: str) -> str:
        return f"{name} AS {name}" if name in cols else f"NULL AS {name}"

    select = ", ".join(["event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav",
                        *[col(c) for c in OPTIONAL_KINEMATIC_COLS]])
    exclude = ""
    if IMPUTED_STATUS_COL in cols:
        exclude = status_in_clause(
            status_codes(is_placeholder=True) | status_codes(owner=owner), negate=True)
    sql = f"SELECT {select} FROM events WHERE 1 = 1{exclude}"
    params: list = []

    record_cols = f"event_id, veh_id, veh_time, {', '.join(SUPERSEDED_VALUE_COLS)}"
    if table_exists(conn, SUPERSEDED_TABLE):
        sql += f" UNION ALL SELECT {record_cols} FROM {SUPERSEDED_TABLE} WHERE owner = ?"
        params.append(owner)
    elif owner == OWNER_GAP and table_exists(conn, LEGACY_GAP_SUPERSEDED_TABLE):
        sql += f" UNION ALL SELECT {record_cols} FROM {LEGACY_GAP_SUPERSEDED_TABLE}"
    return f"({sql})", params


# Side tables holding per-vehicle records, as (table, vehicle-id columns).
# When a trajectory is deleted outright (Trajectory Stitching's undo
# deletes the stitched vehicle), its records here would otherwise outlive
# it and point at rows that no longer exist. Add to this list when a new
# module keeps per-vehicle records.
VEHICLE_KEYED_TABLES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (SUPERSEDED_TABLE, ("veh_id",)),
    (LEGACY_GAP_SUPERSEDED_TABLE, ("veh_id",)),
    ("gaps", ("veh_id",)),                                   # Gap Interpolation
    ("lane_corrections", ("veh_id",)),                       # Lane-Level Anomalies
    ("lane_anomalies", ("veh_id", "other_veh_id")),          # Lane-Level Anomalies
)


def purge_vehicle_records(conn: sqlite3.Connection, event_id: int, veh_id: int) -> int:
    """Delete every VEHICLE_KEYED_TABLES record of one vehicle, for a
    trajectory whose events rows are being deleted outright. Tables that
    don't exist are skipped. Returns the number of records deleted. Does
    not commit -- call inside the transaction that deletes the rows."""
    deleted = 0
    for table, columns in VEHICLE_KEYED_TABLES:
        if not table_exists(conn, table):
            continue
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column in present:
                deleted += conn.execute(
                    f"DELETE FROM {table} WHERE event_id = ? AND {column} = ?",
                    (event_id, veh_id)).rowcount
    return deleted


class BaseComp(ABC):
    """Base class for computational analyses over the events table.

    Subclasses override comp_series. Subclass-specific parameters travel
    through the `options` dict so the base interface stays stable as new
    analyses are added.

    Subclasses should define OPTIONS_SCHEMA as a dict[str, default_value]
    listing the option keys they recognize and their defaults.
    """

    OPTIONS_SCHEMA: Dict[str, Any] = {}

    def comp(self,
             db_connection: sqlite3.Connection,
             options: Optional[Dict[str, Any]] = None) -> List[Any]:
        options = self._resolve_options(options)
        results: List[Any] = []
        cursor = db_connection.cursor()
        cursor.execute("SELECT event_id FROM ranges ORDER BY event_id")
        event_ids = [row[0] for row in cursor.fetchall()]
        for event_id in event_ids:
            results.extend(self.comp_event(db_connection, event_id, options))
        return results

    def comp_event(self,
                   db_connection: sqlite3.Connection,
                   event_id: int,
                   options: Optional[Dict[str, Any]] = None) -> List[Any]:
        options = self._resolve_options(options)
        return self.comp_series(db_connection, event_id, None, options)

    def comp_series(self,
                    db_connection: sqlite3.Connection,
                    event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None) -> List[Any]:
        return []

    @classmethod
    def _resolve_options(cls, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge caller-supplied options with subclass defaults.

        Unknown keys are kept (they may be informational). Missing keys
        are filled with defaults from OPTIONS_SCHEMA.
        """
        resolved = dict(cls.OPTIONS_SCHEMA)
        if options:
            resolved.update(options)
        return resolved

    @staticmethod
    def _events_columns(db_connection: sqlite3.Connection) -> set:
        """Column names present in the events table (utils.events_columns)."""
        return events_columns(db_connection)

    @staticmethod
    def _event_veh_ids(db_connection: sqlite3.Connection, event_id: int) -> List[int]:
        """Active vehicle IDs for an event. Removed (stitched-away)
        trajectories are excluded here, which by the per-vehicle
        traj_status invariant excludes them from every analysis whose
        top-level path passes veh_ids=None."""
        active = active_rows_clause(events_columns(db_connection))
        cursor = db_connection.cursor()
        cursor.execute(
            f"SELECT DISTINCT veh_id FROM events WHERE event_id = ?{active} ORDER BY veh_id",
            (event_id,),
        )
        return [row[0] for row in cursor.fetchall()]
