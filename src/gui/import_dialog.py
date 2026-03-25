import json
import sqlite3
import logging
import math
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QTextEdit, QFileDialog, QMessageBox
from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from data_import.base_process import BaseProcess
from utils import get_resource_path
import importlib

# Set up logger
logger = logging.getLogger(__name__)

class _ImportWorkerThread(QThread):
    """Worker thread for handling import operations."""

    log_signal = pyqtSignal(str, bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, config: dict, input_path: str, output_path: str):
        super().__init__()
        self.config = config
        self.input_path = input_path
        self.output_path = output_path
        self.db_connection = None
        self.interrupt_flag = False
        logger.debug(f"ImportWorkerThread initialized with input: {input_path}, output: {output_path}")

    def run(self):
        """Execute the import process."""
        logger.debug("ImportWorkerThread.run() started")
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
                    veh_time REAL NOT NULL,
                    lane_id INTEGER,
                    orig_lane_id INTEGER,
                    x_map_loc REAL,
                    y_map_loc REAL,
                    x_frenet_loc REAL,
                    y_frenet_loc REAL,
                    x_map_origin REAL,
                    y_map_origin REAL,
                    veh_lat REAL,
                    veh_lon REAL,
                    veh_speed REAL,
                    veh_accel REAL,
                    veh_length REAL,
                    veh_width REAL,
                    veh_automation INTEGER,
                    osm_way_id INTEGER,
                    osm_speed_limit INTEGER,
                    osm_traffic_control INTEGER,
                    preceding_veh_id INTEGER,
                    veh_dist_trav REAL,
                    gap_b REAL,
                    gap_f REAL,
                    hway_b REAL,
                    hway_f REAL,
                    event_name TEXT,
                    PRIMARY KEY (event_id, veh_id, veh_time)
                ) WITHOUT ROWID;
            """)
            
            # Create ranges table for event summaries
            self.db_connection.execute("""
                CREATE TABLE ranges (
                    event_id INTEGER PRIMARY KEY,
                    summary_json TEXT NOT NULL
                );
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

            if error_count == 0:
                if not self.interrupt_flag:
                    cursor = self.db_connection.cursor()
                    logger.debug("Creating event summaries")
                    self.log_signal.emit("Creating event summaries", True)
                    
                    # Get distinct event IDs
                    cursor.execute("SELECT DISTINCT event_id FROM events ORDER BY event_id")
                    event_ids = [row[0] for row in cursor.fetchall()]
                    
                    logger.debug(f"Creating summaries for {len(event_ids)} events")
                    self.log_signal.emit(f"Creating summaries for {len(event_ids)} events", True)
                    
                    for i, event_id in enumerate(event_ids):
                        if self.interrupt_flag:
                            logger.debug("Interrupt flag set during event summary creation")
                            break
                        
                        # Get event name
                        cursor.execute("""
                            SELECT DISTINCT event_name FROM events
                            WHERE event_id = ?
                        """, (event_id,))
                        event_name, = cursor.fetchone()
                        
                        # Get time and distance ranges
                        cursor.execute("""
                            SELECT MIN(veh_time), MAX(veh_time), MIN(veh_dist_trav), MAX(veh_dist_trav)
                            FROM events
                            WHERE event_id = ?
                        """, (event_id,))
                        time_min, time_max, dist_min, dist_max = cursor.fetchone()
                        
                        # Get unique lane IDs
                        cursor.execute("""
                            SELECT DISTINCT lane_id FROM events
                            WHERE event_id = ?
                            ORDER BY lane_id
                        """, (event_id,))
                        lane_ids = [row[0] for row in cursor.fetchall() if row[0] is not None]
                        
                        # Get unique vehicle IDs
                        cursor.execute("""
                            SELECT DISTINCT veh_id FROM events
                            WHERE event_id = ?
                            ORDER BY veh_id
                        """, (event_id,))
                        veh_ids = [row[0] for row in cursor.fetchall()]
                        
                        # Create summary JSON
                        summary = {
                            "event_name": event_name,
                            "veh_time": [math.floor(time_min) if time_min is not None else 0, 
                                        math.ceil(time_max) if time_max is not None else 0],
                            "veh_dist_trav": [math.floor(dist_min) if dist_min is not None else 0, 
                                            math.ceil(dist_max) if dist_max is not None else 0],
                            "lane_id": lane_ids,
                            "veh_id": veh_ids
                        }
                        
                        # Insert summary into ranges table
                        cursor.execute("""
                            INSERT INTO ranges (event_id, summary_json)
                            VALUES (?, ?)
                        """, (event_id, json.dumps(summary)))
                        
                        # Update progress bar based on event processing
                        if len(event_ids) > 0:
                            progress = int(((i + 1) / len(event_ids)) * 100)
                            self.progress_signal.emit(progress)
                            logger.debug(f"Processed event summary {i + 1}/{len(event_ids)}")
                        
                        # Check for interrupt after each event
                        if self.interrupt_flag:
                            logger.debug("Interrupt flag set after event summary processing")
                            break
                    
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
            logger.exception(f"Exception in ImportWorkerThread.run(): {e}")
            self.log_signal.emit(f"Error during import: {str(e)}", False)
        finally:
            logger.debug("ImportWorkerThread.run() finishing, closing database connection")
            try:
                if self.db_connection:
                    self.db_connection.close()
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")


