# trajectory_tools.py

import os
import logging
import sys
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
    """Entry point for the Trajectory Tools application."""

    # Enable Qt debug messages
    os.environ['QT_LOGGING_RULES'] = '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'

    # Set up Python logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()