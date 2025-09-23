import pytest
import sys
import os
from unittest.mock import Mock

# Add the src directory to the Python path for imports
# This assumes conftest.py is in the test/ directory
test_dir = os.path.dirname(__file__)
src_dir = os.path.join(os.path.dirname(test_dir), 'src')
sys.path.insert(0, src_dir)

@pytest.fixture(autouse=True)
def mock_pyqt6():
    """Auto-mock all PyQt6 components to avoid GUI issues in headless testing."""
    with pytest.mock.patch('PyQt6.QtWidgets.QApplication') as mock_app, \
         pytest.mock.patch('PyQt6.QtWidgets.QMainWindow') as mock_main_window, \
         pytest.mock.patch('PyQt6.QtWebEngineWidgets.QWebEngineView') as mock_web_view, \
         pytest.mock.patch('PyQt6.QtWebChannel.QWebChannel') as mock_web_channel, \
         pytest.mock.patch('PyQt6.QtWidgets.QMenuBar') as mock_menu_bar, \
         pytest.mock.patch('PyQt6.QtWidgets.QMenu') as mock_menu, \
         pytest.mock.patch('PyQt6.QtGui.QAction') as mock_action, \
         pytest.mock.patch('PyQt6.QtWidgets.QMessageBox') as mock_message_box, \
         pytest.mock.patch('PyQt6.QtWidgets.QFileDialog') as mock_file_dialog:
        
        # Configure mock instances
        mock_app_instance = Mock()
        mock_app.return_value = mock_app_instance
        mock_app.instance.return_value = None  # Simulate no existing instance
        
        yield {
            'app': mock_app,
            'main_window': mock_main_window,
            'web_view': mock_web_view,
            'web_channel': mock_web_channel,
            'menu_bar': mock_menu_bar,
            'menu': mock_menu,
            'action': mock_action,
            'message_box': mock_message_box,
            'file_dialog': mock_file_dialog
        }

@pytest.fixture
def mock_utm():
    """Mock UTM library for testing."""
    import unittest.mock
    with unittest.mock.patch('utm.from_latlon') as mock_from:
        with unittest.mock.patch('utm.to_latlon') as mock_to:
            mock_from.return_value = (500000, 4000000, 10, 'S')
            mock_to.return_value = (36.1234, -115.5678)
            yield mock_from, mock_to