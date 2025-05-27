#!/usr/bin/env python3
"""Main entry point for the Trajectory Tools application."""

import os
import sys
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow


def main():
    """Initialize and run the Trajectory Tools application."""
    app = QApplication(sys.argv)
    app.setApplicationName("Trajectory Tools")
    
    # Discover application working directory
    app_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(app_dir)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()