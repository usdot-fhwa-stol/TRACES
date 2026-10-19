"""Gap Interpolation (Task 9.1, Missing Data Identification and Interpolation).

Detects gaps, fills them with analysis.spline's spline fit, and lets the user
review and refine each gap.

    gap_interpolation_comp  detection, fill and gaps-table persistence
    gap_sort                swappable gap display order
    gap_config_dialog       first window: default parameters, auto-fill
    gap_review_dialog       second window: per-gap review and Add Points
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "GapInterpolationComp": ".gap_interpolation_comp",
    "GapResult": ".gap_interpolation_comp",
    "GapSpan": ".gap_interpolation_comp",
    "FillRow": ".gap_interpolation_comp",
    "GAP_STATUS_UNFILLED": ".gap_interpolation_comp",
    "find_gaps": ".gap_interpolation_comp",
    "sort_gaps": ".gap_sort",
    "default_gap_sort_key": ".gap_sort",
    "GapConfigDialog": ".gap_config_dialog",
    "GapReviewDialog": ".gap_review_dialog",
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
    from .gap_interpolation_comp import (
        GapInterpolationComp as GapInterpolationComp,
        GapResult as GapResult,
        GapSpan as GapSpan,
        FillRow as FillRow,
        GAP_STATUS_UNFILLED as GAP_STATUS_UNFILLED,
        find_gaps as find_gaps,
    )
    from .gap_sort import (
        sort_gaps as sort_gaps,
        default_gap_sort_key as default_gap_sort_key,
    )
    from .gap_config_dialog import GapConfigDialog as GapConfigDialog
    from .gap_review_dialog import GapReviewDialog as GapReviewDialog