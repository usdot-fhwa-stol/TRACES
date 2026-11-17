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

Spacing conflicts of a pending fix
----------------------------------
A lane reassignment of any anomaly type can itself put a vehicle closer
than the proximity threshold to traffic in its new lane -- a new Proximity
anomaly that only the next detection run would report. spacing_conflicts()
finds those for a fix preview, with the same rule as detection (same lane,
same grid step, center-to-center spacing below the threshold), and
proximity_threshold() supplies the threshold the event was last checked
with.
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from utils import events_columns, active_rows_clause, measured_rows_clause
from .anomaly_registry import AnomalyType, register_anomaly_type
from .lane_anomaly_store import ANOMALIES_TABLE, LaneAnomaly, lane_tables_exist

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


# ── Spacing conflicts of a pending fix ──────────────────────────────────────

def proximity_threshold(conn: sqlite3.Connection, event_id: int) -> Tuple[float, bool]:
    """(threshold in m, whether it came from a detection run): the
    threshold of the event's most recently stored Proximity anomaly, or
    DEFAULT_THRESHOLD_M when none is stored. A run that found no Proximity
    anomalies leaves no record of its threshold, so an earlier run's is
    used then. Read-only."""
    if lane_tables_exist(conn):
        row = conn.execute(
            f"SELECT params_json FROM {ANOMALIES_TABLE} "
            f"WHERE event_id = ? AND anomaly_type = ? "
            f"ORDER BY created_at DESC, anomaly_id DESC LIMIT 1",
            (event_id, PROXIMITY_KEY)).fetchone()
        if row is not None:
            try:
                value = float(json.loads(row[0]).get("threshold_m"))
            except (TypeError, ValueError, AttributeError):
                value = 0.0
            if value > 0:
                return value, True
    return DEFAULT_THRESHOLD_M, False


@dataclass(frozen=True)
class SpacingConflict:
    """A vehicle a fix moves that would come closer than the threshold to
    another vehicle in its new lane: the closest approach, and how many of
    its moved samples are too close."""
    veh_id: int
    other_veh_id: int
    lane_id: int
    time: float
    spacing_m: float
    samples: int


def spacing_conflicts(moves: Sequence[Tuple[int, np.ndarray, np.ndarray, int]],
                      occupants: Dict[int, Dict[str, np.ndarray]],
                      threshold_m: float, time_step: float) -> List[SpacingConflict]:
    """For each move (veh_id, times, distances, to_lane) -- the samples a
    fix would put in *to_lane* -- the closest vehicle in *occupants* in
    that lane at the same grid step, when closer than *threshold_m*.
    *occupants* is {veh_id: {"time", "dist", "lane"}} as the lanes would
    be after the fix (the fix's own vehicles included, already moved); a
    vehicle is never compared with itself. At most one conflict per moved
    vehicle, sorted by spacing. Pure."""
    if threshold_m <= 0 or not time_step or time_step <= 0 or not moves:
        return []
    wanted = {int(lane) for *_, lane in moves}
    by_slot: Dict[Tuple[int, int], List[Tuple[int, float]]] = defaultdict(list)
    for veh, r in occupants.items():
        for t, d, lane in zip(r["time"], r["dist"], r["lane"]):
            if lane is None or not np.isfinite(d) or int(lane) not in wanted:
                continue
            by_slot[(int(lane), int(np.rint(t / time_step)))].append((int(veh), float(d)))

    out: List[SpacingConflict] = []
    for veh, times, dists, lane in moves:
        veh, lane = int(veh), int(lane)
        worst: Optional[Tuple[float, int, float]] = None   # (spacing, other, time)
        too_close = 0
        for t, d in zip(times, dists):
            if not np.isfinite(d):
                continue
            near = [(abs(d - od), other)
                    for other, od in by_slot.get((lane, int(np.rint(t / time_step))), ())
                    if other != veh]
            if not near:
                continue
            spacing, other = min(near)
            if spacing < threshold_m:
                too_close += 1
                if worst is None or spacing < worst[0]:
                    worst = (spacing, other, float(t))
        if worst is not None:
            out.append(SpacingConflict(veh, worst[1], lane, worst[2],
                                       round(float(worst[0]), 4), too_close))
    out.sort(key=lambda c: c.spacing_m)
    # Two moved vehicles too close to each other are one conflict, not two.
    pairs, unique = set(), []
    for c in out:
        pair = frozenset((c.veh_id, c.other_veh_id))
        if pair not in pairs:
            pairs.add(pair)
            unique.append(c)
    return unique


register_anomaly_type(ProximityType())