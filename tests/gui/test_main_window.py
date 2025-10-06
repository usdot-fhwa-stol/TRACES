import pytest
from unittest.mock import Mock, MagicMock, patch, call
from pathlib import Path
from PyQt6.QtWidgets import QDialog
import sqlite3


class TestMainWindowInitialization:
    """Test MainWindow initialization and setup."""
    
    def test_init_creates_main_window(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test that MainWindow initializes correctly."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            assert window._recent_files == []
            assert window._db_connection is None
            assert window._plot_widget is None
            assert window._selected_export_columns == []
            assert window.windowTitle() == "Trajectory Tools"
    
    def test_menu_bar_created(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test that menu bar is created with correct items."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            menu_bar = window.menuBar()
            assert menu_bar is not None
            
            # Check File menu exists
            actions = menu_bar.actions()
            assert len(actions) > 0
            assert actions[0].text() == "&File"
    
    def test_web_view_setup(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test that web view is set up correctly."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            assert window._web_view is not None
            assert window._web_bridge is not None
            assert window._web_channel is not None
            assert window.centralWidget() == window._web_view


class TestSettingsManagement:
    """Test settings loading and saving functionality."""
    
    def test_load_settings_no_config_file(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test loading settings when config file doesn't exist."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Should have default geometry
            assert window.width() > 0
            assert window.height() > 0
    
    def test_load_settings_valid_config(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, tmp_path):
        """Test loading settings from valid config file."""
        config_content = "100,100,800,600,false\n/path/to/file1.db\n/path/to/file2.db"
        config_file = tmp_path / "trajectory_tools.conf"
        config_file.write_text(config_content)
        
        with patch('gui.main_window.Path') as mock_path:
            def path_side_effect(arg):
                if arg == "trajectory_tools.conf":
                    return config_file
                mock_p = MagicMock()
                mock_p.exists.return_value = False
                return mock_p
            
            mock_path.side_effect = path_side_effect
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            assert len(window._recent_files) == 2
            assert window._recent_files[0] == "/path/to/file1.db"
            assert window._recent_files[1] == "/path/to/file2.db"
    
    def test_save_settings(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, tmp_path):
        """Test saving settings to config file."""
        with patch('gui.main_window.Path') as mock_path:
            config_file = tmp_path / "trajectory_tools.conf"
            mock_path.return_value = config_file
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._recent_files = ["/test/file.db"]
            window._save_settings()
            
            assert config_file.exists()
            content = config_file.read_text()
            assert "/test/file.db" in content
    
    def test_add_recent_file(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test adding files to recent files list."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            mock_path.return_value.absolute.return_value = Path("/test/file.db")
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window.add_recent_file("/test/file.db")
            assert len(window._recent_files) == 1
            assert "/test/file.db" in window._recent_files[0]
    
    def test_add_recent_file_maintains_order(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test that recent files maintain correct order."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            def absolute_side_effect():
                return Path(mock_path.call_args[0][0])
            
            mock_path.return_value.absolute = absolute_side_effect
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window.add_recent_file("/test/file1.db")
            window.add_recent_file("/test/file2.db")
            
            # Most recent should be first
            assert window._recent_files[0] == "/test/file2.db"
            assert window._recent_files[1] == "/test/file1.db"
    
    def test_recent_files_limit(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test that recent files list is limited to 7 files."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            def absolute_side_effect():
                return Path(mock_path.call_args[0][0])
            
            mock_path.return_value.absolute = absolute_side_effect
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Add 10 files
            for i in range(10):
                window.add_recent_file(f"/test/file{i}.db")
            
            # Should only keep 7
            assert len(window._recent_files) == 7
            assert window._recent_files[0] == "/test/file9.db"


class TestDatabaseValidation:
    """Test database validation functionality."""
    
    def test_check_database_valid(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test checking a valid database."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Configure mock for valid database
            mock_cursor = mock_sqlite['cursor']
            mock_cursor.fetchone.side_effect = [
                ('events',),  # Table exists
                (100,)  # Row count
            ]
            
            result = window._check_database("/test/valid.db")
            assert result is True
            assert window._db_connection is not None
    
    def test_check_database_no_events_table(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test checking database without events table."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Configure mock for missing table
            mock_cursor = mock_sqlite['cursor']
            mock_cursor.fetchone.return_value = None
            
            result = window._check_database("/test/invalid.db")
            assert result is False
            mock_dialogs['message_box'].critical.assert_called()
    
    def test_check_database_empty_table(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test checking database with empty events table."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Configure mock for empty table
            mock_cursor = mock_sqlite['cursor']
            mock_cursor.fetchone.side_effect = [
                ('events',),  # Table exists
                (0,)  # Row count is 0
            ]
            
            result = window._check_database("/test/empty.db")
            assert result is False
            mock_dialogs['message_box'].critical.assert_called()
    
    def test_check_database_connection_error(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test handling database connection errors."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Configure mock to raise error
            mock_sqlite['module'].connect.side_effect = sqlite3.Error("Connection failed")
            
            result = window._check_database("/test/error.db")
            assert result is False
            mock_dialogs['message_box'].critical.assert_called()


class TestFileOperations:
    """Test file opening and management operations."""
    
    def test_open_file_cancelled(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test opening file when user cancels dialog."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Mock file dialog to return empty path (cancelled)
            mock_dialogs['file_dialog'].getOpenFileName.return_value = ('', '')
            
            window._open_file()
            
            # Should not change anything
            assert window._db_connection is None
            assert window.windowTitle() == "Trajectory Tools"
    
    def test_open_file_success(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test successfully opening a file."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Mock file dialog to return a path
            mock_dialogs['file_dialog'].getOpenFileName.return_value = ('/test/file.db', '')
            
            # Configure valid database
            mock_cursor = mock_sqlite['cursor']
            mock_cursor.fetchone.side_effect = [
                ('events',),
                (100,)
            ]
            
            # Mock settings dialog
            mock_custom_widgets['settings_instance'].exec.return_value = QDialog.DialogCode.Accepted
            
            window._open_file()
            
            # Should show settings dialog
            mock_custom_widgets['settings'].assert_called()
    
    def test_open_recent_file_not_found(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test opening recent file that doesn't exist."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._recent_files = ["/test/missing.db"]
            window._open_recent_file("/test/missing.db")
            
            mock_dialogs['message_box'].critical.assert_called()
            assert "/test/missing.db" not in window._recent_files
    
    def test_close_database_and_reset(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test closing database and resetting window state."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Set up state as if file was open
            window._db_connection = mock_sqlite['connection']
            window.setWindowTitle("Trajectory Tools - /test/file.db")
            
            window._close_database_and_reset()
            
            assert window._db_connection is None
            assert window.windowTitle() == "Trajectory Tools"
            mock_sqlite['connection'].close.assert_called()


class TestExportFunctionality:
    """Test export-related functionality."""
    
    def test_export_all(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test Export All functionality."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._db_connection = mock_sqlite['connection']
            window._export_all()
            
            # Should create export dialog with None for selected_columns
            mock_custom_widgets['export'].assert_called_once()
            call_args = mock_custom_widgets['export'].call_args
            assert call_args[1]['selected_columns'] is None
    
    def test_export_selected(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test Export Selected functionality."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._db_connection = mock_sqlite['connection']
            window._selected_export_columns = ['veh_id', 'veh_time']
            
            window._export_selected()
            
            # Should create export dialog with selected columns
            mock_custom_widgets['export'].assert_called_once()
            call_args = mock_custom_widgets['export'].call_args
            assert call_args[1]['selected_columns'] == ['veh_id', 'veh_time']
    
    def test_export_without_database(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test export when no database is open."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._export_all()
            
            mock_dialogs['message_box'].warning.assert_called()
    
    def test_handle_export_finished(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test handling export finished signal."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Simulate export finished with selected columns
            selected_cols = ['veh_id', 'veh_time', 'veh_speed']
            window._handle_export_finished(selected_cols)
            
            assert window._selected_export_columns == selected_cols


class TestHomePageGeneration:
    """Test home page HTML generation."""
    
    def test_generate_recent_files_html_empty(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test HTML generation with no recent files."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            html = window._generate_recent_files_html()
            assert "No recent files" in html
    
    def test_generate_recent_files_html_with_files(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test HTML generation with recent files."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._recent_files = ["/test/file1.db", "/test/file2.db"]
            html = window._generate_recent_files_html()
            
            assert "<ul>" in html
            assert "</ul>" in html
            assert "file1.db" in html
            assert "file2.db" in html
    
    def test_load_home_page_file_not_found(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, capsys):
        """Test loading home page when file doesn't exist."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            captured = capsys.readouterr()
            assert "home.html file not found" in captured.out or window._web_view is not None


class TestImportFunctionality:
    """Test import-related functionality."""
    
    def test_import_file_dialog_shown(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test that import dialog is shown correctly."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._import_file()
            
            mock_custom_widgets['import'].assert_called()
            assert window._import_dialog is not None
    
    def test_handle_import_finished(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test handling import finished signal."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Configure valid database
            mock_cursor = mock_sqlite['cursor']
            mock_cursor.fetchone.side_effect = [
                ('events',),
                (100,)
            ]
            
            window._handle_import_finished("/test/imported.db")
            
            # Import dialog should be cleared
            assert window._import_dialog is None


class TestDialogs:
    """Test dialog-related functionality."""
    
    def test_show_about_dialog(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test showing about dialog."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._show_about()
            
            mock_custom_widgets['about'].assert_called()
            mock_custom_widgets['about_instance'].exec.assert_called()
    
    def test_settings_dialog_cancelled(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test that canceling settings dialog resets state."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            # Configure valid database
            mock_cursor = mock_sqlite['cursor']
            mock_cursor.fetchone.side_effect = [
                ('events',),
                (100,)
            ]
            
            # Mock settings dialog to return rejected
            mock_custom_widgets['settings_instance'].exec.return_value = QDialog.DialogCode.Rejected
            
            mock_dialogs['file_dialog'].getOpenFileName.return_value = ('/test/file.db', '')
            
            window._open_file()
            
            # Database should be closed and state reset
            assert window._export_menu.isEnabled() is False


class TestWebBridge:
    """Test WebBridge functionality."""
    
    def test_web_bridge_open_file(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test WebBridge openFile method."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow, WebBridge
            window = MainWindow()
            qtbot.addWidget(window)
            
            bridge = WebBridge(window)
            
            with patch.object(window, '_open_file') as mock_open:
                bridge.openFile()
                mock_open.assert_called_once()
    
    def test_web_bridge_import_file(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test WebBridge importFile method."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow, WebBridge
            window = MainWindow()
            qtbot.addWidget(window)
            
            bridge = WebBridge(window)
            
            with patch.object(window, '_import_file') as mock_import:
                bridge.importFile()
                mock_import.assert_called_once()
    
    def test_web_bridge_open_recent_file(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs):
        """Test WebBridge openRecentFile method."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow, WebBridge
            window = MainWindow()
            qtbot.addWidget(window)
            
            bridge = WebBridge(window)
            
            with patch.object(window, '_open_recent_file') as mock_open_recent:
                bridge.openRecentFile("/test/file.db")
                mock_open_recent.assert_called_once()


class TestCloseEvent:
    """Test window close event handling."""
    
    def test_close_event_saves_settings(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, tmp_path):
        """Test that closing window saves settings."""
        with patch('gui.main_window.Path') as mock_path:
            config_file = tmp_path / "trajectory_tools.conf"
            mock_path.return_value = config_file
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._recent_files = ["/test/file.db"]
            window.close()
            
            # Settings should be saved
            assert config_file.exists()
    
    def test_close_event_closes_database(self, qtbot, mock_qwebengine, mock_custom_widgets, mock_utils, mock_dialogs, mock_sqlite):
        """Test that closing window closes database connection."""
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.main_window import MainWindow
            window = MainWindow()
            qtbot.addWidget(window)
            
            window._db_connection = mock_sqlite['connection']
            window.close()
            
            mock_sqlite['connection'].close.assert_called()