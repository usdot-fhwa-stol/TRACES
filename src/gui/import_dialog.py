import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QTextEdit, QFileDialog
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from data_import.base_process import BaseProcess
import importlib

# Set up logger
logger = logging.getLogger(__name__)

class ImportWorker(QObject):
    """Worker class for handling import operations in a separate thread."""

    log_signal = pyqtSignal(str, bool)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()  # Add explicit finished signal

    def __init__(self, config: dict, input_path: str, output_path: str, db_connection: sqlite3.Connection = None):
        super().__init__()
        self.config = config
        self.input_path = input_path
        self.output_path = output_path
        self.db_connection = db_connection  # Will be None initially, created in run()
        self.interrupt_flag = False
        logger.debug(f"ImportWorker initialized with input: {input_path}, output: {output_path}")

    def run(self):
        """Execute the import process and update vehicle distances."""
        logger.debug("ImportWorker.run() started")
        try:
            # CRITICAL FIX: Close any existing database connection first
            # and ensure the database file is not locked before deletion
            logger.debug("Checking and cleaning up existing output file")
            
            if Path(self.output_path).exists():
                logger.debug(f"Output file exists, attempting to remove: {self.output_path}")
                
                # Try to close any existing connections to this database
                try:
                    # Create a temporary connection to ensure file is accessible
                    temp_conn = sqlite3.connect(self.output_path)
                    temp_conn.close()
                    logger.debug("Temporary connection test successful")
                except Exception as e:
                    logger.warning(f"Could not test database connection: {e}")
                
                # Add a small delay to ensure file handles are released
                import time
                time.sleep(0.1)
                
                # Now try to remove the file
                try:
                    Path(self.output_path).unlink()
                    logger.debug(f"Successfully removed existing output file: {self.output_path}")
                except PermissionError as e:
                    logger.error(f"Permission error removing file: {e}")
                    # Try to rename instead of delete (sometimes works when delete fails)
                    backup_path = Path(self.output_path).with_suffix(".sqlite.backup")
                    try:
                        Path(self.output_path).rename(backup_path)
                        logger.debug(f"Renamed existing file to: {backup_path}")
                    except Exception as rename_error:
                        logger.error(f"Could not rename file either: {rename_error}")
                        raise Exception(f"Cannot access output file {self.output_path}. File may be locked by another process.")

            # Now create the new database connection
            logger.debug(f"Creating new database connection to: {self.output_path}")
            # Ensure we're using the instance connection, not creating a new one
            if hasattr(self, 'db_connection') and self.db_connection:
                try:
                    self.db_connection.close()
                except:
                    pass
            
            # Create fresh connection
            self.db_connection = sqlite3.connect(self.output_path)
            
            # Set up database
            logger.debug("Setting up database")
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
            logger.debug("Database setup completed")

            # Instantiate processor
            logger.debug(f"Instantiating processor: {self.config.get('class', 'data_import.csv_process.CsvProcess')}")
            module_name, class_name = self.config.get("class", "data_import.csv_process.CsvProcess").rsplit(".", 1)
            module = importlib.import_module(module_name)
            processor_class = getattr(module, class_name)
            
            if not issubclass(processor_class, BaseProcess):
                logger.error("Processor class is not a subclass of BaseProcess")
                self.log_signal.emit("Error: Processor class is not a subclass of BaseProcess.", False)
                return

            processor = processor_class()
            logger.debug("Starting data processing")
            error_count = processor.process(self.config, self.input_path, self.db_connection, self)
            logger.debug(f"Data processing completed with {error_count} errors")

            if error_count == 0 and not self.interrupt_flag:
                logger.debug("Starting vehicle distance calculation")
                self.log_signal.emit("Updating vehicle distances", True)
                
                # Calculate total records for progress
                cursor = self.db_connection.cursor()
                cursor.execute("SELECT COUNT(*) FROM events")
                total_records = cursor.fetchone()[0]
                logger.debug(f"Total records for distance calculation: {total_records}")
                
                # Calculate cumulative distances
                cursor.execute("SELECT DISTINCT event_id, veh_id FROM events")
                pairs = cursor.fetchall()
                processed_records = 0
                batch_size = 1000
                
                logger.debug(f"Processing {len(pairs)} vehicle pairs")
                for i, (event_id, veh_id) in enumerate(pairs):
                    if self.interrupt_flag:
                        logger.debug("Interrupt flag set, breaking distance calculation loop")
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
                    
                    for j, (event_time, x_map_loc, y_map_loc) in enumerate(points):
                        if self.interrupt_flag:
                            logger.debug("Interrupt flag set during point processing")
                            break
                            
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
                        
                        if (j + 1) % batch_size == 0 or j == len(points) - 1:
                            self.db_connection.commit()
                            processed_records += min(batch_size, len(points) - j)
                            if total_records > 0:
                                progress = int((processed_records / total_records) * 100)
                                self.progress_signal.emit(progress)
                    
                    if i % 100 == 0:  # Log progress every 100 pairs
                        logger.debug(f"Processed {i}/{len(pairs)} vehicle pairs")
                
                if not self.interrupt_flag:
                    logger.debug("Committing final database changes")
                    self.db_connection.commit()
                    self.log_signal.emit(f"{self.input_path} successfully imported", True)
                    logger.info(f"Import completed successfully: {self.input_path}")
                else:
                    logger.debug("Import was interrupted, cleaning up")
                    self.log_signal.emit("Import interrupted", False)
                    if Path(self.output_path).exists():
                        Path(self.output_path).unlink()

        except Exception as e:
            logger.exception(f"Exception in ImportWorker.run(): {e}")
            self.log_signal.emit(f"Error during import: {str(e)}", False)
        finally:
            logger.debug("ImportWorker.run() finishing, closing database connection")
            try:
                self.db_connection.close()
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")
            logger.debug("Emitting finished signal")
            self.finished_signal.emit()

