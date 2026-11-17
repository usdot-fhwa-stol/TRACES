import sqlite3
import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, List, Dict, Any
import numpy as np
from ..base_comp import BaseComp
from utils import measured_rows_clause, missing_samples
from ..trajectory_utils import estimate_time_step

logger = logging.getLogger(__name__)

NAN = float('nan')

@dataclass
class MissingDataResult:
    """One row of missing data analysis output, per (event_id, veh_id)."""
    event_id: int
    veh_id: int
    # Trajectory-level calculated metrics
    missing_record_pct: float = NAN
    num_gaps: int = 0
    # Raw counts (needed to accurately calculate grand totals later)
    missing_count: int = 0
    expected_count: int = 0
    # Array of the lengths of each gap, used for the Right-Click mode box plot
    gap_lengths: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))


class MissingDataComp(BaseComp):
    """Missing data analysis per the missing data spec, Module 2."""

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "time_step":     None,    # seconds (None = auto-calculate mode)
        "gap_threshold": 10,    #points
    }

    def comp_series(self,
                    db_connection: sqlite3.Connection,
                    event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None) -> List[MissingDataResult]:
        options = self._resolve_options(options)
        gap_threshold = options["gap_threshold"]
        time_step = options["time_step"]

        if veh_ids is None:
            veh_ids = self._event_veh_ids(db_connection, event_id)
        else:
            veh_ids = list(veh_ids)
        if not veh_ids:
            return []

        # Exclude synthetic spline-fill rows -- they exist to plug a gap,
        # so counting them as "present" would hide the very gaps this
        # module is meant to report. Column is optional (only added once
        # Spline Fitting has run), so gate on its presence.
        columns = self._events_columns(db_connection)
        imputed_filter = measured_rows_clause(columns)

        placeholders = ",".join("?" for _ in veh_ids)
        sql = (
            f"SELECT veh_id, veh_time FROM events "
            f"WHERE event_id = ? AND veh_id IN ({placeholders})"
            f"{imputed_filter} "
            f"ORDER BY veh_id, veh_time"
        )
        cursor = db_connection.cursor()
        cursor.execute(sql, [event_id] + veh_ids)

        tmp_time_data = {v: [] for v in veh_ids}
        for row in cursor:
            v, t = row[0], row[1]
            if t is not None:
                tmp_time_data[v].append(t)

        grouped_time_arrays = {
            v: np.asarray(times, dtype=np.float64) 
            for v, times in tmp_time_data.items()
        }

        if time_step is None:
             time_step = self._calculate_global_time_step_mode(grouped_time_arrays)

        results = []
        for v in veh_ids:
            r = MissingDataResult(event_id=event_id, veh_id=v)
            t_array = grouped_time_arrays[v]
            
            # Run the vectorized gap analysis
            pct, num_gaps, missing_cnt, expected_cnt, lengths = self._analyze_gaps(t_array, time_step, gap_threshold)
            r.missing_record_pct = pct
            r.num_gaps = num_gaps
            r.missing_count = missing_cnt
            r.expected_count = expected_cnt
            r.gap_lengths = lengths
            
            results.append(r)
        
        return results

    # ─── Vectorized Helpers ───

    @staticmethod
    def _analyze_gaps(t_array: np.ndarray, time_step: float, threshold: int):
        """
        Replaces the row-by-row iteration from the pseudocode with optimized NumPy math.
        """

        # If trajectory is too short to have gaps, return empty metrics
        if len(t_array) < 2:
            return NAN, 0, 0, len(t_array), np.array([], dtype=int)

        # Calculate time difference between consecutive rows
        dt = np.diff(t_array)
        
        # Filter out any NaN time differences just in case
        dt = dt[~np.isnan(dt)]
        if len(dt) == 0:
            return NAN, 0, 0, len(t_array), np.array([], dtype=int)

        # Calculate missing records between points
        missing_records = missing_samples(dt, time_step)

        # Calculate raw counts
        missing_count = int(np.sum(missing_records))
        expected_count = len(t_array) + missing_count

        # Extract only the gaps that meet or exceed the user threshold
        gaps = missing_records[missing_records >= threshold]
        
        num_gaps = len(gaps)
        gap_lengths = gaps.astype(int)

        # Calculate the trajectory percentage
        if expected_count > 0:
            pct = float((missing_count / expected_count) * 100.0)
        else:
            pct = NAN

        return pct, num_gaps, missing_count, expected_count, gap_lengths
        
    @staticmethod
    def _calculate_global_time_step_mode(grouped_time_arrays: Dict[int, np.ndarray]) -> float:
        """
        Calculates the most frequent time increment (mode) across all vehicle trajectories.
        """
        return estimate_time_step(grouped_time_arrays.values())