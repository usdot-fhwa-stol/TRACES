import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

# Add the src directory to the Python path for imports
# This assumes conftest.py is in the test/ directory
test_dir = os.path.dirname(__file__)
src_dir = os.path.join(os.path.dirname(test_dir), 'src')
sys.path.insert(0, src_dir)

@pytest.fixture
def mock_utm():
    """Mock UTM library for testing."""
    import unittest.mock
    with unittest.mock.patch('utm.from_latlon') as mock_from:
        with unittest.mock.patch('utm.to_latlon') as mock_to:
            mock_from.return_value = (500000, 4000000, 10, 'S')
            mock_to.return_value = (36.1234, -115.5678)
            yield mock_from, mock_to

@pytest.fixture
def mock_qapplication(qapp):
    """Provide QApplication instance from pytest-qt's qapp fixture."""
    return qapp

@pytest.fixture
def mock_qwebengine():
    """Mock QWebEngineView and related components to avoid crashes."""
    from PyQt6.QtWidgets import QWidget
    
    with patch('gui.main_window.QWebEngineView') as mock_view_class, \
         patch('gui.main_window.QWebChannel') as mock_channel_class:
        
        # Create a real QWidget to use as the web view
        mock_web_view_instance = QWidget()
        
        # Mock the QWebEngineView class to return our widget
        mock_view_class.return_value = mock_web_view_instance
        
        # Add necessary methods that the code expects
        mock_web_view_instance.page = MagicMock()
        mock_web_view_instance.page.return_value.setWebChannel = MagicMock()
        mock_web_view_instance.setHtml = MagicMock()
        
        # Mock QWebChannel
        mock_channel_instance = MagicMock()
        mock_channel_class.return_value = mock_channel_instance
        
        yield {
            'view_class': mock_view_class,
            'view_instance': mock_web_view_instance,
            'channel_class': mock_channel_class,
            'channel_instance': mock_channel_instance
        }

@pytest.fixture
def mock_dialogs():
    """Mock various Qt dialogs."""
    with patch('gui.main_window.QFileDialog') as mock_file_dialog, \
         patch('gui.main_window.QMessageBox') as mock_message_box:
        
        # Configure QFileDialog mock
        mock_file_dialog.getOpenFileName = Mock(return_value=('', ''))
        mock_file_dialog.getSaveFileName = Mock(return_value=('', ''))
        
        # Configure QMessageBox mock
        mock_message_box.critical = Mock()
        mock_message_box.warning = Mock()
        mock_message_box.information = Mock()
        
        yield {
            'file_dialog': mock_file_dialog,
            'message_box': mock_message_box
        }

@pytest.fixture
def mock_custom_widgets():
    """Mock custom widget imports."""
    from PyQt6.QtWidgets import QWidget, QDialog
    
    with patch('gui.main_window.TimeSpacePlotWidget') as mock_plot, \
         patch('gui.main_window.TimeSpacePlotSettingsDialog') as mock_settings, \
         patch('gui.main_window.ImportDialog') as mock_import, \
         patch('gui.main_window.AboutDialog') as mock_about, \
         patch('gui.main_window.ExportDialog') as mock_export:
        
        # Create real QWidget instances where needed
        mock_plot_instance = QWidget()
        mock_plot_instance.set_database = MagicMock()
        mock_plot_instance._apply_settings = MagicMock()
        mock_plot_instance.setParent = MagicMock()
        mock_plot_instance.deleteLater = MagicMock()
        mock_plot.return_value = mock_plot_instance
        
        # Mock dialog instances
        mock_settings_instance = MagicMock()
        mock_settings_instance.exec.return_value = QDialog.DialogCode.Accepted
        mock_settings_instance.settings_applied = MagicMock()
        mock_settings_instance.settings_applied.connect = MagicMock()
        mock_settings.return_value = mock_settings_instance
        
        mock_import_instance = MagicMock()
        mock_import_instance.setModal = MagicMock()
        mock_import_instance.show = MagicMock()
        mock_import_instance.import_finished = MagicMock()
        mock_import_instance.import_finished.connect = MagicMock()
        mock_import.return_value = mock_import_instance
        
        mock_about_instance = MagicMock()
        mock_about_instance.exec = MagicMock()
        mock_about.return_value = mock_about_instance
        
        mock_export_instance = MagicMock()
        mock_export_instance.setModal = MagicMock()
        mock_export_instance.exec = MagicMock()
        mock_export_instance.export_finished = MagicMock()
        mock_export_instance.export_finished.connect = MagicMock()
        mock_export.return_value = mock_export_instance
        
        yield {
            'plot': mock_plot,
            'plot_instance': mock_plot_instance,
            'settings': mock_settings,
            'settings_instance': mock_settings_instance,
            'import': mock_import,
            'import_instance': mock_import_instance,
            'about': mock_about,
            'about_instance': mock_about_instance,
            'export': mock_export,
            'export_instance': mock_export_instance
        }

@pytest.fixture
def mock_utils():
    """Mock utility functions."""
    with patch('gui.main_window.get_resource_path') as mock_resource:
        mock_resource.return_value = 'home.html'
        yield mock_resource

@pytest.fixture
def mock_sqlite():
    """Mock sqlite3 database operations."""
    import sqlite3 as real_sqlite3
    
    with patch('gui.main_window.sqlite3') as mock_db:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        mock_conn.cursor.return_value = mock_cursor
        mock_db.connect.return_value = mock_conn
        
        # CRITICAL: Make sqlite3.Error point to the real exception class
        # so it can be used in except clauses
        mock_db.Error = real_sqlite3.Error
        
        # Default successful database checks
        mock_cursor.fetchone.return_value = ('events',)
        mock_cursor.fetchall.return_value = [
            (0, 'event_id', 'INTEGER', 0, None, 0),
            (1, 'veh_id', 'INTEGER', 0, None, 0),
            (2, 'veh_time', 'REAL', 0, None, 0),
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
            (22, 'event_name', 'TEXT', 0, None, 0)
        ]
        
        yield {
            'module': mock_db,
            'connection': mock_conn,
            'cursor': mock_cursor
        }

@pytest.fixture
def temp_config_file(tmp_path):
    """Create a temporary configuration file for testing."""
    config_file = tmp_path / "trajectory_tools.conf"
    return config_file

@pytest.fixture
def mock_home_html(tmp_path):
    """Create a mock home.html file for testing."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head><title>Trajectory Tools</title></head>
    <body>
        <h1>Welcome to Trajectory Tools</h1>
        <div id="recent-files">{{RECENT_FILES}}</div>
    </body>
    </html>
    """
    html_file = tmp_path / "home.html"
    html_file.write_text(html_content, encoding='utf-8')
    return html_file