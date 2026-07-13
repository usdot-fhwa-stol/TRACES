"""Spline fitting analysis — implements the module described in
SplineReport.

Fits a B-spline (scipy.interpolate.make_splrep) to one selected kinematic
variable ("fitting base") for a single trajectory, then derives the other
three kinematic quantities from that one spline via differentiation /
integration (see Table 1 of the report). Two write-back columns sets:

    calc_position, calc_speed, calc_acceleration, calc_jerk   (REAL)
    is_spline_imputed                                          (INTEGER)

are added to the events table. ``is_spline_imputed`` distinguishes rows
that already existed (measured data, calc_* sits alongside it) from rows
synthesized to fill timing gaps (calc_* is the only populated data; every
measured column is left NULL because there is no measurement).

This module is invocation-path agnostic:
    - SplineCompDialog (top-level, comp/spline/spline_comp_dialog.py) drives
      SplineComp.comp_series across one or more events via the worker
      thread, persists the row results, and shows SplineFitResult metrics.
    - SplineTestingDialog (right-click, comp/spline/spline_testing_dialog.py)
      calls apply_spline_fit() directly on a single in-memory trajectory and
      never writes to the database.

Position semantics
-------------------
"Position" means longitudinal distance traveled (``veh_dist_trav``), not
2-D map coordinates (``x_map_loc``/``y_map_loc``). Imputing physical
location from a 1-D kinematic spline isn't meaningful, so map coordinates
are out of scope for this module entirely.

Skipped trajectories
---------------------
comp_series() silently omits any (event_id, veh_id) with fewer than k+1
valid points for the selected base — no SplineRowResult is produced for
it. Callers that need to report *why* a requested vehicle produced no
rows (e.g. the stats dialog) are responsible for diffing the requested
veh_ids against the veh_ids actually present in the returned rows.
"""

import logging
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.interpolate import make_splrep

from ..base_comp import BaseComp

logger = logging.getLogger(__name__)

NAN = float("nan")
_ORDER = {"jerk": 3, "acceleration": 2, "speed": 1, "position": 0}
_LEVEL_NAME_BY_ORDER = {v: k for k, v in _ORDER.items()}

# ── Base/column registry ────────────────────────────────────────────────────

FITTING_BASES: Tuple[str, ...] = ("position", "speed", "acceleration", "jerk")
KINEMATICS: Tuple[str, ...] = FITTING_BASES  # same four quantities, different role

BASE_LABELS: Dict[str, str] = {
    "position":     "Position (Distance Traveled)",
    "speed":        "Speed",
    "acceleration": "Acceleration",
    "jerk":         "Jerk",
}

# Measured column read as the fitting target / used for goodness-of-fit comparison.
BASE_MEASURED_COLUMN: Dict[str, str] = {
    "position":     "veh_dist_trav",
    "speed":        "veh_speed",
    "acceleration": "veh_accel",
    "jerk":         "veh_jerk",
}

# Calculated column written back to the events table.
BASE_CALC_COLUMN: Dict[str, str] = {
    "position":     "calc_position",
    "speed":        "calc_speed",
    "acceleration": "calc_acceleration",
    "jerk":         "calc_jerk",
}

# Per SplineReport: degree must be >= this minimum for the chosen base, and
# even degrees are avoided (risk of discontinuities), which narrows the
# practically selectable set to {1, 3, 5}.
ALLOWED_K: Tuple[int, ...] = (1, 3, 5)
MIN_K_BY_BASE: Dict[str, int] = {
    "position":     3,
    "speed":        2,
    "acceleration": 1,
    "jerk":         0,
}

_COMPUTED_REAL_COLS = ["calc_position", "calc_speed", "calc_acceleration", "calc_jerk"]
_COMPUTED_FLAG_COL = "is_spline_imputed"


def allowed_k_options(fitting_base: str) -> Tuple[int, ...]:
    """Return the selectable spline degrees for *fitting_base*.

    Intersects the report's per-base minimum degree with the odd-only set
    {1, 3, 5} (even degrees are never offered, per the report's guidance
    against using them).
    """
    min_k = MIN_K_BY_BASE[fitting_base]
    return tuple(k for k in ALLOWED_K if k >= min_k)


