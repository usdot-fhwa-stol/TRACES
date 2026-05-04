import csv
import sqlite3
import logging
import json
from pathlib import Path
from queue import Queue, Empty
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                           QProgressBar, QLabel, QCheckBox, QComboBox, 
                           QFileDialog, QMessageBox, QGridLayout, QWidget)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

# Set up logger
logger = logging.getLogger(__name__)


class _ExportWorkerThread(QThread):
    """Consumer thread for writing CSV data from the queue."""

    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(self, output_path: str, columns: list, data_queue: Queue):
        """Initialize the export worker thread.
        
        Args:
            output_path (str): Path to the output CSV file.
            columns (list): List of column names to export.
            data_queue (Queue): Queue to receive data batches from producer.
        """
        super().__init__()
        self.output_path = output_path
        self.columns = columns
        self.data_queue = data_queue
        self.interrupt_flag = False
        logger.debug(f"ExportWorkerThread initialized: {output_path}, columns: {len(columns)}")

    def run(self):
        """Execute the CSV writing process."""
        logger.debug("ExportWorkerThread.run() started")
        records_written = 0
        
        try:
            # Open output file for writing
            with open(self.output_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                
                # Write header
                writer.writerow(self.columns)
                
                # Process data from queue
                while True:
                    if self.interrupt_flag:
                        logger.debug("Export interrupted by user")
                        self.finished_signal.emit(False, "Export interrupted by user.")
                        return
                    
                    try:
                        # Get data from queue (blocking with timeout)
                        data_item = self.data_queue.get(timeout=1.0)
                        
                        # Check for end-of-data marker
                        if data_item is None:
                            logger.debug("Received end-of-data marker")
                            break
                        
                        # Extract batch data and progress info
                        batch_data, progress_percent = data_item
                        
                        # Write batch to CSV
                        writer.writerows(batch_data)
                        records_written += len(batch_data)
                        
                        # Update progress
                        self.progress_signal.emit(progress_percent)
                        
                        # Mark task as done
                        self.data_queue.task_done()
                        
                        logger.debug(f"Processed batch: {len(batch_data)} records, total: {records_written}")
                        
                    except Empty:
                        # Timeout - continue loop to check interrupt flag
                        continue
            
            if not self.interrupt_flag:
                logger.info(f"Export completed successfully: {self.output_path}")
                self.finished_signal.emit(True, f"Successfully exported {records_written} records to {self.output_path}")
            
        except Exception as e:
            logger.exception(f"Exception in ExportWorkerThread.run(): {e}")
            self.finished_signal.emit(False, f"Error during export: {str(e)}")


class ExportDialog(QDialog):
    """Modal dialog for exporting trajectory data to CSV files.
    
    Operates in two modes:
    1. Export All: Exports all records from the events table
    2. Export Selected: Exports selected columns for a specific event
    
    Uses a producer/consumer pattern where:
    - Producer (main thread): Fetches data from SQLite in batches
    - Consumer (worker thread): Writes data to CSV file
    
    Signals:
        export_finished (list): Emitted when dialog closes with selected column names.
    """

    export_finished = pyqtSignal(list)  # Signal to emit selected columns

    def __init__(self, parent=None, db_connection: sqlite3.Connection = None, 
                 selected_columns: list = None):
        """Initialize the ExportDialog.
        
        Args:
            parent: Parent widget (typically MainWindow).
            db_connection (sqlite3.Connection): Database connection for querying.
            selected_columns (list): List of pre-selected column names. None for Export All mode.
        """
        super().__init__(parent)
        self.db_connection = db_connection
        self.selected_columns = selected_columns if selected_columns is not None else []
        self.is_export_all_mode = selected_columns is None
        self._worker_thread = None
        self._producer_timer = None
        self._data_queue = None
        self._output_path = ""
        self._column_checkboxes = {}
        self._event_combobox = None
        self._export_button = None
        self._interrupt_button = None
        self._progress_bar = None
        
        # Producer state
        self._cursor = None
        self._total_records = 0
        self._records_processed = 0
        self._batch_size = 1000
        
        logger.debug(f"ExportDialog initialized: export_all_mode={self.is_export_all_mode}")
        
        self._setup_ui()
        
        # Use QTimer to center dialog after it's been properly sized
        QTimer.singleShot(0, self._center_dialog)
        if not self.is_export_all_mode:
            # Use QTimer to update event data after UI is fully constructed
            QTimer.singleShot(0, self._update_event_data)

    def _setup_ui(self):
        """Set up the dialog UI based on the mode."""
        # Set window title and size based on mode
        if self.is_export_all_mode:
            self.setWindowTitle("Export All")
        else:
            self.setWindowTitle("Export Selected")
        
        # Create main layout
        layout = QVBoxLayout(self)
        
        # Add note text
        note_label = QLabel()
        note_label.setWordWrap(True)
        
        if self.is_export_all_mode:
            # Get record count for Export All mode
            record_count = self._get_record_count()
            note_label.setText(f"NOTE: {record_count:,} database records will be exported.")
        else:
            note_label.setText("NOTE: Only the selected columns for the selected event are exported. "
                             "The output file cannot be re-imported into the tool.")
        
        layout.addWidget(note_label)
        
        # Add mode-specific content
        if self.is_export_all_mode:
            self._setup_export_all_ui(layout)
        else:
            self._setup_export_selected_ui(layout)
        
        # Add progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)
        
        # Add button controls
        self._setup_button_controls(layout)

    def _setup_export_all_ui(self, layout: QVBoxLayout):
        """Set up UI components specific to Export All mode.
        
        Args:
            layout (QVBoxLayout): Main layout to add components to.
        """
        # Add some spacing for Export All mode
        layout.addStretch()

    def _setup_export_selected_ui(self, layout: QVBoxLayout):
        """Set up UI components specific to Export Selected mode.
        
        Args:
            layout (QVBoxLayout): Main layout to add components to.
        """
        # Get column names from the database
        column_names = self._get_column_names()
        
        # Create column checkboxes in a grid layout (3 columns)
        checkbox_widget = QWidget()
        checkbox_layout = QGridLayout(checkbox_widget)
        
        row = 0
        col = 0
        for column_name in column_names:
            checkbox = QCheckBox(column_name)
            # Check the box if this column was previously selected
            if column_name in self.selected_columns:
                checkbox.setChecked(True)
            
            self._column_checkboxes[column_name] = checkbox
            checkbox_layout.addWidget(checkbox, row, col)
            
            col += 1
            if col >= 4:  # Wrap to next row
                col = 0
                row += 1
        
        layout.addWidget(checkbox_widget)

        self._event_name = QLabel() # centered label to show event name
        self._event_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Calculate the height using font metrics
        self._event_name.setWordWrap(True)
        self._event_name.setMinimumHeight(int(self._event_name.fontMetrics().height() * 2.5))
        layout.addWidget(self._event_name)

    def _setup_button_controls(self, layout: QVBoxLayout):
        """Set up the button control area.
        
        Args:
            layout (QVBoxLayout): Main layout to add button controls to.
        """
        # Create button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        if not self.is_export_all_mode:
            self._event_combobox = QComboBox()
            self._event_combobox.currentIndexChanged.connect(self._on_event_changed)
            self._populate_event_combobox()
            button_layout.addWidget(self._event_combobox)
            button_layout.addStretch()

        # Create Export button
        self._export_button = QPushButton("Export...")
        self._export_button.clicked.connect(self._on_export)
        button_layout.addWidget(self._export_button)
        
        button_layout.addStretch()
        
        # Create Interrupt button
        self._interrupt_button = QPushButton("Interrupt")
        self._interrupt_button.setEnabled(False)
        self._interrupt_button.clicked.connect(self._interrupt_export)
        button_layout.addWidget(self._interrupt_button)
        
        button_layout.addStretch()
        
        layout.addLayout(button_layout)

    def _center_dialog(self):
        """Center the dialog within the parent window."""
        if self.parent():
            parent_rect = self.parent().geometry()
            dialog_rect = self.geometry()
            x = parent_rect.x() + (parent_rect.width() - dialog_rect.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - dialog_rect.height()) // 2
            self.move(x, y)

    def _get_record_count(self) -> int:
        """Get the total number of records in the events table.
        
        Returns:
            int: Total number of records.
        """
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM events")
            return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error getting record count: {e}")
            return 0

    def _get_column_names(self) -> list:
        """Get the column names from the events table.
        
        Returns:
            list: List of column names.
        """
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("PRAGMA table_info(events)")
            columns = [row[1] for row in cursor.fetchall()]
            return columns
        except Exception as e:
            logger.error(f"Error getting column names: {e}")
            return []

    def _populate_event_combobox(self):
        """Populate the event combobox with distinct event IDs."""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT event_id FROM ranges ORDER BY event_id")
            event_ids = [row[0] for row in cursor.fetchall()]
            
            for event_id in event_ids:
                self._event_combobox.addItem(f"Event {event_id}", event_id)
                
        except Exception as e:
            logger.error(f"Error populating event combobox: {e}")

    def _on_event_changed(self, index: int):
        """Handle event combobox selection change.
        
        Args:
            index (int): Selected combobox index.
        """
        self._update_event_data()
            
    def _update_event_data(self):
        """Update the event name label."""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT summary_json FROM ranges WHERE event_id = ?", (self._get_selected_event_id(),))
            result = cursor.fetchone()

            if not result:
                logger.warning(f"No summary data found for event {self._get_selected_event_id()}")
                return
                
            try:
                summary = json.loads(result[0])
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing summary JSON for event {self._get_selected_event_id()}: {e}")
                return
            
            # Update the event name label
            self._event_name.setText(summary.get("event_name", f"Event {self._get_selected_event_id()}"))

        except sqlite3.Error as e:
            logger.error(f"Error updating event data: {e}")

    def _get_selected_columns(self) -> list:
        """Get the list of selected column names.
        
        Returns:
            list: List of selected column names.
        """
        if self.is_export_all_mode:
            return self._get_column_names()
        else:
            return [name for name, checkbox in self._column_checkboxes.items() 
                   if checkbox.isChecked()]

    def _get_selected_event_id(self) -> int:
        """Get the selected event ID from the combobox.
        
        Returns:
            int: Selected event ID, or None if not in Export Selected mode.
        """
        if not self.is_export_all_mode and self._event_combobox:
            return self._event_combobox.currentData()
        return None

    def _on_export(self):
        """Handle the Export button click."""
        # Validate selection for Export Selected mode
        if not self.is_export_all_mode:
            selected_columns = self._get_selected_columns()
            if not selected_columns:
                QMessageBox.warning(self, "Warning", 
                                  "Please select at least one column to export.")
                return
        
        # Show file dialog to select output file
        if self.is_export_all_mode:
            default_filename = "events_all.csv"
            dialog_title = "Export All Events"
        else:
            event_id = self._get_selected_event_id()
            default_filename = f"events_event_{event_id}.csv"
            dialog_title = "Export Selected Columns"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            dialog_title,
            default_filename,
            "CSV Files (*.csv);;All Files (*)"
        )
        
        if not file_path:
            return  # User cancelled
        
        self._output_path = file_path
        
        # Check if file exists and prompt user
        if Path(file_path).exists():
            reply = QMessageBox.question(
                self,
                "File Exists",
                f"The file '{file_path}' already exists. Do you want to overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.No:
                return  # User chose not to overwrite
        
        # Start the export process
        self._start_export()

    def _start_export(self):
        """Start the export process using producer/consumer pattern."""
        logger.debug("Starting export process")
        
        # Disable Export button and enable Interrupt button
        self._export_button.setEnabled(False)
        self._interrupt_button.setEnabled(True)
        
        # Reset progress bar
        self._progress_bar.setValue(0)
        
        # Get export parameters
        selected_columns = self._get_selected_columns()
        event_id = self._get_selected_event_id()
        
        try:
            # Initialize producer (database cursor)
            self._init_producer(selected_columns, event_id)
            
            # Create data queue for producer/consumer communication
            self._data_queue = Queue(maxsize=5)  # Limit queue size to prevent excessive memory usage
            
            # Create and start consumer thread
            self._worker_thread = _ExportWorkerThread(
                self._output_path, 
                selected_columns, 
                self._data_queue
            )
            
            # Connect signals
            self._worker_thread.progress_signal.connect(self._progress_bar.setValue)
            self._worker_thread.finished_signal.connect(self._on_export_finished)
            
            logger.debug("Starting export worker thread")
            self._worker_thread.start()
            
            # Start producer timer to feed data to the queue
            self._producer_timer = QTimer()
            self._producer_timer.timeout.connect(self._produce_data_batch)
            self._producer_timer.start(10)  # Produce data every 10ms
            
        except Exception as e:
            logger.exception(f"Error starting export: {e}")
            self._on_export_finished(False, f"Error starting export: {str(e)}")

    def _init_producer(self, columns: list, event_id: int = None):
        """Initialize the producer (database cursor and query).
        
        Args:
            columns (list): List of column names to export.
            event_id (int, optional): Event ID for filtered export. None for all events.
        """
        # Build the SQL query
        column_list = ", ".join(columns)
        if event_id is not None:
            query = f"SELECT {column_list} FROM events WHERE event_id = ? ORDER BY veh_id, veh_time"
            count_query = "SELECT COUNT(*) FROM events WHERE event_id = ?"
            query_params = (event_id,)
        else:
            query = f"SELECT {column_list} FROM events ORDER BY event_id, veh_id, veh_time"
            count_query = "SELECT COUNT(*) FROM events"
            query_params = ()
        
        # Get total record count
        cursor = self.db_connection.cursor()
        cursor.execute(count_query, query_params)
        self._total_records = cursor.fetchone()[0]
        logger.debug(f"Total records to export: {self._total_records}")
        
        if self._total_records == 0:
            raise ValueError("No records found to export.")
        
        # Execute the main query and store cursor
        self._cursor = self.db_connection.cursor()
        self._cursor.execute(query, query_params)
        self._records_processed = 0

    def _produce_data_batch(self):
        """Produce a batch of data and put it in the queue (called by timer)."""
        try:
            # Check if worker thread is still running
            if not self._worker_thread or not self._worker_thread.isRunning():
                self._stop_producer()
                return
            
            # Check if queue is full (non-blocking check)
            if self._data_queue.full():
                return  # Skip this cycle, try again later
            
            # Fetch batch of records
            rows = self._cursor.fetchmany(self._batch_size)
            
            if not rows:
                # No more data - send end-of-data marker and stop producer
                logger.debug("No more data to produce, sending end marker")
                self._data_queue.put(None)  # End-of-data marker
                self._stop_producer()
                return
            
            # Calculate progress
            self._records_processed += len(rows)
            progress = int((self._records_processed / self._total_records) * 100)
            
            # Put batch data and progress in queue
            data_item = (rows, progress)
            self._data_queue.put(data_item)
            
            logger.debug(f"Produced batch: {len(rows)} records, total: {self._records_processed}/{self._total_records}")
            
        except Exception as e:
            logger.exception(f"Error in producer: {e}")
            self._data_queue.put(None)  # Signal end of data
            self._stop_producer()

    def _stop_producer(self):
        """Stop the producer timer."""
        if self._producer_timer:
            self._producer_timer.stop()
            self._producer_timer = None
            logger.debug("Producer stopped")

    def _interrupt_export(self):
        """Handle the Interrupt button click."""
        logger.debug("Export interrupt requested")
        
        # Stop producer
        self._stop_producer()
        
        # Signal worker thread to stop
        if self._worker_thread:
            self._worker_thread.interrupt_flag = True
        
        self._interrupt_button.setEnabled(False)

    def _on_export_finished(self, success: bool, message: str):
        """Handle export completion.
        
        Args:
            success (bool): Whether the export was successful.
            message (str): Status message.
        """
        logger.debug(f"Export finished: success={success}, message={message}")
        
        # Stop producer if still running
        self._stop_producer()
        
        # Reset progress bar
        self._progress_bar.setValue(0)
        
        # Re-enable controls
        self._export_button.setEnabled(True)
        self._interrupt_button.setEnabled(False)
        
        # Clean up thread
        if self._worker_thread:
            self._worker_thread.wait()
            self._worker_thread.deleteLater()
            self._worker_thread = None
        
        # Clean up cursor
        if self._cursor:
            self._cursor.close()
            self._cursor = None
        
        # Clean up queue
        self._data_queue = None
        
        # Show result message
        if success:
            QMessageBox.information(self, "Export Complete", message)
        else:
            QMessageBox.warning(self, "Export Failed", message)

    def closeEvent(self, event):
        """Handle dialog close event.
        
        Args:
            event: The close event.
        """
        logger.debug("Export dialog close event triggered")
        
        # If export is running, ask user for confirmation
        if self._worker_thread and self._worker_thread.isRunning():
            logger.debug("Export is running, asking user for confirmation")
            
            reply = QMessageBox.question(
                self,
                "Export In Progress",
                "An export process is currently running. Do you want to interrupt it and close the dialog?\n\n"
                "Warning: Interrupting the export will cancel the process.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            )
            
            if reply == QMessageBox.StandardButton.Cancel:
                logger.debug("User chose to cancel dialog close")
                event.ignore()
                return
            
            # User chose Yes - interrupt the export
            logger.debug("User confirmed to interrupt export and close dialog")
            
            # Stop producer
            self._stop_producer()
            
            # Signal worker thread to stop
            self._worker_thread.interrupt_flag = True
            
            # Wait for thread to finish
            self._worker_thread.quit()
            success = self._worker_thread.wait(3000)
            if not success:
                logger.warning("Forcing export thread termination")
                self._worker_thread.terminate()
                self._worker_thread.wait()
            
            # Clean up cursor
            if self._cursor:
                self._cursor.close()
                self._cursor = None
        
        # Emit the selected columns back to the main window
        if not self.is_export_all_mode:
            selected_columns = self._get_selected_columns()
            logger.debug(f"Emitting selected columns: {selected_columns}")
            self.export_finished.emit(selected_columns)
        else:
            # For Export All mode, emit empty list to avoid clearing stored selections
            self.export_finished.emit([])
        
        event.accept()