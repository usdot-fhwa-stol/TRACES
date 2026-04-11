import math
import logging
import time
import numpy as np
import sqlite3
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
    QLabel, QComboBox, QDialog)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeyEvent, QAction, QActionGroup
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
        _mode_menu (QMenu): Application menu to update with plot interaction modes.
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
        _min_time_span (float): Minimum allowed time slider span for zoom limiting.
        _min_dist_span (float): Minimum allowed distance slider span for zoom limiting.
    """

    _DIMMED_COLOR  = '#a9a9a9'
    
    series_color = [
        '#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', 
        '#42d4f4', '#f032e6', '#bfef45', '#469990', '#9A6324', '#800000', 
        '#808000', '#000075', '#000000'
    ]

    series_paint: dict[str, tuple] = {
        c: (pg.mkPen(color=c), pg.mkBrush(color=c)) for c in series_color
    }
    series_paint[_DIMMED_COLOR] = (pg.mkPen(color=_DIMMED_COLOR), pg.mkBrush(color=_DIMMED_COLOR))
    
    text_color = [
        '#ffffff', '#ffffff', '#000000', '#ffffff', '#ffffff', '#ffffff', 
        '#000000', '#ffffff', '#000000', '#ffffff', '#ffffff', '#ffffff', 
        '#ffffff', '#ffffff', '#ffffff'
    ]

    _SERIES_SEG_THRESHOLD = 0.3   # seconds; max intra-segment time gap
    _BASE_MIN_SPAN = 10.0          # absolute floor for the smaller axis

    def __init__(self, parent=None, mode_menu=None):
        """Initialize the TimeSpacePlotWidget.
        
        Args:
            parent: Parent widget (typically MainWindow).
        """
        super().__init__(parent)
        self._mode_menu = mode_menu
        self._db_connection = None
        self._scatter_plot = None
        self._event_button = None
        self._lane_combobox = None
        self._dist_slider = None
        self._time_slider = None
        self._vertical_gradient = None
        self._speed_toggle = None
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
        self._gh_mode_enabled = False
        self._gh_overlay_items = [] # transient hover items
        self._gh_locked_items = {}  # dict: (veh_id, veh_time) -> list[pg items]
        self._min_time_span = self._BASE_MIN_SPAN
        self._min_dist_span = self._BASE_MIN_SPAN
    
        logger.debug("TimeSpacePlotWidget initialized")
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Set up the widget UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)
        
        # Left side: QVBoxLayout with vertical speed gradient and toggle switches
        left_layout = QVBoxLayout()
        
        # Vertical speed color gradient
        gradient_config = GradientConfig(title="Speed m/s", ranges=[[0, 40, 0, 100, 5]], paint_step=0.1)
        self._vertical_gradient = VerticalGradientValue(gradient_config)
        left_layout.addWidget(self._vertical_gradient, stretch=1)

        # Show speed gradient toggle switch
        self._speed_toggle = ToggleSwitch()
        self._speed_toggle.toggled.connect(self._on_speed_gradient_toggled)
        speed_toggle_label = QLabel("Show Speed") # speed toggle label
        left_layout.addWidget(speed_toggle_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        left_layout.addWidget(self._speed_toggle, alignment=Qt.AlignmentFlag.AlignHCenter)

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

        self._compute_min_spans()

        # Set initial plot view to match slider ranges
        self._scatter_plot.getViewBox().setRange(
            xRange=[time_min, time_max],
            yRange=[dist_min, dist_max],
            padding=0
        )
        
        self._setup_mode_menu()
        
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
                'veh_dist_trav': np.array([], dtype=np.float64),
                'veh_speed': np.array([], dtype=np.float64),
                'segments': []
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
            data = self._lane_combobox.currentData()
            self._lane_combobox.setToolTip(f"Orig: {data['orig_lane_id']}")
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

        # Derive contiguous segments based on time gap threshold.
        # Each segment is a tuple:
        #   (start_idx, end_idx, t_min, t_max, d_min, d_max)
        # where start_idx is inclusive, end_idx is exclusive.
        segments = []
        seg_start = 0
        for j in range(1, len(times)):
            if (times[j] - times[j - 1]) > self._SERIES_SEG_THRESHOLD:
                # Close current segment
                seg_times = times[seg_start:j]
                seg_dists = distances[seg_start:j]
                segments.append((
                    seg_start, j,
                    float(seg_times[0]), float(seg_times[-1]),
                    float(np.min(seg_dists)), float(np.max(seg_dists)),
                    times[seg_start:j],         # seg_t view
                    distances[seg_start:j]      # seg_d view
                ))
                seg_start = j
        # Close final segment
        if seg_start < len(times):
            seg_times = times[seg_start:]
            seg_dists = distances[seg_start:]
            segments.append((
                seg_start, len(times),
                float(seg_times[0]), float(seg_times[-1]),
                float(np.min(seg_dists)), float(np.max(seg_dists)),
                times[seg_start:],          # seg_t view
                distances[seg_start:]       # seg_d view
            ))
        series['segments'] = segments

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
                series['veh_speed'] = np.array([], dtype=np.float64)
                series['segments'] = []

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
        """Handle changes to distance or time sliders.

        If either slider is narrower than its minimum span the range is
        expanded symmetrically around its center (clamped to slider limits)
        and written back to the slider.  The plot view is then updated.
        """
        if not self._series:
            return

        self._clear_selection_box()

        time_lo, time_hi = self._time_slider.value()
        dist_lo, dist_hi = self._dist_slider.value()

        # ── enforce minimum time span ────────────────────────────────
        if (time_hi - time_lo) < self._min_time_span:
            center = (time_lo + time_hi) / 2.0
            half = self._min_time_span / 2.0
            time_lo = center - half
            time_hi = center + half

            # clamp to slider hard limits
            s_min = self._time_slider.minimum()
            s_max = self._time_slider.maximum()
            if time_lo < s_min:
                time_lo = s_min
                time_hi = time_lo + self._min_time_span
            if time_hi > s_max:
                time_hi = s_max
                time_lo = time_hi - self._min_time_span
            time_lo = max(time_lo, s_min)

            self._time_slider.blockSignals(True)
            self._time_slider.setValue((time_lo, time_hi))
            self._time_slider.blockSignals(False)

        # ── enforce minimum distance span ────────────────────────────
        if (dist_hi - dist_lo) < self._min_dist_span:
            center = (dist_lo + dist_hi) / 2.0
            half = self._min_dist_span / 2.0
            dist_lo = center - half
            dist_hi = center + half

            s_min = self._dist_slider.minimum()
            s_max = self._dist_slider.maximum()
            if dist_lo < s_min:
                dist_lo = s_min
                dist_hi = dist_lo + self._min_dist_span
            if dist_hi > s_max:
                dist_hi = s_max
                dist_lo = dist_hi - self._min_dist_span
            dist_lo = max(dist_lo, s_min)

            self._dist_slider.blockSignals(True)
            self._dist_slider.setValue((dist_lo, dist_hi))
            self._dist_slider.blockSignals(False)

        # ── update plot view ─────────────────────────────────────────
        self._scatter_plot.getViewBox().setRange(
            xRange=[time_lo, time_hi],
            yRange=[dist_lo, dist_hi],
            padding=0
        )

        logger.debug(f"Applied slider filters: time=[{time_lo}, {time_hi}], "
                      f"dist=[{dist_lo}, {dist_hi}]")

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
                        gap_f, gap_b, hway_f, hway_b = self._find_gap_hway(
                            veh_id, veh_time, veh_dist
                        )
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
            self._tooltip.adjustSize()
            tip_w = self._tooltip.width()
            tip_h = self._tooltip.height()
            plot_rect = self._scatter_plot.rect()

            cursor_x = int(pos.x())
            cursor_y = int(pos.y())
            margin = 10

            # Horizontal: prefer right of cursor, flip to left if clipped
            if cursor_x + margin + tip_w > plot_rect.width():
                tip_x = cursor_x - margin - tip_w
            else:
                tip_x = cursor_x + margin

            # Vertical: prefer below cursor, flip to above if clipped
            if cursor_y + margin + tip_h > plot_rect.height():
                tip_y = cursor_y - margin - tip_h
            else:
                tip_y = cursor_y + margin

            # Clamp so it never goes past the top-left either
            tip_x = max(0, tip_x)
            tip_y = max(0, tip_y)

            self._tooltip.move(tip_x, tip_y)
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
        by its speed via the vertical gradient. When off, trajectories return
        to their flat per-vehicle series color. Either way only the brushes
        are updated in-place; no database round-trip is needed.

        Args:
            checked (bool): True when the toggle is switched on.
        """
        self._speed_gradient_enabled = checked
        if self._series:
            QTimer.singleShot(0, self._update_plot_colors)
        logger.debug(f"Speed gradient toggled: {checked}")

    def _get_point_brushes(self, veh_id: int):
        """Return per-point (pens, brushes) lists for a vehicle.
    
        All pen/brush objects are sourced from pre-built lookup dicts —
        no pg.mkPen or pg.mkBrush calls are made at paint time.
    
        Decision tree:
          1. Dimmed (unselected while a selection exists) → flat dimmed colour.
          2. Speed-gradient mode active → per-point colour from gradient lookup,
             clamped to the configured range (no missing-colour concept).
          3. Default → flat per-vehicle series colour.
    
        Args:
            veh_id (int): Vehicle ID.
    
        Returns:
            tuple[list, list]: (pens, brushes) — one entry per data point.
        """
        series    = self._series[veh_id]
        n         = len(series['veh_time'])
        is_dimmed = bool(self._selected_series) and veh_id not in self._selected_series
    
        if is_dimmed:
            p, b = self.series_paint[self._DIMMED_COLOR]
            return [p] * n, [b] * n
    
        if self._speed_gradient_enabled:
            speeds  = series.get('veh_speed')
            pens    = []
            brushes = []
            for i in range(n):
                s = float(speeds[i]) if (speeds is not None and i < len(speeds)
                                         and not np.isnan(speeds[i])) else 0.0
                p, b = self._vertical_gradient.get_paint(s)
                pens.append(p)
                brushes.append(b)
            return pens, brushes
    
        p, b = self.series_paint[series['color']]
        return [p] * n, [b] * n

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

    def _setup_mode_menu(self):
        """Populate and enable the Mode menu with exclusive interaction mode actions.

        Called from _apply_settings the first time data is loaded so the menu
        is only active while a plot is displayed.  The menu is cleared and
        rebuilt each time to ensure a clean state when a new dataset is opened.

        The two mutually exclusive modes are:
            - Select Trajectory (default): normal lasso / click selection.
            - Gap Headway: hover / click gap and headway overlay.

        A QActionGroup enforces that exactly one action is checked at all times.
        The widget state (_gh_mode_enabled) is synchronised immediately so the
        menu reflects the current mode even before the user changes it.
        """
        if self._mode_menu is None:
            return

        self._mode_menu.clear()

        action_group = QActionGroup(self._mode_menu)
        action_group.setExclusive(True)

        select_action = QAction("Select Trajectory", self._mode_menu)
        select_action.setCheckable(True)
        select_action.setChecked(True)
        select_action.triggered.connect(
            lambda checked: self._on_mode_changed("select") if checked else None
        )
        action_group.addAction(select_action)
        self._mode_menu.addAction(select_action)

        gh_action = QAction("Gap Headway", self._mode_menu)
        gh_action.setCheckable(True)
        gh_action.setChecked(False)
        gh_action.triggered.connect(
            lambda checked: self._on_mode_changed("gap_headway") if checked else None
        )
        action_group.addAction(gh_action)
        self._mode_menu.addAction(gh_action)

        # Ensure widget state matches the default checked action.
        self._gh_mode_enabled = False

        self._mode_menu.setEnabled(True)
        logger.debug("Mode menu populated and enabled")

    def _on_mode_changed(self, mode: str):
        """Handle interaction mode changes driven by the Mode menu.

        Replaces _on_gap_headway_toggled. Keeps _gh_mode_enabled as the
        single source of truth consumed by CustomViewBox and the mouse
        handlers, so no other code needs to change.

        When enabled:
          - Normal trajectory selection is suppressed.
          - Hovering a point draws live gap/headway lines.
          - Clicking a point locks those lines; clicking again unlocks.

        When disabled:
          - All overlay graphics are removed.
          - Normal selection behaviour is restored.

        Args:
            mode (str): Either "select" or "gap_headway".
        """
        if mode == "gap_headway":
            self._gh_mode_enabled = True
            logger.debug("Mode changed to: Gap Headway")
        else:
            self._gh_mode_enabled = False
            self._clear_gh_overlay()
            self._clear_gh_all_locked()
            self._tooltip.hide()
            logger.debug("Mode changed to: Select Trajectory")

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
            NUDGE_PX = 10
            LINE_PEN    = pg.mkPen(color=(120, 120, 120), width=3,
                                   style=Qt.PenStyle.SolidLine)
            LABEL_COLOR = (80, 80, 80)
    
            def _add_line(x0, y0, x1, y1):
                item = pg.PlotDataItem([x0, x1], [y0, y1], pen=LINE_PEN)
                self._scatter_plot.addItem(item)
                target_list.append(item)

            def _add_label(x, y, text, anchor, angle=0, dx_px=0, dy_px=0):
                item = pg.TextItem(text=text, color=LABEL_COLOR, anchor=anchor,
                                   angle=angle)
                item.setFont(pg.QtGui.QFont("Arial", 8))
                if dx_px != 0 or dy_px != 0:
                    px_w, px_h = self._scatter_plot.getViewBox().viewPixelSize()
                    x = x + dx_px * px_w
                    y = y + dy_px * px_h
                item.setPos(x, y)
                self._scatter_plot.addItem(item)
                target_list.append(item)
                
            # ── vertical lines (gap — same time, different distance) ─────────────
            #
            # gap_f: other vehicle is at higher dist at same time → line goes UP
            #        label: rotated 90°, right of and above source point
            # gap_b: other vehicle is at lower dist at same time  → line goes DOWN
            #        label: rotated 90°, left of and below source point
    
            if math.isfinite(gap_f):
                y_end = veh_dist + gap_f
                _add_line(veh_time, veh_dist, veh_time, y_end)
                _add_label(veh_time, veh_dist,
                           f"{gap_f:.2f} m",
                           anchor=(0.0, 0.0),
                           angle=90,
                           dy_px=NUDGE_PX)
    
            if math.isfinite(gap_b):
                y_end = veh_dist - gap_b
                _add_line(veh_time, veh_dist, veh_time, y_end)
                _add_label(veh_time, veh_dist,
                           f"{gap_b:.2f} m",
                           anchor=(1.0, 1.0),
                           angle=90,
                           dy_px=-NUDGE_PX)
    
            # ── horizontal lines (headway — same distance, different time) ───────
            #
            # hway_f: other vehicle passed this distance earlier → line goes LEFT
            #         label: left of and above source point
            # hway_b: other vehicle passed this distance later   → line goes RIGHT
            #         label: right of and below source point
    
            if math.isfinite(hway_f):
                x_end = veh_time - hway_f
                _add_line(veh_time, veh_dist, x_end, veh_dist)
                _add_label(veh_time, veh_dist,
                           f"{hway_f:.2f} s",
                           anchor=(1.0, 1.0),
                           dx_px=-NUDGE_PX)
    
            if math.isfinite(hway_b):
                x_end = veh_time + hway_b
                _add_line(veh_time, veh_dist, x_end, veh_dist)
                _add_label(veh_time, veh_dist,
                           f"{hway_b:.2f} s",
                           anchor=(0.0, 0.0),
                           dx_px=NUDGE_PX)
    
            logger.debug(
                f"Drew GH overlay for veh {veh_id} at t={veh_time:.2f}: "
                f"gap_b={gap_b}, gap_f={gap_f}, hway_b={hway_b}, hway_f={hway_f}"
            )

    def _find_gap_hway(self, veh_id: int, veh_time: float, veh_dist: float
                           ) -> tuple:
            """Find gap and headway in a single pass over segments.
    
            For each other vehicle's segment, first attempts a gap measurement
            if the segment brackets veh_time.  When a gap is found the segment
            extents are narrowed:
    
            - Forward gap (vehicle ahead at veh_time): it hasn't reached veh_dist
              yet, so the crossing must be after veh_time.  Narrow the search
              window to [gap_idx, seg_end) by advancing seg_start and updating
              t_min / d_min.
            - Backward gap (vehicle behind at veh_time): it already passed
              veh_dist, so the crossing was before veh_time.  Narrow the search
              window to [seg_start, gap_idx+2) by pulling back seg_end and
              updating t_max / d_max.
    
            After narrowing, forward headway (hway_f) and backward headway
            (hway_b) are each checked independently against the (possibly
            narrowed) extents and scanned in their respective directions.
            Segments where no gap was found still get the full headway scan
            with original extents.
    
            Returns ``float('inf')`` for any direction with no candidate.
            ``_draw_gh_overlay`` already handles this via its ``_finite()``
            helper which returns ``None`` for non-finite values.
    
            Args:
                veh_id:   Vehicle ID of the subject point (skipped).
                veh_time: Time coordinate of the subject point (seconds).
                veh_dist: Distance coordinate of the subject point (metres).
    
            Returns:
                ``(gap_f, gap_b, hway_f, hway_b)`` as positive magnitudes,
                or ``float('inf')`` for any direction with no candidate.
            """
            INF = float('inf')
            gap_f = INF
            gap_b = INF
            hway_f = INF
            hway_b = INF
    
            for sid, series in self._series.items():
                if sid == veh_id:
                    continue
    
                t_arr = series['veh_time']
                d_arr = series['veh_dist_trav']

                for seg_start, seg_end, t_min, t_max, d_min, d_max, seg_t, seg_d in series.get('segments', []):
    
                    # ── Gap check: does this segment bracket veh_time? ────────
                    if t_min <= veh_time <= t_max:
                        idx = int(np.searchsorted(seg_t, veh_time, side='left'))
                        idx = min(idx, len(seg_t) - 1)
    
                        if seg_t[idx] == veh_time:
                            diff = float(seg_d[idx]) - veh_dist
                            abs_idx = seg_start + idx
                        else:
                            lo, hi = idx - 1, idx
                            pct = (veh_time - seg_t[lo]) / (seg_t[hi] - seg_t[lo])
                            diff = float(seg_d[lo] + pct * (seg_d[hi] - seg_d[lo])) - veh_dist
                            abs_idx = seg_start + lo

                        if diff > 0:
                            if diff < gap_f:
                                gap_f = diff
                            # Vehicle ahead → crossing is after veh_time
                            seg_end = min(abs_idx + 2, seg_end)
                            t_max = float(t_arr[seg_end - 1])
                            d_max = float(d_arr[seg_end - 1])

                        elif diff < 0:
                            abs_diff = -diff
                            if abs_diff < gap_b:
                                gap_b = abs_diff
                            # Vehicle behind → crossing was before veh_time
                            seg_start = abs_idx
                            t_min = float(t_arr[abs_idx])
                            d_min = float(d_arr[abs_idx])
    
                    # ── Forward headway: scan backward for crossing before veh_time
                    if d_min <= veh_dist <= d_max:
                        for i in range(seg_end - 2, seg_start - 1, -1):
                            d0 = float(d_arr[i])
                            d1 = float(d_arr[i + 1])
                            if not ((d0 <= veh_dist <= d1) or (d1 <= veh_dist <= d0)):
                                continue
                            t0 = float(t_arr[i])
                            t1 = float(t_arr[i + 1])
                            span = d1 - d0
                            if span == 0.0:
                                pct = 1.0
                            else:
                                pct = max(0.0, min(1.0, (veh_dist - d0) / span))
                            crossing_time = t0 + pct * (t1 - t0)
                            diff = veh_time - crossing_time
                            if diff > 0.0:
                                if diff < hway_f:
                                    hway_f = diff
                                break
    
                    # ── Backward headway: scan forward for crossing after veh_time
                    if d_min <= veh_dist <= d_max:
                        for i in range(seg_start, seg_end - 1):
                            d0 = float(d_arr[i])
                            d1 = float(d_arr[i + 1])
                            if not ((d0 <= veh_dist <= d1) or (d1 <= veh_dist <= d0)):
                                continue
                            t0 = float(t_arr[i])
                            t1 = float(t_arr[i + 1])
                            span = d1 - d0
                            if span == 0.0:
                                pct = 0.0
                            else:
                                pct = max(0.0, min(1.0, (veh_dist - d0) / span))
                            crossing_time = t0 + pct * (t1 - t0)
                            diff = crossing_time - veh_time
                            if diff > 0.0:
                                if diff < hway_b:
                                    hway_b = diff
                                break
    
            return gap_f, gap_b, hway_f, hway_b

    def _compute_min_spans(self):
        """Compute aspect-ratio-aware minimum spans from the current slider limits.

        The shorter axis gets _BASE_MIN_SPAN and the longer axis gets a
        proportionally larger floor so both hit their zoom limit at the
        same relative scale.  Results are stored on the widget so both the
        CustomViewBox wheel handler and the slider handler can use them.
        """
        t_total = self._time_slider.maximum() - self._time_slider.minimum()
        d_total = self._dist_slider.maximum() - self._dist_slider.minimum()

        if t_total > 0 and d_total > 0:
            ratio = max(t_total, d_total) / min(t_total, d_total)
            if t_total >= d_total:
                self._min_time_span = self._BASE_MIN_SPAN * ratio
                self._min_dist_span = self._BASE_MIN_SPAN
            else:
                self._min_time_span = self._BASE_MIN_SPAN
                self._min_dist_span = self._BASE_MIN_SPAN * ratio
        else:
            self._min_time_span = self._BASE_MIN_SPAN
            self._min_dist_span = self._BASE_MIN_SPAN