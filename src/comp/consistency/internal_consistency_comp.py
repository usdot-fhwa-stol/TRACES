import sqlite3
import logging
from dataclasses import dataclass
from typing import Iterable, Optional, List, Dict, Any

import numpy as np

from ..base_comp import BaseComp

logger = logging.getLogger(__name__)

NAN = float("nan")

# Columns always written to the events table by this module.
_COMPUTED_COLS = [
    "veh_speed_cal",
    "veh_accel_cal",
    "veh_jerk_cal",
    "speed_error",
    "accel_error",
    "jerk_error",
]


@dataclass
class InternalConsistencyResult:
    """One row of internal consistency output, per (event_id, veh_id, veh_time).

    Computed columns are always present; error fields are NaN when the
    corresponding source column was absent from the dataset.
    """
    event_id:      int
    veh_id:        int
    veh_time:      float
    veh_dist_trav: float

    # Original signals (NaN when absent from the dataset)
    veh_speed: float = NAN
    veh_accel: float = NAN
    veh_jerk:  float = NAN

    # Reconstructed signals
    veh_speed_cal: float = NAN
    veh_accel_cal: float = NAN
    veh_jerk_cal:  float = NAN

    # Difference: original − reconstructed (NaN when source column absent)
    speed_error: float = NAN
    accel_error: float = NAN
    jerk_error:  float = NAN


