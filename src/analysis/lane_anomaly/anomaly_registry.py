"""Extension point for Lane-Level Anomaly types (Task 9.3: "structured in
a way where open source users can later on add more anomalies and
different fixes to them").

An anomaly type is two halves:

    AnomalyType (this module, Qt-free)
        KEY, LABEL, OPTIONS_SCHEMA and detect() -- runs on the worker
        thread, returns LaneAnomaly objects -- plus the choices its fix
        offers (fix_vehicles, fix_target_lanes, default_target_lane),
        apply_fix(), undo_fix() and describe_fix(). Every built-in fix is a
        set of lane reassignments (lane_correction.LaneMove) written through
        lane_correction.apply_lane_reassignments, so the defaults here are
        usually all a new type needs.

    AnomalyTypeUI (anomaly_ui.py, Qt)
        the type's row in the Select Anomalies dialog (Figure 2), its
        button in the review panel (Figure 3), the text the shared
        time-space plot shows for it, and its fix panel -- the controls
        that build and Apply its fix. Resolved lazily through
        AnomalyType.UI_MODULE so detection never imports Qt.

Adding a type
-------------
1. Subclass AnomalyType in a new module of this package (or anywhere
   importable) and call register_anomaly_type() at import time. Override
   fix_vehicles / fix_target_lanes / default_target_lane when the
   defaults (every vehicle of the anomaly, every lane of the event, no
   preselected lane) aren't the right choices, and apply_fix / undo_fix /
   describe_fix for a fix that isn't a lane reassignment.
2. Write its UI module exposing ANOMALY_UI = <AnomalyTypeUI subclass>(...),
   and name that module in UI_MODULE.
3. Add the module to _BUILTIN_MODULES below (or import it before the
   dialogs open) so anomaly_types() sees it.

Lane reassignments write only lane_id and IMPUTED_LANE, so a type using
them needs no new imputed_status code. The review panel's Undo calls the
type's undo_fix (lane_correction.undo_correction by default).
"""

import importlib
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import sqlite3

from .lane_anomaly_store import LaneAnomaly
from .lane_correction import (
    FIX_LANE_REASSIGN, LaneMove, apply_lane_reassignments, describe_corrections,
    event_lanes, undo_correction,
)

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
    FIX_TYPE: str = FIX_LANE_REASSIGN   # lane_corrections.fix_type of this type's fixes

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

    # ── Fix: lane reassignments ───────────────────────────────────────

    def fix_vehicles(self, anomaly: LaneAnomaly) -> Sequence[int]:
        """Vehicles the user may reassign, in the order offered."""
        return anomaly.veh_ids

    def fix_target_lanes(self, conn: sqlite3.Connection,
                         anomaly: LaneAnomaly) -> List[int]:
        """Lanes a vehicle may be moved to. Default: every lane of the
        event -- a lane that isn't a neighbor may still be the right one."""
        return [int(l) for l in event_lanes(conn, anomaly.event_id)]

    def default_target_lane(self, conn: sqlite3.Connection, anomaly: LaneAnomaly,
                            veh_id: int) -> Optional[int]:
        """Lane preselected for *veh_id*, or None to preselect "Keep"."""
        return None

    def apply_fix(self, conn: sqlite3.Connection, anomaly: LaneAnomaly,
                  moves: Sequence[LaneMove]) -> List[int]:
        """Apply *moves* as this anomaly's fix, all or nothing. Returns the
        correction_ids; raises ValueError (user-facing) when a move isn't
        allowed or its rows can't be changed."""
        allowed_vehicles = set(self.fix_vehicles(anomaly))
        allowed_lanes = set(self.fix_target_lanes(conn, anomaly))
        for m in moves:
            if m.veh_id not in allowed_vehicles:
                raise ValueError(f"Vehicle {m.veh_id} can't be reassigned for "
                                 f"anomaly #{anomaly.anomaly_id}.")
            if int(m.to_lane) not in allowed_lanes:
                raise ValueError(f"Lane {m.to_lane} isn't a lane this anomaly's "
                                 f"vehicles can be moved to.")
        return apply_lane_reassignments(conn, anomaly, moves, fix_type=self.FIX_TYPE)

    def undo_fix(self, conn: sqlite3.Connection, anomaly: LaneAnomaly) -> None:
        """Reverse the fix in effect. Raises ValueError (user-facing)."""
        undo_correction(conn, anomaly)

    def describe_fix(self, conn: sqlite3.Connection, anomaly: LaneAnomaly) -> str:
        """User-facing text of the fix in effect, "" when none."""
        return describe_corrections(conn, anomaly)

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
