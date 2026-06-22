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
        gap_secs = gap_pts * time_step   # convert to seconds for time_diff comparison

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

            t    = np.asarray(d["veh_time"],    dtype=np.float64)
            dist = np.asarray(d["veh_dist_trav"], dtype=np.float64)

            orig_speed = self._to_nan_array(d["veh_speed"]) if has_speed else np.full(n, NAN)
            orig_accel = self._to_nan_array(d["veh_accel"]) if has_accel else np.full(n, NAN)
            orig_jerk  = self._to_nan_array(d["veh_jerk"])  if has_jerk  else np.full(n, NAN)

            # Compute time differences.
            time_diff = np.empty(n, dtype=np.float64)
            time_diff[0] = NAN
            time_diff[1:] = np.diff(t)

            # Reconstruct signals following the pseudocode exactly.
            speed_cal = self._estimate_speed(dist, time_diff, gap_secs)
            accel_cal = self._estimate_accel(speed_cal, time_diff, gap_secs)
            jerk_cal  = self._estimate_jerk(accel_cal, time_diff, gap_secs)

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
    def _estimate_speed(dist: np.ndarray, time_diff: np.ndarray,
                        gap_secs: float) -> np.ndarray:
        n = len(dist)
        out = np.full(n, NAN)
        for i in range(1, n):
            td = time_diff[i]
            if np.isnan(td) or td <= 0 or td > gap_secs:
                continue
            out[i] = (dist[i] - dist[i - 1]) / td
        return out

    @staticmethod
    def _estimate_accel(speed_cal: np.ndarray, time_diff: np.ndarray,
                        gap_secs: float) -> np.ndarray:
        n = len(speed_cal)
        out = np.full(n, NAN)
        for i in range(1, n):
            td = time_diff[i]
            if np.isnan(td) or td <= 0 or td > gap_secs:
                continue
            if np.isnan(speed_cal[i]) or np.isnan(speed_cal[i - 1]):
                continue
            out[i] = (speed_cal[i] - speed_cal[i - 1]) / td
        return out

    @staticmethod
    def _estimate_jerk(accel_cal: np.ndarray, time_diff: np.ndarray,
                       gap_secs: float) -> np.ndarray:
        n = len(accel_cal)
        out = np.full(n, NAN)
        for i in range(1, n):
            td = time_diff[i]
            if np.isnan(td) or td <= 0 or td > gap_secs:
                continue
            if np.isnan(accel_cal[i]) or np.isnan(accel_cal[i - 1]):
                continue
            out[i] = (accel_cal[i] - accel_cal[i - 1]) / td
        return out

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