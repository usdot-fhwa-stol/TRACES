"""Gap detection for the Missing Data Identification and Interpolation
module -- top-level Data > Gap Interpolation (Task9.1).

comp_series() is detection only: it reports where gaps are in each
trajectory's timestamp sequence, it does not fill them. The spec's
two-stage flow (detect -> configure defaults -> Apply -> per-gap review)
keeps filling as a separate, later action, so this module mirrors that
split rather than doing detection+fill in one pass the way SplineComp
does -- gap reporting is useful on its own (Task9.1's "detect...
automatically" objective is distinct from "generate corrected trajectory
values").

Gap definition
--------------
Two options (GapConfigDialog's gap definition spinboxes) decide what
counts as a gap -- see find_gaps:
    min_missing  a run of consecutive missing timestamps is a gap only if
                 it has at least this many (>= 1)
    max_bridge   two gaps separated by at most this many existing
                 (measured) points are merged into one gap (>= 0; 0 never
                 merges). The measured points inside a merged gap are
                 "bridged": the fill replaces them.

Measured data and superseded points
-----------------------------------
"Measured data" means the original measurements of a trajectory: events
rows with imputed_status = IMPUTED_ORIGINAL, plus the measured values of
bridged points saved in the gap_superseded_points table (below).
Detection and compute_fill's spline fit both read exactly that
population (_measured_source), so a trajectory's fit always uses all of
its measured data, and a gap this module has already filled -- bridged
points included -- is detected again on every rerun.

events holds one row per (event_id, veh_id, veh_time), so a bridged
point cannot keep its measured row next to a fill row. When a fill is
written over a measured row (write_fill_rows), the row's measured values
are first saved to gap_superseded_points -- the record that flags that
measurement as removed -- and the events row then holds the fill, tagged
IMPUTED_AUTO / IMPUTED_MANUAL like every other fill row, so the imputed
values supersede the measurement everywhere the events table is read.
Clearing the fill (Remove, Delete, a rerun's replace) writes the saved
values back into the row as IMPUTED_ORIGINAL and drops the record.

The fill itself is compute_fill(), below: it calls
analysis.spline.spline_comp.apply_spline_fit() directly per gap
(imported, not reimplemented) and is persisted through
analysis/gap_interpolation/gap_config_dialog.py and gap_review_dialog.py,
following the same comp-computes / dialog-persists split used by every other
analysis module in this codebase.

Gap statuses (gaps.status):
    GAP_STATUS_UNFILLED  detected, no fill (not attempted, or it failed)
    IMPUTED_AUTO         filled by GapConfigDialog's Apply
    IMPUTED_MANUAL       filled by GapReviewDialog's Apply
    GAP_STATUS_REMOVED   fill removed in GapReviewDialog; automatic runs
                         skip it unless "Include manual edits" is checked
"""

import sqlite3
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

import numpy as np

from utils import event_time_step, events_columns
from ..base_comp import (
    BaseComp, ensure_imputed_status_column, IMPUTED_AUTO, IMPUTED_MANUAL, IMPUTED_ORIGINAL,
    IMPUTED_STATUS_COL,
)
from ..spline.spline_comp import apply_spline_fit, nearest_valid_ic
from ..trajectory_utils import generate_complete_timestamps

logger = logging.getLogger(__name__)


# ── Gap definition defaults ─────────────────────────────────────────────────

DEFAULT_MIN_MISSING = 3   # missing points that identify a gap (minimum 1)
DEFAULT_MAX_BRIDGE = 3    # existing points ignored to merge adjacent gaps (minimum 0)
MIN_MISSING_LOWEST = 1
MAX_BRIDGE_LOWEST = 0


# ── Gap span detection ──────────────────────────────────────────────────────

@dataclass
class GapSpan:
    """A single detected gap within one trajectory, before it's attached
    to an (event_id, veh_id, gap_id) identity."""
    start_time: float   # last measured timestamp before the gap
    end_time: float     # first measured timestamp after the gap
    missing_rows: int   # expected-but-absent timestamps in between
    bridged_rows: int = 0   # measured points in between, replaced by the fill (merged gaps only)


