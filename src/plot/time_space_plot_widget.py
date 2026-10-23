import logging
import time
import numpy as np
import sqlite3
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
    QLabel, QComboBox, QDialog)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QAction, QActionGroup
import pyqtgraph as pg
from superqt import QLabeledRangeSlider, QLabeledSlider
from ui_kit.vertical_gradient_value import GradientConfig, VerticalGradientValue
from ui_kit.toggle_switch import ToggleSwitch
from ui_kit.plot_style import (RED, GREEN, YELLOW, BLUE, ORANGE, PURPLE, CYAN, 
    MAGENTA, LIME, TEAL, BROWN, MAROON, OLIVE, NAVY, GREY, WHITE, BLACK)
from .time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
from .custom_view_box import CustomViewBox
from .slider_view_box import enforce_min_span
from .handlers.interaction_mode import InteractionMode, InteractionModeHandler
from .handlers.gap_headway_handler import GapHeadwayModeHandler
from .handlers.shockwave_handler import ShockwaveSemiAutoModeHandler
from .handlers.stitch_select_handler import StitchSelectModeHandler
from utils import event_time_step, segment_bounds

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
        _time_step (float): Event sampling interval used for segments.
    """

    vehicle_ids_changed = pyqtSignal()   # trajectories added/removed (e.g. stitching)

    _HANDLER_FACTORIES = {
        InteractionMode.SELECT:              InteractionModeHandler,
        InteractionMode.GAP_HEADWAY:         GapHeadwayModeHandler,
        # Shockwave modes always receive a pre-constructed handler from
        # _on_shockwave_action_triggered; factory entries are fallback only.
        InteractionMode.SHOCKWAVE_SEMI_AUTO: ShockwaveSemiAutoModeHandler,
        # InteractionMode.SHOCKWAVE_MANUAL:       ShockwaveManualModeHandler,   # Phase 2
        # InteractionMode.SHOCKWAVE_AUTOMATIC:    ShockwaveAutomaticModeHandler, # Phase 3
    }

    # ShockwaveConfigDialog method key -> interaction mode.
    _SHOCKWAVE_MODES = {
        "manual":    InteractionMode.SHOCKWAVE_MANUAL,
        "semi_auto": InteractionMode.SHOCKWAVE_SEMI_AUTO,
        "automatic": InteractionMode.SHOCKWAVE_AUTOMATIC,
    }

    _DIMMED_COLOR  = GREY

    series_color = [
        RED, GREEN, YELLOW, BLUE, ORANGE, PURPLE, CYAN, MAGENTA, 
        LIME, TEAL, BROWN, MAROON, OLIVE, NAVY, BLACK
    ]

    series_paint: dict[str, tuple] = {
        c: (pg.mkPen(color=c), pg.mkBrush(color=c)) for c in series_color
    }
    series_paint[_DIMMED_COLOR] = (pg.mkPen(color=_DIMMED_COLOR), pg.mkBrush(color=_DIMMED_COLOR))
    
    text_color = [
        WHITE, WHITE, BLACK, WHITE, WHITE, WHITE, BLACK, WHITE, 
        BLACK, WHITE, WHITE, WHITE, WHITE, WHITE, WHITE
    ]

    _BASE_MIN_SPAN = 10.0          # absolute floor for the smaller axis
    _RANGE_VALUE_STYLE = (
        "background-color: #c8c8c8; "
        "color: #000000; "
        "border: 1px solid #000000; "
        "border-radius: 2px; "
        "padding: 0px 0px;"
    )

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
        self._interaction_mode = InteractionMode.SELECT
        self._active_handler   = InteractionModeHandler()
        self._min_time_span = self._BASE_MIN_SPAN
        self._min_dist_span = self._BASE_MIN_SPAN
        self._search_action = None  # View > Search Vehicle QAction, owned by MainWindow
        self._mode_actions       = {}    # InteractionMode → QAction
        self._shockwave_action   = None  # stored for cancel-restore
        self._shockwave_slider_widget = None  # created in _setup_ui
        self._stitch_target_resolver = None   # set by MainWindow
        self._tooltip_select = ("lane_id, veh_speed, veh_accel, "
                                "veh_automation, veh_length")
        self._time_step = None

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
        self._style_editable_range_slider(self._time_slider)
        self._time_slider.valueChanged.connect(self._on_slider_changed)
        time_layout.addWidget(self._time_slider, stretch=1)
        
        mid_layout.addLayout(time_layout)

        # ── Shockwave analysis sliders (hidden until Shockwave mode is active) ──
        sw_container = QWidget()
        sw_container.setVisible(False)
        sw_layout = QVBoxLayout(sw_container)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setSpacing(2)
        
        slope_row = QHBoxLayout()
        slope_label = QLabel("Min Slope Change")
        slope_label.setStyleSheet("font-weight: bold;")
        slope_row.addWidget(slope_label)
        self._slope_slider = QLabeledSlider()   # single-handle
        self._slope_slider.setOrientation(Qt.Orientation.Horizontal)
        self._slope_slider.setRange(0, 95)
        self._slope_slider.setValue(80)
        self._slope_slider.sliderReleased.connect(self._on_slope_slider_released)
        slope_row.addWidget(self._slope_slider, stretch=1)
        sw_layout.addLayout(slope_row)
        
        vicinity_row = QHBoxLayout()
        vicinity_label = QLabel("Vicinity")
        vicinity_label.setStyleSheet("font-weight: bold;")
        vicinity_row.addWidget(vicinity_label)
        self._vicinity_slider = QLabeledSlider()
        self._vicinity_slider.setOrientation(Qt.Orientation.Horizontal)
        self._vicinity_slider.setRange(1, 50)      # ×0.01 to get 0.01–0.50
        self._vicinity_slider.setValue(5)          # default 0.05
        self._vicinity_slider.setEnabled(False)   # enabled only for Automatic (Phase 3)
        self._vicinity_slider.sliderReleased.connect(self._on_vicinity_slider_released)
        vicinity_row.addWidget(self._vicinity_slider, stretch=1)
        sw_layout.addLayout(vicinity_row)
        
        self._shockwave_slider_widget = sw_container
        mid_layout.addWidget(sw_container)

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
        self._style_editable_range_slider(self._dist_slider)
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

    def _style_editable_range_slider(self, slider):
        """Style only the editable min/max edge values as visible input boxes."""
        for label_name in ("_min_label", "_max_label"):
            label = getattr(slider, label_name, None)
            if label is not None:
                label.setStyleSheet(self._RANGE_VALUE_STYLE)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        slider.setToolTip("Edit the minimum or maximum range value.")
        
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

        # Only hand control of the action to the dialog if we're currently
        # in Select mode -- otherwise it's already (correctly) disabled by
        # the active non-Select mode and must stay that way regardless of
        # whether the dialog is accepted or cancelled.
        search_action = self._search_action if self._interaction_mode == InteractionMode.SELECT else None

        settings_dialog = TimeSpacePlotSettingsDialog(parent=self, db_connection=self._db_connection, 
            defaults = {"event_id": self._current_event_id, 
                "dist_range": self._dist_range, 
                "time_range": self._time_range},
            search_action=search_action)
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
        self._time_step = event_time_step(self._db_connection, event_id)
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
        self._style_editable_range_slider(self._dist_slider)
    
        # Set time slider range and values
        self._time_slider.setRange(time_min, time_max)
        self._time_slider.setValue(self._time_range)
        self._style_editable_range_slider(self._time_slider)

        self._compute_min_spans()

        # Set initial plot view to match slider ranges
        self._scatter_plot.getViewBox().setRange(
            xRange=[time_min, time_max],
            yRange=[dist_min, dist_max],
            padding=0
        )
        
        self._setup_mode_menu()
        self._update_search_action_for_mode(InteractionMode.SELECT)

    def _new_series_dict(self, veh_id: int, index: int) -> dict:
        return {
            'veh_id': veh_id,
            'color': self.series_color[index % len(self.series_color)],
            'text_color': self.text_color[index % len(self.text_color)],
            'plot_item': None,
            'veh_time': np.array([], dtype=np.float64),
            'veh_dist_trav': np.array([], dtype=np.float64),
            'veh_speed': np.array([], dtype=np.float64),
            'segments': []
        }

    def _initialize_series_from_ids(self):
        """Initialize the _series dictionary using the provided vehicle IDs."""
        self._series = {veh_id: self._new_series_dict(veh_id, index)
                        for index, veh_id in enumerate(self._veh_ids)}
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

        # Contiguous segments split at time gaps (utils.segment_bounds).
        # Each: (start_idx, end_idx, t_min, t_max, d_min, d_max, seg_t, seg_d),
        # start inclusive, end exclusive; seg_t / seg_d are views.
        segments = []
        for start, end in segment_bounds(times, self._time_step):
            seg_t = times[start:end]
            seg_d = distances[start:end]
            segments.append((start, end,
                             float(seg_t[0]), float(seg_t[-1]),
                             float(np.min(seg_d)), float(np.max(seg_d)),
                             seg_t, seg_d))
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
            self._active_handler.on_before_data_reload(self)
            self._scatter_plot.clear()
            self._clear_selection_box()
            self._active_handler.on_plot_cleared(self)

            for series in self._series.values():
                series['plot_item'] = None
                series['veh_time'] = np.array([], dtype=np.float64)
                series['veh_dist_trav'] = np.array([], dtype=np.float64)
                series['veh_speed'] = np.array([], dtype=np.float64)
                series['segments'] = []

            time_min, time_max = self._time_slider.value()
            dist_min, dist_max = self._dist_slider.value()

            from utils import events_columns, active_rows_clause, measured_rows_clause
            cols = events_columns(self._db_connection)

            # Gate speed-dependent features on column presence. veh_speed is
            # optional under the config-driven import schema.
            has_speed = "veh_speed" in cols
            self._speed_toggle.setEnabled(has_speed)
            self._vertical_gradient.setEnabled(has_speed)
            if not has_speed and self._speed_gradient_enabled:
                self._speed_toggle.setChecked(False)
                self._speed_gradient_enabled = False
            speed_expr = "veh_speed" if has_speed else "NULL AS veh_speed"

            # Cache the optional-column SELECT expressions for the hover
            # tooltip query. These columns are import-time only (never added
            # by analysis modules), so caching per data reload is safe.
            self._tooltip_select = ", ".join(
                col if col in cols else f"NULL AS {col}"
                for col in ("lane_id", "veh_speed", "veh_accel",
                            "veh_automation", "veh_length")
            )

            want_spline = self._active_handler.wants_spline_position_data(self)
            if want_spline and "calc_position" in cols:
                position_col = "calc_position"
                # Include spline-imputed fill rows too -- that's the point of
                # this mode -- but skip rows the spline never fit at all.
                where_extra = " AND calc_position IS NOT NULL"
            else:
                position_col = "veh_dist_trav"
                # Outside spline mode, never plot synthetic fill rows as if
                # they were measured trajectory points.
                where_extra = measured_rows_clause(cols)
            # Trajectories consumed by a stitch are never plotted.
            where_extra += active_rows_clause(cols)

            cursor = self._db_connection.cursor()
            cursor.execute(f"""
                SELECT veh_id, veh_time, {position_col}, {speed_expr}
                FROM events
                WHERE event_id = ? AND lane_id = ?{where_extra}
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

            self._active_handler.on_data_reloaded(self)

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

        time_lo, time_hi = enforce_min_span(self._time_slider, self._min_time_span)
        dist_lo, dist_hi = enforce_min_span(self._dist_slider, self._min_dist_span)

        self._scatter_plot.getViewBox().setRange(
            xRange=[time_lo, time_hi],
            yRange=[dist_lo, dist_hi],
            padding=0
        )

        logger.debug(f"Applied slider filters: time=[{time_lo}, {time_hi}], "
                      f"dist=[{dist_lo}, {dist_hi}]")

    def _nearest_point(self, mx: float, my: float, time_tolerance: float = 0.05):
        """Nearest plotted point to (mx, my) in view coordinates, searching
        only points within ±time_tolerance of mx. Shared by hover, context
        menu hit-testing, and mode handlers (e.g. stitch selection).

        Returns:
            (series, veh_time, veh_dist_trav, dist_distance) or None.
        """
        if not self._series:
            return None
        best = None
        for series in self._series.values():
            if not series["plot_item"] or len(series["veh_time"]) == 0:
                continue
            mask = ((series["veh_time"] >= mx - time_tolerance) &
                    (series["veh_time"] <= mx + time_tolerance))
            x_f = series["veh_time"][mask]
            if len(x_f) == 0:
                continue
            y_f = series["veh_dist_trav"][mask]
            distances = np.abs(y_f - my)
            idx = np.argmin(distances)
            if best is None or distances[idx] < best[3]:
                best = (series, x_f[idx], y_f[idx], distances[idx])
        return best

    def find_vehicle_at(self, mx: float, my: float, tolerance: float = 3.0):
        """Vehicle ID of the plotted point nearest (mx, my), or None if
        nothing lies within *tolerance* (view units, same scale the
        context menu has always used)."""
        hit = self._nearest_point(mx, my)
        if hit is None or hit[3] >= tolerance:
            return None
        return hit[0]["veh_id"]

    def set_db_connection(self, conn):
        """Point the widget at a different (content-identical) database
        without reloading, so sliders, view range, lane, selection, and
        color mode are all preserved. Everything here reads
        self._db_connection per operation, so swapping it is enough."""
        self._db_connection = conn
        self._last_queried_point = None   # tooltip cache refers to the old file

    # ── Trajectory stitching ─────────────────────────────────────────────

    def start_stitch_selection(self, veh_id: int):
        """Enter stitch selection mode with *veh_id* as the first trajectory."""
        if self._interaction_mode != InteractionMode.SELECT:
            return
        self._on_mode_changed(InteractionMode.STITCH_SELECT,
                              handler=StitchSelectModeHandler(veh_id))

    def end_stitch_selection(self):
        """Leave stitch selection mode and return to Select."""
        if self._interaction_mode != InteractionMode.STITCH_SELECT:
            return
        action = self._mode_actions.get(InteractionMode.SELECT)
        if action is not None:
            action.setChecked(True)   # setChecked doesn't emit triggered
        self._on_mode_changed(InteractionMode.SELECT)

    def stitch_target_resolver(self):
        """Write-target resolver for the preview dialog, or None to use
        the dialog's in-place default."""
        return self._stitch_target_resolver

    def refresh_after_stitch(self, new_veh_id: int, removed_veh_ids):
        """Show a just-applied stitch without disturbing the view: sliders,
        view range, lane, and color mode are untouched. Removed sources
        are dropped, the new trajectory is added and selected."""
        self.refresh_after_stitch_change([new_veh_id], removed_veh_ids, [new_veh_id])

    def refresh_after_stitch_change(self, added_veh_ids, removed_veh_ids,
                                    select_veh_ids=()):
        """Show stitching changes -- a stitch, an undo, or an undo followed
        by a new stitch -- without disturbing the view: sliders, view range,
        lane, and color mode are untouched. Removed trajectories are
        dropped, added ones (a new stitch, or the sources an undo restored)
        are added, and those of *select_veh_ids* plotted on this lane are
        selected."""
        import json
        row = self._db_connection.execute(
            "SELECT summary_json FROM ranges WHERE event_id = ?",
            (self._current_event_id,)).fetchone()
        if row:
            self._veh_ids = json.loads(row[0]).get('veh_id', self._veh_ids)

        for veh_id in removed_veh_ids:
            self._series.pop(veh_id, None)
            self._selected_series.discard(veh_id)
        for veh_id in added_veh_ids:
            if veh_id not in self._series:
                index = self._veh_ids.index(veh_id) if veh_id in self._veh_ids else len(self._series)
                self._series[veh_id] = self._new_series_dict(veh_id, index)

        self._update_plot_data()
        selected = {v for v in select_veh_ids
                    if v in self._series and self._series[v]['plot_item'] is not None}
        if selected:
            self._selected_series = selected
            self._update_plot_colors()
        logger.info(f"Plot refreshed after stitching change: removed {sorted(removed_veh_ids)}, "
                    f"added {sorted(added_veh_ids)}")
        self.vehicle_ids_changed.emit()

    def _handle_mouse_moved(self, pos):
        """Handle mouse movement calling mode-specific handler.

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

        # ── find closest point (shared by all modes) ─────────────────────
        hit = self._nearest_point(mx, my, time_tolerance)
        if hit is not None:
            series, hit_t, hit_d, min_dist = hit
            closest_data = (series["veh_id"], series["color"],
                            series["text_color"], hit_t, hit_d)
        else:
            min_dist, closest_data = float('inf'), None

        # ── mode handler ──────────────────────────────────────────────
        if self._active_handler.on_hover(self, pos, closest_data, min_dist):
            return

        # ── normal tooltip mode ──────────────────────────────
        if not hasattr(self, '_last_queried_point'):
            self._last_queried_point = None

        if closest_data and min_dist < pixel_tolerance_show:
            veh_id, color, text_color, veh_time, veh_dist_trav = closest_data
            current_point = (veh_id, veh_time, self._current_event_id)

            if self._last_queried_point != current_point:
                cursor = self._db_connection.cursor()
                tooltip_select = getattr(
                    self, "_tooltip_select",
                    "lane_id, veh_speed, veh_accel, veh_automation, veh_length"
                )
                cursor.execute(f"""
                    SELECT {tooltip_select}
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
        routed_keys = (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if event.key() in routed_keys:
            if self._active_handler.on_key_press(self, event):
                event.accept()
                return
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

    def _setup_mode_menu(self):
        """Populate and enable the Mode menu with exclusive interaction mode actions.

        Called from _apply_settings the first time data is loaded so the menu
        is only active while a plot is displayed.  The menu is cleared and
        rebuilt each time to ensure a clean state when a new dataset is opened.

        The two mutually exclusive modes are:
            - Time-Space Diagram (default): normal lasso / click selection.
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

        select_action = QAction("Time-Space Diagram", self._mode_menu)
        select_action.setCheckable(True)
        select_action.setChecked(True)
        select_action.triggered.connect(
            lambda checked: self._on_mode_changed(InteractionMode.SELECT) if checked else None
        )
        action_group.addAction(select_action)
        self._mode_menu.addAction(select_action)

        gh_action = QAction("Gap Headway", self._mode_menu)
        gh_action.setCheckable(True)
        gh_action.setChecked(False)
        gh_action.triggered.connect(
            lambda checked: self._on_mode_changed(InteractionMode.GAP_HEADWAY) if checked else None
        )
        action_group.addAction(gh_action)
        self._mode_menu.addAction(gh_action)

        shockwave_action = QAction("Shockwave", self._mode_menu)
        shockwave_action.setCheckable(True)
        shockwave_action.setChecked(False)
        shockwave_action.triggered.connect(
            lambda checked: self._on_shockwave_action_triggered() if checked else None
        )
        action_group.addAction(shockwave_action)
        self._mode_menu.addAction(shockwave_action)
        self._shockwave_action = shockwave_action

        # Track QActions by mode for cancel-restore.
        self._mode_actions = {
            InteractionMode.SELECT:              select_action,
            InteractionMode.GAP_HEADWAY:         gh_action,
            InteractionMode.SHOCKWAVE_MANUAL:    shockwave_action,
            InteractionMode.SHOCKWAVE_SEMI_AUTO: shockwave_action,
            InteractionMode.SHOCKWAVE_AUTOMATIC: shockwave_action,
            InteractionMode.STITCH_SELECT:       select_action,
        }
        self._interaction_mode = InteractionMode.SELECT
        self._active_handler   = InteractionModeHandler()

        self._mode_menu.menuAction().setVisible(True)
        logger.debug("Mode menu populated and enabled")

    def _update_search_action_for_mode(self, mode: InteractionMode):
        """Vehicle search only makes sense in Select mode, since it relies on
        the default lasso/click selection to highlight the found trajectory.
        Enable the Search Vehicle action only when returning to Select (and
        only if an event is active); disable it for every other mode.
        MainWindow reactively closes an open search dialog whenever this
        action becomes disabled (see MainWindow._on_search_action_changed),
        so no separate close call is needed here.

        Args:
            mode (InteractionMode): The mode just switched to.
        """
        if self._search_action is None:
            return
        self._search_action.setEnabled(
            mode == InteractionMode.SELECT and self._current_event_id is not None
        )

    def _on_mode_changed(self, mode: InteractionMode, handler=None):
        """Handle interaction mode changes driven by the Mode menu.

        Args:
            mode (InteractionMode enum): handles select, gap headway, shockwave.
        """
        self._active_handler.on_exit(self)
        self._active_handler = handler or self._HANDLER_FACTORIES.get(
            mode, InteractionModeHandler
        )()
        self._interaction_mode = mode
        self._active_handler.on_enter(self)
        self._update_search_action_for_mode(mode)
        self._update_plot_data()   # position column may depend on the new handler
        logger.debug(f"Mode changed to: {mode.value}")

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

    def _on_slope_slider_released(self):
        value = float(self._slope_slider.value())
        self._active_handler.on_slope_changed(self, value)
    
    def _on_vicinity_slider_released(self):
        value = self._vicinity_slider.value() * 0.01
        self._active_handler.on_vicinity_changed(self, value)

    def _on_shockwave_action_triggered(self):
        """Show ShockwaveConfigDialog; activate mode on OK, restore previous on Cancel."""
        from analysis.shockwave import ShockwaveConfigDialog
        dlg = ShockwaveConfigDialog(self, self._db_connection, self._current_event_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            mode    = self._SHOCKWAVE_MODES[dlg.selected_method()]
            handler = ShockwaveSemiAutoModeHandler(
                slope_percentile=dlg.selected_slope_percentile(),
                vicinity=dlg.selected_vicinity(),
                use_spline_data=(dlg.selected_data_mode() == "spline"),
            )
            # Also sync the slope slider to the config dialog's chosen value.
            self._slope_slider.setValue(int(dlg.selected_slope_percentile()))
            self._on_mode_changed(mode, handler=handler)
        else:
            # Restore the previously active mode's action check state.
            prev = self._mode_actions.get(self._interaction_mode)
            if prev:
                prev.setChecked(True)

    def set_search_action(self, search_action):
        """Store the View > Search Vehicle QAction so it can be handed to
        TimeSpacePlotSettingsDialog, which owns enabling/disabling it.

        Args:
            search_action (QAction): The Search Vehicle menu action.
        """
        self._search_action = search_action
        logger.debug("Search action registered")

    def get_vehicle_ids(self) -> list:
        """Return the full list of vehicle ids for the current event.

        Returns:
            list[int]: Vehicle ids, independent of lane.
        """
        return list(self._veh_ids)

    def select_searched_vehicle(self, veh_id: int):
        """Locate and select a vehicle trajectory found via the vehicle
        search dialog.

        If the vehicle already has loaded data on the current lane, it is
        selected in place. Otherwise the lane of its first temporal
        occurrence is found and loaded first. Either way, once the series
        data is available the time and distance sliders are snapped to the
        vehicle's exact trajectory extent.

        Args:
            veh_id (int): Vehicle id chosen in the VehicleSearchDialog.
        """
        if not self._db_connection or self._current_event_id is None or veh_id not in self._series:
            logger.warning(f"Cannot search for vehicle {veh_id}: no active event")
            return

        series = self._series.get(veh_id)
        if series is not None and len(series['veh_time']) > 0:
            self._clear_selection_box()
            self._frame_and_select_vehicle(veh_id, series)
            return

        lane_id = self._find_first_lane_for_vehicle(veh_id)
        if lane_id is None:
            logger.warning(f"Vehicle {veh_id} not found in event {self._current_event_id}")
            return

        self._switch_lane_and_select_vehicle(lane_id, veh_id)

    def _find_first_lane_for_vehicle(self, veh_id: int):
        """Find the lane where the vehicle first appears temporally.

        Args:
            veh_id (int): Vehicle id to locate.

        Returns:
            int or None: lane_id of first temporal occurrence, or None if not found.
        """
        try:
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT lane_id FROM events
                WHERE event_id = ? AND veh_id = ?
                ORDER BY veh_time
                LIMIT 1
            """, (self._current_event_id, veh_id))
            result = cursor.fetchone()
            return result[0] if result else None
        except sqlite3.Error as e:
            logger.error(f"Error finding first lane for vehicle {veh_id}: {e}")
            return None

    def _switch_lane_and_select_vehicle(self, lane_id: int, veh_id: int):
        """Switch the lane combobox to `lane_id`, synchronously reload plot
        data, then select and frame `veh_id` using its freshly loaded series.

        Args:
            lane_id (int): Lane to switch to.
            veh_id (int): Vehicle id to select once data is loaded.
        """
        index = -1
        for i in range(self._lane_combobox.count()):
            if self._lane_combobox.itemData(i)['lane_id'] == lane_id:
                index = i
                break

        if index < 0:
            logger.warning(f"Lane {lane_id} not found in lane combobox")
            return

        # Change lane without letting _on_lane_changed's deferred
        # QTimer.singleShot reload race with the synchronous one below.
        self._lane_combobox.blockSignals(True)
        self._lane_combobox.setCurrentIndex(index)
        self._lane_combobox.blockSignals(False)
        data = self._lane_combobox.currentData()
        self._lane_combobox.setToolTip(f"Orig: {data['orig_lane_id']}")

        self._clear_selection_box()
        self._update_plot_data()

        series = self._series.get(veh_id)
        if series is None or len(series['veh_time']) == 0:
            logger.warning(f"Vehicle {veh_id} has no data on lane {lane_id}")
            return

        self._frame_and_select_vehicle(veh_id, series)

    def _frame_and_select_vehicle(self, veh_id: int, series: dict):
        """Snap the time/distance sliders to a vehicle's exact trajectory
        extent and select it, dimming all other trajectories.

        Slider *ranges* are only expanded if the trajectory falls completely
        outside them; slider *values* are always set to the trajectory's
        exact extent (no padding).

        Args:
            veh_id (int): Vehicle id being selected.
            series (dict): The vehicle's series dictionary with loaded data.
        """
        t_min, t_max = float(series['veh_time'].min()), float(series['veh_time'].max())
        d_min, d_max = float(series['veh_dist_trav'].min()), float(series['veh_dist_trav'].max())

        self._expand_slider_range_if_needed(self._time_slider, t_min, t_max)
        self._expand_slider_range_if_needed(self._dist_slider, d_min, d_max)

        self._time_slider.setValue((t_min, t_max))
        self._dist_slider.setValue((d_min, d_max))

        self._selected_series = {veh_id}
        self._update_plot_colors()

        logger.debug(f"Framed and selected vehicle {veh_id}: "
                     f"time=[{t_min}, {t_max}], dist=[{d_min}, {d_max}]")

    @staticmethod
    def _expand_slider_range_if_needed(slider, val_min: float, val_max: float):
        """Expand a slider's min/max range only if the given span falls
        completely outside its current range; otherwise leave it untouched.

        Args:
            slider (QLabeledRangeSlider): Slider to adjust.
            val_min (float): Minimum of the span that must be reachable.
            val_max (float): Maximum of the span that must be reachable.
        """
        range_min, range_max = slider.minimum(), slider.maximum()
        if val_max < range_min or val_min > range_max:
            slider.setRange(min(range_min, val_min), max(range_max, val_max))
