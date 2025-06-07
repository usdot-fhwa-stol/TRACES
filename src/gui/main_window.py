# gui/main_window.py

import sqlite3
from pathlib import Path
from PyQt6.QtWidgets import QMainWindow, QMenuBar, QMenu, QApplication, QFileDialog, QMessageBox
from PyQt6.QtGui import QAction
from .time_space_plot_widget import TimeSpacePlotWidget
from .import_dialog import ImportDialog

class MainWindow(QMainWindow):
    """Main window for the Trajectory Tools application.

    Manages the main GUI window, menu bar, configuration file handling,
    and SQLite database connections for trajectory data.

    Attributes:
        _recent_files (list): List of up to 7 recent file paths.
        _recent_menu (QMenu): Submenu for recent files.
        _export_menu (QMenu): Submenu for export options.
        _db_connection (sqlite3.Connection): Current database connection.
        _plot_widget (TimeSpacePlotWidget): Plot widget for displaying trajectory data.
    """

    def __init__(self):
        """Initialize the main window with menu bar and load settings."""
        super().__init__()
        self._recent_files = []
        self._recent_menu = None
        self._export_menu = None
        self._db_connection = None
        self._plot_widget = None
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        """Set up the main window UI including menu bar and initial settings."""
        self.setWindowTitle("Trajectory Tools")
        
        # Create menu bar
        menu_bar = QMenuBar()
        self.setMenuBar(menu_bar)

        # File menu
        file_menu = QMenu("&File", self)
        menu_bar.addMenu(file_menu)

        # File menu items
        open_action = QAction("&Open...", self)
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)

        # Recent files submenu
        self._recent_menu = QMenu("&Recent", self)
        self._recent_menu.setEnabled(False)
        file_menu.addMenu(self._recent_menu)

        file_menu.addSeparator()

        # Import action
        import_action = QAction("&Import...", self)
        import_action.triggered.connect(self._import_file)
        file_menu.addAction(import_action)

        # Export submenu
        self._export_menu = QMenu("&Export...", self)
        self._export_menu.setEnabled(False)
        file_menu.addMenu(self._export_menu)

        # Export submenu items
        export_all_action = QAction("Export &All...", self)
        export_all_action.triggered.connect(self._export_all)
        self._export_menu.addAction(export_all_action)

        export_select_action = QAction("Export &Select...", self)
        export_select_action.triggered.connect(self._export_select)
        self._export_menu.addAction(export_select_action)

        file_menu.addSeparator()

        # About action
        about_action = QAction("A&bout", self)
        about_action.triggered.connect(self._show_about)
        file_menu.addAction(about_action)

        # Exit action
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _load_settings(self):
        """Load window settings and recent files from configuration file.

        Reads from 'trajectory_tools.conf' in the application directory.
        Sets default window size (80% of desktop) if config is invalid or missing.
        """
        config_path = Path("trajectory_tools.conf")
        desktop = QApplication.primaryScreen()
        screen_rect = desktop.availableGeometry()
        default_width = int(screen_rect.width() * 0.8)
        default_height = int(screen_rect.height() * 0.8)
        default_x = (screen_rect.width() - default_width) // 2
        default_y = (screen_rect.height() - default_height) // 2

        if config_path.exists():
            try:
                with config_path.open('r') as f:
                    lines = f.read().splitlines()
                    if lines:
                        # Parse window geometry
                        geometry = lines[0].split(',')
                        if len(geometry) == 5:
                            x, y, width, height, maximized = map(str.strip, geometry)
                            x, y, width, height = map(int, (x, y, width, height))
                            maximized = maximized.lower() == 'true'

                            # Validate position is on screen
                            if (0 <= x <= screen_rect.width() - 100 and 
                                0 <= y <= screen_rect.height() - 100):
                                self.setGeometry(x, y, width, height)
                                if maximized:
                                    self.showMaximized()
                            else:
                                self._set_default_geometry(default_x, default_y, 
                                                         default_width, default_height)

                            # Load recent files
                            self._recent_files = lines[1:8]  # Up to 7 files
                            self._update_recent_menu()
                        else:
                            self._set_default_geometry(default_x, default_y, 
                                                    default_width, default_height)
                    else:
                        self._set_default_geometry(default_x, default_y, 
                                                default_width, default_height)
            except (ValueError, IndexError, FileNotFoundError):
                self._set_default_geometry(default_x, default_y, 
                                        default_width, default_height)
        else:
            self._set_default_geometry(default_x, default_y, 
                                    default_width, default_height)

    def _set_default_geometry(self, x: int, y: int, width: int, height: int):
        """Set default window geometry.

        Args:
            x (int): X-coordinate of window position.
            y (int): Y-coordinate of window position.
            width (int): Window width.
            height (int): Window height.
        """
        self.setGeometry(x, y, width, height)

    def _save_settings(self):
        """Save window settings and recent files to configuration file."""
        config_path = Path("trajectory_tools.conf")
        maximized = 'true' if self.isMaximized() else 'false'
        geometry = self.normalGeometry() if not self.isMaximized() else self.geometry()
        config_data = [f"{geometry.x()},{geometry.y()},{geometry.width()},{geometry.height()},{maximized}"]
        config_data.extend(self._recent_files)

        with config_path.open('w') as f:
            f.write('\n'.join(config_data))

    def _update_recent_menu(self):
        """Update the recent files submenu with current file list."""
        self._recent_menu.clear()
        for file_path in self._recent_files:
            action = QAction(file_path, self)
            action.triggered.connect(lambda checked, path=file_path: self._open_recent_file(path))
            self._recent_menu.addAction(action)
        self._recent_menu.setEnabled(bool(self._recent_files))

    def _check_database(self, file_path: str) -> bool:
        """Check if the file is a valid SQLite database with correct events table.

        Args:
            file_path (str): Path to the SQLite database file.

        Returns:
            bool: True if database is valid and contains data, False otherwise.

        Raises:
            sqlite3.Error: If SQLite operations fail.
        """
        try:
            # Check if file is a valid SQLite database
            conn = sqlite3.connect(file_path)
            cursor = conn.cursor()

            # Check if events table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            if not cursor.fetchone():
                conn.close()
                QMessageBox.critical(self, "Error", "Database does not contain an 'events' table.")
                return False

            # Check table schema
            expected_columns = {
                "event_id": "INTEGER",
                "veh_id": "INTEGER",
                "event_time": "REAL",
                "lane_id": "INTEGER",
                "x_map_loc": "REAL",
                "y_map_loc": "REAL",
                "x_frenet": "REAL",
                "y_frenet": "REAL",
                "x_map_origin": "REAL",
                "y_map_origin": "REAL",
                "veh_lat": "REAL",
                "veh_lon": "REAL",
                "veh_speed": "REAL",
                "veh_acceleration": "REAL",
                "veh_length": "REAL",
                "veh_width": "REAL",
                "veh_automated": "INTEGER",
                "osm_way_id": "INTEGER",
                "osm_speed_limit": "INTEGER",
                "osm_traffic_control": "INTEGER",
                "preceding_veh_id": "INTEGER",
                "veh_dist": "REAL"
            }

            cursor.execute("PRAGMA table_info(events)")
            columns = {row[1]: row[2] for row in cursor.fetchall()}
            
            if columns != expected_columns:
                conn.close()
                QMessageBox.critical(self, "Error", 
                                   "Events table schema does not match expected format.")
                return False

            # Check for at least one record
            cursor.execute("SELECT COUNT(*) FROM events")
            if cursor.fetchone()[0] == 0:
                conn.close()
                QMessageBox.critical(self, "Error", "Events table is empty.")
                return False

            # If all checks pass, close any existing connection and set new one
            if self._db_connection:
                self._db_connection.close()
            self._db_connection = conn
            return True

        except sqlite3.Error as e:
            QMessageBox.critical(self, "Error", f"Failed to open database: {str(e)}")
            return False

    def _open_file(self):
        """Handle the Open menu action by showing file dialog and validating database."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open SQLite Database",
            "",
            "SQLite Database (*.sqlite)"
        )
        
        if file_path and self._check_database(file_path):
            self.add_recent_file(file_path)
            self.setWindowTitle(f"Trajectory Tools - {file_path}")
            if not self._plot_widget:
                self._plot_widget = TimeSpacePlotWidget()
                self.setCentralWidget(self._plot_widget)
            self._plot_widget.set_database(file_path, self._db_connection)
            self._export_menu.setEnabled(True)

    def _open_recent_file(self, file_path: str):
        """Open a file from the recent files list.

        Args:
            file_path (str): Path to the file to open.
        """
        if not Path(file_path).exists():
            QMessageBox.critical(self, "Error", f"File not found: {file_path}")
            self._recent_files.remove(file_path)
            self._update_recent_menu()
            return

        if self._check_database(file_path):
            self.add_recent_file(file_path)
            self.setWindowTitle(f"Trajectory Tools - {file_path}")
            if not self._plot_widget:
                self._plot_widget = TimeSpacePlotWidget()
                self.setCentralWidget(self._plot_widget)
            self._plot_widget.set_database(file_path, self._db_connection)
            self._export_menu.setEnabled(True)

    def _import_file(self):
        """Handle the Import menu action."""
        dialog = ImportDialog(self)
        if dialog.exec():
            output_path = dialog.get_output_path()
            if output_path and self._check_database(output_path):
                self.add_recent_file(output_path)
                self.setWindowTitle(f"Trajectory Tools - {output_path}")
                if not self._plot_widget:
                    self._plot_widget = TimeSpacePlotWidget()
                    self.setCentralWidget(self._plot_widget)
                self._plot_widget.set_database(output_path, self._db_connection)
                self._export_menu.setEnabled(True)

    def _export_all(self):
        """Handle the Export All menu action."""
        # To be implemented: Show export all dialog
        pass

    def _export_select(self):
        """Handle the Export Select menu action."""
        # To be implemented: Show export select dialog
        pass

    def _show_about(self):
        """Handle the About menu action."""
        # To be implemented: Show about dialog
        pass

    def add_recent_file(self, file_path: str):
        """Add a file to the recent files list.

        Args:
            file_path (str): Path to the file to add.
        """
        file_path = str(Path(file_path).absolute())
        if file_path in self._recent_files:
            self._recent_files.remove(file_path)
        self._recent_files.insert(0, file_path)
        self._recent_files = self._recent_files[:7]  # Keep only 7 recent files
        self._update_recent_menu()

    def closeEvent(self, event):
        """Handle window close event and save settings.

        Args:
            event (QCloseEvent): The close event.
        """
        if self._db_connection:
            self._db_connection.close()
        self._save_settings()
        super().closeEvent(event)