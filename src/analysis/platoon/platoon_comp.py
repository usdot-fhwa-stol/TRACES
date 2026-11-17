"""Module 3 of NoiseAnalysisReport v2.3 (SI units).

Selections restrict followers only. Leader lookup uses the entire event.
A segment ends on a leader change, an invalid assignment, or a missing event
frame. Irregular event-wide timestamps are integrated using their actual dt.
"""
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
import math

import numpy as np

from analysis.base_comp import BaseComp, active_rows_clause, measured_rows_clause

NAN = float('nan')


def finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@dataclass
class PlatoonPairResult:
    event_id: int
    follower_id: int
    leader_id: int
    segment_id: int
    start_time: float
    end_time: float
    sample_count: int
    minimum_spacing: float
    min_bias: float
    max_bias: float
    mean_bias: float
    rmse_bias: float
    rmspe_bias: float
    valid_bias_count: int
    unphysical_spacing: bool


@dataclass
class PlatoonEventResult:
    event_id: int
    pairs: list = field(default_factory=list)
    id_flips: int = 0


def summarize(results):
    pairs = [p for result in results for p in result.pairs]
    count = sum(p.unphysical_spacing for p in pairs)
    return {
        'number_of_vehicle_pairs': len(pairs),
        'number_of_vehicle_pairs_with_unphysical_spacing': count,
        'percentage_of_vehicle_pairs_with_unphysical_spacing':
            100.0 * count / len(pairs) if pairs else NAN,
        'number_of_vehicle_pair_id_flips': sum(r.id_flips for r in results),
    }


