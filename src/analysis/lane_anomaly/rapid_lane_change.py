"""Rapid Lane Change anomaly (Task 9.3): a vehicle repeatedly and rapidly
assigned between two neighboring lanes -- a likely lane-assignment error
rather than real driving.

Detection
---------
Per vehicle, the time-ordered lane_id sequence is split into lane runs; a
lane change is the first sample of a new run. A chain of consecutive lane
changes is an anomaly when

    * every change in it is between the same two lanes (so they alternate),
      neighbors (|a - b| = 1) unless require_adjacent is off,
    * consecutive changes are at most *max_interval_s* apart
      (Figure 2 "Max Time Between Changes", s), and
    * it has at least *min_changes* changes.

The anomaly spans from the last sample before the first change to the
first sample after the last change -- the samples whose lane assignment is
in question. lane_ids is [lane before the first change, the other lane].

Fix: midline
------------
fit_midline() fits, by least squares over the anomaly span plus a margin
of measured data on either side, one smooth curve g(t) (cubic B-spline)
for the first lane's samples and the same curve plus a constant offset
delta for the second lane's:

    dist ~ g(t) + delta * [sample is in the second lane]

so g is the trajectory "as if in lane A" and g + delta "as if in lane B".
The midline at slider weight w (0 = lane A, 1 = lane B) is
g(t) + w * delta. veh_speed and veh_accel of the corrected rows are
recomputed as g'(t) and g''(t) -- the offset is constant, so they don't
depend on w. veh_jerk is left as measured.

The corrected rows get the lane the slider leans toward: lane A for
w < 0.5, lane B for w > 0.5, and at exactly 0.5 the lane holding more of
the span's samples (lane A on a tie). They are tagged IMPUTED_CORRECTED.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from scipy.interpolate import BSpline

from utils import (
    events_columns, active_rows_clause, measured_rows_clause, IMPUTED_CORRECTED,
)
from .anomaly_registry import AnomalyType, register_anomaly_type
from .lane_anomaly_store import LaneAnomaly
from .lane_correction import FIX_MIDLINE, apply_correction, vehicle_rows

RAPID_LANE_CHANGE_KEY = "rapid_lane_change"
DEFAULT_MAX_INTERVAL_S = 2.0
DEFAULT_MIN_CHANGES = 3

MIDLINE_MARGIN_S = 2.0          # measured data either side of the span used in the fit
_KNOT_SPACING_S = 1.0           # B-spline interior knot spacing (seconds)


class RapidLaneChangeType(AnomalyType):
    KEY = RAPID_LANE_CHANGE_KEY
    LABEL = "Rapid Lane Change"
    UI_MODULE = ".rapid_lane_change_view"
    OPTIONS_SCHEMA: Dict[str, Any] = {
        "max_interval_s": DEFAULT_MAX_INTERVAL_S,   # Figure 2 spinbox
        "min_changes": DEFAULT_MIN_CHANGES,
        "require_adjacent": True,
    }

    def detect(self, conn: sqlite3.Connection, event_id: int,
               veh_ids: Optional[Iterable[int]], options: Dict[str, Any],
               time_step: float) -> List[LaneAnomaly]:
        options = self.resolve_options(options)
        max_interval = float(options["max_interval_s"])
        min_changes = max(1, int(options["min_changes"]))
        adjacent = bool(options["require_adjacent"])

        cols = events_columns(conn)
        where, params = "event_id = ? AND lane_id IS NOT NULL", [event_id]
        if veh_ids is not None:
            ids = [int(v) for v in veh_ids]
            if not ids:
                return []
            where += f" AND veh_id IN ({','.join('?' for _ in ids)})"
            params += ids
        rows = conn.execute(
            f"SELECT veh_id, veh_time, lane_id FROM events WHERE {where}"
            f"{active_rows_clause(cols)}{measured_rows_clause(cols)} "
            f"ORDER BY veh_id, veh_time", params).fetchall()

        settings = {"max_interval_s": max_interval, "min_changes": min_changes}
        anomalies: List[LaneAnomaly] = []
        start = 0
        for i in range(1, len(rows) + 1):
            if i == len(rows) or rows[i][0] != rows[start][0]:
                veh = int(rows[start][0])
                t = np.array([r[1] for r in rows[start:i]], dtype=np.float64)
                lanes = [int(r[2]) for r in rows[start:i]]
                anomalies.extend(find_rapid_changes(
                    event_id, veh, t, lanes, max_interval, min_changes, adjacent, settings))
                start = i
        anomalies.sort(key=lambda a: (a.start_time, a.veh_id))
        return anomalies

    def describe(self, anomaly: LaneAnomaly) -> str:
        text = super().describe(anomaly)
        changes = anomaly.metrics.get("changes")
        return text if changes is None else text.replace(
            "  (lane", f"  {changes} changes  (lane", 1)

    @staticmethod
    def apply_fix(conn: sqlite3.Connection, anomaly: LaneAnomaly,
                  plan: "MidlinePlan") -> int:
        """Write a midline plan (IMPUTED_CORRECTED)."""
        values = {"veh_dist_trav": plan.dist, "veh_speed": plan.speed,
                  "veh_accel": plan.accel}
        return apply_correction(
            conn, anomaly, anomaly.veh_id, FIX_MIDLINE, plan.times, IMPUTED_CORRECTED,
            lanes=[plan.lane] * len(plan.times), values=values,
            to_lane=plan.lane, weight=plan.weight)


def find_rapid_changes(event_id: int, veh_id: int, t: np.ndarray, lanes: List[int],
                       max_interval: float, min_changes: int, adjacent: bool,
                       params: Optional[dict] = None) -> List[LaneAnomaly]:
    """Rapid lane change chains in one trajectory (module docstring)."""
    n = len(lanes)
    if n < 2:
        return []
    changes = [i for i in range(1, n) if lanes[i] != lanes[i - 1]]   # first sample of new run
    if len(changes) < min_changes:
        return []

    def pair(i):
        return frozenset((lanes[i - 1], lanes[i]))

    def eligible(i):
        a, b = lanes[i - 1], lanes[i]
        return not adjacent or abs(a - b) == 1

    out: List[LaneAnomaly] = []
    chain: List[int] = []
    for c in changes + [None]:
        extend = (c is not None and chain and eligible(c) and pair(c) == pair(chain[0])
                  and t[c] - t[chain[-1]] <= max_interval)
        if extend:
            chain.append(c)
            continue
        if len(chain) >= min_changes:
            out.append(_chain_anomaly(event_id, veh_id, t, lanes, changes, chain,
                                      max_interval, params or {}))
        chain = [c] if c is not None and eligible(c) else []
    return out


def _chain_anomaly(event_id, veh_id, t, lanes, changes, chain, max_interval, params):
    first, last = chain[0], chain[-1]
    # From the last sample before the first change to the first sample
    # after the last change: the samples whose lane assignment is in
    # question. Steady data either side is left alone (the midline fit
    # still uses it, see fit_midline's margin).
    i0, i1 = first - 1, last

    lane_a = lanes[first - 1]
    lane_b = lanes[first]
    span = lanes[i0:i1 + 1]
    gaps = np.diff([t[c] for c in chain])
    return LaneAnomaly(
        event_id=event_id, anomaly_type=RAPID_LANE_CHANGE_KEY, veh_id=veh_id,
        start_time=float(t[i0]), end_time=float(t[i1]),
        lane_ids=[lane_a, lane_b], params=dict(params),
        metrics={"changes": len(chain),
                 "min_interval_s": round(float(gaps.min()), 4) if gaps.size else None,
                 "samples_lane_a": span.count(lane_a),
                 "samples_lane_b": span.count(lane_b)},
    )


# ── Midline fix ─────────────────────────────────────────────────────────────

@dataclass
class MidlinePlan:
    """Rows a midline fix writes, for one slider weight."""
    weight: float
    lane: int
    times: np.ndarray
    dist: np.ndarray
    speed: np.ndarray
    accel: np.ndarray


@dataclass
class MidlineModel:
    """The fitted lane-A curve g and lane offset delta for one anomaly,
    plus the samples used, for preview plots. Cheap to re-plan."""
    anomaly: LaneAnomaly
    lane_a: int
    lane_b: int
    spline: BSpline
    delta: float
    span_times: np.ndarray       # samples the fix rewrites
    span_lanes: np.ndarray
    fit_times: np.ndarray        # samples the fit used (span + margins)
    fit_dist: np.ndarray
    fit_lanes: np.ndarray
    fit_speed: np.ndarray
    fit_accel: np.ndarray

    def curve(self, weight: float, times: np.ndarray) -> np.ndarray:
        return self.spline(times) + float(weight) * self.delta

    def lane_for(self, weight: float) -> int:
        if weight < 0.5:
            return self.lane_a
        if weight > 0.5:
            return self.lane_b
        n_a = int(np.sum(self.span_lanes == self.lane_a))
        n_b = int(np.sum(self.span_lanes == self.lane_b))
        return self.lane_b if n_b > n_a else self.lane_a

    def plan(self, weight: float) -> MidlinePlan:
        w = min(max(float(weight), 0.0), 1.0)
        t = self.span_times
        return MidlinePlan(
            weight=w, lane=self.lane_for(w), times=t.copy(),
            dist=self.curve(w, t),
            speed=self.spline.derivative(1)(t),
            accel=self.spline.derivative(2)(t))


def fit_midline(conn: sqlite3.Connection, anomaly: LaneAnomaly,
                margin_s: float = MIDLINE_MARGIN_S) -> MidlineModel:
    """Fit the midline model for a Rapid Lane Change anomaly (module
    docstring). Raises ValueError (user-facing) when the data can't
    support a fit."""
    lane_a, lane_b = int(anomaly.lane_ids[0]), int(anomaly.lane_ids[1])
    rows = vehicle_rows(conn, anomaly.event_id, anomaly.veh_id,
                        anomaly.start_time - margin_s, anomaly.end_time + margin_s)
    lanes = np.array([np.nan if l is None else l for l in rows["lane"]], dtype=np.float64)
    keep = np.isfinite(rows["dist"]) & ((lanes == lane_a) | (lanes == lane_b))
    t, d, lane = rows["time"][keep], rows["dist"][keep], lanes[keep].astype(int)
    in_b = (lane == lane_b).astype(np.float64)
    if not np.any(in_b) or np.all(in_b):
        raise ValueError(f"Vehicle {anomaly.veh_id} needs samples in both lane {lane_a} "
                         f"and lane {lane_b} around this anomaly to fit a midline.")

    k = 3
    t0, t1 = float(t[0]), float(t[-1])
    span = t1 - t0
    n_interior = int(span // _KNOT_SPACING_S)
    # Basis functions + 1 offset column must not outnumber the samples.
    n_interior = max(0, min(n_interior, t.size - (k + 1) - 1 - 1))
    if t.size < k + 3 or span <= 0:
        raise ValueError(f"Too few samples of vehicle {anomaly.veh_id} around this "
                         f"anomaly to fit a midline.")
    interior = np.linspace(t0, t1, n_interior + 2)[1:-1]
    knots = np.concatenate([[t0] * (k + 1), interior, [t1] * (k + 1)])
    basis = BSpline.design_matrix(t, knots, k).toarray()
    design = np.column_stack([basis, in_b])
    coef, *_ = np.linalg.lstsq(design, d, rcond=None)
    spline = BSpline(knots, coef[:-1], k, extrapolate=True)

    in_span = (rows["time"] >= anomaly.start_time) & (rows["time"] <= anomaly.end_time)
    span_lanes = np.array([np.nan if l is None else l for l in rows["lane"][in_span]],
                          dtype=np.float64)
    return MidlineModel(
        anomaly=anomaly, lane_a=lane_a, lane_b=lane_b, spline=spline,
        delta=float(coef[-1]),
        span_times=rows["time"][in_span], span_lanes=span_lanes,
        fit_times=t, fit_dist=d, fit_lanes=lane,
        fit_speed=rows["speed"][keep], fit_accel=rows["accel"][keep])


register_anomaly_type(RapidLaneChangeType())
