"""Export all data dialog for Trajectory Tools.

This module implements a dialog for exporting all data from the database.
"""

import sqlite3
import csv
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QProgressBar
)


class ExportAllDialog(QDialog):
    """Dialog for exporting all data from the database."""
    
    def __init__(self, db_connection: sqlite3.Connection, parent=None):
        """Initialize the export all dialog.
        
        Args:
            db_connection: Database connection.
            parent: Parent widget.
        """
        super().__init__(parent)
        
        self._db_connection = db_connection
        
        self.setWindowTitle("Export All Data")
        self.setModal(True)
        self.setMinimumWidth(400)
        
        # Layout
        layout = QVBoxLayout(self)
        
        # Info label
        info_label = QLabel(
            "Export all events data from the database to a CSV file."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        export_button = QPushButton("Export")
        export_button.clicked.connect(self._on_export)
        button_layout.addWidget(export_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        
    def _on_export(self):
        """Handle export button click."""
        # Get output file
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export All Data",
            "trajectory_data.csv",
            "CSV Files (*.csv);;All Files (*.*)"
        )
        
        if not file_path:
            return
            
        try:
            # Show progress
            self._progress_bar.setVisible(True)
            self._progress_bar.setValue(0)
            
            # Get total count
            cursor = self._db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM events")
            total_count = cursor.fetchone()[0]
            
            # Export data
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # Write header
                columns = [
                    "event_id", "veh_id", "event_time", "lane_id",
                    "x_map_loc", "y_map_loc", "x_frenet", "y_frenet",
                    "x_map_origin", "y_map_origin", "veh_lat", "veh_lon",
                    "veh_speed", "veh_acceleration", "veh_length", "veh_width",
                    "veh_automated", "osm_way_id", "osm_speed_limit",
                    "osm_traffic_control", "preceding_veh_id", "veh_dist"
                ]
                writer.writerow(columns)
                
                # Export data in batches
                batch_size = 10000
                exported = 0
                
                cursor.execute("""
                    SELECT event_id, veh_id, event_time, lane_id,
                           x_map_loc, y_map_loc, x_frenet, y_frenet,
                           x_map_origin, y_map_origin, veh_lat, veh_lon,
                           veh_speed, veh_acceleration, veh_length, veh_width,
                           veh_automated, osm_way_id, osm_speed_limit,
                           osm_traffic_control, preceding_veh_id, veh_dist
                    FROM events
                    ORDER BY event_id, veh_id, event_time
                """)
                
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                        
                    writer.writerows(rows)
                    exported += len(rows)
                    
                    # Update progress
                    progress = int((exported / total_count) * 100)
                    self._progress_bar.setValue(progress)
                    
            # Success message
            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {exported} records to:\n{file_path}"
            )
            
            self.accept()
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Error",
                f"Error exporting data: {str(e)}"
            )