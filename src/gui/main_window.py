import logging
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QMenuBar, QMenu, QApplication, QFileDialog, QMessageBox,
    QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox,
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QTimer
from .home_widget import HomeWidget
from utils import get_user_data_path, last_dialog_dir, remember_dialog_path
from .database_session import DatabaseSession

# Set up logger
logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Main window for the TRACES application.

    Manages the main GUI window, menu bar, configuration file handling,
    and SQLite database connections for trajectory data.

    Attributes:
        _recent_files (list): List of up to 7 recent file paths.
        _recent_menu (QMenu): Submenu for recent files.
        _export_menu (QMenu): Submenu for export options.
        _mode_menu (QMenu): Top-level menu used by plots to set interaction mode.
        _data_menu (QMenu): Top-level menu used by plots to perform data manipulation.
        _analysis_menu (QMenu): Top-level Analysis menu; holds the Noise submenu.
        _db_connection (sqlite3.Connection): Current database connection.
        _plot_widget (TimeSpacePlotWidget): Plot widget for displaying trajectory data.
        _selected_export_columns (list): List of selected column names for export.
        _search_vehicle_action (QAction): View > Search Vehicle menu action;
            enabled/disabled by TimeSpacePlotSettingsDialog.
        _search_dialog (VehicleSearchDialog): Modeless vehicle search dialog, or None.
    """

    def __init__(self):
        """Initialize the main window with menu bar and load settings."""
        super().__init__()
        self._recent_files = []
        self._recent_menu = None
        self._export_menu = None
        self._mode_menu = None
        self._data_menu = None
        self._db_session = DatabaseSession(self)
        self._db_session.database_switched.connect(self._on_database_switched)
        self._plot_widget = None
        self._import_dialog = None
        self._selected_export_columns = []  # Initially empty list of selected export columns
        self._search_vehicle_action = None
        self._search_dialog = None
        self._home_widget = None
        self._log_viewer_dialog = None
        self._help_dialogs = {}
        
        self._setup_ui()
        self._load_settings()
        self._setup_home_page()
        self._load_home_page()

    @property
    def _db_connection(self):
        """The open connection (owned by DatabaseSession), or None."""
        return self._db_session.connection

    def _setup_ui(self):
        """Set up the main window UI including menu bar and initial settings."""
        self.setWindowTitle("TRACES")

        # Create menu bar
        menu_bar = QMenuBar()
        self.setMenuBar(menu_bar)

        # File menu
        file_menu = QMenu("&File", self)
        menu_bar.addMenu(file_menu)

        # File menu items
        open_action = QAction("&Open", self)
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)

        # Recent files submenu
        self._recent_menu = QMenu("&Recent", self)
        self._recent_menu.setEnabled(False)
        file_menu.addMenu(self._recent_menu)

        file_menu.addSeparator()

        # Import action
        import_action = QAction("&Import", self)
        import_action.triggered.connect(self._import_file)
        file_menu.addAction(import_action)

        # Export submenu
        self._export_menu = QMenu("&Export", self)
        self._export_menu.setEnabled(False)
        file_menu.addMenu(self._export_menu)

        # Export submenu items
        export_all_action = QAction("Export &All", self)
        export_all_action.triggered.connect(self._export_all)
        self._export_menu.addAction(export_all_action)

        export_select_action = QAction("Export &Selected", self)  # Updated label
        export_select_action.triggered.connect(self._export_selected)
        self._export_menu.addAction(export_select_action)

        export_analysis_menu = QMenu("Export &Analysis", self)
        self._export_menu.addMenu(export_analysis_menu)

        export_jerk_action = QAction("&Jerk Analysis", self)
        export_jerk_action.triggered.connect(self._export_jerk_analysis)
        export_analysis_menu.addAction(export_jerk_action)

        export_missing_data_action = QAction("&Missing Data", self)
        export_missing_data_action.triggered.connect(self._export_missing_data_analysis)
        export_analysis_menu.addAction(export_missing_data_action)

        export_ic_action = QAction("&Internal Consistency", self)
        export_ic_action.triggered.connect(self._export_internal_consistency)
        export_analysis_menu.addAction(export_ic_action)

        export_platoon_action = QAction("&Platoon Consistency", self)
        export_platoon_action.triggered.connect(self._export_platoon_consistency)
        export_analysis_menu.addAction(export_platoon_action)
        export_spectral_action = QAction("&Spectral Analysis", self)
        export_spectral_action.triggered.connect(self._export_spectral_analysis)
        export_analysis_menu.addAction(export_spectral_action)

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

        self._search_vehicle_action = QAction("&Search Vehicle", self)
        self._search_vehicle_action.setEnabled(False)
        self._search_vehicle_action.triggered.connect(self._open_vehicle_search_dialog)
        self._search_vehicle_action.changed.connect(self._on_search_action_changed)
        view_menu.addAction(self._search_vehicle_action)

        log_action = QAction("&Log", self)
        log_action.triggered.connect(self._show_log_viewer)
        view_menu.addAction(log_action)

        # Data menu
        self._data_menu = QMenu("&Data", self)
        menu_bar.addMenu(self._data_menu)
        self._data_menu.menuAction().setVisible(False)  # hidden until DB is open

        spline_fitting_action = QAction("&Spline Fitting", self)
        spline_fitting_action.triggered.connect(self._run_spline_fitting)
        self._data_menu.addAction(spline_fitting_action)

        gap_interp_action = QAction("&Gap Interpolation", self)
        gap_interp_action.triggered.connect(self._run_gap_interpolation)
        self._data_menu.addAction(gap_interp_action)

        lane_anomaly_action = QAction("&Lane-Level Anomalies", self)
        lane_anomaly_action.triggered.connect(self._run_lane_anomalies)
        self._data_menu.addAction(lane_anomaly_action)

        # Analysis menu
        self._analysis_menu = QMenu("&Analysis", self)
        menu_bar.addMenu(self._analysis_menu)
        self._analysis_menu.menuAction().setVisible(False)  # hidden until DB is open

        # Noise submenu (mirrors the right-click Noise Analysis submenu)
        noise_menu = QMenu("&Noise", self)
        self._analysis_menu.addMenu(noise_menu)

        jerk_action = QAction("&Jerk Analysis", self)
        jerk_action.triggered.connect(self._run_jerk_analysis)
        noise_menu.addAction(jerk_action)

        missing_data_action = QAction("&Missing Data", self)
        missing_data_action.triggered.connect(self._run_missing_data_analysis)
        noise_menu.addAction(missing_data_action)

        ic_action = QAction("&Internal Consistency", self)
        ic_action.triggered.connect(self._run_internal_consistency)
        noise_menu.addAction(ic_action)

        platoon_action = QAction("&Platoon Consistency", self)
        platoon_action.triggered.connect(self._run_platoon_consistency)
        noise_menu.addAction(platoon_action)

        spectral_action = QAction("S&pectral Analysis", self)
        spectral_action.triggered.connect(self._run_spectral_analysis)
        noise_menu.addAction(spectral_action)

        self._mode_menu = QMenu("&Mode", self)
        menu_bar.addMenu(self._mode_menu)
        self._mode_menu.menuAction().setVisible(False)

        help_menu = menu_bar.addMenu("&Help")
        self._populate_mode_help(help_menu)

    def _populate_mode_help(self, help_menu):
        """Provide read-only plot controls without changing the active mode."""
        mode_instructions = {
            "Time-Space Diagram": (
                "<ul>"
                "<li><b>Left-click</b> a trajectory to select or deselect it.</li>"
                "<li><b>Left-drag</b> a box to select the trajectories inside it.</li>"
                "<li>Hold <b>Shift</b> and <b>left-drag</b> a box to remove trajectories from the selection.</li>"
                "<li>Press <b>Esc</b> to clear the selection.</li>"
                "<li><b>Right-click</b> to open the plot context menu.</li>"
                "<li>Scroll the <b>mouse wheel</b> to zoom around the cursor.</li>"
                "</ul>"
            ),
            "Gap Headway": (
                "<ul>"
                "<li><b>Hover</b> near a trajectory point to preview its gap and headway.</li>"
                "<li><b>Left-click</b> the point to lock or unlock the gap and headway.</li>"
                "<li>Press <b>Esc</b> to clear all locked gaps and headways.</li>"
                "<li><b>Right-click</b> to open the plot context menu.</li>"
                "<li>Scroll the <b>mouse wheel</b> to zoom around the cursor.</li>"
                "</ul>"
            ),
            "Shockwave": (
                "<p><b>Quick Instructions:</b></p><ul>"
                "<li><b>Left-click</b> a marker to start or extend a shockwave.</li>"
                "<li>Press <b>Enter</b> to commit the shockwave. Select at least two points.</li>"
                "<li>Press <b>Esc</b> to cancel the shockwave currently in progress.</li>"
                "<li>Hold <b>Ctrl</b> and <b>right-click</b> a committed shockwave point to delete the entire shockwave.</li>"
                "<li><b>Left-click</b> the results panel title to open the shockwave results table.</li>"
                "<li><b>Right-click</b> to open the plot context menu.</li>"
                "<li>Scroll the <b>mouse wheel</b> to zoom around the cursor.</li>"
                "</ul><p><b>Edit a Shockwave:</b></p><ul>"
                "<li>Hold <b>Ctrl</b> and <b>left-click</b> a committed shockwave point to start editing that shockwave.</li>"
                "<li>While editing, hold <b>Ctrl</b> and <b>right-click</b> a selected point to remove it.</li>"
                "<li>Press <b>Enter</b> to save the edited shockwave.</li>"
                "<li>Press <b>Esc</b> to cancel the edit and restore the original shockwave.</li>"
                "</ul>"
            ),
        }
        for mode_name, instructions in mode_instructions.items():
            action = help_menu.addAction(mode_name)
            action.triggered.connect(
                lambda checked=False, name=mode_name, commands=instructions:
                    self._show_mode_help(name, commands)
            )

    def _show_mode_help(self, mode_name, instructions):
        """Keep controls visible in a separate window while using the plot."""
        dialog = self._help_dialogs.get(mode_name)
        if dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(f"{mode_name} — Help")
            dialog.resize(620, 420)
            layout = QVBoxLayout(dialog)
            commands = QTextBrowser(dialog)
            commands.setHtml(instructions)
            layout.addWidget(commands)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
            buttons.rejected.connect(dialog.close)
            layout.addWidget(buttons)
            self._help_dialogs[mode_name] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

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

        Reads from 'traces.conf' in the application directory.
        Sets default window size (80% of desktop) if config is invalid or missing.
        """
        config_path = get_user_data_path("traces.conf")
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
        config_path = get_user_data_path("traces.conf")
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
        """Check if the file is a valid trajectory database.

        With the config-driven import schema, databases no longer share a
        fixed column layout. A valid database is one that:
            * contains an ``events`` table with the required columns
              (event_id, veh_id, veh_time, lane_id, veh_dist_trav),
            * contains a ``ranges`` table (created last by the import
              worker, so its presence also marks a completed import),
            * has at least one record in ``events``.

        All other columns are optional; consumers must check for them via
        ``PRAGMA table_info(events)`` before use.

        Args:
            file_path (str): Path to the SQLite database file.

        Returns:
            bool: True if the database is valid and contains data, False otherwise.
        """
        ok, error = self._db_session.open(file_path)
        if not ok:
            QMessageBox.critical(self, "Error", error)
        return ok

    def _open_file(self):
        """Handle the Open menu action by showing file dialog and validating database."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open SQLite Database",
            last_dialog_dir(),
            "SQLite Database (*.sqlite);;SQLite Database (*.db);;SQLite Database (*)"
        )

        if not file_path:
            return
        remember_dialog_path(file_path)

        if self._check_database(file_path):
            self.add_recent_file(file_path)
            self.setWindowTitle(f"TRACES - {file_path}")

            # Show settings dialog before creating plot widget
            from plot.time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
            settings_dialog = TimeSpacePlotSettingsDialog(parent=self,
                db_connection=self._db_connection, search_action=self._search_vehicle_action)
            settings_dialog.settings_applied.connect(self._create_plot_widget)

            result = settings_dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                # Plot widget will be created in _create_plot_widget callback
                self._export_menu.setEnabled(True)
                self._data_menu.menuAction().setVisible(True)
                self._analysis_menu.menuAction().setVisible(True)
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
            self.setWindowTitle(f"TRACES - {file_path}")

            # Show settings dialog before creating plot widget
            from plot.time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
            settings_dialog = TimeSpacePlotSettingsDialog(parent=self,
                db_connection=self._db_connection, search_action=self._search_vehicle_action)
            settings_dialog.settings_applied.connect(self._create_plot_widget)

            result = settings_dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                # Plot widget will be created in _create_plot_widget callback
                self._export_menu.setEnabled(True)
                self._data_menu.menuAction().setVisible(True)
                self._analysis_menu.menuAction().setVisible(True)
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
            self.setWindowTitle(f"TRACES - {output_path}")

            # Use QTimer to delay showing settings dialog to allow import dialog to close
            QTimer.singleShot(200, lambda: self._show_settings_for_imported_file())

        self._import_dialog = None

    def _show_settings_for_imported_file(self):
        """Show the settings dialog for the imported file after import dialog closes."""
        if self._db_connection:
            # Show settings dialog before creating plot widget
            from plot.time_space_plot_settings_dialog import TimeSpacePlotSettingsDialog
            settings_dialog = TimeSpacePlotSettingsDialog(parent=self,
                db_connection=self._db_connection, search_action=self._search_vehicle_action)
            settings_dialog.settings_applied.connect(self._create_plot_widget)

            result = settings_dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                # Plot widget will be created in _create_plot_widget callback
                self._export_menu.setEnabled(True)
                self._data_menu.menuAction().setVisible(True)
                self._analysis_menu.menuAction().setVisible(True)
            else:
                # User cancelled settings dialog, close database and reset window
                self._close_database_and_reset()

    def _close_database_and_reset(self):
        """Close the current database connection and reset the window state."""
        if self._search_dialog:
            self._search_dialog.close()
        self._search_vehicle_action.setEnabled(False)

        # Close database connection
        self._db_session.close()

        # Reset window title
        self.setWindowTitle("TRACES")

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
        self._data_menu.menuAction().setVisible(False)
        self._analysis_menu.menuAction().setVisible(False)

        # Reset plot mode menu
        self._mode_menu.menuAction().setVisible(False)
        self._mode_menu.clear()

    def _on_database_switched(self, new_conn, old_conn, new_path: str):
        """DatabaseSession copied the open database and switched to the
        copy. Keep the current view: swap the plot widget's connection
        rather than rebuilding it."""
        self._close_dialogs_using(old_conn)
        if self._plot_widget is not None:
            self._plot_widget.set_db_connection(new_conn)
        self.add_recent_file(new_path)
        self.setWindowTitle(f"TRACES - {new_path}")
        logger.info(f"Now working in {new_path}")

    def _close_dialogs_using(self, conn):
        """Close visible modeless dialogs still holding *conn* (e.g. a Spline
        Fitting preview), before that connection is closed under them.

        Dialogs that set KEEPS_OPEN_ON_DB_SWITCH (GapConfigDialog) requested
        the switch themselves and move onto the new connection, so they
        are left open."""
        from PyQt6.QtWidgets import QDialog
        for dlg in self.findChildren(QDialog):
            if getattr(dlg, "KEEPS_OPEN_ON_DB_SWITCH", False):
                continue
            if dlg.isVisible() and getattr(dlg, "_db_connection", None) is conn:
                logger.debug(f"Closing {type(dlg).__name__}: its database was replaced")
                dlg.close()

    def _on_vehicle_ids_changed(self):
        """Trajectories were added/removed (e.g. by stitching): refresh the
        vehicle search dialog if it's open."""
        if self._search_dialog is not None and self._plot_widget is not None:
            self._search_dialog.set_vehicle_ids(self._plot_widget.get_vehicle_ids())

    def _on_event_data_changed(self, event_id: int):
        """A data module (Gap Interpolation) changed the events table of
        *event_id*. If the plot shows that event, reload it from the
        database -- sliders, view range, lane, selection, mode and color
        mode are kept -- so filled or removed gaps appear immediately."""
        widget = self._plot_widget
        if widget is None:
            return
        if widget._current_event_id != event_id:
            logger.info(f"Event {event_id} data changed; plot shows event "
                        f"{widget._current_event_id}, not reloading")
            return
        logger.info(f"Event {event_id} data changed; reloading plot")
        # Deferred to the next event-loop pass, after the closing dialog is
        # fully gone -- the same path a lane change takes.
        QTimer.singleShot(0, widget._update_plot_data)

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
            self._plot_widget._stitch_target_resolver = self._db_session.ensure_write_target
            self._plot_widget.vehicle_ids_changed.connect(self._on_vehicle_ids_changed)
            self._plot_widget.set_search_action(self._search_vehicle_action)

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

    def _export_jerk_analysis(self):
        """Handle the Export Analysis → Jerk Analysis menu action."""
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return

        # Step 1: ask the user which events to include.
        from .event_selection_dialog import EventSelectionDialog
        sel_dlg = EventSelectionDialog(self, self._db_connection)
        if sel_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        event_ids = sel_dlg.selected_event_ids
        if not event_ids:
            return

        # Step 2: show JerkCompDialog in export mode (Save button).
        from analysis.jerk import JerkCompDialog
        jerk_dlg = JerkCompDialog(self, self._db_connection, event_ids=event_ids, export_mode=True)
        jerk_dlg.exec()

    def _export_spectral_analysis(self):
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return
        from gui.event_selection_dialog import EventSelectionDialog
        from analysis.spectral import SpectralDialog
        selection = EventSelectionDialog(self, self._db_connection)
        if selection.exec() != QDialog.DialogCode.Accepted or not selection.selected_event_ids:
            return
        dlg = SpectralDialog(self, self._db_connection,
                             event_ids=selection.selected_event_ids, export_mode=True)
        dlg.exec()

    def _run_spectral_analysis(self):
        if not self._db_connection or self._plot_widget._current_event_id is None:
            return
        from analysis.spectral import SpectralDialog
        dlg = SpectralDialog(self, self._db_connection,
                             event_ids=[self._plot_widget._current_event_id])
        dlg.exec()

    def _export_platoon_consistency(self):
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return
        from gui.event_selection_dialog import EventSelectionDialog
        from analysis.platoon import PlatoonConsistencyDialog
        selection = EventSelectionDialog(self, self._db_connection)
        if selection.exec() != QDialog.DialogCode.Accepted or not selection.selected_event_ids:
            return
        dlg = PlatoonConsistencyDialog(
            self, self._db_connection,
            event_ids=selection.selected_event_ids, export_mode=True)
        dlg.exec()

    def _run_platoon_consistency(self):
        if not self._db_connection or self._plot_widget._current_event_id is None:
            return
        from analysis.platoon import PlatoonConsistencyDialog
        dlg = PlatoonConsistencyDialog(
            self, self._db_connection,
            event_ids=[self._plot_widget._current_event_id])
        dlg.exec()

    def _export_internal_consistency(self):
        if not self._db_connection:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return

        from PyQt6.QtWidgets import QDialog
        from .event_selection_dialog import EventSelectionDialog
        sel_dlg = EventSelectionDialog(self, self._db_connection)
        if sel_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        event_ids = sel_dlg.selected_event_ids
        if not event_ids:
            return

        from analysis.internal_consistency import InternalConsistencyDialog
        dlg = InternalConsistencyDialog(
            self, self._db_connection,
            event_ids=event_ids, export_mode=True)
        dlg.exec()

    def _export_missing_data_analysis(self):
        """Handle the Export Analysis → Missing Data Analysis menu action."""
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return

        # Step 1: ask the user which events to include.
        from .event_selection_dialog import EventSelectionDialog
        sel_dlg = EventSelectionDialog(self, self._db_connection)
        if sel_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        event_ids = sel_dlg.selected_event_ids
        if not event_ids:
            return

        # Step 2: show MissingDataCompDialog in export mode (Save… button).
        from analysis.missing_data import MissingDataCompDialog
        md_dlg = MissingDataCompDialog(self, self._db_connection, event_ids=event_ids, export_mode=True)
        md_dlg.exec()

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

    def _open_vehicle_search_dialog(self):
        """Open (or bring forward) the modeless VehicleSearchDialog."""
        if not self._plot_widget:
            logger.warning("No plot widget available for vehicle search")
            return

        if self._search_dialog is None:
            from plot.vehicle_search_dialog import VehicleSearchDialog
            self._search_dialog = VehicleSearchDialog(
                parent=self, veh_ids=self._plot_widget.get_vehicle_ids())
            self._search_dialog.vehicle_selected.connect(self._plot_widget.select_searched_vehicle)
        else:
            self._search_dialog.set_vehicle_ids(self._plot_widget.get_vehicle_ids())

        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.activateWindow()

    def _on_search_action_changed(self):
        """React to the Search Vehicle action's enabled state, which is owned
        by TimeSpacePlotSettingsDialog. Close the modeless search dialog
        whenever the action becomes disabled (event no longer confirmed)."""
        if not self._search_vehicle_action.isEnabled() and self._search_dialog is not None:
            self._search_dialog.close()

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

        if self._search_dialog is not None:
            self._search_dialog.close()

        self._db_session.close()

        self._save_settings()
        super().closeEvent(event)

    def _run_spline_fitting(self):
        """Handle the Data > Spline Fitting menu action."""
        if not self._db_connection:
            QMessageBox.warning(self, "Warning", "No database is currently open.")
            return

        from .event_selection_dialog import EventSelectionDialog
        sel_dlg = EventSelectionDialog(self, self._db_connection)
        if sel_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        event_ids = sel_dlg.selected_event_ids
        if not event_ids:
            return

        try:
            from analysis.spline import SplineCompDialog
        except ModuleNotFoundError as exc:
            if exc.name != "scipy":
                raise
            QMessageBox.warning(
                self,
                "Missing dependency",
                "Spline Fitting requires SciPy. Install project requirements and restart the app.",
            )
            return
        dlg = SplineCompDialog(self, self._db_connection, event_ids=event_ids)
        dlg.exec()

    def _run_gap_interpolation(self):
        event_id = self._plot_widget._current_event_id
        from analysis.gap_interpolation import GapConfigDialog
        dlg = GapConfigDialog(self, self._db_connection, event_ids=[event_id],
                              resolve_target=self._db_session.ensure_write_target)
        # Writes reported by the config dialog itself (e.g. Delete, then
        # Close) arrive on its data_changed. The review window it opens
        # usually outlives it, so connect to that window directly as it
        # is created rather than relaying through the closed config dialog.
        dlg.data_changed.connect(self._on_event_data_changed)
        dlg.review_opened.connect(
            lambda review: review.data_changed.connect(self._on_event_data_changed))
        dlg.exec()

    def _run_lane_anomalies(self):
        """Handle the Data > Lane-Level Anomalies menu action."""
        event_id = self._plot_widget._current_event_id
        from analysis.lane_anomaly import LaneAnomalyConfigDialog
        dlg = LaneAnomalyConfigDialog(self, self._db_connection, event_ids=[event_id],
                                      resolve_target=self._db_session.ensure_write_target)
        dlg.review_opened.connect(
            lambda review: review.data_changed.connect(self._on_event_data_changed))
        dlg.exec()

    def _run_jerk_analysis(self):
        """Handle the Analysis > Noise > Jerk Analysis menu action."""
        from analysis.jerk import JerkCompDialog
        event_id = self._plot_widget._current_event_id
        dlg = JerkCompDialog(self, self._db_connection, event_ids=[event_id])
        dlg.exec()

    def _run_internal_consistency(self):
        """Handle the Analysis > Noise > Internal Consistency menu action."""
        from analysis.internal_consistency import InternalConsistencyDialog
        event_id = self._plot_widget._current_event_id
        dlg = InternalConsistencyDialog(self, self._db_connection, event_ids=[event_id])
        dlg.exec()

    def _run_missing_data_analysis(self):
        """Handle the Analysis > Noise > Missing Data menu action."""
        event_id = self._plot_widget._current_event_id
        from analysis.missing_data import MissingDataCompDialog
        dlg = MissingDataCompDialog(self, self._db_connection, event_ids=[event_id])
        dlg.exec()
