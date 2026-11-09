"""Import of source files into a trajectory database, and database checks.

    base_process    BaseProcess, the four-stage import processor interface
    csv_process     CsvProcess, the JSON-configured CSV processor
    csv_algorithms  column functions available to CSV configurations
    units           unit conversion to system units
    db_utils        database validation and ranges.summary_json (Qt-free)

Processor classes are named by dotted path in the JSON configuration
("class": "data_import.csv_process.CsvProcess") and resolved with
importlib, so that path must stay valid.

Dependency rule: data_import uses only utils; no Qt.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    "BaseProcess": ".base_process",
    "LogFn": ".base_process",
    "CsvProcess": ".csv_process",
    "CsvAlgorithms": ".csv_algorithms",
    "Units": ".units",
    "REQUIRED_EVENTS_COLUMNS": ".db_utils",
    "validate_trajectory_db": ".db_utils",
    "build_event_summary": ".db_utils",
    "update_event_summary": ".db_utils",
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
    from .base_process import BaseProcess as BaseProcess, LogFn as LogFn
    from .csv_process import CsvProcess as CsvProcess
    from .csv_algorithms import CsvAlgorithms as CsvAlgorithms
    from .units import Units as Units
    from .db_utils import (
        REQUIRED_EVENTS_COLUMNS as REQUIRED_EVENTS_COLUMNS,
        validate_trajectory_db as validate_trajectory_db,
        build_event_summary as build_event_summary,
        update_event_summary as update_event_summary,
    )