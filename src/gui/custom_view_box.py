"""
Custom ViewBox for TimeSpacePlotWidget with selection and context menu functionality.

This module provides a CustomViewBox class that extends pyqtgraph's ViewBox to add:
- Shift+drag selection box functionality
- Custom right-click context menus with dynamic registry system
- Vehicle selection and manipulation features
"""

import logging
import numpy as np
from PyQt6.QtWidgets import QMenu, QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
import pyqtgraph as pg

# Set up logger
logger = logging.getLogger(__name__)


class CustomViewBox(pg.ViewBox):
    """Custom ViewBox that handles selection box drawing when Shift is held and custom context menus.
    
    This class extends pyqtgraph's ViewBox to provide:
    - Shift+drag selection box for selecting multiple vehicles
    - Right-click context menus with dynamic content based on current state
    - Integration with parent TimeSpacePlotWidget for vehicle selection management
    
    Attributes:
        parent_widget: Reference to the parent TimeSpacePlotWidget
        _selection_start (QPointF): Starting point of selection box in view coordinates
        _is_selecting (bool): Flag indicating if selection box is active
        _selection_box (pg.RectROI): Selection box item for group selection
        _context_menu_registry (list): Registry of context menu items
        _current_context_vehicle (int): Vehicle ID for series-specific context menu actions
    """
    
    def __init__(self, parent_widget, *args, **kwargs):
        """Initialize the CustomViewBox.
        
        Args:
            parent_widget: Reference to the parent TimeSpacePlotWidget
            *args: Additional positional arguments passed to parent ViewBox
            **kwargs: Additional keyword arguments passed to parent ViewBox
        """
        super().__init__(*args, **kwargs)
        self.parent_widget = parent_widget
        self._selection_start = None
        self._is_selecting = False
        self._selection_box = None
        self._current_context_vehicle = None
        
        # Context menu registry - list of dictionaries with keys: filter, label, callback
        # Filter string contains combinations of: A (all), S (selected), O (series)
        self._context_menu_registry = [
            {'filter': 'SO', 'label': 'Speed-Time Diagram', 'callback': self._do_nothing},
            {'filter': 'SO', 'label': 'Accel-Time Diagram', 'callback': self._do_nothing},
            {'filter': 'ASO', 'label': 'Export...', 'callback': self._do_nothing},
            {'filter': 'ASO', 'label': 'Statistics', 'callback': self._generate_statistics}
        ]

    def mousePressEvent(self, ev):
        """Handle mouse press events for selection box creation.
        
        Args:
            ev: Mouse event containing button and modifier information
        """
        if ev.button() == Qt.MouseButton.LeftButton and ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Start selection
            self._selection_start = self.mapSceneToView(ev.scenePos())
            self._is_selecting = True
            
            # Create selection box
            self._selection_box = pg.RectROI(
                pos=[self._selection_start.x(), self._selection_start.y()],
                size=[0, 0],
                pen=pg.mkPen(color='red', width=2, style=Qt.PenStyle.DashLine),
                removable=False
            )
            
            # Disable handles
            for handle in self._selection_box.getHandles():
                handle.hide()
            
            # Add to parent plot
            self.parent_widget._scatter_plot.addItem(self._selection_box)
            ev.accept()
            return
        
        # For non-shift clicks, use default behavior
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        """Handle mouse move events for selection box resizing.

        Args:
            ev: Mouse event containing position information
        """
        if self._is_selecting and self._selection_box:
            # Update selection box
            current_pos = self.mapSceneToView(ev.scenePos())

            x0, y0 = self._selection_start.x(), self._selection_start.y()
            x1, y1 = current_pos.x(), current_pos.y()

            # Update selection box position and size
            self._selection_box.setPos(min(x0, x1), min(y0, y1))
            self._selection_box.setSize([abs(x1 - x0), abs(y1 - y0)])
            ev.accept()
            # Still call super to ensure proper event propagation
            super().mouseMoveEvent(ev)
            return

        # For non-selection moves, use default behavior
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        """Handle mouse release events for selection box completion.
        
        Args:
            ev: Mouse event containing button and position information
        """
        if ev.button() == Qt.MouseButton.LeftButton and self._is_selecting:
            # Complete selection
            end_pos = self.mapSceneToView(ev.scenePos())
            
            x0, y0 = self._selection_start.x(), self._selection_start.y()
            x1, y1 = end_pos.x(), end_pos.y()
            
            # Get selection box bounds
            x_min, x_max = min(x0, x1), max(x0, x1)
            y_min, y_max = min(y0, y1), max(y0, y1)
            
            # Find vehicles with points in the selection box
            selected_vehicles = set()
            for series in self.parent_widget._series.values():
                if series['veh_time'] is not None and series['veh_dist_trav'] is not None:
                    mask = (series['veh_time'] >= x_min) & (series['veh_time'] <= x_max) & \
                           (series['veh_dist_trav'] >= y_min) & (series['veh_dist_trav'] <= y_max)
                    if np.any(mask):
                        selected_vehicles.add(series['veh_id'])
            
            # Update selected_series
            self.parent_widget._selected_series.update(selected_vehicles)
            # Clear selected_series if all vehicles are selected
            if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
                self.parent_widget._selected_series.clear()
            
            # Update plot colors
            self.parent_widget._update_plot_colors()
            
            # Clear selection box
            self._clear_selection_box()
            ev.accept()
            return
        
        # For non-shift releases, use default behavior
        super().mouseReleaseEvent(ev)
    
    def mouseClickEvent(self, ev):
        """Override to handle right-click context menu.
        
        Args:
            ev: Mouse event containing button information
        """
        if ev.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(ev)
            ev.accept()
            return
        
        # For other clicks, use default behavior
        super().mouseClickEvent(ev)
    
    def _show_context_menu(self, ev):
        """Show custom context menu based on click location and current state.
        
        Args:
            ev: Mouse event containing position information
        """
        if not self.parent_widget._series:
            return
            
        # Get click position
        pos = ev.scenePos()
        mouse_point = self.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()
        
        # Check if we clicked on a plot item
        clicked_vehicle_id = self._find_clicked_vehicle(mx, my)
        
        # Determine context menu type and title
        if clicked_vehicle_id is not None:
            menu_type = 'O'  # series (O for "One vehicle")
            title = 'Series'
            self._current_context_vehicle = clicked_vehicle_id
        elif len(self.parent_widget._selected_series) > 0:
            menu_type = 'S'  # selected
            title = 'Selected'
            self._current_context_vehicle = None
        else:
            menu_type = 'A'  # all
            title = 'All'
            self._current_context_vehicle = None
        
        # Create and populate context menu
        context_menu = QMenu(self.parent_widget)
        
        # Add disabled pseudo-title
        title_action = QAction(title, context_menu)
        title_action.setEnabled(False)
        context_menu.addAction(title_action)
        context_menu.addSeparator()
        
        # Add menu items based on registry and current context
        for item in self._context_menu_registry:
            if menu_type in item['filter']:
                action = QAction(item['label'], context_menu)
                action.triggered.connect(lambda checked, callback=item['callback']: callback())
                context_menu.addAction(action)
        
        # Show context menu at cursor position
        global_pos = self.parent_widget._scatter_plot.mapToGlobal(
            self.parent_widget._scatter_plot.mapFromScene(pos)
        )
        context_menu.exec(global_pos)
    
    def _find_clicked_vehicle(self, mx, my):
        """Find vehicle ID at the given coordinates, if any.
        
        Args:
            mx (float): Mouse x coordinate in view space
            my (float): Mouse y coordinate in view space
            
        Returns:
            int or None: Vehicle ID if found, None otherwise
        """
        time_tolerance = 0.05  # Time tolerance in seconds
        pixel_tolerance = 10   # Pixel distance to register click
        
        min_dist = float('inf')
        closest_veh_id = None
        
        for series in self.parent_widget._series.values():
            if series["plot_item"] and len(series["veh_time"]) > 0:
                mask = (series["veh_time"] >= mx - time_tolerance) & (series["veh_time"] <= mx + time_tolerance)
                x_filtered = series["veh_time"][mask]
                y_filtered = series["veh_dist_trav"][mask]
                
                if len(x_filtered) > 0:
                    distances = np.abs(y_filtered - my)
                    min_idx = np.argmin(distances)
                    if distances[min_idx] < pixel_tolerance and distances[min_idx] < min_dist:
                        min_dist = distances[min_idx]
                        closest_veh_id = series["veh_id"]
        
        return closest_veh_id
    
    def _clear_selection_box(self):
        """Remove the selection box from the plot."""
        if self._selection_box:
            self.parent_widget._scatter_plot.removeItem(self._selection_box)
            self._selection_box = None
            self._is_selecting = False
            self._selection_start = None

    # Context menu callback methods
    def _do_nothing(self):
        pass

    def _select_all_vehicles(self):
        """Select all vehicles."""
        self.parent_widget._selected_series = set(self.parent_widget._series.keys())
        if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
            self.parent_widget._selected_series.clear()
        self.parent_widget._update_plot_colors()
        logger.debug("Selected all vehicles")
    
    def _clear_selection(self):
        """Clear vehicle selection."""
        self.parent_widget._selected_series.clear()
        self.parent_widget._update_plot_colors()
        logger.debug("Cleared vehicle selection")
    
    def _invert_selection(self):
        """Invert current vehicle selection."""
        all_vehicles = set(self.parent_widget._series.keys())
        self.parent_widget._selected_series = all_vehicles - self.parent_widget._selected_series
        if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
            self.parent_widget._selected_series.clear()
        self.parent_widget._update_plot_colors()
        logger.debug("Inverted vehicle selection")
    
    def _export_data(self):
        """Export data for vehicles."""
        if not self.parent_widget._selected_series:
            logger.warning("No vehicles selected for export")
            return
        
        # Placeholder for export functionality
        selected_count = len(self.parent_widget._selected_series)
        logger.info(f"Export requested for {selected_count} selected vehicles")
        # TODO: Implement actual export functionality
    
    def _select_vehicle_only(self):
        """Select only the clicked vehicle."""
        if self._current_context_vehicle is not None:
            self.parent_widget._selected_series = {self._current_context_vehicle}
            self.parent_widget._update_plot_colors()
            logger.debug(f"Selected only vehicle {self._current_context_vehicle}")
    
    def _toggle_vehicle_selection(self):
        """Toggle selection of the clicked vehicle."""
        if self._current_context_vehicle is not None:
            veh_id = self._current_context_vehicle
            if veh_id in self.parent_widget._selected_series:
                self.parent_widget._selected_series.remove(veh_id)
            else:
                self.parent_widget._selected_series.add(veh_id)
                # Clear selection if all vehicles are selected
                if len(self.parent_widget._selected_series) == len(self.parent_widget._series):
                    self.parent_widget._selected_series.clear()
            self.parent_widget._update_plot_colors()
            logger.debug(f"Toggled selection for vehicle {veh_id}")
    
    def _hide_vehicle(self):
        """Hide the clicked vehicle (placeholder functionality)."""
        if self._current_context_vehicle is not None:
            veh_id = self._current_context_vehicle
            logger.info(f"Hide vehicle {veh_id} requested")
            # TODO: Implement vehicle hiding functionality
    
    def _reset_view(self):
        """Reset the plot view to show all data."""
        self.autoRange()
        logger.debug("Reset plot view")
    
    def _generate_statistics(self):
        """Generate and display statistics for vehicles based on current context."""
        if not self.parent_widget._db_connection or self.parent_widget._current_event_id is None:
            logger.warning("No database connection or event ID available for statistics")
            return
        
        # Determine which vehicle IDs to include based on context
        target_veh_ids = self._get_target_vehicle_ids()
        if not target_veh_ids:
            logger.warning("No vehicles available for statistics")
            return
        
        # Determine the mode for the dialog title
        mode = self._get_statistics_mode()
        
        # Create and show statistics dialog
        dialog = StatisticsDialog(self.parent_widget, target_veh_ids, 
                                self.parent_widget._db_connection, 
                                self.parent_widget._current_event_id,
                                mode)
        dialog.exec()
    
    def _get_target_vehicle_ids(self):
        """Get the list of vehicle IDs based on current context.
        
        Returns:
            list: List of vehicle IDs to include in statistics
        """
        if self._current_context_vehicle is not None:
            # Series context - only the clicked vehicle
            return [self._current_context_vehicle]
        elif len(self.parent_widget._selected_series) > 0:
            # Selected context - vehicles in selected series
            return list(self.parent_widget._selected_series)
        else:
            # All context - vehicles with plot items
            return [veh_id for veh_id, series in self.parent_widget._series.items() 
                   if series['plot_item'] is not None]
    
    def _get_statistics_mode(self):
        """Get the mode string for the statistics dialog title.
        
        Returns:
            str: Mode string (All, Selected, or Series)
        """
        if self._current_context_vehicle is not None:
            return "Series"
        elif len(self.parent_widget._selected_series) > 0:
            return "Selected"
        else:
            return "All"


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
        self.resize(600, dialog_height)  # Increased initial width for more columns
        
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