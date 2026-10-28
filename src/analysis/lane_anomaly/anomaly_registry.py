"""Extension point for Lane-Level Anomaly types (Task 9.3: "structured in
a way where open source users can later on add more anomalies and
different fixes to them").

An anomaly type is two halves:

    AnomalyType (this module, Qt-free)
        KEY, LABEL, OPTIONS_SCHEMA and detect() -- runs on the worker
        thread, returns LaneAnomaly objects. Fix computations and writes
        live alongside it (see lane_correction.py for the shared writes).

    AnomalyTypeUI (anomaly_ui.py, Qt)
        the type's row in the Select Anomalies dialog (Figure 2), its
        view and button in the review panel (Figure 3), and its Fix flow.
        Resolved lazily through AnomalyType.UI_MODULE so detection never
        imports Qt.

Adding a type
-------------
1. Subclass AnomalyType in a new module of this package (or anywhere
   importable) and call register_anomaly_type() at import time.
2. Write its UI module exposing ANOMALY_UI = <AnomalyTypeUI subclass>(...),
   and name that module in UI_MODULE.
3. Add the module to _BUILTIN_MODULES below (or import it before the
   dialogs open) so anomaly_types() sees it.
4. If its fix writes events rows, register any new imputed_status code in
   utils.IMPUTED_CODES and audit the modules that read codes.

Undo is generic (lane_correction.undo_correction) for any fix that writes
through lane_correction.apply_correction.
"""

import importlib
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterable, List, Optional

import sqlite3

from .lane_anomaly_store import LaneAnomaly

ProgressCallback = Optional[Callable[[], None]]

# Built-in type modules, imported on first use of anomaly_types(). Order is
# the order of the rows in Figure 2 and the buttons in Figure 3.
_BUILTIN_MODULES = (".proximity", ".rapid_lane_change")

_TYPES: Dict[str, "AnomalyType"] = {}
_builtins_loaded = False


class AnomalyType(ABC):
    """One kind of lane-level anomaly. Instances are stateless singletons."""

    KEY: str = ""                       # stored in lane_anomalies.anomaly_type
    LABEL: str = ""                     # e.g. "Proximity"
    OPTIONS_SCHEMA: Dict[str, Any] = {}
    UI_MODULE: str = ""                 # module path for ANOMALY_UI, relative to this package

    def resolve_options(self, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        resolved = dict(self.OPTIONS_SCHEMA)
        if options:
            resolved.update(options)
        return resolved

    @abstractmethod
    def detect(self, conn: sqlite3.Connection, event_id: int,
               veh_ids: Optional[Iterable[int]], options: Dict[str, Any],
               time_step: float) -> List[LaneAnomaly]:
        """Every anomaly of this type in one event, anomaly_id None.
        Active trajectories only; SplineComp placeholder rows excluded.
        *veh_ids* (None = all) keeps anomalies involving those vehicles.
        Read-only."""

    def describe(self, anomaly: LaneAnomaly) -> str:
        """One line for the Selected Anomaly list."""
        vehicles = " & ".join(f"Veh {v}" for v in anomaly.veh_ids)
        lanes = ", ".join(str(v) for v in anomaly.lane_ids)
        text = (f"#{anomaly.anomaly_id}  {vehicles}  "
                f"{anomaly.start_time:.1f}–{anomaly.end_time:.1f} s  (lane {lanes})")
        return text + ("  [fixed]" if anomaly.is_fixed else "")

    def ui(self):
        """This type's AnomalyTypeUI (imports Qt)."""
        cached = getattr(self, "_ui", None)
        if cached is None:
            module = importlib.import_module(self.UI_MODULE, __package__)
            cached = module.ANOMALY_UI
            self._ui = cached
        return cached


def register_anomaly_type(anomaly_type: AnomalyType) -> AnomalyType:
    if not anomaly_type.KEY:
        raise ValueError(f"{type(anomaly_type).__name__} has no KEY")
    existing = _TYPES.get(anomaly_type.KEY)
    if existing is not None and type(existing) is not type(anomaly_type):
        raise ValueError(f"Anomaly type key {anomaly_type.KEY!r} is already registered "
                         f"by {type(existing).__name__}")
    _TYPES[anomaly_type.KEY] = anomaly_type
    return anomaly_type


def _load_builtins() -> None:
    global _builtins_loaded
    if not _builtins_loaded:
        _builtins_loaded = True
        for name in _BUILTIN_MODULES:
            importlib.import_module(name, __package__)


def _rank(t: AnomalyType) -> int:
    """Position of a built-in type's module in _BUILTIN_MODULES; types
    registered from anywhere else come after all built-ins."""
    module = type(t).__module__
    for i, name in enumerate(_BUILTIN_MODULES):
        if module == f"{__package__}{name}":
            return i
    return len(_BUILTIN_MODULES)


def anomaly_types() -> List[AnomalyType]:
    """Every registered type: built-ins in _BUILTIN_MODULES order, then
    any others in registration order. Not plain registration order, which
    depends on which module happened to be imported first."""
    _load_builtins()
    return sorted(_TYPES.values(), key=_rank)   # stable: others keep their order


def anomaly_type(key: str) -> AnomalyType:
    _load_builtins()
    try:
        return _TYPES[key]
    except KeyError:
        raise KeyError(f"Unknown lane anomaly type {key!r} -- is its module "
                       f"imported? (anomaly_registry._BUILTIN_MODULES)") from None
