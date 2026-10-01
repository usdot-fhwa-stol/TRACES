import sys
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


_last_dialog_dir: Optional[str] = None   # process-wide, not persisted across app restarts


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to a bundled read-only resource.
    Works for dev and PyInstaller frozen builds."""
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)


def get_user_data_path(relative_path: str = "") -> Path:
    """Get path for writable user data (configs, logs).
    
    Points to %APPDATA%\\TrajectoryTools in frozen builds,
    or the current working directory in development.
    """
    if getattr(sys, 'frozen', False):
        base = Path(os.environ.get('APPDATA', Path.home())) / 'TrajectoryTools'
    else:
        base = Path(os.path.abspath("."))
    base.mkdir(parents=True, exist_ok=True)
    return base / relative_path if relative_path else base


def initialize_user_config() -> None:
    """Seed the user's config directory from bundled defaults on first run.
    
    Copies all *.json files from the bundled config directory to
    %APPDATA%\\TrajectoryTools\\config\\ if the destination is empty.
    Existing user configs are never overwritten.
    """
    user_config_dir = get_user_data_path("config")
    user_config_dir.mkdir(parents=True, exist_ok=True)

    # Only seed if no JSON files exist yet
    if any(user_config_dir.glob("*.json")):
        return

    bundled_config_dir = Path(get_resource_path("config"))
    if not bundled_config_dir.exists():
        return

    for src in bundled_config_dir.glob("*.json"):
        dst = user_config_dir / src.name
        shutil.copy2(src, dst)

def default_save_stem(prefix: str, suffix: str = "") -> str:
    """Return a filename stem of the form  prefix[_suffix]_YYYYMMDD_HHMMSS.

    Args:
        prefix:  short content identifier, e.g. 'jerk_analysis_e3'
        suffix:  optional settings tag, e.g. 'thr15.0_from_accel'
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [p for p in (prefix, suffix, ts) if p]
    return "_".join(parts)

def last_dialog_dir() -> str:
    """Starting directory for file/directory dialogs: the most recently
    used location across the app this session, or the user's home
    directory if none has been used yet."""
    return _last_dialog_dir or str(Path.home())

def remember_dialog_path(path: str) -> None:
    """Record the directory containing *path* as the most recently used
    file-dialog location. *path* may be a file or a directory.

    Call this only after a dialog returns a non-empty/accepted result --
    never on cancel, so a rejected dialog can't clobber a previously good
    location.
    """
    global _last_dialog_dir
    if not path:
        return
    p = Path(path)
    _last_dialog_dir = str(p if p.is_dir() else p.parent)

def events_columns(db_connection) -> set:
    """Return the set of column names present in the events table.

    With the config-driven import schema, databases only guarantee the
    required columns (event_id, veh_id, veh_time, lane_id, veh_dist_trav).
    Every consumer of an optional column — veh_speed, veh_accel,
    orig_lane_id, veh_length, and so on — must check for its presence
    before referencing it in SQL. This helper is the single shared way
    to do that check.

    Note: analysis modules may ALTER TABLE to add computed columns
    (veh_speed_cal, calc_*, imputed_status, ...) during a session, so
    the result reflects the table at call time and should be re-queried
    per operation rather than cached for the lifetime of a connection.

    Args:
        db_connection: Open connection to a trajectory database.

    Returns:
        set: Column names currently present in the events table. Empty
            set if the table does not exist.
    """
    import sqlite3
    try:
        cursor = db_connection.cursor()
        cursor.execute("PRAGMA table_info(events)")
        return {row[1] for row in cursor.fetchall()}
    except sqlite3.Error:
        return set()

# ── events-table column vocabulary ─────────────────────────────────────────
# Defined here (not in comp) so plot/gui/data_import can build filters
# without importing the comp package. comp.base_comp re-exports these.

# Row provenance -- events.imputed_status (per row).
IMPUTED_ORIGINAL = " "   # real observation (column default)
IMPUTED_SPLINE   = "S"   # synthetic row created solely to complete SplineComp's calc_* timing grid
IMPUTED_AUTO     = "A"   # gap filled automatically by Missing Data Interpolation
IMPUTED_MANUAL   = "M"   # gap filled/refined manually by Missing Data Interpolation
IMPUTED_STATUS_COL = "imputed_status"

# Trajectory status -- events.traj_status (uniform per event_id, veh_id).
TRAJ_KEPT      = "K"     # not part of a stitch (column default)
TRAJ_REMOVED   = "R"     # consumed by a stitch; hidden from plot and analyses
TRAJ_STITCHED  = "S"     # created by a stitch
TRAJ_STATUS_COL = "traj_status"


def active_rows_clause(cols: set, alias: str = "") -> str:
    """SQL fragment excluding rows of removed (stitched-away) trajectories,
    or "" if the traj_status column doesn't exist yet. Starts with ' AND'."""
    if TRAJ_STATUS_COL not in cols:
        return ""
    p = f"{alias}." if alias else ""
    return f" AND {p}{TRAJ_STATUS_COL} <> '{TRAJ_REMOVED}'"


def measured_rows_clause(cols: set, alias: str = "") -> str:
    """SQL fragment excluding SplineComp synthetic rows (no measurement).
    Falls back to the legacy is_spline_imputed column on databases that
    predate imputed_status. Starts with ' AND', or "" if neither exists."""
    p = f"{alias}." if alias else ""
    if IMPUTED_STATUS_COL in cols:
        return f" AND {p}{IMPUTED_STATUS_COL} <> '{IMPUTED_SPLINE}'"
    if "is_spline_imputed" in cols:
        return f" AND ({p}is_spline_imputed = 0 OR {p}is_spline_imputed IS NULL)"
    return ""