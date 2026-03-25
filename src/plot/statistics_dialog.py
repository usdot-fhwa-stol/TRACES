import logging
import numpy as np
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
from PyQt6.QtCore import Qt, QTimer

# Set up logger
logger = logging.getLogger(__name__)

class StatisticsDialog(QDialog):
    """Dialog for displaying vehicle statistics in a table format."""
    
    def __init__(self, parent, veh_ids, db_connection, event_id, mode):
        """Initialize the statistics dialog.
        
        Args:
            parent: Parent widget
            veh_ids (list): List of vehicle IDs to analyze
            db_connection: Database connection
            event_id (int): Event ID to filter by
            mode (str): Mode string for the title (All, Selected, or Series)
        """
        super().__init__(parent)
        self.veh_ids = veh_ids
        self.db_connection = db_connection
        self.event_id = event_id
        self.mode = mode
        
        self.setWindowTitle(f"Vehicle Statistics ({mode})")
        self.setModal(True)
        
        # Set dialog height to 60% of parent window height
        parent_height = parent.height()
        dialog_height = int(parent_height * 0.6)
        self.resize(640, dialog_height)  # Increased initial width for more columns
        
        self._setup_ui()
        self._populate_statistics()
        
        # Use single shot to center dialog after initial render
        QTimer.singleShot(0, self._center_dialog)
    
    def _setup_ui(self):
        """Set up the dialog UI components."""
        layout = QVBoxLayout(self)
        
        # Create table widget with 9 columns
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            'veh_id', 
            'min m/s', 'med m/s', 'max m/s', 'std dev',
            'min m/s²', 'med m/s²', 'max m/s²', 'std dev',
        ])
        
        # Configure table appearance
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        layout.addWidget(self.table)
    
    def _populate_statistics(self):
        """Populate the table with vehicle statistics using streaming approach."""
        try:
            # Prepare data structures for streaming
            vehicle_data = {veh_id: {'speed': [], 'accel': []} for veh_id in self.veh_ids}
            
            # Build SQL query with veh_id IN clause for efficient filtering
            placeholders = ','.join('?' for _ in self.veh_ids)
            cursor = self.db_connection.cursor()
            cursor.execute(f"""
                SELECT veh_id, veh_time, veh_speed, veh_accel
                FROM events
                WHERE event_id = ? AND veh_id IN ({placeholders})
                ORDER BY veh_id, veh_time
            """, [self.event_id] + self.veh_ids)
            
            # Process records in streaming fashion
            for row in cursor:
                veh_id, veh_time, veh_speed, veh_accel = row
                
                # Add non-null speed values
                if veh_speed is not None:
                    vehicle_data[veh_id]['speed'].append(veh_speed)
                
                # Add non-null acceleration values
                if veh_accel is not None:
                    vehicle_data[veh_id]['accel'].append(veh_accel)
            
            # Calculate statistics and populate table
            self._calculate_and_display_statistics(vehicle_data)
            
        except Exception as e:
            logger.error(f"Error generating statistics: {e}")
    
    def _calculate_and_display_statistics(self, vehicle_data):
        """Calculate statistics and display in table.
        
        Args:
            vehicle_data (dict): Dictionary of vehicle data by veh_id
        """
        # Count vehicles with data
        vehicles_with_data = [veh_id for veh_id in self.veh_ids 
                             if vehicle_data[veh_id]['speed'] or vehicle_data[veh_id]['accel']]
        
        self.table.setRowCount(len(vehicles_with_data))
        
        row = 0
        for veh_id in sorted(vehicles_with_data):
            speed_data = vehicle_data[veh_id]['speed']
            accel_data = vehicle_data[veh_id]['accel']
            
            # Calculate statistics for speed and acceleration
            speed_stats = self._calculate_stats(speed_data) if speed_data else None
            accel_stats = self._calculate_stats(accel_data) if accel_data else None
            
            # Populate the row
            self._populate_table_row(row, veh_id, speed_stats, accel_stats)
            row += 1
    
    def _calculate_stats(self, data):
        """Calculate statistics for a data array.
        
        Args:
            data (list): List of numeric values
            
        Returns:
            dict: Dictionary with min, median, max, std statistics
        """
        if not data:
            return None
            
        np_data = np.array(data)
        return {
            'min': np.min(np_data),
            'median': np.median(np_data),
            'max': np.max(np_data),
            'std': np.std(np_data)
        }
    
    def _populate_table_row(self, row, veh_id, speed_stats, accel_stats):
        """Populate a single table row with statistics.
        
        Args:
            row (int): Row index
            veh_id (int): Vehicle ID
            speed_stats (dict or None): Speed statistics dictionary or None if no data
            accel_stats (dict or None): Acceleration statistics dictionary or None if no data
        """
        # Vehicle ID - center aligned
        veh_id_item = QTableWidgetItem(str(veh_id))
        veh_id_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, 0, veh_id_item)
        
        # Speed statistics (columns 1-4) - right aligned
        if speed_stats is not None:
            min_item = QTableWidgetItem(f"{speed_stats['min']:.2f}")
            min_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 1, min_item)
            
            med_item = QTableWidgetItem(f"{speed_stats['median']:.2f}")
            med_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 2, med_item)
            
            max_item = QTableWidgetItem(f"{speed_stats['max']:.2f}")
            max_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, max_item)
            
            std_item = QTableWidgetItem(f"{speed_stats['std']:.3f}")
            std_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 4, std_item)
        else:
            for col in range(1, 5):
                na_item = QTableWidgetItem("N/A")
                na_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, na_item)
        
        # Acceleration statistics (columns 5-8) - right aligned
        if accel_stats is not None:
            min_item = QTableWidgetItem(f"{accel_stats['min']:.2f}")
            min_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 5, min_item)
            
            med_item = QTableWidgetItem(f"{accel_stats['median']:.2f}")
            med_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 6, med_item)
            
            max_item = QTableWidgetItem(f"{accel_stats['max']:.2f}")
            max_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 7, max_item)
            
            std_item = QTableWidgetItem(f"{accel_stats['std']:.3f}")
            std_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 8, std_item)
        else:
            for col in range(5, 9):
                na_item = QTableWidgetItem("N/A")
                na_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, na_item)
    
    def _center_dialog(self):
        """Center the dialog relative to parent after initial render."""
        # Resize dialog width to fit contents
        self.table.resizeColumnsToContents()
        
        # Calculate required width based on table content
        total_width = sum(self.table.columnWidth(i) for i in range(self.table.columnCount()))
        total_width += 50  # Add padding for scrollbar and margins
        
        # Get parent geometry
        parent_rect = self.parent().geometry()
        
        # Calculate centered position
        x = parent_rect.x() + (parent_rect.width() - total_width) // 2
        y = parent_rect.y() + (parent_rect.height() - self.height()) // 2
        
        # Set final geometry
        self.setGeometry(x, y, total_width, self.height())