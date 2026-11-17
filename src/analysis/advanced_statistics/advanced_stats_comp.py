"""Advanced Statistics computation (Task 9.4). Qt-free and read-only.

AdvancedStatisticsComp.comp_series() builds the event's
LeaderFollowerIndex from every active trajectory -- even when only some
vehicles are reported (Task 9.4 N.B.) -- detects each reported vehicle's
valid lane changes and their gaps, and evaluates every statistic of
stat_registry.ADVANCED_STATISTICS for it.

Options:
    min_dwell_s  seconds a vehicle must stay in a new lane for the move to
                 count as a lane change (lane_changes module docstring)
    max_gap_m    a vehicle further than this ahead / behind is not a
                 leader / follower; 0 = no limit

Each AdvancedStatsResult keeps, besides the table values, the samples
behind every StatGroup, so the summary row and the box plots can pool
them across vehicles.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from utils import event_time_step
from ..base_comp import BaseComp
from .lane_changes import DEFAULT_MIN_DWELL_S, LaneChange, detect_lane_changes, measure_gaps
from .leader_follower import LeaderFollowerIndex, load_trajectories
from .stat_registry import ADVANCED_STATISTICS, Samples, StatGroup, VehicleData


@dataclass
class AdvancedStatsResult:
    event_id: int
    veh_id: int
    values: Dict[str, Any] = field(default_factory=dict)     # column key -> value
    samples: Dict[str, Samples] = field(default_factory=dict)  # StatGroup key -> samples
    lane_changes: List[LaneChange] = field(default_factory=list)


class AdvancedStatisticsComp(BaseComp):

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "min_dwell_s": DEFAULT_MIN_DWELL_S,
        "max_gap_m": 0.0,
    }

    def comp_series(self, db_connection: sqlite3.Connection, event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None,
                    progress_callback=None) -> List[AdvancedStatsResult]:
        """One result per vehicle of *veh_ids* (None = every active vehicle
        of the event) that has measured data, ordered by vehicle ID.
        progress_callback ticks once after the index is built and once per
        reported vehicle."""
        options = self._resolve_options(options)
        min_dwell = float(options["min_dwell_s"])
        trajectories = load_trajectories(db_connection, event_id)
        index = LeaderFollowerIndex(trajectories, event_time_step(db_connection, event_id),
                                    max_gap=float(options["max_gap_m"] or 0.0))
        if progress_callback is not None:
            progress_callback()

        wanted = sorted(trajectories) if veh_ids is None else sorted({int(v) for v in veh_ids})
        results: List[AdvancedStatsResult] = []
        for veh in wanted:
            traj = trajectories.get(veh)
            if traj is not None and traj.time.size:
                results.append(self.vehicle_result(event_id, traj, index, min_dwell))
            if progress_callback is not None:
                progress_callback()
        return results

    @staticmethod
    def vehicle_result(event_id: int, traj, index: LeaderFollowerIndex,
                       min_dwell_s: float = DEFAULT_MIN_DWELL_S,
                       stats=ADVANCED_STATISTICS) -> AdvancedStatsResult:
        changes = [measure_gaps(c, traj, index) for c in detect_lane_changes(traj, min_dwell_s)]
        data = VehicleData(traj=traj, series=index.series.get(traj.veh_id),
                           lane_changes=changes, step=index.step)
        result = AdvancedStatsResult(event_id=event_id, veh_id=traj.veh_id,
                                     lane_changes=changes)
        result.values["veh_id"] = traj.veh_id
        for stat in stats:
            if isinstance(stat, StatGroup):
                values, times = stat.samples(data)
                result.samples[stat.key] = (values, times)
                result.values.update(stat.package(values))
            else:
                result.values[stat.key] = stat.value(data)
        return result
