"""Export selected data dialog for Trajectory Tools.

This module implements a dialog for exporting selected/filtered data from the database.
"""

import sqlite3
import csv
from typing import Optional, Dict, Any
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QProgressBar, QCheckBox
)


class ExportSelectDialog(QDialog):
    """Dialog for exporting selected/filtered data from the database."""
    
    def __init__(self, db_connection: sqlite3.Connection, 
                 selected_data: Optional[Dict[str, Any]], 
                 parent=None):
        """Initialize the export select dialog.
        
        Args:
            db_connection: Database connection.
            selected_data: Dictionary with selection criteria.
            parent: Parent widget.
        """
        super().__init__(parent)
        
        self._db_connection = db_connection
        self._selected_data = selected_data
        
        self.setWindowTitle("Export Selected Data")
        self.setModal(True)
        self.setMinimumWidth(400)
        
        # Layout
        layout = QVBoxLayout(self)
        
        # Info label
        if selected_data:
            info_text = (
                f"Export filtered data:\n"
                f"Event ID: {selected_data['event_id']}\n"
                f"Lane ID: {selected_data['lane_id']}\n"
                f"Time Range: {selected_data['time_range'][0]:.1f} - {selected_data['time_range'][1]:.1f} s\n"
                f"Distance Range: {selected_data['dist_range'][0]:.1f} - {selected_data['dist_range'][1]:.1f} m"
            )
            
            if selected_data['selected_vehicles']:
                info_text += f"\nSelected Vehicles: {len(selected_data['selected_vehicles'])} vehicles"
        else:
            info_text = "No data selected for export."
            
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Options
        self._export_all_checkbox = QCheckBox("Export all vehicles (ignore selection)")
        self._export_all_checkbox.setChecked(False)
        layout.addWidget(self._export_all_checkbox)
        
        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        export_button = QPushButton("Export")
        export_button.clicked.connect(self._on_export)
        export_button.setEnabled(selected_data is not None)
        button_layout.addWidget(export_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        
    def _on_export(self):
        """Handle export button click."""
        if not self._selected_data:
            return
            
        # Get output file
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Selected Data",
            "trajectory_data_filtered.csv",
            "CSV Files (*.csv);;All Files (*.*)"
        )
        
        if not file_path:
            return
            
        try:
            # Show progress
            self._progress_bar.setVisible(True)
            self._progress_bar.setValue(0)
            
            # Build query
            query = """
                SELECT event_id, veh_id, event_time, lane_id,
                       x_map_loc, y_map_loc, x_frenet, y_frenet,
                       x_map_origin, y_map_origin, veh_lat, veh_lon,
                       veh_speed, veh_acceleration, veh_length, veh_width,
                       veh_automated, osm_way_id, osm_speed_limit,
                       osm_traffic_control, preceding_veh_id, veh_dist
                FROM events
                WHERE event_id = ?
                  AND lane_id = ?
                  AND event_time >= ?
                  AND event_time <= ?
                  AND veh_dist >= ?
                  AND veh_dist <= ?
            """
            
            params = [
                self._selected_data['event_id'],
                self._selected_data['lane_id'],
                self._selected_data['time_range'][0],
                self._selected_data['time_range'][1],
                self._selected_data['dist_range'][0],
                self._selected_data['dist_range'][1]
            ]
            
            # Add vehicle filter if needed
            if (self._selected_data['selected_vehicles'] and 
                not self._export_all_checkbox.isChecked()):
                placeholders = ','.join(['?'] * len(self._selected_data['selected_vehicles']))
                query += f" AND veh_id IN ({placeholders})"
                params.extend(self._selected_data['selected_vehicles'])
                
            query += " ORDER BY event_id, veh_id, event_time"
            
            # Get total count
            count_query = query.replace(
                "SELECT event_id,", 
                "SELECT COUNT(*) FROM (SELECT event_id,"
            ) + ") AS subquery"
            
            cursor = self._db_connection.cursor()
            cursor.execute(count_query, params)
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
                
                cursor.execute(query, params)
                
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                        
                    writer.writerows(rows)
                    exported += len(rows)
                    
                    # Update progress
                    if total_count > 0:
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