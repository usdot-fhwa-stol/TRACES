"""Abstract base class for import processors.

An import processor is responsible for everything specific to a source file
format: validating its JSON configuration, declaring the database columns the
import will produce, checking the input file, and importing the data.

The orchestration contract with the GUI layer is:

    ImportDialog (GUI thread)
        1. Resolves the ``class`` key from the JSON configuration file,
           instantiates the processor, and calls ``check_configuration()``
           to decide whether the configuration is usable at all.
        2. At import start, creates a FRESH processor instance, calls
           ``check_configuration()`` again on it, and hands the validated
           instance to the worker thread. (Fresh instances guarantee no
           state — e.g. group-index dictionaries — leaks between imports.)

    _ImportWorkerThread (worker thread)
        3. Calls ``column_defs()`` and creates the ``events`` table.
        4. Calls ``check_file()`` to validate the input file.
        5. Calls ``import_data()`` to populate the ``events`` table.
        6. Creates and populates the ``ranges`` table itself — summary
           creation is generic over the events table and is NOT a processor
           responsibility.

The processor retains any configuration state it needs between stages;
``config`` is only passed to ``check_configuration()``. The dialog and the
worker never inspect ``col_defs`` (or any other processor-specific keys)
themselves — they only know about the top-level ``file_ext`` and ``class``
keys.
"""

import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict

# Signature of the logging callable passed to check_configuration():
#   log_fn(message: str, is_html: bool) -> None
# On the GUI thread this is ImportDialog._log_message; stage 4 and 5 use the
# worker object's log_signal instead because they run on the worker thread.
LogFn = Callable[[str, bool], None]


class BaseProcess(ABC):
    """Base class for processing input files into SQLite databases.

    Subclasses implement the four import stages. A single instance is
    expected to run the stages in order exactly once; callers create a new
    instance for each import run.
    """

    @abstractmethod
    def check_configuration(self, config: Dict[str, Any], log_fn: LogFn) -> int:
        """Validate the JSON configuration and retain what later stages need.

        This is the only stage that receives the configuration dictionary.
        Implementations must parse and store whatever they require for
        ``column_defs()``, ``check_file()``, and ``import_data()``.

        Safe to call on the GUI thread; must not touch the database or the
        input file.

        Args:
            config (Dict[str, Any]): Parsed JSON configuration for the import.
                Top-level keys ``file_ext`` and ``class`` belong to the
                dialog and may be ignored.
            log_fn (LogFn): Callable for reporting progress and errors,
                invoked as ``log_fn(message, is_html)``.

        Returns:
            int: Number of configuration errors found. 0 means the
                configuration is valid and later stages may run.
        """

    @abstractmethod
    def column_defs(self) -> Dict[str, str]:
        """Return the database columns this import will produce.

        Only valid after ``check_configuration()`` has returned 0.

        Returns:
            Dict[str, str]: Ordered mapping of column name to SQLite type
                affinity (``"INTEGER"``, ``"REAL"``, or ``"TEXT"``). Insertion
                order defines column order in the created table. The mapping
                always contains the required columns ``event_id``, ``veh_id``,
                ``veh_time``, ``lane_id``, and ``veh_dist_trav``.
        """

    @abstractmethod
    def check_file(self, input_path: str, worker: Any) -> int:
        """Validate the structure and content of the input file.

        Runs on the worker thread before any data is imported. Must not
        write to the database.

        Args:
            input_path (str): Path to the input file.
            worker (Any): Worker object providing ``log_signal`` and
                ``progress_signal`` Qt signals and an ``interrupt_flag``
                boolean attribute.

        Returns:
            int: Number of errors found in the input file. 0 means the file
                is importable.
        """

    @abstractmethod
    def import_data(self, input_path: str, db_connection: sqlite3.Connection,
                    worker: Any) -> int:
        """Import the input file into the ``events`` table.

        Runs on the worker thread after ``check_file()`` has returned 0 and
        after the worker has created the ``events`` table from
        ``column_defs()``.

        Args:
            input_path (str): Path to the input file.
            db_connection (sqlite3.Connection): Open connection to the
                output database.
            worker (Any): Worker object providing ``log_signal`` and
                ``progress_signal`` Qt signals and an ``interrupt_flag``
                boolean attribute.

        Returns:
            int: Number of errors encountered during import. 0 means the
                import completed successfully (or was cleanly interrupted —
                implementations should honor ``worker.interrupt_flag`` and
                return without error).
        """