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

def _interp_at(
    seg_meta: list[tuple[int, int, float, float]],
    x_arr:    np.ndarray,
    y_arr:    np.ndarray,
    x_query:  float,
) -> float:
    """Linearly interpolate y at x_query using pre-built segment metadata.

    Args:
        seg_meta : list of (start, end_exclusive, seg_min, seg_max) tuples.
        x_arr    : sorted x values (e.g. veh_time).
        y_arr    : corresponding y values (e.g. veh_dist_trav).
        x_query  : value to interpolate at.

    Returns:
        y_interpolated, or nan if x_query is outside every segment.
    """
    for s, e, seg_min, seg_max in seg_meta:
        if x_query < seg_min:
            break
        if x_query > seg_max:
            continue

        xs  = x_arr[s:e]
        idx = int(np.searchsorted(xs, x_query, side="right")) - 1
        idx = max(0, min(idx, len(xs) - 2))

        x0, x1 = xs[idx], xs[idx + 1]
        dx = x1 - x0
        if dx == 0.0:
            return float(y_arr[s + idx])

        t = (x_query - x0) / dx
        return float(y_arr[s + idx] * (1.0 - t) + y_arr[s + idx + 1] * t)

    return float("nan")


def _interp_time_at_dist(
    seg_meta_d: list[tuple[int, int, float, float]],
    d_arr:      np.ndarray,
    t_arr:      np.ndarray,
    subj_dist:  float,
    before:     float | None = None,
    after:      float | None = None,
) -> float:
    """Find the time at which a vehicle crosses subj_dist, with an optional
    time-window constraint to separate lane-change ambiguity.

    Iterates all segments (a vehicle may cross the same distance more than
    once across different lane passages) and returns the crossing time that
    satisfies the time constraint and is closest to the boundary:

        before : crossing must have occurred at time <= before  (forward headway)
        after  : crossing must occur at time >= after           (backward headway)

    Returns the best qualifying crossing time, or nan if none found.

    Args:
        seg_meta_d : segment metadata indexed by distance.
        d_arr      : distance array (generally monotone within each segment).
        t_arr      : time array (sorted).
        subj_dist  : distance value to find a crossing for.
        before     : upper time bound (forward headway constraint).
        after      : lower time bound (backward headway constraint).

    Returns:
        Best crossing time as a float, or nan.
    """
    best = float("nan")
    best_gap = np.inf   # how close the crossing is to the time boundary

    for s, e, dmin, dmax in seg_meta_d:
        if subj_dist < dmin or subj_dist > dmax:
            continue

        d_slice = d_arr[s:e]
        t_slice = t_arr[s:e]

        # searchsorted on the distance slice to bracket the crossing
        idx = int(np.searchsorted(d_slice, subj_dist, side="right")) - 1
        idx = max(0, min(idx, len(d_slice) - 2))

        d0, d1 = d_slice[idx], d_slice[idx + 1]
        dd = d1 - d0
        if dd == 0.0:
            t_cross = float(t_slice[idx])
        else:
            frac    = (subj_dist - d0) / dd
            t_cross = float(t_slice[idx] * (1.0 - frac) + t_slice[idx + 1] * frac)

        if not np.isfinite(t_cross):
            continue

        if before is not None:
            if t_cross > before:
                continue
            gap = before - t_cross      # smaller = closer to subj_time
        elif after is not None:
            if t_cross < after:
                continue
            gap = t_cross - after
        else:
            gap = 0.0

        if gap < best_gap:
            best_gap = gap
            best     = t_cross

    return best

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
        self._selected_series = set()
        self._speed_gradient_enabled = False
        self._last_mouse_move_time = 0.0
        self._lane_ids = []
        self._veh_ids = []
        self._gh_mode_enabled       = False
        self._gh_locked_point       = None   # (veh_id, veh_time) | None
        self._gh_overlay_items      = []     # transient hover items
        self._gh_locked_items       = []     # persistent locked-point items

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

    def _build_series_segments(self, veh_id: int) -> None:
        """Build and cache segment metadata for a vehicle's time and distance arrays.

        Segments are contiguous runs where the time gap between consecutive
        points is less than _SERIES_SEG_THRESHOLD seconds.  Two metadata
        lists are stored on the series dict:

            'seg_meta_t'  — segments indexed by veh_time   (for gap lookup)
            'seg_meta_d'  — segments indexed by veh_dist_trav (for headway lookup)

        Each entry is (start, end_exclusive, range_min, range_max).

        Args:
            veh_id: Key into self._series.
        """
        series = self._series.get(veh_id)
        if series is None:
            return

        t = series['veh_time']
        d = series['veh_dist_trav']

        if len(t) < 2:
            series['seg_meta_t'] = []
            series['seg_meta_d'] = []
            return

        # ── segment boundaries on time ────────────────────────────────────
        diffs  = np.diff(t)
        breaks = np.where(diffs > self._SERIES_SEG_THRESHOLD)[0] + 1
        starts = np.concatenate(([0], breaks))
        ends   = np.concatenate((breaks, [len(t)]))

        seg_pairs = [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= 2]

        # meta_t: ranges are over time (t is sorted)
        series['seg_meta_t'] = [
            (s, e, float(t[s]), float(t[e - 1]))
            for s, e in seg_pairs
        ]

        # meta_d: ranges are over distance — distance is generally monotone
        # within a lane but may not be perfectly sorted, so use min/max.
        series['seg_meta_d'] = [
            (s, e, float(np.min(d[s:e])), float(np.max(d[s:e])))
            for s, e in seg_pairs
        ]

        logger.debug(
            f"Built {len(seg_pairs)} segment(s) for veh {veh_id} "
            f"({len(t)} points, threshold={self._SERIES_SEG_THRESHOLD} s)"
        )

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
        self._build_series_segments(veh_id) # series segments for gap/headway

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
            # Hover: show transient overlay for closest unlocked point.
            # If the hovered point IS the locked point, skip the hover draw
            # (the locked lines already cover it) but still clear any stale
            # hover lines left from a previously hovered point.
            if closest_data and min_dist < pixel_tolerance_show:
                veh_id, _, _, veh_time, veh_dist = closest_data
                current_point = (veh_id, veh_time, self._current_event_id)
                is_locked = (self._gh_locked_point == (veh_id, veh_time))

                if is_locked:
                    # Hovering the locked point — clear stale hover lines only
                    self._clear_gh_overlay()
                    self._last_queried_point = current_point
                elif self._last_queried_point != current_point:
                    try:
                        cursor = self._db_connection.cursor()
                        cursor.execute("""
                            SELECT gap_b, gap_f, hway_b, hway_f
                            FROM   events
                            WHERE  event_id = ? AND veh_id = ? AND veh_time = ?
                        """, (self._current_event_id, veh_id, veh_time))
                        row = cursor.fetchone()
                    except Exception as e:
                        logger.error(f"GH hover query error: {e}")
                        return

                    self._clear_gh_overlay()
                    if row:
                        self._draw_gh_overlay(veh_id, veh_time, veh_dist, *row,
                                              target_list=self._gh_overlay_items)
                        self._last_queried_point = current_point
                    else:
                        self._last_queried_point = None

            elif min_dist > pixel_tolerance_hide or not closest_data:
                self._clear_gh_overlay()
                self._last_queried_point = None

            self._tooltip.hide()
            return

        # ── normal tooltip mode (unchanged) ──────────────────────────────
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

    def _clear_gh_locked(self):
        """Remove persistent locked-point gap/headway lines and labels from the plot."""
        for item in self._gh_locked_items:
            self._scatter_plot.removeItem(item)
        self._gh_locked_items.clear()

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
        if not checked:
            self._clear_gh_overlay()
            self._clear_gh_locked()
            self._gh_locked_point = None
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
            import math
    
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

    def _find_gap_headway(
        self,
        subj_time:   float,
        subj_series: dict,
    ) -> tuple[float, float, float, float]:
        """Compute gap and time-headway at a single subject-vehicle point.

        Definitions (matching the import-time computation):
            gap_f   — forward  spatial gap  (m): distance to the nearest vehicle
                      ahead  (higher dist) at the same time.  Positive.
            gap_b   — backward spatial gap  (m): distance to the nearest vehicle
                      behind (lower  dist) at the same time.  Positive.
            hway_f  — forward  time headway (s): time since the nearest vehicle
                      ahead crossed the subject's current distance.  Positive.
            hway_b  — backward time headway (s): time until the nearest vehicle
                      behind crosses the subject's current distance.  Positive.

        Gap and headway are computed independently across all other series so
        that a lane-change by another vehicle does not corrupt either result.
        The gap search finds the closest vehicle ahead/behind at subj_time.
        The headway search finds the most recent crossing of subj_dist before
        subj_time (forward) and the next crossing after subj_time (backward),
        across all other series regardless of which vehicle won the gap search.

        Args:
            subj_time   : veh_time of the clicked/hovered point (seconds).
            subj_series : the _series dict entry for the subject vehicle.

        Returns:
            (gap_f, hway_f, gap_b, hway_b) — NaN for any value that cannot
            be determined from the loaded series data.
        """
        t_subj = subj_series['veh_time']
        d_subj = subj_series['veh_dist_trav']
        subj_id = subj_series['veh_id']

        # ── 1.  Subject distance at subj_time ────────────────────────────
        meta_t_subj = subj_series.get('seg_meta_t', [])
        subj_dist = _interp_at(meta_t_subj, t_subj, d_subj, subj_time)

        if not np.isfinite(subj_dist):
            return float("nan"), float("nan"), float("nan"), float("nan")

        # ── 2.  Gap pass: interpolate every other series at subj_time ────
        # Collect signed deltas so we can sort for the headway pass.
        gap_candidates: list[tuple[float, dict]] = []   # (signed_delta, series)

        for other in self._series.values():
            if other['veh_id'] == subj_id:
                continue
            meta_t = other.get('seg_meta_t')
            if not meta_t:
                continue
            d_other = _interp_at(meta_t, other['veh_time'], other['veh_dist_trav'],
                                  subj_time)
            if np.isfinite(d_other):
                gap_candidates.append((d_other - subj_dist, other))

        # Best gap values
        fwd = [(delta, s) for delta, s in gap_candidates if delta >= 0]
        bwd = [(delta, s) for delta, s in gap_candidates if delta <  0]

        gap_f = min((d for d, _ in fwd), default=float("nan"))
        gap_b = min((-d for d, _ in bwd), default=float("nan"))

        # ── 3.  Headway pass: search series sorted by gap proximity ──────
        # Starting from the closest vehicle in each direction means the first
        # valid crossing sets a tight time bound that prunes later searches.
        # Forward headway — sort ascending by gap delta (closest ahead first)
        fwd_sorted = sorted(fwd, key=lambda x: x[0])
        # Backward headway — sort ascending by abs gap delta (closest behind first)
        bwd_sorted = sorted(bwd, key=lambda x: -x[0])

        hway_f    = float("nan")
        best_hf   = np.inf

        for _, other in fwd_sorted:
            d_o     = other['veh_dist_trav']
            t_o     = other['veh_time']
            meta_d  = other.get('seg_meta_d')
            if not meta_d:
                continue
            t_cross = _interp_time_at_dist(
                meta_d, d_o, t_o, subj_dist,
                before=subj_time,
            )
            if np.isfinite(t_cross):
                delta_hf = subj_time - t_cross
                if delta_hf < best_hf:
                    best_hf = delta_hf
                    hway_f  = delta_hf
                    # Any further series is at least as far ahead spatially;
                    # their headway crossing cannot be more recent than this.
                    break

        hway_b    = float("nan")
        best_hb   = np.inf

        for _, other in bwd_sorted:
            d_o     = other['veh_dist_trav']
            t_o     = other['veh_time']
            meta_d  = other.get('seg_meta_d')
            if not meta_d:
                continue
            t_cross = _interp_time_at_dist(
                meta_d, d_o, t_o, subj_dist,
                after=subj_time,
            )
            if np.isfinite(t_cross):
                delta_hb = t_cross - subj_time
                if delta_hb < best_hb:
                    best_hb = delta_hb
                    hway_b  = delta_hb
                    break

        logger.debug(
            f"_find_gap_headway veh={subj_id} t={subj_time:.3f}: "
            f"gf={gap_f:.3f} hf={hway_f:.3f} gb={gap_b:.3f} hb={hway_b:.3f}"
        )
        return gap_f, hway_f, gap_b, hway_b