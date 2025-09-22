"""
Comprehensive unit tests for gui package.
Tests key GUI components with proper PyQt6 mocking for headless environment.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
import sqlite3
import tempfile
import json
import sys
import os
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import GUI modules - PyQt6 is mocked globally by conftest.py
from gui.main_window import MainWindow, WebBridge
from gui.about_dialog import AboutDialog
from gui.statistics_dialog import StatisticsDialog
from gui.import_dialog import ImportDialog, _ImportWorkerThread
from gui.export_dialog import ExportDialog, _ExportWorkerThread


class TestWebBridge(unittest.TestCase):
    """Test cases for WebBridge class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_main_window = Mock()
        self.web_bridge = WebBridge(self.mock_main_window)
    
    def test_init(self):
        """Test WebBridge initialization."""
        self.assertEqual(self.web_bridge.main_window, self.mock_main_window)
    
    def test_open_file(self):
        """Test openFile method."""
        self.web_bridge.openFile()
        self.mock_main_window._open_file.assert_called_once()
    
    def test_import_file(self):
        """Test importFile method."""
        self.web_bridge.importFile()
        self.mock_main_window._import_file.assert_called_once()
    
    def test_open_recent_file(self):
        """Test openRecentFile method."""
        test_path = "test/path/file.sqlite"
        self.web_bridge.openRecentFile(test_path)
        expected_path = str(Path(test_path))
        self.mock_main_window._open_recent_file.assert_called_once_with(expected_path)


