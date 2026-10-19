"""Database-level helpers shared by import, open, and later database
copy/switch -- free of Qt so callers decide how to present errors."""

import math
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from utils import events_columns, active_rows_clause

# Columns every valid trajectory database must provide in the events
# table. Matches the required set enforced by the import configuration
# check (BaseProcess.check_configuration implementations).
REQUIRED_EVENTS_COLUMNS = ("event_id", "veh_id", "veh_time",
                           "lane_id", "veh_dist_trav")


def validate_trajectory_db(file_path: str) -> Tuple[Optional[sqlite3.Connection], Optional[str]]:
    """Open *file_path* and check it is a usable trajectory database.

    Valid means: an events table with REQUIRED_EVENTS_COLUMNS, a ranges
    table (created last by the import worker, so it also marks a
    completed import), and at least one events record.

    Opens read-write WITHOUT create (URI mode=rw), so a missing path is
    reported as an error instead of silently creating an empty file.

    Returns:
        (connection, None) on success -- caller owns the connection.
        (None, message) on failure -- nothing is left open.
    """
    try:
        uri = Path(file_path).resolve().as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True)
    except (sqlite3.Error, ValueError) as e:
        return None, f"Failed to open database: {e}"

    def fail(message: str):
        conn.close()
        return None, message

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('events', 'ranges')"
        )
        found = {row[0] for row in cursor.fetchall()}
        if "events" not in found:
            return fail("Database does not contain an 'events' table.")
        if "ranges" not in found:
            return fail("Database does not contain a 'ranges' table. The import "
                        "may not have completed successfully — re-import the "
                        "source file.")

        missing = [c for c in REQUIRED_EVENTS_COLUMNS if c not in events_columns(conn)]
        if missing:
            return fail("Events table is missing required column(s): "
                        f"{', '.join(missing)}.")

        cursor.execute("SELECT COUNT(*) FROM events")
        if cursor.fetchone()[0] == 0:
            return fail("Events table is empty.")

        return conn, None
    except sqlite3.Error as e:
        return fail(f"Failed to open database: {e}")


def build_event_summary(conn: sqlite3.Connection, event_id: int) -> dict:
    """Compute the ranges.summary_json dict for one event.

    Removed trajectories (traj_status = R) are excluded, so veh_id lists
    only active trajectories -- vehicle search and the settings dialog's
    vehicle count stay correct after stitching. Defensive against the
    optional orig_lane_id / event_name columns.
    """
    cols = events_columns(conn)
    active = active_rows_clause(cols)
    cursor = conn.cursor()

    event_name = f"Event {event_id}"
    if "event_name" in cols:
        cursor.execute(
            f"SELECT DISTINCT event_name FROM events WHERE event_id = ?{active}",
            (event_id,))
        row = cursor.fetchone()
        if row and row[0] is not None:
            event_name = row[0]

    cursor.execute(
        f"SELECT MIN(veh_time), MAX(veh_time), MIN(veh_dist_trav), MAX(veh_dist_trav) "
        f"FROM events WHERE event_id = ?{active}", (event_id,))
    time_min, time_max, dist_min, dist_max = cursor.fetchone()

    # Pair lane_id with orig_lane_id when available, otherwise None, so
    # consumers always see the same [lane_id, orig] shape.
    if "orig_lane_id" in cols:
        cursor.execute(
            f"SELECT DISTINCT lane_id, orig_lane_id FROM events "
            f"WHERE event_id = ?{active} ORDER BY lane_id", (event_id,))
        lane_ids = [[r[0], r[1]] for r in cursor.fetchall() if r[0] is not None]
    else:
        cursor.execute(
            f"SELECT DISTINCT lane_id FROM events "
            f"WHERE event_id = ?{active} ORDER BY lane_id", (event_id,))
        lane_ids = [[r[0], None] for r in cursor.fetchall() if r[0] is not None]

    cursor.execute(
        f"SELECT DISTINCT veh_id FROM events WHERE event_id = ?{active} ORDER BY veh_id",
        (event_id,))
    veh_ids = [r[0] for r in cursor.fetchall()]

    return {
        "event_name": event_name,
        "veh_time": [math.floor(time_min) if time_min is not None else 0,
                     math.ceil(time_max) if time_max is not None else 0],
        "veh_dist_trav": [math.floor(dist_min) if dist_min is not None else 0,
                          math.ceil(dist_max) if dist_max is not None else 0],
        "lane_id": lane_ids,
        "veh_id": veh_ids,
    }


def update_event_summary(conn: sqlite3.Connection, event_id: int) -> dict:
    """Recompute and store one event's summary. Does NOT commit -- callers
    include this in their own transaction (e.g. a stitch apply)."""
    import json
    summary = build_event_summary(conn, event_id)
    conn.execute("UPDATE ranges SET summary_json = ? WHERE event_id = ?",
                 (json.dumps(summary), event_id))
    return summary