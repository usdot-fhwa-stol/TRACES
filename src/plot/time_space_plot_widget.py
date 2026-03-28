import sys
import math
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

    _SERIES_SEG_THRESHOLD = 0.3   # seconds; max intra-segment time gap

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
        self._last_gh_values = None  # cached (gap_f, gap_b) from hover
        self._selected_series = set()
        self._speed_gradient_enabled = False
        self._last_mouse_move_time = 0.0
        self._lane_ids = []
        self._veh_ids = []
        self._gh_mode_enabled       = False
        self._gh_overlay_items      = []     # transient hover items
        self._gh_locked_items       = {}     # dict: (veh_id, veh_time) -> list[pg items]

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
        self._gap_headway_toggle.toggled.connect(self._on_gap_headway_toggled)
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
        series['veh_speed'] = speeds # cached for speed gradient

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
        """Handle mouse movement: gap/headway overlay or normal tooltip.

        Args:
            pos (QPointF): Mouse position in scene coordinates.
        """
        if not self._series:
            return

        # Debounce
        current_time = time.time()
        if (current_time - self._last_mouse_move_time) < 0.05:
            return
        self._last_mouse_move_time = current_time

        view_box   = self._scatter_plot.getViewBox()
        mouse_point = view_box.mapSceneToView(pos)
        mx, my     = mouse_point.x(), mouse_point.y()

        time_tolerance       = 0.05
        pixel_tolerance_show = 3
        pixel_tolerance_hide = 6

        # ── find closest point (shared by both modes) ─────────────────────
        min_dist     = float('inf')
        closest_data = None

        for series in self._series.values():
            if series["plot_item"] and len(series["veh_time"]) > 0:
                mask = ((series["veh_time"] >= mx - time_tolerance) &
                        (series["veh_time"] <= mx + time_tolerance))
                x_f = series["veh_time"][mask]
                y_f = series["veh_dist_trav"][mask]
                if len(x_f) > 0:
                    distances = np.abs(y_f - my)
                    idx = np.argmin(distances)
                    if distances[idx] < min_dist:
                        min_dist     = distances[idx]
                        closest_data = (series["veh_id"],
                                        series["color"],
                                        series["text_color"],
                                        x_f[idx], y_f[idx])

        # ── gap/headway mode ──────────────────────────────────────────────
        if self._gh_mode_enabled:

            if closest_data and min_dist < pixel_tolerance_show:
                veh_id, _, _, veh_time, veh_dist = closest_data
                current_point = (veh_id, veh_time, self._current_event_id)
                pt_key = (veh_id, veh_time)
                is_locked = pt_key in self._gh_locked_items

                if is_locked:
                    # Hovering a locked point: clear transient overlay, leave reticle hidden
                    self._last_queried_point = current_point
                else:

                    if self._last_queried_point != current_point:
                        gap_f, gap_b = self._find_gap(veh_id, veh_time, veh_dist)
                        hway_f, hway_b = self._find_hway(veh_id, veh_time, veh_dist)
                        self._last_gh_values = (gap_f, gap_b, hway_f, hway_b)
                        self._clear_gh_overlay()
                        self._draw_gh_overlay(
                            veh_id, veh_time, veh_dist,
                            gap_b, gap_f, hway_b, hway_f,
                            target_list=self._gh_overlay_items
                        )
                        self._last_queried_point = current_point
            else:
                # Mouse not near any point: clear transient overlay, reticle stays hidden
                self._clear_gh_overlay()
                self._last_queried_point = None
                self._last_gh_values = None

            self._tooltip.hide()
            return

        # ── normal tooltip mode ──────────────────────────────
        if not hasattr(self, '_last_queried_point'):
            self._last_queried_point = None

        if closest_data and min_dist < pixel_tolerance_show:
            veh_id, color, text_color, veh_time, veh_dist_trav = closest_data
            current_point = (veh_id, veh_time, self._current_event_id)

            if self._last_queried_point != current_point:
                cursor = self._db_connection.cursor()
                cursor.execute("""
                    SELECT lane_id, veh_speed, veh_accel, veh_automation, veh_length
                    FROM   events
                    WHERE  event_id = ? AND veh_id = ? AND veh_time = ?
                """, (self._current_event_id, veh_id, veh_time))
                row = cursor.fetchone()

                if row:
                    lane_id   = f"{row[0]:d}"   if row[0] is not None else " "
                    veh_speed = f"{row[1]:.2f}" if row[1] is not None else " "
                    veh_accel = f"{row[2]:.2f}" if row[2] is not None else " "
                    veh_auto  = f"{row[3]:d}"   if row[3] is not None else " "
                    veh_len   = f"{row[4]:.2f}" if row[4] is not None else " "

                    self._tooltip.setText(
                        f"Vehicle ID: {veh_id}\n"
                        f"Time: {veh_time:.2f} s\n"
                        f"Lane ID: {lane_id}\n"
                        f"Distance: {veh_dist_trav:.2f} m\n"
                        f"Speed: {veh_speed} m/s\n"
                        f"Acceleration: {veh_accel} m/s²\n"
                        f"Automation: {veh_auto}\n"
                        f"Length: {veh_len} m"
                    )
                    self._last_queried_point = current_point
                else:
                    self._tooltip.hide()
                    self._last_queried_point = None
                    return

            self._tooltip.setStyleSheet(
                "QLabel { background-color: #000; color: #fff; padding: 5px; }"
            )
            self._tooltip.move(int(pos.x()) + 10, int(pos.y()) + 10)
            self._tooltip.show()

        else:
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
            if self._gh_mode_enabled and self._gh_locked_items:
                # In GH mode: clear all locked points
                self._clear_gh_all_locked()
                self._last_queried_point = None
                logger.debug("Cleared all GH locked points on Escape")
            else:
                # Normal mode: clear trajectory selection
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

    def _clear_gh_overlay(self):
        """Remove transient hover gap/headway lines and labels from the plot."""
        for item in self._gh_overlay_items:
            self._scatter_plot.removeItem(item)
        self._gh_overlay_items.clear()

    def _clear_gh_all_locked(self):
        """Remove all persistent locked-point gap/headway lines."""
        for items in self._gh_locked_items.values():
            for item in items:
                self._scatter_plot.removeItem(item)
        self._gh_locked_items.clear()
    
    def _clear_gh_locked_point(self, pt_key: tuple):
        """Remove persistent lines for one locked point (veh_id, veh_time)."""
        items = self._gh_locked_items.pop(pt_key, [])
        for item in items:
            self._scatter_plot.removeItem(item)

    def _on_gap_headway_toggled(self, checked: bool):
        """Enable or disable gap/headway overlay mode.

        When enabled:
          - Normal trajectory selection is suppressed.
          - Hovering a point draws live gap/headway lines.
          - Clicking a point locks those lines; clicking again unlocks.

        When disabled:
          - All overlay graphics are removed.
          - Normal selection behaviour is restored.
        """
        self._gh_mode_enabled = checked
