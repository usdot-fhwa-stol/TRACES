import sys
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

import numpy as np

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
# Defined here (not in analysis) so plot/gui/data_import can build filters
# without importing the analysis package. analysis.base_comp re-exports these.

# Row provenance -- events.imputed_status (per row). Every code must also be
# registered in IMPUTED_CODES below.
IMPUTED_ORIGINAL  = " "   # real observation (column default)
IMPUTED_SPLINE    = "S"   # synthetic row created solely to complete SplineComp's calc_* timing grid
IMPUTED_AUTO      = "A"   # gap filled automatically by Missing Data Interpolation
IMPUTED_MANUAL    = "M"   # gap filled/refined manually by Missing Data Interpolation
IMPUTED_LANE      = "L"   # lane_id reassigned by Lane-Level Anomaly correction; kinematics still measured
IMPUTED_CORRECTED = "C"   # position and lane replaced by Lane-Level Anomaly midline correction;
                          # veh_speed / veh_accel recomputed from the corrected position
IMPUTED_STATUS_COL = "imputed_status"

# Modules that own imputed_status codes. A module may change the code of a
# row only if the row is IMPUTED_ORIGINAL or carries one of its own codes;
# superseded_points records (analysis.base_comp) are keyed by this name.
OWNER_IMPORT       = "import"
OWNER_SPLINE       = "spline"
OWNER_GAP          = "gap"
OWNER_LANE_ANOMALY = "lane_anomaly"


@dataclass(frozen=True)
class ImputedCode:
    """What one imputed_status code means for the row carrying it.

    Consumers should select rows by these properties (status_codes()) rather
    than by comparing against code literals, so a new code is handled by
    declaring its properties here. A new code still needs an audit of every
    module that owns or reads codes -- the properties make each module's
    intent explicit, they don't decide it.

    is_placeholder       the row exists only to complete a timing grid and
                         carries no trajectory value of its own (spline
                         synthetic rows). Excluded wherever a trajectory is
                         read as data (measured_rows_clause).
    kinematics_measured  veh_dist_trav / veh_speed / veh_accel / veh_jerk
                         hold the original sensor values.
    lane_measured        lane_id holds the original (imported) value.
    """
    code: str
    label: str
    owner: str
    is_placeholder: bool
    kinematics_measured: bool
    lane_measured: bool


IMPUTED_CODES: Dict[str, ImputedCode] = {c.code: c for c in (
    ImputedCode(IMPUTED_ORIGINAL,  "original",         OWNER_IMPORT,
                is_placeholder=False, kinematics_measured=True,  lane_measured=True),
    ImputedCode(IMPUTED_SPLINE,    "spline timing row", OWNER_SPLINE,
                is_placeholder=True,  kinematics_measured=False, lane_measured=False),
    ImputedCode(IMPUTED_AUTO,      "gap fill (auto)",   OWNER_GAP,
                is_placeholder=False, kinematics_measured=False, lane_measured=False),
    ImputedCode(IMPUTED_MANUAL,    "gap fill (manual)", OWNER_GAP,
                is_placeholder=False, kinematics_measured=False, lane_measured=False),
    ImputedCode(IMPUTED_LANE,      "lane reassigned",   OWNER_LANE_ANOMALY,
                is_placeholder=False, kinematics_measured=True,  lane_measured=False),
    ImputedCode(IMPUTED_CORRECTED, "midline corrected", OWNER_LANE_ANOMALY,
                is_placeholder=False, kinematics_measured=False, lane_measured=False),
)}

for _c in IMPUTED_CODES.values():
    # Codes are inlined into SQL as literals (status_in_clause), so they
    # must be single characters that need no escaping.
    assert len(_c.code) == 1 and _c.code != "'", f"invalid imputed_status code {_c.code!r}"
del _c


def status_codes(owner: Optional[str] = None, *,
                 is_placeholder: Optional[bool] = None,
                 kinematics_measured: Optional[bool] = None,
                 lane_measured: Optional[bool] = None) -> FrozenSet[str]:
    """Registered imputed_status codes matching every given property;
    None means "any". e.g. status_codes(owner=OWNER_GAP) -> {'A', 'M'}."""
    def keep(c: ImputedCode) -> bool:
        return ((owner is None or c.owner == owner)
                and (is_placeholder is None or c.is_placeholder == is_placeholder)
                and (kinematics_measured is None or c.kinematics_measured == kinematics_measured)
                and (lane_measured is None or c.lane_measured == lane_measured))
    return frozenset(c.code for c in IMPUTED_CODES.values() if keep(c))


def imputed_label(code: Optional[str]) -> str:
    """Human-readable name of an imputed_status code, for UI and logs."""
    c = IMPUTED_CODES.get(code if code is not None else IMPUTED_ORIGINAL)
    return c.label if c else f"unknown ({code!r})"


