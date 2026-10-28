"""Lane-Level Anomaly Detection and Correction (Task 9.3).

Public names are re-exported lazily (PEP 562), so importing the package
-- e.g. for LaneAnomalyComp on a worker thread -- doesn't import Qt.
"""

import importlib

_EXPORTS = {
    # Qt-free core
    "LaneAnomaly":              ".lane_anomaly_store",
    "CorrectionRecord":         ".lane_anomaly_store",
    "ANOMALY_DETECTED":         ".lane_anomaly_store",
    "ANOMALY_FIXED":            ".lane_anomaly_store",
    "ensure_schema":            ".lane_anomaly_store",
    "read_anomalies":           ".lane_anomaly_store",
    "replace_detected":         ".lane_anomaly_store",
    "AnomalyType":              ".anomaly_registry",
    "anomaly_types":            ".anomaly_registry",
    "anomaly_type":             ".anomaly_registry",
    "register_anomaly_type":    ".anomaly_registry",
    "LaneAnomalyComp":          ".lane_anomaly_comp",
    "DetectionSummary":         ".lane_anomaly_comp",
    "apply_correction":         ".lane_correction",
    "undo_correction":          ".lane_correction",
    "ProximityType":            ".proximity",
    "RapidLaneChangeType":      ".rapid_lane_change",
    "fit_midline":              ".rapid_lane_change",
    # Qt
    "AnomalyTypeUI":            ".anomaly_ui",
    "AnomalyView":              ".anomaly_ui",
    "LaneAnomalyConfigDialog":  ".anomaly_config_dialog",
    "LaneAnomalyReviewDialog":  ".anomaly_review_dialog",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