class ImportDialog(QDialog):
    """Modal dialog for importing trajectory data into SQLite databases."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Trajectory Data")
        self.setFixedSize(800, 600)
        self._input_path = ""
        self._output_path = ""
        self._configs = {}
        self._selected_file_type = ""
        self._worker_thread = None
        self._worker = None
        self._log_file = Path("trajectory_tools.log")
        logger.debug("ImportDialog initialized")
        self._setup_ui()
        self._center_dialog()
        self._initialize()

    def _setup_ui(self):
        """Set up the dialog layout and widgets."""
        logger.debug("Setting up UI")
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
        logger.debug("Initializing dialog")
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
        """Log a message to the text edit and log file."""
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
        logger.debug("Selecting input file")
        file_types = ";".join([f"{ft} (*.{config['file_ext']})" for ft, config in self._configs.items()])
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            "",
            file_types
        )
        
        if file_path:
            self._input_path = file_path
            self._selected_file_type = selected_filter.split(" (")[0]
            self._output_path = str(Path(file_path).with_suffix(".sqlite"))
            logger.debug(f"Input file selected: {file_path}, type: {self._selected_file_type}")
            self._log_message(f"Info: Input file selected: {file_path}", True)
            self._log_message(f"Info: Output file set to: {self._output_path}", True)
            if Path(self._output_path).exists():
                self._log_message(f"Warning: Output file {self._output_path} will be overwritten.", False)
            self._log_message("Info: You can change the output file.", True)
            self._output_button.setEnabled(True)
            self._import_button.setEnabled(True)

    def _select_output(self):
        """Handle output file selection."""
        logger.debug("Selecting output file")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output SQLite Database",
            self._output_path,
            "SQLite Database (*.sqlite)"
        )
        
        if file_path:
            self._output_path = file_path
            logger.debug(f"Output file changed to: {file_path}")
            self._log_message(f"Info: Output file changed to: {file_path}", True)

    def _start_import(self):
        """Start the import process in a separate thread."""
        logger.debug("Starting import process")
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self._input_button.setEnabled(False)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(True)
        
        if not self._selected_file_type or self._selected_file_type not in self._configs:
            logger.error(f"Invalid file type: {self._selected_file_type}")
            self._log_message(f"Error: Invalid or missing file type '{self._selected_file_type}'.", False)
            self._input_button.setEnabled(True)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)
            return
        
        config = self._configs[self._selected_file_type]
        
        # DON'T create the connection here - let the worker create it
        # This prevents file locking issues
        logger.debug("Creating worker thread")
        self._worker_thread = QThread()
        self._worker = ImportWorker(config, self._input_path, self._output_path, None)  # Pass None for db_connection
        
        # CRITICAL: Move worker to thread AFTER creating all connections
        logger.debug("Connecting signals before moving to thread")
        
        # Connect signals BEFORE moving to thread
        self._worker.log_signal.connect(self._log_message)
        self._worker.progress_signal.connect(self._progress_bar.setValue)
        self._worker.finished_signal.connect(self._finish_import)
        
        # Connect thread started to worker run - use Qt.QueuedConnection explicitly
        self._worker_thread.started.connect(self._worker.run, Qt.QueuedConnection)
        
        # Add debug connection to verify thread started signal
        self._worker_thread.started.connect(lambda: logger.debug("QThread started signal emitted"))
        
        # Move to thread AFTER signal connections
        logger.debug("Moving worker to thread")
        self._worker.moveToThread(self._worker_thread)
        
        # Verify the worker is in the correct thread
        logger.debug(f"Worker thread: {self._worker.thread()}")
        logger.debug(f"Created thread: {self._worker_thread}")
        
        logger.debug("Starting worker thread")
#        self._worker_thread.start()
        self._worker.run()  # Direct call to test if the method works
        
        # Add a small delay and check if thread is running
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self._check_thread_status)

    def _interrupt_import(self):
        """Handle interrupt button click."""
        logger.debug("Interrupt requested")
        if self._worker:
            self._worker.interrupt_flag = True
        self._interrupt_button.setEnabled(False)
        self._log_message("Warning: Interrupt requested", False)

    def _finish_import(self):
        """Handle import completion or interruption."""
        logger.debug("Finishing import process")
        
        if self._worker_thread and self._worker_thread.isRunning():
            logger.debug("Waiting for worker thread to finish")
            self._worker_thread.quit()
            success = self._worker_thread.wait(5000)  # Wait up to 5 seconds
            if not success:
                logger.warning("Worker thread did not finish within timeout")
                self._worker_thread.terminate()
                self._worker_thread.wait()
        
        if self._worker and self._worker.interrupt_flag:
            logger.debug("Import was interrupted, cleaning up")
            if Path(self._output_path).exists():
                Path(self._output_path).unlink()
            self._input_path = ""
            self._output_path = ""
        else:
            logger.debug("Import completed normally")
            self._input_path = ""
        
        # Clean up references
        self._worker = None
        self._worker_thread = None
        
        self._input_button.setEnabled(True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        logger.debug("Import process finished")

    def _check_thread_status(self):
        """Debug method to check thread status after start."""
        if self._worker_thread:
            logger.debug(f"Thread running: {self._worker_thread.isRunning()}")
            logger.debug(f"Thread finished: {self._worker_thread.isFinished()}")
            if self._worker:
                logger.debug(f"Worker thread ID: {self._worker.thread()}")
                logger.debug(f"Main thread ID: {self.thread()}")

    def closeEvent(self, event):
        """Handle dialog close event."""
        logger.debug("Dialog close event triggered")
        if self._worker_thread and self._worker_thread.isRunning():
            logger.debug("Worker thread still running, interrupting")
            if self._worker:
                self._worker.interrupt_flag = True
            self._worker_thread.quit()
            success = self._worker_thread.wait(3000)
            if not success:
                logger.warning("Forcing thread termination")
                self._worker_thread.terminate()
                self._worker_thread.wait()
        event.accept()

    def get_output_path(self) -> str:
        """Get the output file path."""
        return self._output_path