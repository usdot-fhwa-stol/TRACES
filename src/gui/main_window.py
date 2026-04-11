from pathlib import Path
from PyQt6.QtWidgets import QMainWindow, QMenuBar, QMenu, QApplication, QFileDialog, QMessageBox, QDialog
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QTimer
from .home_widget import HomeWidget
from utils import get_user_data_path

class MainWindow(QMainWindow):
    """Main window for the Trajectory Tools application.

    Manages the main GUI window, menu bar, configuration file handling,
    and SQLite database connections for trajectory data.

    Attributes:
        _recent_files (list): List of up to 7 recent file paths.
        _recent_menu (QMenu): Submenu for recent files.
        _export_menu (QMenu): Submenu for export options.
        _mode_menu (QMenu): Top-level menu used by plots to set interaction mode.
        _db_connection (sqlite3.Connection): Current database connection.
        _plot_widget (TimeSpacePlotWidget): Plot widget for displaying trajectory data.
        _selected_export_columns (list): List of selected column names for export.
    """

    def __init__(self):
        """Initialize the main window with menu bar and load settings."""
        super().__init__()
        self._recent_files = []
        self._recent_menu = None
        self._export_menu = None
        self._mode_menu = None
        self._db_connection = None
        self._plot_widget = None
        self._import_dialog = None
        self._selected_export_columns = []  # Initially empty list of selected export columns
        self._home_widget = None
        self._log_viewer_dialog = None
        
        self._setup_ui()
        self._load_settings()
        self._setup_home_page()
        self._load_home_page()

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

        export_select_action = QAction("Export &Selected...", self)  # Updated label
        export_select_action.triggered.connect(self._export_selected)
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

        # View menu
        view_menu = QMenu("&View", self)
        menu_bar.addMenu(view_menu)
        
        log_action = QAction("&Log", self)
        log_action.triggered.connect(self._show_log_viewer)
        view_menu.addAction(log_action)

        self._mode_menu = QMenu("&Mode", self)
        self._mode_menu.setEnabled(False)
        menu_bar.addMenu(self._mode_menu)

    def _setup_home_page(self):
        self._home_widget = HomeWidget()
        self._home_widget.open_file_requested.connect(self._open_file)
        self._home_widget.import_file_requested.connect(self._import_file)
        self._home_widget.recent_file_requested.connect(self._open_recent_file)
        self.setCentralWidget(self._home_widget)
    
    def _load_home_page(self):
        if self._home_widget:
            self._home_widget.refresh(self._recent_files)

    def _load_settings(self):
        """Load window settings and recent files from configuration file.

        Reads from 'trajectory_tools.conf' in the application directory.
        Sets default window size (80% of desktop) if config is invalid or missing.
        """
        config_path = get_user_data_path("trajectory_tools.conf")
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
        config_path = get_user_data_path("trajectory_tools.conf")
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
    
            # Update home widget if it is currently the central widget
            if self._home_widget and self.centralWidget() == self._home_widget:
                self._home_widget.refresh(self._recent_files)

    def _check_database(self, file_path: str) -> bool:
        """Check if the file is a valid SQLite database with correct events table.

        Args:
            file_path (str): Path to the SQLite database file.

        Returns:
            bool: True if database is valid and contains data, False otherwise.

        Raises:
            sqlite3.Error: If SQLite operations fail.
        """
        import sqlite3
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
                "veh_time": "REAL",
                "lane_id": "INTEGER",
                "orig_lane_id": "INTEGER",
                "x_map_loc": "REAL",
                "y_map_loc": "REAL",
                "x_frenet_loc": "REAL",
                "y_frenet_loc": "REAL",
                "x_map_origin": "REAL",
                "y_map_origin": "REAL",
                "veh_lat": "REAL",
                "veh_lon": "REAL",
                "veh_speed": "REAL",
                "veh_accel": "REAL",
                "veh_length": "REAL",
                "veh_width": "REAL",
                "veh_automation": "INTEGER",
                "osm_way_id": "INTEGER",
                "osm_speed_limit": "INTEGER",
                "osm_traffic_control": "INTEGER",
                "preceding_veh_id": "INTEGER",
                "veh_dist_trav": "REAL",
                "event_name": "TEXT"
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
            "SQLite Database (*.sqlite);;SQLite Database (*.db);;SQLite Database (*)"
        )
        
        if file_path and self._check_database(file_path):
            self.add_recent_file(file_path)
            self.setWindowTitle(f"Trajectory Tools - {file_path}")
            
            # Show settings dialog before creating plot widget
            from plot.time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
            settings_dialog = TimeSpacePlotSettingsDialog(parent=self, db_connection=self._db_connection)
            settings_dialog.settings_applied.connect(self._create_plot_widget)
            
            result = settings_dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                # Plot widget will be created in _create_plot_widget callback
                self._export_menu.setEnabled(True)
            else:
                # User cancelled settings dialog, close database and reset window
                self._close_database_and_reset()

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
            
            # Show settings dialog before creating plot widget
            from plot.time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
            settings_dialog = TimeSpacePlotSettingsDialog(parent=self, db_connection=self._db_connection)
            settings_dialog.settings_applied.connect(self._create_plot_widget)
            
            result = settings_dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                # Plot widget will be created in _create_plot_widget callback
                self._export_menu.setEnabled(True)
            else:
                # User cancelled settings dialog, close database and reset window
                self._close_database_and_reset()

    def _import_file(self):
        """Handle the Import menu action."""
        from .import_dialog import ImportDialog
        dialog = ImportDialog(self)
        dialog.setModal(True)
        # Connect to the import_finished signal to receive the output path
        dialog.import_finished.connect(self._handle_import_finished)
        dialog.show()
        self._import_dialog = dialog

    def _handle_import_finished(self, output_path: str):
        """Handle the import finished signal from ImportDialog.

        Args:
            output_path (str): Path to the imported SQLite database file.
        """
        if output_path and self._check_database(output_path):
            print(output_path)
            self.add_recent_file(output_path)
            self.setWindowTitle(f"Trajectory Tools - {output_path}")
            
            # Use QTimer to delay showing settings dialog to allow import dialog to close
            QTimer.singleShot(200, lambda: self._show_settings_for_imported_file())

        self._import_dialog = None

    def _show_settings_for_imported_file(self):
        """Show the settings dialog for the imported file after import dialog closes."""
        if self._db_connection:
            # Show settings dialog before creating plot widget
            from plot.time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
            settings_dialog = TimeSpacePlotSettingsDialog(parent=self, db_connection=self._db_connection)
            settings_dialog.settings_applied.connect(self._create_plot_widget)
            
            result = settings_dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                # Plot widget will be created in _create_plot_widget callback
                self._export_menu.setEnabled(True)
            else:
                # User cancelled settings dialog, close database and reset window
                self._close_database_and_reset()

    def _close_database_and_reset(self):
        """Close the current database connection and reset the window state."""
        # Close database connection
        if self._db_connection:
            self._db_connection.close()
            self._db_connection = None
        
        # Reset window title
        self.setWindowTitle("Trajectory Tools")
        
        # Remove plot widget if it exists and show home page
        if self._plot_widget:
            self._plot_widget.setParent(None)
            self._plot_widget.deleteLater()
            self._plot_widget = None
        
        # Show home page again
        self.setCentralWidget(self._home_widget)
        self._load_home_page()
        
        # Disable export menu
        self._export_menu.setEnabled(False)
        
        # Reset plot mode menu
        self._mode_menu.setEnabled(False)
        self._mode_menu.clear()

    def _create_plot_widget(self, event_id: int, time_min: float, time_max: float, 
                           dist_min: float, dist_max: float, event_summary: dict):
        """Create and configure the plot widget with settings from the dialog.
        
        Args:
            event_id (int): Selected event ID.
            time_min (float): Minimum time value.
            time_max (float): Maximum time value.
            dist_min (float): Minimum distance value.
            dist_max (float): Maximum distance value.
            event_summary (dict) dictionary object containing rollup for event
        """
        from plot.time_space_plot_widget import TimeSpacePlotWidget
        if not self._plot_widget:
            self._plot_widget = TimeSpacePlotWidget(mode_menu=self._mode_menu)
        
        # Set database connection and apply settings with all parameters
        self._plot_widget.set_database(self._db_connection)
        self._plot_widget._apply_settings(event_id, time_min, time_max, dist_min, dist_max, event_summary)
        self.setCentralWidget(self._plot_widget)

    def _export_all(self):
        """Handle the Export All menu action."""
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return

        from .export_dialog import ExportDialog
        dialog = ExportDialog(parent=self, db_connection=self._db_connection, selected_columns=None)
        dialog.setModal(True)
        dialog.export_finished.connect(self._handle_export_finished)
        dialog.exec()

    def _export_selected(self):
        """Handle the Export Selected menu action."""
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return
            
        from .export_dialog import ExportDialog
        dialog = ExportDialog(parent=self, db_connection=self._db_connection, 
                            selected_columns=self._selected_export_columns.copy())
        dialog.setModal(True)
        dialog.export_finished.connect(self._handle_export_finished)
        dialog.exec()

    def _handle_export_finished(self, selected_columns: list):
        """Handle the export finished signal from ExportDialog.

        Args:
            selected_columns (list): List of selected column names for future exports.
        """
        # Only update the selected export columns list if we received a non-empty list
        # (i.e., only from Export Selected mode, not Export All mode)
        if selected_columns:
            self._selected_export_columns = selected_columns.copy()

    def _show_about(self):
        """Handle the About menu action by showing the AboutDialog."""
        from .about_dialog import AboutDialog
        about_dialog = AboutDialog(parent=self, is_splash=False)
        about_dialog.exec()

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

    def _show_log_viewer(self) -> None:
        """Show the modeless System Log Viewer dialog.
    
        If the dialog is already open and minimized, restore it.  If it is
        visible and not minimized, bring it to the front.  If it does not exist
        yet, create it.
        """
        if self._log_viewer_dialog is not None and self._log_viewer_dialog.isVisible():
            if self._log_viewer_dialog.isMinimized():
                self._log_viewer_dialog.showNormal()
            self._log_viewer_dialog.raise_()
            self._log_viewer_dialog.activateWindow()
            return

        from .log_viewer_dialog import LogViewerDialog    
        self._log_viewer_dialog = LogViewerDialog(parent=self)
        self._log_viewer_dialog.show()

    def closeEvent(self, event):
        """Handle window close event and save settings.

        Args:
            event (QCloseEvent): The close event.
        """
        if self._log_viewer_dialog is not None:
            self._log_viewer_dialog.close()

        if self._db_connection:
            self._db_connection.close()

        self._save_settings()
        super().closeEvent(event)