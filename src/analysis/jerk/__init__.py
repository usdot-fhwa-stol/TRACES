"""Jerk analysis (noise analysis Module 1).

    jerk_comp                  JerkComp (Qt-free)
    jerk_comp_dialog           parameter dialog (top-level, right-click, export)
    jerk_box_plot_dialog       top-level result: per-trajectory box plots
    jerk_results_table_dialog  right-click result: per-trajectory table
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "JerkComp": ".jerk_comp",
    "JerkResult": ".jerk_comp",
    "JerkCompDialog": ".jerk_comp_dialog",
    "JerkBoxPlotDialog": ".jerk_box_plot_dialog",
    "JerkResultsTableDialog": ".jerk_results_table_dialog",
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
    from .jerk_comp import JerkComp as JerkComp, JerkResult as JerkResult
    from .jerk_comp_dialog import JerkCompDialog as JerkCompDialog
    from .jerk_box_plot_dialog import JerkBoxPlotDialog as JerkBoxPlotDialog
    from .jerk_results_table_dialog import (
        JerkResultsTableDialog as JerkResultsTableDialog,
    )