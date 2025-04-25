import os
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QMenuBar, QMenu, QApplication
from PyQt6.QtGui import QAction, QScreen
from PyQt6.QtCore import Qt, QTimer
from .progress_dialog import ProgressDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.recent_files = []
        self.max_recent_files = 7
        self.conf_file = f"{QApplication.applicationName().lower().replace(' ', '-')}.conf"
        self.init_ui()
        self.load_config()

    def init_ui(self):
        self.setWindowTitle(QApplication.applicationName())
        
        # Create menu bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

        # Open action
        open_action = QAction("&Open...", self)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        # Open Recent submenu
        self.recent_menu = QMenu("&Recent", self)
        file_menu.addMenu(self.recent_menu)
        self.update_recent_menu()

        file_menu.addSeparator()

        # Import action
        import_action = QAction("&Import...", self)
        import_action.triggered.connect(self.import_data)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        # Exit action
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def load_config(self):
        if not os.path.exists(self.conf_file):
            # Default window size and position
            default_width, default_height = 1280, 720
            screen = QApplication.primaryScreen().availableGeometry()
            x = (screen.width() - default_width) // 2
            y = (screen.height() - default_height) // 2
            self.setGeometry(x, y, default_width, default_height)
            return

        with open(self.conf_file, 'r') as f:
            lines = f.readlines()
            if lines:
                # Parse window geometry
                geometry = lines[0].strip().split(',')
                x, y, width, height, maximized = map(int, geometry)
                self.setGeometry(x, y, width, height)
                if maximized:
                    self.showMaximized()
                # Load recent files
                self.recent_files = [line.strip() for line in lines[1:] if line.strip()]
                self.update_recent_menu()

    def save_config(self):
        maximized = 1 if self.isMaximized() else 0
        if not maximized:
            geom = self.geometry()
            x, y, width, height = geom.x(), geom.y(), geom.width(), geom.height()
        else:
            x, y, width, height = 0, 0, 1280, 720  # Default values when maximized

        with open(self.conf_file, 'w') as f:
            f.write(f"{x},{y},{width},{height},{maximized}\n")
            for path in self.recent_files:
                f.write(f"{path}\n")

    def update_recent_menu(self):
        self.recent_menu.clear()
        for path in self.recent_files:
            action = QAction(path, self)
            action.triggered.connect(lambda checked, p=path: self.open_recent_file(p))
            self.recent_menu.addAction(action)
        self.recent_menu.setEnabled(bool(self.recent_files))

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File")
        if file_path:
            self.setWindowTitle(f"{QApplication.applicationName()} - {file_path}")
            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
            self.recent_files.insert(0, file_path)
            if len(self.recent_files) > self.max_recent_files:
                self.recent_files.pop()
            self.update_recent_menu()

    def open_recent_file(self, file_path):
        if os.path.exists(file_path):
            self.setWindowTitle(f"{QApplication.applicationName()} - {file_path}")
            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
            self.recent_files.insert(0, file_path)
            self.update_recent_menu()
        else:
            self.recent_files.remove(file_path)
            self.update_recent_menu()

    def import_data(self):
        self.progress_dialog = ProgressDialog(self)
        self.progress_dialog.show()
        self.simulate_import()

    def simulate_import(self):
        self.event_count = 0
        self.total_events = 10

        def process_event():
            self.event_count += 1
            progress = (self.event_count / self.total_events) * 100
            self.progress_dialog.update_progress(progress)
            self.progress_dialog.append_message(f"Processing event {self.event_count}...")
            if self.event_count < self.total_events:
                QTimer.singleShot(1000, process_event)
            else:
                self.progress_dialog.append_message("Import completed.")
                self.progress_dialog.enable_close_button()

        QTimer.singleShot(1000, process_event)

    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)