def find_gaps(time_array: np.ndarray, time_step: Optional[float] = None,
              min_missing: int = 1, max_bridge: int = 0) -> List[GapSpan]:
    """Detect gaps in one trajectory's time values.

    Builds on trajectory_utils.generate_complete_timestamps(), which infers
    the expected sample step as the most common inter-sample interval and
    returns the original timestamps plus synthetic ones filling each gap.
    This function adds the piece that function doesn't need for its own
    purpose (completing calc_*'s timing grid) but this module does:
    grouping the synthetic points into discrete gaps, each bounded by
    measured timestamps on either side.

    1. Every maximal run of consecutive missing timestamps is a candidate.
    2. A candidate is a gap only if it has at least *min_missing* missing
       timestamps. Shorter runs are not gaps on their own.
    3. Consecutive gaps (from step 2) separated by at most *max_bridge*
       measured points are merged into one gap spanning both. Everything
       between them -- those measured points and any shorter missing runs
       -- is inside the merged gap, so its fill covers it: missing_rows
       counts every missing timestamp in the span and bridged_rows every
       measured point. *max_bridge* = 0 never merges.

    Returns an empty list for trajectories with fewer than two distinct
    timestamps (no step can be inferred) or with no gaps.
    """
    min_missing = max(MIN_MISSING_LOWEST, int(min_missing))
    max_bridge = max(MAX_BRIDGE_LOWEST, int(max_bridge))

    t = np.unique(np.asarray(time_array, dtype=np.float64))
    if t.size < 2:
        return []

    full = generate_complete_timestamps(t, time_step)
    if full.size == t.size:
        return []   # nothing synthetic -- no gaps

    # Exact equality is safe here: `full` is t concatenated with a
    # separately-constructed grid, so every original value survives
    # unchanged (see generate_complete_timestamps's docstring).
    is_original = np.isin(full, t)

    # 1-2. Maximal synthetic runs [start, end) as indices into `full`,
    # keeping only those long enough to be gaps.
    runs: List[Tuple[int, int]] = []
    i = 0
    n = full.size
    while i < n:
        if is_original[i]:
            i += 1
            continue
        run_start = i
        while i < n and not is_original[i]:
            i += 1
        if i - run_start >= min_missing:
            runs.append((run_start, i))

    # 3. Merge gaps separated by at most max_bridge measured points.
    merged: List[Tuple[int, int]] = []
    for run_start, run_end in runs:
        if merged:
            prev_start, prev_end = merged[-1]
            between = int(np.count_nonzero(is_original[prev_end:run_start]))
            if between <= max_bridge:
                merged[-1] = (prev_start, run_end)
                continue
        merged.append((run_start, run_end))

    spans: List[GapSpan] = []
    for run_start, run_end in merged:
        # generate_complete_timestamps' grid never extends past t[0]/t[-1],
        # so a synthetic run is always bounded by measured points on both
        # sides -- run_start - 1 and run_end are always valid indices.
        inside = is_original[run_start:run_end]
        bridged = int(np.count_nonzero(inside))
        spans.append(GapSpan(
            start_time=float(full[run_start - 1]),
            end_time=float(full[run_end]),
            missing_rows=int(inside.size - bridged),
            bridged_rows=bridged,
        ))
    return spans


# gaps.status value for a detected gap with no fill attempted yet, or
# whose fill failed. Distinct from analysis.base_comp's IMPUTED_* codes --
# those describe events.imputed_status (a row that exists); an unfilled
# gap produces no events row at all, so it has no counterpart there.
GAP_STATUS_UNFILLED = "U"

# gaps.status value for a gap whose fill the user removed in the review
# window: its events rows are deleted (same as unfilled), but the record
# is kept so automatic reruns leave it alone. Like UNFILLED, it has no
# events-column counterpart.
GAP_STATUS_REMOVED = "R"

# Statuses an automatic run replaces (and GapConfigDialog's Delete
# deletes) without / with "Include manual edits".
_AUTO_STATUSES: FrozenSet[str] = frozenset({IMPUTED_AUTO, GAP_STATUS_UNFILLED})
_ALL_STATUSES: FrozenSet[str] = _AUTO_STATUSES | {IMPUTED_MANUAL, GAP_STATUS_REMOVED}

# Statuses that record a user decision -- kept unless "Include manual edits".
MANUAL_STATUSES: FrozenSet[str] = frozenset({IMPUTED_MANUAL, GAP_STATUS_REMOVED})


def replaceable_statuses(include_manual: bool) -> FrozenSet[str]:
    """Gap statuses an automatic run replaces, and GapConfigDialog's
    Delete deletes: automatic and unfilled gaps, plus manually filled and
    removed gaps when *include_manual*."""
    return _ALL_STATUSES if include_manual else _AUTO_STATUSES


# ── Result container ────────────────────────────────────────────────────────

@dataclass
class GapResult:
    """One detected gap in a single trajectory, per (event_id, veh_id, gap_id).

    gap_id is 1-based, assigned in chronological (start_time) order within
    each trajectory -- a stable identity independent of how a caller later
    chooses to sort gaps for display. Detection reads measured data only,
    so the same gap definition finds the same gaps on every run; a rerun
    with a different definition may number them differently, and
    GapConfigDialog renumbers kept gaps to stay chronological.

    bridged_rows counts the measured points inside a merged gap (see
    find_gaps) -- the fill replaces them, and their measured values are
    kept in gap_superseded_points until the fill is cleared.

    spline_fitting_base/s/k, added_points_json, and status describe a
    fill that hasn't happened yet at detection time (all None / status
    defaulted) -- they're populated once the config dialog's default fill
    or the review dialog's manual refinement runs. status defaults to
    GAP_STATUS_UNFILLED and is set to IMPUTED_AUTO/IMPUTED_MANUAL once a fill
    actually succeeds for this gap -- it reuses analysis.base_comp's codes for the
    two "succeeded" cases so a filled gap's row here agrees with the events
    rows it produced, but GAP_STATUS_UNFILLED and GAP_STATUS_REMOVED have no
    events-column counterpart, since such a gap has no events rows.
    """
    event_id: int
    veh_id: int
    gap_id: int
    start_time: float
    end_time: float
    missing_rows: int
    bridged_rows: int = 0

    spline_fitting_base: Optional[str] = None
    spline_s: Optional[float] = None
    spline_k: Optional[int] = None
    added_points_json: Optional[str] = None

    status: str = GAP_STATUS_UNFILLED

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def overlaps(self, other: "GapResult") -> bool:
        """True if the two gaps (same vehicle assumed) share any time
        strictly inside both spans -- touching at a boundary point
        doesn't count."""
        return self.start_time < other.end_time and other.start_time < self.end_time


