import sqlite3
import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, List, Dict, Any
import numpy as np
from ..base_comp import BaseComp

logger = logging.getLogger(__name__)

NAN = float('nan')


@dataclass
class JerkResult:
    """One row of jerk analysis output, per (event_id, veh_id)."""
    event_id: int
    veh_id: int
    speed: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    accel: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    jerk:  np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    time:  np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    jerk_violation_pct: float = NAN
    max_jerk: float = NAN
    min_jerk: float = NAN
    mean_jerk: float = NAN
    sign_inversion_excess_pct: float = NAN
    # "dataset" | "from_accel" | "from_speed" | "unavailable"
    jerk_source: str = "unavailable"


class JerkComp(BaseComp):
    """Jerk analysis per the noise analysis spec, Module 1."""

    SIGN_INVERSION_WINDOW = 1.0  # seconds (per spec)

    # Options recognized by this comp, with defaults.
    OPTIONS_SCHEMA: Dict[str, Any] = {
        "jerk_threshold":      15.0,    # m/s^3
        "missing_gap_seconds": 1.0,     # seconds
        "jerk_method":         "dataset",  # "dataset" | "from_accel" | "from_speed"
    }

    def comp_series(self,
                    db_connection: sqlite3.Connection,
                    event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None) -> List[JerkResult]:
        options = self._resolve_options(options)
        jerk_threshold      = options["jerk_threshold"]
        missing_gap_seconds = options["missing_gap_seconds"]
        jerk_method         = options["jerk_method"]

        if veh_ids is None:
            veh_ids = self._event_veh_ids(db_connection, event_id)
        else:
            veh_ids = list(veh_ids)
        if not veh_ids:
            return []

        columns = self._events_columns(db_connection)
        has_jerk  = "veh_jerk"  in columns
        has_accel = "veh_accel" in columns
        has_speed = "veh_speed" in columns

        series: Dict[int, JerkResult] = {
            v: JerkResult(event_id=event_id, veh_id=v) for v in veh_ids
        }

        # Only SELECT what the method actually requires.
        # Sourcing is strict: the chosen method dictates the columns we read.
        select_cols = ["veh_id", "veh_time"]
        if jerk_method == "dataset" and has_jerk:
            select_cols.append("veh_jerk")
        elif jerk_method == "from_accel" and has_accel:
            select_cols.append("veh_accel")
        elif jerk_method == "from_speed" and has_speed:
            select_cols.append("veh_speed")
        else:
            # Required source column isn't present — every result stays "unavailable".
            logger.warning(
                "JerkComp: method '%s' selected but required column is missing.",
                jerk_method,
            )
            return list(series.values())

        placeholders = ",".join("?" for _ in veh_ids)
        sql = (
            f"SELECT {', '.join(select_cols)} FROM events "
            f"WHERE event_id = ? AND veh_id IN ({placeholders}) "
            f"ORDER BY veh_id, veh_time"
        )
        cursor = db_connection.cursor()
        cursor.execute(sql, [event_id] + veh_ids)

        tmp = {v: {"t": [], "src": []} for v in veh_ids}
        for row in cursor:
            v, t, src_val = row[0], row[1], row[2]
            tmp[v]["t"].append(t)
            tmp[v]["src"].append(src_val)

        for v, r in series.items():
            r.time = np.asarray(tmp[v]["t"], dtype=np.float64)
            src = self._to_nan_array(tmp[v]["src"])

            if jerk_method == "dataset":
                r.jerk = src
                if r.jerk.size > 0 and np.any(~np.isnan(r.jerk)):
                    r.jerk_source = "dataset"
            elif jerk_method == "from_accel":
                r.accel = src
                if r.accel.size > 0 and np.any(~np.isnan(r.accel)):
                    r.jerk = self._derive_jerk_from_accel(r.time, r.accel,
                                                          missing_gap_seconds)
                    r.jerk_source = "from_accel"
            elif jerk_method == "from_speed":
                r.speed = src
                if r.speed.size > 0 and np.any(~np.isnan(r.speed)):
                    r.accel = self._derive_accel_from_speed(r.time, r.speed,
                                                            missing_gap_seconds)
                    r.jerk = self._derive_jerk_from_accel(r.time, r.accel,
                                                          missing_gap_seconds)
                    r.jerk_source = "from_speed"

            if r.jerk_source == "unavailable" or r.jerk.size == 0:
                continue

            r.jerk_violation_pct = self._jerk_violation_pct(r.jerk, jerk_threshold)
            r.min_jerk, r.mean_jerk, r.max_jerk = self._min_mean_max_jerk(r.jerk)
            r.sign_inversion_excess_pct = self._sign_inversion_excess_pct(r.time, r.jerk)

        return list(series.values())

    # ─── helpers (unchanged from before, but now take params instead of self.x) ──

    @staticmethod
    def _to_nan_array(seq):
        return np.array([NAN if x is None else x for x in seq], dtype=np.float64)

    @staticmethod
    def _derive_accel_from_speed(t, v, missing_gap_seconds):
        """out[i] is the average rate of change over [t[i], t[i+1]], assigned
        to the *starting* record i.  out[-1] is always NaN — no following
        point exists to pair the last record with."""
        out = np.full_like(v, NAN)
        if v.size < 2:
            return out
        dt = np.diff(t)
        valid = (~np.isnan(v[1:])) & (~np.isnan(v[:-1])) & (dt > 0) & (dt <= missing_gap_seconds)
        out[:-1][valid] = (v[1:][valid] - v[:-1][valid]) / dt[valid]
        return out
    
    @staticmethod
    def _derive_jerk_from_accel(t, a, missing_gap_seconds):
        """Same indexing convention as _derive_accel_from_speed, one
        derivative level up."""
        out = np.full_like(a, NAN)
        if a.size < 2:
            return out
        dt = np.diff(t)
        valid = (~np.isnan(a[1:])) & (~np.isnan(a[:-1])) & (dt > 0) & (dt <= missing_gap_seconds)
        out[:-1][valid] = (a[1:][valid] - a[:-1][valid]) / dt[valid]
        return out

    @staticmethod
    def _jerk_violation_pct(jerk, jerk_threshold):
        valid = jerk[~np.isnan(jerk)]
        if valid.size == 0:
            return NAN
        return 100.0 * np.sum(np.abs(valid) > jerk_threshold) / valid.size

    @staticmethod
    def _min_mean_max_jerk(jerk):
        valid = jerk[~np.isnan(jerk)]
        if valid.size == 0:
            return (NAN, NAN, NAN)
        return (float(np.min(valid)), float(np.mean(valid)), float(np.max(valid)))

    @classmethod
    def _sign_inversion_excess_pct(cls, t, jerk):
        valid_mask = ~np.isnan(jerk)
        tv = t[valid_mask]
        jv = jerk[valid_mask]
        if tv.size == 0:
            return NAN

        t_start, t_end = tv[0], tv[-1]
        n_total = n_excess = 0
        w = cls.SIGN_INVERSION_WINDOW
        k = 0
        while True:
            w_lo = t_start + k * w
            if w_lo > t_end:
                break
            w_hi = w_lo + w
            sel = (tv >= w_lo) & (tv < w_hi)
            window_jerk = jv[sel]
            window_jerk = window_jerk[window_jerk != 0]
            if window_jerk.size >= 2:
                n_total += 1
                signs = np.sign(window_jerk)
                if int(np.sum(signs[1:] != signs[:-1])) > 1:
                    n_excess += 1
            elif sel.any():
                n_total += 1
            k += 1

        if n_total == 0:
            return NAN
        return 100.0 * n_excess / n_total