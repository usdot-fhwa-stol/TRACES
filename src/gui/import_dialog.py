# gui/import_dialog.py

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QTextEdit, QFileDialog
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from data_import.base_process import BaseProcess
import importlib

class ImportWorker(QObject):
    """Worker class for handling import operations in a separate thread.

    Attributes:
        log_signal (pyqtSignal): Signal to emit log messages.
        progress_signal (pyqtSignal): Signal to emit progress updates.
        interrupt_flag (bool): Flag to indicate if the import is interrupted.
    """

    log_signal = pyqtSignal(str, bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, config: dict, input_path: str, output_path: str, db_connection: sqlite3.Connection):
        """Initialize the worker with import parameters.

        Args:
            config (dict): JSON configuration for the import.
            input_path (str): Path to the input file.
            output_path (str): Path to the output SQLite database.
            db_connection (sqlite3.Connection): Database connection.
        """
        super().__init__()
        self.config = config
        self.input_path = input_path
        self.output_path = output_path
        self.db_connection = db_connection
        self.interrupt_flag = False

    def run(self):
        """Execute the import process and update vehicle distances."""
        try:
            # Delete existing output file
            if Path(self.output_path).exists():
                Path(self.output_path).unlink()

            # Set up database
            self.db_connection.execute("PRAGMA auto_vacuum=INCREMENTAL;")
            self.db_connection.execute("VACUUM;")
            self.db_connection.execute("""
                CREATE TABLE events (
                    event_id INTEGER NOT NULL,
                    veh_id INTEGER NOT NULL,
                    event_time REAL NOT NULL,
                    lane_id INTEGER,
                    x_map_loc REAL,
                    y_map_loc REAL,
                    x_frenet REAL,
                    y_frenet REAL,
                    x_map_origin REAL,
                    y_map_origin REAL,
                    veh_lat REAL,
                    veh_lon REAL,
                    veh_speed REAL,
                    veh_acceleration REAL,
                    veh_length REAL,
                    veh_width REAL,
                    veh_automated INTEGER,
                    osm_way_id INTEGER,
                    osm_speed_limit INTEGER,
                    osm_traffic_control INTEGER,
                    preceding_veh_id INTEGER,
                    veh_dist REAL,
                    PRIMARY KEY (event_id, veh_id, event_time)
                ) WITHOUT ROWID;
            """)
            self.db_connection.commit()

            # Instantiate processor
            module_name, class_name = self.config.get("class", "data_import.csv_process.CsvProcess").rsplit(".", 1)
            module = importlib.import_module(module_name)
            processor_class = getattr(module, class_name)
            
            if not issubclass(processor_class, BaseProcess):
                self.log_signal.emit("Error: Processor class is not a subclass of BaseProcess.", False)
                return

            processor = processor_class()
            error_count = processor.process(self.config, self.input_path, self.db_connection, self)

            if error_count == 0 and not self.interrupt_flag:
                self.log_signal.emit("Updating vehicle distances", True)
                
                # Calculate total records for progress
                cursor = self.db_connection.cursor()
                cursor.execute("SELECT COUNT(*) FROM events")
                total_records = cursor.fetchone()[0]
                
                # Calculate cumulative distances
                cursor.execute("SELECT DISTINCT event_id, veh_id FROM events")
                pairs = cursor.fetchall()
                processed_records = 0
                batch_size = 1000
                
                for event_id, veh_id in pairs:
                    if self.interrupt_flag:
                        break
                    cursor.execute("""
                        SELECT event_time, x_map_loc, y_map_loc
                        FROM events
                        WHERE event_id = ? AND veh_id = ?
                        ORDER BY event_time
                    """, (event_id, veh_id))
                    points = cursor.fetchall()
                    
                    total_dist = 0.0
                    prev_x_loc = None
                    prev_y_loc = None
                    
                    for i, (event_time, x_map_loc, y_map_loc) in enumerate(points):
                        if prev_x_loc is not None and prev_y_loc is not None:
                            dist = ((x_map_loc - prev_x_loc) ** 2 + (y_map_loc - prev_y_loc) ** 2) ** 0.5
                            total_dist += dist
                        cursor.execute("""
                            UPDATE events
                            SET veh_dist = ?
                            WHERE event_id = ? AND veh_id = ? AND event_time = ?
                        """, (total_dist, event_id, veh_id, event_time))
                        prev_x_loc = x_map_loc
                        prev_y_loc = y_map_loc
                        
                        if (i + 1) % batch_size == 0 or i == len(points) - 1:
                            self.db_connection.commit()
                            processed_records += min(batch_size, len(points) - i)
                            self.progress_signal.emit(int((processed_records / total_records) * 100))
                
                if not self.interrupt_flag:
                    self.db_connection.commit()
                    self.log_signal.emit(f"{self.input_path} successfully imported", True)
                else:
                    self.log_signal.emit("Import interrupted", False)
                    if Path(self.output_path).exists():
                        Path(self.output_path).unlink()

        except Exception as e:
            self.log_signal.emit(f"Error during import: {str(e)}", False)

