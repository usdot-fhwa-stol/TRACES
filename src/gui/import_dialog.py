import json
import sqlite3
import logging
import math
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QTextEdit, QFileDialog, QMessageBox, QLabel
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from data_import.base_process import BaseProcess
from utils import get_user_data_path, last_dialog_dir, remember_dialog_path
import importlib

# Set up logger
logger = logging.getLogger(__name__)


class _ImportWorkerThread(QThread):
    """Worker thread for handling import operations.

    Receives a validated processor instance from the dialog and runs the
    worker-side import stages in order:
        1. Create the events table from processor.column_defs().
        2. processor.check_file()
        3. processor.import_data()
        4. Create and populate the ranges table (generic over the events
           table; performed here, not by the processor).
    """

    log_signal = pyqtSignal(str, bool)
    progress_signal = pyqtSignal(int)

    def __init__(self, processor: BaseProcess, input_path: str, output_path: str):
        super().__init__()
        self.processor = processor
        self.input_path = input_path
        self.output_path = output_path
        self.db_connection = None
        self.interrupt_flag = False
        self.success = False
        self._continue_to_plot = False
        logger.debug(f"ImportWorkerThread initialized with input: {input_path}, output: {output_path}")

    def _cleanup_failed_import(self):
        """Close the database connection and remove the output file on failure."""
        if self.db_connection:
            try:
                self.db_connection.close()
            except Exception as e:
                logger.error(f"Error closing database connection during cleanup: {e}")
            self.db_connection = None
        try:
            if Path(self.output_path).exists():
                Path(self.output_path).unlink()
        except OSError as e:
            logger.error(f"Could not remove failed output file: {e}")

    def _create_events_table(self):
        """Create the events table from the processor's column definitions.

        The schema is config-driven: column names, order, and SQLite type
        affinities all come from processor.column_defs(). The composite
        primary key over the required columns provides duplicate-record
        suppression via INSERT OR IGNORE.
        """
        col_defs = self.processor.column_defs()
        logger.debug(f"Creating events table with {len(col_defs)} columns")

        column_sql = []
        for name, affinity in col_defs.items():
            if name in ("event_id", "veh_id", "veh_time"):
                column_sql.append(f"{name} {affinity} NOT NULL")
            else:
                column_sql.append(f"{name} {affinity}")

        create_sql = (
            "CREATE TABLE events (\n    "
            + ",\n    ".join(column_sql)
            + ",\n    PRIMARY KEY (event_id, veh_id, veh_time)\n"
            + ") WITHOUT ROWID;"
        )
        logger.debug(f"Events table SQL:\n{create_sql}")
        self.db_connection.execute(create_sql)
        self.db_connection.commit()

    def _create_ranges_table(self) -> bool:
        """Create and populate the ranges table with per-event summaries.

        Defensive against optional columns: orig_lane_id and event_name may
        be absent from a config-driven schema. Required columns (event_id,
        veh_id, veh_time, lane_id, veh_dist_trav) are guaranteed present.

        Returns:
            bool: True if summaries completed, False if interrupted.
        """
        cursor = self.db_connection.cursor()

        self.db_connection.execute("""
            CREATE TABLE ranges (
                event_id INTEGER PRIMARY KEY,
                summary_json TEXT NOT NULL
            );
        """)

        # Determine which optional columns exist in this database
        cursor.execute("PRAGMA table_info(events)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        has_orig_lane = "orig_lane_id" in existing_cols
        has_event_name = "event_name" in existing_cols

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
                return False

            # Get event name (optional column; fall back to a generated label)
            event_name = f"Event {event_id}"
            if has_event_name:
                cursor.execute("""
                    SELECT DISTINCT event_name FROM events
                    WHERE event_id = ?
                """, (event_id,))
                result = cursor.fetchone()
                if result and result[0] is not None:
                    event_name = result[0]

            # Get time and distance ranges
            cursor.execute("""
                SELECT MIN(veh_time), MAX(veh_time), MIN(veh_dist_trav), MAX(veh_dist_trav)
                FROM events
                WHERE event_id = ?
            """, (event_id,))
            time_min, time_max, dist_min, dist_max = cursor.fetchone()

            # Get unique lane IDs; pair with orig_lane_id when available,
            # otherwise pair with None so consumers see a consistent shape.
            if has_orig_lane:
                cursor.execute("""
                    SELECT DISTINCT lane_id, orig_lane_id FROM events
                    WHERE event_id = ?
                    ORDER BY lane_id
                """, (event_id,))
                lane_ids = [[row[0], row[1]] for row in cursor.fetchall() if row[0] is not None]
            else:
                cursor.execute("""
                    SELECT DISTINCT lane_id FROM events
                    WHERE event_id = ?
                    ORDER BY lane_id
                """, (event_id,))
                lane_ids = [[row[0], None] for row in cursor.fetchall() if row[0] is not None]

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

        return True

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

            # Stage: create events table from the processor's column defs
            self._create_events_table()

            # Stage: validate the input file
            logger.debug("Checking input file")
            error_count = self.processor.check_file(self.input_path, self)

            # Stage: import the data
            if error_count == 0 and not self.interrupt_flag:
                logger.debug("Starting data import")
                error_count = self.processor.import_data(self.input_path, self.db_connection, self)
                logger.debug(f"Data import completed with {error_count} errors")

            # Stage: create the ranges table last — its presence marks a
            # completed import.
            if error_count == 0 and not self.interrupt_flag:
                completed = self._create_ranges_table()

                if completed and not self.interrupt_flag:
                    logger.debug("Committing final database changes")
                    self.db_connection.commit()
                    self.log_signal.emit(f"{self.input_path} successfully imported", True)
                    logger.info(f"Import completed successfully: {self.input_path}")
                else:
                    logger.debug("Import was interrupted, cleaning up")
                    self.log_signal.emit("Import interrupted", False)
                    self._cleanup_failed_import()   # closes connection, then deletes file
            else:
                logger.debug("Import was interrupted or failed, cleaning up")
                if self.interrupt_flag:
                    self.log_signal.emit("Import interrupted", False)
                self._cleanup_failed_import()   # closes connection, then deletes file

        except Exception as e:
            logger.exception(f"Exception in ImportWorkerThread.run(): {e}")
            self.log_signal.emit(f"Error during import: {str(e)}", False)
            error_count += 1
            self._cleanup_failed_import()
        finally:
            logger.debug("ImportWorkerThread.run() finishing, closing database connection")
            if self.db_connection:
                try:
                    self.db_connection.close()
                except Exception as e:
                    logger.error(f"Error closing database connection: {e}")
                self.db_connection = None

        if error_count == 0 and not self.interrupt_flag:
            self.log_signal.emit("Info: Import completed successfully.", True)
            self.success = True


class ImportDialog(QDialog):
    """Modal dialog for importing trajectory data into SQLite databases.

    The dialog owns configuration discovery: it reads the JSON files in the
    config folder, keeps only the top-level ``file_ext`` and ``class`` keys
    for itself, resolves and instantiates each processor class, and asks the
    processor whether the configuration is valid. Configurations that fail
    validation are excluded from the file type selector. At import start a
    FRESH processor instance is created and validated, then handed to the
    worker thread — processor instances are never reused across imports.

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
        self._configs = {}  # file type -> {"config": dict, "processor_class": type}
        self._selected_file_type = ""
        self._worker_thread = None
        # Use get_user_data_path for log file to work in both development and PyInstaller environments
        self._log_file = get_user_data_path("trajectory_tools.log")
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

        self._input_button.setToolTip("Select the input file to import")
        self._output_button.setToolTip("Set the database output path")
        self._import_button.setToolTip("Begin importing the selected file")
        self._interrupt_button.setToolTip("Stop the running import process")

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

    def _resolve_processor_class(self, class_path: str):
        """Resolve a fully qualified class name to a BaseProcess subclass.

        Args:
            class_path (str): Dotted path, e.g. 'data_import.csv_process.CsvProcess'.

        Returns:
            type: The resolved class, or None if it could not be resolved or
                is not a BaseProcess subclass. Errors are logged to the dialog.
        """
        try:
            module_name, class_name = class_path.rsplit(".", 1)
            module = importlib.import_module(module_name)
            processor_class = getattr(module, class_name)
        except (ValueError, ImportError, AttributeError) as e:
            self._log_message(f"Error: Cannot resolve processor class '{class_path}': {e}", False)
            return None

        if not isinstance(processor_class, type) or not issubclass(processor_class, BaseProcess):
            self._log_message(f"Error: '{class_path}' is not a subclass of BaseProcess.", False)
            return None

        return processor_class

    def _initialize(self):
        """Initialize the dialog by checking configuration files.

        Each configuration is validated with a throwaway processor instance;
        configurations that fail validation are reported and excluded from
        the file type selector. Info-level validation messages are suppressed
        here to keep the startup log readable — full validation output is
        produced again at import start.
        """
        logger.debug("Initializing dialog")
        # Keep close button enabled throughout

        # Clear log file
        if self._log_file.exists():
            self._log_file.unlink()

        # Use get_user_data_path for config directory to work in both development and PyInstaller environments
        config_dir = get_user_data_path("config")
        config_dir.mkdir(exist_ok=True)

        # Check JSON configuration files
        config_files = list(config_dir.glob("*.json"))
        if not config_files:
            self._log_message("Error: No JSON configuration files found in 'config' directory.", False)
            return

        def quiet_log(message: str, is_html: bool):
            """Suppress Info messages during startup validation."""
            if not message.startswith("Info:"):
                self._log_message(message, is_html)

        for config_file in config_files:
            try:
                with config_file.open('r') as f:
                    config = json.load(f)
            except json.JSONDecodeError:
                self._log_message(f"Error: {config_file} is not a valid JSON file.", False)
                continue

            if not isinstance(config, dict):
                self._log_message(f"Error: {config_file} does not contain a JSON object.", False)
                continue

            if "col_defs" not in config:
                self._log_message(f"Error: {config_file} missing 'col_defs' key.", False)
                continue

            config.setdefault("file_ext", "csv")
            config.setdefault("class", "data_import.csv_process.CsvProcess")

            # Resolve and verify the processor class
            processor_class = self._resolve_processor_class(config["class"])
            if processor_class is None:
                self._log_message(f"Error: {config_file} excluded (invalid processor class).", False)
                continue

            # Validate the configuration with a throwaway processor instance
            try:
                processor = processor_class()
                error_count = processor.check_configuration(config, quiet_log)
            except Exception as e:
                logger.exception(f"Exception validating {config_file}: {e}")
                self._log_message(f"Error: {config_file} excluded (validation raised: {e}).", False)
                continue

            if error_count > 0:
                self._log_message(
                    f"Error: {config_file} excluded ({error_count} configuration error(s)).", False)
                continue

            self._configs[Path(config_file).stem] = {
                "config": config,
                "processor_class": processor_class,
            }

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
            self._text_edit.append(display_message.replace("<", "&lt;").replace(">", "&gt;"))

        # Write plain text to log file
        plain_message = display_message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        with self._log_file.open('a') as f:
            f.write(plain_message + "\n")

        self._text_edit.verticalScrollBar().setValue(self._text_edit.verticalScrollBar().maximum())

    def _select_input(self):
        """Handle input file selection."""
        logger.debug("Selecting input file")
        file_types = ";;".join(
            [f"{ft} (*.{entry['config']['file_ext']})" for ft, entry in self._configs.items()]
        )
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            last_dialog_dir(),
            file_types
        )

        if file_path:
            remember_dialog_path(file_path)
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
            remember_dialog_path(file_path)
            self._output_path = file_path
            logger.debug(f"Output file changed to: {file_path}")
            self._log_message(f"Info: Output file changed to: {file_path}", True)

    def _on_import(self):
        QTimer.singleShot(200, self._start_import) # delay for button repaint

    def _start_import(self):
        """Start the import process in a separate thread.

        Creates a fresh processor instance, validates the configuration with
        it (full logging this time), and hands the validated instance to the
        worker thread.
        """
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
            self._interrupt_button.setEnabled(False)
            return

        entry = self._configs[self._selected_file_type]
        config = entry["config"]
        processor_class = entry["processor_class"]

        # Fresh processor instance for this import run — never reuse an
        # instance, so no state (e.g. group indices) leaks between imports.
        try:
            processor = processor_class()
            error_count = processor.check_configuration(config, self._log_message)
        except Exception as e:
            logger.exception(f"Exception validating configuration at import start: {e}")
            self._log_message(f"Error: Configuration validation failed: {e}", False)
            error_count = 1

        if error_count > 0:
            self._log_message("Error: Configuration is invalid. Import cannot continue.", False)
            self._input_button.setEnabled(True)
            self._interrupt_button.setEnabled(False)
            return

        logger.debug("Creating worker thread")
        self._worker_thread = _ImportWorkerThread(processor, self._input_path, self._output_path)

        # Connect signals directly to the thread
        self._worker_thread.log_signal.connect(self._log_message)
        self._worker_thread.progress_signal.connect(self._progress_bar.setValue)
        self._worker_thread.finished.connect(self._finish_import)

        logger.debug("Starting worker thread")
        self._worker_thread.start()

    def _confirm_interrupt(self) -> bool:
        """Ask the user to confirm interrupting the running import.

        Returns:
            bool: True if the user confirmed (Yes), False if they declined (No).
        """
        reply = QMessageBox.question(
            self,
            "Import In Progress",
            "An import process is currently running. Do you want to interrupt it?\n\n"
            "Warning: Interrupting the import will cancel the process and any "
            "partial results will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No   # default to No for safety
        )
        return reply == QMessageBox.StandardButton.Yes

    def _interrupt_import(self):
        """Handle interrupt button click."""
        logger.debug("Interrupt requested")
        if not self._worker_thread:
            return

        if not self._confirm_interrupt():
            logger.debug("User declined interrupt")
            return

        self._worker_thread.interrupt_flag = True
        self._interrupt_button.setEnabled(False)
        self._log_message("Warning: Import interrupted by user", False)
        logger.debug("Interrupt confirmed and flagged")

    def _show_completion_popup(self, success: bool):
        """Show a completion popup with context-appropriate buttons.

        Args:
            success (bool): Whether the import completed successfully.
        """
        popup = QDialog(self)
        popup.setWindowTitle("Import Complete" if success else "Import Unsuccessful")
        popup.setFixedSize(400, 150)
        popup.setWindowFlags(
            popup.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Message label
        if success:
            msg = QLabel(
                "The file was imported successfully.\n\n"
                "Click Continue to open the plot settings.\n"
                "Click Back to import another file."
            )
        else:
            msg = QLabel(
                "The import did not complete successfully.\n\n"
                "Click Back to review the import log for details."
            )
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg)

        # Button row
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        back_btn = QPushButton("Back")
        back_btn.setMinimumWidth(80)
        back_btn.clicked.connect(popup.reject)
        btn_layout.addWidget(back_btn)

        if success:
            continue_btn = QPushButton("Continue")
            continue_btn.setMinimumWidth(80)
            continue_btn.setDefault(True)
            continue_btn.clicked.connect(popup.accept)
            btn_layout.addWidget(continue_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        result = popup.exec()

        if success and result == QDialog.DialogCode.Accepted:
            self._continue_to_plot = True
            self.close()  # triggers closeEvent which emits import_finished

    def _finish_import(self):
        """Handle import completion without closing dialog."""
        logger.debug("Import process completed")

        self._progress_bar.setValue(0)

        if getattr(self._worker_thread, 'success', False):
            self._import_completed = True
            self._successful_import_path = self._output_path
            logger.debug("Import completed successfully")
        else:
            self._import_completed = False
            self._successful_import_path = ""
            logger.debug("Import was interrupted or failed")

        if self._worker_thread:
            self._worker_thread.wait()
            self._worker_thread.deleteLater()
            self._worker_thread = None

        self._input_path = ""
        self._output_path = ""
        self._selected_file_type = ""

        self._input_button.setEnabled(True)
        self._output_button.setEnabled(False)
        self._import_button.setEnabled(False)
        self._interrupt_button.setEnabled(False)

        logger.debug("Import process finished, dialog ready for next import")

        # Show completion popup after UI is fully reset
        self._show_completion_popup(self._import_completed)

    def closeEvent(self, event):
        """Handle dialog close event and emit the output path."""
        logger.debug("Dialog close event triggered")

        if self._worker_thread and self._worker_thread.isRunning():
            logger.debug("Import is running, asking user for confirmation")

            if not self._confirm_interrupt():
                logger.debug("User chose not to interrupt; keeping dialog open")
                event.ignore()
                return

            # User confirmed — interrupt the import
            logger.debug("User confirmed interrupt; closing dialog")
            self._worker_thread.interrupt_flag = True
            self._log_message("Warning: Import interrupted by user", False)

            self._worker_thread.quit()
            if not self._worker_thread.wait(3000):
                logger.warning("Forcing thread termination")
                self._worker_thread.terminate()
                self._worker_thread.wait()

            self._import_completed = False

        # Emit result signal
        if (self._import_completed
                and self._successful_import_path
                and Path(self._successful_import_path).exists()):
            logger.debug(f"Emitting import_finished signal: {self._successful_import_path}")
            self.import_finished.emit(self._successful_import_path)
        else:
            logger.debug("No valid output path to emit or import was not completed")
            self.import_finished.emit("")

        event.accept()