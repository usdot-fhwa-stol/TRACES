"""Gap detection for the Missing Data Identification and Interpolation
module -- top-level Data > Missing Data and Interpolation (Task9.1).

comp_series() is detection only: it reports where gaps are in each
trajectory's timestamp sequence, it does not fill them. The spec's
two-stage flow (detect -> configure defaults -> Apply -> per-gap review)
keeps filling as a separate, later action, so this module mirrors that
split rather than doing detection+fill in one pass the way SplineComp
does -- gap reporting is useful on its own (Task9.1's "detect... 
automatically" objective is distinct from "generate corrected trajectory
values").

The fill itself is compute_fill(), below: it calls 
comp.spline.spline_comp.apply_spline_fit() directly per gap 
(imported, not reimplemented) and is persisted through 
comp/missing_data_interpolation/gap_config_dialog.py and gap_review_dialog.py, 
following the same comp-computes / dialog-persists split used by every other 
analysis module in this codebase.
"""

import sqlite3
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from ..base_comp import (
    BaseComp, ensure_imputed_status_column, IMPUTED_AUTO, IMPUTED_MANUAL, IMPUTED_ORIGINAL,
)
from comp.spline.spline_comp import (
    generate_complete_timestamps, apply_spline_fit, nearest_valid_ic,
)

logger = logging.getLogger(__name__)


# ── Gap span detection ──────────────────────────────────────────────────────

@dataclass
class GapSpan:
    """A single detected run of missing timestamps within one trajectory,
    before it's attached to an (event_id, veh_id, gap_id) identity."""
    start_time: float   # last real timestamp before the gap
    end_time: float     # first real timestamp after the gap
    missing_rows: int   # count of expected-but-absent timestamps in between


def find_gaps(time_array: np.ndarray) -> List[GapSpan]:
    """Detect missing-timestamp runs in one trajectory's time values.

    Builds on SplineComp.generate_complete_timestamps(), which infers the
    expected sample step from the smallest observed positive inter-sample
    gap and returns original timestamps merged with a synthetic grid
    filling any gaps. This function adds the piece that function doesn't
    need for its own purpose (completing calc_*'s timing grid) but this
    module does: grouping the synthetic points into discrete spans, each
    bounded by the real timestamps on either side.

    Returns an empty list for trajectories with fewer than two distinct
    timestamps (no step can be inferred) or with no missing timestamps.
    """
    t = np.unique(np.asarray(time_array, dtype=np.float64))
    if t.size < 2:
        return []

    full = generate_complete_timestamps(t)
    if full.size == t.size:
        return []   # nothing synthetic -- no gaps

    # Exact equality is safe here: `full` is t concatenated with a
    # separately-constructed grid, so every original value survives
    # unchanged (see generate_complete_timestamps's docstring).
    is_original = np.isin(full, t)

    spans: List[GapSpan] = []
    i = 0
    n = full.size
    while i < n:
        if is_original[i]:
            i += 1
            continue
        run_start = i
        while i < n and not is_original[i]:
            i += 1
        run_end = i   # exclusive
        # generate_complete_timestamps' grid never extends past t[0]/t[-1],
        # so a synthetic run is always bounded by real points on both sides
        # -- run_start - 1 and run_end are always valid indices into `full`.
        spans.append(GapSpan(
            start_time=float(full[run_start - 1]),
            end_time=float(full[run_end]),
            missing_rows=run_end - run_start,
        ))
    return spans


# gaps.status value for a detected gap with no fill attempted yet, or
# whose fill failed. Distinct from comp.base_comp's IMPUTED_* codes --
# those describe events.imputed_status (a row that exists); an unfilled
# gap produces no events row at all, so it has no counterpart there.
GAP_STATUS_UNFILLED = "U"


# ── Result container ────────────────────────────────────────────────────────