def status_in_clause(codes: Iterable[str], alias: str = "", negate: bool = False) -> str:
    """SQL fragment ' AND [alias.]imputed_status [NOT] IN (...)' with the
    codes inlined as literals. The caller checks the column exists.

    Prefer negate=True (exclude the codes a query must not see) over an
    allow-list: a code added later, or written by a newer version of the
    application, is then treated as ordinary trajectory data instead of
    silently disappearing from the query.

    Empty *codes*: "" when negated (nothing to exclude), otherwise a
    clause that matches no row."""
    codes = sorted(set(codes))
    for c in codes:
        if len(c) != 1 or c == "'":
            raise ValueError(f"invalid imputed_status code {c!r}")
    if not codes:
        return "" if negate else " AND 0"
    p = f"{alias}." if alias else ""
    listed = ", ".join(f"'{c}'" for c in codes)
    return f" AND {p}{IMPUTED_STATUS_COL} {'NOT IN' if negate else 'IN'} ({listed})"


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
    """SQL fragment excluding placeholder rows -- registered codes with
    is_placeholder (today only SplineComp's synthetic timing rows), which
    carry no trajectory value. Every other row, gap fills and lane
    corrections included, is trajectory data. Falls back to the legacy
    is_spline_imputed column on databases that predate imputed_status.
    Starts with ' AND', or "" if neither exists."""
    p = f"{alias}." if alias else ""
    if IMPUTED_STATUS_COL in cols:
        return status_in_clause(status_codes(is_placeholder=True), alias, negate=True)
    if "is_spline_imputed" in cols:
        return f" AND ({p}is_spline_imputed = 0 OR {p}is_spline_imputed IS NULL)"
    return ""

# ── Sampling interval and gaps ─────────────────────────────────────────────
# The single definition of "gap" for the whole application. Qt-free and
# numpy-only so ui_kit, analysis and plot can all use it.
#
#   step     the dataset's sampling interval: the most common positive
#            interval between consecutive samples. event_time_step() for an
#            event (preferred); estimate_time_step() for arrays in memory.
#   gap      consecutive samples more than GAP_FACTOR * step apart
#            (half a step of tolerance for timestamp jitter)
#   missing  expected samples absent inside an interval (missing_samples)

DEFAULT_TIME_STEP = 0.1   # seconds; fallback when no step can be inferred
GAP_FACTOR = 1.5


def step_mode(diffs: np.ndarray, decimals: int = 3,
              default: float = DEFAULT_TIME_STEP) -> float:
    """Most frequent positive, finite inter-sample difference. Zero diffs
    (duplicate timestamps) are excluded -- a zero step would otherwise
    make every downstream dt / step divide by zero."""
    diffs = np.asarray(diffs, dtype=np.float64)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return default
    # Round before counting to absorb float jitter (0.1000001 vs 0.0999999).
    vals, counts = np.unique(np.round(diffs, decimals), return_counts=True)
    return float(vals[np.argmax(counts)])


def estimate_time_step(time_arrays: Iterable[np.ndarray], decimals: int = 3,
                       default: float = DEFAULT_TIME_STEP) -> float:
    """Mode of consecutive time differences across several trajectories.
    Each array must be one trajectory's times in ascending order."""
    diffs = [np.diff(np.asarray(t, dtype=np.float64))
             for t in time_arrays if len(t) > 1]
    if not diffs:
        return default
    return step_mode(np.concatenate(diffs), decimals, default)


def event_time_step(db_connection, event_id: int, decimals: int = 3,
                    default: float = DEFAULT_TIME_STEP) -> float:
    """Dataset sampling interval for one event: the mode of consecutive
    time differences within each active trajectory, measured rows only
    (SplineComp synthetic rows excluded). The step every gap decision for
    this event should use."""
    cols = events_columns(db_connection)
    rows = db_connection.execute(
        f"SELECT veh_id, veh_time FROM events WHERE event_id = ?"
        f"{active_rows_clause(cols)}{measured_rows_clause(cols)} "
        f"ORDER BY veh_id, veh_time",
        (event_id,),
    ).fetchall()
    if len(rows) < 2:
        return default
    arr = np.asarray(rows, dtype=np.float64)
    same_vehicle = np.diff(arr[:, 0]) == 0
    return step_mode(np.diff(arr[:, 1])[same_vehicle], decimals, default)


def gap_indices(t, step: Optional[float]) -> np.ndarray:
    """Indices i where t[i] -> t[i + 1] is a gap. *t* ascending. Empty when
    there are fewer than two samples or no valid step."""
    t = np.asarray(t, dtype=np.float64)
    if t.size < 2 or not step or step <= 0:
        return np.empty(0, dtype=np.intp)
    return np.nonzero(np.diff(t) > GAP_FACTOR * step)[0]


def missing_samples(dt, step: float):
    """Expected samples absent inside interval(s) *dt*: 0 for any interval
    up to GAP_FACTOR * step, else ceil(dt/step - 0.5) - 1 -- the samples
    at start + k*step lying more than half a step before the interval's
    end. Scalar in, int out; array in, int array out."""
    n = np.ceil(np.asarray(dt, dtype=np.float64) / step - 0.5 - 1e-6) - 1
    n = np.where(np.isfinite(n), np.maximum(n, 0), 0).astype(int)
    return int(n) if n.ndim == 0 else n


def segment_bounds(t, step: Optional[float]) -> List[Tuple[int, int]]:
    """(start, end) index pairs, end exclusive, of the gap-free runs of *t*."""
    n = len(t)
    if n == 0:
        return []
    cuts = gap_indices(t, step) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [n]))
    return list(zip(starts.tolist(), ends.tolist()))