class PlatoonConsistencyComp(BaseComp):
    OPTIONS_SCHEMA = {
        'unphysical_spacing_threshold': 5.0,
        'leader_search_distance_threshold': 50.0,
    }

    def comp_series(self, db_connection, event_id, veh_ids, options=None):
        options = self._resolve_options(options)
        threshold = float(options['unphysical_spacing_threshold'])
        search_distance = float(options['leader_search_distance_threshold'])
        if not finite(threshold) or threshold < 0:
            raise ValueError('Unphysical spacing threshold must be finite and nonnegative.')
        if not finite(search_distance) or search_distance <= 0:
            raise ValueError('Leader search distance must be finite and positive.')
        cols = self._events_columns(db_connection)
        required = {'event_id', 'veh_id', 'veh_time'}
        if not required <= cols:
            raise ValueError('Platoon consistency requires event_id, veh_id and veh_time.')
        supplied = 'preceding_veh_id' in cols and bool({'spacing', 'veh_spacing'} & cols)
        search = {'lane_id', 'veh_dist_trav'} <= cols
        if not supplied and not search:
            raise ValueError('Provide preceding_veh_id and spacing (or veh_spacing), '
                             'or lane_id and veh_dist_trav for leader search.')
        names = ['veh_id', 'veh_time'] + [c for c in (
            'lane_id', 'veh_dist_trav', 'veh_speed', 'preceding_veh_id',
            'spacing', 'veh_spacing') if c in cols]
        cursor = db_connection.execute(
            f"SELECT {', '.join(names)} FROM events WHERE event_id = ?"
            f"{active_rows_clause(cols)}{measured_rows_clause(cols)} ORDER BY veh_time, veh_id",
            (event_id,))
        rows = [dict(zip(names, row)) for row in cursor]
        lookup = {}
        frames = set()
        trajectories = defaultdict(list)
        lanes = defaultdict(list)
        for row in rows:
            if row['veh_id'] is None or not finite(row['veh_time']):
                continue
            row['veh_time'] = float(row['veh_time'])
            key = (row['veh_id'], row['veh_time'])
            if key in lookup:
                raise ValueError(f'Duplicate vehicle/time records in event {event_id}: {key}.')
            lookup[key] = row
            frames.add(row['veh_time'])
            trajectories[row['veh_id']].append(row)
            if search and row.get('lane_id') is not None and finite(row.get('veh_dist_trav')):
                lanes[(row['veh_time'], row['lane_id'])].append(
                    (float(row['veh_dist_trav']), row['veh_id']))
        lane_positions = {}
        for key, candidates in lanes.items():
            candidates.sort()
            lane_positions[key] = [c[0] for c in candidates]
        frame_index = {t: i for i, t in enumerate(sorted(frames))}
        selected = set(trajectories) if veh_ids is None else set(veh_ids)
        result = PlatoonEventResult(event_id)
        orientations = defaultdict(lambda: defaultdict(set))
        for follower in sorted(selected):
            segment = []
            segment_id = 0

            def finish():
                nonlocal segment, segment_id
                if segment:
                    segment_id += 1
                    result.pairs.append(self._pair_result(
                        event_id, follower, segment_id, segment, threshold))
                    segment = []

            for row in trajectories.get(follower, []):
                time = row['veh_time']
                leader = row.get('preceding_veh_id')
                spacing = next((float(row[c]) for c in ('spacing', 'veh_spacing')
                                if finite(row.get(c))), NAN)
                if not (finite(leader) and leader != follower and finite(spacing)):
                    leader, spacing = None, NAN
                    if search and finite(row.get('veh_dist_trav')):
                        key = (time, row.get('lane_id'))
                        candidates = lanes.get(key, [])
                        position = float(row['veh_dist_trav'])
                        idx = bisect_right(lane_positions.get(key, []), position)
                        if idx < len(candidates):
                            ahead, candidate = candidates[idx]
                            if ahead - position <= search_distance:
                                leader, spacing = candidate, ahead - position
                if leader is None:
                    finish()
                    continue
                leader_row = lookup.get((leader, time), {})
                if segment and (leader != segment[-1][1] or
                                frame_index[time] != frame_index[segment[-1][0]] + 1):
                    finish()
                segment.append((time, leader, spacing, row.get('veh_speed'),
                                leader_row.get('veh_speed')))
                pair_key = tuple(sorted((follower, leader)))
                orientations[pair_key][time].add((follower, leader))
            finish()
        # Simultaneous reciprocal assignments are ambiguous, not temporal flips.
        for observations in orientations.values():
            previous = None
            for time in sorted(observations):
                values = observations[time]
                if len(values) != 1:
                    previous = None
                    continue
                current = next(iter(values))
                if previous is not None and current == previous[::-1]:
                    result.id_flips += 1
                previous = current
        return [result]

    @staticmethod
    def _pair_result(event_id, follower, segment_id, segment, threshold):
        times = np.array([r[0] for r in segment])
        spacing = np.array([r[2] for r in segment])
        speeds = np.array([[float(v) if finite(v) else NAN for v in r[3:5]]
                           for r in segment])
        bias = np.full(len(segment), NAN)
        # Missing speeds leave spacing metrics usable, but cannot establish a
        # cumulative motion integral. Never bridge or restart inside a segment.
        if np.isfinite(speeds[0]).all():
            bias[0] = 0.0
            relative = speeds[:, 1] - speeds[:, 0]
            cumulative = spacing[0]
            for i in range(1, len(segment)):
                if not np.isfinite(speeds[i - 1:i + 1]).all():
                    break
                cumulative += 0.5 * (relative[i - 1] + relative[i]) * (times[i] - times[i - 1])
                bias[i] = cumulative - spacing[i]
        valid = bias[np.isfinite(bias)]
        percent_mask = np.isfinite(bias) & (spacing != 0)
        relative_bias = bias[percent_mask] / spacing[percent_mask]
        return PlatoonPairResult(
            event_id, follower, segment[0][1], segment_id,
            float(times[0]), float(times[-1]), len(segment), float(spacing.min()),
            float(valid.min()) if valid.size else NAN,
            float(valid.max()) if valid.size else NAN,
            float(valid.mean()) if valid.size else NAN,
            float(np.sqrt(np.mean(valid ** 2))) if valid.size else NAN,
            float(np.sqrt(np.mean(relative_bias ** 2))) if relative_bias.size else NAN,
            len(valid), bool(spacing.min() < threshold))
