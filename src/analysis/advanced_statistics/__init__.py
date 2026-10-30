"""Advanced Statistics module (Task 9.4): leader / follower interactions
and lane-change gaps, right-click Statistics > Advanced Statistics.

Public names are imported lazily (PEP 562), so importing the package does
not import Qt.
"""

import importlib

_EXPORTS = {
    "AdvancedStatisticsDialog": ".advanced_stats_dialog",
    "AdvancedStatisticsResultsDialog": ".advanced_stats_results_dialog",
    "PlotVariableSelectionDialog": ".plot_selection_dialog",
    "AdvancedStatisticsComp": ".advanced_stats_comp",
    "AdvancedStatsResult": ".advanced_stats_comp",
    "LeaderFollowerIndex": ".leader_follower",
    "LaneChange": ".lane_changes",
    "detect_lane_changes": ".lane_changes",
    "ADVANCED_STATISTICS": ".stat_registry",
    "ScalarStat": ".stat_registry",
    "StatGroup": ".stat_registry",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value
