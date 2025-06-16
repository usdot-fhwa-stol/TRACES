import sqlite3
import logging
from pathlib import Path
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, 
                           QLabel, QComboBox, QDialog)
from PyQt6.QtCore import Qt
import pyqtgraph as pg
from superqt import QLabeledRangeSlider
from .time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog

# Set up logger
logger = logging.getLogger(__name__)


class TimeSpacePlotWidget(QWidget):
    """Widget for displaying time-space trajectory plots with interactive controls.
    
    The widget displays a scatter plot of vehicle trajectories on the left and provides
    interactive controls on the right for filtering and manipulating the data view.
    
    Attributes:
        _db_path (str): Path to the SQLite database file.
        _db_connection (sqlite3.Connection): Database connection for querying.
        _scatter_plot (pg.PlotWidget): PyQtGraph scatter plot widget.
        _event_button (QPushButton): Button to open settings dialog.
        _lane_combobox (QComboBox): Combobox for lane selection.
        _dist_slider (QLabeledRangeSlider): Vertical slider for distance filtering.
        _time_slider (QLabeledRangeSlider): Vertical slider for time filtering.
        _export_button (QPushButton): Button for exporting data (initially disabled).
        _hide_show_button (QPushButton): Button for hide/show functionality (initially disabled).
        _current_event_id (int): Currently selected event ID.
        _time_range (tuple): Current time range (min, max).
        _dist_range (tuple): Current distance range (min, max).
    """
    
    def __init__(self, parent=None):
        """Initialize the TimeSpacePlotWidget.
        
        Args:
            parent: Parent widget (typically MainWindow).
        """
        super().__init__(parent)
        self._db_path = ""
        self._db_connection = None
        self._scatter_plot = None
        self._event_button = None
        self._lane_combobox = None
        self._dist_slider = None
        self._time_slider = None
        self._export_button = None
        self._hide_show_button = None
        self._current_event_id = None
        self._time_range = (0.0, 0.0)
        self._dist_range = (0.0, 0.0)
        
        logger.debug("TimeSpacePlotWidget initialized")
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Set up the widget UI components."""
        # Create main horizontal layout
        main_layout = QHBoxLayout(self)
        
        # Left side: scatter plot
        self._scatter_plot = pg.PlotWidget()
        self._scatter_plot.setBackground('w')
        self._scatter_plot.showGrid(x=True, y=True)
        self._scatter_plot.setLabel('left', 'Distance (m)')
        self._scatter_plot.setLabel('bottom', 'Time (s)')
        main_layout.addWidget(self._scatter_plot, stretch=1)  # Stretch to fill available space
        
        # Right side: control set
        control_layout = QVBoxLayout()
        
        # Event button
        self._event_button = QPushButton(" ")
        self._event_button.clicked.connect(self._open_settings_dialog)
        control_layout.addWidget(self._event_button)
        
        # Lane combobox
        self._lane_combobox = QComboBox()
        control_layout.addWidget(self._lane_combobox)
        
        # Distance slider section
        dist_label = QLabel("Dist (m)")
        dist_label.setStyleSheet("font-weight: bold;")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_layout.addWidget(dist_label)
        
        self._dist_slider = QLabeledRangeSlider()
        self._dist_slider.setOrientation(Qt.Orientation.Vertical)
        control_layout.addWidget(self._dist_slider, stretch=1)  # Stretch to fill available space
        
        # Time slider section
        time_label = QLabel("Time (s)")
        time_label.setStyleSheet("font-weight: bold;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_layout.addWidget(time_label)
        
        self._time_slider = QLabeledRangeSlider()
        self._time_slider.setOrientation(Qt.Orientation.Vertical)
        control_layout.addWidget(self._time_slider, stretch=1)  # Stretch to fill available space
        
        # Export button (initially disabled)
        self._export_button = QPushButton("Export...")
        self._export_button.setEnabled(False)
        control_layout.addWidget(self._export_button)
        
        # Hide/Show button (initially disabled with space label)
        self._hide_show_button = QPushButton(" ")
        self._hide_show_button.setEnabled(False)
        control_layout.addWidget(self._hide_show_button)
        
        main_layout.addLayout(control_layout)
        
    def set_database(self, db_path: str, db_connection: sqlite3.Connection):
        """Set the database connection and path for the widget.
        
        Args:
            db_path (str): Path to the SQLite database file.
            db_connection (sqlite3.Connection): Database connection for querying.
        """
        self._db_path = db_path
        self._db_connection = db_connection
        self._scatter_plot.setTitle(Path(db_path).name)
        logger.debug(f"Database set: {db_path}")
        
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
        
        # Update event button label
        self._event_button.setText(f"Event {event_id:3d}")
        
        # Update lane combobox
        self._populate_lane_combobox()

        # Set distance slider range and values
        self._dist_slider.setRange(dist_min, dist_max)
        self._dist_slider.setValue(self._dist_range)

        # Set time slider range and values
        self._time_slider.setRange(time_min, time_max)
        self._time_slider.setValue(self._time_range)

        # TODO: Update plot data
        # self._update_plot_data()
        
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
                
            # Default to first item
            if lanes:
                self._lane_combobox.setCurrentIndex(0)
                
        except sqlite3.Error as e:
            logger.error(f"Error populating lane combobox: {e}")

    def get_current_event_id(self) -> int:
        """Get the currently selected event ID.
        
        Returns:
            int: Current event ID or None if no event selected.
        """
        return self._current_event_id