# ── Result containers ───────────────────────────────────────────────────────

@dataclass
class SplineRowResult:
    """One row of write-back data, per (event_id, veh_id, veh_time).

    ``is_spline_imputed=False`` rows already exist in the events table —
    only the four calc_* columns get UPDATEd. ``is_spline_imputed=True``
    rows do not exist yet and must be INSERTed; ``lane_id``/``orig_lane_id``
    are inherited from the temporally nearest *earlier* measured record
    (per the application's row-creation rule), and every measured column
    besides those two stays NULL.

    The meas_* fields are never written to the database — they exist only
    so summarize_fit() can compare calculated vs. measured values for
    goodness-of-fit metrics. They are NaN on imputed rows (no measurement
    exists) and may be NaN on measured rows too, if that particular
    column was absent from the dataset or null for that record.
    """
    event_id: int
    veh_id: int
    veh_time: float
    is_spline_imputed: bool

    lane_id: Optional[int] = None
    orig_lane_id: Optional[int] = None

    calc_position: float = NAN
    calc_speed: float = NAN
    calc_acceleration: float = NAN
    calc_jerk: float = NAN

    meas_position: float = NAN
    meas_speed: float = NAN
    meas_acceleration: float = NAN
    meas_jerk: float = NAN


@dataclass
class SplineFitResult:
    """Goodness-of-fit summary, one row per (event_id, veh_id).

    For each of the four kinematics, (rmse, mae, max_err, min_err, mean_err)
    is computed over (measured - calculated) at every point where a
    measured value exists — this includes the fitted base variable itself
    (its "residual"), as well as the other three derived kinematics where
    their measured columns are present.

    status="skipped" rows carry no metrics (all NaN) and exist only so a
    results table can show *why* a requested vehicle produced nothing
    (see module docstring, "Skipped trajectories").
    """
    event_id: int
    veh_id: int
    fitting_base: str
    s: float
    k: int

    n_points: int = 0     # measured points used in/compared against the fit
    n_imputed: int = 0    # synthetic rows created (0 unless fill_missing_rows)

    status: str = "ok"    # "ok" | "skipped"
    message: str = ""

    position_rmse: float = NAN
    position_mae: float = NAN
    position_max_err: float = NAN
    position_min_err: float = NAN
    position_mean_err: float = NAN

    speed_rmse: float = NAN
    speed_mae: float = NAN
    speed_max_err: float = NAN
    speed_min_err: float = NAN
    speed_mean_err: float = NAN

    acceleration_rmse: float = NAN
    acceleration_mae: float = NAN
    acceleration_max_err: float = NAN
    acceleration_min_err: float = NAN
    acceleration_mean_err: float = NAN

    jerk_rmse: float = NAN
    jerk_mae: float = NAN
    jerk_max_err: float = NAN
    jerk_min_err: float = NAN
    jerk_mean_err: float = NAN


# ── Core math (shared by both invocation paths) ────────────────────────────

def generate_complete_timestamps(time_array: np.ndarray) -> np.ndarray:
    """Return a complete timestamp grid spanning *time_array*.

    The step is inferred from the smallest observed positive inter-sample
    gap. Every original timestamp is preserved exactly; synthetic grid
    points are added only where they don't fall within tolerance of an
    existing original timestamp, so originals always take priority over
    the synthetic grid (no original timestamp is ever displaced).
    """
    t = np.unique(np.asarray(time_array, dtype=np.float64))
    if t.size < 2:
        return t

    gaps = np.diff(t)
    gaps = gaps[gaps > 0]
    if gaps.size == 0:
        return t

    dt = float(gaps.min())
    tol = dt * 1e-3

    grid = np.arange(t[0], t[-1] + dt * 0.5, dt)

    idx_hi = np.clip(np.searchsorted(t, grid), 0, t.size - 1)
    idx_lo = np.clip(idx_hi - 1, 0, t.size - 1)
    near_original = (np.abs(t[idx_hi] - grid) <= tol) | (np.abs(t[idx_lo] - grid) <= tol)
    grid = grid[~near_original]

    return np.sort(np.concatenate([t, grid]))


