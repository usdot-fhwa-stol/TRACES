"""Lane-Level Anomaly Detection and Correction -- computational entry point
(Task 9.3, top-level Data > Lane-Level Anomalies).

LaneAnomalyComp runs the enabled anomaly types' detectors over one event
and returns a DetectionSummary per event. It is read-only: the config
dialog stores the results on the main thread (lane_anomaly_store.
replace_detected), following the comp-computes / dialog-persists split of
every other analysis module.

Options:
    enabled   list of AnomalyType.KEY to run (default: every registered type)
    types     {AnomalyType.KEY: {option: value}} per-type options, merged
              over each type's OPTIONS_SCHEMA
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from utils import event_time_step
from ..base_comp import BaseComp
from .anomaly_registry import anomaly_type, anomaly_types
from .lane_anomaly_store import LaneAnomaly


@dataclass
class DetectionSummary:
    """One event's detection run -- the unit BaseAnalysisWorker aggregates.
    type_keys are the types that ran, so the rerun policy replaces exactly
    those (a type left unchecked keeps its stored anomalies)."""
    event_id: int
    type_keys: List[str]
    anomalies: List[LaneAnomaly] = field(default_factory=list)
    options: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class LaneAnomalyComp(BaseComp):

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "enabled": None,   # None = every registered type
        "types": {},
    }

    def comp_series(self, db_connection: sqlite3.Connection, event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None,
                    progress_callback=None) -> List[DetectionSummary]:
        options = self._resolve_options(options)
        keys = options["enabled"]
        if keys is None:
            keys = [t.KEY for t in anomaly_types()]
        per_type = options.get("types") or {}

        step = event_time_step(db_connection, event_id)
        summary = DetectionSummary(event_id=event_id, type_keys=list(keys))
        for key in keys:
            t = anomaly_type(key)
            type_options = t.resolve_options(per_type.get(key))
            summary.options[key] = type_options
            summary.anomalies.extend(
                t.detect(db_connection, event_id, veh_ids, type_options, step))
            if progress_callback is not None:
                progress_callback()
        return [summary]