#        if checked:
#            self._test_find_hway()
    
        if not checked:
            self._clear_gh_overlay()
            self._clear_gh_all_locked()        # clears entire dict
            self._tooltip.hide()
        logger.debug(f"Gap/headway mode toggled: {checked}")

    def _draw_gh_overlay(self, veh_id: int, veh_time: float, veh_dist: float,
                             gap_b, gap_f, hway_b, hway_f, *,
                             target_list: list):
            """Draw gap and headway lines + labels for the given point.
    
            Items are appended to `target_list` (either _gh_overlay_items for
            transient hover lines or _gh_locked_items for persistent locked lines)
            so the two sets can be cleared independently.  The caller is
            responsible for clearing the appropriate list before calling.
    
            Axes convention (same as the plot):
                x-axis → time (s)
                y-axis → distance (m)
    
            gap_b / gap_f  are distances (m) → drawn as vertical segments
                            (same time, ± distance).
            hway_b / hway_f are times   (s) → drawn as horizontal segments
                            (same distance, ± time).
    
            Args:
                veh_id      : vehicle identifier (used only for logging).
                veh_time    : x-coordinate of the source point (s).
                veh_dist    : y-coordinate of the source point (m).
                gap_b, gap_f   : backward/forward spatial gap (m); may be None/NaN.
                hway_b, hway_f : backward/forward time headway (s); may be None/NaN.
                target_list    : list to append created pg items to.
            """
            LINE_PEN    = pg.mkPen(color=(120, 120, 120), width=3,
                                   style=Qt.PenStyle.SolidLine)
            LABEL_COLOR = (80, 80, 80)
    
            def _finite(v):
                """Return float if v is a finite number, else None."""
                try:
                    f = float(v)
                    return f if math.isfinite(f) else None
                except (TypeError, ValueError):
                    return None
    
            def _add_line(x0, y0, x1, y1):
                item = pg.PlotDataItem([x0, x1], [y0, y1], pen=LINE_PEN)
                self._scatter_plot.addItem(item)
                target_list.append(item)
    
            def _add_label(x, y, text, anchor, angle=0):
                item = pg.TextItem(text=text, color=LABEL_COLOR, anchor=anchor,
                                   angle=angle)
                item.setPos(x, y)
                item.setFont(pg.QtGui.QFont("Arial", 8))
                self._scatter_plot.addItem(item)
                target_list.append(item)
    
            # ── vertical lines (gap — same time, different distance) ─────────────
            #
            # gap_f: other vehicle is at higher dist at same time → line goes UP
            #        label: rotated 90°, right of and above source point
            # gap_b: other vehicle is at lower dist at same time  → line goes DOWN
            #        label: rotated 90°, left of and below source point
    
            gf = _finite(gap_f)
            if gf is not None and gf > 0:
                y_end = veh_dist + gf
                _add_line(veh_time, veh_dist, veh_time, y_end)
                _add_label(veh_time, veh_dist,
                           f"{gf:.2f} m",
                           anchor=(0.0, 0.0),
                           angle=90)
    
            gb = _finite(gap_b)
            if gb is not None and gb > 0:
                y_end = veh_dist - gb
                _add_line(veh_time, veh_dist, veh_time, y_end)
                _add_label(veh_time, veh_dist,
                           f"{gb:.2f} m",
                           anchor=(1.0, 1.0),
                           angle=90)
    
            # ── horizontal lines (headway — same distance, different time) ───────
            #
            # hway_f: other vehicle passed this distance earlier → line goes LEFT
            #         label: left of and above source point
            # hway_b: other vehicle passed this distance later   → line goes RIGHT
            #         label: right of and below source point
    
            hf = _finite(hway_f)
            if hf is not None and hf > 0:
                x_end = veh_time - hf
                _add_line(veh_time, veh_dist, x_end, veh_dist)
                _add_label(veh_time, veh_dist,
                           f"{hf:.2f} s",
                           anchor=(1.0, 1.0))
    
            hb = _finite(hway_b)
            if hb is not None and hb > 0:
                x_end = veh_time + hb
                _add_line(veh_time, veh_dist, x_end, veh_dist)
                _add_label(veh_time, veh_dist,
                           f"{hb:.2f} s",
                           anchor=(0.0, 0.0))
    
            logger.debug(
                f"Drew GH overlay for veh {veh_id} at t={veh_time:.2f}: "
                f"gap_b={gb}, gap_f={gf}, hway_b={hb}, hway_f={hf}"
            )

    @staticmethod
    def _find_hway_f(arr: np.ndarray, start_idx: int, search_val: float
                     ) -> tuple[int, int, float]:
        """Reverse-iterate from *start_idx* to find the forward headway offset.

        Scans backward (decreasing index) from *start_idx* until an element is
        found that is **less than** *search_val*, then linearly interpolates the
        fractional position between that index and the next higher index.

        Args:
            arr:        1-D sorted float64 array (e.g. ``veh_dist_trav``).
            start_idx:  Index to begin the reverse scan from.
            search_val: Value to locate within *arr*.

        Returns:
            ``(index1, index2, offset)`` where *index1* < *index2* are the
            bracketing indices and *offset* ∈ [0, 1] is the interpolation
            weight toward *index2*.  Returns ``(-1, -1, 0.0)`` when no
            bracketing pair is found.
        """
        i = start_idx
        while i >= 0:
            if arr[i] < search_val:
                # arr[i] < search_val <= arr[i+1]  (i+1 is valid: we entered
                # the loop from start_idx, so i+1 <= start_idx which is a
                # valid index checked by the caller).
                idx1, idx2 = i, i + 1
                span = arr[idx2] - arr[idx1]
                offset = (search_val - arr[idx1]) / span if span != 0.0 else 0.0
                return idx1, idx2, offset
            i -= 1
        return -1, -1, 0.0

    @staticmethod
    def _find_hway_b(arr: np.ndarray, start_idx: int, search_val: float
                     ) -> tuple[int, int, float]:
        """Forward-iterate from *start_idx* to find the backward headway offset.

        Scans forward (increasing index) from *start_idx* until an element is
        found that is **greater than** *search_val*, then linearly interpolates
        the fractional position between that index and the one before it.

        Args:
            arr:        1-D sorted float64 array (e.g. ``veh_dist_trav``).
            start_idx:  Index to begin the forward scan from.
            search_val: Value to locate within *arr*.

        Returns:
            ``(index1, index2, offset)`` where *index1* < *index2* are the
            bracketing indices and *offset* ∈ [0, 1] is the interpolation
            weight toward *index2*.  Returns ``(-1, -1, 0.0)`` when no
            bracketing pair is found.
        """
        n = len(arr)
        i = start_idx
        while i < n:
            if arr[i] > search_val:
                # arr[i-1] <= search_val < arr[i]  (i-1 is valid because we
                # entered at start_idx and i advanced at least once only when
                # arr[start_idx] <= search_val, guaranteeing i >= 1).
                idx1, idx2 = i - 1, i
                span = arr[idx2] - arr[idx1]
                offset = (search_val - arr[idx1]) / span if span != 0.0 else 0.0
                return idx1, idx2, offset
            i += 1
        return -1, -1, 0.0

    def _find_headway_gap(self, veh_id: int, veh_time: float, veh_dist: float
                          ) -> tuple[float, float, float, float]:
        """Find forward/backward headway and gap values for a trajectory point.

        Iterates over every series that is **not** the queried vehicle and
        attempts to derive spatial gap and time-headway values using the cached
        ``veh_time`` / ``veh_dist_trav`` arrays.

        Gap classification (relative to *veh_dist*):
          * **gap_f** – closest other-vehicle distance **above** *veh_dist* at
            the same instant; stored as the absolute difference (positive).
          * **gap_b** – closest other-vehicle distance **below** *veh_dist* at
            the same instant; stored as the absolute difference (positive).

        Headway classification (relative to *veh_time*):
          * **hway_f** – the other vehicle passed *veh_dist* **before**
            *veh_time* (negative offset stored as positive magnitude).
          * **hway_b** – the other vehicle passed *veh_dist* **after**
            *veh_time* (positive offset).

        Initial sentinel values ensure the first valid candidate always wins:
          * ``gap_b`` and ``gap_f``  → ``sys.float_info.max``
          * ``hway_f`` and ``hway_b`` → ``-sys.float_info.max``

        Args:
            veh_id:   Vehicle ID of the subject point (its own series is skipped).
            veh_time: Time coordinate of the subject point (seconds).
            veh_dist: Distance-travelled coordinate of the subject point (metres).

        Returns:
            ``(hway_f, hway_b, gap_f, gap_b)`` – all as positive magnitudes.
            Sentinel values (``±sys.float_info.max``) are returned for
            directions where no candidate was found.
        """
        gap_f  =  sys.float_info.max   # smallest above-distance found so far
        gap_b  =  sys.float_info.max   # smallest below-distance found so far
        hway_f = -sys.float_info.max   # headway forward  (will store negative)
        hway_b = -sys.float_info.max   # headway backward (will store negative)

        for sid, series in self._series.items():
            if sid == veh_id:
                continue

            t_arr = series['veh_time']
            d_arr = series['veh_dist_trav']

            if len(t_arr) == 0 or len(d_arr) == 0:
                continue

            t_min, t_max = t_arr[0], t_arr[-1]
            d_min, d_max = d_arr[0], d_arr[-1]

            # ── Can this series bracket veh_time? ────────────────────────────
            time_brackets = (t_min <= veh_time <= t_max)

            if time_brackets:
                # ── Locate veh_time in this series' time array ───────────────
                t_idx = int(np.searchsorted(t_arr, veh_time, side='left'))
                t_idx = min(t_idx, len(t_arr) - 1)

                # Determine the interpolated distance at veh_time
                if t_arr[t_idx] == veh_time:
                    # Exact match – use directly
                    other_dist = float(d_arr[t_idx])
                    d_idx = t_idx          # index into d_arr for headway search
                elif t_idx > 0 and (veh_time - t_arr[t_idx - 1]) < self._SERIES_SEG_THRESHOLD:
                    # Previous sample is within the intra-segment threshold –
                    # interpolate between t_idx-1 and t_idx
                    lo, hi = t_idx - 1, t_idx
                    pct = (veh_time - t_arr[lo]) / (t_arr[hi] - t_arr[lo])
                    other_dist = float(d_arr[lo] + pct * (d_arr[hi] - d_arr[lo]))
                    d_idx = lo             # use lower bracket index for headway scan
                else:
                    # Gap in trajectory too large to interpolate reliably
                    other_dist = None
                    d_idx = None

                if other_dist is not None:
                    diff = other_dist - veh_dist   # signed: + means ahead, – means behind

                    if diff > 0:
                        # ── Forward gap candidate ────────────────────────────
                        if diff < gap_f:
                            gap_f = diff

                        # ── Forward gap → search for backward headway ────────
                        # "Who passed veh_dist after veh_time?" – scan forward
                        # in d_arr from d_idx for veh_dist.
                        i1, i2, ofs = TimeSpacePlotWidget._find_hway_b(
                            d_arr, d_idx, veh_dist
                        )
                        if i1 >= 0:
                            hw = float(t_arr[i1] + ofs * (t_arr[i2] - t_arr[i1]))
                            hw_delta = hw - veh_time          # positive: after
                            if hw_delta > hway_b:
                                hway_b = hw_delta

                    elif diff < 0:
                        # ── Backward gap candidate ───────────────────────────
                        abs_diff = -diff
                        if abs_diff < gap_b:
                            gap_b = abs_diff

                        # ── Backward gap → search for forward headway ────────
                        # "Who passed veh_dist before veh_time?" – scan backward
                        # in d_arr from d_idx for veh_dist.
                        i1, i2, ofs = TimeSpacePlotWidget._find_hway_f(
                            d_arr, d_idx, veh_dist
                        )
                        if i1 >= 0:
                            hw = float(t_arr[i1] + ofs * (t_arr[i2] - t_arr[i1]))
                            hw_delta = veh_time - hw          # positive: before
                            if hw_delta > hway_f:
                                hway_f = hw_delta

                    # diff == 0: same distance, skip headway (degenerate case)
                    continue

            # ── Series cannot bracket veh_time – try headway-only paths ──────
            # A series whose distance range contains veh_dist can yield a
            # headway value even when the time ranges don't overlap.
            dist_brackets = (d_min <= veh_dist <= d_max)

            if not dist_brackets:
                continue   # series is entirely irrelevant to this query point

            if t_max < veh_time:
                # Series lies entirely in the past → candidate for hway_f
                # (it passed veh_dist before the query time)
                i1, i2, ofs = TimeSpacePlotWidget._find_hway_f(
                    d_arr, len(d_arr) - 1, veh_dist
                )
                if i1 >= 0:
                    hw = float(t_arr[i1] + ofs * (t_arr[i2] - t_arr[i1]))
                    hw_delta = veh_time - hw
                    if hw_delta > hway_f:
                        hway_f = hw_delta

            elif t_min > veh_time:
                # Series lies entirely in the future → candidate for hway_b
                # (it will pass veh_dist after the query time)
                i1, i2, ofs = TimeSpacePlotWidget._find_hway_b(
                    d_arr, 0, veh_dist
                )
                if i1 >= 0:
                    hw = float(t_arr[i1] + ofs * (t_arr[i2] - t_arr[i1]))
                    hw_delta = hw - veh_time
                    if hw_delta > hway_b:
                        hway_b = hw_delta

        return (
            hway_f if hway_f != -sys.float_info.max else None,
            hway_b if hway_b != -sys.float_info.max else None,
            gap_f  if gap_f  !=  sys.float_info.max else None,
            gap_b  if gap_b  !=  sys.float_info.max else None,
        )
    
    def _find_gap(self, veh_id: int, veh_time: float, veh_dist: float
                     ) -> tuple:
            """Find forward and backward spatial gap for a trajectory point.
    
            For each other vehicle whose time range brackets *veh_time*, the
            distance at *veh_time* is determined by linear interpolation between
            the two surrounding samples.  The closest vehicle ahead (higher
            distance) gives *gap_f*; the closest vehicle behind (lower distance)
            gives *gap_b*.
    
            Args:
                veh_id:   Vehicle ID of the subject point (its own series is skipped).
                veh_time: Time coordinate of the subject point (seconds).
                veh_dist: Distance coordinate of the subject point (metres).
    
            Returns:
                ``(gap_f, gap_b)`` as positive magnitudes, or ``None`` when no
                candidate was found in that direction.
            """
            gap_f = None   # closest vehicle ahead  (higher distance)
            gap_b = None   # closest vehicle behind (lower distance)
    
            for sid, series in self._series.items():
                if sid == veh_id:
                    continue
    
                t_arr = series['veh_time']
                d_arr = series['veh_dist_trav']
    
                if len(t_arr) == 0:
                    continue
    
                # Series must bracket veh_time for a gap measurement
                if t_arr[0] > veh_time or t_arr[-1] < veh_time:
                    continue
    
                # Locate the insertion point: first index where t_arr[idx] >= veh_time
                idx = int(np.searchsorted(t_arr, veh_time, side='left'))
                idx = min(idx, len(t_arr) - 1)
    
                if t_arr[idx] == veh_time:
                    # Exact time match
                    other_dist = float(d_arr[idx])
                elif idx > 0:
                    lo, hi = idx - 1, idx
                    # Ensure the two bracketing samples are from a continuous segment
                    if (t_arr[hi] - t_arr[lo]) > self._SERIES_SEG_THRESHOLD:
                        continue
                    pct = (veh_time - t_arr[lo]) / (t_arr[hi] - t_arr[lo])
                    other_dist = float(d_arr[lo] + pct * (d_arr[hi] - d_arr[lo]))
                else:
                    # idx == 0 and not an exact match — veh_time < t_arr[0],
                    # but we already filtered this case above, so skip defensively
                    continue
    
                diff = other_dist - veh_dist
    
                if diff > 0:
                    # Vehicle is ahead (higher distance)
                    if gap_f is None or diff < gap_f:
                        gap_f = diff
                elif diff < 0:
                    # Vehicle is behind (lower distance)
                    abs_diff = -diff
                    if gap_b is None or abs_diff < gap_b:
                        gap_b = abs_diff
                # diff == 0: same distance, degenerate — skip
    
            return gap_f, gap_b

    def _find_hway(self, veh_id: int, veh_time: float, veh_dist: float
                      ) -> tuple:
            """Find forward and backward time headway for a trajectory point.
    
            For each other vehicle, scans its trajectory for adjacent sample pairs
            that bracket *veh_dist* (one side <= and the other >=).  For each such
            crossing the time is linearly interpolated, and the crossing closest
            to *veh_time* in each direction is kept.
    
            Because ``veh_dist_trav`` is derived from noisy GPS data, it is NOT
            guaranteed to be monotonically sorted.  A vehicle's distance may
            locally decrease due to measurement noise, so the scan checks every
            consecutive pair rather than using binary search.
    
            The segment-continuity threshold ``_SERIES_SEG_THRESHOLD`` is applied
            to the time gap between bracketing samples to avoid interpolating
            across trajectory gaps (e.g. missing records).
    
            Args:
                veh_id:   Vehicle ID of the subject point (its own series is skipped).
                veh_time: Time coordinate of the subject point (seconds).
                veh_dist: Distance coordinate of the subject point (metres).
    
            Returns:
                ``(hway_f, hway_b)`` as positive magnitudes, or ``None`` when no
                candidate was found in that direction.
    
                * **hway_f** — time since the nearest vehicle crossed *veh_dist*
                  *before* ``veh_time`` (positive; overlay draws left).
                * **hway_b** — time until the nearest vehicle crosses *veh_dist*
                  *after* ``veh_time`` (positive; overlay draws right).
            """
            hway_f = None  # closest crossing before veh_time
            hway_b = None  # closest crossing after veh_time
    
            for sid, series in self._series.items():
                if sid == veh_id:
                    continue
    
                t_arr = series['veh_time']
                d_arr = series['veh_dist_trav']
                n = len(d_arr)
    
                if n < 2:
                    continue
    
                # Quick reject: if the entire distance range can't reach veh_dist
                d_min = float(np.min(d_arr))
                d_max = float(np.max(d_arr))
                if veh_dist < d_min or veh_dist > d_max:
                    continue
    
                # Scan consecutive pairs for crossings of veh_dist
                for i in range(n - 1):
                    d0, d1 = float(d_arr[i]), float(d_arr[i + 1])
    
                    # Check if this pair brackets veh_dist (either direction)
                    if not ((d0 <= veh_dist <= d1) or (d1 <= veh_dist <= d0)):
                        continue
    
                    t0, t1 = float(t_arr[i]), float(t_arr[i + 1])
    
                    # Interpolate the crossing time, clamping to [0, 1]
                    # to guard against noise-amplified extrapolation
                    span = d1 - d0
                    if span == 0.0:
                        pct = 0.5
                    else:
                        pct = max(0.0, min(1.0, (veh_dist - d0) / span))
                    crossing_time = t0 + pct * (t1 - t0)
    
                    diff = veh_time - crossing_time
    
                    if diff > 0:
                        # Crossing was before veh_time → forward headway candidate
                        if hway_f is None or diff < hway_f:
                            hway_f = diff
                    elif diff < 0:
                        # Crossing was after veh_time → backward headway candidate
                        abs_diff = -diff
                        if hway_b is None or abs_diff < hway_b:
                            hway_b = abs_diff
                    # diff == 0: simultaneous — skip
    
            return hway_f, hway_b

    def _test_find_hway(self):
            """Temporary test harness for _find_hway using contrived data.
            
            Three vehicles travelling at constant speed (10 m/s), staggered
            by 2.5 seconds on the time axis:
            
                veh 1: t=[0, 1, 2, 3, 4, 5]  d=[0, 10, 20, 30, 40, 50]
                veh 2: t=[2.5, 3.5, 4.5, 5.5, 6.5, 7.5]  d=[0, 10, 20, 30, 40, 50]
                veh 3: t=[5, 6, 7, 8, 9, 10]  d=[0, 10, 20, 30, 40, 50]
            
            Query point: veh 2 at t=4.5, d=20
                Expected hway_f = 2.5 s (veh 1 crossed d=20 at t=2.0)
                Expected hway_b = 2.5 s (veh 3 crosses d=20 at t=7.0)
            
            Query point: veh 1 at t=2.0, d=20
                Expected hway_f = None  (no vehicle crossed d=20 earlier)
                Expected hway_b = 2.5 s (veh 2 crosses d=20 at t=4.5)
            
            Query point: veh 3 at t=7.0, d=20
                Expected hway_f = 2.5 s (veh 2 crossed d=20 at t=4.5)
                Expected hway_b = None  (no vehicle crosses d=20 later)
            """
            # Save original series
            original_series = self._series
    
            # Inject contrived data
            self._series = {
                1: {
                    'veh_id': 1,
                    'veh_time': np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0]),
                    'veh_dist_trav': np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0]),
                    'plot_item': True,
                    'color': '#ff0000', 'text_color': '#ffffff'
                },
                2: {
                    'veh_id': 2,
                    'veh_time': np.array([2.5, 3.5, 4.5, 5.5, 6.5, 7.5]),
                    'veh_dist_trav': np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0]),
                    'plot_item': True,
                    'color': '#00ff00', 'text_color': '#ffffff'
                },
                3: {
                    'veh_id': 3,
                    'veh_time': np.array([5.0, 6.0, 7.0, 8.0, 9.0, 10.0]),
                    'veh_dist_trav': np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0]),
                    'plot_item': True,
                    'color': '#0000ff', 'text_color': '#ffffff'
                }
            }
    
            print("=" * 60)
            print("_find_hway test harness")
            print("=" * 60)
    
            # Test 1: middle vehicle
            hway_f, hway_b = self._find_hway(2, 4.5, 20.0)
            print(f"\nTest 1: veh=2, t=4.5, d=20.0")
            print(f"  hway_f = {hway_f}  (expected 2.5)")
            print(f"  hway_b = {hway_b}  (expected 2.5)")
            print(f"  PASS: {hway_f == 2.5 and hway_b == 2.5}")
    
            # Test 2: first vehicle
            hway_f, hway_b = self._find_hway(1, 2.0, 20.0)
            print(f"\nTest 2: veh=1, t=2.0, d=20.0")
            print(f"  hway_f = {hway_f}  (expected None)")
            print(f"  hway_b = {hway_b}  (expected 2.5)")
            print(f"  PASS: {hway_f is None and hway_b == 2.5}")
    
            # Test 3: last vehicle
            hway_f, hway_b = self._find_hway(3, 7.0, 20.0)
            print(f"\nTest 3: veh=3, t=7.0, d=20.0")
            print(f"  hway_f = {hway_f}  (expected 2.5)")
            print(f"  hway_b = {hway_b}  (expected None)")
            print(f"  PASS: {hway_f == 2.5 and hway_b is None}")
    
            # Test 4: query at interpolated distance (d=25, between samples)
            # veh 1 crosses d=25 at t=2.5, veh 3 crosses d=25 at t=7.5
            hway_f, hway_b = self._find_hway(2, 5.0, 25.0)
            print(f"\nTest 4: veh=2, t=5.0, d=25.0")
            print(f"  hway_f = {hway_f}  (expected 2.5)")
            print(f"  hway_b = {hway_b}  (expected 2.5)")
            print(f"  PASS: {hway_f == 2.5 and hway_b == 2.5}")
    
            # Test 5: query at distance no other vehicle reaches
            hway_f, hway_b = self._find_hway(1, 2.0, 55.0)
            print(f"\nTest 5: veh=1, t=2.0, d=55.0 (beyond all)")
            print(f"  hway_f = {hway_f}  (expected None)")
            print(f"  hway_b = {hway_b}  (expected None)")
            print(f"  PASS: {hway_f is None and hway_b is None}")
    
            print("\n" + "=" * 60)
    
            # Restore original series
            self._series = original_series

    def _find_hway_brute(self, veh_id: int, veh_time: float, veh_dist: float
                            ) -> tuple:
            """Brute-force forward and backward time headway for a trajectory point.
    
            For every other vehicle, walks every consecutive pair of samples
            (i, i+1).  When veh_dist falls between d[i] and d[i+1] (inclusive,
            either direction to handle noise), the crossing time is linearly
            interpolated from t[i] and t[i+1].  The closest crossing before
            veh_time gives hway_f; the closest crossing after gives hway_b.
    
            Args:
                veh_id:   Vehicle ID of the subject point (skipped in iteration).
                veh_time: Time coordinate of the subject point (seconds).
                veh_dist: Distance coordinate of the subject point (metres).
    
            Returns:
                ``(hway_f, hway_b)`` as positive magnitudes, or ``None`` when no
                candidate was found in that direction.
            """
            hway_f = None
            hway_b = None
    
            for sid, series in self._series.items():
                if sid == veh_id:
                    continue
    
                t_arr = series['veh_time']
                d_arr = series['veh_dist_trav']
                n = len(d_arr)
    
                if n < 2:
                    continue
    
                for i in range(n - 1):
                    d0 = float(d_arr[i])
                    d1 = float(d_arr[i + 1])
                    lo_d = min(d0, d1)
                    hi_d = max(d0, d1)
    
                    # Does veh_dist fall within this pair?
                    if veh_dist < lo_d or veh_dist > hi_d:
                        continue
    
                    t0 = float(t_arr[i])
                    t1 = float(t_arr[i + 1])
    
                    # Interpolate crossing time
                    if d1 == d0:
                        crossing_time = (t0 + t1) * 0.5
                    else:
                        pct = (veh_dist - d0) / (d1 - d0)
                        pct = max(0.0, min(1.0, pct))
                        crossing_time = t0 + pct * (t1 - t0)
    
                    diff = veh_time - crossing_time
    
                    if diff > 0.0:
                        if hway_f is None or diff < hway_f:
                            hway_f = diff
                    elif diff < 0.0:
                        abs_diff = -diff
                        if hway_b is None or abs_diff < hway_b:
                            hway_b = abs_diff
    
            return hway_f, hway_b