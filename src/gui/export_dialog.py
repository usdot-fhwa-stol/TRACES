import csv
import sqlite3
import logging
from pathlib import Path
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                           QProgressBar, QLabel, QCheckBox, QComboBox, 
                           QFileDialog, QMessageBox, QGridLayout, QWidget)
from PyQt6.QtCore import QThread, pyqtSignal

# Set up logger
logger = logging.getLogger(__name__)


class _ExportWorkerThread(QThread):
    """Worker thread for handling export operations."""

    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(self, db_connection: sqlite3.Connection, output_path: str, 
                 columns: list, event_id: int = None):
        """Initialize the export worker thread.
        
        Args:
            db_connection (sqlite3.Connection): Database connection.
            output_path (str): Path to the output CSV file.
            columns (list): List of column names to export.
            event_id (int, optional): Event ID for filtered export. None for all events.
        """
        super().__init__()
        self.db_connection = db_connection
        self.output_path = output_path
        self.columns = columns
        self.event_id = event_id
        self.interrupt_flag = False
        logger.debug(f"ExportWorkerThread initialized: {output_path}, columns: {len(columns)}, event_id: {event_id}")

    def run(self):
        """Execute the export process."""
        logger.debug("ExportWorkerThread.run() started")
        try:
            cursor = self.db_connection.cursor()
            
            # Build the SQL query
            column_list = ", ".join(self.columns)
            if self.event_id is not None:
                query = f"SELECT {column_list} FROM events WHERE event_id = ? ORDER BY veh_id, event_time"
                count_query = "SELECT COUNT(*) FROM events WHERE event_id = ?"
                query_params = (self.event_id,)
            else:
                query = f"SELECT {column_list} FROM events ORDER BY event_id, veh_id, event_time"
                count_query = "SELECT COUNT(*) FROM events"
                query_params = ()
            
            # Get total record count for progress tracking
            cursor.execute(count_query, query_params)
            total_records = cursor.fetchone()[0]
            logger.debug(f"Total records to export: {total_records}")
            
            if total_records == 0:
                self.finished_signal.emit(False, "No records found to export.")
                return
            
            # Open output file for writing
            with open(self.output_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                
                # Write header
                writer.writerow(self.columns)
                
                # Execute the main query
                cursor.execute(query, query_params)
                
                # Process records in batches
                batch_size = 1000
                records_processed = 0
                
                while True:
                    if self.interrupt_flag:
                        logger.debug("Export interrupted by user")
                        self.finished_signal.emit(False, "Export interrupted by user.")
                        return
                    
                    # Fetch batch of records
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    
                    # Write batch to CSV
                    writer.writerows(rows)
                    
                    # Update progress
                    records_processed += len(rows)
                    progress = int((records_processed / total_records) * 100)
                    self.progress_signal.emit(progress)
                    
                    logger.debug(f"Processed {records_processed}/{total_records} records")
            
            if not self.interrupt_flag:
                logger.info(f"Export completed successfully: {self.output_path}")
                self.finished_signal.emit(True, f"Successfully exported {records_processed} records to {self.output_path}")
            
        except Exception as e:
            logger.exception(f"Exception in ExportWorkerThread.run(): {e}")
            self.finished_signal.emit(False, f"Error during export: {str(e)}")


class ExportDialog(QDialog):
    """Modal dialog for exporting trajectory data to CSV files.
    
    Operates in two modes:
    1. Export All: Exports all records from the events table
    2. Export Selected: Exports selected columns for a specific event
    
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
        self._output_path = ""
        self._column_checkboxes = {}
        self._event_combobox = None
        self._export_button = None
        self._interrupt_button = None
        self._progress_bar = None
        
        logger.debug(f"ExportDialog initialized: export_all_mode={self.is_export_all_mode}")
        
        self._setup_ui()
        self._center_dialog()

    def _setup_ui(self):
        """Set up the dialog UI based on the mode."""
        # Set window title and size based on mode
        if self.is_export_all_mode:
            self.setWindowTitle("Export All")
            self.setFixedSize(400, 200)
        else:
            self.setWindowTitle("Export Selected")
            self.setFixedSize(600, 500)
        
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
            if col >= 3:  # Wrap to next row after 3 columns
                col = 0
                row += 1
        
        layout.addWidget(checkbox_widget)
        
        # Add event selection combobox with label
        event_layout = QHBoxLayout()
        event_layout.addWidget(QLabel("Event:"))
        
        self._event_combobox = QComboBox()
        self._populate_event_combobox()
        event_layout.addWidget(self._event_combobox)
        event_layout.addStretch()
        
        layout.addLayout(event_layout)

    def _setup_button_controls(self, layout: QVBoxLayout):
        """Set up the button control area.
        
        Args:
            layout (QVBoxLayout): Main layout to add button controls to.
        """
        # Create button layout
        button_layout = QHBoxLayout()
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
            cursor.execute("SELECT DISTINCT event_id FROM events ORDER BY event_id")
            event_ids = [row[0] for row in cursor.fetchall()]
            
            for event_id in event_ids:
                self._event_combobox.addItem(f"Event {event_id}", event_id)
                
        except Exception as e:
            logger.error(f"Error populating event combobox: {e}")

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
        """Start the export process in a worker thread."""
        logger.debug("Starting export process")
        
        # Disable Export button and enable Interrupt button
        self._export_button.setEnabled(False)
        self._interrupt_button.setEnabled(True)
        
        # Reset progress bar
        self._progress_bar.setValue(0)
        
        # Get export parameters
        selected_columns = self._get_selected_columns()
        event_id = self._get_selected_event_id()
        
        # Create and start worker thread
        self._worker_thread = _ExportWorkerThread(
            self.db_connection, 
            self._output_path, 
            selected_columns, 
            event_id
        )
        
        # Connect signals
        self._worker_thread.progress_signal.connect(self._progress_bar.setValue)
        self._worker_thread.finished_signal.connect(self._on_export_finished)
        
        logger.debug("Starting export worker thread")
        self._worker_thread.start()

    def _interrupt_export(self):
        """Handle the Interrupt button click."""
        logger.debug("Export interrupt requested")
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
            self._worker_thread.interrupt_flag = True
            
            # Wait for thread to finish
            self._worker_thread.quit()
            success = self._worker_thread.wait(3000)
            if not success:
                logger.warning("Forcing export thread termination")
                self._worker_thread.terminate()
                self._worker_thread.wait()
        
        # Emit the selected columns back to the main window
        if not self.is_export_all_mode:
            selected_columns = self._get_selected_columns()
            logger.debug(f"Emitting selected columns: {selected_columns}")
            self.export_finished.emit(selected_columns)
        else:
            self.export_finished.emit([])
        
        event.accept()