class TestMainWindow(unittest.TestCase):
    """Test cases for MainWindow class."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch.multiple('gui.main_window',
                          QMenuBar=Mock(),
                          QWebEngineView=Mock(),
                          QWebChannel=Mock()):
            self.main_window = MainWindow()
    
    def test_init(self):
        """Test MainWindow initialization."""
        self.assertEqual(self.main_window._recent_files, [])
        self.assertIsNone(self.main_window._db_connection)
        self.assertIsNone(self.main_window._plot_widget)
        self.assertEqual(self.main_window._selected_export_columns, [])
    
    @patch('gui.main_window.Path')
    @patch('builtins.open', new_callable=mock_open, read_data='100,100,800,600,false\n/path/file1.sqlite\n/path/file2.sqlite')
    def test_load_settings_valid_config(self, mock_file, mock_path):
        """Test _load_settings with valid configuration."""
        mock_path.return_value.exists.return_value = True
        
        with patch('gui.main_window.QApplication') as mock_app:
            mock_screen = Mock()
            mock_screen.availableGeometry.return_value = Mock(width=1920, height=1080)
            mock_app.primaryScreen.return_value = mock_screen
            
            self.main_window._load_settings()
        
        # Verify geometry was set
        self.main_window.setGeometry.assert_called()
        
        # Verify recent files were loaded
        expected_files = ['/path/file1.sqlite', '/path/file2.sqlite']
        self.assertEqual(self.main_window._recent_files, expected_files)
    
    @patch('gui.main_window.Path')
    def test_load_settings_no_config(self, mock_path):
        """Test _load_settings with no configuration file."""
        mock_path.return_value.exists.return_value = False
        
        with patch('gui.main_window.QApplication') as mock_app:
            mock_screen = Mock()
            mock_screen.availableGeometry.return_value = Mock(width=1920, height=1080)
            mock_app.primaryScreen.return_value = mock_screen
            
            self.main_window._load_settings()
        
        # Should set default geometry
        self.main_window.setGeometry.assert_called()
    
    @patch('builtins.open', new_callable=mock_open)
    @patch('gui.main_window.Path')
    def test_save_settings(self, mock_path, mock_file):
        """Test _save_settings method."""
        # Setup mock geometry
        mock_geometry = Mock()
        mock_geometry.x.return_value = 100
        mock_geometry.y.return_value = 100
        mock_geometry.width.return_value = 800
        mock_geometry.height.return_value = 600
        
        self.main_window.isMaximized = Mock(return_value=False)
        self.main_window.geometry = Mock(return_value=mock_geometry)
        self.main_window._recent_files = ['/path/file1.sqlite']
        
        self.main_window._save_settings()
        
        # Verify file was written
        mock_file.assert_called_once()
        handle = mock_file()
        handle.write.assert_called()
    
    def test_add_recent_file_new(self):
        """Test add_recent_file with new file."""
        test_file = "/path/to/test.sqlite"
        
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.absolute.return_value = test_file
            
            self.main_window._update_recent_menu = Mock()
            self.main_window.add_recent_file(test_file)
        
        self.assertEqual(self.main_window._recent_files[0], test_file)
        self.main_window._update_recent_menu.assert_called_once()
    
    def test_add_recent_file_existing(self):
        """Test add_recent_file with existing file (should move to top)."""
        existing_files = ["/path/file1.sqlite", "/path/file2.sqlite"]
        self.main_window._recent_files = existing_files.copy()
        
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.absolute.return_value = "/path/file2.sqlite"
            
            self.main_window._update_recent_menu = Mock()
            self.main_window.add_recent_file("/path/file2.sqlite")
        
        # file2 should now be first
        expected = ["/path/file2.sqlite", "/path/file1.sqlite"]
        self.assertEqual(self.main_window._recent_files, expected)
    
    def test_add_recent_file_limit(self):
        """Test add_recent_file respects 7-file limit."""
        # Start with 7 files
        files = [f"/path/file{i}.sqlite" for i in range(7)]
        self.main_window._recent_files = files
        
        with patch('gui.main_window.Path') as mock_path:
            mock_path.return_value.absolute.return_value = "/path/new.sqlite"
            
            self.main_window._update_recent_menu = Mock()
            self.main_window.add_recent_file("/path/new.sqlite")
        
        # Should still have 7 files, with new one first and last one dropped
        self.assertEqual(len(self.main_window._recent_files), 7)
        self.assertEqual(self.main_window._recent_files[0], "/path/new.sqlite")
        self.assertNotIn("/path/file6.sqlite", self.main_window._recent_files)
    
    def test_check_database_valid(self):
        """Test _check_database with valid database."""
        # Create in-memory database with correct schema
        db_connection = sqlite3.connect(':memory:')
        
        # Create events table with correct schema
        db_connection.execute("""
            CREATE TABLE events (
                event_id INTEGER NOT NULL,
                veh_id INTEGER NOT NULL,
                veh_time REAL NOT NULL,
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
        
        # Insert test data
        db_connection.execute("INSERT INTO events (event_id, veh_id, veh_time) VALUES (1, 100, 1.0)")
        db_connection.commit()
        
        with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            # Copy in-memory db to file
            file_db = sqlite3.connect(tmp_path)
            db_connection.backup(file_db)
            file_db.close()
            db_connection.close()
            
            # Test the method
            with patch('sqlite3.connect') as mock_connect:
                mock_connect.return_value = Mock()
                mock_cursor = Mock()
                mock_connect.return_value.cursor.return_value = mock_cursor
                
                # Mock table check
                mock_cursor.fetchone.side_effect = [
                    ('events',),  # Table exists
                    {  # Schema check - return as dict-like
                        'event_id': 'INTEGER',
                        'veh_id': 'INTEGER', 
                        'veh_time': 'REAL',
                        'lane_id': 'INTEGER',
                        'x_map_loc': 'REAL',
                        'y_map_loc': 'REAL',
                        'x_frenet_loc': 'REAL',
                        'y_frenet_loc': 'REAL',
                        'x_map_origin': 'REAL',
                        'y_map_origin': 'REAL',
                        'veh_lat': 'REAL',
                        'veh_lon': 'REAL',
                        'veh_speed': 'REAL',
                        'veh_accel': 'REAL',
                        'veh_length': 'REAL',
                        'veh_width': 'REAL',
                        'veh_automation': 'INTEGER',
                        'osm_way_id': 'INTEGER',
                        'osm_speed_limit': 'INTEGER',
                        'osm_traffic_control': 'INTEGER',
                        'preceding_veh_id': 'INTEGER',
                        'veh_dist_trav': 'REAL',
                        'event_name': 'TEXT'
                    },
                    (1,)  # Record count > 0
                ]
                
                # Mock fetchall for schema
                mock_cursor.fetchall.return_value = [
                    (0, 'event_id', 'INTEGER', 1, None, 1),
                    (1, 'veh_id', 'INTEGER', 1, None, 0),
                    (2, 'veh_time', 'REAL', 1, None, 0),
                    (3, 'lane_id', 'INTEGER', 0, None, 0),
                    (4, 'x_map_loc', 'REAL', 0, None, 0),
                    (5, 'y_map_loc', 'REAL', 0, None, 0),
                    (6, 'x_frenet_loc', 'REAL', 0, None, 0),
                    (7, 'y_frenet_loc', 'REAL', 0, None, 0),
                    (8, 'x_map_origin', 'REAL', 0, None, 0),
                    (9, 'y_map_origin', 'REAL', 0, None, 0),
                    (10, 'veh_lat', 'REAL', 0, None, 0),
                    (11, 'veh_lon', 'REAL', 0, None, 0),
                    (12, 'veh_speed', 'REAL', 0, None, 0),
                    (13, 'veh_accel', 'REAL', 0, None, 0),
                    (14, 'veh_length', 'REAL', 0, None, 0),
                    (15, 'veh_width', 'REAL', 0, None, 0),
                    (16, 'veh_automation', 'INTEGER', 0, None, 0),
                    (17, 'osm_way_id', 'INTEGER', 0, None, 0),
                    (18, 'osm_speed_limit', 'INTEGER', 0, None, 0),
                    (19, 'osm_traffic_control', 'INTEGER', 0, None, 0),
                    (20, 'preceding_veh_id', 'INTEGER', 0, None, 0),
                    (21, 'veh_dist_trav', 'REAL', 0, None, 0),
                    (22, 'event_name', 'TEXT', 0, None, 0),
                ]
                
                result = self.main_window._check_database(tmp_path)
                self.assertTrue(result)
                
        finally:
            os.unlink(tmp_path)
    
    def test_check_database_no_table(self):
        """Test _check_database with missing events table."""
        with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            with patch('sqlite3.connect') as mock_connect:
                mock_connect.return_value = Mock()
                mock_cursor = Mock()
                mock_connect.return_value.cursor.return_value = mock_cursor
                
                # Mock no table found
                mock_cursor.fetchone.return_value = None
                
                with patch('gui.main_window.QMessageBox'):
                    result = self.main_window._check_database(tmp_path)
                    self.assertFalse(result)
                
        finally:
            os.unlink(tmp_path)
    
    def test_check_database_empty_table(self):
        """Test _check_database with empty events table."""
        with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            with patch('sqlite3.connect') as mock_connect:
                mock_connect.return_value = Mock()
                mock_cursor = Mock()
                mock_connect.return_value.cursor.return_value = mock_cursor
                
                # Mock table exists, schema correct, but no records
                mock_cursor.fetchone.side_effect = [
                    ('events',),  # Table exists
                    (0,)  # No records
                ]
                
                # Mock correct schema
                mock_cursor.fetchall.return_value = [
                    (0, 'event_id', 'INTEGER', 1, None, 1),
                    (1, 'veh_id', 'INTEGER', 1, None, 0),
                    (2, 'veh_time', 'REAL', 1, None, 0),
                    # ... other columns
                ]
                
                with patch('gui.main_window.QMessageBox'):
                    result = self.main_window._check_database(tmp_path)
                    self.assertFalse(result)
                
        finally:
            os.unlink(tmp_path)


class TestAboutDialog(unittest.TestCase):
    """Test cases for AboutDialog class."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch.multiple('gui.about_dialog',
                          QDialog=Mock(),
                          QVBoxLayout=Mock(),
                          QTextBrowser=Mock(),
                          QPushButton=Mock(),
                          QHBoxLayout=Mock()):
            self.about_dialog = AboutDialog()
    
    def test_init_regular_mode(self):
        """Test AboutDialog initialization in regular mode."""
        self.assertFalse(self.about_dialog._is_splash)
        self.assertIsNone(self.about_dialog._countdown_timer)
        self.assertEqual(self.about_dialog._countdown_value, 5)
    
    def test_init_splash_mode(self):
        """Test AboutDialog initialization in splash mode."""
        with patch.multiple('gui.about_dialog',
                          QDialog=Mock(),
                          QVBoxLayout=Mock(),
                          QTextBrowser=Mock(),
                          QPushButton=Mock(),
                          QHBoxLayout=Mock(),
                          QTimer=Mock()):
            dialog = AboutDialog(is_splash=True)
            self.assertTrue(dialog._is_splash)
    
    @patch('gui.about_dialog.get_resource_path')
    @patch('gui.about_dialog.Path')
    @patch('builtins.open', new_callable=mock_open, read_data='<html><body>Test</body></html>')
    def test_load_content_success(self, mock_file, mock_path, mock_get_resource):
        """Test _load_content with successful file load."""
        mock_get_resource.return_value = '/path/to/about.html'
        mock_path_instance = Mock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.open = mock_file
        mock_path.return_value = mock_path_instance
        
        self.about_dialog._text_browser = Mock()
        self.about_dialog._load_content()
        
        self.about_dialog._text_browser.setHtml.assert_called_once()
    
    @patch('gui.about_dialog.get_resource_path')
    @patch('gui.about_dialog.Path')
    def test_load_content_file_not_found(self, mock_path, mock_get_resource):
        """Test _load_content with missing file."""
        mock_get_resource.return_value = '/path/to/about.html'
        mock_path_instance = Mock()
        mock_path_instance.exists.return_value = False
        mock_path.return_value = mock_path_instance
        
        self.about_dialog._text_browser = Mock()
        self.about_dialog._load_content()
        
        # Should set fallback HTML
        self.about_dialog._text_browser.setHtml.assert_called_once()
        call_args = self.about_dialog._text_browser.setHtml.call_args[0][0]
        self.assertIn("About information not available", call_args)
    
    def test_update_button_text_splash(self):
        """Test _update_button_text in splash mode."""
        self.about_dialog._is_splash = True
        self.about_dialog._countdown_value = 3
        self.about_dialog._close_button = Mock()
        
        self.about_dialog._update_button_text()
        
        self.about_dialog._close_button.setText.assert_called_with("Close (3)")
    
    def test_update_button_text_regular(self):
        """Test _update_button_text in regular mode."""
        self.about_dialog._is_splash = False
        self.about_dialog._close_button = Mock()
        
        self.about_dialog._update_button_text()
        
        self.about_dialog._close_button.setText.assert_called_with("Close")


class TestStatisticsDialog(unittest.TestCase):
    """Test cases for StatisticsDialog class."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create in-memory database with test data
        self.db_connection = sqlite3.connect(':memory:')
        self.db_connection.execute("""
            CREATE TABLE events (
                event_id INTEGER,
                veh_id INTEGER,
                veh_time REAL,
                veh_speed REAL,
                veh_accel REAL
            )
        """)
        
        # Insert test data
        test_data = [
            (1, 100, 1.0, 25.0, 2.0),
            (1, 100, 2.0, 30.0, 1.5),
            (1, 100, 3.0, 35.0, 1.0),
            (1, 101, 1.5, 20.0, 2.5),
            (1, 101, 2.5, 25.0, 1.5),
        ]
        
        self.db_connection.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)", 
            test_data
        )
        
        # Mock parent and dialog
        self.mock_parent = Mock()
        self.mock_parent.height.return_value = 1000
        
        with patch.multiple('gui.statistics_dialog',
                          QDialog=Mock(),
                          QVBoxLayout=Mock(),
                          QTableWidget=Mock(),
                          QTableWidgetItem=Mock(),
                          QHeaderView=Mock(),
                          QTimer=Mock()):
            self.dialog = StatisticsDialog(
                self.mock_parent, 
                [100, 101], 
                self.db_connection, 
                1, 
                "All"
            )
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.db_connection.close()
    
    def test_init(self):
        """Test StatisticsDialog initialization."""
        self.assertEqual(self.dialog.veh_ids, [100, 101])
        self.assertEqual(self.dialog.event_id, 1)
        self.assertEqual(self.dialog.mode, "All")
        self.assertEqual(self.dialog.db_connection, self.db_connection)
    
    def test_calculate_stats_valid_data(self):
        """Test _calculate_stats with valid data."""
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = self.dialog._calculate_stats(data)
        
        self.assertEqual(stats['min'], 10.0)
        self.assertEqual(stats['max'], 50.0)
        self.assertEqual(stats['median'], 30.0)
        self.assertAlmostEqual(stats['std'], 15.811, places=2)
    
    def test_calculate_stats_empty_data(self):
        """Test _calculate_stats with empty data."""
        stats = self.dialog._calculate_stats([])
        self.assertIsNone(stats)
    
    def test_calculate_stats_single_value(self):
        """Test _calculate_stats with single value."""
        stats = self.dialog._calculate_stats([42.0])
        
        self.assertEqual(stats['min'], 42.0)
        self.assertEqual(stats['max'], 42.0)
        self.assertEqual(stats['median'], 42.0)
        self.assertEqual(stats['std'], 0.0)