@dataclass
class GapResult:
    """One detected gap in a single trajectory, per (event_id, veh_id, gap_id).

    gap_id is 1-based, assigned in chronological (start_time) order within
    each trajectory -- a stable identity independent of how a caller later
    chooses to sort gaps for display.

    spline_fitting_base/s/k, added_points_json, and status describe a
    fill that hasn't happened yet at detection time (all None / status
    defaulted) -- they're populated once the config dialog's default fill
    or the review dialog's manual refinement runs. status defaults to 
    GAP_STATUS_UNFILLED and is set to IMPUTED_AUTO/IMPUTED_MANUAL once a fill 
    actually succeeds for this gap -- it reuses comp.base_comp's codes for the 
    two "succeeded" cases so a filled gap's row here agrees with the events 
    rows it produced, but GAP_STATUS_UNFILLED has no events-column 
    counterpart, since an unfilled gap produces no events row.
    """
    event_id: int
    veh_id: int
    gap_id: int
    start_time: float
    end_time: float
    missing_rows: int

    spline_fitting_base: Optional[str] = None
    spline_s: Optional[float] = None
    spline_k: Optional[int] = None
    added_points_json: Optional[str] = None

    status: str = GAP_STATUS_UNFILLED

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


# ── Schema ───────────────────────────────────────────────────────────────────

_GAPS_TABLE_COLUMNS: List[Tuple[str, str]] = [
    ("event_id",          "INTEGER NOT NULL"),
    ("veh_id",             "INTEGER NOT NULL"),
    ("gap_id",              "INTEGER NOT NULL"),
    ("start_time",          "REAL NOT NULL"),
    ("end_time",            "REAL NOT NULL"),
    ("missing_rows",        "INTEGER NOT NULL"),
    ("spline_fitting_base", "TEXT"),
    ("spline_s",             "REAL"),
    ("spline_k",              "INTEGER"),
    ("added_points_json",     "TEXT"),
    ("status",                 "TEXT NOT NULL"),
]


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


