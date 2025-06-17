import logging
import time
import numpy as np
import sqlite3
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
                           QLabel, QComboBox, QDialog)
from PyQt6.QtCore import Qt
import pyqtgraph as pg
from superqt import QLabeledRangeSlider
from .time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog

# Set up logger
logger = logging.getLogger(__name__)


class TimeSpacePlotWidget(QWidget):
    """Widget for displaying time-space trajectory plots with interactive controls.
    
    The widget displays a scatter plot of vehicle trajectories on the left and provides
    interactive controls on the right for filtering and manipulating the data view.
    
    Attributes:
        _db_connection (sqlite3.Connection): Database connection for querying.
        _scatter_plot (pg.PlotWidget): PyQtGraph scatter plot widget.
        _event_button (QPushButton): Button to open settings dialog.
        _lane_combobox (QComboBox): Combobox for lane selection.
        _dist_slider (QLabeledRangeSlider): Vertical slider for distance filtering.
        _time_slider (QLabeledRangeSlider): Vertical slider for time filtering.
        _export_button (QPushButton): Button for exporting data (initially disabled).
        _hide_show_button (QPushButton): Button for hide/show functionality (initially disabled).
        _current_event_id (int): Currently selected event ID.
        _time_range (tuple): Current time range (min, max).
        _dist_range (tuple): Current distance range (min, max).
        _series (list): List of dictionaries containing vehicle series data.
        _tooltip (QLabel): Tooltip for displaying point information.
        _last_queried_point (tuple): Tracks tooltip point to reduce queries.
        series_color (list): Static list of HTML hex colors for series.
        text_color (list): Static list of text colors for tooltip content.
    """
    
    series_color = [
        '#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', 
        '#42d4f4', '#f032e6', '#bfef45', '#fabed4', '#469990', '#dcbeff', 
        '#9A6324', '#fffac8', '#800000', '#aaffc3', '#808000', '#ffd8b1', 
        '#000075', '#000000'
    ]
    
    text_color = [
        "#ffffff", "#ffffff", "#000000", "#ffffff", "#ffffff", "#ffffff", 
        "#000000", "#ffffff", "#000000", "#000000", "#ffffff", "#000000", 
        "#ffffff", "#000000", "#ffffff", "#000000", "#ffffff", "#000000", 
        "#ffffff", "#ffffff"
    ]

    def __init__(self, parent=None):
        """Initialize the TimeSpacePlotWidget.
        
        Args:
            parent: Parent widget (typically MainWindow).
        """
        super().__init__(parent)
        self._db_connection = None
        self._scatter_plot = None
        self._event_button = None
        self._lane_combobox = None
        self._dist_slider = None
        self._time_slider = None
        self._export_button = None
        self._hide_show_button = None
        self._current_event_id = None
        self._time_range = (0.0, 0.0)
        self._dist_range = (0.0, 0.0)
        self._series = []
        self._tooltip = None
        self._last_queried_point = None
        self._last_tooltip_data = None

        logger.debug("TimeSpacePlotWidget initialized")
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Set up the widget UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)
        
        # Left side: scatter plot
        self._scatter_plot = pg.PlotWidget()
        self._scatter_plot.setBackground('w')
        self._scatter_plot.showGrid(x=True, y=True)
        self._scatter_plot.setLabel('left', 'Distance (m)')
        self._scatter_plot.setLabel('bottom', 'Time (s)')
        self._scatter_plot.scene().sigMouseMoved.connect(self._handle_mouse_moved)
        main_layout.addWidget(self._scatter_plot, stretch=1)  # Stretch to fill available space
        
        # Right side: control set
        control_layout = QVBoxLayout()
        
        # Event button
        self._event_button = QPushButton(" ")
        self._event_button.clicked.connect(self._open_settings_dialog)
        control_layout.addWidget(self._event_button)
        
        # Lane combobox
        self._lane_combobox = QComboBox()
        self._lane_combobox.currentIndexChanged.connect(self._on_lane_changed)
        control_layout.addWidget(self._lane_combobox)
        
        # Distance slider section
        dist_label = QLabel("Dist (m)")
        dist_label.setStyleSheet("font-weight: bold;")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_layout.addWidget(dist_label)
        
        self._dist_slider = QLabeledRangeSlider()
        self._dist_slider.setOrientation(Qt.Orientation.Vertical)
        self._dist_slider.valueChanged.connect(self._on_slider_changed)
        control_layout.addWidget(self._dist_slider, stretch=1)  # Stretch to fill available space
        
        # Time slider section
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_layout.addWidget(time_label)
        
        self._time_slider = QLabeledRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Vertical)
        self._time_slider.valueChanged.connect(self._on_slider_changed)
        control_layout.addWidget(self._time_slider, stretch=1)  # Stretch to fill available space
        
        # Export button (initially disabled)
        self._export_button = QPushButton("Export...")
        self._export_button.setEnabled(False)
        control_layout.addWidget(self._export_button)
        
        # Hide/Show button (initially disabled with space label)
        self._hide_show_button = QPushButton(" ")
        self._hide_show_button.setEnabled(False)
        control_layout.addWidget(self._hide_show_button)
        
        main_layout.addLayout(control_layout)
        self._tooltip = QLabel(self._scatter_plot)
        self._tooltip.setStyleSheet("QLabel { padding: 5px; }")
        
    def set_database(self, db_connection: sqlite3.Connection):
        """Set the database connection for the widget.
        
        Args:
            db_connection (sqlite3.Connection): Database connection for querying.
        """
        self._db_connection = db_connection
        logger.debug("Database connection set")
        
    def _open_settings_dialog(self):
        """Open the TimeSpacePlotSettingsDialog to configure plot settings."""
        if not self._db_connection:
            logger.warning("No database connection available for settings dialog")
            return

        settings_dialog = TimeSpacePlotSettingsDialog(parent=self, db_connection=self._db_connection, 
            defaults = {"event_id": self._current_event_id, 
                "dist_range": self._dist_range, 
                "time_range": self._time_range})
        settings_dialog.settings_applied.connect(self._apply_settings)

        # Show dialog modally
        result = settings_dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            logger.debug("Settings dialog accepted")
        else:
            logger.debug("Settings dialog cancelled")
            
    def _apply_settings(self, event_id: int, time_min: float, time_max: float, 
                       dist_min: float, dist_max: float):
        """Apply settings from the settings dialog.
        
        Args:
            event_id (int): Selected event ID.
            time_min (float): Minimum time value.
            time_max (float): Maximum time value.
            dist_min (float): Minimum distance value.
            dist_max (float): Maximum distance value.
        """
        logger.debug(f"Applying settings: event_id={event_id}, "
                    f"time=[{time_min}, {time_max}], dist=[{dist_min}, {dist_max}]")
        
        self._current_event_id = event_id
        self._dist_range = (dist_min, dist_max)
        self._time_range = (time_min, time_max)
        
        # Update event button label
        self._event_button.setText(f"Event {event_id:3d}")
        
        # Initialize series for the new event
        self._initialize_series()
        
        # Update lane combobox
        self._populate_lane_combobox()

        # Set distance slider range and values
        self._dist_slider.setRange(dist_min, dist_max)
        self._dist_slider.setValue(self._dist_range)

        # Set time slider range and values
        self._time_slider.setRange(time_min, time_max)
        self._time_slider.setValue(self._time_range)
        
    def _initialize_series(self):
        """Initialize the _series list with unique vehicle IDs for the current event."""
        if not self._db_connection or self._current_event_id is None:
            return
            
        try:
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT DISTINCT veh_id 
                FROM events 
                WHERE event_id = ? 
                ORDER BY veh_id
            """, (self._current_event_id,))
            
            vehicle_ids = cursor.fetchall()
            
            # Clear existing series
            self._series = []
            
            # Create series dictionaries for each vehicle
            for i, (veh_id,) in enumerate(vehicle_ids):
                series_dict = {
                    'veh_id': veh_id,
                    'series_idx': i,
                    'color': self.series_color[i % len(self.series_color)],
                    'plot_item': None,
                    'veh_time': None,
                    'veh_dist_trav': None
                }
                self._series.append(series_dict)
                
            logger.debug(f"Initialized {len(self._series)} vehicle series for event {self._current_event_id}")
                
        except sqlite3.Error as e:
            logger.error(f"Error initializing series: {e}")
            
    def _populate_lane_combobox(self):
        """Populate the lane combobox with distinct lane IDs for the current event."""
        if not self._db_connection or self._current_event_id is None:
            return
            
        try:
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT DISTINCT lane_id 
                FROM events 
                WHERE event_id = ? 
                ORDER BY lane_id
            """, (self._current_event_id,))
            
            lanes = cursor.fetchall()
            
            self._lane_combobox.clear()
            for lane in lanes:
                lane_id = lane[0]
                self._lane_combobox.addItem(f"Lane {lane_id:2d}", lane_id)
                
            # Default to first item and update plot
            if lanes:
                self._lane_combobox.setCurrentIndex(0)
                self._update_plot_data()
                
        except sqlite3.Error as e:
            logger.error(f"Error populating lane combobox: {e}")
            
    def _on_lane_changed(self, index: int):
        """Handle lane combobox selection change.
        
        Args:
            index (int): Selected combobox index.
        """
        if index >= 0:
            self._update_plot_data()
            
    def _update_plot_data(self):
        """Update the plot data for the current event and lane."""
        if not self._db_connection or self._current_event_id is None or self._lane_combobox.count() == 0:
            return
            
        current_lane_id = self._lane_combobox.currentData()
        if current_lane_id is None:
            return
            
        try:
            # Clear existing plot items
            self._scatter_plot.clear()
            
            # Reset plot_item, veh_time, and veh_dist_trav for each series
            for series in self._series:
                series['plot_item'] = None
                series['veh_time'] = None
                series['veh_dist_trav'] = None
            
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT veh_id, veh_time, veh_dist_trav 
                FROM events 
                WHERE event_id = ? AND lane_id = ? 
                ORDER BY veh_id, veh_time
            """, (self._current_event_id, current_lane_id))
            
            results = cursor.fetchall()
            
            # Group data by vehicle ID
            vehicle_data = {}
            for veh_id, veh_time, veh_dist_trav in results:
                if veh_id not in vehicle_data:
                    vehicle_data[veh_id] = {'times': [], 'distances': []}
                vehicle_data[veh_id]['times'].append(veh_time)
                vehicle_data[veh_id]['distances'].append(veh_dist_trav)
            
            # Create scatter plots for each vehicle in the series
            for series in self._series:
                veh_id = series['veh_id']
                if veh_id in vehicle_data:
                    # Convert to numpy arrays
                    times = np.array(vehicle_data[veh_id]['times'])
                    distances = np.array(vehicle_data[veh_id]['distances'])
                    
                    # Store arrays in series
                    series['veh_time'] = times
                    series['veh_dist_trav'] = distances
                    
                    # Apply current slider filters
                    time_min, time_max = self._time_slider.value()
                    dist_min, dist_max = self._dist_slider.value()
                    
                    time_filter = (times >= time_min) & (times <= time_max)
                    dist_filter = (distances >= dist_min) & (distances <= dist_max)
                    combined_filter = time_filter & dist_filter
                    
                    # Apply filter to get filtered arrays for display
                    filtered_times = times[combined_filter]
                    filtered_distances = distances[combined_filter]
                    
                    # Create scatter plot item with filtered data
                    scatter_item = pg.ScatterPlotItem(
                        x=filtered_times, 
                        y=filtered_distances, 
                        pen=pg.mkPen(color=series['color']), 
                        brush=pg.mkBrush(color=series['color']), 
                        size=4
                    )
                    
                    # Add to plot and store reference
                    self._scatter_plot.addItem(scatter_item)
                    series['plot_item'] = scatter_item

            logger.debug(f"Updated plot data for event {self._current_event_id}, lane {current_lane_id}")

        except sqlite3.Error as e:
            logger.error(f"Error updating plot data: {e}")
            
    def _on_slider_changed(self):
        """Handle changes to distance or time sliders by filtering the plot data."""
        if not self._series:
            return
            
        # Get current slider ranges
        time_min, time_max = self._time_slider.value()
        dist_min, dist_max = self._dist_slider.value()
        
        # Update each series with filtered data
        for series in self._series:
            if series['plot_item'] is not None and series['veh_time'] is not None and series['veh_dist_trav'] is not None:
                # Create numpy filter for time and distance ranges
                time_filter = (series['veh_time'] >= time_min) & (series['veh_time'] <= time_max)
                dist_filter = (series['veh_dist_trav'] >= dist_min) & (series['veh_dist_trav'] <= dist_max)
                combined_filter = time_filter & dist_filter
                
                # Apply filter to get filtered arrays
                filtered_times = series['veh_time'][combined_filter]
                filtered_distances = series['veh_dist_trav'][combined_filter]
                
                # Update the scatter plot item with filtered data
                series['plot_item'].setData(x=filtered_times, y=filtered_distances)
                
        logger.debug(f"Applied slider filters: time=[{time_min}, {time_max}], dist=[{dist_min}, {dist_max}]")

    def _handle_mouse_moved(self, pos):
        """Handle mouse movement for showing tooltips with hysteresis.
    
        Args:
            pos (QPointF): Mouse position in scene coordinates.
        """
        if not self._series:
            return
    
        # Debounce to limit processing frequency
        current_time = time.time()
        if hasattr(self, '_last_mouse_move_time') and (current_time - self._last_mouse_move_time) < 0.05:  # 50ms debounce
            return
        self._last_mouse_move_time = current_time
    
        view_box = self._scatter_plot.getViewBox()
        mouse_point = view_box.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()
    
        min_dist = float('inf')
        closest_data = None
        time_tolerance = 0.05  # Time tolerance in seconds
        pixel_tolerance_show = 10  # Pixel distance to show tooltip
        pixel_tolerance_hide = 15  # Pixel distance to hide tooltip (hysteresis)
    
        for i, series in enumerate(self._series):
            if series["plot_item"]:
                mask = (series["veh_time"] >= mx - time_tolerance) & (series["veh_time"] <= mx + time_tolerance)
                x_filtered = series["veh_time"][mask]
                y_filtered = series["veh_dist_trav"][mask]
                
                if len(x_filtered) > 0:
                    distances = np.abs(y_filtered - my)
                    min_idx = np.argmin(distances)
                    if distances[min_idx] < min_dist:
                        min_dist = distances[min_idx]
                        closest_data = (series["veh_id"], x_filtered[min_idx], y_filtered[min_idx], i)
    
        # Initialize last queried point if not exists
        if not hasattr(self, '_last_queried_point'):
            self._last_queried_point = None
    
        if closest_data and min_dist < pixel_tolerance_show:
            veh_id, veh_time, veh_dist_trav, series_idx = closest_data
    
            # Check if the point is the same as the last queried point
            current_point = (veh_id, veh_time, self._current_event_id)
            if self._last_queried_point != current_point:
                # Query database for new point
                cursor = self._db_connection.cursor()
                cursor.execute("""
                    SELECT veh_speed, veh_accel, veh_automation, veh_length
                    FROM events
                    WHERE event_id = ? AND veh_id = ? AND veh_time = ?
                """, (self._current_event_id, veh_id, veh_time))
                row = cursor.fetchone()
                
                if row:
                    veh_speed = f"{row[0]:.2f}" if row[0] is not None else " "
                    veh_accel = f"{row[1]:.2f}" if row[1] is not None else " "
                    veh_auto = f"{row[2]:d}" if row[2] is not None else " "
                    veh_length = f"{row[3]:.2f}" if row[3] is not None else " "
    
                    tooltip_text = (
                        f"Vehicle ID: {veh_id}\n"
                        f"Time: {veh_time:.2f} s\n"
                        f"Distance: {veh_dist_trav:.2f} m\n"
                        f"Speed: {veh_speed} m/s\n"
                        f"Acceleration: {veh_accel} m/s²\n"
                        f"Automation: {veh_auto}\n"
                        f"Length: {veh_length} m"
                    )
                    self._tooltip.setText(tooltip_text)
                    self._last_queried_point = current_point
                else:
                    self._tooltip.hide()
                    self._last_queried_point = None
                    return
    
            # Update tooltip style and position
            color = self.series_color[series_idx % len(self.series_color)]
            text_color = self.text_color[series_idx % len(self.text_color)]
            self._tooltip.setStyleSheet(
                f"QLabel {{ background-color: {color}; color: {text_color}; padding: 5px; }}"
            )
            self._tooltip.move(int(pos.x()) + 10, int(pos.y()) + 10)
            self._tooltip.show()
        else:
            # Only hide tooltip if distance exceeds hide tolerance (hysteresis)
            if min_dist > pixel_tolerance_hide or not closest_data:
                self._tooltip.hide()
                self._last_queried_point = None