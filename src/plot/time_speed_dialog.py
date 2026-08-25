"""
Base Time Plot Dialog and specialized classes for speed and acceleration plots.

This module provides a base TimePlotDialog class and specialized subclasses
for displaying time-speed and time-acceleration plots with interactive tooltips.
"""

import logging
import math
import time
from typing import Iterable, Optional, Tuple

import numpy as np
import sqlite3
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QVBoxLayout, QLabel
import pyqtgraph as pg
from superqt import QLabeledDoubleRangeSlider
from utils import (
    events_columns, active_rows_clause, measured_rows_clause,
    event_time_step, gap_indices,
)
from .slider_view_box import SliderViewBox, enforce_min_span

# Set up logger
logger = logging.getLogger(__name__)


def data_extent(arrays: Iterable[np.ndarray]) -> Optional[Tuple[int, int]]:
    """(floor(min), ceil(max)) over the finite values of all *arrays* --
    every sample of every vehicle, across all of its gaps -- or None when
    there are none. Widened to at least 1 unit so a single-valued series
    still gives the slider a usable range."""
    lo, hi = math.inf, -math.inf
    for a in arrays:
        a = np.asarray(a, dtype=np.float64)
        a = a[np.isfinite(a)]
        if a.size:
            lo = min(lo, float(a.min()))
            hi = max(hi, float(a.max()))
    if not math.isfinite(lo):
        return None
    lo, hi = math.floor(lo), math.ceil(hi)
    if hi <= lo:
        hi = lo + 1
    return lo, hi


def initial_window(window: Optional[Tuple[float, float]],
                   extent: Tuple[float, float]) -> Tuple[float, float]:
    """Starting slider position: *window* (the caller's view) clipped to
    *extent* (the slider limits). The whole extent when there is no window
    or it doesn't overlap the extent -- the caller was looking at a span
    where the selected vehicles have no data."""
    lo, hi = extent
    if window is None:
        return lo, hi
    w_lo, w_hi = sorted(window)
    c_lo, c_hi = max(w_lo, lo), min(w_hi, hi)
    if c_hi <= c_lo:
        return lo, hi
    return c_lo, c_hi


