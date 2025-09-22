"""
Unit tests for top-level modules (trajectory_tools.py and utils.py).
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import tempfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import modules - PyQt6 is mocked globally by conftest.py
import utils
import trajectory_tools


class TestUtils(unittest.TestCase):
    """Test cases for utils.py module."""
    
    def test_get_resource_path_development(self):
        """Test get_resource_path in development environment."""
        # Mock sys._MEIPASS to not exist (development mode)
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/mock/current/dir'
            
            result = utils.get_resource_path('config/test.json')
            
            mock_abspath.assert_called_once_with('.')
            expected = os.path.join('/mock/current/dir', 'config/test.json')
            self.assertEqual(result, expected)
    
    def test_get_resource_path_pyinstaller(self):
        """Test get_resource_path in PyInstaller frozen environment."""
        # Mock sys._MEIPASS to exist (PyInstaller mode)
        with patch.object(sys, '_MEIPASS', '/mock/pyinstaller/path'):
            result = utils.get_resource_path('config/test.json')
            
            expected = os.path.join('/mock/pyinstaller/path', 'config/test.json')
            self.assertEqual(result, expected)
    
    def test_get_resource_path_empty_string(self):
        """Test get_resource_path with empty string."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/mock/current/dir'
            
            result = utils.get_resource_path('')
            
            expected = os.path.join('/mock/current/dir', '')
            self.assertEqual(result, expected)
    
    def test_get_resource_path_relative_paths(self):
        """Test get_resource_path with various relative paths."""
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = '/base/path'
            
            test_cases = [
                'file.txt',
                'folder/file.txt',
                '../file.txt',
                'deep/nested/folder/file.txt'
            ]
            
            for relative_path in test_cases:
                result = utils.get_resource_path(relative_path)
                expected = os.path.join('/base/path', relative_path)
                self.assertEqual(result, expected)


class TestTrajectoryTools(unittest.TestCase):
    """Test cases for trajectory_tools.py module."""
    
    @patch('trajectory_tools.QApplication')
    @patch('trajectory_tools.MainWindow')
    @patch('sys.exit')
    @patch('os.environ')
    def test_main_function(self, mock_environ, mock_exit, mock_main_window, mock_qapp):
        """Test the main function execution flow."""
        # Setup mocks
        mock_app_instance = MagicMock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = MagicMock()
        mock_main_window.return_value = mock_window_instance
        
        # Mock sys.argv to avoid issues with test runner arguments
        with patch('sys.argv', ['trajectory_tools.py']):
            # Call main function
            trajectory_tools.main()
        
        # Verify environment variable was set
        mock_environ.__setitem__.assert_called_with(
            'QT_LOGGING_RULES', 
            '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'
        )
        
        # Verify QApplication was created with correct arguments
        mock_qapp.assert_called_once_with(['trajectory_tools.py'])
        
        # Verify MainWindow was created and shown
        mock_main_window.assert_called_once()
        mock_window_instance.show.assert_called_once()
        
        # Verify app.exec() was called
        mock_app_instance.exec.assert_called_once()
        
        # Verify sys.exit was called with app.exec() return value
        mock_exit.assert_called_once_with(0)
    
    @patch('trajectory_tools.logging.basicConfig')
    @patch('trajectory_tools.QApplication')
    @patch('trajectory_tools.MainWindow')
    @patch('sys.exit')
    def test_logging_configuration(self, mock_exit, mock_main_window, mock_qapp, mock_logging):
        """Test that logging is properly configured."""
        # Setup mocks
        mock_app_instance = MagicMock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = MagicMock()
        mock_main_window.return_value = mock_window_instance
        
        with patch('sys.argv', ['trajectory_tools.py']):
            trajectory_tools.main()
        
        # Verify logging was configured correctly
        mock_logging.assert_called_once_with(
            level=trajectory_tools.logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    
    @patch('trajectory_tools.QApplication')
    @patch('trajectory_tools.MainWindow')
    @patch('sys.exit')
    def test_main_with_exception(self, mock_exit, mock_main_window, mock_qapp):
        """Test main function when an exception occurs."""
        # Setup mock to raise exception
        mock_qapp.side_effect = Exception("Test exception")
        
        with patch('sys.argv', ['trajectory_tools.py']):
            # Verify exception is not caught (should propagate)
            with self.assertRaises(Exception) as context:
                trajectory_tools.main()
            
            self.assertEqual(str(context.exception), "Test exception")
        
        # Verify sys.exit was not called due to exception
        mock_exit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
