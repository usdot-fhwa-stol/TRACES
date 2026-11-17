import pytest
import os
import sys
import logging
from unittest.mock import Mock, patch, MagicMock
from src.trajectory_tools import main


class TestTrajectoryTools:
    """Test suite for trajectory_tools.py main application entry point."""

    def setup_method(self):
        """Set up test fixtures."""
        # Store original environment and sys.argv for restoration
        self.original_env = os.environ.copy()
        self.original_argv = sys.argv.copy()

    def teardown_method(self):
        """Clean up test fixtures."""
        # Restore original environment and sys.argv
        os.environ.clear()
        os.environ.update(self.original_env)
        sys.argv = self.original_argv

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_normal_execution(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test normal execution of main function."""
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main
        main()
        
        # Verify Qt logging rules were set
        assert os.environ['QT_LOGGING_RULES'] == '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'
        
        # Verify logging configuration
        mock_logging_config.assert_called_once_with(
            level=logging.DEBUG, 
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        
        # Verify QApplication was created with sys.argv
        mock_qapp.assert_called_once_with(sys.argv)
        
        # Verify MainWindow was created and shown
        mock_main_window.assert_called_once()
        mock_window_instance.show.assert_called_once()
        
        # Verify app execution and exit
        mock_app_instance.exec.assert_called_once()
        mock_exit.assert_called_once_with(0)

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_with_different_exit_code(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test main function when QApplication returns non-zero exit code."""
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 1  # Non-zero exit code
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main
        main()
        
        # Verify exit was called with the returned code
        mock_exit.assert_called_once_with(1)

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_preserves_existing_qt_logging_rules(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test that main function overwrites existing QT_LOGGING_RULES."""
        # Set existing environment variable
        os.environ['QT_LOGGING_RULES'] = 'existing.rule=true'
        
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main
        main()
        
        # Verify Qt logging rules were overwritten
        assert os.environ['QT_LOGGING_RULES'] == '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_handles_mainwindow_exception(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test main function behavior when MainWindow raises an exception."""
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        
        # Make MainWindow raise an exception
        mock_main_window.side_effect = Exception("MainWindow initialization failed")
        
        # Execute main and expect exception to propagate
        with pytest.raises(Exception, match="MainWindow initialization failed"):
            main()
        
        # Verify QApplication was still created
        mock_qapp.assert_called_once_with(sys.argv)
        
        # Verify logging was still configured
        mock_logging_config.assert_called_once()

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_handles_qapplication_exception(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test main function behavior when QApplication raises an exception."""
        # Make QApplication raise an exception
        mock_qapp.side_effect = Exception("QApplication initialization failed")
        
        # Execute main and expect exception to propagate
        with pytest.raises(Exception, match="QApplication initialization failed"):
            main()
        
        # Verify environment was still set
        assert os.environ['QT_LOGGING_RULES'] == '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'
        
        # Verify logging was still configured
        mock_logging_config.assert_called_once()

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_handles_app_exec_exception(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test main function behavior when app.exec() raises an exception."""
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.side_effect = Exception("App execution failed")
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main and expect exception to propagate
        with pytest.raises(Exception, match="App execution failed"):
            main()
        
        # Verify window was shown before the exception
        mock_window_instance.show.assert_called_once()

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_with_custom_sys_argv(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test main function with custom sys.argv."""
        # Modify sys.argv
        sys.argv = ['trajectory_tools.py', '--some-arg', 'value']
        
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main
        main()
        
        # Verify QApplication was created with the custom sys.argv
        mock_qapp.assert_called_once_with(['trajectory_tools.py', '--some-arg', 'value'])

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_logging_configuration(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test that main function configures logging correctly."""
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main
        main()
        
        # Verify logging.basicConfig was called with correct parameters
        mock_logging_config.assert_called_once_with(
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    def test_main_if_name_main_block(self):
        """Test that main() is called when script is run directly."""
        # This test verifies the if __name__ == "__main__": block
        # We can't easily test this without actually running the script,
        # but we can verify the block exists in the source code
        import src.trajectory_tools
        import inspect
        
        source = inspect.getsource(src.trajectory_tools)
        assert 'if __name__ == "__main__":' in source
        assert 'main()' in source

    @patch('src.trajectory_tools.QApplication')
    @patch('src.trajectory_tools.MainWindow')
    @patch('src.trajectory_tools.logging.basicConfig')
    @patch('sys.exit')
    def test_main_qt_logging_rules_format(self, mock_exit, mock_logging_config, mock_main_window, mock_qapp):
        """Test that Qt logging rules are set to the correct format."""
        # Setup mocks
        mock_app_instance = Mock()
        mock_qapp.return_value = mock_app_instance
        mock_app_instance.exec.return_value = 0
        
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance
        
        # Execute main
        main()
        
        # Verify specific Qt logging rules format
        expected_rules = '*.critical=true;*.warning=true;qt.qpa.*=false;qt.gui.*=false'
        assert os.environ['QT_LOGGING_RULES'] == expected_rules
        
        # Verify the rules disable verbose Qt platform and GUI messages
        assert 'qt.qpa.*=false' in os.environ['QT_LOGGING_RULES']
        assert 'qt.gui.*=false' in os.environ['QT_LOGGING_RULES']
        assert '*.critical=true' in os.environ['QT_LOGGING_RULES']
        assert '*.warning=true' in os.environ['QT_LOGGING_RULES']