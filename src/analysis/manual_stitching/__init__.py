"""Manual Trajectory Stitching (Task 9.2).

    stitching_comp            ManualStitchingComp and the Qt-free stitch plan
    safety_rules              pure warn-only safety checks for a selected pair
    stitching_preview_dialog  preview window that applies the stitch
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "ManualStitchingComp": ".stitching_comp",
    "StitchInputs": ".stitching_comp",
    "StitchPlan": ".stitching_comp",
    "StitchPreview": ".stitching_comp",
    "SeamInfo": ".stitching_comp",
    "JoinMetrics": ".stitching_comp",
    "TemporalCase": ".stitching_comp",
    "TimeRange": ".stitching_comp",
    "STITCHES_TABLE": ".stitching_comp",
    "SafetyContext": ".safety_rules",
    "SafetyRule": ".safety_rules",
    "SAFETY_RULES": ".safety_rules",
    "evaluate_safety": ".safety_rules",
    "StitchingPreviewDialog": ".stitching_preview_dialog",
    "StitchResult": ".stitching_preview_dialog",
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
    from .stitching_comp import (
        ManualStitchingComp as ManualStitchingComp,
        StitchInputs as StitchInputs,
        StitchPlan as StitchPlan,
        StitchPreview as StitchPreview,
        SeamInfo as SeamInfo,
        JoinMetrics as JoinMetrics,
        TemporalCase as TemporalCase,
        TimeRange as TimeRange,
        STITCHES_TABLE as STITCHES_TABLE,
    )
    from .safety_rules import (
        SafetyContext as SafetyContext,
        SafetyRule as SafetyRule,
        SAFETY_RULES as SAFETY_RULES,
        evaluate_safety as evaluate_safety,
    )
    from .stitching_preview_dialog import (
        StitchingPreviewDialog as StitchingPreviewDialog,
        StitchResult as StitchResult,
    )