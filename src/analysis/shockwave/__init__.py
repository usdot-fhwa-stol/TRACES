"""Shockwave analysis on the time-space plot.

The interaction itself lives in plot.handlers.shockwave_handler; this
package holds the computation and its dialogs.

    shockwave_comp                  ShockwaveComp, inflection points, fits (Qt-free)
    shockwave_config_dialog         configuration shown when the mode is selected
    shockwave_results_table_dialog  results table and CSV save
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "ShockwaveComp": ".shockwave_comp",
    "ShockwaveResult": ".shockwave_comp",
    "InflectionPoint": ".shockwave_comp",
    "InflectionType": ".shockwave_comp",
    "ShockwaveConfigDialog": ".shockwave_config_dialog",
    "ShockwaveResultsTableDialog": ".shockwave_results_table_dialog",
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
    from .shockwave_comp import (
        ShockwaveComp as ShockwaveComp,
        ShockwaveResult as ShockwaveResult,
        InflectionPoint as InflectionPoint,
        InflectionType as InflectionType,
    )
    from .shockwave_config_dialog import ShockwaveConfigDialog as ShockwaveConfigDialog
    from .shockwave_results_table_dialog import (
        ShockwaveResultsTableDialog as ShockwaveResultsTableDialog,
    )