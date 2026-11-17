"""Internal Consistency analysis (noise analysis Module 4).

Reconstructs speed from distance, acceleration from speed and jerk from
acceleration, and reports the error against each measured column.

    internal_consistency_comp           InternalConsistencyComp (Qt-free)
    internal_consistency_dialog         parameter dialog, writes results to events
    internal_consistency_result_dialog  right-click plots + tables
    internal_consistency_stats_dialog   top-level statistics summary
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "InternalConsistencyComp": ".internal_consistency_comp",
    "InternalConsistencyResult": ".internal_consistency_comp",
    "InternalConsistencyDialog": ".internal_consistency_dialog",
    "InternalConsistencyResultDialog": ".internal_consistency_result_dialog",
    "InternalConsistencyStatsDialog": ".internal_consistency_stats_dialog",
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
    from .internal_consistency_comp import (
        InternalConsistencyComp as InternalConsistencyComp,
        InternalConsistencyResult as InternalConsistencyResult,
    )
    from .internal_consistency_dialog import (
        InternalConsistencyDialog as InternalConsistencyDialog,
    )
    from .internal_consistency_result_dialog import (
        InternalConsistencyResultDialog as InternalConsistencyResultDialog,
    )
    from .internal_consistency_stats_dialog import (
        InternalConsistencyStatsDialog as InternalConsistencyStatsDialog,
    )