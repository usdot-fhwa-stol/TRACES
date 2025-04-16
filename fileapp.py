import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QFileDialog, QMenu)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt, QRect
import pathlib

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.app_name = "FileApp"
        self.setWindowTitle(self.app_name)
        self.recent_files = []
        self.max_recent_files = 7
        self.conf_file = f"{self.app_name}.conf"
        
        # Initialize UI
        self.init_ui()
        # Load configuration
        self.load_config()
        
    def init_ui(self):
        # Create menu bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        
        # Open action
        open_action = QAction("&Open", self)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)
        
        # Import action
        import_action = QAction("&Import", self)
        file_menu.addAction(import_action)
        
        # Export action
        export_action = QAction("&Export", self)
        export_action.setEnabled(False)
        file_menu.addAction(export_action)
        
        # Separator
        file_menu.addSeparator()
        
        # Recent files menu items will be added here
        self.recent_files_menu = file_menu.addMenu("Recent Files")
        
        # Separator
        file_menu.addSeparator()
        
        # Exit action
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Update recent files menu
        self.update_recent_files_menu()
    
    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "", "All Files (*.*)"
        )
        if file_path:
            # Update title bar
            self.setWindowTitle(f"{self.app_name} - {file_path}")
            
            # Update recent files
            file_path = str(pathlib.Path(file_path))
            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
            self.recent_files.insert(0, file_path)
            
            # Trim recent files list
            if len(self.recent_files) > self.max_recent_files:
                self.recent_files = self.recent_files[:self.max_recent_files]
                
            # Update recent files menu
            self.update_recent_files_menu()
    
    def update_recent_files_menu(self):
        self.recent_files_menu.clear()
        for file_path in self.recent_files:
            # Compact file path for display
            display_path = pathlib.Path(file_path).name
            action = QAction(display_path, self)
            action.setData(file_path)  # Store full path
            action.triggered.connect(self.open_recent_file)
            self.recent_files_menu.addAction(action)
    
    def open_recent_file(self):
        action = self.sender()
        if action:
            file_path = action.data()
            self.setWindowTitle(f"{self.app_name} - {file_path}")
    
    def load_config(self):
        default_width = 1280
        default_height = 720
        
        if os.path.exists(self.conf_file):
            with open(self.conf_file, 'r') as f:
                lines = f.readlines()
                if lines:
                    try:
                        # Read window geometry
                        x, y, width, height, maximized = map(int, lines[0].strip().split(','))
                        self.setGeometry(x, y, width, height)
                        if maximized:
                            self.showMaximized()
                    except (ValueError, IndexError):
                        self.set_default_geometry(default_width, default_height)
                    
                    # Read recent files
                    self.recent_files = []
                    for line in lines[1:]:
                        file_path = line.strip()
                        if file_path and os.path.exists(file_path):
                            self.recent_files.append(file_path)
                    self.recent_files = self.recent_files[:self.max_recent_files]
                    self.update_recent_files_menu()
        else:
            self.set_default_geometry(default_width, default_height)
    
    def set_default_geometry(self, width, height):
        # Get primary screen
        screen = QApplication.primaryScreen()
        screen_rect = screen.availableGeometry()
        
        # Center window
        x = (screen_rect.width() - width) // 2
        y = (screen_rect.height() - height) // 2
        self.setGeometry(x, y, width, height)
    
    def save_config(self):
        if self.isMinimized():
            return
            
        geometry = self.geometry()
        maximized = 1 if self.isMaximized() else 0
        
        with open(self.conf_file, 'w') as f:
            # Write window geometry
            f.write(f"{geometry.x()},{geometry.y()},{geometry.width()},{geometry.height()},{maximized}\n")
            # Write recent files
            for file_path in self.recent_files:
                if os.path.exists(file_path):  # Only save existing files
                    f.write(f"{file_path}\n")
    
    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