def apply_spline_fit(
    time_valid: np.ndarray,
    base_valid: np.ndarray,
    fitting_base: str,
    s: float,
    k: int,
    target_timestamps: np.ndarray,
    initial_conditions: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Optional[Dict[str, np.ndarray]]:
    """Fit a B-spline to (time_valid, base_valid) and derive all four
    kinematics, evaluated at target_timestamps.

    initial_conditions: optional {"position"|"speed"|"acceleration": (ic_time, ic_value)}.
    Needed only for levels strictly below fitting_base's integration order
    (fitting_base="jerk" needs all three; "speed" needs only "position";
    "position" needs none). Anchors the constant of integration that
    scipy's antiderivative() otherwise implicitly sets to zero, so the
    derived kinematic actually passes through the measured value at
    ic_time rather than an arbitrary offset/ramp. Each level's ic_time may
    differ from the others' -- e.g. veh_speed's nearest valid sample may
    not fall at the same timestamp as veh_dist_trav's -- and the solve
    accounts for that directly rather than assuming a shared t0.

    A missing entry for a level that needs one leaves that level's
    integration constant at 0 and logs a warning: the result for that
    level (and anything integrated further below it) will have the
    correct shape but may be offset from the true trajectory.
    """
    time_valid = np.asarray(time_valid, dtype=np.float64)
    base_valid = np.asarray(base_valid, dtype=np.float64)
    target_timestamps = np.asarray(target_timestamps, dtype=np.float64)
    initial_conditions = initial_conditions or {}

    if time_valid.size < k + 1:
        logger.warning(
            "apply_spline_fit: only %d valid points, need at least k+1=%d.",
            time_valid.size, k + 1,
        )
        return None

    if time_valid.size >= 2 and np.any(np.diff(time_valid) <= 0):
        logger.warning("apply_spline_fit: time_valid must be strictly increasing (no duplicates).")
        return None

    try:
        spl_base = make_splrep(time_valid, base_valid, k=k, s=s)
    except Exception as exc:
        logger.exception("apply_spline_fit: make_splrep failed: %s", exc)
        return None

    base_order = _ORDER[fitting_base]
    t_ref = time_valid[0]

    # Raw splines at every order <= base_order (implicit zero integration
    # constant -- these need correcting). Orders > base_order are exact
    # derivatives and need no correction at all.
    raw_below = {base_order: spl_base}
    for order in range(base_order - 1, -1, -1):
        raw_below[order] = spl_base.antiderivative(base_order - order)

    # Solve correction-polynomial coefficients sequentially, from the
    # level just below base_order down to position, each anchored at
    # that level's own (ic_time, ic_value).
    c: Dict[int, float] = {}
    for order in range(base_order - 1, -1, -1):
        name = _LEVEL_NAME_BY_ORDER[order]
        ic = initial_conditions.get(name)
        if ic is None:
            logger.warning(
                "apply_spline_fit: no initial condition for '%s'; its "
                "integration constant stays 0 (result may be offset "
                "from the true trajectory).", name)
            c[order] = 0.0
            continue
        ic_time, ic_value = ic
        already = sum(
            c[j] * (ic_time - t_ref) ** (j - order) / math.factorial(j - order)
            for j in c
        )
        c[order] = ic_value - float(raw_below[order](ic_time)) - already

    def eval_order(order: int, t: np.ndarray) -> np.ndarray:
        if order > base_order:
            return spl_base.derivative(order - base_order)(t)
        val = raw_below[order](t)
        correction = sum(
            c[j] * (t - t_ref) ** (j - order) / math.factorial(j - order)
            for j in c if j >= order
        )
        return val + correction

    n = target_timestamps.size
    result = {
        "calc_position": np.full(n, NAN),
        "calc_speed": np.full(n, NAN),
        "calc_acceleration": np.full(n, NAN),
        "calc_jerk": np.full(n, NAN),
    }

    domain_min, domain_max = time_valid[0], time_valid[-1]
    in_domain = (target_timestamps >= domain_min) & (target_timestamps <= domain_max)
    idx = np.where(in_domain)[0]
    if idx.size:
        pts = target_timestamps[idx]
        result["calc_position"][idx]     = eval_order(_ORDER["position"], pts)
        result["calc_speed"][idx]        = eval_order(_ORDER["speed"], pts)
        result["calc_acceleration"][idx] = eval_order(_ORDER["acceleration"], pts)
        result["calc_jerk"][idx]         = eval_order(_ORDER["jerk"], pts)

    return result


def compute_error_metrics(measured: np.ndarray, calculated: np.ndarray) -> Tuple[float, float, float, float, float]:
    """Return (rmse, mae, max_err, min_err, mean_err) of (measured -
    calculated) over indices where both are non-NaN. All-NaN -> all-NaN."""
    measured = np.asarray(measured, dtype=np.float64)
    calculated = np.asarray(calculated, dtype=np.float64)
    diff = measured - calculated
    mask = ~np.isnan(diff)
    if not np.any(mask):
        return (NAN, NAN, NAN, NAN, NAN)
    d = diff[mask]
    rmse = float(np.sqrt(np.mean(d ** 2)))
    mae = float(np.mean(np.abs(d)))
    max_err = float(np.max(d))
    min_err = float(np.min(d))
    mean_err = float(np.mean(d))
    return (rmse, mae, max_err, min_err, mean_err)


def summarize_fit(rows: List[SplineRowResult], fitting_base: str, s: float, k: int) -> List[SplineFitResult]:
    """Aggregate per-row results into one SplineFitResult per (event_id, veh_id)."""
    grouped: Dict[Tuple[int, int], List[SplineRowResult]] = defaultdict(list)
    for r in rows:
        grouped[(r.event_id, r.veh_id)].append(r)

    results: List[SplineFitResult] = []
    for (event_id, veh_id), group in grouped.items():
        group.sort(key=lambda r: r.veh_time)

        meas = {k_: np.array([getattr(r, f"meas_{k_}") for r in group]) for k_ in KINEMATICS}
        calc = {k_: np.array([getattr(r, f"calc_{k_}") for r in group]) for k_ in KINEMATICS}
        metrics = {k_: compute_error_metrics(meas[k_], calc[k_]) for k_ in KINEMATICS}

        n_imputed = sum(1 for r in group if r.is_spline_imputed)
        n_points = len(group) - n_imputed

        results.append(SplineFitResult(
            event_id=event_id, veh_id=veh_id, fitting_base=fitting_base, s=s, k=k,
            n_points=n_points, n_imputed=n_imputed, status="ok",
            position_rmse=metrics["position"][0], position_mae=metrics["position"][1],
            position_max_err=metrics["position"][2], position_min_err=metrics["position"][3],
            position_mean_err=metrics["position"][4],
            speed_rmse=metrics["speed"][0], speed_mae=metrics["speed"][1],
            speed_max_err=metrics["speed"][2], speed_min_err=metrics["speed"][3],
            speed_mean_err=metrics["speed"][4],
            acceleration_rmse=metrics["acceleration"][0], acceleration_mae=metrics["acceleration"][1],
            acceleration_max_err=metrics["acceleration"][2], acceleration_min_err=metrics["acceleration"][3],
            acceleration_mean_err=metrics["acceleration"][4],
            jerk_rmse=metrics["jerk"][0], jerk_mae=metrics["jerk"][1],
            jerk_max_err=metrics["jerk"][2], jerk_min_err=metrics["jerk"][3],
            jerk_mean_err=metrics["jerk"][4],
        ))
    return results

def nearest_valid_ic(t_full: np.ndarray, values_full: np.ndarray,
                      anchor_t: float) -> Optional[Tuple[float, float]]:
    """Return (time, value) of the valid (non-NaN) sample in values_full
    closest in time to anchor_t, or None if values_full has no valid
    entries at all."""
    valid_mask = ~np.isnan(values_full)
    if not np.any(valid_mask):
        return None
    t_v = t_full[valid_mask]
    v_v = values_full[valid_mask]
    idx = int(np.argmin(np.abs(t_v - anchor_t)))
    return float(t_v[idx]), float(v_v[idx])


# ── BaseComp subclass ────────────────────────────────────────────────────────

class SplineComp(BaseComp):
    """Spline fitting analysis — top-level Data > Spline Fitting module."""

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "fitting_base":      "position",  # "position"|"speed"|"acceleration"|"jerk"
        "s":                 0.0,
        "k":                 3,
        "fill_missing_rows": False,
    }

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        """Add the four calc_* REAL columns and is_spline_imputed to events,
        if not already present. Mirrors InternalConsistencyComp.ensure_schema."""
        for col in _COMPUTED_REAL_COLS:
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} REAL")
                conn.commit()
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc).lower():
                    pass
                else:
                    raise
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {_COMPUTED_FLAG_COL} INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column" in str(exc).lower():
                pass
            else:
                raise

    def comp_series(
        self,
        db_connection: sqlite3.Connection,
        event_id: int,
        veh_ids: Optional[Iterable[int]],
        options: Optional[Dict[str, Any]] = None,
        progress_callback = None,
    ) -> List[SplineRowResult]:
        options = self._resolve_options(options)
        fitting_base = options["fitting_base"]
        s = float(options["s"])
        k = int(options["k"])
        fill_missing_rows = bool(options["fill_missing_rows"])

        if fitting_base not in FITTING_BASES:
            raise ValueError(f"Unknown fitting_base: {fitting_base!r}")

        if veh_ids is None:
            veh_ids = self._event_veh_ids(db_connection, event_id)
        else:
            veh_ids = list(veh_ids)
        if not veh_ids:
            return []

        # Purge previously-imputed rows unconditionally since
        # fitting parameters (fitting_base/s/k) can change between runs
        db_connection.execute("DELETE FROM events WHERE event_id = ? AND is_spline_imputed = 1", (event_id,),)
        db_connection.commit()

        columns = self._events_columns(db_connection)
        has_speed = "veh_speed" in columns
        has_accel = "veh_accel" in columns
        has_jerk = "veh_jerk" in columns

        select_cols = ["veh_id", "veh_time", "veh_dist_trav", "lane_id", "orig_lane_id"]
        if has_speed:
            select_cols.append("veh_speed")
        if has_accel:
            select_cols.append("veh_accel")
        if has_jerk:
            select_cols.append("veh_jerk")

        placeholders = ",".join("?" for _ in veh_ids)
        sql = (
            f"SELECT {', '.join(select_cols)} FROM events "
            f"WHERE event_id = ? AND veh_id IN ({placeholders}) "
            f"ORDER BY veh_id, veh_time"
        )
        cursor = db_connection.cursor()
        cursor.execute(sql, [event_id] + veh_ids)

        raw: Dict[int, dict] = {v: {c: [] for c in select_cols} for v in veh_ids}
        for row in cursor:
            v = row[0]
            for ci, col in enumerate(select_cols):
                raw[v][col].append(row[ci])

        all_rows: List[SplineRowResult] = []

        for veh_id in veh_ids:
            if progress_callback is not None:
                progress_callback()
            d = raw[veh_id]
            n = len(d["veh_time"])
            if n == 0:
                continue

            t = np.asarray(d["veh_time"], dtype=np.float64)
            dist = np.asarray(d["veh_dist_trav"], dtype=np.float64)
            speed_meas = self._to_nan_array(d["veh_speed"]) if has_speed else np.full(n, NAN)
            accel_meas = self._to_nan_array(d["veh_accel"]) if has_accel else np.full(n, NAN)
            jerk_meas = self._to_nan_array(d["veh_jerk"]) if has_jerk else np.full(n, NAN)
            lane_ids = d["lane_id"]
            orig_lane_ids = d["orig_lane_id"]

            base_array = {
                "position": dist,
                "speed": speed_meas,
                "acceleration": accel_meas,
                "jerk": jerk_meas,
            }[fitting_base]

            valid_mask = ~np.isnan(base_array)
            time_valid = t[valid_mask]
            base_valid = base_array[valid_mask]

            # Defensive de-dup: ORDER BY veh_time should already guarantee
            # uniqueness, but a corrupt/duplicated import could violate that.
            if time_valid.size >= 2 and np.any(np.diff(time_valid) <= 0):
                _, uniq_idx = np.unique(time_valid, return_index=True)
                uniq_idx = np.sort(uniq_idx)
                time_valid = time_valid[uniq_idx]
                base_valid = base_valid[uniq_idx]

            if time_valid.size < k + 1:
                logger.warning(
                    "SplineComp: event=%s veh=%s skipped — only %d valid '%s' "
                    "points (need %d for k=%d).",
                    event_id, veh_id, time_valid.size, fitting_base, k + 1, k,
                )
                continue

            target_t = generate_complete_timestamps(t) if fill_missing_rows else t

            level_arrays = {"position": dist, "speed": speed_meas, "acceleration": accel_meas}
            initial_conditions = {
                name: ic
                for name, arr in level_arrays.items()
                if _ORDER[name] < _ORDER[fitting_base]
                for ic in [nearest_valid_ic(t, arr, time_valid[0])]
                if ic is not None
            }
            
            fit = apply_spline_fit(time_valid, base_valid, fitting_base, s, k, 
                                   target_t, initial_conditions=initial_conditions)

            if fit is None:
                logger.warning("SplineComp: spline fit failed for event=%s veh=%s.", event_id, veh_id)
                continue

            tol = 1e-6
            idx_hi = np.clip(np.searchsorted(t, target_t), 0, n - 1)
            idx_lo = np.clip(idx_hi - 1, 0, n - 1)
            use_lo = np.abs(t[idx_lo] - target_t) < np.abs(t[idx_hi] - target_t)
            idx_nearest = np.where(use_lo, idx_lo, idx_hi)
            is_measured = np.abs(t[idx_nearest] - target_t) <= tol

            # Lane inherited from the nearest EARLIER-or-equal measured record.
            idx_earlier = np.clip(np.searchsorted(t, target_t, side="right") - 1, 0, n - 1)

            for i, tt in enumerate(target_t):
                if is_measured[i]:
                    j = idx_nearest[i]
                    all_rows.append(SplineRowResult(
                        event_id=event_id, veh_id=veh_id, veh_time=float(tt),
                        is_spline_imputed=False,
                        lane_id=lane_ids[j], orig_lane_id=orig_lane_ids[j],
                        calc_position=fit["calc_position"][i],
                        calc_speed=fit["calc_speed"][i],
                        calc_acceleration=fit["calc_acceleration"][i],
                        calc_jerk=fit["calc_jerk"][i],
                        meas_position=dist[j], meas_speed=speed_meas[j],
                        meas_acceleration=accel_meas[j], meas_jerk=jerk_meas[j],
                    ))
                else:
                    j = idx_earlier[i]
                    all_rows.append(SplineRowResult(
                        event_id=event_id, veh_id=veh_id, veh_time=float(tt),
                        is_spline_imputed=True,
                        lane_id=lane_ids[j], orig_lane_id=orig_lane_ids[j],
                        calc_position=fit["calc_position"][i],
                        calc_speed=fit["calc_speed"][i],
                        calc_acceleration=fit["calc_acceleration"][i],
                        calc_jerk=fit["calc_jerk"][i],
                    ))

        return all_rows

    @staticmethod
    def _to_nan_array(seq) -> np.ndarray:
        return np.array([NAN if x is None else x for x in seq], dtype=np.float64)