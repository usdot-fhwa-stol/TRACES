"""Time-space plot and its dialogs.

    time_space_plot_widget           TimeSpacePlotWidget, the main plot
    time_space_plot_settings_dialog  event and time/distance range selection
    slider_view_box                  scroll wheel and per-axis zooming
    custom_view_box                  selection and the right-click menu
    vehicle_search_dialog            find a trajectory by vehicle ID
    statistics_dialog                right-click Statistics table
    time_speed_dialog                right-click Time-Speed plot
    time_accel_dialog                right-click Time-Accel plot
    handlers/                        interaction modes and their overlays

Reusable widgets, pens and HTML export live in ui_kit.

Dependency rule: plot may import utils, ui_kit and analysis. analysis and
ui_kit never import plot, and only gui imports plot.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "TimeSpacePlotWidget": ".time_space_plot_widget",
    "TimeSpacePlotSettingsDialog": ".time_space_plot_settings_dialog",
    "SliderViewBox": ".slider_view_box",
    "CustomViewBox": ".custom_view_box",
    "VehicleSearchDialog": ".vehicle_search_dialog",
    "StatisticsDialog": ".statistics_dialog",
    "TimeSpeedDialog": ".time_speed_dialog",
    "TimeAccelDialog": ".time_accel_dialog",
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
    from .time_space_plot_widget import TimeSpacePlotWidget as TimeSpacePlotWidget
    from .time_space_plot_settings_dialog import (
        TimeSpacePlotSettingsDialog as TimeSpacePlotSettingsDialog,
    )
    from .slider_view_box import SliderViewBox as SliderViewBox
    from .custom_view_box import CustomViewBox as CustomViewBox
    from .vehicle_search_dialog import VehicleSearchDialog as VehicleSearchDialog
    from .statistics_dialog import StatisticsDialog as StatisticsDialog
    from .time_speed_dialog import TimeSpeedDialog as TimeSpeedDialog
    from .time_accel_dialog import TimeAccelDialog as TimeAccelDialog