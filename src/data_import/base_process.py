import sqlite3
from abc import ABC, abstractmethod

class BaseProcess(ABC):
    """Base class for processing input files into SQLite databases.

    Subclasses must implement the process method to handle specific file types.
    """

    @abstractmethod
    def process(self, config: dict, input_path: str, db_connection: sqlite3.Connection, worker: object) -> int:
        """Process an input file and import data into the database.

        Args:
            config (dict): JSON configuration for the import.
            input_path (str): Path to the input file.
            db_connection (sqlite3.Connection): Database connection.
            worker (object): Worker object for logging and progress updates.

        Returns:
            int: Number of errors encountered during processing.
        """
        pass