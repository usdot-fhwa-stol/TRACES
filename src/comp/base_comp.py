import sqlite3
from abc import ABC
from typing import Iterable, Optional, List, Any, Dict

# Sentinel for "no options provided" — distinguishable from an explicit empty dict.
_UNSET: Dict[str, Any] = {}


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
        cursor = db_connection.cursor()
        cursor.execute("PRAGMA table_info(events)")
        return {row[1] for row in cursor.fetchall()}

    @staticmethod
    def _event_veh_ids(db_connection: sqlite3.Connection, event_id: int) -> List[int]:
        cursor = db_connection.cursor()
        cursor.execute(
            "SELECT DISTINCT veh_id FROM events WHERE event_id = ? ORDER BY veh_id",
            (event_id,),
        )
        return [row[0] for row in cursor.fetchall()]

import abc
from PyQt6.QtCore import pyqtSignal, QObject

class BaseAnalysisWorker(QObject):
    """Abstract background worker for every noise-analysis module.

    Signals
    -------
    progress(int)   – emitted after each event completes (1-based).
    finished(list)  – emitted with the full result list on success.
    error(str)      – emitted with a human-readable message on failure.
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, db_path: str, event_ids: list,
                 veh_ids, params: dict, dest_path):
        super().__init__()
        self._db_path   = db_path
        self._event_ids = event_ids
        self._veh_ids   = veh_ids       # None → all vehicles
        self._params    = params
        self._dest_path = dest_path     # None → normal, str → export

    @abc.abstractmethod
    def _run_for_event(self, conn, event_id: int, veh_ids, params: dict) -> list:
        """Run the module computation for a single event.  Must be overridden."""

    @abc.abstractmethod
    def _write_csv(self, results: list, dest: str):
        """Write *results* to *dest* as CSV.  Must be overridden."""

    def run(self):
        import sqlite3, logging
        logger = logging.getLogger(__name__)
        try:
            conn = sqlite3.connect(self._db_path)
            all_results = []
            for i, event_id in enumerate(self._event_ids):
                results = self._run_for_event(
                    conn, event_id, self._veh_ids, self._params)
                all_results.extend(results)
                self.progress.emit(i + 1)
            if self._dest_path is not None:
                self._write_csv(all_results, self._dest_path)
            conn.close()
            self.finished.emit(all_results)
        except Exception as exc:
            logger.exception("%s failed", type(self).__name__)
            self.error.emit(str(exc))