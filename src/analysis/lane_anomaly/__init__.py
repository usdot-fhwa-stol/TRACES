"""Lane-Level Anomaly Detection and Correction (Task 9.3).

Qt-free:
    lane_anomaly_store      lane_anomalies / lane_corrections side tables
    anomaly_registry        AnomalyType and the registry of anomaly types
    lane_anomaly_comp       LaneAnomalyComp, detection across registered types
    lane_correction         lane reassignment fixes and their undo, written
                            as IMPUTED_LANE through base_comp's
                            superseded_points helpers
    proximity               Proximity anomaly type
    rapid_lane_change       Rapid Lane Change anomaly type

Qt:
    anomaly_ui              AnomalyTypeUI, the shared plot and fix panels
    anomaly_config_dialog   Data > Lane-Level Anomalies (Figure 2)
    anomaly_review_dialog   review panel (Figure 3)
    proximity_view          Proximity's AnomalyTypeUI
    rapid_lane_change_view  Rapid Lane Change's AnomalyTypeUI

The *_view modules are not re-exported: each anomaly type names its own
in AnomalyType.UI_MODULE and the registry imports it on demand, so
detection on a worker thread never imports Qt. Adding a type is described
in anomaly_registry.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Public name -> submodule that defines it. Python runs this file before
# any submodule of the package, so it imports nothing up front: a name is
# loaded on first access (PEP 562). Importing one submodule therefore never
# pulls in its siblings, and these re-exports add no import cycles.
_EXPORTS = {
    # Qt-free core
    "LaneAnomaly": ".lane_anomaly_store",
    "CorrectionRecord": ".lane_anomaly_store",
    "ANOMALIES_TABLE": ".lane_anomaly_store",
    "CORRECTIONS_TABLE": ".lane_anomaly_store",
    "ANOMALY_DETECTED": ".lane_anomaly_store",
    "ANOMALY_FIXED": ".lane_anomaly_store",
    "ensure_schema": ".lane_anomaly_store",
    "read_anomalies": ".lane_anomaly_store",
    "replace_detected": ".lane_anomaly_store",
    "AnomalyType": ".anomaly_registry",
    "anomaly_types": ".anomaly_registry",
    "anomaly_type": ".anomaly_registry",
    "register_anomaly_type": ".anomaly_registry",
    "LaneAnomalyComp": ".lane_anomaly_comp",
    "DetectionSummary": ".lane_anomaly_comp",
    "LaneMove": ".lane_correction",
    "apply_lane_reassignments": ".lane_correction",
    "apply_lane_reassignment": ".lane_correction",
    "undo_correction": ".lane_correction",
    "ProximityType": ".proximity",
    "PROXIMITY_KEY": ".proximity",
    "RapidLaneChangeType": ".rapid_lane_change",
    "RAPID_LANE_CHANGE_KEY": ".rapid_lane_change",
    # Qt
    "AnomalyTypeUI": ".anomaly_ui",
    "ConfigRow": ".anomaly_ui",
    "FixPanel": ".anomaly_ui",
    "SegmentFixPanel": ".anomaly_ui",
    "VehicleLanesFixPanel": ".anomaly_ui",
    "LaneAnomalyConfigDialog": ".anomaly_config_dialog",
    "LaneAnomalyReviewDialog": ".anomaly_review_dialog",
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
    from .lane_anomaly_store import (
        LaneAnomaly as LaneAnomaly,
        CorrectionRecord as CorrectionRecord,
        ANOMALIES_TABLE as ANOMALIES_TABLE,
        CORRECTIONS_TABLE as CORRECTIONS_TABLE,
        ANOMALY_DETECTED as ANOMALY_DETECTED,
        ANOMALY_FIXED as ANOMALY_FIXED,
        ensure_schema as ensure_schema,
        read_anomalies as read_anomalies,
        replace_detected as replace_detected,
    )
    from .anomaly_registry import (
        AnomalyType as AnomalyType,
        anomaly_types as anomaly_types,
        anomaly_type as anomaly_type,
        register_anomaly_type as register_anomaly_type,
    )
    from .lane_anomaly_comp import (
        LaneAnomalyComp as LaneAnomalyComp,
        DetectionSummary as DetectionSummary,
    )
    from .lane_correction import (
        LaneMove as LaneMove,
        apply_lane_reassignments as apply_lane_reassignments,
        apply_lane_reassignment as apply_lane_reassignment,
        undo_correction as undo_correction,
    )
    from .proximity import (
        ProximityType as ProximityType,
        PROXIMITY_KEY as PROXIMITY_KEY,
    )
    from .rapid_lane_change import (
        RapidLaneChangeType as RapidLaneChangeType,
        RAPID_LANE_CHANGE_KEY as RAPID_LANE_CHANGE_KEY,
    )
    from .anomaly_ui import (
        AnomalyTypeUI as AnomalyTypeUI,
        ConfigRow as ConfigRow,
        FixPanel as FixPanel,
        SegmentFixPanel as SegmentFixPanel,
        VehicleLanesFixPanel as VehicleLanesFixPanel,
    )
    from .anomaly_config_dialog import (
        LaneAnomalyConfigDialog as LaneAnomalyConfigDialog,
    )
    from .anomaly_review_dialog import (
        LaneAnomalyReviewDialog as LaneAnomalyReviewDialog,
    )
