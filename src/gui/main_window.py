from PyQt6.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QMenu, QApplication
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt
import os
import sqlite3
from .import_dialog import ImportDialog
from .export_dialog import ExportDialog
from .about_dialog import AboutDialog


class MainWindow(QMainWindow):
    """Main application window for Trajectory Tools.

    Manages the main GUI, menu actions, and database connections.
    """

    def __init__(self):
        """Initialize the main window and setup UI."""
        super().__init__()
        self.db_conn = None
        self.recent_files = []
        self.setWindowTitle("Trajectory Tools")
        self.setup_menu()
        self.load_settings()
        self.show_splash()

    def setup_menu(self):
        """Setup the File menu and its actions."""
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        self.recent_menu = QMenu("Recent", self)
        self.recent_menu.setEnabled(False)
        file_menu.addMenu(self.recent_menu)

        file_menu.addSeparator()

        import_action = QAction("&Import…", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self.show_import_dialog)
        file_menu.addAction(import_action)

        self.export_action = QAction("&Export…", self)
        self.export_action.setShortcut("Ctrl+E")
        self.export_action.setEnabled(False)
        self.export_action.triggered.connect(self.show_export_dialog)
        file_menu.addAction(self.export_action)

        file_menu.addSeparator()

        about_action = QAction("&About", self)
        about_action.setShortcut("Ctrl+?")
        about_action.triggered.connect(self.show_about_dialog)
        file_menu.addAction(about_action)

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def load_settings(self):
        """Load window settings and recent files from config file."""
        config_file = "trajectory_tools.conf"
        desktop = QApplication.primaryScreen().availableGeometry()

        # Default to 1280x720 centered on screen
        default_width = 1280
        default_height = 720
        default_x = (desktop.width() - default_width) // 2
        default_y = (desktop.height() - default_height) // 2

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if not lines or not lines[0].strip():  # Check for empty file
                    self.setGeometry(default_x, default_y, default_width, default_height)
                    return
                x, y, width, height, maximized = lines[0].strip().split(',')
                x, y, width, height = map(int, (x, y, width, height))
                maximized = int(maximized)
                if (x < 0 or y < 0 or x + width > desktop.width() or
                        y + height > desktop.height()):
                    x, y, width, height = default_x, default_y, default_width, default_height
                self.setGeometry(x, y, width, height)
                if maximized:
                    self.showMaximized()
                self.recent_files = [line.strip() for line in lines[1:] if line.strip()][:7]
                self.update_recent_menu()
        except (FileNotFoundError, IndexError, ValueError) as e:
            self.setGeometry(default_x, default_y, default_width, default_height)
            QMessageBox.warning(self, "Warning", f"Error loading settings: {str(e)}")

    def save_settings(self):
        """Save window settings and recent files to config file."""
        config_file = "trajectory_tools.conf"
        geometry = self.geometry()
        maximized = 1 if self.isMaximized() else 0
        with open(config_file, 'w', encoding='utf-8') as f:
            f.write(f"{geometry.x()},{geometry.y()},{geometry.width()},{geometry.height()},{maximized}\n")
            for path in self.recent_files:
                f.write(f"{path}\n")

    def update_recent_menu(self):
        """Update the Recent submenu with current recent files."""
        self.recent_menu.clear()
        for path in self.recent_files:
            action = QAction(path, self)
            action.triggered.connect(lambda checked, p=path: self.open_recent_file(p))
            self.recent_menu.addAction(action)
        self.recent_menu.setEnabled(bool(self.recent_files))

    def open_file(self):
        """Open a SQLite database file."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Database", "",
                                                  "SQLite Database (*.sqlite)")
        if file_path:
            self.open_database(file_path)

    def open_recent_file(self, file_path: str):
        """Open a recent database file.

        Args:
            file_path: Path to the database file.
        """
        if not os.path.exists(file_path):
            QMessageBox.critical(self, "Error", f"File {file_path} no longer exists")
            self.recent_files.remove(file_path)
            self.update_recent_menu()
            self.save_settings()
            return
        self.open_database(file_path)

    def open_database(self, file_path: str):
        """Open and validate a SQLite database file.

        Args:
            file_path: Path to the database file.
        """
        try:
            conn = sqlite3.connect(file_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trajectories'")
            if not cursor.fetchone():
                conn.close()
                QMessageBox.critical(self, "Error", "Trajectories table not found")
                return

            cursor.execute("PRAGMA table_info(trajectories)")
            columns = cursor.fetchall()
            expected_columns = {
                "event": "INTEGER", "id": "INTEGER", "time": "REAL", "lane_id": "INTEGER",
                "x_map_loc": "REAL", "y_map_loc": "REAL", "x_frenet": "REAL",
                "y_frenet": "REAL", "x_map_origin": "REAL", "y_map_origin": "REAL",
                "veh_lat": "REAL", "veh_lon": "REAL", "speed": "REAL",
                "acceleration": "REAL", "length": "REAL", "automated": "INTEGER",
                "way_id": "REAL", "preceding": "REAL", "speed_limit": "REAL",
                "width": "REAL", "traffic_control": "REAL"
            }
            if len(columns) != len(expected_columns) or any(
                    col[1] not in expected_columns or col[2].upper() != expected_columns[col[1]]
                    for col in columns):
                conn.close()
                QMessageBox.critical(self, "Error", "Trajectories table has incorrect schema")
                return

            cursor.execute("SELECT COUNT(*) FROM trajectories")
            if cursor.fetchone()[0] == 0:
                conn.close()
                QMessageBox.critical(self, "Error", "Trajectories table is empty")
                return

            if self.db_conn:
                self.db_conn.close()
            self.db_conn = conn
            self.setWindowTitle(f"Trajectory Tools - {file_path}")
            self.export_action.setEnabled(True)

            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
            self.recent_files.insert(0, file_path)
            if len(self.recent_files) > 7:
                self.recent_files.pop()
            self.update_recent_menu()
            self.save_settings()
        except sqlite3.Error as e:
            QMessageBox.critical(self, "Error", f"Invalid SQLite database: {str(e)}")
            if 'conn' in locals():
                conn.close()

    def show_import_dialog(self):
        """Show the import dialog."""
        dialog = ImportDialog(self)
        dialog.exec()

    def show_export_dialog(self):
        """Show the export dialog."""
        dialog = ExportDialog(self, self.db_conn)
        dialog.exec()

    def show_about_dialog(self):
        """Show the about dialog."""
        dialog = AboutDialog(self, splash=False)
        dialog.exec()

    def show_splash(self):
        """Show the splash screen."""
        dialog = AboutDialog(self, splash=True)
        dialog.exec()

    def closeEvent(self, event):
        """Handle window close event.

        Args:
            event: Close event.
        """
        if self.db_conn:
            self.db_conn.close()
        self.save_settings()
        super().closeEvent(event)