class TimeSpeedDialog(QDialog):
    """Base dialog class for displaying time-based plots for multiple vehicles.

    This dialog creates a scatter plot showing vehicle data over time
    with interactive tooltips that display detailed vehicle information.
    Each vehicle is drawn as one PlotDataItem: a marker at every sample
    and a thin line joining consecutive samples, broken wherever
    utils.gap_indices finds a gap (the app-wide gap rule, using the
    event's sampling interval from event_time_step). Gaps in the
    measured data therefore show as breaks rather than being bridged.

    Rows are filtered the same way as TimeSpacePlotWidget: synthetic fill
    rows (measured_rows_clause) and trajectories consumed by a stitch
    (active_rows_clause) are never plotted.

    The time and y sliders own the visible range. Both slider ranges
    (limits) are set from the loaded data -- every sample of every
    vehicle, on both sides of any gap. The time slider opens on the time
    window passed in (the time-space plot's current slider window),
    clipped to the data, so the dialog starts near what the user was
    viewing while every sample stays reachable; if that window holds no
    data, it opens on all of it. The y slider opens on its full range.
    The plot's SliderViewBox
    turns left-drag (pan) and scroll-wheel (zoom) gestures into slider
    changes; over an axis, either gesture affects only that axis. Both
    axes have a minimum span (zoom limit) enforced for gestures and for
    direct slider edits alike.

    Attributes:
        db_connection (sqlite3.Connection): Database connection for querying.
        event_id (int): Event ID to filter data.
        veh_ids (list): List of vehicle IDs to display.
        series_colors (list): List of HTML hex colors for each series.
        text_colors (list): List of text colors for tooltips.
        _plot (pg.PlotWidget): PyQtGraph plot widget.
        _view_box (SliderViewBox): Plot view box driving the sliders.
        _tooltip (QLabel): Tooltip for displaying point information.
        _last_queried_point (tuple): Tracks tooltip point to reduce queries.
        _last_mouse_move_time (float): Timestamp of last mouse move for debouncing.
        _vehicle_data (dict): Dictionary storing plot data for each vehicle.
        _events_cols (set): Events-table columns, refreshed on each data load.
        _time_step (float): Event sampling interval used to break lines at gaps.
    """

    # Subclasses should override these class attributes
    Y_COLUMN = "veh_speed"
    Y_LABEL = "Speed (m/s)"
    PLOT_TITLE_SUFFIX = "Speed vs Time"
    TOLERANCE = 2.0  # Tolerance for point detection
    POINT_SIZE = 4   # Scatter point size in pixels (matches TimeSpacePlotWidget)
    LINE_WIDTH = 2   # line joining consecutive samples, in pixels

    # Zoom limits. Each axis' minimum span is MIN_SPAN_FRACTION of its full
    # slider range, so with the wheel over the plot both axes reach their
    # limit together; the absolute floors guard very small ranges.
    MIN_SPAN_FRACTION = 0.02
    MIN_TIME_SPAN = 0.5   # s
    MIN_Y_SPAN = 0.5      # Y units (m/s here)

    def __init__(self, parent, db_connection, event_id, veh_ids, lane_ids, time_min, time_max, series_colors, text_colors):
        """Initialize the TimeSpeedDialog.

        Args:
            parent: Parent widget
            db_connection (sqlite3.Connection): Database connection
            event_id (int): Event ID to filter by
            veh_ids (list): List of vehicle IDs to display
            lane_ids (list): Lane IDs offered in the lane combobox
            time_min, time_max (float): Initial time slider position,
                clipped to the data; the slider's limits come from the
                data (or this window, if no data loads)
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
        self._initial_time_window = (time_min, time_max)
        self._lane_label_items = []

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

        # Guard: the Y column is optional under the config-driven import
        # schema. The context menu disables the opening action when it is
        # absent, but this protects any other entry point.
        self._events_cols = events_columns(db_connection)
        if self.Y_COLUMN not in self._events_cols:
            self._setup_unavailable_ui()
            return

        self._time_step = event_time_step(db_connection, event_id)
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

    def _setup_unavailable_ui(self):
        """Show an explanation instead of the plot when the Y column is
        absent from this dataset."""
        layout = QVBoxLayout(self)
        message = QLabel(
            f"This dataset does not contain the {self.Y_COLUMN} column, "
            f"so the {self.PLOT_TITLE_SUFFIX} plot is not available."
        )
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        layout.addWidget(message)

    def _setup_ui(self, lane_ids, time_min, time_max):
        """Set up the dialog UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)

        # Left side: QVBoxLayout with plot on top and time slider below
        left_layout = QVBoxLayout()

        # Create plot. SliderViewBox disables pyqtgraph's own mouse
        # handling and the view-box menu, and turns drag/wheel gestures
        # into slider changes.
        self._view_box = SliderViewBox()
        self._plot = pg.PlotWidget(viewBox=self._view_box)
        self._plot.setBackground('w')
        self._plot.showGrid(x=True, y=True)
        self._plot.setLabel('left', self.Y_LABEL)
        self._plot.setLabel('bottom', 'Time (s)')

        # Connect mouse move event for tooltips
        self._plot.scene().sigMouseMoved.connect(self._handle_mouse_moved)

        # Disable auto-range button
        self._plot.getPlotItem().hideButtons()

        left_layout.addWidget(self._plot, stretch=1)

        # Time controls below plot
        time_layout = QHBoxLayout()

        # Time label
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_layout.addWidget(time_label)

        # Time slider. Double-valued so panning and zooming are smooth on
        # the short time windows typical of a few selected vehicles.
        self._time_slider = QLabeledDoubleRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Horizontal)
        self._time_slider.setDecimals(1)
        time_layout.addWidget(self._time_slider, stretch=1)

        # Provisional: the limits become the data's extent once loaded
        # (_fit_sliders_to_data), with this window as the position.
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

        # Let the view box drive the sliders, and set initial zoom limits
        # (recomputed once both ranges are known from the data).
        self._view_box.bind_sliders(self._time_slider, self._y_slider)
        self._compute_min_spans()

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

    def _compute_min_spans(self):
        """Set each axis' zoom floor to MIN_SPAN_FRACTION of its full slider
        range, but never below the axis' absolute floor. Using the same
        fraction for both axes means a wheel zoom over the plot reaches
        both limits at the same relative scale, as in TimeSpacePlotWidget.
        """
        t_total = self._time_slider.maximum() - self._time_slider.minimum()
        y_total = self._y_slider.maximum() - self._y_slider.minimum()
        self._view_box.set_min_spans(
            max(t_total * self.MIN_SPAN_FRACTION, self.MIN_TIME_SPAN),
            max(y_total * self.MIN_SPAN_FRACTION, self.MIN_Y_SPAN),
        )

    def _row_filter_clause(self):
        """SQL fragment applying the same row filters as TimeSpacePlotWidget:
        skip synthetic fill rows and rows of trajectories consumed by a
        stitch. Each helper returns "" when its status column is absent,
        and otherwise a fragment starting with ' AND'.

        Returns:
            str: SQL fragment to append to a WHERE clause.
        """
        return (measured_rows_clause(self._events_cols)
                + active_rows_clause(self._events_cols))

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
            SELECT veh_id, veh_time, {self.Y_COLUMN}, lane_id
            FROM events
            WHERE event_id = ?
            AND veh_id IN ({veh_ids})
            AND lane_id IN ({lane_ids})
            AND {self.Y_COLUMN} IS NOT NULL{self._row_filter_clause()}
            ORDER BY veh_id, veh_time
        """

    def _get_tooltip_query(self):
        """Get the SQL query for tooltip data.

        The Y column is guaranteed present (guarded in __init__), but the
        other optional tooltip columns may be absent; they are selected as
        NULL so the row shape stays constant for the unpacking code.

        The same row filters as the data query are applied, so the nearest
        row returned for the tooltip is always one that is actually plotted
        (never an unplotted synthetic fill row within the time window).

        Returns:
            str: SQL query string
        """
        optional = ", ".join(
            col if col in self._events_cols else f"NULL AS {col}"
            for col in ("veh_speed", "veh_accel", "veh_automation", "veh_length")
        )
        return f"""
            SELECT veh_id, veh_time, lane_id, {self.Y_COLUMN}, veh_dist_trav, {optional}
            FROM events
            WHERE event_id = ? AND veh_id = ?
            AND veh_time BETWEEN ? AND ?
            AND {self.Y_COLUMN} IS NOT NULL{self._row_filter_clause()}
            ORDER BY ABS(veh_time - ?) + ABS({self.Y_COLUMN} - ?)
            LIMIT 1
        """

    def _load_and_plot_data(self, reset_view=True):
        """Load data from database and create a scatter plot item per vehicle."""
        try:
            # Clear previous plots and state
            self._plot.clear()
            self._vehicle_data.clear()
            self._last_queried_point = None

            # Re-query columns per load: analysis modules may add the
            # imputed_status / traj_status columns during a session.
            self._events_cols = events_columns(self.db_connection)

            cursor = self.db_connection.cursor()
            cursor.execute(self._get_data_query(), [self.event_id])

            # Process data by vehicle using streaming approach
            current_veh_id = None
            current_times = []
            current_y_values = []
            current_lane_ids = []
            
            def plot_vehicle_data(veh_id, times, y_values, lane_ids_for_vehicle):
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
                lane_array = np.array(lane_ids_for_vehicle)
                
                # Store data for tooltip queries
                self._vehicle_data[veh_id] = {
                    'times': times_array,
                    'y_values': y_array,
                    'lane_ids': lane_array,
                    'color': color
                }

                # One item per vehicle: a marker at each sample and a line
                # joining consecutive samples, broken at every gap
                # (connect[i] False = no line from sample i to i+1).
                connect = np.ones(times_array.size, dtype=bool)
                connect[gap_indices(times_array, self._time_step)] = False
                plot_item = pg.PlotDataItem(
                    times_array, y_array,
                    connect=connect,
                    pen=pg.mkPen(color=color, width=self.LINE_WIDTH),
                    symbol='o',
                    symbolSize=self.POINT_SIZE,
                    symbolPen=pg.mkPen(color=color),
                    symbolBrush=pg.mkBrush(color=color),
                )
                plot_item.veh_id = veh_id   # Store vehicle ID on plot item
                self._plot.addItem(plot_item)

                logger.debug(f"Plotted {len(times)} {self.Y_COLUMN} points for vehicle {veh_id}")

            # Stream through database results
            for row in cursor:
                veh_id, veh_time, y_value, lane_id = row
                
                # Check if we've moved to a new vehicle
                if veh_id != current_veh_id:
                    # Plot previous vehicle if we have data
                    if current_veh_id is not None and current_times:
                        plot_vehicle_data(
                            current_veh_id,
                            current_times,
                            current_y_values,
                            current_lane_ids,
                        )
                    
                    # Start collecting data for new vehicle
                    current_veh_id = veh_id
                    current_times = []
                    current_y_values = []
                    current_lane_ids = []
                
                # Add current record to collections
                current_times.append(veh_time)
                current_y_values.append(y_value)
                current_lane_ids.append(lane_id)
            
            # Handle the last vehicle after loop ends
            if current_veh_id is not None and current_times:
                plot_vehicle_data(
                    current_veh_id,
                    current_times,
                    current_y_values,
                    current_lane_ids,
                )

            if reset_view:
                self._fit_sliders_to_data()

            self._add_lane_annotations()

            logger.debug(f"Loaded {self.Y_COLUMN} data for {len(self._vehicle_data)} vehicles")

        except sqlite3.Error as e:
            logger.error(f"Error loading {self.Y_COLUMN} data: {e}")

    def _fit_sliders_to_data(self):
        """Set both sliders' ranges (limits) to the full extent of the
        loaded data -- all samples of all vehicles, before and after every
        gap. The time slider is positioned on the initial time window
        clipped to that extent (initial_window); the y slider shows its
        full range. An axis with no data keeps its current range."""
        data = self._vehicle_data.values()
        t_extent = data_extent(d['times'] for d in data)
        y_extent = data_extent(d['y_values'] for d in data)
        if t_extent is None and y_extent is None:
            return
        t_value = (None if t_extent is None
                   else initial_window(self._initial_time_window, t_extent))

        # Ranges first, then zoom limits (they depend on the full ranges),
        # then values, so _on_slider_changed sees the new limits. Signals
        # are blocked while setting ranges: setRange can clamp the current
        # value and fire a change before the other slider is ready.
        for slider, extent in ((self._time_slider, t_extent), (self._y_slider, y_extent)):
            if extent is not None:
                slider.blockSignals(True)
                slider.setRange(*extent)
                slider.blockSignals(False)
        self._compute_min_spans()
        for slider, value in ((self._time_slider, t_value), (self._y_slider, y_extent)):
            if value is not None:
                slider.setValue(value)
        self._on_slider_changed()   # in case neither value actually changed

    def _on_slider_changed(self):
        """Handle slider changes (direct edits, pans and zooms alike).

        A window narrower than its axis' minimum span is widened about its
        center (clamped to the slider limits) and written back to the
        slider, then the plot view is set to the two slider windows.
        """
        time_min, time_max = enforce_min_span(self._time_slider, self._view_box.min_x_span)
        y_min, y_max = enforce_min_span(self._y_slider, self._view_box.min_y_span)

        # Update plot view to match slider ranges
        self._view_box.setRange(
            xRange=[time_min, time_max],
            yRange=[y_min, y_max],
            padding=0
        )
        self._update_lane_label_positions(y_max)
        
        logger.debug(f"Applied slider filters: time=[{time_min}, {time_max}], y=[{y_min}, {y_max}]")

    def _add_lane_annotations(self):
        """Add dashed vertical lane-boundary markers and lane labels."""
        self._lane_label_items.clear()
        if len(self._vehicle_data) != 1:
            return

        y_min, y_max = self._y_slider.value()
        label_y = self._lane_label_y_position(y_min, y_max)
        line_pen = pg.mkPen(color=(0, 0, 0, 170), width=1, style=Qt.PenStyle.DashLine)

        for veh_id, data in self._vehicle_data.items():
            times = data["times"]
            lanes = data["lane_ids"]
            if len(times) == 0 or len(lanes) == 0:
                continue

            boundary_times = [float(times[0])]
            segment_lanes = [lanes[0]]
            for idx in range(1, len(lanes)):
                if lanes[idx] != lanes[idx - 1]:
                    boundary_times.append(float(times[idx]))
                    segment_lanes.append(lanes[idx])
            boundary_times.append(float(times[-1]))

            for boundary_time in boundary_times:
                marker = pg.InfiniteLine(
                    pos=boundary_time,
                    angle=90,
                    movable=False,
                    pen=line_pen,
                )
                marker.setZValue(5)
                self._plot.addItem(marker)

            for idx, lane_id in enumerate(segment_lanes):
                start_time = boundary_times[idx]
                end_time = boundary_times[idx + 1]
                label = pg.TextItem(
                    text=f"Lane {lane_id}",
                    color=(0, 0, 0),
                    anchor=(0.5, 0),
                )
                label.setPos((start_time + end_time) / 2.0, label_y)
                label.setZValue(10)
                self._plot.addItem(label)
                self._lane_label_items.append(label)

        self._update_lane_label_positions(y_max)

    def _update_lane_label_positions(self, y_max):
        """Keep lane labels at the top of the current y-axis range."""
        y_min, _ = self._y_slider.value()
        label_y = self._lane_label_y_position(y_min, y_max)
        for label in self._lane_label_items:
            label.setY(label_y)

    @staticmethod
    def _lane_label_y_position(y_min, y_max):
        """Place lane labels just inside the top of the current y range."""
        return y_max - (y_max - y_min) * 0.03

    def _handle_mouse_moved(self, pos):
        """Handle mouse movement for showing tooltips with hysteresis.

        Args:
            pos (QPointF): Mouse position in scene coordinates.
        """
        # No tooltips while the user is dragging the plot.
        if self._view_box.is_panning:
            self._tooltip.hide()
            self._last_queried_point = None
            return

        # Debounce to limit processing frequency
        current_time = time.time()
        if (current_time - self._last_mouse_move_time) < 0.05:  # 50ms debounce
            return
        self._last_mouse_move_time = current_time

        mouse_point = self._view_box.mapSceneToView(pos)
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

