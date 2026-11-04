"""Trajectory analysis modules.

Each analysis lives in its own subpackage (advanced_statistics,
gap_interpolation, internal_consistency, jerk, lane_anomaly,
manual_stitching, missing_data, shockwave, spline) and is split into
Qt-free computation modules and dialog modules that collect parameters
and show results.

The modules at this level are shared by every analysis:
    base_comp             BaseComp and the events-table schema helpers (Qt-free)
    base_analysis_dialog  BaseAnalysisDialog, the parameter/progress dialog
    base_analysis_worker  BaseAnalysisWorker, the dialog's QThread worker
    trajectory_utils      Qt-free trajectory loading and sampling helpers

Dependency rule: analysis may use utils, data_import and ui_kit, but never
plot or gui; those import analysis, not the reverse.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "BaseComp": ".base_comp",
    "DERIVED_EVENTS_COLUMNS": ".base_comp",
    "ensure_imputed_status_column": ".base_comp",
    "ensure_traj_status_column": ".base_comp",
    "BaseAnalysisDialog": ".base_analysis_dialog",
    "BaseAnalysisWorker": ".base_analysis_worker",
    "DEFAULT_TIME_STEP": ".trajectory_utils",
    "TrajectoryData": ".trajectory_utils",
    "load_trajectory": ".trajectory_utils",
    "estimate_time_step": ".trajectory_utils",
    "event_time_step": ".trajectory_utils",
    "generate_complete_timestamps": ".trajectory_utils",
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
    from .base_comp import (
        BaseComp as BaseComp,
        DERIVED_EVENTS_COLUMNS as DERIVED_EVENTS_COLUMNS,
        ensure_imputed_status_column as ensure_imputed_status_column,
        ensure_traj_status_column as ensure_traj_status_column,
    )
    from .base_analysis_dialog import BaseAnalysisDialog as BaseAnalysisDialog
    from .base_analysis_worker import BaseAnalysisWorker as BaseAnalysisWorker
    from .trajectory_utils import (
        DEFAULT_TIME_STEP as DEFAULT_TIME_STEP,
        TrajectoryData as TrajectoryData,
        load_trajectory as load_trajectory,
        estimate_time_step as estimate_time_step,
        event_time_step as event_time_step,
        generate_complete_timestamps as generate_complete_timestamps,
    )