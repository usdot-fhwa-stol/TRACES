"""Missing Data analysis (noise analysis Module 2): gap statistics only.

Filling gaps is a separate module, analysis.gap_interpolation.

    missing_data_comp                  MissingDataComp (Qt-free)
    missing_data_comp_dialog           parameter dialog (top-level, right-click, export)
    missing_data_box_plot_dialog       top-level result: box plots
    missing_data_results_table_dialog  right-click result: per-gap table
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "MissingDataComp": ".missing_data_comp",
    "MissingDataResult": ".missing_data_comp",
    "MissingDataCompDialog": ".missing_data_comp_dialog",
    "MissingDataBoxPlotDialog": ".missing_data_box_plot_dialog",
    "MissingDataResultsTableDialog": ".missing_data_results_table_dialog",
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
    from .missing_data_comp import (
        MissingDataComp as MissingDataComp,
        MissingDataResult as MissingDataResult,
    )
    from .missing_data_comp_dialog import MissingDataCompDialog as MissingDataCompDialog
    from .missing_data_box_plot_dialog import (
        MissingDataBoxPlotDialog as MissingDataBoxPlotDialog,
    )
    from .missing_data_results_table_dialog import (
        MissingDataResultsTableDialog as MissingDataResultsTableDialog,
    )