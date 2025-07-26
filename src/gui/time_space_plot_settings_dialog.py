import sqlite3
import logging
import json
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                           QLabel, QComboBox, QWidget, QSizePolicy)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
from superqt import QLabeledRangeSlider

# Set up logger
logger = logging.getLogger(__name__)


class TimeSpacePlotSettingsDialog(QDialog):
    """Modal dialog for configuring time-space plot settings.
    
    The dialog allows users to select an event and configure time and distance ranges
    for plotting trajectory data. It provides real-time feedback on the number of
    records that will be displayed and enforces a maximum record limit.
    
    Attributes:
        _db_connection (sqlite3.Connection): Database connection for querying.
        _defaults (dict): Control values when re-opened from plot widget.
        _event_combobox (QComboBox): Combobox for selecting event ID.
        _lane_count_label (QLabel): Label displaying number of lanes.
        _veh_count_label (QLabel): Label displaying number of vehicles.
        _time_slider (QLabeledRangeSlider): Slider for time range selection.
        _dist_slider (QLabeledRangeSlider): Slider for distance range selection.
        _record_count_label (QLabel): Label displaying current record count.
        _apply_button (QPushButton): Button to apply settings.
        _current_event_id (int): Currently selected event ID.
        _record_limit (int): Maximum allowed records (100,000).
        _current_event_summary (dict): dictonary of rollups for current event.
    """
    
    # Signal emitted when settings are applied
    # event_id, time_min, time_max, dist_min, dist_max, event_summary
    settings_applied = pyqtSignal(int, float, float, float, float, dict)

    def __init__(self, parent=None, db_connection: sqlite3.Connection = None, defaults: dict = None):
        """Initialize the TimeSpacePlotSettingsDialog.
        
        Args:
            parent: Parent widget (typically MainWindow or TimeSpacePlotWidget).
            db_connection (sqlite3.Connection): Database connection for querying.
            defaults (dict): Default settings to restore when reopening dialog.
        """
        super().__init__(parent)
        self._db_connection = db_connection
        self._defaults = defaults
        self._event_combobox = None
        self._lane_count_label = None
        self._veh_count_label = None
        self._time_slider = None
        self._dist_slider = None
        self._record_count_label = None
        self._apply_button = None
        self._current_event_id = None
        self._record_limit = 500
        self._current_event_summary = {}
        
        logger.debug("TimeSpacePlotSettingsDialog initialized")
        
        self._setup_ui()
        self._populate_events()
        # Use QTimer to center dialog after it's been properly sized
        QTimer.singleShot(0, self._center_dialog)
        
    def _setup_ui(self):
        """Set up the dialog UI components."""
        self.setWindowTitle("Time Space Plot Settings")
        self.setFixedWidth(600) # height sizes from content
        
        # Create main layout
        layout = QVBoxLayout(self)
        
        self._event_name = QLabel() # centered label to show event name
        self._event_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Calculate the height using font metrics
        self._event_name.setWordWrap(True)
        self._event_name.setMinimumHeight(int(self._event_name.fontMetrics().height() * 2.5))
        layout.addWidget(self._event_name)

        # Top control set with centered elements
        top_layout = QHBoxLayout()
        
        top_layout.addStretch()  # Left spacer
        
        # Event combobox
        self._event_combobox = QComboBox()
        self._event_combobox.currentIndexChanged.connect(self._on_event_changed)
        top_layout.addWidget(self._event_combobox)
        
        top_layout.addStretch()
        
        # Lane count
        top_layout.addWidget(QLabel("Lanes:"))
        self._lane_count_label = QLabel("0")
        top_layout.addWidget(self._lane_count_label)
        
        top_layout.addStretch()
        
        # Vehicle count
        top_layout.addWidget(QLabel("Total Vehicles:"))
        self._veh_count_label = QLabel("0")
        top_layout.addWidget(self._veh_count_label)
        
        top_layout.addStretch()  # Right spacer
        
        layout.addLayout(top_layout)
        
        # Time slider section with widget container
        time_widget = QWidget()
        time_layout = QVBoxLayout(time_widget)
        time_layout.setSpacing(0)
        
        self._time_slider = QLabeledRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Horizontal)
        # Connect to mouse release event for interrelated operations
        self._time_slider.sliderReleased.connect(self._on_time_slider_released)
        time_layout.addWidget(self._time_slider)
        
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_layout.addWidget(time_label)
        
        layout.addWidget(time_widget)
        
        # Distance slider section with widget container
        dist_widget = QWidget()
        dist_layout = QVBoxLayout(dist_widget)
        dist_layout.setSpacing(0)

        self._dist_slider = QLabeledRangeSlider()
        self._dist_slider.setOrientation(Qt.Orientation.Horizontal)
        # Connect to mouse release event for interrelated operations
        self._dist_slider.sliderReleased.connect(self._on_dist_slider_released)
        dist_layout.addWidget(self._dist_slider)
        
        dist_label = QLabel("Distance (m)")
        dist_label.setStyleSheet("font-weight: bold;")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dist_layout.addWidget(dist_label)
        
        layout.addWidget(dist_widget)
        
        # Bottom control set with centered elements
        bottom_layout = QHBoxLayout()
        
        bottom_layout.addStretch()  # Left spacer
        
        # Record count label
        self._record_count_label = QLabel(f"0 of {self._record_limit} vehicle limit")
        self._record_count_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        self._record_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._record_count_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        self._record_count_label.setFont(font)
        self._record_count_label.setMinimumHeight(self._record_count_label.fontMetrics().height() + 6)
        bottom_layout.addWidget(self._record_count_label)
        
        bottom_layout.addStretch()
        
        # Apply button
        self._apply_button = QPushButton("Apply")
        self._apply_button.clicked.connect(self._on_apply)
        self._apply_button.setEnabled(False)
        bottom_layout.addWidget(self._apply_button)
        
        bottom_layout.addStretch()  # Right spacer
        
        layout.addLayout(bottom_layout)
        
    def _center_dialog(self):
        """Center the dialog within the parent window."""
        if self.parent():
            parent_rect = self.parent().geometry()
            dialog_rect = self.geometry()
            x = parent_rect.x() + (parent_rect.width() - dialog_rect.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - dialog_rect.height()) // 2
            self.move(x, y)
            
    def _populate_events(self):
        """Populate the event combobox with event IDs from the ranges table."""
        if not self._db_connection:
            logger.warning("No database connection available")
            return
            
        try:
            cursor = self._db_connection.cursor()
            cursor.execute("SELECT event_id FROM ranges ORDER BY event_id")
            events = cursor.fetchall()
            
            self._event_combobox.clear()
            for event in events:
                event_id = event[0]
                self._event_combobox.addItem(f"Event {event_id:3d}", event_id)
                
            if events:
                # Set default selection
                if self._defaults and "event_id" in self._defaults:
                    event_id = self._defaults["event_id"]
                    for i in range(self._event_combobox.count()):
                        if self._event_combobox.itemData(i) == event_id:
                            self._event_combobox.setCurrentIndex(i)
                            break
                    self._current_event_id = event_id
                else:
                    self._current_event_id = events[0][0]
                    
                # Use QTimer to update event data after UI is fully constructed
                QTimer.singleShot(0, self._update_event_data)
                
        except sqlite3.Error as e:
            logger.error(f"Error populating events: {e}")
            
    def _on_event_changed(self, index: int):
        """Handle event combobox selection change.
        
        Args:
            index (int): Selected combobox index.
        """
        if index >= 0:
            self._current_event_id = self._event_combobox.itemData(index)
            self._update_event_data()
            
    def _update_event_data(self):
        """Update all event-dependent data (lane count, vehicle count, sliders, record count)."""
        if not self._db_connection or self._current_event_id is None:
            return
            
        try:
            cursor = self._db_connection.cursor()
            cursor.execute("SELECT summary_json FROM ranges WHERE event_id = ?", (self._current_event_id,))
            result = cursor.fetchone()
            
            if not result:
                logger.warning(f"No summary data found for event {self._current_event_id}")
                return
                
            try:
                summary = json.loads(result[0])
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing summary JSON for event {self._current_event_id}: {e}")
                return
            
            # Persist summary and set event name
            self._current_event_summary = summary
            self._event_name.setText(summary.get("event_name", ""))

            # Update lane list from summary
            lane_ids = summary.get("lane_id", [])
            self._lane_count_label.setText(', '.join(map(str, lane_ids)))

            # Update vehicle count from summary
            veh_ids = summary.get("veh_id", [])
            self._veh_count_label.setText(str(len(veh_ids)))
            
            # Update slider ranges from summary
            time_range = summary.get("veh_time", [0, 0])
            dist_range = summary.get("veh_dist_trav", [0, 0])
            
            if len(time_range) >= 2 and len(dist_range) >= 2:
                time_min, time_max = time_range[0], time_range[1]
                dist_min, dist_max = dist_range[0], dist_range[1]
                
                # Set slider ranges
                self._dist_slider.setRange(dist_min, dist_max)
                self._time_slider.setRange(time_min, time_max)
                
                # Set slider values
                if self._defaults:
                    self._dist_slider.setValue(self._defaults["dist_range"])
                    self._time_slider.setValue(self._defaults["time_range"])
                    self._defaults = None  # Clear defaults after first use
                else:
                    self._dist_slider.setValue([dist_min, dist_max])
                    self._time_slider.setValue([time_min, time_max])
                
                # Update record count
                self._update_record_count()
            else:
                logger.warning(f"Invalid time or distance range in summary for event {self._current_event_id}")
                
        except sqlite3.Error as e:
            logger.error(f"Error updating event data: {e}")
            
    def _on_time_slider_released(self):
        """Handle time slider mouse release to update distance slider and record count."""
        if not self._db_connection or self._current_event_id is None:
            return
            
        try:
            time_min, time_max = self._time_slider.value()
            
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT MIN(veh_dist_trav), MAX(veh_dist_trav)
                FROM events 
                WHERE event_id = ? AND veh_time BETWEEN ? AND ?
            """, (self._current_event_id, time_min, time_max))
            
            result = cursor.fetchone()
            if result and all(x is not None for x in result):
                dist_min, dist_max = result
                
                # Adjust distance slider handle values within its range
                current_dist_min, current_dist_max = self._dist_slider.minimum(), self._dist_slider.maximum()
                adjusted_dist_min = max(dist_min, current_dist_min)
                adjusted_dist_max = min(dist_max, current_dist_max)
                
                self._dist_slider.setValue([adjusted_dist_min, adjusted_dist_max])
                
            self._update_record_count()
            
        except sqlite3.Error as e:
            logger.error(f"Error updating distance slider from time selection: {e}")
            
    def _on_dist_slider_released(self):
        """Handle distance slider mouse release to update time slider and record count."""
        if not self._db_connection or self._current_event_id is None:
            return
            
        try:
            dist_min, dist_max = self._dist_slider.value()
            
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT MIN(veh_time), MAX(veh_time)
                FROM events 
                WHERE event_id = ? AND veh_dist_trav BETWEEN ? AND ?
            """, (self._current_event_id, dist_min, dist_max))
            
            result = cursor.fetchone()
            if result and all(x is not None for x in result):
                time_min, time_max = result
                
                # Adjust time slider handle values within its range
                current_time_min, current_time_max = self._time_slider.minimum(), self._time_slider.maximum()
                adjusted_time_min = max(time_min, current_time_min)
                adjusted_time_max = min(time_max, current_time_max)
                
                self._time_slider.setValue([adjusted_time_min, adjusted_time_max])
                
            self._update_record_count()
            
        except sqlite3.Error as e:
            logger.error(f"Error updating time slider from distance selection: {e}")
            
    def _update_record_count(self):
        """Update the record count label and enable/disable Apply button."""
        if not self._db_connection or self._current_event_id is None:
            return
            
        try:
            time_min, time_max = self._time_slider.value()
            dist_min, dist_max = self._dist_slider.value()
            
            cursor = self._db_connection.cursor()
            cursor.execute("""
                SELECT COUNT(DISTINCT veh_id) 
                FROM events 
                WHERE event_id = ? 
                AND veh_time BETWEEN ? AND ? 
                AND veh_dist_trav BETWEEN ? AND ?
            """, (self._current_event_id, time_min, time_max, dist_min, dist_max))
            
            record_count = cursor.fetchone()[0]
            self._record_count_label.setText(f"{record_count} of {self._record_limit} vehicle limit")
            
            # Color the label based on record limit
            if record_count > self._record_limit:
                self._record_count_label.setStyleSheet("color: #F00; background-color: #000; font-weight: bold; padding: 2px; border-radius: 2px; border: 1px solid #999;")
            else:
                self._record_count_label.setStyleSheet("color: #0F0; background-color: #000; font-weight: bold; padding: 2px; border-radius: 2px; border: 1px solid #999;")
            
            # Enable/disable Apply button based on record limit
            self._apply_button.setEnabled(record_count <= self._record_limit)
            
        except sqlite3.Error as e:
            logger.error(f"Error updating record count: {e}")
            
    def _on_apply(self):
        """Handle Apply button click by emitting settings and closing dialog."""
        if self._current_event_id is not None:
            time_min, time_max = self._time_slider.value()
            dist_min, dist_max = self._dist_slider.value()
            
            logger.debug(f"Applying settings: event_id={self._current_event_id}, "
                f"time=[{time_min}, {time_max}], dist=[{dist_min}, {dist_max}]")
            # Emit signal with lane_ids and veh_ids included
            self.settings_applied.emit(self._current_event_id, 
                time_min, time_max, dist_min, dist_max, self._current_event_summary)
            self.accept()