def _gap_missing_timestamps(times: np.ndarray, gap: GapResult) -> np.ndarray:
    """Recompute the synthetic timestamps inside one gap's span.

    Cheap and deterministic -- generate_complete_timestamps() is pure --
    so this avoids carrying a redundant timestamp list on GapResult,
    which mirrors a gaps table row (Figure 5) and has no such column.
    """
    full = generate_complete_timestamps(times)
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
    base_valid would filter down to, without any of the spline math.
    Used by GapConfigDialog's live viability label, so a k choice's
    consequences are visible before Apply is clicked.
    """
    measured_col = _MEASURED_COL[fitting_base]
    veh_ids = list(veh_ids)
    if not veh_ids:
        return {}

    extra = "" if measured_col == "veh_dist_trav" else f" AND {measured_col} IS NOT NULL"
    placeholders = ",".join("?" for _ in veh_ids)
    cursor = db_connection.cursor()
    cursor.execute(
        f"SELECT veh_id, COUNT(*) FROM events "
        f"WHERE event_id = ? AND veh_id IN ({placeholders}) "
        f"AND veh_dist_trav IS NOT NULL{extra} "
        f"GROUP BY veh_id",
        (event_id, *veh_ids),
    )
    counts = {veh_id: count for veh_id, count in cursor.fetchall()}
    return {veh_id: counts.get(veh_id, 0) for veh_id in veh_ids}


# ── BaseComp subclass ────────────────────────────────────────────────────────

class MissingDataInterpolationComp(BaseComp):
    """Gap detection -- top-level Data > Missing Data and Interpolation."""

    OPTIONS_SCHEMA: Dict[str, Any] = {}

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        """Create the gaps table if absent, and ensure the shared
        imputed_status column exists on events (comp/base_comp.py -- also
        added by SplineComp.ensure_schema; safe to call from either)."""
        ensure_imputed_status_column(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gaps'"
        )
        if cursor.fetchone() is None:
            cols_sql = ",\n    ".join(
                f"{name} {affinity}" for name, affinity in _GAPS_TABLE_COLUMNS
            )
            conn.execute(
                f"CREATE TABLE gaps (\n    {cols_sql},\n"
                f"    PRIMARY KEY (event_id, veh_id, gap_id)\n"
                f") WITHOUT ROWID;"
            )
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

        if veh_ids is None:
            veh_ids = self._event_veh_ids(db_connection, event_id)
        else:
            veh_ids = list(veh_ids)
        if not veh_ids:
            return []

        placeholders = ",".join("?" for _ in veh_ids)
        cursor = db_connection.cursor()
        # veh_dist_trav IS NOT NULL excludes SplineComp's synthetic
        # (imputed_status = IMPUTED_SPLINE) rows -- those occupy a
        # timestamp slot but carry no real measurement, so they must
        # still be treated as a gap here, not as "already filled."
        cursor.execute(
            f"SELECT veh_id, veh_time FROM events "
            f"WHERE event_id = ? AND veh_id IN ({placeholders}) "
            f"AND veh_dist_trav IS NOT NULL "
            f"ORDER BY veh_id, veh_time",
            (event_id, *veh_ids),
        )

        by_veh: Dict[int, List[float]] = defaultdict(list)
        for veh_id, veh_time in cursor.fetchall():
            by_veh[veh_id].append(veh_time)

        results: List[GapResult] = []
        for veh_id in veh_ids:
            times = by_veh.get(veh_id, [])
            spans = find_gaps(np.asarray(times, dtype=np.float64))
            for gap_id, span in enumerate(spans, start=1):
                results.append(GapResult(
                    event_id=event_id, veh_id=veh_id, gap_id=gap_id,
                    start_time=span.start_time, end_time=span.end_time,
                    missing_rows=span.missing_rows,
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
    ) -> List[FillRow]:
        """Spline-fit *veh_id*'s trajectory once and evaluate it at every
        missing timestamp across *gaps* (all gaps must belong to this one
        vehicle). Mirrors SplineComp.comp_series' fitting steps but
        writes the four kinematic values as real fill data, not a calc_*
        comparison pair.

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

        cursor = db_connection.cursor()
        # imputed_status = IMPUTED_ORIGINAL restricts the fit to real
        # measurements only -- excluding IMPUTED_SPLINE, IMPUTED_AUTO, and
        # IMPUTED_MANUAL rows keeps each gap's fit independent of every other
        # gap's fill state, so results don't depend on the order gaps happen
        # to be reviewed/applied in.
        cursor.execute(
            f"SELECT {', '.join(select_cols)} FROM events "
            f"WHERE event_id = ? AND veh_id = ? AND veh_dist_trav IS NOT NULL "
            f"AND imputed_status = ? "
            f"ORDER BY veh_time",
            (event_id, veh_id, IMPUTED_ORIGINAL),
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
                "MissingDataInterpolationComp: event=%s veh=%s skipped -- "
                "only %d valid '%s' points (need %d for k=%d).",
                event_id, veh_id, time_valid.size, fitting_base, k + 1, k,
            )
            return []

        fill_rows: List[FillRow] = []
        for gap in gaps:
            target_t = _gap_missing_timestamps(t, gap)
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
                    "MissingDataInterpolationComp: fit failed for event=%s "
                    "veh=%s gap=%s.", event_id, veh_id, gap.gap_id,
                )
                continue

            # Lane inherited from the nearest EARLIER real record, same
            # convention SplineComp uses for its synthetic rows.
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
]


def _nan_to_none(x):
    return None if isinstance(x, float) and math.isnan(x) else x


