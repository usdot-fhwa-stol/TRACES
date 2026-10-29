"""Proximity anomaly (Task 9.3): two vehicles in the same lane closer than
a threshold.

Detection
---------
Spacing is center-to-center: |veh_dist_trav(a) - veh_dist_trav(b)| at the
same timestamp in the same lane. Timestamps are matched on the event's
sampling grid (round(veh_time / step)), so vehicles whose clocks differ by
less than half a step are compared. Only vehicles adjacent in distance
within a lane are compared -- the closest pair in any group is always
adjacent, so nothing is missed.

Samples where a pair is closer than *threshold_m* are grouped per (pair,
lane) into runs; a run continues across at most *max_gap_steps* missing
grid steps. Each run with at least *min_samples* samples is one anomaly:
veh_id is the follower (smaller distance at the run's first sample),
other_veh_id the leader.

Fix
---
The user may move either vehicle, or both, to any lane of the event over
one time segment (default: the anomaly span) -- one LaneMove per vehicle
moved, applied together (lane_correction.apply_lane_reassignments ->
IMPUTED_LANE, kinematics unchanged).
"""

import sqlite3
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from utils import events_columns, active_rows_clause, measured_rows_clause
from .anomaly_registry import AnomalyType, register_anomaly_type
from .lane_anomaly_store import LaneAnomaly

PROXIMITY_KEY = "proximity"
DEFAULT_THRESHOLD_M = 5.0


class ProximityType(AnomalyType):
    KEY = PROXIMITY_KEY
    LABEL = "Proximity"
    UI_MODULE = ".proximity_view"
    OPTIONS_SCHEMA: Dict[str, Any] = {
        "threshold_m": DEFAULT_THRESHOLD_M,   # Figure 2 "Proximity Threshold" (m)
        "max_gap_steps": 2,                   # missing grid steps a run may span
        "min_samples": 1,                     # shortest run reported
    }

    def detect(self, conn: sqlite3.Connection, event_id: int,
               veh_ids: Optional[Iterable[int]], options: Dict[str, Any],
               time_step: float) -> List[LaneAnomaly]:
        options = self.resolve_options(options)
        threshold = float(options["threshold_m"])
        max_gap = max(1, int(options["max_gap_steps"]))
        min_samples = max(1, int(options["min_samples"]))
        if threshold <= 0 or not time_step or time_step <= 0:
            return []

        cols = events_columns(conn)
        rows = conn.execute(
            f"SELECT veh_id, veh_time, lane_id, veh_dist_trav FROM events "
            f"WHERE event_id = ? AND lane_id IS NOT NULL AND veh_dist_trav IS NOT NULL"
            f"{active_rows_clause(cols)}{measured_rows_clause(cols)}",
            (event_id,)).fetchall()
        if len(rows) < 2:
            return []

        arr = np.asarray(rows, dtype=np.float64)
        veh, t, lane, dist = arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2], arr[:, 3]
        tick = np.rint(t / time_step).astype(np.int64)

        order = np.lexsort((dist, tick, lane))
        veh, t, lane, dist, tick = veh[order], t[order], lane[order], dist[order], tick[order]

        same = (lane[1:] == lane[:-1]) & (tick[1:] == tick[:-1]) & (veh[1:] != veh[:-1])
        spacing = dist[1:] - dist[:-1]
        hits = np.nonzero(same & (spacing < threshold))[0]

        # (veh_a, veh_b, lane) -> [(tick, spacing, follower, leader, t_f, t_l)]
        groups: Dict[Tuple[int, int, float], list] = defaultdict(list)
        for i in hits:
            f, l = int(veh[i]), int(veh[i + 1])
            key = (min(f, l), max(f, l), float(lane[i]))
            groups[key].append((int(tick[i]), float(spacing[i]), f, l,
                                float(t[i]), float(t[i + 1])))

        wanted = None if veh_ids is None else {int(v) for v in veh_ids}
        params = {"threshold_m": threshold}
        anomalies: List[LaneAnomaly] = []
        for (a, b, lane_id), samples in groups.items():
            if wanted is not None and a not in wanted and b not in wanted:
                continue
            samples.sort()
            run = [samples[0]]
            for s in samples[1:]:
                if s[0] - run[-1][0] > max_gap:
                    anomalies.extend(_run_anomaly(event_id, lane_id, run, params, min_samples))
                    run = []
                run.append(s)
            anomalies.extend(_run_anomaly(event_id, lane_id, run, params, min_samples))

        anomalies.sort(key=lambda x: (x.start_time, x.veh_id))
        return anomalies

    def describe(self, anomaly: LaneAnomaly) -> str:
        text = super().describe(anomaly)
        spacing = anomaly.metrics.get("min_spacing_m")
        return text if spacing is None else text.replace(
            "  (lane", f"  min {spacing:.1f} m  (lane", 1)


def _run_anomaly(event_id: int, lane_id: float, run: list, params: dict,
                 min_samples: int) -> List[LaneAnomaly]:
    if len(run) < min_samples:
        return []
    first = run[0]
    times = [s[4] for s in run] + [s[5] for s in run]
    spacings = [s[1] for s in run]
    return [LaneAnomaly(
        event_id=event_id, anomaly_type=PROXIMITY_KEY,
        veh_id=first[2], other_veh_id=first[3],
        start_time=min(times), end_time=max(times),
        lane_ids=[int(lane_id)], params=dict(params),
        metrics={"min_spacing_m": round(min(spacings), 4),
                 "mean_spacing_m": round(float(np.mean(spacings)), 4),
                 "samples": len(run)},
    )]


register_anomaly_type(ProximityType())