# ── Schema ───────────────────────────────────────────────────────────────────

_GAPS_TABLE_COLUMNS: List[Tuple[str, str]] = [
    ("event_id",          "INTEGER NOT NULL"),
    ("veh_id",             "INTEGER NOT NULL"),
    ("gap_id",              "INTEGER NOT NULL"),
    ("start_time",          "REAL NOT NULL"),
    ("end_time",            "REAL NOT NULL"),
    ("missing_rows",        "INTEGER NOT NULL"),
    ("bridged_rows",        "INTEGER NOT NULL DEFAULT 0"),
    ("spline_fitting_base", "TEXT"),
    ("spline_s",             "REAL"),
    ("spline_k",              "INTEGER"),
    ("added_points_json",     "TEXT"),
    ("status",                 "TEXT NOT NULL"),
]

# Measured values of points a gap fill replaced (bridged points of merged
# gaps) -- see the module docstring. One row per replaced events row; no
# gap_id, so renumbering gaps never touches it -- a gap's superseded
# points are the ones strictly inside its time span.
SUPERSEDED_TABLE = "gap_superseded_points"
_SUPERSEDED_COLUMNS: List[Tuple[str, str]] = [
    ("event_id",      "INTEGER NOT NULL"),
    ("veh_id",        "INTEGER NOT NULL"),
    ("veh_time",      "REAL NOT NULL"),
    ("lane_id",       ""),      # untyped: stored exactly as events holds it
    ("veh_dist_trav", "REAL"),
    ("veh_speed",     "REAL"),
    ("veh_accel",     "REAL"),
    ("veh_jerk",      "REAL"),
]
_SUPERSEDED_VALUE_COLS = ("lane_id", "veh_dist_trav", "veh_speed", "veh_accel", "veh_jerk")
_OPTIONAL_KINEMATIC_COLS = ("veh_speed", "veh_accel", "veh_jerk")


def _table_exists(db_connection: sqlite3.Connection, name: str) -> bool:
    return db_connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def gaps_table_exists(db_connection: sqlite3.Connection) -> bool:
    """True once GapInterpolationComp.ensure_schema has created the gaps
    table. Readers check this instead of creating it, so merely opening
    the dialog never modifies the database before the user has chosen
    where changes are written."""
    return _table_exists(db_connection, "gaps")


def _gaps_columns(db_connection: sqlite3.Connection) -> set:
    return {row[1] for row in db_connection.execute("PRAGMA table_info(gaps)").fetchall()}


def _ensure_gap_storage(db_connection: sqlite3.Connection) -> None:
    """Bring an existing gap store up to date, without committing: add
    gaps.bridged_rows to a gaps table created before gap merging, and
    create gap_superseded_points. Called by the write helpers, so a
    database whose gaps table predates these still works when written to
    from the review window (which doesn't call ensure_schema). Only ever
    runs on a write path, after the write target has been resolved."""
    if gaps_table_exists(db_connection) and "bridged_rows" not in _gaps_columns(db_connection):
        db_connection.execute(
            "ALTER TABLE gaps ADD COLUMN bridged_rows INTEGER NOT NULL DEFAULT 0")
    if not _table_exists(db_connection, SUPERSEDED_TABLE):
        cols_sql = ",\n    ".join(
            f"{name} {affinity}".rstrip() for name, affinity in _SUPERSEDED_COLUMNS)
        db_connection.execute(
            f"CREATE TABLE {SUPERSEDED_TABLE} (\n    {cols_sql},\n"
            f"    PRIMARY KEY (event_id, veh_id, veh_time)\n"
            f") WITHOUT ROWID;"
        )


def _original_rows_clause(db_connection: sqlite3.Connection) -> Tuple[str, list]:
    """(sql_fragment, params) restricting events to original measurements.
    Empty when imputed_status doesn't exist yet -- then every row is an
    original measurement."""
    if IMPUTED_STATUS_COL not in events_columns(db_connection):
        return "", []
    return f" AND {IMPUTED_STATUS_COL} = ?", [IMPUTED_ORIGINAL]


def _measured_source(db_connection: sqlite3.Connection) -> Tuple[str, list]:
    """(sql, params): a parenthesised derived table of every measured point
    -- original events rows plus superseded points -- with columns
    event_id, veh_id, veh_time, lane_id, veh_dist_trav, veh_speed,
    veh_accel, veh_jerk (NULL where events lacks the column). Use as
    ``SELECT ... FROM {sql} WHERE ...`` with *params* placed before the
    outer query's own parameters.

    This is the single definition of "measured data" for detection and
    fitting: a fill that replaced measured points doesn't change either.
    """
    cols = events_columns(db_connection)

    def col(name: str) -> str:
        return f"{name} AS {name}" if name in cols else f"NULL AS {name}"

    select = ", ".join(["event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav",
                        *[col(c) for c in _OPTIONAL_KINEMATIC_COLS]])
    original, params = _original_rows_clause(db_connection)
    sql = f"SELECT {select} FROM events WHERE 1 = 1{original}"
    if _table_exists(db_connection, SUPERSEDED_TABLE):
        sql += (f" UNION ALL SELECT event_id, veh_id, veh_time, lane_id, veh_dist_trav, "
                f"veh_speed, veh_accel, veh_jerk FROM {SUPERSEDED_TABLE}")
    return f"({sql})", params


