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

Fix
---
A lane reassignment, like every built-in lane-anomaly fix: the vehicle's
samples in the span (or a segment the user narrows it to) are moved into
one lane -- lane_id only, positions unchanged (IMPUTED_LANE). The UI
offers the anomaly's two lanes first, preselecting the one holding more
of the span's samples (lane A on a tie), and any other lane of the event.

Task 9.3 describes this fix as a midline trajectory with a slider between
the two lanes. Per customer direction (2026-10-29) lane-level fixes only
reassign lane_id, so no position is generated or written.
"""

import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from utils import events_columns, active_rows_clause, measured_rows_clause
from .anomaly_registry import AnomalyType, register_anomaly_type
from .lane_anomaly_store import LaneAnomaly

RAPID_LANE_CHANGE_KEY = "rapid_lane_change"
DEFAULT_MAX_INTERVAL_S = 2.0
DEFAULT_MIN_CHANGES = 3


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

    # ── Fix ───────────────────────────────────────────────────────────

    def fix_vehicles(self, anomaly: LaneAnomaly) -> Sequence[int]:
        return (anomaly.veh_id,)

    def default_target_lane(self, conn: sqlite3.Connection, anomaly: LaneAnomaly,
                            veh_id: int) -> Optional[int]:
        lane_a, lane_b = (int(l) for l in anomaly.lane_ids[:2])
        n_a = int(anomaly.metrics.get("samples_lane_a") or 0)
        n_b = int(anomaly.metrics.get("samples_lane_b") or 0)
        return lane_b if n_b > n_a else lane_a


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
            out.append(_chain_anomaly(event_id, veh_id, t, lanes, chain, params or {}))
        chain = [c] if c is not None and eligible(c) else []
    return out


def _chain_anomaly(event_id, veh_id, t, lanes, chain, params):
    first, last = chain[0], chain[-1]
    # From the last sample before the first change to the first sample
    # after the last change: the samples whose lane assignment is in
    # question. Steady data either side is left alone.
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


register_anomaly_type(RapidLaneChangeType())
