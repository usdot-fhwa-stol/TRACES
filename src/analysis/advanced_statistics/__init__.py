"""Advanced Statistics (Task 9.4): leader / follower interactions and
lane-change gaps, opened from the time-space plot's right-click
Statistics > Advanced Statistics. Read-only: writes nothing to events.

    stat_registry                  the table's columns as data (Qt-free);
                                   add a statistic by adding an entry here
    leader_follower                leader / follower identification (Qt-free)
    lane_changes                   lane-change detection and gaps (Qt-free)
    advanced_stats_comp            AdvancedStatisticsComp (Qt-free)
    advanced_stats_dialog          parameter dialog
    advanced_stats_results_dialog  results table and CSV save
    plot_selection_dialog          Generate Plots variable selection
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "AdvancedStatisticsComp": ".advanced_stats_comp",
    "AdvancedStatsResult": ".advanced_stats_comp",
    "LeaderFollowerIndex": ".leader_follower",
    "LaneChange": ".lane_changes",
    "detect_lane_changes": ".lane_changes",
    "ADVANCED_STATISTICS": ".stat_registry",
    "VehicleData": ".stat_registry",
    "Column": ".stat_registry",
    "ScalarStat": ".stat_registry",
    "StatGroup": ".stat_registry",
    "AdvancedStatisticsDialog": ".advanced_stats_dialog",
    "AdvancedStatisticsResultsDialog": ".advanced_stats_results_dialog",
    "PlotVariableSelectionDialog": ".plot_selection_dialog",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value   # cache, so __getattr__ runs once per name
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


# Never runs. Read by IDEs and type checkers ("X as X" marks a re-export),
# and by PyInstaller's import scan, which can't follow import_module() above.
if TYPE_CHECKING:
    from .advanced_stats_comp import (
        AdvancedStatisticsComp as AdvancedStatisticsComp,
        AdvancedStatsResult as AdvancedStatsResult,
    )
    from .leader_follower import LeaderFollowerIndex as LeaderFollowerIndex
    from .lane_changes import (
        LaneChange as LaneChange,
        detect_lane_changes as detect_lane_changes,
    )
    from .stat_registry import (
        ADVANCED_STATISTICS as ADVANCED_STATISTICS,
        VehicleData as VehicleData,
        Column as Column,
        ScalarStat as ScalarStat,
        StatGroup as StatGroup,
    )
    from .advanced_stats_dialog import (
        AdvancedStatisticsDialog as AdvancedStatisticsDialog,
    )
    from .advanced_stats_results_dialog import (
        AdvancedStatisticsResultsDialog as AdvancedStatisticsResultsDialog,
    )
    from .plot_selection_dialog import (
        PlotVariableSelectionDialog as PlotVariableSelectionDialog,
    )