class ImportDialog(QDialog):
    """Modal dialog for importing trajectory data into SQLite databases.

    Manages the import process, displays progress, and logs messages.

    Attributes:
        _progress_bar (QProgressBar): Progress bar for import progress.
        _text_edit (QTextEdit): Log display for import messages.
        _input_button (QPushButton): Button to select input file.
        _output_button (QPushButton): Button to select output file.
        _import_button (QPushButton): Button to start import.
        _interrupt_button (QPushButton): Button to interrupt import.
        _input_path (str): Selected input file path.
        _output_path (str): Selected output file path.
        _configs (dict): Valid JSON configurations by file type.
        _selected_file_type (str): File type selected in the file dialog.
        _worker_thread (QThread): Thread for running import worker.
        _worker (ImportWorker): Worker for import process.
        _log_file (Path): Path to the log file.
    """

    def __init__(self, parent=None):
        """Initialize the dialog with layout and initial state."""
        super().__init__(parent)
        self.setWindowTitle("Import Trajectory Data")
        self.setFixedSize(800, 600)
        self._input_path = ""
        self._output_path = ""
        self._configs = {}
        self._selected_file_type = ""  # New: Store selected file type
        self._worker_thread = None
        self._worker = None
        self._log_file = Path("trajectory_tools.log")
        self._setup_ui()
        self._center_dialog()
        self._initialize()

    def _setup_ui(self):
        """Set up the dialog layout and widgets."""
        layout = QVBoxLayout(self)
        
        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        layout.addWidget(self._progress_bar)
        
        # Text edit
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        layout.addWidget(self._text_edit, stretch=1)
        
        # Button row
        button_layout = QHBoxLayout()
        self._input_button = QPushButton("Input")
        self._output_button = QPushButton("Output")
        self._import_button = QPushButton("Import")
        self._interrupt_button = QPushButton("Interrupt")
        
        self._input_button.setEnabled(False)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(False)
        
        button_layout.addStretch()
        button_layout.addWidget(self._input_button)
        button_layout.addStretch()
        button_layout.addWidget(self._output_button)
        button_layout.addStretch()
        button_layout.addWidget(self._import_button)
        button_layout.addStretch()
        button_layout.addWidget(self._interrupt_button)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # Connect signals
        self._input_button.clicked.connect(self._select_input)
        self._output_button.clicked.connect(self._select_output)
        self._import_button.clicked.connect(self._start_import)
        self._interrupt_button.clicked.connect(self._interrupt_import)

    def _center_dialog(self):
        """Center the dialog within the parent window."""
        parent = self.parent()
        if parent:
            parent_rect = parent.geometry()
            dialog_rect = self.geometry()
            x = parent_rect.x() + (parent_rect.width() - dialog_rect.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - dialog_rect.height()) // 2
            self.move(x, y)

    def _initialize(self):
        """Initialize the dialog by checking configuration files."""
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        
        # Clear log file
        if self._log_file.exists():
            self._log_file.unlink()
        
        # Create config directory if it doesn't exist
        config_dir = Path("config")
        config_dir.mkdir(exist_ok=True)
        
        # Check JSON configuration files
        config_files = list(config_dir.glob("*.json"))
        if not config_files:
            self._log_message("Error: No JSON configuration files found in 'config' directory.", False)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)
            return
        
        for config_file in config_files:
            try:
                with config_file.open('r') as f:
                    config = json.load(f)
                
                if not isinstance(config, dict):
                    self._log_message(f"Error: {config_file} does not contain a JSON object.", False)
                    continue
                
                if "file_type" not in config or not isinstance(config["file_type"], str):
                    self._log_message(f"Error: {config_file} missing or invalid 'file_type' key.", False)
                    continue
                
                if "col_defs" not in config:
                    self._log_message(f"Error: {config_file} missing 'col_defs' key.", False)
                    continue
                
                file_type = config["file_type"]
                if file_type in self._configs:
                    self._log_message(f"Error: Multiple configurations for file type '{file_type}'.", False)
                    self._configs.clear()
                    break
                
                config.setdefault("file_ext", "csv")
                config.setdefault("class", "data_import.csv_process.CsvProcess")
                self._configs[file_type] = config
                
            except json.JSONDecodeError:
                self._log_message(f"Error: {config_file} is not a valid JSON file.", False)
        
        if not self._configs:
            self._log_message("Error: No valid JSON configuration files found. Import cannot continue.", False)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        else:
            file_types = ", ".join(self._configs.keys())
            self._log_message(f"Info: Available file types for import: {file_types}", True)
            self._log_message("Info: Please select an input file.", True)
            self._input_button.setEnabled(True)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)

    def _log_message(self, message: str, is_html: bool = False):
        """Log a message to the text edit and log file.

        Args:
            message (str): Message to log.
            is_html (bool): Whether the message contains HTML formatting.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display_message = f"[{timestamp}] {message}"
        
        if is_html:
            self._text_edit.append(display_message)
        else:
            self._text_edit.append(display_message.replace("<", "&lt;").replace(">", "&gt;"))
        
        # Write plain text to log file
        plain_message = display_message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        with self._log_file.open('a') as f:
            f.write(plain_message + "\n")
        
        self._text_edit.verticalScrollBar().setValue(self._text_edit.verticalScrollBar().maximum())

    def _select_input(self):
        """Handle input file selection."""
        file_types = ";".join([f"{ft} (*.{config['file_ext']})" for ft, config in self._configs.items()])
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            "",
            file_types
        )
        
        if file_path:
            self._input_path = file_path
            # Extract file_type from selected_filter (e.g., "ohio-one-veh (*.csv)" -> "ohio-one-veh")
            self._selected_file_type = selected_filter.split(" (")[0]
            self._output_path = str(Path(file_path).with_suffix(".sqlite"))
            self._log_message(f"Info: Input file selected: {file_path}", True)
            self._log_message(f"Info: Output file set to: {self._output_path}", True)
            if Path(self._output_path).exists():
                self._log_message(f"Warning: Output file {self._output_path} will be overwritten.", False)
            self._log_message("Info: You can change the output file.", True)
            self._output_button.setEnabled(True)
            self._import_button.setEnabled(True)

    def _select_output(self):
        """Handle output file selection."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output SQLite Database",
            self._output_path,
            "SQLite Database (*.sqlite)"
        )
        
        if file_path:
            self._output_path = file_path
            self._log_message(f"Info: Output file changed to: {file_path}", True)

    def _start_import(self):
        """Start the import process in a separate thread."""
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self._input_button.setEnabled(False)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(True)
        
        if not self._selected_file_type or self._selected_file_type not in self._configs:
            self._log_message(f"Error: Invalid or missing file type '{self._selected_file_type}'.", False)
            self._input_button.setEnabled(True)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)
            return
        
        config = self._configs[self._selected_file_type]
        db_connection = sqlite3.connect(self._output_path)
        
        self._worker_thread = QThread()
        self._worker = ImportWorker(config, self._input_path, self._output_path, db_connection)
        self._worker.moveToThread(self._worker_thread)
        
        self._worker.log_signal.connect(self._log_message)
        self._worker.progress_signal.connect(self._progress_bar.setValue)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.finished.connect(self._finish_import)
        
        self._worker_thread.start()

    def _interrupt_import(self):
        """Handle interrupt button click."""
        if self._worker:
            self._worker.interrupt_flag = True
        self._interrupt_button.setEnabled(False)
        self._log_message("Warning: Interrupt requested", False)

    def _finish_import(self):
        """Handle import completion or interruption."""
        self._worker_thread.quit()
        self._worker_thread.wait()
        
        if self._worker.interrupt_flag:
            if Path(self._output_path).exists():
                Path(self._output_path).unlink()
            self._input_path = ""
            self._output_path = ""
        else:
            self._input_path = ""
        
        self._input_button.setEnabled(True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self._worker_thread = None
        self._worker = None

    def get_output_path(self) -> str:
        """Get the output file path.

        Returns:
            str: Path to the output SQLite database, or empty string if none.
        """
        return self._output_path