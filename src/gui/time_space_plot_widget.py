# gui/time_space_plot_widget.py

import sqlite3
import numpy as np
from math import floor, ceil
from pathlib import Path
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QPushButton
from PyQt6.QtCore import Qt
from pyqtgraph import PlotWidget, ScatterPlotItem, mkPen
from qtrangeslider import QLabeledRangeSlider
from PyQt6.QtGui import QMouseEvent

class TimeSpacePlotWidget(QWidget):
    """Widget for displaying time-space scatter plots from trajectory data.

    Displays a scatter plot of vehicle trajectories with interactive controls
    for selecting events, lanes, and filtering by time and distance ranges.

    Attributes:
        _plot_widget (PlotWidget): PyQtGraph widget for displaying the scatter plot.
        _plot_controls (QWidget): Container for control widgets.
        _event_select (QComboBox): ComboBox for selecting events.
        _lane_select (QComboBox): ComboBox for selecting lanes.
        _dist_slider (QLabeledRangeSlider): Slider for filtering distance range.
        _time_slider (QLabeledRangeSlider): Slider for filtering time range.
        _hide_show_button (QPushButton): Button to toggle visibility of unselected series.
        _db_connection (sqlite3.Connection): Database connection.
        _event_dicts (dict): Dictionary of event data by event_id.
        _plot_series (list): List of series dictionaries for plotting.
        _series_color (list): Colors for series plotting.
        _text_color (list): Colors for tooltip text.
        _units_dict (dict): Units for displayed values.
        _tooltip (QLabel): Tooltip for displaying point information.
    """

    def __init__(self, parent=None):
        """Initialize the widget with plot and control layouts."""
        super().__init__(parent)
        self._db_connection = None
        self._event_dicts = {}
        self._plot_series = []
        self._series_color = [
            "#e6194B", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", 
            "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff", 
            "#9A6324", "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1", 
            "#000075", "#000000"
        ]
        self._text_color = [
            "#ffffff", "#ffffff", "#000000", "#ffffff", "#ffffff", "#ffffff", 
            "#000000", "#ffffff", "#000000", "#000000", "#ffffff", "#000000", 
            "#ffffff", "#000000", "#ffffff", "#000000", "#ffffff", "#000000", 
            "#ffffff", "#ffffff"
        ]
        self._units_dict = {
            "event_time": "s",
            "veh_dist": "m",
            "veh_speed": "m/s",
            "veh_acceleration": "m/s²",
            "veh_length": "m"
        }
        self._tooltip = None
        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget layout with plot and controls."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # Plot widget
        self._plot_widget = PlotWidget()
        self._plot_widget.setBackground('w')
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setLabel('bottom', 'Time (s)')
        self._plot_widget.setLabel('left', 'Dist (m)')
        layout.addWidget(self._plot_widget, stretch=1)

        # Plot controls
        self._plot_controls = QWidget()
        self._plot_controls.setSizePolicy(self._plot_controls.sizePolicy().Minimum, 
            self._plot_controls.sizePolicy().Preferred)
        self._plot_controls.setLayout(QVBoxLayout())
        self._plot_controls.layout().setSpacing(5)
        self._plot_controls.layout().setContentsMargins(0, 0, 0, 10)
        layout.addWidget(self._plot_controls)

        # Event select
        self._event_select = QWidget()
        self._event_select.setLayout(QVBoxLayout())
        self._event_select.layout().addWidget(QLabel("Event"))
        self._event_combo = QComboBox()
        self._event_select.layout().addWidget(self._event_combo)
        self._plot_controls.layout().addWidget(self._event_select)

        # Lane select
        self._lane_select = QWidget()
        self._lane_select.setLayout(QVBoxLayout())
        self._lane_select.layout().addWidget(QLabel("Lanes"))
        self._lane_combo = QComboBox()
        self._lane_select.layout().addWidget(self._lane_combo)
        self._plot_controls.layout().addWidget(self._lane_select)

        # Distance slider
        self._dist_slider = QWidget()
        self._dist_slider.setLayout(QVBoxLayout())
        dist_label = QLabel("Dist (m)")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dist_slider.layout().addWidget(dist_label)
        self._dist_range = QLabeledRangeSlider(Qt.Orientation.Vertical)
        self._dist_slider.layout().addWidget(self._dist_range, stretch=1)
        self._plot_controls.layout().addWidget(self._dist_slider)

        # Time slider
        self._time_slider = QWidget()
        self._time_slider.setLayout(QVBoxLayout())
        time_label = QLabel("Time (s)")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_slider.layout().addWidget(time_label)
        self._time_range = QLabeledRangeSlider(Qt.Orientation.Vertical)
        self._time_slider.layout().addWidget(self._time_range, stretch=1)
        self._plot_controls.layout().addWidget(self._time_slider)

        # Hide/Show button
        self._hide_show_button = QPushButton(" ")
        self._hide_show_button.setMinimumWidth(60)
        self._hide_show_button.setMaximumWidth(60)
        self._plot_controls.layout().addWidget(self._hide_show_button, 0,  Qt.AlignmentFlag.AlignCenter)

        # Disable controls initially
        self._set_controls_enabled(False)

        # Connect signals
        self._event_combo.currentIndexChanged.connect(self._on_event_changed)
        self._lane_combo.currentIndexChanged.connect(self._on_lane_changed)
        self._dist_range.valueChanged.connect(self._update_plot)
        self._time_range.valueChanged.connect(self._update_plot)
        self._hide_show_button.clicked.connect(self._toggle_hide_show)
        self._plot_widget.scene().sigMouseMoved.connect(self._handle_mouse_moved)
        self._plot_widget.scene().sigMouseClicked.connect(self._handle_mouse_clicked)

    def _set_controls_enabled(self, enabled: bool):
        """Enable or disable all control widgets.

        Args:
            enabled (bool): Whether to enable or disable controls.
        """
        self._event_select.setEnabled(enabled)
        self._lane_select.setEnabled(enabled)
        self._dist_slider.setEnabled(enabled)
        self._time_slider.setEnabled(enabled)
        self._hide_show_button.setEnabled(enabled and bool(self._event_dicts.get(self._event_combo.currentData(), {}).get('selected_series', set())))

    def set_database(self, file_path: str, db_connection: sqlite3.Connection):
        """Set the database connection and populate controls.

        Args:
            file_path (str): Path to the SQLite database file.
            db_connection (sqlite3.Connection): Database connection.
        """
        self._db_connection = db_connection
        self._plot_widget.setTitle(f"Time-Space Diagram - {Path(file_path).name}")
        self._event_dicts.clear()
        self._event_combo.clear()
        
        # Populate event combobox
        cursor = self._db_connection.cursor()
        cursor.execute("""
            SELECT event_id, MIN(event_time), MAX(event_time), MIN(veh_dist), MAX(veh_dist)
            FROM events
            GROUP BY event_id
            ORDER BY event_id
        """)
        for row in cursor.fetchall():
            event_id = row[0]
            if event_id is not None:  # Explicit None check
                event_dict = {
                    "event_id": event_id,
                    "min_time": floor(row[1]),
                    "max_time": ceil(row[2]),
                    "min_dist": floor(row[3]),
                    "max_dist": ceil(row[4]),
                    "min_time_handle": floor(row[1]),
                    "max_time_handle": ceil(row[2]),
                    "min_dist_handle": floor(row[3]),
                    "max_dist_handle": ceil(row[4]),
                    "lane_id": -1,
                    "selected_series": set(),
                    "hidden": False,
                    "plot_view_box": None
                }
                self._event_dicts[event_id] = event_dict
                self._event_combo.addItem(str(event_id), event_id)
        
        if self._event_combo.count() > 0:
            self._set_controls_enabled(True)
            self._event_changed(self._event_combo.currentData())
            self._update_plot()

    def _on_event_changed(self, index: int):
        """Handle event combobox change signal."""
        self._set_controls_enabled(False)
        self._event_changed(self._event_combo.currentData())
        self._set_controls_enabled(True)

    def _event_changed(self, event_id: int):
        """Update state when event changes.

        Args:
            event_id (int): Selected event ID.
        """
        if event_id is None:
            return
        
        # Save current view box and settings
        if self._event_combo.count() > 1:
            prev_event_id = self._event_combo.itemData(self._event_combo.currentIndex() - 1)
            if prev_event_id in self._event_dicts:
                self._event_dicts[prev_event_id]["plot_view_box"] = self._plot_widget.getViewBox().viewRange()
                self._event_dicts[prev_event_id]["min_time_handle"] = self._time_range.value()[0]
                self._event_dicts[prev_event_id]["max_time_handle"] = self._time_range.value()[1]
                self._event_dicts[prev_event_id]["min_dist_handle"] = self._dist_range.value()[0]
                self._event_dicts[prev_event_id]["max_dist_handle"] = self._dist_range.value()[1]
                self._event_dicts[prev_event_id]["lane_id"] = self._lane_combo.currentData()

        # Update controls
        event_dict = self._event_dicts[event_id]
        self._time_range.setRange(event_dict["min_time"], event_dict["max_time"])
        self._time_range.setValue((event_dict["min_time_handle"], event_dict["max_time_handle"]))
        self._dist_range.setRange(event_dict["min_dist"], event_dict["max_dist"])
        self._dist_range.setValue((event_dict["min_dist_handle"], event_dict["max_dist_handle"]))

        # Populate lane combobox
        self._lane_combo.clear()
        cursor = self._db_connection.cursor()
        cursor.execute("""
            SELECT DISTINCT lane_id
            FROM events
            WHERE event_id = ?
            ORDER BY lane_id
        """, (event_id,))
        for row in cursor.fetchall():
            lane_id = row[0]
            if lane_id is not None:  # Explicit None check
                self._lane_combo.addItem(str(lane_id), lane_id)
        
        # Restore previous lane if available
        if event_dict["lane_id"] in [self._lane_combo.itemData(i) for i in range(self._lane_combo.count())]:
            self._lane_combo.setCurrentIndex(
                [self._lane_combo.itemData(i) for i in range(self._lane_combo.count())].index(event_dict["lane_id"])
            )
        else:
            event_dict["lane_id"] = self._lane_combo.currentData() if self._lane_combo.count() > 0 else -1
        
        self._update_plot()
        
        # Restore view box if available
        if event_dict["plot_view_box"]:
            self._plot_widget.getViewBox().setRange(xRange=event_dict["plot_view_box"][0], 
                                                  yRange=event_dict["plot_view_box"][1])

    def _on_lane_changed(self, index: int):
        """Handle lane combobox change signal."""
        self._set_controls_enabled(False)
        self._lane_changed(self._lane_combo.currentData())
        self._set_controls_enabled(True)

    def _lane_changed(self, lane_id: int):
        """Update state when lane changes.

        Args:
            lane_id (int): Selected lane ID.
        """
        if lane_id is not None:
            event_dict = self._event_dicts[self._event_combo.currentData()]
            event_dict["lane_id"] = lane_id
            self._update_plot()

    def _update_plot(self):
        """Update the scatter plot with current selections."""
        self._plot_widget.clear()
        if not self._db_connection or self._event_combo.currentData() < 0:
            return

        event_id = self._event_combo.currentData()
        lane_id = self._lane_combo.currentData() if self._lane_combo.currentData() is not None else -1
        event_dict = self._event_dicts[event_id]
        min_time, max_time = self._time_range.value()
        min_dist, max_dist = self._dist_range.value()

        # Update hide/show button
        self._hide_show_button.setText("Hide" if not event_dict["hidden"] else "Show")
        self._hide_show_button.setEnabled(bool(event_dict["selected_series"]))

        # Populate plot series
        self._plot_series.clear()
        cursor = self._db_connection.cursor()
        cursor.execute("""
            SELECT DISTINCT veh_id
            FROM events
            WHERE event_id = ?
            ORDER BY veh_id
        """, (event_id,))
        veh_ids = [row[0] for row in cursor.fetchall() if row[0] is not None]

        for veh_id in veh_ids:
            cursor.execute("""
                SELECT event_time, veh_dist
                FROM events
                WHERE event_id = ? AND veh_id = ? AND lane_id = ?
                ORDER BY event_time
            """, (event_id, veh_id, lane_id))
            data = cursor.fetchall()
            if data:
                x = np.array([row[0] for row in data])
                y = np.array([row[1] for row in data])
                self._plot_series.append({"veh_id": veh_id, "x": x, "y": y})

        # Plot filtered series
        for i, series in enumerate(self._plot_series):
            mask = (series["x"] >= min_time) & (series["x"] <= max_time) & \
                   (series["y"] >= min_dist) & (series["y"] <= max_dist)
            x_filtered = series["x"][mask]
            y_filtered = series["y"][mask]
            
            if len(x_filtered) > 0:
                color = self._series_color[i % len(self._series_color)]
                if event_dict["hidden"] and series["veh_id"] not in event_dict["selected_series"]:
                    color = "#a9a9a9"
                elif series["veh_id"] not in event_dict["selected_series"] and event_dict["selected_series"]:
                    color = "#a9a9a9"
                
                scatter = ScatterPlotItem(
                    x=x_filtered, y=y_filtered, size=4, pen=mkPen(color), brush=color,
                    data=[(series["veh_id"], x, y) for x, y in zip(x_filtered, y_filtered)]
                )
                self._plot_widget.addItem(scatter)

    def _handle_mouse_moved(self, pos):
        """Handle mouse movement for showing tooltips.

        Args:
            pos (QPointF): Mouse position in scene coordinates.
        """
        if not self._plot_series:
            return

        view_box = self._plot_widget.getViewBox()
        mouse_point = view_box.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()

        min_dist = float('inf')
        closest_data = None
        tolerance = 0.1  # Time tolerance in seconds

        for i, series in enumerate(self._plot_series):
            mask = (series["x"] >= mx - tolerance) & (series["x"] <= mx + tolerance)
            x_filtered = series["x"][mask]
            y_filtered = series["y"][mask]
            
            if len(x_filtered) > 0:
                distances = np.abs(y_filtered - my)
                min_idx = np.argmin(distances)
                if distances[min_idx] < min_dist:
                    min_dist = distances[min_idx]
                    closest_data = (series["veh_id"], x_filtered[min_idx], y_filtered[min_idx], i)

        if closest_data and min_dist < 10:  # Pixel tolerance
            veh_id, event_time, veh_dist, series_idx = closest_data
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT veh_speed, veh_acceleration, veh_automated, veh_length
                FROM events
                WHERE event_id = ? AND veh_id = ? AND event_time = ?
            """, (self._event_combo.currentData(), veh_id, event_time))
            row = cursor.fetchone()
            
            if row:
                veh_speed = f"{row[0]:.2f}" if row[0] is not None else " "
                veh_accel = f"{row[1]:.2f}" if row[1] is not None else " "
                veh_auto = f"{row[2]:.2f}" if row[2] is not None else " "
                veh_length = f"{row[3]:.2f}" if row[3] is not None else " "

                tooltip_text = (
                    f"Vehicle ID: {veh_id}\n"
                    f"Time: {event_time:.2f} {self._units_dict['event_time']}\n"
                    f"Distance: {veh_dist:.2f} {self._units_dict['veh_dist']}\n"
                    f"Speed: {veh_speed} {self._units_dict['veh_speed']}\n"
                    f"Acceleration: {veh_accel} {self._units_dict['veh_acceleration']}\n"
                    f"Automated: {veh_auto}\n"
                    f"Length: {veh_length} {self._units_dict['veh_length']}"
                )
                
                if not self._tooltip:
                    self._tooltip = QLabel(self._plot_widget)
                    self._tooltip.setStyleSheet("QLabel { padding: 5px; }")
                
                self._tooltip.setText(tooltip_text)
                color = self._series_color[series_idx % len(self._series_color)]
                text_color = self._text_color[series_idx % len(self._text_color)]
                self._tooltip.setStyleSheet(
                    f"QLabel {{ background-color: {color}; color: {text_color}; padding: 5px; }}"
                )
                self._tooltip.move(int(pos.x()) + 10, int(pos.y()) + 10)
                self._tooltip.show()
            else:
                if self._tooltip:
                    self._tooltip.hide()
        else:
            if self._tooltip:
                self._tooltip.hide()

    def _handle_mouse_clicked(self, event: QMouseEvent):
        """Handle mouse click for selecting series.

        Args:
            event (QMouseEvent): Mouse click event.
        """
        if not self._plot_series or event.button() != Qt.MouseButton.LeftButton:
            return

        view_box = self._plot_widget.getViewBox()
        mouse_point = view_box.mapSceneToView(event.scenePos())
        mx, my = mouse_point.x(), mouse_point.y()

        min_dist = float('inf')
        closest_veh_id = None
        tolerance = 0.1  # Time tolerance in seconds

        for series in self._plot_series:
            mask = (series["x"] >= mx - tolerance) & (series["x"] <= mx + tolerance)
            x_filtered = series["x"][mask]
            y_filtered = series["y"][mask]
            
            if len(x_filtered) > 0:
                distances = np.abs(y_filtered - my)
                if np.min(distances) < min_dist:
                    min_dist = np.min(distances)
                    closest_veh_id = series["veh_id"]

        if closest_veh_id is not None and min_dist < 10:  # Pixel tolerance
            event_dict = self._event_dicts[self._event_combo.currentData()]
            selected_series = event_dict["selected_series"]
            
            if closest_veh_id in selected_series:
                selected_series.remove(closest_veh_id)
            else:
                selected_series.add(closest_veh_id)
            
            # Clear selection if all series are selected
            if len(selected_series) == len(self._plot_series):
                selected_series.clear()
            
            self._update_plot()

    def _toggle_hide_show(self):
        """Toggle visibility of unselected series."""
        event_dict = self._event_dicts[self._event_combo.currentData()]
        event_dict["hidden"] = not event_dict["hidden"]
        self._update_plot()