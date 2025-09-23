import pytest
import sqlite3
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open
from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtCore import QRect
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt

# Import the classes to test
from src.gui.main_window import MainWindow, WebBridge


@pytest.fixture
def qapp():
    """Create QApplication instance for testing."""
    if not QApplication.instance():
        app = QApplication([])
    else:
        app = QApplication.instance()
    yield app


class TestWebBridge:
    """Test suite for WebBridge class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_main_window = Mock()
        self.bridge = WebBridge(self.mock_main_window)
    
    def test_initialization(self):
        """Test WebBridge initialization."""
        assert self.bridge.main_window == self.mock_main_window
    
    def test_open_file(self):
        """Test openFile slot."""
        self.bridge.openFile()
        self.mock_main_window._open_file.assert_called_once()
    
    def test_import_file(self):
        """Test importFile slot."""
        self.bridge.importFile()
        self.mock_main_window._import_file.assert_called_once()
    
    def test_open_recent_file(self):
        """Test openRecentFile slot with path conversion."""
        test_path = "C:/Users/test/file.db"
        self.bridge.openRecentFile(test_path)
        
        # Verify Path conversion was applied
        expected_path = str(Path(test_path))
        self.mock_main_window._open_recent_file.assert_called_once_with(expected_path)
    
    def test_open_recent_file_unix_path(self):
        """Test openRecentFile slot with Unix-style path."""
        test_path = "/home/user/data/file.db"
        self.bridge.openRecentFile(test_path)
        
        expected_path = str(Path(test_path))
        self.mock_main_window._open_recent_file.assert_called_once_with(expected_path)


class TestMainWindow:
    """Test suite for MainWindow class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.temp_files = []
        
    def teardown_method(self):
        """Clean up test fixtures."""
        # Clean up temporary files
        for temp_file in self.temp_files:
            try:
                os.unlink(temp_file)
            except FileNotFoundError:
                pass
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_initialization(self, mock_web_channel, mock_web_view, qapp):
        """Test MainWindow initialization."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            # Check initial state
            assert window._recent_files == []
            assert window._db_connection is None
            assert window._plot_widget is None
            assert window._selected_export_columns == []
            assert window.windowTitle() == "Trajectory Tools"
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_setup_ui_creates_menus(self, mock_web_channel, mock_web_view, qapp):
        """Test that _setup_ui creates the proper menu structure."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            # Check menu bar exists
            menu_bar = window.menuBar()
            assert menu_bar is not None
            
            # Check File menu exists
            actions = menu_bar.actions()
            assert len(actions) > 0
            
            file_menu = actions[0].menu()
            assert file_menu.title() == "&File"
            
            # Check recent menu and export menu exist and are disabled initially
            assert window._recent_menu is not None
            assert window._export_menu is not None
            assert not window._recent_menu.isEnabled()
            assert not window._export_menu.isEnabled()
    
    def create_temp_db(self, with_events_table=True, with_data=True, correct_schema=True):
        """Create a temporary SQLite database for testing."""
        temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp_file.close()
        self.temp_files.append(temp_file.name)
        
        conn = sqlite3.connect(temp_file.name)
        cursor = conn.cursor()
        
        if with_events_table:
            if correct_schema:
                # Create table with correct schema
                cursor.execute("""
                    CREATE TABLE events (
                        event_id INTEGER,
                        veh_id INTEGER,
                        veh_time REAL,
                        lane_id INTEGER,
                        x_map_loc REAL,
                        y_map_loc REAL,
                        x_frenet_loc REAL,
                        y_frenet_loc REAL,
                        x_map_origin REAL,
                        y_map_origin REAL,
                        veh_lat REAL,
                        veh_lon REAL,
                        veh_speed REAL,
                        veh_accel REAL,
                        veh_length REAL,
                        veh_width REAL,
                        veh_automation INTEGER,
                        osm_way_id INTEGER,
                        osm_speed_limit INTEGER,
                        osm_traffic_control INTEGER,
                        preceding_veh_id INTEGER,
                        veh_dist_trav REAL,
                        event_name TEXT
                    )
                """)
            else:
                # Create table with incorrect schema
                cursor.execute("CREATE TABLE events (id INTEGER, name TEXT)")
            
            if with_data:
                if correct_schema:
                    cursor.execute("""
                        INSERT INTO events VALUES (
                            1, 100, 1.5, 2, 123.45, 678.90, 0, 0, 0, 0, 36.1, -115.2,
                            25.0, 0.5, 4.5, 2.0, 0, 12345, 55, 0, NULL, 150.0, 'test_event'
                        )
                    """)
                else:
                    cursor.execute("INSERT INTO events VALUES (1, 'test')")
        
        conn.commit()
        conn.close()
        return temp_file.name
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QMessageBox')
    def test_check_database_valid(self, mock_messagebox, mock_web_channel, mock_web_view, qapp):
        """Test _check_database with valid database."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            db_path = self.create_temp_db()
            
            result = window._check_database(db_path)
            
            assert result is True
            assert window._db_connection is not None
            mock_messagebox.critical.assert_not_called()
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QMessageBox')
    def test_check_database_no_events_table(self, mock_messagebox, mock_web_channel, mock_web_view, qapp):
        """Test _check_database with database missing events table."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            db_path = self.create_temp_db(with_events_table=False)
            
            result = window._check_database(db_path)
            
            assert result is False
            mock_messagebox.critical.assert_called_once()
            args = mock_messagebox.critical.call_args[0]
            assert "does not contain an 'events' table" in args[2]
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QMessageBox')
    def test_check_database_wrong_schema(self, mock_messagebox, mock_web_channel, mock_web_view, qapp):
        """Test _check_database with incorrect table schema."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            db_path = self.create_temp_db(correct_schema=False)
            
            result = window._check_database(db_path)
            
            assert result is False
            mock_messagebox.critical.assert_called_once()
            args = mock_messagebox.critical.call_args[0]
            assert "schema does not match" in args[2]
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QMessageBox')
    def test_check_database_empty_table(self, mock_messagebox, mock_web_channel, mock_web_view, qapp):
        """Test _check_database with empty events table."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            db_path = self.create_temp_db(with_data=False)
            
            result = window._check_database(db_path)
            
            assert result is False
            mock_messagebox.critical.assert_called_once()
            args = mock_messagebox.critical.call_args[0]
            assert "Events table is empty" in args[2]
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QMessageBox')
    def test_check_database_sqlite_error(self, mock_messagebox, mock_web_channel, mock_web_view, qapp):
        """Test _check_database with SQLite error."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            # Test with non-existent file
            result = window._check_database('/nonexistent/file.db')
            
            assert result is False
            mock_messagebox.critical.assert_called_once()
            args = mock_messagebox.critical.call_args[0]
            assert "Failed to open database" in args[2]
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_load_settings_default_geometry(self, mock_web_channel, mock_web_view, qapp):
        """Test _load_settings with no config file (default geometry)."""
        with patch.object(MainWindow, '_setup_home_page'), \
             patch('pathlib.Path.exists', return_value=False):
            
            window = MainWindow()
            
            # Should have some default geometry set
            geometry = window.geometry()
            assert geometry.width() > 0
            assert geometry.height() > 0
            assert window._recent_files == []
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_load_settings_with_config_file(self, mock_web_channel, mock_web_view, qapp):
        """Test _load_settings with valid config file."""
        config_content = "100,100,800,600,false\n/path/to/recent1.db\n/path/to/recent2.db"
        
        with patch.object(MainWindow, '_setup_home_page'), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.open', mock_open(read_data=config_content)), \
             patch.object(MainWindow, '_update_recent_menu'):
            
            window = MainWindow()
            
            # Check geometry was set
            geometry = window.geometry()
            assert geometry.x() == 100
            assert geometry.y() == 100
            assert geometry.width() == 800
            assert geometry.height() == 600
            
            # Check recent files were loaded
            assert len(window._recent_files) == 2
            assert '/path/to/recent1.db' in window._recent_files
            assert '/path/to/recent2.db' in window._recent_files
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_save_settings(self, mock_web_channel, mock_web_view, qapp):
        """Test _save_settings writes correct config format."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            window._recent_files = ['/path/to/file1.db', '/path/to/file2.db']
            
            mock_file = mock_open()
            with patch('pathlib.Path.open', mock_file):
                window._save_settings()
                
                # Check that write was called
                mock_file().write.assert_called_once()
                written_content = mock_file().write.call_args[0][0]
                
                # Should contain geometry and recent files
                lines = written_content.split('\n')
                assert len(lines) >= 3  # geometry + 2 recent files
                assert '/path/to/file1.db' in lines
                assert '/path/to/file2.db' in lines
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_add_recent_file(self, mock_web_channel, mock_web_view, qapp):
        """Test add_recent_file functionality."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch.object(MainWindow, '_update_recent_menu'):
            
            window = MainWindow()
            
            # Add first file
            window.add_recent_file('/path/to/file1.db')
            assert len(window._recent_files) == 1
            assert window._recent_files[0] == str(Path('/path/to/file1.db').absolute())
            
            # Add second file
            window.add_recent_file('/path/to/file2.db')
            assert len(window._recent_files) == 2
            
            # Add duplicate file (should move to top)
            window.add_recent_file('/path/to/file1.db')
            assert len(window._recent_files) == 2
            assert window._recent_files[0] == str(Path('/path/to/file1.db').absolute())
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_add_recent_file_limit(self, mock_web_channel, mock_web_view, qapp):
        """Test add_recent_file respects 7 file limit."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch.object(MainWindow, '_update_recent_menu'):
            
            window = MainWindow()
            
            # Add 8 files
            for i in range(8):
                window.add_recent_file(f'/path/to/file{i}.db')
            
            # Should only keep 7 most recent
            assert len(window._recent_files) == 7
            assert str(Path('/path/to/file7.db').absolute()) == window._recent_files[0]
            assert str(Path('/path/to/file1.db').absolute()) == window._recent_files[6]
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QFileDialog')
    @patch('src.gui.main_window.TimeSpacePlotSettingsDialog')
    def test_open_file_success(self, mock_settings_dialog, mock_file_dialog, mock_web_channel, mock_web_view, qapp):
        """Test _open_file with successful database open."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch.object(MainWindow, 'add_recent_file'), \
             patch.object(MainWindow, '_check_database', return_value=True):
            
            window = MainWindow()
            
            # Mock file dialog
            mock_file_dialog.getOpenFileName.return_value = ('/path/to/file.db', '')
            
            # Mock settings dialog
            mock_dialog_instance = Mock()
            mock_settings_dialog.return_value = mock_dialog_instance
            mock_dialog_instance.exec.return_value = QDialog.DialogCode.Accepted
            
            window._open_file()
            
            # Verify file dialog was shown
            mock_file_dialog.getOpenFileName.assert_called_once()
            
            # Verify settings dialog was created and shown
            mock_settings_dialog.assert_called_once()
            mock_dialog_instance.exec.assert_called_once()
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QFileDialog')
    def test_open_file_no_selection(self, mock_file_dialog, mock_web_channel, mock_web_view, qapp):
        """Test _open_file when user cancels file dialog."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            # Mock file dialog returning empty string (cancelled)
            mock_file_dialog.getOpenFileName.return_value = ('', '')
            
            window._open_file()
            
            # Should not attempt database check
            mock_file_dialog.getOpenFileName.assert_called_once()
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.QMessageBox')
    def test_open_recent_file_not_found(self, mock_messagebox, mock_web_channel, mock_web_view, qapp):
        """Test _open_recent_file with non-existent file."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch.object(MainWindow, '_update_recent_menu'):
            
            window = MainWindow()
            window._recent_files = ['/nonexistent/file.db']
            
            window._open_recent_file('/nonexistent/file.db')
            
            # Should show error message
            mock_messagebox.critical.assert_called_once()
            args = mock_messagebox.critical.call_args[0]
            assert "File not found" in args[2]
            
            # Should remove from recent files
            assert '/nonexistent/file.db' not in window._recent_files
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.ImportDialog')
    def test_import_file(self, mock_import_dialog, mock_web_channel, mock_web_view, qapp):
        """Test _import_file creates and shows ImportDialog."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            mock_dialog_instance = Mock()
            mock_import_dialog.return_value = mock_dialog_instance
            
            window._import_file()
            
            mock_import_dialog.assert_called_once_with(window)
            mock_dialog_instance.setModal.assert_called_once_with(True)
            mock_dialog_instance.show.assert_called_once()
            assert window._import_dialog == mock_dialog_instance
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.ExportDialog')
    def test_export_all_no_database(self, mock_export_dialog, mock_web_channel, mock_web_view, qapp):
        """Test _export_all with no database connection."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch('src.gui.main_window.QMessageBox') as mock_messagebox:
            
            window = MainWindow()
            window._db_connection = None
            
            window._export_all()
            
            mock_messagebox.warning.assert_called_once()
            mock_export_dialog.assert_not_called()
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.ExportDialog')
    def test_export_all_with_database(self, mock_export_dialog, mock_web_channel, mock_web_view, qapp):
        """Test _export_all with valid database connection."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            window._db_connection = Mock()  # Mock database connection
            
            mock_dialog_instance = Mock()
            mock_export_dialog.return_value = mock_dialog_instance
            
            window._export_all()
            
            mock_export_dialog.assert_called_once()
            # Should pass None for selected_columns in export all mode
            args = mock_export_dialog.call_args[1]
            assert args['selected_columns'] is None
            mock_dialog_instance.exec.assert_called_once()
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    @patch('src.gui.main_window.AboutDialog')
    def test_show_about(self, mock_about_dialog, mock_web_channel, mock_web_view, qapp):
        """Test _show_about creates and shows AboutDialog."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            mock_dialog_instance = Mock()
            mock_about_dialog.return_value = mock_dialog_instance
            
            window._show_about()
            
            mock_about_dialog.assert_called_once()
            args = mock_about_dialog.call_args[1]
            assert args['parent'] == window
            assert args['is_splash'] is False
            mock_dialog_instance.exec.assert_called_once()
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_close_database_and_reset(self, mock_web_channel, mock_web_view, qapp):
        """Test _close_database_and_reset functionality."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch.object(MainWindow, '_load_home_page'):
            
            window = MainWindow()
            
            # Set up initial state
            mock_db = Mock()
            window._db_connection = mock_db
            window._plot_widget = Mock()
            window._export_menu = Mock()
            window._export_menu.setEnabled = Mock()
            
            window._close_database_and_reset()
            
            # Verify database was closed
            mock_db.close.assert_called_once()
            assert window._db_connection is None
            
            # Verify window title was reset
            assert window.windowTitle() == "Trajectory Tools"
            
            # Verify plot widget was cleaned up
            assert window._plot_widget is None
            
            # Verify export menu was disabled
            window._export_menu.setEnabled.assert_called_with(False)
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_generate_recent_files_html_empty(self, mock_web_channel, mock_web_view, qapp):
        """Test _generate_recent_files_html with empty recent files."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            window._recent_files = []
            
            html = window._generate_recent_files_html()
            
            assert "No recent files" in html
            assert "empty-state" in html
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_generate_recent_files_html_with_files(self, mock_web_channel, mock_web_view, qapp):
        """Test _generate_recent_files_html with recent files."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            window._recent_files = ['/path/to/file1.db', 'C:\\Windows\\file2.db']
            
            html = window._generate_recent_files_html()
            
            assert "<ul>" in html
            assert "</ul>" in html
            assert "file1.db" in html
            assert "file2.db" in html
            assert "openRecentFile" in html
            # Should convert backslashes to forward slashes for HTML
            assert "C:/Windows/file2.db" in html
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_handle_export_finished(self, mock_web_channel, mock_web_view, qapp):
        """Test _handle_export_finished updates selected columns."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'):
            
            window = MainWindow()
            
            # Test with non-empty selected columns
            selected_columns = ['column1', 'column2', 'column3']
            window._handle_export_finished(selected_columns)
            
            assert window._selected_export_columns == selected_columns
            
            # Test with empty list (shouldn't update)
            window._selected_export_columns = ['existing']
            window._handle_export_finished([])
            
            assert window._selected_export_columns == ['existing']
    
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_closeEvent_cleanup(self, mock_web_channel, mock_web_view, qapp):
        """Test closeEvent performs proper cleanup."""
        with patch.object(MainWindow, '_load_settings'), \
             patch.object(MainWindow, '_setup_home_page'), \
             patch.object(MainWindow, '_save_settings'):
            
            window = MainWindow()
            
            # Set up database connection
            mock_db = Mock()
            window._db_connection = mock_db
            
            # Create mock close event
            mock_event = Mock()
            
            window.closeEvent(mock_event)
            
            # Verify database was closed
            mock_db.close.assert_called_once()
            
            # Verify settings were saved
            window._save_settings.assert_called_once()
    
    @patch('src.gui.main_window.get_resource_path')
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_load_home_page_file_not_found(self, mock_web_channel, mock_web_view, mock_get_resource_path, qapp):
        """Test _load_home_page when home.html file is not found."""
        with patch.object(MainWindow, '_load_settings'):
            
            # Mock Path.exists to return False
            mock_path = Mock()
            mock_path.exists.return_value = False
            mock_get_resource_path.return_value = mock_path
            
            # Capture print output
            with patch('builtins.print') as mock_print:
                window = MainWindow()
                mock_print.assert_called_with("home.html file not found")
    
    @patch('src.gui.main_window.get_resource_path')
    @patch('src.gui.main_window.QWebEngineView')
    @patch('src.gui.main_window.QWebChannel')
    def test_load_home_page_success(self, mock_web_channel, mock_web_view, mock_get_resource_path, qapp):
        """Test _load_home_page with successful file loading."""
        with patch.object(MainWindow, '_load_settings'):
            
            template_content = "<html><body>{{RECENT_FILES}}</body></html>"
            
            # Mock Path and file operations
            mock_path = Mock()
            mock_path.exists.return_value = True
            mock_path.open.return_value.__enter__ = Mock(return_value=Mock())
            mock_path.open.return_value.__enter__.return_value.read.return_value = template_content
            mock_path.open.return_value.__exit__ = Mock(return_value=None)
            mock_get_resource_path.return_value = mock_path
            
            # Mock web view
            mock_web_view_instance = Mock()
            mock_web_view.return_value = mock_web_view_instance
            
            window = MainWindow()
            
            # Verify setHtml was called with processed content
            mock_web_view_instance.setHtml.assert_called()
            args = mock_web_view_instance.setHtml.call_args[0]
            assert "{{RECENT_FILES}}" not in args[0]  # Should be replaced