"""The Advanced Statistics table's columns, as data (Task 9.4, Figure 2).
Qt-free.

The results table, its CSV and the Generate Plots variable list are all
built from ADVANCED_STATISTICS, so adding a statistic ("additional
interaction statistics specified by Leidos") is adding one entry here:

    ScalarStat   one column computed from a vehicle's VehicleData
    StatGroup    a quantity with the full statistic package -- mean,
                 minimum, maximum, Q1 and Q3 columns -- computed from the
                 vehicle's samples of it; it can be box-plotted

Summary row
-----------
The table ends with an "All" row over every vehicle in it. A StatGroup's
summary columns are computed from all vehicles' samples pooled. A
ScalarStat says how it is summarized (ScalarStat.summary):

    "min" / "max" / "sum" / "mean"   of the vehicles' values
    a callable(results) -> value     anything else
    None                             left blank

Spec wording fixes: Task 9.4's Max Speed / Min Speed descriptions are
swapped, its "25th / 75th Quartile" are the first and third quartiles
(Q1 / Q3), and the accepted-gap header's "(Mean, Median, Outliers)" is
dropped -- no median column is specified.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .lane_changes import LaneChange
from .leader_follower import NO_VEHICLE, TickSeries, Trajectory

# The full statistic package of a StatGroup: (suffix, short name, function).
PACKAGE: Tuple[Tuple[str, str, Callable[[np.ndarray], float]], ...] = (
    ("mean", "Mean",    lambda a: float(np.mean(a))),
    ("min",  "Minimum", lambda a: float(np.min(a))),
    ("max",  "Maximum", lambda a: float(np.max(a))),
    ("q1",   "Q1",      lambda a: float(np.percentile(a, 25))),
    ("q3",   "Q3",      lambda a: float(np.percentile(a, 75))),
)

Samples = Tuple[np.ndarray, np.ndarray]     # (values, times)


@dataclass
class VehicleData:
    """Everything a statistic may read for one vehicle."""
    traj: Trajectory
    series: Optional[TickSeries]       # None when it has no grid ticks
    lane_changes: List[LaneChange]
    step: float

    def tick_times(self) -> np.ndarray:
        return (self.series.tick * self.step if self.series is not None
                else np.array([], dtype=np.float64))


@dataclass
class Column:
    """One table / CSV column."""
    key: str
    header: str              # table header (may contain a line break)
    name: str                # CSV header and tooltip title
    description: str
    fmt: str                 # format for a number, e.g. "{:.2f}"


@dataclass
class ScalarStat:
    key: str
    name: str
    unit: str
    description: str
    value: Callable[[VehicleData], Any]
    fmt: str = "{:.2f}"
    summary: Union[None, str, Callable[[list], Any]] = None

    def columns(self) -> List[Column]:
        unit = f" ({self.unit})" if self.unit else ""
        return [Column(self.key, f"{self.name}{unit}", f"{self.name}{unit}",
                       self.description, self.fmt)]


@dataclass
class StatGroup:
    key: str
    name: str                 # e.g. "Leader Gap"
    unit: str
    description: str          # completes "... of ...", e.g. "the distance to the leader"
    samples: Callable[[VehicleData], Samples]
    fmt: str = "{:.2f}"
    plot_label: Optional[str] = None     # Generate Plots checkbox text

    def column_key(self, suffix: str) -> str:
        return f"{self.key}_{suffix}"

    def columns(self) -> List[Column]:
        unit = f" ({self.unit})" if self.unit else ""
        return [Column(self.column_key(suffix), f"{short}\n{self.name}{unit}",
                       f"{short} {self.name}{unit}", f"{short} of {self.description}.",
                       self.fmt)
                for suffix, short, _fn in PACKAGE]

    def package(self, values: np.ndarray) -> Dict[str, float]:
        values = values[np.isfinite(values)]
        if values.size == 0:
            return {self.column_key(s): np.nan for s, _n, _f in PACKAGE}
        return {self.column_key(s): fn(values) for s, _n, fn in PACKAGE}


Statistic = Union[ScalarStat, StatGroup]


# ── Sample and value functions ─────────────────────────────────────────────

def _speed(d: VehicleData) -> Samples:
    if d.traj.speed is None:
        return np.array([]), np.array([])
    return d.traj.speed, d.traj.time


def _gap(attr: str) -> Callable[[VehicleData], Samples]:
    def samples(d: VehicleData) -> Samples:
        if d.series is None:
            return np.array([]), np.array([])
        gaps = getattr(d.series, attr)
        keep = np.isfinite(gaps)
        return gaps[keep], d.tick_times()[keep]
    return samples


def _lane_change_gap(attr: str) -> Callable[[VehicleData], Samples]:
    def samples(d: VehicleData) -> Samples:
        values = np.array([getattr(c, attr) for c in d.lane_changes], dtype=np.float64)
        times = np.array([c.time for c in d.lane_changes], dtype=np.float64)
        keep = np.isfinite(values)
        return values[keep], times[keep]
    return samples


def _ids(attr: str) -> Callable[[VehicleData], np.ndarray]:
    def ids(d: VehicleData) -> np.ndarray:
        if d.series is None:
            return np.array([], dtype=np.int64)
        a = getattr(d.series, attr)
        return a[a != NO_VEHICLE]
    return ids


def _unique(attr: str) -> Callable[[VehicleData], int]:
    return lambda d: int(np.unique(_ids(attr)(d)).size)


def _changes(attr: str) -> Callable[[VehicleData], int]:
    """Changes from one leader (follower) to a different one; a spell with
    none between two vehicles counts once, between the same vehicle not
    at all."""
    def count(d: VehicleData) -> int:
        ids = _ids(attr)(d)
        return int(np.count_nonzero(ids[1:] != ids[:-1])) if ids.size > 1 else 0
    return count


def longest_run(d: VehicleData, attr: str) -> Tuple[float, Optional[int]]:
    """(seconds, vehicle) of the longest uninterrupted spell with one
    leader (follower): consecutive grid ticks with the same vehicle, in
    one lane. A tick without one, a data gap or a lane change ends the
    spell. Its length is last tick - first tick. (0.0, None) when there is
    no spell."""
    s = d.series
    if s is None or s.tick.size == 0:
        return 0.0, None
    ids = getattr(s, attr)
    breaks = ((np.diff(s.tick) != 1) | (ids[1:] != ids[:-1])
              | (s.lane[1:] != s.lane[:-1]))
    starts = np.concatenate(([0], np.flatnonzero(breaks) + 1))
    ends = np.concatenate((starts[1:], [ids.size]))
    best, who = -1.0, None
    for a, b in zip(starts, ends):
        if ids[a] == NO_VEHICLE:
            continue
        length = (s.tick[b - 1] - s.tick[a]) * d.step
        if length > best:
            best, who = float(length), int(ids[a])
    return (best, who) if who is not None else (0.0, None)


def _summary_max_with_id(results: list, time_key: str, id_key: str) -> Any:
    """Summary of a "Maximum-Time ... ID" column: the ID belonging to the
    largest time over all vehicles."""
    rows = [r.values for r in results if r.values.get(id_key) is not None]
    if not rows:
        return None
    return max(rows, key=lambda v: v.get(time_key) or 0.0)[id_key]


# ── The table (Task 9.4, Figure 2) ─────────────────────────────────────────

ADVANCED_STATISTICS: List[Statistic] = [
    ScalarStat("start_time", "Start Time", "s", "First valid timestamp of the trajectory.",
               lambda d: float(d.traj.time[0]), summary="min"),
    ScalarStat("end_time", "End Time", "s", "Last valid timestamp of the trajectory.",
               lambda d: float(d.traj.time[-1]), summary="max"),
    ScalarStat("duration", "Duration", "s", "Total duration of the trajectory "
               "(summary row: sum over the vehicles).",
               lambda d: float(d.traj.time[-1] - d.traj.time[0]), summary="sum"),
    ScalarStat("distance", "Total Distance Travelled", "m", "Total distance travelled by "
               "the trajectory (summary row: sum over the vehicles).",
               lambda d: float(d.traj.dist[-1] - d.traj.dist[0]), summary="sum"),
    StatGroup("speed", "Speed", "m/s", "the trajectory's speed (veh_speed)", _speed,
              plot_label="Speed"),
    ScalarStat("unique_leaders", "Unique Leaders", "", "Number of distinct vehicles "
               "identified as leaders (summary row: mean per vehicle).",
               _unique("leader_id"), fmt="{:d}", summary="mean"),
    ScalarStat("unique_followers", "Unique Followers", "", "Number of distinct vehicles "
               "identified as followers (summary row: mean per vehicle).",
               _unique("follower_id"), fmt="{:d}", summary="mean"),
    ScalarStat("leader_changes", "Leader Changes", "", "Number of times the assigned "
               "leader changes to a different vehicle (summary row: sum).",
               _changes("leader_id"), fmt="{:d}", summary="sum"),
    ScalarStat("follower_changes", "Follower Changes", "", "Number of times the assigned "
               "follower changes to a different vehicle (summary row: sum).",
               _changes("follower_id"), fmt="{:d}", summary="sum"),
    ScalarStat("max_following_time", "Maximum Continuous Following Time", "s",
               "Longest uninterrupted time spent following one leader (summary row: "
               "maximum).", lambda d: longest_run(d, "leader_id")[0], summary="max"),
    ScalarStat("max_time_leader", "Maximum-Time Leader ID", "", "Vehicle ID of the leader "
               "of the longest continuous following interval.",
               lambda d: longest_run(d, "leader_id")[1], fmt="{:d}",
               summary=lambda results: _summary_max_with_id(
                   results, "max_following_time", "max_time_leader")),
    ScalarStat("max_followed_time", "Maximum Continuous Followed Time", "s",
               "Longest uninterrupted time spent being followed by one follower (summary "
               "row: maximum).", lambda d: longest_run(d, "follower_id")[0], summary="max"),
    ScalarStat("max_time_follower", "Maximum-Time Follower ID", "", "Vehicle ID of the "
               "follower of the longest continuous followed interval.",
               lambda d: longest_run(d, "follower_id")[1], fmt="{:d}",
               summary=lambda results: _summary_max_with_id(
                   results, "max_followed_time", "max_time_follower")),
    StatGroup("leader_gap", "Leader Gap", "m", "the longitudinal distance to the assigned "
              "leader", _gap("leader_gap"), plot_label="Leader Gap"),
    StatGroup("follower_gap", "Follower Gap", "m", "the longitudinal distance to the "
              "assigned follower", _gap("follower_gap"), plot_label="Follower Gap"),
    ScalarStat("lane_changes", "Number of Lane Changes", "", "Number of valid lane-change "
               "events detected (summary row: sum).",
               lambda d: len(d.lane_changes), fmt="{:d}", summary="sum"),
    StatGroup("accepted_gap", "Accepted Gap Space Before Lane Change", "m",
              "the target-lane gap available before lane changes",
              _lane_change_gap("accepted_gap"),
              plot_label="Accepted Gap Space Before Lane Change"),
    StatGroup("front_gap", "Front Gap After Lane Change", "m",
              "the distance to the new leader after lane changes",
              _lane_change_gap("front_gap"), plot_label="Front Gap After Lane Change"),
    StatGroup("rear_gap", "Rear Gap After Lane Change", "m",
              "the distance to the new follower after lane changes",
              _lane_change_gap("rear_gap"), plot_label="Rear Gap After Lane Change"),
]

VEHICLE_ID_COLUMN = Column("veh_id", "Vehicle ID", "Vehicle ID",
                           "Identifier of the analyzed trajectory.", "{}")


def table_columns(stats: Sequence[Statistic] = ADVANCED_STATISTICS) -> List[Column]:
    cols = [VEHICLE_ID_COLUMN]
    for stat in stats:
        cols.extend(stat.columns())
    return cols


def plot_groups(stats: Sequence[Statistic] = ADVANCED_STATISTICS) -> List[StatGroup]:
    """Statistics with the full package -- the Generate Plots variables."""
    return [s for s in stats if isinstance(s, StatGroup) and s.plot_label]


def summarize(results: list, stats: Sequence[Statistic] = ADVANCED_STATISTICS
              ) -> Dict[str, Any]:
    """The summary ("All") row's values over *results* (module docstring)."""
    out: Dict[str, Any] = {"veh_id": "All"}
    for stat in stats:
        if isinstance(stat, StatGroup):
            pooled = [r.samples[stat.key][0] for r in results if stat.key in r.samples]
            values = np.concatenate(pooled) if pooled else np.array([])
            out.update(stat.package(values))
            continue
        vals = [r.values.get(stat.key) for r in results]
        nums = np.array([v for v in vals if v is not None and np.isfinite(v)], dtype=np.float64)
        how = stat.summary
        if callable(how):
            out[stat.key] = how(results)
        elif how is None or nums.size == 0:
            out[stat.key] = None
        elif how == "mean":
            out[stat.key] = float(nums.mean())
        else:
            value = {"min": nums.min, "max": nums.max, "sum": nums.sum}[how]()
            out[stat.key] = int(value) if stat.fmt == "{:d}" else float(value)
    return out
