"""Interaction-mode handlers for TimeSpacePlotWidget.

    interaction_mode        InteractionMode and the no-op base handler
    gap_headway_handler     Gap Headway mode
    shockwave_handler       Shockwave modes (uses analysis.shockwave)
    shockwave_result_panel  floating result panel for the shockwave modes
    stitch_select_handler   Trajectory Stitching selection
                            (uses analysis.manual_stitching)
    plot_overlay_label      top-center banner used by stitch selection

Handlers drive TimeSpacePlotWidget's internals, so they live with the plot;
the computations they call are in analysis.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "InteractionMode": ".interaction_mode",
    "InteractionModeHandler": ".interaction_mode",
    "GapHeadwayModeHandler": ".gap_headway_handler",
    "ShockwaveModeHandler": ".shockwave_handler",
    "ShockwaveSemiAutoModeHandler": ".shockwave_handler",
    "ShockwaveResultPanel": ".shockwave_result_panel",
    "StitchSelectModeHandler": ".stitch_select_handler",
    "PlotOverlayLabel": ".plot_overlay_label",
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
    from .interaction_mode import (
        InteractionMode as InteractionMode,
        InteractionModeHandler as InteractionModeHandler,
    )
    from .gap_headway_handler import GapHeadwayModeHandler as GapHeadwayModeHandler
    from .shockwave_handler import (
        ShockwaveModeHandler as ShockwaveModeHandler,
        ShockwaveSemiAutoModeHandler as ShockwaveSemiAutoModeHandler,
    )
    from .shockwave_result_panel import ShockwaveResultPanel as ShockwaveResultPanel
    from .stitch_select_handler import (
        StitchSelectModeHandler as StitchSelectModeHandler,
    )
    from .plot_overlay_label import PlotOverlayLabel as PlotOverlayLabel