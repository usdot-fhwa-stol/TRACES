import logging
import os
import sys
import json
import importlib
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QTextEdit, QFileDialog, QMessageBox
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QThread
from data_import.base_process import BaseProcess


# Configure logging
logging.basicConfig(
    filename='trajectory_tools.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def resource_path(relative_path: str) -> str:
    """Get absolute path to resource in PyInstaller or development environment.

    Args:
        relative_path: Relative path to the resource.

    Returns:
        str: Absolute path to the resource.
    """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)

class WorkerThread(QThread):
    """Worker thread for import operations.

    Attributes:
        finished: Signal emitted when processing is complete.
        Args:
            config_file: Path to the JSON configuration file.
            input_file: Path to the input file.
            dialog: Reference to the import dialog.
            class_path: Path to the processing class.
    """
    finished = pyqtSignal(dict)

    def __init__(self, config_file: str, input_file: str, dialog: 'ImportDialog', class_path: str):
        super().__init__()
        self.config_file = config_file
        self.input_file = input_file
        self.dialog = dialog
        self.class_path = class_path

    def run(self):
        """Execute the import process in the worker thread."""
        logging.debug("Starting WorkerThread.run")
        try:
            module_path, class_name = self.class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            process_class = getattr(module, class_name)
            if not issubclass(process_class, BaseProcess):
                error_msg = f"Specified class {self.class_path} is not of type BaseProcess"
                logging.error(error_msg)
                self.dialog.append_message(error_msg)
                self.finished.emit({"error_count": 1, "db_conn": None})
                return
            processor = process_class()
            result = processor.process(self.config_file, self.input_file, self.dialog)
            self.finished.emit(result)
        except Exception as e:
            error_msg = f"Error in WorkerThread.run: {str(e)}"
            logging.error(error_msg, exc_info=True)
            self.dialog.append_message(error_msg)
            self.finished.emit({"error_count": 1, "db_conn": None})