class InternalConsistencyComp(BaseComp):
    """Internal consistency analysis — Module 4 of the noise analysis spec.

    Reconstructs velocity from distance, acceleration from velocity, and
    jerk from acceleration.  Compares each reconstructed signal with the
    corresponding column in the dataset (when present) to produce error terms.

    Indexing convention
    --------------------
    Every "_cal" value is a forward difference over the interval
    [t[i], t[i+1]] and is stored at index i — the record where that
    interval *begins* — not at i+1 where it ends.  This means the *last*
    record of every (event_id, veh_id) series is always NaN for every
    calculated column (there is no i+1 to pair it with), so the blank row
    in any displayed table lands at the bottom, not the top.

    Source preference
    ------------------
    Acceleration is reconstructed from measured speed wherever a measured
    value is available at both endpoints of the interval, falling back to
    the reconstructed speed only where the measured value is missing
    (row-level) or the veh_speed column is absent from the dataset
    entirely.  Jerk follows the identical pattern one level up, preferring
    measured acceleration over veh_accel_cal.

    The computed columns are written back to the events table by the dialog's
    _on_worker_finished, NOT by this class.  comp_series is purely computational.
    """

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "missing_gap_points": 10,   # consecutive missing records threshold
        "time_step":          0.1,  # expected sampling interval (seconds)
    }

    # ── Public API ────────────────────────────────────────────────────────

    def comp_series(
        self,
        db_connection: sqlite3.Connection,
        event_id: int,
        veh_ids: Optional[Iterable[int]],
        options: Optional[Dict[str, Any]] = None,
    ) -> List[InternalConsistencyResult]:
        options  = self._resolve_options(options)
        gap_pts  = int(options["missing_gap_points"])
        time_step = float(options["time_step"])
        gap_secs = gap_pts * time_step   # convert to seconds for interval comparison

        if veh_ids is None:
            veh_ids = self._event_veh_ids(db_connection, event_id)
        else:
            veh_ids = list(veh_ids)
        if not veh_ids:
            return []

        columns   = self._events_columns(db_connection)
        has_speed = "veh_speed" in columns
        has_accel = "veh_accel" in columns
        has_jerk  = "veh_jerk"  in columns

        # Always need veh_dist_trav; caller gates the menu on its presence.
        select_cols = ["veh_id", "veh_time", "veh_dist_trav"]
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

        # Group rows by veh_id.
        raw: Dict[int, dict] = {
            v: {c: [] for c in select_cols} for v in veh_ids
        }
        for row in cursor:
            v = row[0]
            for ci, col in enumerate(select_cols):
                raw[v][col].append(row[ci])

        all_results: List[InternalConsistencyResult] = []

        for veh_id in veh_ids:
            d = raw[veh_id]
            n = len(d["veh_time"])
            if n == 0:
                continue

            t    = np.asarray(d["veh_time"],      dtype=np.float64)
            dist = np.asarray(d["veh_dist_trav"], dtype=np.float64)

            orig_speed = self._to_nan_array(d["veh_speed"]) if has_speed else np.full(n, NAN)
            orig_accel = self._to_nan_array(d["veh_accel"]) if has_accel else np.full(n, NAN)
            orig_jerk  = self._to_nan_array(d["veh_jerk"])  if has_jerk  else np.full(n, NAN)

            # ── Reconstruction, with values attributed to the START of the
            # interval that produced them (see class docstring).
            speed_cal = self._derivative_from_start(dist, t, gap_secs)

            # Acceleration prefers measured speed; falls back to speed_cal
            # wherever a measured reading is missing or absent altogether.
            speed_for_accel = self._prefer_measured(orig_speed, speed_cal)
            accel_cal = self._derivative_from_start(speed_for_accel, t, gap_secs)

            # Jerk follows the same pattern, one level up.
            accel_for_jerk = self._prefer_measured(orig_accel, accel_cal)
            jerk_cal = self._derivative_from_start(accel_for_jerk, t, gap_secs)

            # Error terms: original − reconstructed; NaN when source absent.
            speed_err = self._error(orig_speed, speed_cal) if has_speed else np.full(n, NAN)
            accel_err = self._error(orig_accel, accel_cal) if has_accel else np.full(n, NAN)
            jerk_err  = self._error(orig_jerk,  jerk_cal)  if has_jerk  else np.full(n, NAN)

            for i in range(n):
                all_results.append(InternalConsistencyResult(
                    event_id      = event_id,
                    veh_id        = veh_id,
                    veh_time      = t[i],
                    veh_dist_trav = dist[i],
                    veh_speed     = orig_speed[i],
                    veh_accel     = orig_accel[i],
                    veh_jerk      = orig_jerk[i],
                    veh_speed_cal = speed_cal[i],
                    veh_accel_cal = accel_cal[i],
                    veh_jerk_cal  = jerk_cal[i],
                    speed_error   = speed_err[i],
                    accel_error   = accel_err[i],
                    jerk_error    = jerk_err[i],
                ))

        return all_results

    # ── Schema helpers (called on the worker thread) ──────────────────────

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        """Add the six computed columns to the events table if absent.

        SQLite has no ADD COLUMN IF NOT EXISTS, so each column is attempted
        individually and the DUPLICATE COLUMN error is silently swallowed.
        """
        for col in _COMPUTED_COLS:
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} REAL")
                conn.commit()
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc).lower():
                    pass  # already present — expected on re-run
                else:
                    raise

    # ── Reconstruction helpers ────────────────────────────────────────────

    @staticmethod
    def _derivative_from_start(source: np.ndarray, t: np.ndarray,
                               gap_secs: float) -> np.ndarray:
        """Forward-difference rate of change, assigned to the interval's
        *starting* record.

        For each i, out[i] approximates d(source)/dt over [t[i], t[i+1]]
        using (source[i+1] - source[i]) / (t[i+1] - t[i]).  The result is
        stored at index i, where the interval begins, rather than at i+1
        where it ends — so out[n-1] is always NaN (no i+1 exists to pair
        the last record with), and any record skipped because the gap
        exceeds gap_secs, or because either endpoint is NaN, is also NaN.
        """
        n = len(source)
        out = np.full(n, NAN)
        for i in range(n - 1):
            td = t[i + 1] - t[i]
            if np.isnan(td) or td <= 0 or td > gap_secs:
                continue
            a, b = source[i], source[i + 1]
            if np.isnan(a) or np.isnan(b):
                continue
            out[i] = (b - a) / td
        return out

    @staticmethod
    def _prefer_measured(measured: np.ndarray, calculated: np.ndarray) -> np.ndarray:
        """Element-wise: the measured value where present, else the
        calculated (reconstructed) value.

        Used so acceleration is derived from real speed readings wherever
        they exist, and jerk from real acceleration readings wherever they
        exist — falling back to the reconstructed signal only where the
        measured one is missing at that row, or the column is absent from
        the dataset entirely (in which case `measured` is all-NaN and
        every element falls back to `calculated`).
        """
        return np.where(np.isnan(measured), calculated, measured)

    @staticmethod
    def _error(original: np.ndarray, computed: np.ndarray) -> np.ndarray:
        out = np.full(len(original), NAN)
        valid = ~np.isnan(original) & ~np.isnan(computed)
        out[valid] = original[valid] - computed[valid]
        return out

    @staticmethod
    def _to_nan_array(lst: list) -> np.ndarray:
        arr = np.empty(len(lst), dtype=np.float64)
        for i, v in enumerate(lst):
            try:
                arr[i] = float(v) if v is not None else NAN
            except (TypeError, ValueError):
                arr[i] = NAN
        return arr