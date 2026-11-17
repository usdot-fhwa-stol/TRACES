"""Reusable UI building blocks shared by the analysis, plot and gui packages.

    brand                          app name, version, colors, logo and icon
    db_target_row                  write-target row for dialogs that modify
                                   the open database
    kinematic_stacked_plot_widget  stacked distance/speed/accel/jerk plots
    box_plot_item                  box-and-whisker pyqtgraph item
    box_plot_grid_dialog           grid of box plots with hover statistics
    gap_point_editor               click/drag point editor for one plot
    point_edit_utils               click-vs-drag and nearest-point helpers
    plot_style                     shared pens, colors and line roles
    plot_export                    self-contained interactive HTML pages
    plot_view.js                   viewer script inlined by plot_export
    toggle_switch                  animated on/off switch widget
    vertical_gradient_value        vertical color-gradient legend

plot_export reads plot_view.js with importlib.resources from this package,
so this must stay a regular package (keep this file) and frozen builds
must bundle plot_view.js as data. brand draws the logo and icon from
assets/traces_logo.svg and assets/traces_icon.svg, which frozen builds
must also bundle.

Dependency rule: ui_kit imports only utils; nothing from data_import,
analysis, plot or gui.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "APP_NAME": ".brand",
    "APP_VERSION": ".brand",
    "ACCENT": ".brand",
    "SAXTON_BLUE": ".brand",
    "HEADING_COLOR": ".brand",
    "logo_pixmap": ".brand",
    "app_icon": ".brand",
    "apply_application_branding": ".brand",
    "background_gradient": ".brand",
    "DbTargetRow": ".db_target_row",
    "TargetResolver": ".db_target_row",
    "DB_FILE_FILTER": ".db_target_row",
    "db_file_path": ".db_target_row",
    "same_file": ".db_target_row",
    "in_place_resolver": ".db_target_row",
    "BoxPlotGridDialog": ".box_plot_grid_dialog",
    "BoxPlotPanel": ".box_plot_grid_dialog",
    "BoxStats": ".box_plot_grid_dialog",
    "box_stats": ".box_plot_grid_dialog",
    "KinematicStackedPlotWidget": ".kinematic_stacked_plot_widget",
    "Series": ".kinematic_stacked_plot_widget",
    "KinematicSpec": ".kinematic_stacked_plot_widget",
    "KINEMATICS": ".kinematic_stacked_plot_widget",
    "BoxPlotItem": ".box_plot_item",
    "GapPointEditor": ".gap_point_editor",
    "is_click": ".point_edit_utils",
    "find_nearest_point": ".point_edit_utils",
    "Role": ".plot_style",
    "ROLE_STYLES": ".plot_style",
    "REGION_RGBA": ".plot_style",
    "KINEMATIC_COLORS": ".plot_style",
    "PROPOSAL_COLOR": ".plot_style",
    "HALO_COLOR": ".plot_style",
    "apply_plot_style": ".plot_style",
    "resolve_color": ".plot_style",
    "role_pen": ".plot_style",
    "outline_pen": ".plot_style",
    "region_brush": ".plot_style",
    "build_plot_html": ".plot_export",
    "plot_spec": ".plot_export",
    "curve_item": ".plot_export",
    "vline_item": ".plot_export",
    "region_item": ".plot_export",
    "box_item": ".plot_export",
    "pen_spec": ".plot_export",
    "SPACER": ".plot_export",
    "load_asset": ".plot_export",
    "ToggleSwitch": ".toggle_switch",
    "GradientConfig": ".vertical_gradient_value",
    "VerticalGradientValue": ".vertical_gradient_value",
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
    from .brand import (
        APP_NAME as APP_NAME,
        APP_VERSION as APP_VERSION,
        ACCENT as ACCENT,
        SAXTON_BLUE as SAXTON_BLUE,
        HEADING_COLOR as HEADING_COLOR,
        logo_pixmap as logo_pixmap,
        app_icon as app_icon,
        apply_application_branding as apply_application_branding,
        background_gradient as background_gradient,
    )
    from .db_target_row import (
        DbTargetRow as DbTargetRow,
        TargetResolver as TargetResolver,
        DB_FILE_FILTER as DB_FILE_FILTER,
        db_file_path as db_file_path,
        same_file as same_file,
        in_place_resolver as in_place_resolver,
    )
    from .box_plot_grid_dialog import (
        BoxPlotGridDialog as BoxPlotGridDialog,
        BoxPlotPanel as BoxPlotPanel,
        BoxStats as BoxStats,
        box_stats as box_stats,
    )
    from .kinematic_stacked_plot_widget import (
        KinematicStackedPlotWidget as KinematicStackedPlotWidget,
        Series as Series,
        KinematicSpec as KinematicSpec,
        KINEMATICS as KINEMATICS,
    )
    from .box_plot_item import BoxPlotItem as BoxPlotItem
    from .gap_point_editor import GapPointEditor as GapPointEditor
    from .point_edit_utils import (
        is_click as is_click,
        find_nearest_point as find_nearest_point,
    )
    from .plot_style import (
        Role as Role,
        ROLE_STYLES as ROLE_STYLES,
        REGION_RGBA as REGION_RGBA,
        KINEMATIC_COLORS as KINEMATIC_COLORS,
        PROPOSAL_COLOR as PROPOSAL_COLOR,
        HALO_COLOR as HALO_COLOR,
        apply_plot_style as apply_plot_style,
        resolve_color as resolve_color,
        role_pen as role_pen,
        outline_pen as outline_pen,
        region_brush as region_brush,
    )
    from .plot_export import (
        build_plot_html as build_plot_html,
        plot_spec as plot_spec,
        curve_item as curve_item,
        vline_item as vline_item,
        region_item as region_item,
        box_item as box_item,
        pen_spec as pen_spec,
        SPACER as SPACER,
        load_asset as load_asset,
    )
    from .toggle_switch import ToggleSwitch as ToggleSwitch
    from .vertical_gradient_value import (
        GradientConfig as GradientConfig,
        VerticalGradientValue as VerticalGradientValue,
    )