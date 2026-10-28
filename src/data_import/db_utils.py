"""Database-level helpers shared by import, open, and later database
copy/switch -- free of Qt so callers decide how to present errors."""

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils import events_columns, active_rows_clause, status_codes, IMPUTED_STATUS_COL

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


def _lane_pairs(conn: sqlite3.Connection, event_id: int, cols: set, active: str) -> List[list]:
    """[[lane_id, orig_lane_id], ...] -- exactly one entry per lane_id, in
    lane order. Consumers (the settings dialog's lane list, the plot's
    lane combobox) treat each entry as one lane.

    lane_id is per row and can be changed after import (lane-level
    anomaly corrections); orig_lane_id is the import's source label and
    never changes, and rows created after import (gap fills) leave it
    NULL. So DISTINCT (lane_id, orig_lane_id) can list a lane more than
    once. Each lane's label is instead the most common non-NULL
    orig_lane_id among its rows whose lane is still as imported
    (utils.status_codes(lane_measured=True)), ties to the smaller label.
    A lane with no such rows -- one that exists only through corrections
    -- has no source label of its own and gets None: its rows'
    orig_lane_id values name the lanes they were moved from.
    """
    if "orig_lane_id" not in cols:
        rows = conn.execute(
            f"SELECT DISTINCT lane_id FROM events WHERE event_id = ? AND lane_id IS NOT NULL"
            f"{active} ORDER BY lane_id", (event_id,)).fetchall()
        return [[r[0], None] for r in rows]

    if IMPUTED_STATUS_COL in cols:
        codes = ", ".join(f"'{c}'" for c in sorted(status_codes(lane_measured=True)))
        measured = f"CASE WHEN {IMPUTED_STATUS_COL} IN ({codes}) THEN 1 ELSE 0 END"
    else:
        measured = "1"   # nothing has been changed since import
    rows = conn.execute(
        f"SELECT lane_id, orig_lane_id, {measured} AS measured, COUNT(*) FROM events "
        f"WHERE event_id = ? AND lane_id IS NOT NULL{active} "
        f"GROUP BY lane_id, orig_lane_id, measured", (event_id,)).fetchall()

    by_lane: Dict[object, list] = {}
    for lane, orig, is_measured, count in rows:
        by_lane.setdefault(lane, []).append((orig, is_measured, count))

    def label(candidates):
        totals: Dict[object, int] = {}
        for orig, is_measured, count in candidates:
            if orig is not None and is_measured:
                totals[orig] = totals.get(orig, 0) + count
        return min(totals, key=lambda o: (-totals[o], str(o))) if totals else None

    return [[lane, label(by_lane[lane])] for lane in sorted(by_lane)]


def build_event_summary(conn: sqlite3.Connection, event_id: int) -> dict:
    """Compute the ranges.summary_json dict for one event.

    Removed trajectories (traj_status = R) are excluded, so veh_id lists
    only active trajectories -- vehicle search and the settings dialog's
    vehicle count stay correct after stitching. Defensive against the
    optional orig_lane_id / event_name columns. lane_id holds one
    [lane_id, orig_lane_id] pair per lane (_lane_pairs).
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

    lane_ids = _lane_pairs(conn, event_id, cols, active)

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
