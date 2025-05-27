from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QCheckBox, QFileDialog, QMessageBox, QGridLayout
import csv
import os


class ExportDialog(QDialog):
    """Dialog for exporting database records to CSV.

    Allows selection of an event and columns to export.
    """

    def __init__(self, parent=None, db_conn=None):
        """Initialize the export dialog.

        Args:
            parent: Parent widget, typically the main window.
            db_conn: SQLite database connection.
        """
        super().__init__(parent)
        self.setWindowTitle("Export Data")
        self.db_conn = db_conn
        self.check_states = [False] * 21
        self.setup_ui()
        self.populate_events()
        self.setFixedSize(self.sizeHint())
        if parent:
            parent_geo = parent.geometry()
            self.move(parent_geo.center() - self.rect().center())

    def setup_ui(self):
        """Setup the dialog UI."""
        layout = QVBoxLayout()
        self.event_combo = QComboBox()
        layout.addWidget(self.event_combo)

        columns = ["event", "id", "time", "lane_id", "x_map_loc", "y_map_loc", "x_frenet",
                   "y_frenet", "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
                   "speed", "acceleration", "length", "automated", "way_id", "preceding",
                   "speed_limit", "width", "traffic_control"]
        grid_layout = QGridLayout()
        self.checkboxes = []
        for i, col in enumerate(columns):
            checkbox = QCheckBox(col)
            checkbox.setChecked(self.check_states[i])
            checkbox.stateChanged.connect(lambda state, idx=i: self.check_states.__setitem__(idx, bool(state)))
            grid_layout.addWidget(checkbox, i % 7, i // 7)
            self.checkboxes.append(checkbox)
        layout.addLayout(grid_layout)

        button_layout = QHBoxLayout()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_file)
        button_layout.addWidget(save_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def populate_events(self):
        """Populate the event combobox with distinct events."""
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT DISTINCT event FROM trajectories ORDER BY event")
        events = [str(row[0]) for row in cursor.fetchall()]
        self.event_combo.addItems(events)

    def save_file(self):
        """Save selected data to a CSV file."""
        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV File", "", "CSV Files (*.csv)")
        if not file_path:
            return

        if os.path.exists(file_path) and not os.access(file_path, os.W_OK):
            QMessageBox.critical(self, "Error", "Destination is not writable")
            return
        if os.path.exists(file_path):
            reply = QMessageBox.question(self, "Overwrite File",
                                       f"File {file_path} already exists. Overwrite?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No:
                return

        selected_columns = [cb.text() for i, cb in enumerate(self.checkboxes) if self.check_states[i]]
        if not selected_columns:
            QMessageBox.warning(self, "Warning", "No columns selected for export")
            return

        cursor = self.db_conn.cursor()
        query = f"SELECT {', '.join(selected_columns)} FROM trajectories WHERE event = ? ORDER BY time"
        cursor.execute(query, (self.event_combo.currentText(),))
        rows = cursor.fetchall()

        try:
            with open(file_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter=',')
                writer.writerow(selected_columns)
                writer.writerows(rows)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error writing CSV file: {str(e)}")