# ── Gap fill (spline evaluation at each gap's missing timestamps) ──────────

_ORDER = {"jerk": 3, "acceleration": 2, "speed": 1, "position": 0}
_MEASURED_COL: Dict[str, str] = {
    "position":     "veh_dist_trav",
    "speed":        "veh_speed",
    "acceleration": "veh_accel",
    "jerk":         "veh_jerk",
}


@dataclass
class FillRow:
    """One row of gap-fill output, ready to write into events.

    Unlike SplineRowResult's calc_* columns (a parallel signal kept
    alongside real data for comparison), these values go into the real
    measured columns themselves -- see the "fill directly into normal
    columns" decision. gap_id is carried through for the caller's
    bookkeeping (e.g. which gaps table row a fill run actually produced
    rows for) but has no events column of its own.
    """
    event_id: int
    veh_id: int
    veh_time: float
    gap_id: int
    lane_id: Optional[int]
    veh_dist_trav: float
    veh_speed: float = float("nan")
    veh_accel: float = float("nan")
    veh_jerk: float = float("nan")


def _col_array(rows, idx: int, present: bool) -> np.ndarray:
    if not present:
        return np.full(len(rows), np.nan)
    return np.array([np.nan if r[idx] is None else r[idx] for r in rows], dtype=np.float64)


def _gap_fill_timestamps(times: np.ndarray, gap: GapResult,
                         time_step: Optional[float] = None) -> np.ndarray:
    """Every timestamp a fill of *gap* writes: the synthetic (missing)
    timestamps inside its span plus, for a merged gap, the measured
    timestamps it bridges -- everything strictly between its two boundary
    points on the completed grid.

    Cheap and deterministic -- generate_complete_timestamps() is pure --
    so this avoids carrying a redundant timestamp list on GapResult,
    which mirrors a gaps table row (Figure 5) and has no such column.
    """
    full = generate_complete_timestamps(times, time_step)
    mask = (full > gap.start_time) & (full < gap.end_time)
    return full[mask]


def count_valid_points_by_vehicle(
    db_connection: sqlite3.Connection,
    event_id: int,
    veh_ids: Iterable[int],
    fitting_base: str,
) -> Dict[int, int]:
    """Cheap, fit-free count of usable points per vehicle for
    *fitting_base* -- the same population compute_fill's time_valid/
    base_valid would filter down to (measured data, superseded points
    included), without any of the spline math. Used by GapConfigDialog's
    live viability label, so a k choice's consequences are visible before
    Apply is clicked.
    """
    measured_col = _MEASURED_COL[fitting_base]
    veh_ids = list(veh_ids)
    if not veh_ids:
        return {}

    extra = "" if measured_col == "veh_dist_trav" else f" AND {measured_col} IS NOT NULL"
    source, source_params = _measured_source(db_connection)
    placeholders = ",".join("?" for _ in veh_ids)
    cursor = db_connection.cursor()
    cursor.execute(
        f"SELECT veh_id, COUNT(*) FROM {source} "
        f"WHERE event_id = ? AND veh_id IN ({placeholders}) "
        f"AND veh_dist_trav IS NOT NULL{extra} "
        f"GROUP BY veh_id",
        (*source_params, event_id, *veh_ids),
    )
    counts = {veh_id: count for veh_id, count in cursor.fetchall()}
    return {veh_id: counts.get(veh_id, 0) for veh_id in veh_ids}


# ── BaseComp subclass ────────────────────────────────────────────────────────

