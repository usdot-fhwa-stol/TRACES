import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QAction

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.app_name = "FileApp"
        self.recent_files = []
        self.conf_file = f"{self.app_name}.conf"
        self.init_ui()
        self.load_config()

    def init_ui(self):
        self.setWindowTitle(self.app_name)

        # Create menu bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        # Create File menu items
        open_action = QAction("Open", self)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        import_action = QAction("Import", self)
        file_menu.addAction(import_action)

        export_action = QAction("Export", self)
        export_action.setEnabled(False)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        # Placeholder for recent files
        self.recent_menu = file_menu.addMenu("Recent Files")
        self.update_recent_menu()

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open File")
        if file_name:
            # Update title bar
            self.setWindowTitle(f"{self.app_name} - {file_name}")
            # Update recent files list
            if file_name in self.recent_files:
                self.recent_files.remove(file_name)
            self.recent_files.insert(0, file_name)
            if len(self.recent_files) > 7:
                self.recent_files.pop()
            self.update_recent_menu()

    def update_recent_menu(self):
        self.recent_menu.clear()
        for file in self.recent_files:
            action = QAction(file, self)
            action.setData(file)
            action.triggered.connect(lambda: self.setWindowTitle(f"{self.app_name} - {action.data()}"))
            self.recent_menu.addAction(action)

    def load_config(self):
        default_width, default_height = 1280, 720
        # Use QScreen to get the primary screen's available geometry
        screen = QGuiApplication.primaryScreen()
        desktop = screen.availableGeometry()
        default_x = (desktop.width() - default_width) // 2
        default_y = (desktop.height() - default_height) // 2
        x, y, width, height, maximized = default_x, default_y, default_width, default_height, False

        if os.path.exists(self.conf_file):
            with open(self.conf_file, "r") as f:
                line = f.readline().strip()
                try:
                    x, y, width, height, maximized = map(int, line.split(","))
                except ValueError:
                    pass

        if maximized:
            self.showMaximized()
        else:
            self.setGeometry(x, y, width, height)

    def save_config(self):
        if self.isMinimized():
            return
        x, y, width, height = self.geometry().getRect()
        maximized = 1 if self.isMaximized() else 0
        with open(self.conf_file, "w") as f:
            f.write(f"{x},{y},{width},{height},{maximized}")

    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
