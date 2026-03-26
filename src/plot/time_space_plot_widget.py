import logging
import time
import numpy as np
import sqlite3
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
                           QLabel, QComboBox, QDialog)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeyEvent
import pyqtgraph as pg
from superqt import QLabeledRangeSlider
from .time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
from .custom_view_box import CustomViewBox
from .vertical_gradient_value import GradientConfig, VerticalGradientValue
from gui.toggle_switch import ToggleSwitch

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
        _current_event_id (int): Currently selected event ID.
        _time_range (tuple): Current time range (min, max).
        _dist_range (tuple): Current distance range (min, max).
        _series (dict): Dictionary of vehicle series data by veh_id.
        _tooltip (QLabel): Tooltip for displaying point information.
        _last_queried_point (tuple): Tracks tooltip point to reduce queries.
        _selected_series (set): Set of selected vehicle IDs on current lane.
        _last_mouse_move_time (float): Timestamp of last mouse move for debouncing.
        _lane_ids (list): List of available lane IDs for current event.
        _veh_ids (list): List of available vehicle IDs for entire event.
        series_color (list): Static list of HTML hex colors for series.
        text_color (list): Static list of text colors for tooltip content.
    """
    
    series_color = [
        '#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', 
        '#42d4f4', '#f032e6', '#bfef45', '#469990', '#9A6324', '#800000', 
        '#808000', '#000075', '#000000'
    ]
    
    text_color = [
        '#ffffff', '#ffffff', '#000000', '#ffffff', '#ffffff', '#ffffff', 
        '#000000', '#ffffff', '#000000', '#ffffff', '#ffffff', '#ffffff', 
        '#ffffff', '#ffffff', '#ffffff'
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
        self._vertical_gradient = None
        self._speed_toggle = None
        self._gap_headway_toggle = None
        self._current_event_id = None
        self._time_range = (0.0, 0.0)
        self._dist_range = (0.0, 0.0)
        self._series = None
        self._tooltip = None
        self._last_queried_point = None
        self._selected_series = set()
        self._speed_gradient_enabled = False
        self._last_mouse_move_time = 0.0
        self._lane_ids = []
        self._veh_ids = []

        logger.debug("TimeSpacePlotWidget initialized")
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Set up the widget UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)
        
        # Left side: QVBoxLayout with vertical speed gradient and toggle switches
        left_layout = QVBoxLayout()
        
        # Vertical speed color gradient
        gradient_config = GradientConfig(title="Speed m/s", ranges=[[0, 40, 0, 100, 5]])
        self._vertical_gradient = VerticalGradientValue(gradient_config)
        left_layout.addWidget(self._vertical_gradient, stretch=1)

        # Toggle switches
        self._speed_toggle = ToggleSwitch()
        self._speed_toggle.toggled.connect(self._on_speed_gradient_toggled)
        speed_toggle_label = QLabel("Show Speed") # speed toggle label
        left_layout.addWidget(speed_toggle_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        left_layout.addWidget(self._speed_toggle, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._gap_headway_toggle = ToggleSwitch()
        gap_headway_toggle_label = QLabel("Show Gap\nHeadway") # gap/headway toggle label
        left_layout.addWidget(gap_headway_toggle_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        left_layout.addWidget(self._gap_headway_toggle, alignment=Qt.AlignmentFlag.AlignHCenter)

        main_layout.addLayout(left_layout)

        # middle: QVBoxLayout with plot on top and time controls below
        mid_layout = QVBoxLayout()

        # Plot widget - pass self to CustomViewBox
        custom_viewbox = CustomViewBox(parent_widget=self)
        self._scatter_plot = pg.PlotWidget(viewBox=custom_viewbox)
        self._scatter_plot.setBackground('w')
        self._scatter_plot.showGrid(x=True, y=True)
        self._scatter_plot.setLabel('left', 'Distance (m)')
        self._scatter_plot.setLabel('bottom', 'Time (s)')
        self._scatter_plot.scene().sigMouseMoved.connect(self._handle_mouse_moved)
        
        # Disable auto-range button
        self._scatter_plot.getPlotItem().hideButtons()
        self._scatter_plot.getViewBox().setMenuEnabled(False)
        
        # Disable default mouse interactions
        self._scatter_plot.getViewBox().setMouseEnabled(x=False, y=False)
        
        mid_layout.addWidget(self._scatter_plot, stretch=1)
        
        # Time controls below plot
        time_layout = QHBoxLayout()
        
        # Time label
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_layout.addWidget(time_label)
        
        # Time slider
        self._time_slider = QLabeledRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Horizontal)
        self._time_slider.valueChanged.connect(self._on_slider_changed)
        time_layout.addWidget(self._time_slider, stretch=1)
        
        mid_layout.addLayout(time_layout)
        
        main_layout.addLayout(mid_layout, stretch=1)
        
        # Right side: control set (rearranged)
        right_layout = QVBoxLayout()
        
        # Event button
        self._event_button = QPushButton(" ")
        self._event_button.clicked.connect(self._open_settings_dialog)
        right_layout.addWidget(self._event_button)

        # Distance slider section
        dist_label = QLabel("Dist (m)")
        dist_label.setStyleSheet("font-weight: bold;")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(dist_label)
        
        self._dist_slider = QLabeledRangeSlider()
        self._dist_slider.setOrientation(Qt.Orientation.Vertical)
        self._dist_slider.valueChanged.connect(self._on_slider_changed)
        right_layout.addWidget(self._dist_slider, stretch=1)
        
        # Lane combobox next
        self._lane_combobox = QComboBox()
        self._lane_combobox.currentIndexChanged.connect(self._on_lane_changed)
        right_layout.addWidget(self._lane_combobox)
        
        main_layout.addLayout(right_layout)
        
        # Create tooltip
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
                       dist_min: float, dist_max: float, event_summary: dict):
        """Apply settings from the settings dialog.
        
        Args:
            event_id (int): Selected event ID.
            time_min (float): Minimum time value.
            time_max (float): Maximum time value.
            dist_min (float): Minimum distance value.
            dist_max (float): Maximum distance value.
            event_summary (dict): dictonary object of summary data for event
        """
        logger.debug(f"Applying settings: event_id={event_id}, "
                    f"time=[{time_min}, {time_max}], dist=[{dist_min}, {dist_max}]")
        
        self._current_event_id = event_id
        self._dist_range = (dist_min, dist_max)
        self._time_range = (time_min, time_max)
        self._lane_ids = event_summary.get('lane_id', [])
        print(self._lane_ids)
        self._veh_ids = event_summary.get('veh_id', [])
        self._clear_selection_box()
        self._selected_series.clear()

        # Update plot title
        self._scatter_plot.setTitle(event_summary.get('event_name', f"Event {event_id}"))
    
        # Update event button label
        self._event_button.setText(f"Event {event_id:3d}")
        
        # Initialize series for the new event using provided veh_ids
        self._initialize_series_from_ids()
        
        # Update lane combobox using provided lane_ids
        self._populate_lane_combobox_from_ids()
    
        # Set distance slider range and values
        self._dist_slider.setRange(dist_min, dist_max)
        self._dist_slider.setValue(self._dist_range)
    
        # Set time slider range and values
        self._time_slider.setRange(time_min, time_max)
        self._time_slider.setValue(self._time_range)
        
        # Set initial plot view to match slider ranges
        self._scatter_plot.getViewBox().setRange(
            xRange=[time_min, time_max],
            yRange=[dist_min, dist_max],
            padding=0
        )
        
    def _initialize_series_from_ids(self):
        """Initialize the _series dictionary using the provided vehicle IDs."""
        # Clear existing series
        self._series = {}
        
        # Create series dictionaries for each vehicle using provided veh_ids
        for index, veh_id in enumerate(self._veh_ids):
            series_dict = {
                'veh_id': veh_id,
                'color': self.series_color[index % len(self.series_color)],
                'text_color': self.text_color[index % len(self.text_color)],
                'plot_item': None,
                'veh_time': np.array([], dtype=np.float64),
                'veh_dist_trav': np.array([], dtype=np.float64)
            }
            self._series[veh_id] = series_dict

        logger.debug(f"Initialized {len(self._series)} vehicle series for event {self._current_event_id}")

    def _populate_lane_combobox_from_ids(self):
        """Populate the lane combobox using the provided lane ID pairs."""
        self._lane_combobox.blockSignals(True)
        self._lane_combobox.clear()

        for pair in self._lane_ids:
            lane_id = pair[0]
            orig_lane_id = pair[1] if len(pair) > 1 else None
            self._lane_combobox.addItem(
                f"Lane {lane_id:2d}",
                {'lane_id': lane_id, 'orig_lane_id': orig_lane_id}
            )

        self._lane_combobox.blockSignals(False)
        if self._lane_ids:
            self._lane_combobox.setCurrentIndex(0)
            QTimer.singleShot(0, self._update_plot_data)

        logger.debug(f"Populated lane combobox with {len(self._lane_ids)} lanes")

    def _on_lane_changed(self, index: int):
        """Handle lane combobox selection change.

        Args:
            index (int): Selected combobox index.
        """
        if index >= 0:
            data = self._lane_combobox.currentData()
            self._lane_combobox.setToolTip(f"Orig: {data['orig_lane_id']}")
            self._clear_selection_box()
            QTimer.singleShot(0, self._update_plot_data)

    def _create_scatter_plot_for_vehicle(self, veh_id, times_list, distances_list, speeds_list,
                                          time_min, time_max, dist_min, dist_max):
        """Create a scatter plot item for a single vehicle.

        Args:
            veh_id (int): Vehicle ID.
            times_list (list): Time values.
            distances_list (list): Distance values.
            speeds_list (list): Speed values (elements may be None).
            time_min (float): Minimum time filter.
            time_max (float): Maximum time filter.
            dist_min (float): Minimum distance filter.
            dist_max (float): Maximum distance filter.
        """
        if not times_list or veh_id not in self._series:
            return

        times = np.array(times_list, dtype=np.float64)
        distances = np.array(distances_list, dtype=np.float64)
        # Store speeds as float array; Python None → np.nan so indexing is safe
        speeds = np.array(
            [s if s is not None else np.nan for s in speeds_list],
            dtype=np.float64
        )

        series = self._series[veh_id]
        series['veh_time'] = times
        series['veh_dist_trav'] = distances
        series['veh_speed'] = speeds               # <── always cached

        if len(times) == 0:
            return

        pens, brushes = self._get_point_brushes(veh_id)

        scatter_item = pg.ScatterPlotItem(
            x=times,
            y=distances,
            pen=pens,
            brush=brushes,
            size=4,
        )
        scatter_item.veh_id = veh_id
        self._scatter_plot.addItem(scatter_item)
        series['plot_item'] = scatter_item

    def _update_plot_data(self):
        """Update the plot data for the current event and lane using streaming approach."""
        if not self._db_connection or self._current_event_id is None or self._lane_combobox.count() == 0:
            return

        try:
            self._scatter_plot.clear()
            self._clear_selection_box()

            for series in self._series.values():
                series['plot_item'] = None
                series['veh_time'] = np.array([], dtype=np.float64)
                series['veh_dist_trav'] = np.array([], dtype=np.float64)
                series['veh_speed'] = np.array([], dtype=np.float64)  # <── always reset

            time_min, time_max = self._time_slider.value()
            dist_min, dist_max = self._dist_slider.value()

            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT veh_id, veh_time, veh_dist_trav, veh_speed
                FROM events
                WHERE event_id = ? AND lane_id = ?
                ORDER BY veh_id, veh_time
            """, (self._current_event_id, self._lane_combobox.currentData()['lane_id']))

            prev_veh_id = -1
            current_times = []
            current_distances = []
            current_speeds = []

            for row in cursor:
                veh_id, veh_time, veh_dist_trav, veh_speed = row

                if veh_id != prev_veh_id:
                    if prev_veh_id != -1 and current_times:
                        self._create_scatter_plot_for_vehicle(
                            prev_veh_id, current_times, current_distances, current_speeds,
                            time_min, time_max, dist_min, dist_max
                        )
                    prev_veh_id = veh_id
                    current_times = []
                    current_distances = []
                    current_speeds = []

                current_times.append(veh_time)
                current_distances.append(veh_dist_trav)
                current_speeds.append(veh_speed)   # may be None

            if prev_veh_id != -1 and current_times:
                self._create_scatter_plot_for_vehicle(
                    prev_veh_id, current_times, current_distances, current_speeds,
                    time_min, time_max, dist_min, dist_max
                )

            logger.debug(
                f"Updated plot data for event {self._current_event_id}, "
                f"lane {self._lane_combobox.currentData()['lane_id']}, "
                f"orig lane {self._lane_combobox.currentData()['orig_lane_id']}"
            )

        except sqlite3.Error as e:
            logger.error(f"Error updating plot data: {e}")

    def _on_slider_changed(self):
        """Handle changes to distance or time sliders by filtering the plot data and updating view."""
        if not self._series:
            return
            
        # Clear selection box
        self._clear_selection_box()
        
        # Get current slider ranges
        time_min, time_max = self._time_slider.value()
        dist_min, dist_max = self._dist_slider.value()
        
        # Update plot view to match slider ranges
        self._scatter_plot.getViewBox().setRange(
            xRange=[time_min, time_max],
            yRange=[dist_min, dist_max],
            padding=0
        )

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
        pixel_tolerance_show = 3  # Pixel distance to show tooltip
        pixel_tolerance_hide = 6  # Pixel distance to hide tooltip (hysteresis)
    
        for series in self._series.values():
            if series["plot_item"] and len(series["veh_time"]) > 0:
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
                    SELECT lane_id, veh_speed, veh_accel, veh_automation, veh_length
                    FROM events
                    WHERE event_id = ? AND veh_id = ? AND veh_time = ?
                """, (self._current_event_id, veh_id, veh_time))
                row = cursor.fetchone()
                
                if row:
                    lane_id = f"{row[0]:d}" if row[0] is not None else " "
                    veh_speed = f"{row[1]:.2f}" if row[1] is not None else " "
                    veh_accel = f"{row[2]:.2f}" if row[2] is not None else " "
                    veh_auto = f"{row[3]:d}" if row[3] is not None else " "
                    veh_length = f"{row[4]:.2f}" if row[4] is not None else " "
    
                    tooltip_text = (
                        f"Vehicle ID: {veh_id}\n"
                        f"Time: {veh_time:.2f} s\n"
                        f"Lane ID: {lane_id}\n"
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
            color = '#000'
            text_color = '#fff'
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

    def _update_plot_colors(self):
        """Update scatter plot item colors based on _selected_series and color mode.

        Uses _get_point_brushes so the gradient / flat / dimmed decision is
        made in one place.  Works in-place on existing ScatterPlotItems; no
        database round-trip required.
        """
        for series in self._series.values():
            if series['plot_item'] is not None:
                veh_id = series['veh_id']
                pens, brushes = self._get_point_brushes(veh_id)
                series['plot_item'].setPen(pens)
                series['plot_item'].setBrush(brushes)
        logger.debug("Updated plot colors based on selected series and color mode")
    
    def _on_speed_gradient_toggled(self, checked: bool):
        """Handle speed gradient toggle switch.

        When on, each point of every active (non-dimmed) trajectory is colored
        by its speed via the vertical gradient.  When off, trajectories return
        to their flat per-vehicle series color.  Either way only the brushes
        are updated in-place; no database round-trip is needed.

        Args:
            checked (bool): True when the toggle is switched on.
        """
        self._speed_gradient_enabled = checked
        if self._series:
            self._update_plot_colors()
        logger.debug(f"Speed gradient toggled: {checked}")

    def _get_point_brushes(self, veh_id: int):
        """Return per-point (pens, brushes) lists for a vehicle.

        Decision tree:
          1. Dimmed (unselected while a selection exists) → flat neutral grey.
          2. Speed-gradient mode active → per-point color from gradient lookup.
          3. Default → flat per-vehicle series color.

        Args:
            veh_id (int): Vehicle ID.

        Returns:
            tuple[list, list]: (pens, brushes) — one entry per data point.
        """
        series = self._series[veh_id]
        n = len(series['veh_time'])

        # Determine whether this vehicle is dimmed
        is_dimmed = bool(self._selected_series) and veh_id not in self._selected_series

        if is_dimmed:
            # Flat neutral grey for every point
            pen = pg.mkPen(color='#a9a9a9')
            brush = pg.mkBrush(color='#a9a9a9')
            return [pen] * n, [brush] * n

        if self._speed_gradient_enabled:
            # Per-point color from the speed → gradient lookup
            speeds = series.get('veh_speed')
            pens = []
            brushes = []
            for i in range(n):
                spd = speeds[i] if speeds is not None and i < len(speeds) else None
                if spd is not None and not np.isnan(spd):
                    hex_color = self._vertical_gradient.value_to_color_hex(float(spd))
                else:
                    hex_color = '#808080'   # grey for missing/NaN speed
                pens.append(pg.mkPen(color=hex_color))
                brushes.append(pg.mkBrush(color=hex_color))
            return pens, brushes

        # Default: flat per-vehicle series color
        color = series['color']
        pen = pg.mkPen(color=color)
        brush = pg.mkBrush(color=color)
        return [pen] * n, [brush] * n
