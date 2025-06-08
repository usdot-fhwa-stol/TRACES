import sqlite3
import os
from math import floor, ceil
from typing import Dict, Optional
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QComboBox, QMessageBox, QPushButton)
from PyQt6.QtCore import Qt
import pyqtgraph as pg
from qtrangeslider import QLabeledRangeSlider
import numpy as np


class TimeSpacePlotWidget(QWidget):
    """Main widget for the time-space plot visualization."""
    
    # Color scheme for series
    series_colors = ['#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231', 
                     '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#fabed4', 
                     '#469990', '#dcbeff', '#9A6324', '#fffac8', '#800000', 
                     '#aaffc3', '#808000', '#ffd8b1', '#000075', '#000000']
    
    text_colors = ['#ffffff', '#ffffff', '#000000', '#ffffff', '#ffffff', 
                   '#ffffff', '#000000', '#ffffff', '#000000', '#000000', 
                   '#ffffff', '#000000', '#ffffff', '#000000', '#ffffff', 
                   '#000000', '#ffffff', '#000000', '#ffffff', '#ffffff']
    
    # Units for display columns
    units_dict = {
        'event_time': 's',
        'veh_dist': 'm',
        'veh_speed': 'm/s',
        'veh_acceleration': 'm/s&#178;',
        'veh_length': 'm'
    }
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_connection = None
        self.vehicle_list = []
        self.current_event = None
        self.current_lane_id = None
        self.selected_series = set()
        self.plot_data = {}
        self.scatter_items = {}
        self.hover_tolerance = 0.01
        self.non_selected_hidden = False
        
        # Store state for lane changes
        self.preserved_ranges = {
            'view': None,
            'dist': None,
            'time': None
        }
        
        # Tooltip caching to prevent redundant queries
        self.tooltip_cache = {
            'id': None,
            'time': None,
            'text': ""
        }
        
        self._setup_ui()
        self._setup_plot()
        self._connect_signals()
        
    def _setup_ui(self):
        """Initialize the user interface layout."""
        # Main horizontal layout
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        
        # Create PyQtGraph plot widget
        self.plot_widget = pg.PlotWidget()
        main_layout.addWidget(self.plot_widget, 1)  # stretch = 1
        
        # Create plot controls widget
        plot_controls = QWidget()
        plot_controls.setSizePolicy(plot_controls.sizePolicy().Minimum, 
                                       plot_controls.sizePolicy().Preferred)
        controls_layout = QVBoxLayout(plot_controls)
        controls_layout.setSpacing(5)  # Set spacing between widgets to 5 pixels
        controls_layout.setContentsMargins(0, 0, 0, 10)  # Only bottom margin
        
        # Event selection
        event_select = QWidget()
        event_layout = QVBoxLayout(event_select)
        event_layout.addWidget(QLabel("Event"))
        self.event_combo = QComboBox()
        self.event_combo.setEnabled(False)
        event_layout.addWidget(self.event_combo)
        
        # Lane selection
        lane_select = QWidget()
        lane_layout = QVBoxLayout(lane_select)
        lane_layout.addWidget(QLabel("Lanes"))
        self.lane_combo = QComboBox()
        self.lane_combo.setEnabled(False)
        lane_layout.addWidget(self.lane_combo)
        
        # Distance slider
        dist_slider = QWidget()
        dist_layout = QVBoxLayout(dist_slider)
        dist_label = QLabel("Dist (m)")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dist_layout.addWidget(dist_label)
        self.dist_range_slider = QLabeledRangeSlider(Qt.Orientation.Vertical)
        self.dist_range_slider.setEnabled(False)
        dist_layout.addWidget(self.dist_range_slider, 1)  # stretch = 1
        
        # Time slider
        time_slider = QWidget()
        time_layout = QVBoxLayout(time_slider)
        time_label = QLabel("Time (s)")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_layout.addWidget(time_label)
        self.time_range_slider = QLabeledRangeSlider(Qt.Orientation.Vertical)
        self.time_range_slider.setEnabled(False)
        time_layout.addWidget(self.time_range_slider, 1)  # stretch = 1
        
        # Hide/Show toggle button
        self.hide_show_button = QPushButton("")
        self.hide_show_button.setEnabled(False)
        self.hide_show_button.setMinimumWidth(60)
        self.hide_show_button.setMaximumWidth(60)
        self.hide_show_button.clicked.connect(self._toggle_hide_show)
        
        # Add all controls to the main controls layout
        controls_layout.addWidget(event_select)
        controls_layout.addWidget(lane_select)
        controls_layout.addWidget(dist_slider, 1)  # stretch = 1
        controls_layout.addWidget(time_slider, 1)  # stretch = 1
        controls_layout.addWidget(self.hide_show_button, 0, Qt.AlignmentFlag.AlignCenter)
        
        main_layout.addWidget(plot_controls)
        
        # Store controls in a list for easy enabling/disabling
        self.control_list = [
            self.event_combo,
            self.lane_combo,
            self.dist_range_slider,
            self.time_range_slider,
            self.plot_widget,
            self.hide_show_button
        ]
        
    def _setup_plot(self):
        """Configure the PyQtGraph plot appearance."""
        # Set white background
        self.plot_widget.setBackground('w')
        
        # Get plot item and configure
        plot_item = self.plot_widget.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.8)
        plot_item.setLabel('left', 'Distance (m)')
        plot_item.setLabel('bottom', 'Time (s)')
        
        # Set grid pen to black
        plot_item.getAxis('left').setPen('k')
        plot_item.getAxis('bottom').setPen('k')
        
        # Set initial title
        self.plot_widget.setTitle("Time-Space Diagram")
        
        # Get view box and constrain panning
        self.view_box = plot_item.getViewBox()
        self.view_box.setLimits(xMin=0, xMax=1, yMin=0, yMax=1)  # Will be updated when data loads
        
        # Override the auto-range button behavior
        auto_btn = plot_item.autoBtn
        auto_btn.clicked.disconnect()  # Disconnect default behavior
        auto_btn.clicked.connect(self._auto_range_to_sliders)
        
        # Enable mouse interaction
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_move)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_mouse_click)
        
    def _connect_signals(self):
        """Connect widget signals to their handlers."""
        self.event_combo.currentIndexChanged.connect(self.on_event_changed)
        self.lane_combo.currentIndexChanged.connect(self.on_lane_changed)
        self.dist_range_slider.valueChanged.connect(self.on_dist_range_changed)
        self.time_range_slider.valueChanged.connect(self.on_time_range_changed)
        
    def set_database(self, db_path: str, db_connection: sqlite3.Connection):
        """Set the database connection and initialize controls."""
        try:
            # Disable controls during database operations
            self._enable_controls(False)
            
            self.db_connection = db_connection
            
            # Update plot title with filename
            filename = os.path.basename(db_path)
            title = f"Time-Space Diagram - {filename}"
            self.plot_widget.setTitle(title)
            
            # Populate event combo
            self._populate_event_combo()
            
            # Initialize with first event and lane
            if self.event_combo.count() > 0:
                # Enable controls before triggering the change
                self._enable_controls(True)
                self.on_event_changed()
            
            # Cache tooltip query
            self._get_tooltip_record(0, 0.0)
                
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to initialize database: {str(e)}")
            
    def _get_tooltip_record(self, vehicle_id, time):
        """Get tooltip record from database for the given vehicle and time."""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""
                SELECT veh_id, event_time, veh_dist, veh_speed, veh_acceleration, veh_automated, veh_length FROM events 
                WHERE event_id = ? AND veh_id = ? AND event_time = ?
                LIMIT 1
            """, (self.current_event, vehicle_id, time))
            return cursor.fetchone()
        except Exception:
            # If the query fails (e.g., missing columns), return None
            return None
            
    def _populate_event_combo(self):
        """Populate the event combobox with events and their limits."""
        cursor = self.db_connection.cursor()
        cursor.execute("""
            SELECT event_id, MIN(event_time), MAX(event_time), MIN(veh_dist), MAX(veh_dist)
            FROM events
            GROUP BY event_id
            ORDER BY event_id
        """)

        
        self.event_combo.clear()
        for row in cursor.fetchall():
            # Add item with display text and full row dict as data
            event_dict = {
                "event": row[0], 
                "time_min": floor(row[1]), 
                "time_max": ceil(row[2]), 
			    "dist_min": floor(row[3]), 
			    "dist_max": ceil(row[4])
			}
            self.event_combo.addItem(str(row[0]), event_dict)
        
    def _populate_lane_combo(self, event):
        """Populate the lane combobox for the given event."""
        cursor = self.db_connection.cursor()
        cursor.execute("""
            SELECT DISTINCT lane_id 
            FROM events 
            WHERE event_id = ? 
            ORDER BY lane_id
        """, (event,))
        
        self.lane_combo.clear()
        for row in cursor.fetchall():
            # Add item with display text and data
            self.lane_combo.addItem(str(row[0]), row[0])
        
    def _load_vehicle_colors(self, event):
        """Load vehicle IDs and assign colors for the given event."""
        cursor = self.db_connection.cursor()
        cursor.execute("""
            SELECT DISTINCT veh_id 
            FROM events 
            WHERE event_id = ?
            ORDER BY veh_id
        """, (event,))
        
        self.vehicle_list = []
        for idx, row in enumerate(cursor.fetchall()):
            color_idx = idx % len(self.series_colors)
            vehicle_dict = {
                'id': row[0],
                'series_color': self.series_colors[color_idx],
                'text_color': self.text_colors[color_idx]
            }
            self.vehicle_list.append(vehicle_dict)
        
    def _enable_controls(self, enabled: bool):
        """Enable or disable all controls."""
        for control in self.control_list:
            control.setEnabled(enabled)
        
    def on_event_changed(self):
        """Handle event combobox change."""
        if not self.event_combo.isEnabled() or self.event_combo.currentIndex() < 0:
            return
            
        # Get event and limits from combo box data
        limits = self.event_combo.currentData()
        if not limits:
            return
            
        self._initialize_event(limits)
                
    def _initialize_event(self, limits):
        """Initialize the display for a new event."""
        self._enable_controls(False)
        
        event = limits['event']
        self.current_event = event
        self.selected_series.clear()
        self.non_selected_hidden = False
        self._update_hide_show_button()
        
        # Reset preserved state
        self.preserved_ranges = {
            'view': None,
            'dist': None,
            'time': None
        }
        
        # Clear tooltip cache when event changes
        self.tooltip_cache = {
            'id': None,
            'time': None,
            'text': ""
        }
        
        # Load vehicle colors
        self._load_vehicle_colors(event)
        
        # Populate lane combo
        self._populate_lane_combo(event)
        
        # Setup sliders
        self._setup_sliders(limits)
            
        self._enable_controls(True)
        
        # Auto-select first lane
        if self.lane_combo.count() > 0:
            # When changing events, always auto-fit
            self.on_lane_changed()
            self._auto_fit_view()
                
        # Keep focus on event combo for keyboard navigation
        self.event_combo.setFocus()
                
    def on_lane_changed(self):
        """Handle lane combobox change."""
        if not self.lane_combo.isEnabled() or self.lane_combo.currentIndex() < 0:
            return
            
        # Get lane_id from combo box data
        lane_id = self.lane_combo.currentData()
        if lane_id is None:
            return
        
        self.plot_widget.setEnabled(False)
        
        # Preserve current view range if it exists
        if self.plot_widget.getPlotItem().getViewBox().viewRange() != [[0, 1], [0, 1]]:
            self.preserved_ranges['view'] = self.plot_widget.getPlotItem().getViewBox().viewRange()
            
        # Preserve slider values
        if self.dist_range_slider.isEnabled():
            self.preserved_ranges['dist'] = self.dist_range_slider.value()
        if self.time_range_slider.isEnabled():
            self.preserved_ranges['time'] = self.time_range_slider.value()
            
        self.current_lane_id = lane_id
        self._update_plot()
        
        # Restore preserved view if it exists
        if self.preserved_ranges['view']:
            self.plot_widget.getPlotItem().getViewBox().setRange(
                xRange=self.preserved_ranges['view'][0],
                yRange=self.preserved_ranges['view'][1],
                padding=0
            )
            
        self.plot_widget.setEnabled(True)
        
    def _auto_fit_view(self):
        """Auto-fit the view to show all data points with some padding."""
        if not self.plot_data:
            return
            
        # Calculate bounds of all visible data
        all_times = []
        all_dists = []
        
        for vehicle_id, data_dict in self.plot_data.items():
            all_times.extend(data_dict['time'])
            all_dists.extend(data_dict['dist'])
            
        if all_times and all_dists:
            time_min, time_max = min(all_times), max(all_times)
            dist_min, dist_max = min(all_dists), max(all_dists)
            
            # Add 10% padding
            time_padding = (time_max - time_min) * 0.1
            dist_padding = (dist_max - dist_min) * 0.1
            
            self.plot_widget.getPlotItem().getViewBox().setRange(
                xRange=[time_min - time_padding, time_max + time_padding],
                yRange=[dist_min - dist_padding, dist_max + dist_padding],
                padding=0
            )
        
    def _setup_sliders(self, limits: Dict):
        """Setup the range sliders with the given limits."""
        # Distance slider - floor min, ceiling max to ensure all data is included
        dist_min = limits['dist_min']
        dist_max = limits['dist_max']
        self.dist_range_slider.setRange(dist_min, dist_max)
        if self.preserved_ranges['dist']:
            self.dist_range_slider.setValue(self.preserved_ranges['dist'])
        else:
            self.dist_range_slider.setValue((dist_min, dist_max))
            
        # Time slider - floor min, ceiling max to ensure all data is included
        time_min = limits['time_min']
        time_max = limits['time_max']
        self.time_range_slider.setRange(time_min, time_max)
        if self.preserved_ranges['time']:
            self.time_range_slider.setValue(self.preserved_ranges['time'])
        else:
            self.time_range_slider.setValue((time_min, time_max))
            
        # Update plot limits to constrain panning - use original float values for precision
        self.view_box.setLimits(
            xMin=limits['time_min'], xMax=limits['time_max'],
            yMin=limits['dist_min'], yMax=limits['dist_max']
        )
            
    def _update_plot(self):
        """Update the plot with current data."""
        if self.current_event is None or self.current_lane_id is None:
            return
            
        # Clear existing plot
        self.plot_widget.clear()
        self.plot_data.clear()
        self.scatter_items.clear()
        
        # Get data from database
        cursor = self.db_connection.cursor()
        cursor.execute("""
            SELECT veh_id, event_time, veh_dist 
            FROM events 
            WHERE event_id = ? AND lane_id = ?
            ORDER BY veh_id, event_time
        """, (self.current_event, self.current_lane_id))
        
        data = cursor.fetchall()
        
        if not data:
            print(f"No data found for event {self.current_event}, lane {self.current_lane_id}")
            return
        
        # Group data by id
        series_data = {}
        for row in data:
            vehicle_id = row[0]
            if vehicle_id not in series_data:
                series_data[vehicle_id] = {'time': [], 'dist': []}
            series_data[vehicle_id]['time'].append(row[1])
            series_data[vehicle_id]['dist'].append(row[2])
            
        print(f"Found {len(series_data)} vehicle series with {len(data)} total points")
        
        # Create scatter plots for each series
        for vehicle_id, data_dict in series_data.items():
            # Get color for this vehicle
            original_color = '#000000'  # default black
            for vehicle in self.vehicle_list:
                if vehicle['id'] == vehicle_id:
                    original_color = vehicle['series_color']
                    break
                    
            # Convert to numpy arrays for better performance
            times = np.array(data_dict['time'])
            dists = np.array(data_dict['dist'])
            
            print(f"Vehicle {vehicle_id}: {len(times)} points, color {original_color}")
            
            # Create scatter plot with original color (will be updated by selection)
            scatter = pg.ScatterPlotItem(
                x=times, 
                y=dists,
                pen=pg.mkPen(original_color, width=2),
                brush=pg.mkBrush(original_color),
                size=4,
                symbol='o'
            )
            
            # Store original color as a custom property
            scatter.original_color = original_color
            
            self.plot_widget.addItem(scatter)
            self.scatter_items[vehicle_id] = scatter
            self.plot_data[vehicle_id] = data_dict
            
        # Apply current filters
        self._apply_range_filters()
        
        print(f"Plot updated with {len(self.scatter_items)} series")
        
    def _apply_range_filters(self):
        """Apply distance and time range filters to the plot."""
        if not (self.dist_range_slider.isEnabled() and self.time_range_slider.isEnabled()):
            return
            
        dist_range = self.dist_range_slider.value()
        time_range = self.time_range_slider.value()
        
        for vehicle_id, scatter in self.scatter_items.items():
            if vehicle_id not in self.plot_data:
                continue
                
            data = self.plot_data[vehicle_id]
            filtered_times = []
            filtered_dists = []
            
            for i, (time, dist) in enumerate(zip(data['time'], data['dist'])):
                if (time_range[0] <= time <= time_range[1] and 
                    dist_range[0] <= dist <= dist_range[1]):
                    filtered_times.append(time)
                    filtered_dists.append(dist)
                    
            scatter.setData(x=filtered_times, y=filtered_dists)
            
        # Handle selection appearance
        self._update_selection_appearance()
        
    def _update_selection_appearance(self):
        """Update appearance based on current selection."""
        grey_color = '#a9a9a9'
        
        if not self.selected_series:
            # No selection - all series use their original colors
            for vehicle_id, scatter in self.scatter_items.items():
                color = scatter.original_color
                scatter.setPen(pg.mkPen(color, width=2))
                scatter.setBrush(pg.mkBrush(color))
                # Always visible when no selection (hide/show doesn't apply)
                scatter.setVisible(True)
        else:
            # Some series selected
            for vehicle_id, scatter in self.scatter_items.items():
                if vehicle_id in self.selected_series:
                    # Selected - use original color
                    color = scatter.original_color
                    scatter.setPen(pg.mkPen(color, width=2))
                    scatter.setBrush(pg.mkBrush(color))
                else:
                    # Not selected - use grey
                    scatter.setPen(pg.mkPen(grey_color, width=2))
                    scatter.setBrush(pg.mkBrush(grey_color))
                    
                # Visibility based on hide/show state
                if self.non_selected_hidden and vehicle_id not in self.selected_series:
                    scatter.setVisible(False)
                else:
                    scatter.setVisible(True)
                    
        self._update_hide_show_button()
                
    def _update_hide_show_button(self):
        """Update the hide/show button state and label."""
        if not self.selected_series:
            # No selection - disable button and clear label
            self.hide_show_button.setEnabled(False)
            self.hide_show_button.setText("")
        else:
            # Has selection - enable button and set appropriate label
            self.hide_show_button.setEnabled(True)
            if self.non_selected_hidden:
                self.hide_show_button.setText("Show")
            else:
                self.hide_show_button.setText("Hide")
                
    def _toggle_hide_show(self):
        """Toggle the visibility of non-selected series."""
        self.non_selected_hidden = not self.non_selected_hidden
        self._update_selection_appearance()
        
    def on_dist_range_changed(self):
        """Handle distance range slider change."""
        if self.dist_range_slider.isEnabled():
            self._apply_range_filters()
            self._update_plot_limits()
            self._check_viewport_adjustment()
            
    def on_time_range_changed(self):
        """Handle time range slider change."""
        if self.time_range_slider.isEnabled():
            self._apply_range_filters()
            self._update_plot_limits()
            self._check_viewport_adjustment()
            
    def _update_plot_limits(self):
        """Update plot panning limits based on current slider values."""
        if not (self.dist_range_slider.isEnabled() and self.time_range_slider.isEnabled()):
            return
            
        dist_range = self.dist_range_slider.value()
        time_range = self.time_range_slider.value()
        
        # Constrain panning to current slider ranges
        self.view_box.setLimits(
            xMin=time_range[0], xMax=time_range[1],
            yMin=dist_range[0], yMax=dist_range[1]
        )
            
    def _check_viewport_adjustment(self):
        """Adjust viewport if it crosses slider boundaries."""
        if not (self.dist_range_slider.isEnabled() and self.time_range_slider.isEnabled()):
            return
            
        view_range = self.plot_widget.getPlotItem().getViewBox().viewRange()
        current_x_range = view_range[0]
        current_y_range = view_range[1]
        
        time_range = self.time_range_slider.value()
        dist_range = self.dist_range_slider.value()
        
        new_x_range = list(current_x_range)
        new_y_range = list(current_y_range)
        
        # Adjust time (x-axis) if viewport crosses boundaries
        if current_x_range[0] < time_range[0]:
            shift = time_range[0] - current_x_range[0]
            new_x_range = [current_x_range[0] + shift, current_x_range[1] + shift]
        elif current_x_range[1] > time_range[1]:
            shift = current_x_range[1] - time_range[1]
            new_x_range = [current_x_range[0] - shift, current_x_range[1] - shift]
            
        # Adjust distance (y-axis) if viewport crosses boundaries  
        if current_y_range[0] < dist_range[0]:
            shift = dist_range[0] - current_y_range[0]
            new_y_range = [current_y_range[0] + shift, current_y_range[1] + shift]
        elif current_y_range[1] > dist_range[1]:
            shift = current_y_range[1] - dist_range[1]
            new_y_range = [current_y_range[0] - shift, current_y_range[1] - shift]
            
        # Apply adjustment if needed
        if new_x_range != current_x_range or new_y_range != current_y_range:
            self.plot_widget.getPlotItem().getViewBox().setRange(
                xRange=new_x_range, yRange=new_y_range, padding=0
            )
            
    def _on_mouse_move(self, pos):
        """Handle mouse move for hover tooltips."""
        if not self.plot_data:
            return
            
        # Convert to plot coordinates
        plot_pos = self.plot_widget.getPlotItem().getViewBox().mapSceneToView(pos)
        x, y = plot_pos.x(), plot_pos.y()
        
        # Find nearest point within visible range
        self._show_hover_info(x, y)
        
    def _show_hover_info(self, x: float, y: float):
        """Show hover information for the nearest point."""
        min_dist = float('inf')
        hover_info = None
        tolerance = 10.0  # Screen coordinate tolerance for easier hovering
        
        # Check all visible data points using the original data arrays
        for vehicle_id, data_dict in self.plot_data.items():
            if vehicle_id not in self.scatter_items or not self.scatter_items[vehicle_id].isVisible():
                continue
                
            # Check each data point in this series
            for i, (time_val, dist_val) in enumerate(zip(data_dict['time'], data_dict['dist'])):
                # Convert data coordinates to screen coordinates for distance calculation
                view_box = self.plot_widget.getPlotItem().getViewBox()
                screen_pos = view_box.mapViewToScene(pg.Point(time_val, dist_val))
                mouse_scene_pos = view_box.mapViewToScene(pg.Point(x, y))
                
                # Calculate screen distance
                screen_distance = ((screen_pos.x() - mouse_scene_pos.x()) ** 2 + 
                                 (screen_pos.y() - mouse_scene_pos.y()) ** 2) ** 0.5
                
                if screen_distance < min_dist and screen_distance < tolerance:
                    min_dist = screen_distance
                    hover_info = (vehicle_id, time_val, dist_val)
                    
        if hover_info:
            vehicle_id, time, dist = hover_info
            
            # Check if this is the same point as last time (caching)
            if vehicle_id == self.tooltip_cache['id'] and time == self.tooltip_cache['time']:
                # Use cached tooltip
                self.plot_widget.setToolTip(self.tooltip_cache['text'])
                return
                
            # Get full record from database
            record = self._get_tooltip_record(vehicle_id, time)

            if record:
                # Format specific columns with appropriate precision
                tooltip_lines = []
                columns_to_show = ["veh_id", "event_time", "veh_dist", "veh_speed", "veh_acceleration", "veh_automated", "veh_length"]
                
                for col_index, col in enumerate(columns_to_show):
                    if record[col_index] is not None:
                        value = record[col_index]
                        if col in ["veh_id", "veh_automated"]:
                            # Integer columns
                            formatted_value = f"{int(value)}"
                        else:
                            # Decimal columns - round to 2 places
                            formatted_value = f"{value:.2f}"
                        
                        # Add units if available
                        if col in self.units_dict:
                            formatted_value += f" {self.units_dict[col]}"
                        
                        tooltip_lines.append(f"{col}: {formatted_value}")
                
                '\n'.join(tooltip_lines)
                
                # Get colors for this vehicle
                bg_color = '#000000'  # default
                fg_color = '#ffffff'  # default
                for vehicle in self.vehicle_list:
                    if vehicle['id'] == vehicle_id:
                        bg_color = vehicle['series_color']
                        fg_color = vehicle['text_color']
                        break
                
                # Format tooltip with colors - convert newlines to <br> for HTML
                tooltip_html_lines = '<br>'.join(tooltip_lines)
                formatted_tooltip = f'<div style="background-color: {bg_color}; color: {fg_color}; padding: 4px;">{tooltip_html_lines}</div>'
                
                # Cache the result
                self.tooltip_cache['id'] = vehicle_id
                self.tooltip_cache['time'] = time
                self.tooltip_cache['text'] = formatted_tooltip
                
                self.plot_widget.setToolTip(formatted_tooltip)
            else:
                # Clear cache if no record found
                self.tooltip_cache = {'id': None, 'time': None, 'text': ""}
                self.plot_widget.setToolTip("")
        else:
            # Clear tooltip and cache when not hovering over any point
            self.tooltip_cache = {'id': None, 'time': None, 'text': ""}
            self.plot_widget.setToolTip("")
            
    def _on_mouse_click(self, event):
        """Handle mouse click for series selection."""
        if not self.plot_data or event.button() != Qt.MouseButton.LeftButton:
            return
            
        # Convert to plot coordinates
        plot_pos = self.plot_widget.getPlotItem().getViewBox().mapSceneToView(event.scenePos())
        x, y = plot_pos.x(), plot_pos.y()
        
        # Find clicked series
        clicked_series = self._find_clicked_series(x, y)
        
        if clicked_series is not None:  # Handle id 0
            if clicked_series in self.selected_series:
                # Remove from selection
                self.selected_series.remove(clicked_series)
            else:
                # Add to selection
                self.selected_series.add(clicked_series)
            
            # Check boundary conditions
            if len(self.selected_series) == len(self.scatter_items):
                # All selected - clear selection
                self.selected_series.clear()
                
            # Reset hide state when selection becomes empty
            if not self.selected_series:
                self.non_selected_hidden = False
                    
            self._update_selection_appearance()
            
    def _find_clicked_series(self, x: float, y: float) -> Optional[int]:
        """Find the series that was clicked."""
        min_dist = float('inf')
        clicked_series = None
        tolerance = 10.0  # Screen coordinate tolerance
        
        for vehicle_id, data in self.plot_data.items():
            if vehicle_id not in self.scatter_items or not self.scatter_items[vehicle_id].isVisible():
                continue
                
            for time, dist in zip(data['time'], data['dist']):
                # Convert data coordinates to screen coordinates
                view_box = self.plot_widget.getPlotItem().getViewBox()
                screen_pos = view_box.mapViewToScene(pg.Point(time, dist))
                mouse_scene_pos = view_box.mapViewToScene(pg.Point(x, y))
                
                # Calculate screen distance
                screen_distance = ((screen_pos.x() - mouse_scene_pos.x()) ** 2 + 
                                 (screen_pos.y() - mouse_scene_pos.y()) ** 2) ** 0.5
                
                if screen_distance < min_dist and screen_distance < tolerance:
                    min_dist = screen_distance
                    clicked_series = vehicle_id
                    
        return clicked_series
        
    def _auto_range_to_sliders(self):
        """Handle auto-range button click by setting range to slider values."""
        if self.dist_range_slider.isEnabled() and self.time_range_slider.isEnabled():
            dist_range = self.dist_range_slider.value()
            time_range = self.time_range_slider.value()
            
            # Set range to match sliders with a small padding
            self.view_box.setRange(
                xRange=time_range,
                yRange=dist_range,
                padding=0.02  # Small padding for visibility
            )