"""
Base Time Plot Dialog and specialized classes for speed and acceleration plots.

This module provides a base TimePlotDialog class and specialized subclasses
for displaying time-speed and time-acceleration plots with interactive tooltips.
"""

import logging
import math
import time
import numpy as np
import sqlite3
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QVBoxLayout, QLabel
import pyqtgraph as pg
from superqt import QLabeledRangeSlider, QLabeledDoubleRangeSlider

# Set up logger
logger = logging.getLogger(__name__)


class TimeSpeedDialog(QDialog):
    """Base dialog class for displaying time-based plots for multiple vehicles.
    
    This dialog creates a plot showing vehicle data over time
    with interactive tooltips that display detailed vehicle information.
    
    Attributes:
        db_connection (sqlite3.Connection): Database connection for querying.
        event_id (int): Event ID to filter data.
        veh_ids (list): List of vehicle IDs to display.
        series_colors (list): List of HTML hex colors for each series.
        text_colors (list): List of text colors for tooltips.
        _plot (pg.PlotWidget): PyQtGraph plot widget.
        _tooltip (QLabel): Tooltip for displaying point information.
        _last_queried_point (tuple): Tracks tooltip point to reduce queries.
        _last_mouse_move_time (float): Timestamp of last mouse move for debouncing.
        _vehicle_data (dict): Dictionary storing plot data for each vehicle.
    """
    
    # Subclasses should override these class attributes
    Y_COLUMN = "veh_speed"
    Y_LABEL = "Speed (m/s)"
    PLOT_TITLE_SUFFIX = "Speed vs Time"
    TOLERANCE = 2.0  # Tolerance for point detection
    
    def __init__(self, parent, db_connection, event_id, veh_ids, lane_ids, time_min, time_max, series_colors, text_colors):
        """Initialize the TimeSpeedDialog.
        
        Args:
            parent: Parent widget
            db_connection (sqlite3.Connection): Database connection
            event_id (int): Event ID to filter by
            veh_ids (list): List of vehicle IDs to display
            series_colors (list): List of HTML hex colors for each series
            text_colors (list): List of text colors for tooltips
        """
        super().__init__(parent)
        self.db_connection = db_connection
        self.event_id = event_id
        self.veh_ids = veh_ids
        self.series_colors = series_colors
        self.text_colors = text_colors
        self._last_queried_point = None
        self._last_mouse_move_time = 0.0
        self._vehicle_data = {}
        
        # Create title based on number of vehicles
        if len(veh_ids) == 1:
            title = f"{self.PLOT_TITLE_SUFFIX} - Vehicle {veh_ids[0]}"
        else:
            veh_list = ', '.join(str(v) for v in veh_ids)
            title = f"{self.PLOT_TITLE_SUFFIX} - Vehicles {veh_list}"
        self.setWindowTitle(title)
        self.setModal(True)  # Do not allow interaction with parent window
        
        # Size dialog to 80% of parent size and center it
        self._size_and_center_dialog()
        
        self._setup_ui(lane_ids, time_min, time_max)
        self._load_and_plot_data()
    
    def _size_and_center_dialog(self):
        """Size the dialog to 80% of parent size and center it within the parent."""
        parent = self.parent().parent()
        if parent:
            parent_geom = parent.frameGeometry()
            dialog_width = int(parent_geom.width() * 0.8)
            dialog_height = int(parent_geom.height() * 0.8)
    
            # Resize dialog
            self.resize(dialog_width, dialog_height)
    
            # Center the dialog over the parent
            parent_center = parent_geom.center()
            self_geom = self.frameGeometry()
            self_geom.moveCenter(parent_center)
            self.move(self_geom.topLeft())
        else:
            # Fallback
            self.resize(900, 600)

    
    def _setup_ui(self, lane_ids, time_min, time_max):
        """Set up the dialog UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)
        
        # Left side: QVBoxLayout with plot on top and time slider below
        left_layout = QVBoxLayout()
        
        # Create plot
        self._plot = pg.PlotWidget()
        self._plot.setBackground('w')
        self._plot.showGrid(x=True, y=True)
        self._plot.setLabel('left', self.Y_LABEL)
        self._plot.setLabel('bottom', 'Time (s)')
        
        # Connect mouse move event for tooltips
        self._plot.scene().sigMouseMoved.connect(self._handle_mouse_moved)
                
        # Disable auto-range button
        self._plot.getPlotItem().hideButtons()
        self._plot.getViewBox().setMenuEnabled(False)
        
        # Disable default mouse interactions
        self._plot.getViewBox().setMouseEnabled(x=False, y=False)

        left_layout.addWidget(self._plot, stretch=1)
        
        # Time controls below plot
        time_layout = QHBoxLayout()
        
        # Time label
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_layout.addWidget(time_label)
        
        # Time slider
        self._time_slider = QLabeledRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Horizontal)
        time_layout.addWidget(self._time_slider, stretch=1)
        
        # Set time slider range and values
        self._time_slider.setRange(time_min, time_max)
        self._time_slider.setValue((time_min, time_max))
        left_layout.addLayout(time_layout)
        main_layout.addLayout(left_layout, stretch=1)
        
        # Right side: control set (rearranged)
        control_layout = QVBoxLayout()
        
        # y-axis slider section
        y_label = QLabel(self.Y_LABEL)
        y_label.setStyleSheet("font-weight: bold;")
        y_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_layout.addWidget(y_label)

        self._y_slider = QLabeledDoubleRangeSlider()
        self._y_slider.setOrientation(Qt.Orientation.Vertical)
        self._y_slider.setDecimals(1)
        control_layout.addWidget(self._y_slider, stretch=1)
        
        # Lane combobox next
        self._lane_combobox = QComboBox()
        control_layout.addWidget(self._lane_combobox)

        main_layout.addLayout(control_layout)
        
        # connect slider signals after initializaton
        self._time_slider.valueChanged.connect(self._on_slider_changed)
        self._y_slider.valueChanged.connect(self._on_slider_changed)

        # Create tooltip
        self._tooltip = QLabel(self._plot)
        self._tooltip.setStyleSheet("QLabel { padding: 5px; }")
        self._tooltip.hide()

        # Populate combobox with lane_ids
        self._lane_combobox.addItem("All Lanes", -1)
        for lane_id in lane_ids:
            self._lane_combobox.addItem(f"Lane {lane_id:2d}", lane_id)

        self._lane_combobox.currentIndexChanged.connect(self._on_lane_changed)
    
    def _get_data_query(self):
        """Get the SQL query for loading plot data.
        
        Returns:
            str: SQL query string
        """
        all_lane_ids = []
        selected_lane = self._lane_combobox.currentData()
        if selected_lane < 0: # gather all lane ids from the combo box
            for i in range(1, self._lane_combobox.count()): # skip All lanes value
                all_lane_ids.append(self._lane_combobox.itemData(i))
        else:
            all_lane_ids.append(selected_lane)

        veh_ids = ','.join(str(x) for x in self.veh_ids)
        lane_ids = ','.join(str(x) for x in all_lane_ids)
        return f"""
            SELECT veh_id, veh_time, {self.Y_COLUMN}
            FROM events
            WHERE event_id = ? 
            AND veh_id IN ({veh_ids}) 
            AND lane_id IN ({lane_ids}) 
            AND {self.Y_COLUMN} IS NOT NULL
            ORDER BY veh_id, veh_time
        """
    
    def _get_tooltip_query(self):
        """Get the SQL query for tooltip data.
        
        Returns:
            str: SQL query string
        """
        return f"""
            SELECT veh_id, veh_time, lane_id, {self.Y_COLUMN}, veh_dist_trav, veh_speed, veh_accel, veh_automation, veh_length
            FROM events
            WHERE event_id = ? AND veh_id = ? 
            AND veh_time BETWEEN ? AND ?
            AND {self.Y_COLUMN} IS NOT NULL
            ORDER BY ABS(veh_time - ?) + ABS({self.Y_COLUMN} - ?)
            LIMIT 1
        """
    
    def _load_and_plot_data(self, reset_view=True):
        """Load data from database and create plots for vehicles."""
        try:
            # Clear previous plots and state
            self._plot.clear()
            self._vehicle_data.clear()

            cursor = self.db_connection.cursor()
            cursor.execute(self._get_data_query(), [self.event_id])
            
            # Process data by vehicle using streaming approach
            current_veh_id = None
            current_times = []
            current_y_values = []
            
            def plot_vehicle_data(veh_id, times, y_values):
                """Helper function to plot data for a single vehicle."""
                if not times:
                    return
                    
                # Find vehicle index for colors
                try:
                    veh_index = self.veh_ids.index(veh_id)
                    color = self.series_colors[veh_index]
                except (ValueError, IndexError):
                    color = '#000000'  # Default black
                
                # Convert to numpy arrays
                times_array = np.array(times, dtype=np.float64)
                y_array = np.array(y_values, dtype=np.float64)
                
                # Store data for tooltip queries
                self._vehicle_data[veh_id] = {
                    'times': times_array,
                    'y_values': y_array,
                    'color': color
                }
                
                # Create line plot connecting the points
                line_item = pg.PlotDataItem(
                    x=times_array,
                    y=y_array,
                    pen=pg.mkPen(color=color, width=4),
                    symbol=None
                )
                line_item.veh_id = veh_id  # Store vehicle ID on plot item
                self._plot.addItem(line_item)

                logger.debug(f"Plotted {len(times)} {self.Y_COLUMN} points for vehicle {veh_id}")
            
            # Stream through database results
            for row in cursor:
                veh_id, veh_time, y_value = row
                
                # Check if we've moved to a new vehicle
                if veh_id != current_veh_id:
                    # Plot previous vehicle if we have data
                    if current_veh_id is not None and current_times:
                        plot_vehicle_data(current_veh_id, current_times, current_y_values)
                    
                    # Start collecting data for new vehicle
                    current_veh_id = veh_id
                    current_times = []
                    current_y_values = []
                
                # Add current record to collections
                current_times.append(veh_time)
                current_y_values.append(y_value)
            
            # Handle the last vehicle after loop ends
            if current_veh_id is not None and current_times:
                plot_vehicle_data(current_veh_id, current_times, current_y_values)
            
            # Initialize y-axis slider range based on loaded data
            if reset_view and self._vehicle_data:
                all_y_values = []
                for data in self._vehicle_data.values():
                    all_y_values.extend(data['y_values'])
                
                if all_y_values:
                    y_min = math.floor(float(np.min(all_y_values)))
                    y_max = math.ceil(float(np.max(all_y_values)))
                    self._y_slider.setRange(y_min, y_max)
                    self._y_slider.setValue((y_min, y_max))
            
            logger.debug(f"Loaded {self.Y_COLUMN} data for {len(self._vehicle_data)} vehicles")
            
        except sqlite3.Error as e:
            logger.error(f"Error loading {self.Y_COLUMN} data: {e}")
            
    def _on_slider_changed(self):
        """Handle changes to sliders by updating the plot view range."""
        # Get current slider ranges
        time_min, time_max = self._time_slider.value()
        y_min, y_max = self._y_slider.value()
        
        # Update plot view to match slider ranges
        self._plot.getViewBox().setRange(
            xRange=[time_min, time_max],
            yRange=[y_min, y_max],
            padding=0
        )
        
        logger.debug(f"Applied slider filters: time=[{time_min}, {time_max}], y=[{y_min}, {y_max}]")
    
    def _handle_mouse_moved(self, pos):
        """Handle mouse movement for showing tooltips with hysteresis.
        
        Args:
            pos (QPointF): Mouse position in scene coordinates.
        """
        # Debounce to limit processing frequency
        current_time = time.time()
        if (current_time - self._last_mouse_move_time) < 0.05:  # 50ms debounce
            return
        self._last_mouse_move_time = current_time
        
        view_box = self._plot.getViewBox()
        mouse_point = view_box.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()
        
        # Find closest point across all vehicles
        min_dist = float('inf')
        closest_vehicle = None
        closest_data = None
        time_tolerance = 0.05  # Time tolerance in seconds
        
        for veh_id, data in self._vehicle_data.items():
            times = data['times']
            y_values = data['y_values']
            
            # Filter by time tolerance first
            time_mask = (times >= mx - time_tolerance) & (times <= mx + time_tolerance)
            filtered_times = times[time_mask]
            filtered_y_values = y_values[time_mask]
            
            if len(filtered_times) > 0:
                # Find closest point in y-direction
                y_distances = np.abs(filtered_y_values - my)
                min_idx = np.argmin(y_distances)
                
                if y_distances[min_idx] < self.TOLERANCE and y_distances[min_idx] < min_dist:
                    min_dist = y_distances[min_idx]
                    closest_vehicle = veh_id
                    closest_data = (filtered_times[min_idx], filtered_y_values[min_idx])
        
        if closest_vehicle is not None and closest_data is not None:
            veh_time, y_value = closest_data
            
            # Check if this is the same point as last queried
            current_point = (closest_vehicle, veh_time, self.event_id)
            if self._last_queried_point != current_point:
                # Query database for complete vehicle data at this point
                try:
                    cursor = self.db_connection.cursor()
                    cursor.execute(self._get_tooltip_query(), 
                                  (self.event_id, closest_vehicle, 
                                   veh_time - time_tolerance, veh_time + time_tolerance,
                                   veh_time, y_value))
                    
                    row = cursor.fetchone()
                    
                    if row:
                        # Extract data
                        veh_id = row[0]
                        veh_time = row[1]
                        lane_id = row[2]
                        y_value = row[3]
                        veh_dist_trav = row[4] if len(row) > 4 else None
                        veh_speed = row[5] if len(row) > 5 else None
                        veh_accel = row[6] if len(row) > 6 else None
                        veh_automation = row[7] if len(row) > 7 else None
                        veh_length = row[8] if len(row) > 8 else None
                        
                        # Generate tooltip content
                        tooltip_text = self._generate_tooltip_content(
                            veh_id, veh_time, lane_id, veh_dist_trav, veh_speed, 
                            veh_accel, veh_automation, veh_length
                        )
                        self._tooltip.setText(tooltip_text)
                        self._last_queried_point = current_point
                    else:
                        self._tooltip.hide()
                        self._last_queried_point = None
                        return
                        
                except sqlite3.Error as e:
                    logger.error(f"Error querying tooltip data: {e}")
                    self._tooltip.hide()
                    self._last_queried_point = None
                    return
            
            # Get colors for this vehicle
#            try:
#                veh_index = self.veh_ids.index(closest_vehicle)
#                series_color = self.series_colors[veh_index]
#                text_color = self.text_colors[veh_index]
#            except (ValueError, IndexError):
            series_color = '#000000'
            text_color = '#ffffff'
            
            # Update tooltip style and position
            self._tooltip.setStyleSheet(
                f"QLabel {{ background-color: {series_color}; "
                f"color: {text_color}; padding: 5px; }}"
            )
            self._tooltip.move(int(pos.x()) + 10, int(pos.y()) + 10)
            self._tooltip.show()
        else:
            # Hide tooltip if no point found or outside tolerance
            self._tooltip.hide()
            self._last_queried_point = None
    
    @staticmethod
    def _generate_tooltip_content(veh_id, veh_time, lane_id, veh_dist_trav, veh_speed, 
                                 veh_accel, veh_automation, veh_length):
        """Generate tooltip content for vehicle data point.
        
        Args:
            veh_id (int): Vehicle ID
            veh_time (float): Vehicle time
            lane_id (int): Lane ID
            veh_dist_trav (float or None): Distance traveled
            veh_speed (float or None): Vehicle speed
            veh_accel (float or None): Vehicle acceleration
            veh_automation (int or None): Automation level
            veh_length (float or None): Vehicle length
            
        Returns:
            str: Formatted tooltip text
        """
        veh_dist_str = f"{veh_dist_trav:.2f}" if veh_dist_trav is not None else " "
        veh_speed_str = f"{veh_speed:.2f}" if veh_speed is not None else " "
        veh_accel_str = f"{veh_accel:.2f}" if veh_accel is not None else " "
        veh_auto_str = f"{veh_automation:d}" if veh_automation is not None else " "
        veh_length_str = f"{veh_length:.2f}" if veh_length is not None else " "
        
        return (
            f"Vehicle ID: {veh_id}\n"
            f"Time: {veh_time:.2f} s\n"
            f"Lane ID: {lane_id}\n"
            f"Distance: {veh_dist_str} m\n"
            f"Speed: {veh_speed_str} m/s\n"
            f"Acceleration: {veh_accel_str} m/s²\n"
            f"Automation: {veh_auto_str}\n"
            f"Length: {veh_length_str} m"
        )

    def _on_lane_changed(self):
        """Handle change in selected lane."""
        self._load_and_plot_data(reset_view=False)