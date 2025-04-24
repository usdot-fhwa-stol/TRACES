import os
import pytest
from PyQt6.QtWidgets import QApplication, QMainWindow, QFileDialog, QMenuBar, QMenu
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt, QTimer
from unittest.mock import Mock, patch, mock_open
from gui.main_window import MainWindow  # Adjust import based on your module structure

# Fixture to create a QApplication instance
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Clean up after tests
    app.quit()

# Fixture to create a MainWindow instance
@pytest.fixture
def main_window(qapp):
    window = MainWindow()
    yield window
    window.close()

# Test initialization and UI setup
def test_init_ui(main_window):
    assert main_window.windowTitle() == QApplication.applicationName()
    assert isinstance(main_window.menuBar(), QMenuBar)
    
    # Check File menu and actions
    file_menu = main_window.menuBar().findChild(QMenu, "&File")
    assert file_menu is not None
    actions = file_menu.actions()
    assert len(actions) == 5  # Open, Open Recent, Import, Export, Exit
    assert actions[0].text() == "&Open"
    assert actions[1].text() == "Open &Recent"
    assert actions[2].text() == "&Import"
    assert actions[3].text() == "&Export"
    assert actions[4].text() == "E&xit"
    assert not actions[3].isEnabled()  # Export should be disabled

# Test load_config with no config file
@patch("os.path.exists", return_value=False)
def test_load_config_no_file(mock_exists, main_window, qapp):
    main_window.load_config()
    screen = qapp.primaryScreen().availableGeometry()
    expected_width, expected_height = 1280, 720
    expected_x = (screen.width() - expected_width) // 2
    expected_y = (screen.height() - expected_height) // 2
    geom = main_window.geometry()
    assert geom.x() == expected_x
    assert geom.y() == expected_y
    assert geom.width() == expected_width
    assert geom.height() == expected_height

# Test load_config with existing config file
@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=mock_open, read_data="100,200,1280,720,1\nfile1.txt\nfile2.txt")
def test_load_config_with_file(mock_file, mock_exists, main_window):
    main_window.load_config()
    assert main_window.geometry().x() == 100
    assert main_window.geometry().y() == 200
    assert main_window.geometry().width() == 1280
    assert main_window.geometry().height() == 720
    assert main_window.isMaximized()
    assert main_window.recent_files == ["file1.txt", "file2.txt"]
    assert len(main_window.recent_menu.actions()) == 2

# Test save_config for non-maximized window
@patch("builtins.open", new_callable=mock_open)
def test_save_config_non_maximized(mock_file, main_window):
    main_window.setGeometry(100, 200, 1280, 720)
    main_window.recent_files = ["file1.txt", "file2.txt"]
    main_window.save_config()
    mock_file().write.assert_called_once_with("100,200,1280,720,0\nfile1.txt\nfile2.txt\n")

# Test save_config for maximized window
@patch("builtins.open", new_callable=mock_open)
def test_save_config_maximized(mock_file, main_window):
    main_window.showMaximized()
    main_window.recent_files = ["file1.txt"]
    main_window.save_config()
    mock_file().write.assert_called_once_with("0,0,1280,720,1\nfile1.txt\n")

# Test update_recent_menu
def test_update_recent_menu(main_window):
    main_window.recent_files = ["file1.txt", "file2.txt"]
    main_window.update_recent_menu()
    actions = main_window.recent_menu.actions()
    assert len(actions) == 2
    assert actions[0].text() == "file1.txt"
    assert actions[1].text() == "file2.txt"
    assert main_window.recent_menu.isEnabled()

# Test update_recent_menu with empty recent files
def test_update_recent_menu_empty(main_window):
    main_window.recent_files = []
    main_window.update_recent_menu()
    assert not main_window.recent_menu.actions()
    assert not main_window.recent_menu.isEnabled()

# Test open_file
@patch.object(QFileDialog, "getOpenFileName", return_value=("test.txt", ""))
def test_open_file(mock_get_open, main_window):
    main_window.open_file()
    assert main_window.windowTitle() == f"{QApplication.applicationName()} - test.txt"
    assert main_window.recent_files == ["test.txt"]
    assert len(main_window.recent_menu.actions()) == 1
    assert main_window.recent_menu.actions()[0].text() == "test.txt"

# Test open_file with existing recent file
@patch.object(QFileDialog, "getOpenFileName", return_value=("file1.txt", ""))
def test_open_file_existing_recent(mock_get_open, main_window):
    main_window.recent_files = ["file2.txt", "file1.txt"]
    main_window.open_file()
    assert main_window.recent_files == ["file1.txt", "file2.txt"]
    assert len(main_window.recent_menu.actions()) == 2
    assert main_window.recent_menu.actions()[0].text() == "file1.txt"

# Test open_file with max recent files
@patch.object(QFileDialog, "getOpenFileName", return_value=("new_file.txt", ""))
def test_open_file_max_recent(mock_get_open, main_window):
    main_window.recent_files = [f"file{i}.txt" for i in range(7)]
    main_window.open_file()
    assert len(main_window.recent_files) == 7
    assert main_window.recent_files[0] == "new_file.txt"
    assert main_window.recent_files[-1] == "file1.txt"

# Test open_recent_file with existing file
@patch("os.path.exists", return_value=True)
def test_open_recent_file_exists(mock_exists, main_window):
    main_window.recent_files = ["file2.txt"]
    main_window.open_recent_file("file2.txt")
    assert main_window.windowTitle() == f"{QApplication.applicationName()} - file2.txt"
    assert main_window.recent_files == ["file2.txt"]
    assert main_window.recent_menu.actions()[0].text() == "file2.txt"

# Test open_recent_file with non-existing file
@patch("os.path.exists", return_value=False)
def test_open_recent_file_not_exists(mock_exists, main_window):
    main_window.recent_files = ["file1.txt"]
    main_window.open_recent_file("file1.txt")
    assert main_window.recent_files == []
    assert not main_window.recent_menu.actions()
    assert not main_window.recent_menu.isEnabled()

# Test import_data
@patch("main_window.ProgressDialog")
def test_import_data(mock_progress_dialog, main_window):
    main_window.import_data()
    mock_progress_dialog.assert_called_once_with(main_window)
    assert main_window.progress_dialog.show.called

# Test simulate_import (simplified, as it involves QTimer)
@patch("main_window.QTimer")
def test_simulate_import(mock_qtimer, main_window):
    main_window.progress_dialog = Mock()
    main_window.simulate_import()
    assert main_window.event_count == 0
    assert main_window.total_events == 10
    # Verify QTimer is set up
    mock_qtimer.singleShot.assert_called()

# Test closeEvent
@patch.object(MainWindow, "save_config")
def test_close_event(mock_save_config, main_window):
    event = Mock()
    main_window.closeEvent(event)
    mock_save_config.assert_called_once()
    event.accept.assert_called_once()