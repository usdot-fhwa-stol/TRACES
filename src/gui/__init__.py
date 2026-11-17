"""Application shell: main window, home page, file and database dialogs.

gui sits at the top of the dependency order: it may import every other
package, and no other package imports gui.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "MainWindow": ".main_window",
    "HomeWidget": ".home_widget",
    "DatabaseSession": ".database_session",
    "ImportDialog": ".import_dialog",
    "ExportDialog": ".export_dialog",
    "EventSelectionDialog": ".event_selection_dialog",
    "LogViewerDialog": ".log_viewer_dialog",
    "AboutDialog": ".about_dialog",
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
    from .main_window import MainWindow as MainWindow
    from .home_widget import HomeWidget as HomeWidget
    from .database_session import DatabaseSession as DatabaseSession
    from .import_dialog import ImportDialog as ImportDialog
    from .export_dialog import ExportDialog as ExportDialog
    from .event_selection_dialog import EventSelectionDialog as EventSelectionDialog
    from .log_viewer_dialog import LogViewerDialog as LogViewerDialog
    from .about_dialog import AboutDialog as AboutDialog