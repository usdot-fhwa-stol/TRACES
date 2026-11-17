"""Background worker shared by the analysis parameter dialogs.

BaseAnalysisDialog moves an instance onto a QThread. The worker opens its
own SQLite connection on that thread, runs the module's per-event callable
for each event, and optionally writes the CSV export.
"""

import logging
import sqlite3

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class BaseAnalysisWorker(QObject):
    """Background worker for every analysis module.

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