class ImportDialog(QDialog):
    """Modal dialog for importing trajectory data into SQLite databases.

    Signals:
        import_finished (str): Emitted when the dialog closes with the most recently imported database file path.
    """

    import_finished = pyqtSignal(str)  # Signal to emit output path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Trajectory Data")
        self.setFixedSize(800, 600)
        self._input_path = ""
        self._output_path = ""
        self._configs = {}
        self._selected_file_type = ""
        self._worker_thread = None
        # Use get_resource_path for log file to work in both development and PyInstaller environments
        self._log_file = Path(get_resource_path("trajectory_tools.log"))
        self._import_completed = False  # Track if import completed successfully
        self._successful_import_path = ""  # Store path of successful import
        logger.debug("ImportDialog initialized")
        self._setup_ui()
        self._center_dialog()
        self._initialize()

    def __del__(self):
        logger.debug("ImportDialog.__del__ called")

    def _setup_ui(self):
        """Set up the dialog layout and widgets."""
        logger.debug("Setting up UI")
        layout = QVBoxLayout(self)
        
        # Text edit
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        layout.addWidget(self._text_edit, stretch=1)
        
        # Progress bar (moved between text area and buttons)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        layout.addWidget(self._progress_bar)
        
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
        self._import_button.clicked.connect(self._on_import)
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
        # Keep close button enabled throughout
        
        # Clear log file
        if self._log_file.exists():
            self._log_file.unlink()
        
        # Use get_resource_path for config directory to work in both development and PyInstaller environments
        config_dir = Path(get_resource_path("config"))
        config_dir.mkdir(exist_ok=True)
        
        # Check JSON configuration files
        config_files = list(config_dir.glob("*.json"))
        if not config_files:
            self._log_message("Error: No JSON configuration files found in 'config' directory.", False)
            return
        
        for config_file in config_files:
            try:
                with config_file.open('r') as f:
                    config = json.load(f)
                
                if not isinstance(config, dict):
                    self._log_message(f"Error: {config_file} does not contain a JSON object.", False)
                    continue
                
                if "col_defs" not in config:
                    self._log_message(f"Error: {config_file} missing 'col_defs' key.", False)
                    continue
                
                config.setdefault("file_ext", "csv")
                config.setdefault("class", "data_import.csv_process.CsvProcess")
                self._configs[Path(config_file).stem] = config

            except json.JSONDecodeError:
                self._log_message(f"Error: {config_file} is not a valid JSON file.", False)
        
        if not self._configs:
            self._log_message("Error: No valid JSON configuration files found. Import cannot continue.", False)
        else:
            file_types = ", ".join(self._configs.keys())
            self._log_message(f"Info: Available file types for import: {file_types}", True)
            self._log_message("Info: Please select an input file.", True)
            self._input_button.setEnabled(True)

    def _log_message(self, message: str, is_html: bool = False):
        """Log a message to the text edit and log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display_message = f"[{timestamp}] {message}"
        
        if is_html:
            self._text_edit.append(display_message)
        else:
            self._text_edit.append(display_message.replace("<", "<").replace(">", ">"))
        
        # Write plain text to log file
        plain_message = display_message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        with self._log_file.open('a') as f:
            f.write(plain_message + "\n")
        
        self._text_edit.verticalScrollBar().setValue(self._text_edit.verticalScrollBar().maximum())

    def _select_input(self):
        """Handle input file selection."""
        logger.debug("Selecting input file")
        file_types = ";;".join([f"{ft} (*.{config['file_ext']})" for ft, config in self._configs.items()])
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

    def _on_import(self):
        QTimer.singleShot(200, self._start_import) # delay for button repaint

    def _start_import(self):
        """Start the import process in a separate thread."""
        logger.debug("Starting import process")
        
        # Keep close button enabled, handle close logic in closeEvent
        self._input_button.clearFocus()
        self._input_button.setEnabled(False)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(True)
        self._import_completed = False  # Reset completion flag
        
        if not self._selected_file_type or self._selected_file_type not in self._configs:
            logger.error(f"Invalid file type: {self._selected_file_type}")
            self._log_message(f"Error: Invalid or missing file type '{self._selected_file_type}'.", False)
            self._input_button.setEnabled(True)
            return
        
        config = self._configs[self._selected_file_type]
        
        logger.debug("Creating worker thread")
        self._worker_thread = _ImportWorkerThread(config, self._input_path, self._output_path)
        
        # Connect signals directly to the thread
        self._worker_thread.log_signal.connect(self._log_message)
        self._worker_thread.progress_signal.connect(self._progress_bar.setValue)
        self._worker_thread.finished.connect(self._finish_import)
        
        logger.debug("Starting worker thread")
        self._worker_thread.start()

    def _interrupt_import(self):
        """Handle interrupt button click."""
        logger.debug("Interrupt requested")
        if self._worker_thread:
            self._worker_thread.interrupt_flag = True
        self._interrupt_button.setEnabled(False)
        self._log_message("Warning: Interrupt requested", False)

    def _finish_import(self):
        """Handle import completion without closing dialog."""
        logger.debug("Import process completed")
        
        # Always reset progress bar to 0
        self._progress_bar.setValue(0)
        
        # Check if import was successful (not interrupted and output file exists)
        if (not getattr(self._worker_thread, 'interrupt_flag', False) and 
            self._output_path and 
            Path(self._output_path).exists()):
            self._import_completed = True
            self._successful_import_path = self._output_path  # Store successful path
            logger.debug("Import completed successfully")
        else:
            self._import_completed = False
            self._successful_import_path = ""  # Clear on failure
            logger.debug("Import was interrupted or failed")
        
        # Clean up thread
        if self._worker_thread:
            self._worker_thread.wait()  # Wait for thread to finish
            self._worker_thread.deleteLater()
            self._worker_thread = None
        
        # Reset UI for next import - clear paths and reset button states
        self._input_path = ""
        self._output_path = ""
        self._selected_file_type = ""
        
        # Reset button states: only Input enabled, others disabled
        self._input_button.setEnabled(True)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(False)
        
        logger.debug("Import process finished, dialog ready for next import")

    def closeEvent(self, event):
        """Handle dialog close event and emit the output path."""
        logger.debug("Dialog close event triggered")
        
        # If import is running, ask user for confirmation
        if self._worker_thread and self._worker_thread.isRunning():
            logger.debug("Import is running, asking user for confirmation")
            
            reply = QMessageBox.question(
                self,
                "Import In Progress",
                "An import process is currently running. Do you want to interrupt it and close the dialog?\n\n"
                "Warning: Interrupting the import will cancel the process and any partial results will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel  # Default to Cancel for safety
            )
            
            if reply == QMessageBox.StandardButton.Cancel:
                logger.debug("User chose to cancel dialog close")
                event.ignore()  # Prevent the dialog from closing
                return
            
            # User chose Yes - interrupt the import
            logger.debug("User confirmed to interrupt import and close dialog")
            self._worker_thread.interrupt_flag = True
            self._log_message("Warning: Import interrupted by user", False)
            
            # Wait for thread to finish
            self._worker_thread.quit()
            success = self._worker_thread.wait(3000)
            if not success:
                logger.warning("Forcing thread termination")
                self._worker_thread.terminate()
                self._worker_thread.wait()
            
            # Mark import as not completed since it was interrupted
            self._import_completed = False
        
        # Only emit signal if import was completed successfully
        if self._import_completed and self._successful_import_path and Path(self._successful_import_path).exists():
            logger.debug(f"Emitting import_finished signal with path: {self._successful_import_path}")
            self.import_finished.emit(self._successful_import_path)
        else:
            logger.debug("No valid output path to emit or import was not completed")
            self.import_finished.emit("")
        
        event.accept()