class GapInterpolationComp(BaseComp):
    """Gap detection -- top-level Data > Gap and Interpolation.

    Options (see find_gaps):
        min_missing  missing points that identify a gap (>= 1)
        max_bridge   existing points ignored to merge adjacent gaps (>= 0)
    """

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "min_missing": DEFAULT_MIN_MISSING,
        "max_bridge":  DEFAULT_MAX_BRIDGE,
    }

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        """Create the gaps and gap_superseded_points tables if absent
        (adding gaps.bridged_rows to an older gaps table), and ensure the
        shared imputed_status column exists on events (analysis/base_comp.py
        -- also added by SplineComp.ensure_schema; safe to call from
        either). Modifies the database, so callers run it only after the
        write target has been resolved."""
        ensure_imputed_status_column(conn)

        if not gaps_table_exists(conn):
            cols_sql = ",\n    ".join(
                f"{name} {affinity}" for name, affinity in _GAPS_TABLE_COLUMNS
            )
            conn.execute(
                f"CREATE TABLE gaps (\n    {cols_sql},\n"
                f"    PRIMARY KEY (event_id, veh_id, gap_id)\n"
                f") WITHOUT ROWID;"
            )
        _ensure_gap_storage(conn)
        conn.commit()

    def comp_series(
        self,
        db_connection: sqlite3.Connection,
        event_id: int,
        veh_ids: Optional[Iterable[int]],
        options: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> List[GapResult]:
        options = self._resolve_options(options)
        min_missing = options["min_missing"]
        max_bridge = options["max_bridge"]

        if veh_ids is None:
            veh_ids = self._event_veh_ids(db_connection, event_id)
        else:
            veh_ids = list(veh_ids)
        if not veh_ids:
            return []

        placeholders = ",".join("?" for _ in veh_ids)
        source, source_params = _measured_source(db_connection)
        cursor = db_connection.cursor()
        # Measured data only (original rows plus superseded points): this
        # module's own fills (IMPUTED_AUTO / IMPUTED_MANUAL) still count as
        # gaps, so a rerun with the same gap definition detects the same
        # gaps whatever their fill state. SplineComp's synthetic rows
        # (IMPUTED_SPLINE) are excluded too -- they occupy a timestamp slot
        # but carry no measurement. veh_dist_trav IS NOT NULL also covers
        # databases without the imputed_status column yet.
        cursor.execute(
            f"SELECT veh_id, veh_time FROM {source} "
            f"WHERE event_id = ? AND veh_id IN ({placeholders}) "
            f"AND veh_dist_trav IS NOT NULL "
            f"ORDER BY veh_id, veh_time",
            (*source_params, event_id, *veh_ids),
        )

        by_veh: Dict[int, List[float]] = defaultdict(list)
        for veh_id, veh_time in cursor.fetchall():
            by_veh[veh_id].append(veh_time)

        step = event_time_step(db_connection, event_id)
        results: List[GapResult] = []
        for veh_id in veh_ids:
            times = by_veh.get(veh_id, [])
            spans = find_gaps(np.asarray(times, dtype=np.float64), step,
                              min_missing=min_missing, max_bridge=max_bridge)
            for gap_id, span in enumerate(spans, start=1):
                results.append(GapResult(
                    event_id=event_id, veh_id=veh_id, gap_id=gap_id,
                    start_time=span.start_time, end_time=span.end_time,
                    missing_rows=span.missing_rows, bridged_rows=span.bridged_rows,
                ))
            if progress_callback is not None:
                progress_callback()

        return results

    def compute_fill(
        self,
        db_connection: sqlite3.Connection,
        event_id: int,
        veh_id: int,
        gaps: List[GapResult],
        fitting_base: str,
        s: float,
        k: int,
        added_points: Optional[Dict[int, List[Tuple[float, float]]]] = None,
        time_step: Optional[float] = None,
    ) -> List[FillRow]:
        """Spline-fit *veh_id*'s trajectory once and evaluate it at every
        fill timestamp across *gaps* (all gaps must belong to this one
        vehicle): each gap's missing timestamps and, for a merged gap, the
        measured timestamps it bridges. Mirrors SplineComp.comp_series'
        fitting steps but writes the four kinematic values as real fill
        data, not a calc_* comparison pair.

        The fit uses all of the trajectory's measured data, bridged points
        included -- they shape the curve even though the fill replaces
        them.

        Gaps with too few valid points for the chosen k are silently
        skipped, same convention as SplineComp.comp_series -- a caller
        that needs to know why can diff gap.gap_id against the gap_id
        values present in the returned rows.
        """
        if fitting_base not in _MEASURED_COL:
            raise ValueError(f"Unknown fitting_base: {fitting_base!r}")
        if not gaps:
            return []

        columns = self._events_columns(db_connection)
        has_speed = "veh_speed" in columns
        has_accel = "veh_accel" in columns
        has_jerk  = "veh_jerk"  in columns

        select_cols = ["veh_time", "veh_dist_trav", "lane_id"]
        if has_speed:
            select_cols.append("veh_speed")
        if has_accel:
            select_cols.append("veh_accel")
        if has_jerk:
            select_cols.append("veh_jerk")

        source, source_params = _measured_source(db_connection)
        cursor = db_connection.cursor()
        # Measured data only -- excluding IMPUTED_SPLINE, IMPUTED_AUTO and
        # IMPUTED_MANUAL rows keeps each gap's fit independent of every
        # other gap's fill state, so results don't depend on the order gaps
        # happen to be reviewed/applied in. Superseded points are measured
        # data whose events rows now hold a fill, so they come from
        # gap_superseded_points instead.
        cursor.execute(
            f"SELECT {', '.join(select_cols)} FROM {source} "
            f"WHERE event_id = ? AND veh_id = ? AND veh_dist_trav IS NOT NULL "
            f"ORDER BY veh_time",
            (*source_params, event_id, veh_id),
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        idx = {name: i for i, name in enumerate(select_cols)}
        t        = np.array([r[idx["veh_time"]] for r in rows], dtype=np.float64)
        dist     = np.array([r[idx["veh_dist_trav"]] for r in rows], dtype=np.float64)
        lane_ids = [r[idx["lane_id"]] for r in rows]
        speed    = _col_array(rows, idx.get("veh_speed", -1), has_speed)
        accel    = _col_array(rows, idx.get("veh_accel", -1), has_accel)
        jerk     = _col_array(rows, idx.get("veh_jerk", -1), has_jerk)

        level_arrays = {"position": dist, "speed": speed, "acceleration": accel}
        base_arrays  = {"position": dist, "speed": speed, "acceleration": accel, "jerk": jerk}
        base_mask  = ~np.isnan(base_arrays[fitting_base])
        time_valid = t[base_mask]
        base_valid = base_arrays[fitting_base][base_mask]

        if time_valid.size < k + 1:
            logger.warning(
                "GapInterpolationComp: event=%s veh=%s skipped -- "
                "only %d valid '%s' points (need %d for k=%d).",
                event_id, veh_id, time_valid.size, fitting_base, k + 1, k,
            )
            return []

        if time_step is None:
            time_step = event_time_step(db_connection, event_id)

        fill_rows: List[FillRow] = []
        for gap in gaps:
            target_t = _gap_fill_timestamps(t, gap, time_step)
            if target_t.size == 0:
                continue

            gap_time_valid, gap_base_valid = time_valid, base_valid
            extra = (added_points or {}).get(gap.gap_id)
            if extra:
                # User-added guide points from GapPointEditor -- knots to
                # shape the curve, not new observations (see FillRow's
                # docstring); unioned in for this gap's fit only.
                extra_t = np.array([p[0] for p in extra], dtype=np.float64)
                extra_v = np.array([p[1] for p in extra], dtype=np.float64)
                order = np.argsort(np.concatenate([time_valid, extra_t]))
                gap_time_valid = np.concatenate([time_valid, extra_t])[order]
                gap_base_valid = np.concatenate([base_valid, extra_v])[order]

            # Anchored at the gap's start_time (a real, measured boundary
            # point) rather than the trajectory's global first-valid
            # point -- SplineComp anchors globally because it fits/
            # evaluates the whole trajectory at once; a per-gap fill is
            # better served by the nearest real data to that gap.
            initial_conditions = {
                name: ic
                for name, arr in level_arrays.items()
                if _ORDER[name] < _ORDER[fitting_base]
                for ic in [nearest_valid_ic(t, arr, gap.start_time)]
                if ic is not None
            }
            fit = apply_spline_fit(
                gap_time_valid, gap_base_valid, fitting_base, s, k,
                target_timestamps=target_t,
                initial_conditions=initial_conditions,
            )
            if fit is None:
                logger.warning(
                    "GapInterpolationComp: fit failed for event=%s "
                    "veh=%s gap=%s.", event_id, veh_id, gap.gap_id,
                )
                continue

            # Lane inherited from the nearest EARLIER measured record, same
            # convention SplineComp uses for its synthetic rows. A bridged
            # point is its own nearest earlier record, so it keeps its lane.
            idx_earlier = np.clip(np.searchsorted(t, target_t, side="right") - 1, 0, t.size - 1)

            for i, tt in enumerate(target_t):
                fill_rows.append(FillRow(
                    event_id=event_id, veh_id=veh_id, veh_time=float(tt),
                    gap_id=gap.gap_id,
                    lane_id=lane_ids[idx_earlier[i]],
                    veh_dist_trav=fit["calc_position"][i],
                    veh_speed=fit["calc_speed"][i]        if has_speed else float("nan"),
                    veh_accel=fit["calc_acceleration"][i] if has_accel else float("nan"),
                    veh_jerk=fit["calc_jerk"][i]          if has_jerk  else float("nan"),
                ))

        return fill_rows

# ── Persistence (shared by GapConfigDialog and GapReviewDialog) ────────────

GAPS_INSERT_COLUMNS = [
    "event_id", "veh_id", "gap_id", "start_time", "end_time", "missing_rows",
    "spline_fitting_base", "spline_s", "spline_k", "added_points_json", "status",
    "bridged_rows",
]


def _nan_to_none(x):
    return None if isinstance(x, float) and math.isnan(x) else x


def _supersede_measured(db_connection: sqlite3.Connection, fill_rows: List[FillRow]) -> None:
    """Save the measured values of every original events row a fill is
    about to overwrite (a merged gap's bridged points) into
    gap_superseded_points, flagging that measurement as removed. Rows that
    are already fills (a refill) or SplineComp synthetic rows have no
    measurement and are skipped; INSERT OR IGNORE keeps the first saved
    values if a point is somehow saved twice. Does not commit."""
    _ensure_gap_storage(db_connection)
    cols = events_columns(db_connection)
    kinematics = ", ".join(c if c in cols else "NULL" for c in _OPTIONAL_KINEMATIC_COLS)
    original, original_params = _original_rows_clause(db_connection)
    db_connection.executemany(
        f"INSERT OR IGNORE INTO {SUPERSEDED_TABLE} "
        f"(event_id, veh_id, veh_time, {', '.join(_SUPERSEDED_VALUE_COLS)}) "
        f"SELECT event_id, veh_id, veh_time, lane_id, veh_dist_trav, {kinematics} "
        f"FROM events WHERE event_id = ? AND veh_id = ? AND veh_time = ?{original}",
        [(r.event_id, r.veh_id, r.veh_time, *original_params) for r in fill_rows],
    )


def _restore_superseded(db_connection: sqlite3.Connection, event_id: int,
                        veh_id: Optional[int] = None,
                        start_time: Optional[float] = None,
                        end_time: Optional[float] = None) -> int:
    """Write superseded measured values back into their events rows as
    IMPUTED_ORIGINAL and drop their gap_superseded_points records -- for
    one event, optionally narrowed to one vehicle and the open interval
    (start_time, end_time). Returns the number of points restored. Does
    not commit.

    UPDATE-in-place (via UPSERT) rather than delete-and-insert, so the
    row's other columns -- import columns like veh_length, analysis
    columns -- survive; the UPSERT's INSERT branch only runs if the row
    has gone missing, which shouldn't happen."""
    if not _table_exists(db_connection, SUPERSEDED_TABLE):
        return 0
    where = "event_id = ?"
    params: list = [event_id]
    if veh_id is not None:
        where += " AND veh_id = ?"
        params.append(veh_id)
    if start_time is not None and end_time is not None:
        where += " AND veh_time > ? AND veh_time < ?"
        params += [start_time, end_time]

    saved = db_connection.execute(
        f"SELECT event_id, veh_id, veh_time, {', '.join(_SUPERSEDED_VALUE_COLS)} "
        f"FROM {SUPERSEDED_TABLE} WHERE {where}", params).fetchall()
    if not saved:
        return 0

    cols = events_columns(db_connection)
    kinematics = [c for c in _OPTIONAL_KINEMATIC_COLS if c in cols]
    insert_cols = ["event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav",
                   *kinematics, IMPUTED_STATUS_COL]
    update_cols = insert_cols[3:]
    value_index = {name: 3 + i for i, name in enumerate(_SUPERSEDED_VALUE_COLS)}
    db_connection.executemany(
        f"INSERT INTO events ({', '.join(insert_cols)}) "
        f"VALUES ({','.join('?' for _ in insert_cols)}) "
        f"ON CONFLICT(event_id, veh_id, veh_time) DO UPDATE SET "
        f"{', '.join(f'{c} = excluded.{c}' for c in update_cols)}",
        [
            (row[0], row[1], row[2], row[value_index["lane_id"]],
             row[value_index["veh_dist_trav"]],
             *[row[value_index[c]] for c in kinematics],
             IMPUTED_ORIGINAL)
            for row in saved
        ],
    )
    db_connection.execute(f"DELETE FROM {SUPERSEDED_TABLE} WHERE {where}", params)
    return len(saved)


def write_fill_rows(db_connection: sqlite3.Connection, fill_rows: List[FillRow], status: str) -> None:
    """UPSERT fill_rows into events, tagging every row with *status*.

    A plain INSERT would collide whenever a gap's timestamps already hold
    a row -- a prior fill (this gap's own earlier auto-fill), a SplineComp
    synthetic row occupying the same slot, or a measured point a merged
    gap bridges; INSERT OR REPLACE would delete-and-recreate the row,
    silently wiping any calc_* values SplineComp had written there and any
    other columns of a measured row. ON CONFLICT ... DO UPDATE touches
    only the columns this module owns.

    A measured row about to be overwritten is first saved to
    gap_superseded_points (_supersede_measured), so the fill supersedes
    the measurement without losing it.

    Shared by GapConfigDialog (bulk write, status=IMPUTED_AUTO) and
    GapReviewDialog (one gap at a time, status=IMPUTED_MANUAL) so both
    write paths stay in sync. Does not commit.
    """
    if not fill_rows:
        return
    _supersede_measured(db_connection, fill_rows)

    cols = events_columns(db_connection)
    optional = [c for c in _OPTIONAL_KINEMATIC_COLS if c in cols]

    insert_cols = ["event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav",
                   *optional, IMPUTED_STATUS_COL]
    update_cols = [c for c in insert_cols if c not in ("event_id", "veh_id", "veh_time")]
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    placeholders = ",".join("?" for _ in insert_cols)
    sql = (
        f"INSERT INTO events ({', '.join(insert_cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(event_id, veh_id, veh_time) DO UPDATE SET {set_clause}"
    )

    db_connection.executemany(
        sql,
        [
            (
                r.event_id, r.veh_id, r.veh_time, r.lane_id, r.veh_dist_trav,
                *[_nan_to_none(getattr(r, c)) for c in optional],
                status,
            )
            for r in fill_rows
        ],
    )


def write_gap_rows(db_connection: sqlite3.Connection, gaps: List[GapResult]) -> None:
    """INSERT OR REPLACE each gap's current state (params, status) into
    the gaps table. REPLACE is safe here (unlike for events, above) --
    a gaps row has no sibling columns another module writes into, so
    there's nothing a full-row replace could clobber. Does not commit."""
    if not gaps:
        return
    _ensure_gap_storage(db_connection)
    placeholders = ",".join("?" for _ in GAPS_INSERT_COLUMNS)
    db_connection.executemany(
        f"INSERT OR REPLACE INTO gaps ({', '.join(GAPS_INSERT_COLUMNS)}) VALUES ({placeholders})",
        [
            (g.event_id, g.veh_id, g.gap_id, g.start_time, g.end_time, g.missing_rows,
             g.spline_fitting_base, g.spline_s, g.spline_k, g.added_points_json, g.status,
             g.bridged_rows)
            for g in gaps
        ],
    )


def delete_gap_row(db_connection: sqlite3.Connection, event_id: int, veh_id: int,
                   gap_id: int) -> None:
    """Delete one gaps-table record only (its events rows untouched) --
    used when renumbering a kept gap. Does not commit."""
    db_connection.execute(
        "DELETE FROM gaps WHERE event_id = ? AND veh_id = ? AND gap_id = ?",
        (event_id, veh_id, gap_id),
    )


def clear_gap_fill(db_connection: sqlite3.Connection, gap: GapResult) -> None:
    """Undo this module's fill inside *gap*'s span (strictly between its
    two measured boundary timestamps), leaving the gap missing again:
    measured points the fill superseded get their measured values back,
    then the remaining fill rows (IMPUTED_AUTO / IMPUTED_MANUAL) are
    deleted. Original rows and SplineComp's synthetic rows are untouched.
    Does not commit."""
    _restore_superseded(db_connection, gap.event_id, gap.veh_id,
                        gap.start_time, gap.end_time)
    db_connection.execute(
        f"DELETE FROM events WHERE event_id = ? AND veh_id = ? "
        f"AND veh_time > ? AND veh_time < ? AND {IMPUTED_STATUS_COL} IN (?, ?)",
        (gap.event_id, gap.veh_id, gap.start_time, gap.end_time,
         IMPUTED_AUTO, IMPUTED_MANUAL),
    )


def delete_gaps(db_connection: sqlite3.Connection, gaps: Iterable[GapResult]) -> None:
    """Delete each gap's gaps-table record and undo its fill, so the gap is
    detected as new by the next automatic run. Does not commit -- callers
    commit (or roll back) the whole batch."""
    for gap in gaps:
        clear_gap_fill(db_connection, gap)
        delete_gap_row(db_connection, gap.event_id, gap.veh_id, gap.gap_id)


def _exclude_removed_vehicles(db_connection: sqlite3.Connection, event_id: int):
    """(sql_fragment, params) excluding gaps on removed trajectories, so the
    review dialog can never navigate to -- or Apply a fill into -- a
    trajectory consumed by a stitch."""
    from utils import TRAJ_STATUS_COL, TRAJ_REMOVED
    if TRAJ_STATUS_COL not in events_columns(db_connection):
        return "", []
    return (
        f" AND veh_id NOT IN (SELECT DISTINCT veh_id FROM events "
        f"WHERE event_id = ? AND {TRAJ_STATUS_COL} = ?)",
        [event_id, TRAJ_REMOVED],
    )

def event_has_gap_records(db_connection: sqlite3.Connection, event_id: int) -> bool:
    """True if the gaps table already has at least one row for *event_id*
    -- i.e. a previous GapConfigDialog Apply (or GapReviewDialog Apply)
    has run for this event. Drives GapConfigDialog's Skip button.
    """
    if not gaps_table_exists(db_connection):
        return False
    cursor = db_connection.cursor()
    extra, params = _exclude_removed_vehicles(db_connection, event_id)
    cursor.execute(f"SELECT 1 FROM gaps WHERE event_id = ?{extra} LIMIT 1",
                   [event_id, *params])
    return cursor.fetchone() is not None


def read_gap_rows(db_connection: sqlite3.Connection, event_id: int) -> List[GapResult]:
    """Reconstruct GapResult objects from the persisted gaps table for
    *event_id*. This is the single source of truth GapReviewDialog reads
    from -- opened via GapConfigDialog's Apply or via Skip, it sees
    exactly the gaps, spline parameters, and statuses a previous run
    left behind, since neither path hands it an in-memory list anymore.
    Empty when the gaps table doesn't exist yet. Read-only: a gaps table
    created before gap merging reads bridged_rows as 0.
    """
    if not gaps_table_exists(db_connection):
        return []
    select = [c if c != "bridged_rows" or "bridged_rows" in _gaps_columns(db_connection)
              else "0" for c in GAPS_INSERT_COLUMNS]
    cursor = db_connection.cursor()
    extra, params = _exclude_removed_vehicles(db_connection, event_id)
    cursor.execute(
        f"SELECT {', '.join(select)} FROM gaps "
        f"WHERE event_id = ?{extra} ORDER BY veh_id, gap_id",
        [event_id, *params],
    )
    return [
        GapResult(
            event_id=row[0], veh_id=row[1], gap_id=row[2],
            start_time=row[3], end_time=row[4], missing_rows=row[5],
            spline_fitting_base=row[6], spline_s=row[7], spline_k=row[8],
            added_points_json=row[9], status=row[10], bridged_rows=row[11] or 0,
        )
        for row in cursor.fetchall()
    ]


def delete_gap_records(db_connection: sqlite3.Connection, event_id: int) -> None:
    """Delete every gaps-table row for *event_id*, restore every measured
    point a fill superseded in that event, and delete every remaining
    events row this module filled (IMPUTED_AUTO/IMPUTED_MANUAL); commit.
    Rows with IMPUTED_ORIGINAL or IMPUTED_SPLINE (SplineComp's own
    synthetic rows) are untouched -- this module only ever owns rows it
    tagged itself.

    No longer used by the dialogs, which delete per gap (delete_gaps) so
    manual edits can be kept; retained for callers that want a full reset.
    """
    if not gaps_table_exists(db_connection):
        return
    _restore_superseded(db_connection, event_id)
    db_connection.execute(
        f"DELETE FROM events WHERE event_id = ? AND {IMPUTED_STATUS_COL} IN (?, ?)",
        (event_id, IMPUTED_AUTO, IMPUTED_MANUAL),
    )
    db_connection.execute("DELETE FROM gaps WHERE event_id = ?", (event_id,))
    db_connection.commit()
