"""
Unit tests for the main trajectory_tools module and utils.
"""

import unittest
import os
import sys
import logging
from unittest.mock import patch, MagicMock

# Mock PyQt6 modules before any imports that might use them
def setup_qt_mocks():
    """Set up comprehensive PyQt6 mocking."""
    # Create the main PyQt6 mock
    pyqt6_mock = MagicMock()
    
    # Create submodule mocks
    qtcore_mock = MagicMock()
    qtwidgets_mock = MagicMock()
    qtgui_mock = MagicMock()
    qtwebengine_mock = MagicMock()
    qtwebchannel_mock = MagicMock()
    
    # Set up the package structure
    pyqt6_mock.QtCore = qtcore_mock
    pyqt6_mock.QtWidgets = qtwidgets_mock
    pyqt6_mock.QtGui = qtgui_mock
    pyqt6_mock.QtWebEngineWidgets = qtwebengine_mock
    pyqt6_mock.QtWebChannel = qtwebchannel_mock
    
    # Install all the mocks
    sys.modules['PyQt6'] = pyqt6_mock
    sys.modules['PyQt6.QtCore'] = qtcore_mock
    sys.modules['PyQt6.QtWidgets'] = qtwidgets_mock
    sys.modules['PyQt6.QtGui'] = qtgui_mock
    sys.modules['PyQt6.QtWebEngineWidgets'] = qtwebengine_mock
    sys.modules['PyQt6.QtWebChannel'] = qtwebchannel_mock
    
    # Mock other GUI libraries
    sys.modules['pyqtgraph'] = MagicMock()
    sys.modules['superqt'] = MagicMock()

setup_qt_mocks()

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import utils
import trajectory_tools


class TestUtils(unittest.TestCase):
    """Test cases for the utils module."""

    def test_get_resource_path_normal(self):
        """Test get_resource_path with normal relative path."""
        test_path = "config/test.json"
        result = utils.get_resource_path(test_path)
        
        # Should return an absolute path containing the relative path
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.endswith(test_path.replace('/', os.sep)))

    def test_get_resource_path_empty(self):
        """Test get_resource_path with empty string."""
        result = utils.get_resource_path("")
        
        # Should return base path when given empty string
        expected = getattr(sys, '_MEIPASS', os.path.abspath("."))
        # Normalize paths to handle trailing slashes consistently
        result_normalized = os.path.normpath(result)
        expected_normalized = os.path.normpath(expected)
        self.assertEqual(result_normalized, expected_normalized)

    def test_get_resource_path_single_file(self):
        """Test get_resource_path with single filename."""
        test_file = "trajectory_tools.log"
        result = utils.get_resource_path(test_file)
        
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.endswith(test_file))

    def test_get_resource_path_frozen(self):
        """Test get_resource_path when running as frozen PyInstaller app."""
        test_path = "about.html"
        
        # Temporarily add _MEIPASS to sys to simulate frozen environment
        original_meipass = getattr(sys, '_MEIPASS', None)
        try:
            sys._MEIPASS = '/frozen/app/path'
            result = utils.get_resource_path(test_path)
            
            # Should use _MEIPASS when available
            expected = os.path.join('/frozen/app/path', test_path)
            self.assertEqual(result, expected)
        finally:
            # Clean up - remove _MEIPASS or restore original value
            if original_meipass is None:
                if hasattr(sys, '_MEIPASS'):
                    delattr(sys, '_MEIPASS')
            else:
                sys._MEIPASS = original_meipass

    def test_get_resource_path_with_subdirs(self):
        """Test get_resource_path with nested subdirectories."""
        test_path = "config/import/csv_config.json"
        result = utils.get_resource_path(test_path)
        
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.endswith(test_path.replace('/', os.sep)))


class TestTrajectoryTools(unittest.TestCase):
    """Test cases for the main trajectory_tools module."""

    @patch('trajectory_tools.QApplication')
    @patch('trajectory_tools.MainWindow')
    def test_main_function_creates_app_and_window(self, mock_main_window, mock_qapp):
        """Test that main() creates QApplication and MainWindow."""
        # Mock the QApplication instance and its exec method
        mock_app_instance = MagicMock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        # Mock the MainWindow instance
        mock_window_instance = MagicMock()
        mock_main_window.return_value = mock_window_instance
        
        # Mock sys.exit to prevent actual exit
        with patch('sys.exit') as mock_exit:
            trajectory_tools.main()
            
            # Verify QApplication was created with sys.argv
            mock_qapp.assert_called_once_with(sys.argv)
            
            # Verify MainWindow was created
            mock_main_window.assert_called_once()
            
            # Verify window.show() was called
            mock_window_instance.show.assert_called_once()
            
            # Verify app.exec() was called
            mock_app_instance.exec.assert_called_once()
            
            # Verify sys.exit was called with app.exec() return value
            mock_exit.assert_called_once_with(0)

    @patch.dict(os.environ, {}, clear=True)
    @patch('trajectory_tools.logging.basicConfig')
    def test_main_sets_logging_config(self, mock_logging_config):
        """Test that main() sets up logging configuration."""
        with patch('trajectory_tools.QApplication'), \
             patch('trajectory_tools.MainWindow'), \
             patch('sys.exit'):
            
            trajectory_tools.main()
            
            # Verify logging was configured
            mock_logging_config.assert_called_once_with(
                level=logging.DEBUG, 
                format='%(asctime)s - %(levelname)s - %(message)s'
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_main_sets_qt_logging_env_var(self):
        """Test that main() sets QT_LOGGING_RULES environment variable."""
        with patch('trajectory_tools.QApplication'), \
             patch('trajectory_tools.MainWindow'), \
             patch('sys.exit'):
            
            trajectory_tools.main()
            
            # Check that the environment variable was set
            expected_value = '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'
            self.assertEqual(os.environ.get('QT_LOGGING_RULES'), expected_value)

    @patch('trajectory_tools.QApplication')
    @patch('trajectory_tools.MainWindow')
    def test_main_handles_app_exec_return_value(self, mock_main_window, mock_qapp):
        """Test that main() properly handles QApplication.exec() return value."""
        # Test with non-zero exit code
        mock_app_instance = MagicMock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 1  # Non-zero exit code
        
        mock_window_instance = MagicMock()
        mock_main_window.return_value = mock_window_instance
        
        with patch('sys.exit') as mock_exit:
            trajectory_tools.main()
            mock_exit.assert_called_once_with(1)

    @patch('trajectory_tools.QApplication')
    def test_main_handles_exception_during_window_creation(self, mock_qapp):
        """Test that main() handles exceptions during window creation."""
        mock_app_instance = MagicMock()
        mock_qapp.return_value = mock_app_instance
        
        # Mock MainWindow to raise an exception
        with patch('trajectory_tools.MainWindow', side_effect=Exception("Test exception")):
            with self.assertRaises(Exception) as context:
                trajectory_tools.main()
            
            self.assertEqual(str(context.exception), "Test exception")
            # QApplication should still have been created
            mock_qapp.assert_called_once_with(sys.argv)


if __name__ == '__main__':
    # Fix import for standalone running
    import logging
    unittest.main()