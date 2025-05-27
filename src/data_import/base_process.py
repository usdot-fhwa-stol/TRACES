from abc import ABC, abstractmethod


class BaseProcess(ABC):
    """Abstract base class for data import processing.

    Defines the interface for processing input files into SQLite databases.
    """

    @abstractmethod
    def process(self, config_file: str, input_file: str, dialog: object) -> dict:
        """Process an input file based on configuration.

        Args:
            config_file: Path to the JSON configuration file.
            input_file: Path to the input data file.
            dialog: Reference to the import dialog for progress updates.

        Returns:
            dict: Contains 'error_count' (int) and 'db_conn' (sqlite3.Connection or None).
        """
        pass