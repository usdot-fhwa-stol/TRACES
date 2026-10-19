import sqlite3
from abc import ABC
from typing import Iterable, Optional, List, Any, Dict

# Sentinel for "no options provided" — distinguishable from an explicit empty dict.
_UNSET: Dict[str, Any] = {}

# Column vocabulary lives in utils (importable by plot/gui without
# analysis); re-exported here so `from analysis.base_comp import
# IMPUTED_*` and `from ..base_comp import IMPUTED_*` keep working.
from utils import (  # noqa: F401
    events_columns,
    IMPUTED_ORIGINAL, IMPUTED_SPLINE, IMPUTED_AUTO, IMPUTED_MANUAL, IMPUTED_STATUS_COL,
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