def write_fill_rows(db_connection: sqlite3.Connection, fill_rows: List[FillRow], status: str) -> None:
    """UPSERT fill_rows into events, tagging every row with *status*.

    A plain INSERT would collide whenever a gap's timestamps already hold
    a prior fill (this gap's own earlier auto-fill, or a SplineComp
    synthetic row occupying the same slot); INSERT OR REPLACE would
    delete-and-recreate the row, silently wiping any calc_* values
    SplineComp had written there. ON CONFLICT ... DO UPDATE touches only
    the columns this module owns.

    Shared by GapConfigDialog (bulk write, status=IMPUTED_AUTO) and
    GapReviewDialog (one gap at a time, status=IMPUTED_MANUAL) so both
    write paths stay in sync -- previously each dialog built this SQL
    itself.
    """
    if not fill_rows:
        return
    from utils import events_columns
    cols = events_columns(db_connection)
    optional = [c for c in ("veh_speed", "veh_accel", "veh_jerk") if c in cols]

    insert_cols = ["event_id", "veh_id", "veh_time", "lane_id", "veh_dist_trav",
                   *optional, "imputed_status"]
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
    there's nothing a full-row replace could clobber."""
    if not gaps:
        return
    placeholders = ",".join("?" for _ in GAPS_INSERT_COLUMNS)
    db_connection.executemany(
        f"INSERT OR REPLACE INTO gaps ({', '.join(GAPS_INSERT_COLUMNS)}) VALUES ({placeholders})",
        [
            (g.event_id, g.veh_id, g.gap_id, g.start_time, g.end_time, g.missing_rows,
             g.spline_fitting_base, g.spline_s, g.spline_k, g.added_points_json, g.status)
            for g in gaps
        ],
    )

def event_has_gap_records(db_connection: sqlite3.Connection, event_id: int) -> bool:
    """True if the gaps table already has at least one row for *event_id*
    -- i.e. a previous GapConfigDialog Apply (or GapReviewDialog Apply)
    has run for this event. Drives GapConfigDialog's Skip button and its
    Apply-button start-over confirmation.
    """
    cursor = db_connection.cursor()
    cursor.execute("SELECT 1 FROM gaps WHERE event_id = ? LIMIT 1", (event_id,))
    return cursor.fetchone() is not None


def read_gap_rows(db_connection: sqlite3.Connection, event_id: int) -> List[GapResult]:
    """Reconstruct GapResult objects from the persisted gaps table for
    *event_id*. This is the single source of truth GapReviewDialog reads
    from -- opened via GapConfigDialog's Apply or via Skip, it sees
    exactly the gaps, spline parameters, and statuses a previous run
    left behind, since neither path hands it an in-memory list anymore.
    """
    cursor = db_connection.cursor()
    cursor.execute(
        f"SELECT {', '.join(GAPS_INSERT_COLUMNS)} FROM gaps "
        f"WHERE event_id = ? ORDER BY veh_id, gap_id",
        (event_id,),
    )
    return [
        GapResult(
            event_id=row[0], veh_id=row[1], gap_id=row[2],
            start_time=row[3], end_time=row[4], missing_rows=row[5],
            spline_fitting_base=row[6], spline_s=row[7], spline_k=row[8],
            added_points_json=row[9], status=row[10],
        )
        for row in cursor.fetchall()
    ]


def delete_gap_records(db_connection: sqlite3.Connection, event_id: int) -> None:
    """Delete *event_id*'s gaps-table rows and any events rows this
    module previously filled (IMPUTED_AUTO/IMPUTED_MANUAL), restoring
    the pre-fill state so a fresh GapConfigDialog Apply starts over
    cleanly. Rows with IMPUTED_ORIGINAL or IMPUTED_SPLINE (SplineComp's
    own synthetic rows) are untouched -- this module only ever owns rows
    it tagged itself.
    """
    db_connection.execute(
        "DELETE FROM events WHERE event_id = ? AND imputed_status IN (?, ?)",
        (event_id, IMPUTED_AUTO, IMPUTED_MANUAL),
    )
    db_connection.execute("DELETE FROM gaps WHERE event_id = ?", (event_id,))
    db_connection.commit()