class ImportDialog(QDialog):
    """Dialog for importing data files.

    Displays import progress and logs messages.
    """
    update_progress_signal = pyqtSignal(float)
    append_message_signal = pyqtSignal(str)
    stop_signal = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the import dialog.

        Args:
            parent: Parent widget, typically the main window.
        """
        super().__init__(parent)
        self.setWindowTitle("Import Progress")
        self.setFixedSize(800, 600)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)
        self.setup_ui()
        self.worker = None
        self.stopped = False

        # Center dialog in parent window
        if parent:
            parent_geo = parent.geometry()
            self.move(parent_geo.center() - self.rect().center())

    def setup_ui(self):
        """Setup the dialog UI."""
        layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        layout.addWidget(self.log_area)

        button_layout = QHBoxLayout()
        self.close_button = QPushButton("Close")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_signal)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Connect signals
        self.update_progress_signal.connect(self.update_progress)
        self.append_message_signal.connect(self.append_message)
        self.stop_signal.connect(self.stop_import)

    @pyqtSlot(float)
    def update_progress(self, value: float):
        """Update the progress bar.

        Args:
            value: Progress percentage (0-100).
        """
        self.progress_bar.setValue(int(value))

    @pyqtSlot(str)
    def append_message(self, message: str):
        """Append a message to the log area.

        Args:
            message: Message to append.
        """
        self.log_area.append(message)
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    @pyqtSlot()
    def stop_import(self):
        """Handle the stop button click."""
        self.stopped = True
        self.stop_button.setEnabled(False)

    def is_stopped(self) -> bool:
        """Check if the stop button has been clicked.

        Returns:
            bool: True if stopped, False otherwise.
        """
        return self.stopped

    def cleanup(self):
        """Cleanup after import process."""
        self.stop_button.setEnabled(False)
        self.close_button.setEnabled(True)

    def showEvent(self, event):
        """Handle dialog show event.

        Args:
            event: Show event.
        """
        logging.debug("Entering ImportDialog.showEvent")
        try:
            super().showEvent(event)
            self.progress_bar.setValue(0)
            self.log_area.clear()
            self.close_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.stopped = False

            self.append_message("Reading configuration files...")
            config_dir = resource_path("configuration")
            if not os.path.exists(config_dir):
                error_msg = f"Configuration directory {config_dir} not found"
                logging.error(error_msg)
                self.append_message(error_msg)
                self.cleanup()
                return

            file_types = []
            for file in os.listdir(config_dir):
                if file.endswith(".json"):
                    file_path = os.path.join(config_dir, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                        if "type" not in config:
                            error_msg = f"File {file_path} is missing required type key"
                            logging.error(error_msg)
                            self.append_message(error_msg)
                            self.cleanup()
                            return
                        file_type = {
                            "config_file": file_path,
                            "ext": config.get("ext", "csv"),
                            "class": config.get("class", "data_import.csv_process.CsvProcess"),
                            "type": config["type"]
                        }
                        if any(ft["type"] == file_type["type"] for ft in file_types):
                            error_msg = f"File {file_path} contains a duplicate definition for file type {file_type['type']}"
                            logging.error(error_msg)
                            self.append_message(error_msg)
                            self.cleanup()
                            return
                        file_types.append(file_type)
                    except json.JSONDecodeError as e:
                        error_msg = f"Invalid JSON in {file_path}: {str(e)}"
                        logging.error(error_msg)
                        self.append_message(error_msg)
                        self.cleanup()
                        return
                    except Exception as e:
                        error_msg = f"Error reading {file_path}: {str(e)}"
                        logging.error(error_msg)
                        self.append_message(error_msg)
                        self.cleanup()
                        return

            if not file_types:
                error_msg = "No valid configuration files found"
                logging.error(error_msg)
                self.append_message(error_msg)
                self.cleanup()
                return

            filters = ";".join(f"{ft['type']} (*.{ft['ext']})" for ft in file_types)
            file_path, selected_filter = QFileDialog.getOpenFileName(self, "Select Input File", "", filters)
            if not file_path:
                self.append_message("No file selected")
                self.cleanup()
                return

            selected_type = next((ft for ft in file_types
                                 if f"{ft['type']} (*.{ft['ext']})" == selected_filter), None)
            if not selected_type:
                error_msg = "Invalid file type selected"
                logging.error(error_msg)
                self.append_message(error_msg)
                self.cleanup()
                return

            self.start_import(selected_type["config_file"], file_path, selected_type["class"])
        except Exception as e:
            error_msg = f"Unexpected error in showEvent: {str(e)}"
            logging.error(error_msg, exc_info=True)
            self.append_message(error_msg)
            self.cleanup()

    def start_import(self, config_file: str, input_file: str, class_path: str):
        """Start the import process in a worker thread.

        Args:
            config_file: Path to the JSON configuration file.
            input_file: Path to the input file.
            class_path: Path to the processing class.
        """
        logging.debug(f"Starting import: config={config_file}, input={input_file}, class={class_path}")
        db_file = os.path.splitext(input_file)[0] + ".sqlite"
        if os.path.exists(db_file):
            reply = QMessageBox.question(self, "Overwrite Database",
                                        f"Database {db_file} already exists. Overwrite?",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No:
                self.append_message("Existing database file not overwritten")
                self.cleanup()
                return
            self.append_message(f"Overwriting {db_file}")
            if self.parent().db_conn and self.parent().db_conn.cursor().execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                self.parent().db_conn.close()
            os.remove(db_file)

        self.worker = WorkerThread(config_file, input_file, self, class_path)
        self.worker.finished.connect(self.on_import_finished)
        self.worker.start()

    def on_import_finished(self, result: dict):
        """Handle completion of the import process.

        Args:
            result: Dictionary containing 'error_count' and 'db_conn'.
        """
        logging.debug(f"Import finished with result: {result}")
        if result["error_count"] == 0 and result["db_conn"]:
            self.parent().db_conn = result["db_conn"]
            self.parent().setWindowTitle(f"Trajectory Tools - {result['db_conn'].cursor().execute('PRAGMA database_list').fetchone()[2]}")
            self.parent().export_action.setEnabled(True)
            if self.parent().db_conn.cursor().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone()[0] == "trajectories":
                if self.parent().db_conn.cursor().execute("SELECT COUNT(*) FROM trajectories").fetchone()[0] > 0:
                    if input_file := result["db_conn"].cursor().execute("PRAGMA database_list").fetchone()[2]:
                        if input_file in self.parent().recent_files:
                            self.parent().recent_files.remove(input_file)
                        self.parent().recent_files.insert(0, input_file)
                        if len(self.parent().recent_files) > 7:
                            self.parent().recent_files.pop()
                        self.parent().update_recent_menu()
                        self.parent().save_settings()
        self.cleanup()
