import logging
import time
import numpy as np
import sqlite3
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
                           QLabel, QComboBox, QDialog)
from PyQt6.QtCore import Qt, QPointF, QEvent
from PyQt6.QtGui import QKeyEvent, QMouseEvent
import pyqtgraph as pg
from superqt import QLabeledRangeSlider
from .time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog

# Set up logger
logger = logging.getLogger(__name__)

class CustomViewBox(pg.ViewBox):
    """Custom ViewBox that handles selection box drawing when Shift is held."""
    
    def __init__(self, parent_widget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent_widget = parent_widget
        self._selection_start = None
        self._is_selecting = False
        self._selection_box = None

    def mousePressEvent(self, ev):
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
        if self._is_selecting and self._selection_box:
            # Update selection box
            current_pos = self.mapSceneToView(ev.scenePos())
            
            x0, y0 = self._selection_start.x(), self._selection_start.y()
            x1, y1 = current_pos.x(), current_pos.y()
            
            # Update selection box position and size
            self._selection_box.setPos(min(x0, x1), min(y0, y1))
            self._selection_box.setSize([abs(x1 - x0), abs(y1 - y0)])
            ev.accept()
            return
        
        # For non-selection moves, use default behavior
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
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

    def _clear_selection_box(self):
        """Remove the selection box from the plot."""
        if self._selection_box:
            self.parent_widget._scatter_plot.removeItem(self._selection_box)
            self._selection_box = None
            self._is_selecting = False
            self._selection_start = None

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
        _current_event_id (int): Currently selected event ID.
        _time_range (tuple): Current time range (min, max).
        _dist_range (tuple): Current distance range (min, max).
        _series (dict): Dictionary of vehicle series data by veh_id.
        _tooltip (QLabel): Tooltip for displaying point information.
        _last_queried_point (tuple): Tracks tooltip point to reduce queries.
        _selected_series (set): Set of selected vehicle IDs.
        _last_mouse_move_time (float): Timestamp of last mouse move for debouncing.
        _selection_box (pg.RectROI): Selection box for group selection.
        _is_selecting (bool): Flag indicating if selection box is active.
        _selection_start (QPointF): Starting point of selection box in view coordinates.
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
        self._current_event_id = None
        self._time_range = (0.0, 0.0)
        self._dist_range = (0.0, 0.0)
        self._series = None
        self._tooltip = None
        self._last_queried_point = None
        self._selected_series = set()
        self._last_mouse_move_time = 0.0
        self._selection_box = None
        self._is_selecting = False
        self._selection_start = None

        logger.debug("TimeSpacePlotWidget initialized")
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Set up the widget UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)
        
        # Left side: scatter plot - pass self to CustomViewBox
        custom_viewbox = CustomViewBox(parent_widget=self)
        self._scatter_plot = pg.PlotWidget(viewBox=custom_viewbox)
        self._scatter_plot.setBackground('w')
        self._scatter_plot.showGrid(x=True, y=True)
        self._scatter_plot.setLabel('left', 'Distance (m)')
        self._scatter_plot.setLabel('bottom', 'Time (s)')
        self._scatter_plot.scene().sigMouseMoved.connect(self._handle_mouse_moved)
        self._scatter_plot.scene().sigMouseClicked.connect(self._handle_mouse_clicked)
        main_layout.addWidget(self._scatter_plot, stretch=1)
        
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
        control_layout.addWidget(self._dist_slider, stretch=1)
        
        # Time slider section
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_layout.addWidget(time_label)
        
        self._time_slider = QLabeledRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Vertical)
        self._time_slider.valueChanged.connect(self._on_slider_changed)
        control_layout.addWidget(self._time_slider, stretch=1)
        
        main_layout.addLayout(control_layout)
        self._tooltip = QLabel(self._scatter_plot)
        self._tooltip.setStyleSheet("QLabel { padding: 5px; }")
        
        # Keep default PanMode to allow panning when Shift is not held
        view_box = self._scatter_plot.getViewBox()
        view_box.setMouseMode(view_box.PanMode)
        
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
        self._selected_series.clear()
        self._clear_selection_box()

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
            self._series = {}
            
            # Create series dictionaries for each vehicle
            for (veh_id,) in vehicle_ids:
                series_dict = {
                    'veh_id': veh_id,
                    'color': self.series_color[veh_id % len(self.series_color)],
                    'text_color': self.text_color[veh_id % len(self.text_color)],
                    'plot_item': None,
                    'veh_time': None,
                    'veh_dist_trav': None
                }
                self._series[veh_id] = series_dict

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
            self._clear_selection_box()
            self._update_plot_data()
            
    def _update_plot_data(self):
        """Update the plot data for the current event and lane."""
        if not self._db_connection or self._current_event_id is None or self._lane_combobox.count() == 0:
            return
            
        try:
            # Clear existing plot items and selection box
            self._scatter_plot.clear()
            self._clear_selection_box()
            
            # Reset plot_item, veh_time, and veh_dist_trav for each series
            for series in self._series.values():
                series['plot_item'] = None
                series['veh_time'] = None
                series['veh_dist_trav'] = None
            
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT veh_id, veh_time, veh_dist_trav 
                FROM events 
                WHERE event_id = ? AND lane_id = ? 
                ORDER BY veh_id, veh_time
            """, (self._current_event_id, self._lane_combobox.currentData()))
            
            results = cursor.fetchall()
            
            # Group data by vehicle ID
            vehicle_data = {}
            for veh_id, veh_time, veh_dist_trav in results:
                if veh_id not in vehicle_data:
                    vehicle_data[veh_id] = {'times': [], 'distances': []}
                vehicle_data[veh_id]['times'].append(veh_time)
                vehicle_data[veh_id]['distances'].append(veh_dist_trav)
            
            # Create scatter plots for each vehicle in the series
            for series in self._series.values():
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
                    
                    # Set color based on _selected_series
                    color = series['color'] if veh_id in self._selected_series or not self._selected_series else '#a9a9a9'
                    
                    # Create scatter plot item with filtered data
                    scatter_item = pg.ScatterPlotItem(
                        x=filtered_times, 
                        y=filtered_distances, 
                        pen=pg.mkPen(color=color), 
                        brush=pg.mkBrush(color=color), 
                        size=4
                    )
                    scatter_item.veh_id = veh_id
                    
                    # Add to plot and store reference
                    self._scatter_plot.addItem(scatter_item)
                    series['plot_item'] = scatter_item

            logger.debug(f"Updated plot data for event {self._current_event_id}, lane {self._lane_combobox.currentData()}")

        except sqlite3.Error as e:
            logger.error(f"Error updating plot data: {e}")
            
    def _on_slider_changed(self):
        """Handle changes to distance or time sliders by filtering the plot data."""
        if not self._series:
            return
            
        # Clear selection box
        self._clear_selection_box()
        
        # Get current slider ranges
        time_min, time_max = self._time_slider.value()
        dist_min, dist_max = self._dist_slider.value()
        
        # Update each series with filtered data
        for series in self._series.values():
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
        if (current_time - self._last_mouse_move_time) < 0.05:  # 50ms debounce
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
    
        for series in self._series.values():
            if series["plot_item"]:
                mask = (series["veh_time"] >= mx - time_tolerance) & (series["veh_time"] <= mx + time_tolerance)
                x_filtered = series["veh_time"][mask]
                y_filtered = series["veh_dist_trav"][mask]
                
                if len(x_filtered) > 0:
                    distances = np.abs(y_filtered - my)
                    min_idx = np.argmin(distances)
                    if distances[min_idx] < min_dist:
                        min_dist = distances[min_idx]
                        closest_data = (series["veh_id"], series["color"], series["text_color"], x_filtered[min_idx], y_filtered[min_idx])
    
        # Initialize last queried point if not exists
        if not hasattr(self, '_last_queried_point'):
            self._last_queried_point = None
    
        if closest_data and min_dist < pixel_tolerance_show:
            veh_id, color, text_color, veh_time, veh_dist_trav = closest_data
    
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
                        f"Acceleration: {veh_accel} m/s^2\n"
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

    def _clear_selection_box(self):
        """Remove the selection box from the plot."""
        view_box = self._scatter_plot.getViewBox()
        if hasattr(view_box, '_clear_selection_box'):
            view_box._clear_selection_box()

    def keyPressEvent(self, event: QKeyEvent):
        """Handle key press events, clearing selection on Escape key."""
        if event.key() == Qt.Key.Key_Escape:
            self._selected_series.clear()
            self._update_plot_colors()
            self._clear_selection_box()
            logger.debug("Cleared selected series on Escape key")
            event.accept()
        else:
            super().keyPressEvent(event)

    def _handle_mouse_clicked(self, event):
        """Handle mouse click to toggle vehicle selection."""
        # Handle single left-click without Shift modifier
        if event.button() != Qt.MouseButton.LeftButton or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return
            
        pos = event.scenePos()
        view_box = self._scatter_plot.getViewBox()
        mouse_point = view_box.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()
        
        # Find closest point
        min_dist = float('inf')
        closest_veh_id = None
        time_tolerance = 0.05  # Time tolerance in seconds
        pixel_tolerance = 10   # Pixel distance to register click
        
        for series in self._series.values():
            if series["plot_item"]:
                mask = (series["veh_time"] >= mx - time_tolerance) & (series["veh_time"] <= mx + time_tolerance)
                x_filtered = series["veh_time"][mask]
                y_filtered = series["veh_dist_trav"][mask]
                
                if len(x_filtered) > 0:
                    distances = np.abs(y_filtered - my)
                    min_idx = np.argmin(distances)
                    if distances[min_idx] < pixel_tolerance and distances[min_idx] < min_dist:
                        min_dist = distances[min_idx]
                        closest_veh_id = series["veh_id"]
        
        if closest_veh_id is not None:
            # Toggle veh_id in selected_series
            if closest_veh_id in self._selected_series:
                self._selected_series.remove(closest_veh_id)
            else:
                self._selected_series.add(closest_veh_id)
                # Clear selected_series if it contains all vehicles
                if len(self._selected_series) == len(self._series):
                    self._selected_series.clear()
            
            # Update plot colors
            self._update_plot_colors()
            logger.debug(f"Toggled veh_id {closest_veh_id}, selected_series: {self._selected_series}")
    
    def _update_plot_colors(self):
        """Update scatter plot item colors based on _selected_series."""
        # Update colors for all series
        for series in self._series.values():
            if series['plot_item'] is not None:
                veh_id = series['veh_id']
                color = series['color'] if veh_id in self._selected_series or not self._selected_series else '#a9a9a9'
                series['plot_item'].setPen(pg.mkPen(color=color))
                series['plot_item'].setBrush(pg.mkBrush(color=color))
        logger.debug("Updated plot colors based on selected series")