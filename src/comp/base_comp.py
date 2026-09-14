import sqlite3
from abc import ABC
from typing import Iterable, Optional, List, Any, Dict

# Sentinel for "no options provided" — distinguishable from an explicit empty dict.
_UNSET: Dict[str, Any] = {}

# Shared row-provenance flag on events.<row>. One column, one place of
# truth, used by both SplineComp and MissingDataInterpolationComp instead
# of each owning a private flag.
IMPUTED_ORIGINAL = " "   # real observation (column default)
IMPUTED_SPLINE   = "S"   # synthetic row created solely to complete SplineComp's calc_* timing grid
IMPUTED_AUTO     = "A"   # gap filled automatically by Missing Data Interpolation
IMPUTED_MANUAL   = "M"   # gap filled/refined manually by Missing Data Interpolation

IMPUTED_STATUS_COL = "imputed_status"


def ensure_imputed_status_column(conn: sqlite3.Connection) -> None:
    """Add the shared imputed_status TEXT column to events, if not already
    present. Safe to call repeatedly (checked via PRAGMA table_info rather
    than the try/ALTER/except-duplicate-column pattern used elsewhere,
    since the migration step below needs that column list anyway).

    Migrates any pre-existing is_spline_imputed INTEGER column (databases
    created before this column was unified): 1 -> IMPUTED_SPLINE, 0/NULL
    left at the new column's default (IMPUTED_ORIGINAL). The old column
    is left in place rather than dropped — SQLite's DROP COLUMN support
    is version-dependent and an unused column is harmless.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(events)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    if IMPUTED_STATUS_COL not in existing_cols:
        conn.execute(
            f"ALTER TABLE events ADD COLUMN {IMPUTED_STATUS_COL} "
            f"TEXT NOT NULL DEFAULT '{IMPUTED_ORIGINAL}'"
        )
        conn.commit()

    if "is_spline_imputed" in existing_cols:
        conn.execute(
            f"UPDATE events SET {IMPUTED_STATUS_COL} = '{IMPUTED_SPLINE}' "
            f"WHERE is_spline_imputed = 1 AND {IMPUTED_STATUS_COL} = '{IMPUTED_ORIGINAL}'"
        )
        conn.commit()

class BaseComp(ABC):
    """Base class for computational analyses over the events table.

    Subclasses override comp_series. Subclass-specific parameters travel
    through the `options` dict so the base interface stays stable as new
    analyses are added.

    Subclasses should define OPTIONS_SCHEMA as a dict[str, default_value]
    listing the option keys they recognize and their defaults.
    """

    OPTIONS_SCHEMA: Dict[str, Any] = {}

    def comp(self,
             db_connection: sqlite3.Connection,
             options: Optional[Dict[str, Any]] = None) -> List[Any]:
        options = self._resolve_options(options)
        results: List[Any] = []
        cursor = db_connection.cursor()
        cursor.execute("SELECT event_id FROM ranges ORDER BY event_id")
        event_ids = [row[0] for row in cursor.fetchall()]
        for event_id in event_ids:
            results.extend(self.comp_event(db_connection, event_id, options))
        return results

    def comp_event(self,
                   db_connection: sqlite3.Connection,
                   event_id: int,
                   options: Optional[Dict[str, Any]] = None) -> List[Any]:
        options = self._resolve_options(options)
        return self.comp_series(db_connection, event_id, None, options)

    def comp_series(self,
                    db_connection: sqlite3.Connection,
                    event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None) -> List[Any]:
        return []

    @classmethod
    def _resolve_options(cls, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge caller-supplied options with subclass defaults.

        Unknown keys are kept (they may be informational). Missing keys
        are filled with defaults from OPTIONS_SCHEMA.
        """
        resolved = dict(cls.OPTIONS_SCHEMA)
        if options:
            resolved.update(options)
        return resolved

    @staticmethod
    def _events_columns(db_connection: sqlite3.Connection) -> set:
        """Column names present in the events table. Delegates to the shared
        utils.events_columns helper so plot and gui modules can perform the
        same check without importing from the comp package."""
        from utils import events_columns
        return events_columns(db_connection)

    @staticmethod
    def _event_veh_ids(db_connection: sqlite3.Connection, event_id: int) -> List[int]:
        cursor = db_connection.cursor()
        cursor.execute(
            "SELECT DISTINCT veh_id FROM events WHERE event_id = ? ORDER BY veh_id",
            (event_id,),
        )
        return [row[0] for row in cursor.fetchall()]

from PyQt6.QtCore import pyqtSignal, QObject

class BaseAnalysisWorker(QObject):
    """Abstract background worker for every noise-analysis module.

    Signals
    -------
    progress(int)   – emitted each time run_for_event's progress_callback
                       is invoked, carrying the cumulative tick count.
    finished(list)  – emitted with the full result list on success.
    error(str)      – emitted with a human-readable message on failure.
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, db_path: str, event_ids: list,
                 veh_ids, params: dict, dest_path,
                 run_for_event,   # callable(conn, event_id, veh_ids, params, progress_callback) -> list
                 write_csv):      # callable(results, dest) or None
        super().__init__()
        self._db_path        = db_path
        self._event_ids      = event_ids
        self._veh_ids        = veh_ids
        self._params         = params
        self._dest_path      = dest_path
        self._run_for_event  = run_for_event
        self._write_csv      = write_csv
        self._progress_count = 0

    def _tick(self):
        """Passed to run_for_event as progress_callback. Each call advances
        the cumulative progress count by one tick and emits it."""
        self._progress_count += 1
        self.progress.emit(self._progress_count)

    def run(self):
        import sqlite3, logging
        logger = logging.getLogger(__name__)
        try:
            conn = sqlite3.connect(self._db_path)
            all_results = []
            self._progress_count = 0
            for event_id in self._event_ids:
                results = self._run_for_event(
                    conn, event_id, self._veh_ids, self._params,
                    progress_callback=self._tick)
                all_results.extend(results)
            if self._dest_path is not None and self._write_csv is not None:
                self._write_csv(all_results, self._dest_path)
            conn.close()
            self.finished.emit(all_results)
        except Exception as exc:
            logger.exception("BaseAnalysisWorker failed")
            self.error.emit(str(exc))