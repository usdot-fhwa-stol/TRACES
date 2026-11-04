"""Spline Fitting.

    spline_comp            SplineComp and the shared spline math (Qt-free);
                           also used by analysis.gap_interpolation
    spline_comp_dialog     top-level dialog, writes calc_* columns
    spline_stats_dialog    top-level result: goodness-of-fit table
    spline_testing_dialog  right-click preview, never writes to the database
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "SplineComp": ".spline_comp",
    "SplineRowResult": ".spline_comp",
    "SplineFitResult": ".spline_comp",
    "apply_spline_fit": ".spline_comp",
    "compute_error_metrics": ".spline_comp",
    "summarize_fit": ".spline_comp",
    "nearest_valid_ic": ".spline_comp",
    "allowed_k_options": ".spline_comp",
    "FITTING_BASES": ".spline_comp",
    "KINEMATICS": ".spline_comp",
    "BASE_LABELS": ".spline_comp",
    "SplineCompDialog": ".spline_comp_dialog",
    "SplineStatsDialog": ".spline_stats_dialog",
    "SplineTestingDialog": ".spline_testing_dialog",
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
    from .spline_comp import (
        SplineComp as SplineComp,
        SplineRowResult as SplineRowResult,
        SplineFitResult as SplineFitResult,
        apply_spline_fit as apply_spline_fit,
        compute_error_metrics as compute_error_metrics,
        summarize_fit as summarize_fit,
        nearest_valid_ic as nearest_valid_ic,
        allowed_k_options as allowed_k_options,
        FITTING_BASES as FITTING_BASES,
        KINEMATICS as KINEMATICS,
        BASE_LABELS as BASE_LABELS,
    )
    from .spline_comp_dialog import SplineCompDialog as SplineCompDialog
    from .spline_stats_dialog import SplineStatsDialog as SplineStatsDialog
    from .spline_testing_dialog import SplineTestingDialog as SplineTestingDialog