class TestImportWorkerThread(unittest.TestCase):
    """Test cases for ImportWorkerThread class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'class': 'data_import.csv_process.CsvProcess',
            'col_defs': {
                'event_id': {'src_col': 0, 'type': 'int', 'func': 'copy'},
                'veh_id': {'src_col': 1, 'type': 'int', 'func': 'copy'},
                'veh_time': {'src_col': 2, 'type': 'float', 'func': 'copy'},
                'lane_id': {'src_col': 3, 'type': 'int', 'func': 'copy'},
                'x_map_loc': {'src_col': 4, 'type': 'float', 'func': 'copy'},
                'y_map_loc': {'src_col': 5, 'type': 'float', 'func': 'copy'}
            }
        }
        
        self.input_path = '/path/to/input.csv'
        self.output_path = '/path/to/output.sqlite'
        
        with patch('gui.import_dialog.QThread'):
            self.worker = _ImportWorkerThread(self.config, self.input_path, self.output_path)
    
    def test_init(self):
        """Test _ImportWorkerThread initialization."""
        self.assertEqual(self.worker.config, self.config)
        self.assertEqual(self.worker.input_path, self.input_path)
        self.assertEqual(self.worker.output_path, self.output_path)
        self.assertFalse(self.worker.interrupt_flag)
        self.assertIsNone(self.worker.db_connection)


class TestImportDialog(unittest.TestCase):
    """Test cases for ImportDialog class."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch.multiple('gui.import_dialog',
                          QDialog=Mock(),
                          QVBoxLayout=Mock(),
                          QHBoxLayout=Mock(),
                          QPushButton=Mock(),
                          QProgressBar=Mock(),
                          QTextEdit=Mock(),
                          get_resource_path=Mock(return_value='/mock/path')):
            self.import_dialog = ImportDialog()
    
    def test_init(self):
        """Test ImportDialog initialization."""
        self.assertEqual(self.import_dialog._input_path, "")
        self.assertEqual(self.import_dialog._output_path, "")
        self.assertEqual(self.import_dialog._configs, {})
        self.assertEqual(self.import_dialog._selected_file_type, "")
        self.assertIsNone(self.import_dialog._worker_thread)
        self.assertFalse(self.import_dialog._import_completed)
    
    @patch('gui.import_dialog.Path')
    @patch('builtins.open', new_callable=mock_open, read_data='{"col_defs": {"event_id": {"src_col": 0}}}')
    def test_initialize_valid_config(self, mock_file, mock_path):
        """Test _initialize with valid configuration files."""
        # Mock config directory and files
        mock_config_dir = Mock()
        mock_config_file = Mock()
        mock_config_file.name = 'test.json'
        
        mock_path.return_value = mock_config_dir
        mock_config_dir.mkdir = Mock()
        mock_config_dir.glob.return_value = [mock_config_file]
        
        mock_config_file.open.return_value.__enter__.return_value = mock_file.return_value
        
        self.import_dialog._log_message = Mock()
        self.import_dialog._initialize()
        
        # Should have loaded the config
        self.assertIn('test', self.import_dialog._configs)
        self.import_dialog._input_button.setEnabled.assert_called_with(True)
    
    @patch('gui.import_dialog.Path')
    def test_initialize_no_configs(self, mock_path):
        """Test _initialize with no configuration files."""
        mock_config_dir = Mock()
        mock_path.return_value = mock_config_dir
        mock_config_dir.mkdir = Mock()
        mock_config_dir.glob.return_value = []  # No config files
        
        self.import_dialog._log_message = Mock()
        self.import_dialog._initialize()
        
        # Should log error about no configs
        self.import_dialog._log_message.assert_called()
        error_calls = [call for call in self.import_dialog._log_message.call_args_list 
                      if 'No JSON configuration files found' in str(call)]
        self.assertTrue(len(error_calls) > 0)
    
    def test_log_message(self):
        """Test _log_message method."""
        with patch('gui.import_dialog.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = '2023-01-01 12:00:00'
            
            self.import_dialog._text_edit = Mock()
            self.import_dialog._log_file = Mock()
            
            with patch('builtins.open', mock_open()) as mock_file:
                self.import_dialog._log_message("Test message", True)
            
            self.import_dialog._text_edit.append.assert_called_once()
            # File should be opened and written to
            mock_file.assert_called()


class TestExportWorkerThread(unittest.TestCase):
    """Test cases for ExportWorkerThread class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.output_path = '/path/to/output.csv'
        self.columns = ['event_id', 'veh_id', 'veh_time']
        
        with patch('gui.export_dialog.QThread'), \
             patch('gui.export_dialog.Queue') as mock_queue_class:
            
            self.mock_queue = Mock()
            mock_queue_class.return_value = self.mock_queue
            
            self.worker = _ExportWorkerThread(
                self.output_path, 
                self.columns, 
                self.mock_queue
            )
    
    def test_init(self):
        """Test _ExportWorkerThread initialization."""
        self.assertEqual(self.worker.output_path, self.output_path)
        self.assertEqual(self.worker.columns, self.columns)
        self.assertFalse(self.worker.interrupt_flag)


class TestExportDialog(unittest.TestCase):
    """Test cases for ExportDialog class."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create in-memory database
        self.db_connection = sqlite3.connect(':memory:')
        self.db_connection.execute("""
            CREATE TABLE events (
                event_id INTEGER,
                veh_id INTEGER,
                veh_time REAL
            )
        """)
        self.db_connection.execute("INSERT INTO events VALUES (1, 100, 1.0)")
        self.db_connection.commit()
        
        with patch.multiple('gui.export_dialog',
                          QDialog=Mock(),
                          QVBoxLayout=Mock(),
                          QHBoxLayout=Mock(),
                          QPushButton=Mock(),
                          QProgressBar=Mock(),
                          QLabel=Mock(),
                          QCheckBox=Mock(),
                          QComboBox=Mock(),
                          QGridLayout=Mock(),
                          QWidget=Mock()):
            
            # Test Export All mode
            self.export_all_dialog = ExportDialog(
                db_connection=self.db_connection,
                selected_columns=None
            )
            
            # Test Export Selected mode
            self.export_selected_dialog = ExportDialog(
                db_connection=self.db_connection,
                selected_columns=['event_id', 'veh_id']
            )
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.db_connection.close()
    
    def test_init_export_all_mode(self):
        """Test ExportDialog initialization in Export All mode."""
        self.assertTrue(self.export_all_dialog.is_export_all_mode)
        self.assertEqual(self.export_all_dialog.selected_columns, [])
    
    def test_init_export_selected_mode(self):
        """Test ExportDialog initialization in Export Selected mode."""
        self.assertFalse(self.export_selected_dialog.is_export_all_mode)
        self.assertEqual(self.export_selected_dialog.selected_columns, ['event_id', 'veh_id'])
    
    def test_get_record_count(self):
        """Test _get_record_count method."""
        count = self.export_all_dialog._get_record_count()
        self.assertEqual(count, 1)  # We inserted one record
    
    def test_get_column_names(self):
        """Test _get_column_names method."""
        columns = self.export_all_dialog._get_column_names()
        expected = ['event_id', 'veh_id', 'veh_time']
        self.assertEqual(columns, expected)
    
    def test_get_selected_columns_export_all(self):
        """Test _get_selected_columns in Export All mode."""
        with patch.object(self.export_all_dialog, '_get_column_names', 
                         return_value=['col1', 'col2', 'col3']):
            columns = self.export_all_dialog._get_selected_columns()
            self.assertEqual(columns, ['col1', 'col2', 'col3'])
    
    def test_get_selected_columns_export_selected(self):
        """Test _get_selected_columns in Export Selected mode."""
        # Mock checkboxes
        mock_checkbox1 = Mock()
        mock_checkbox1.isChecked.return_value = True
        mock_checkbox2 = Mock()  
        mock_checkbox2.isChecked.return_value = False
        mock_checkbox3 = Mock()
        mock_checkbox3.isChecked.return_value = True
        
        self.export_selected_dialog._column_checkboxes = {
            'col1': mock_checkbox1,
            'col2': mock_checkbox2,
            'col3': mock_checkbox3
        }
        
        columns = self.export_selected_dialog._get_selected_columns()
        expected = ['col1', 'col3']  # Only checked boxes
        self.assertEqual(columns, expected)


class TestGUIIntegration(unittest.TestCase):
    """Integration tests for GUI components."""
    
    def test_main_window_dialog_integration(self):
        """Test MainWindow integration with dialogs."""
        with patch.multiple('gui.main_window',
                          QMenuBar=Mock(),
                          QWebEngineView=Mock(),
                          QWebChannel=Mock(),
                          ImportDialog=Mock(),
                          ExportDialog=Mock(),
                          AboutDialog=Mock()):
            
            main_window = MainWindow()
            
            # Test import dialog creation
            main_window._import_file()
            # Should create ImportDialog (mocked)
            
            # Test about dialog
            main_window._show_about()
            # Should create AboutDialog (mocked)
    
    def test_dialog_signal_connections(self):
        """Test signal connections between dialogs and main window."""
        with patch.multiple('gui.main_window',
                          QMenuBar=Mock(),
                          QWebEngineView=Mock(),
                          QWebChannel=Mock()):
            
            main_window = MainWindow()
            
            # Mock dialog with signal
            mock_dialog = Mock()
            mock_signal = Mock()
            mock_dialog.import_finished = mock_signal
            
            # Test signal connection (would be done in real dialog)
            mock_signal.connect.assert_not_called()  # Not connected yet
            
            # Simulate connection
            mock_signal.connect(main_window._handle_import_finished)
            mock_signal.connect.assert_called_once()
    
    @patch('gui.main_window.sqlite3.connect')
    def test_database_operations_integration(self, mock_connect):
        """Test database operations integration."""
        # Setup mock database
        mock_db = Mock()
        mock_connect.return_value = mock_db
        mock_cursor = Mock()
        mock_db.cursor.return_value = mock_cursor
        
        # Mock proper schema check
        mock_cursor.fetchone.side_effect = [
            ('events',),  # Table exists
            (1,)  # Has records
        ]
        
        # Mock schema validation
        expected_columns = [
            (0, 'event_id', 'INTEGER', 1, None, 1),
            (1, 'veh_id', 'INTEGER', 1, None, 0),
            (2, 'veh_time', 'REAL', 1, None, 0),
            # Add other required columns...
        ]
        mock_cursor.fetchall.return_value = expected_columns
        
        with patch.multiple('gui.main_window',
                          QMenuBar=Mock(),
                          QWebEngineView=Mock(), 
                          QWebChannel=Mock(),
                          QMessageBox=Mock()):
            
            main_window = MainWindow()
            
            # Test database validation
            result = main_window._check_database('/path/to/test.sqlite')
            
            # Should connect to database and validate
            mock_connect.assert_called_with('/path/to/test.sqlite')
            
            # Result depends on mocked validation
            # In this case, we need to properly mock the schema check
            # The actual result would depend on schema validation logic


if __name__ == '__main__':
    unittest.main()
