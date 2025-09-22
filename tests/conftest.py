"""
Pytest configuration file for trajectory tools tests.
This file is automatically loaded by pytest and sets up global test configuration.
"""

import sys
import os
from unittest.mock import MagicMock

# Add src directory to Python path for imports
src_path = os.path.join(os.path.dirname(__file__), 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Mock PyQt6 modules globally to prevent import errors in headless environment
# This must be done before any imports that depend on PyQt6
def pytest_configure(config):
    """Configure pytest with global mocks."""
    
    # Mock all PyQt6 modules
    pyqt6_modules = [
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtWidgets', 
        'PyQt6.QtGui',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebChannel'
    ]
    
    for module_name in pyqt6_modules:
        sys.modules[module_name] = MagicMock()
    
    # Mock specific PyQt6 classes that are commonly used
    sys.modules['PyQt6.QtCore'].QObject = MagicMock()
    sys.modules['PyQt6.QtCore'].QThread = MagicMock() 
    sys.modules['PyQt6.QtCore'].QTimer = MagicMock()
    sys.modules['PyQt6.QtCore'].Qt = MagicMock()
    sys.modules['PyQt6.QtCore'].pyqtSignal = MagicMock()
    sys.modules['PyQt6.QtCore'].pyqtSlot = MagicMock()
    
    sys.modules['PyQt6.QtWidgets'].QApplication = MagicMock()
    sys.modules['PyQt6.QtWidgets'].QMainWindow = MagicMock()
    sys.modules['PyQt6.QtWidgets'].QDialog = MagicMock()
    sys.modules['PyQt6.QtWidgets'].QWidget = MagicMock()
    
    # Mock other graphics libraries that might be imported
    sys.modules['pyqtgraph'] = MagicMock()
    sys.modules['superqt'] = MagicMock()

# Set up test environment
def pytest_sessionstart(session):
    """Called after the Session object has been created."""
    # Suppress Qt warnings in test environment
    os.environ['QT_LOGGING_RULES'] = 'qt6.accessibility.warning=false'
