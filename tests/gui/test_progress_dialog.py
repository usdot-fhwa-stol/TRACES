import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QProgressBar, QTextEdit, QVBoxLayout
from PyQt6.QtCore import Qt
from gui.progress_dialog import ProgressDialog  # Adjust import based on your module structure

# Fixture to create a QApplication instance
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    app.quit()

# Fixture to create a ProgressDialog instance
@pytest.fixture
def progress_dialog(qapp):
    dialog = ProgressDialog()
    yield dialog
    dialog.close()

# Test initialization and UI setup
def test_init_ui(progress_dialog):
    assert progress_dialog.windowTitle() == "Import Progress"
    assert progress_dialog.size().width() == 640
    assert progress_dialog.size().height() == 400
    assert progress_dialog.windowModality() == Qt.WindowModality.ApplicationModal
    assert progress_dialog.windowFlags() == (Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)

    # Check layout and widgets
    layout = progress_dialog.layout()
    assert isinstance(layout, QVBoxLayout)
    assert layout.count() == 2

    # Check progress bar
    progress_bar = progress_dialog.progress_bar
    assert isinstance(progress_bar, QProgressBar)
    assert progress_bar.minimum() == 0
    assert progress_bar.maximum() == 100
    assert progress_bar.value() == 0

    # Check text area
    text_area = progress_dialog.text_area
    assert isinstance(text_area, QTextEdit)
    assert text_area.isReadOnly()

# Test update_progress
def test_update_progress(progress_dialog):
    progress_dialog.update_progress(50.6)
    assert progress_dialog.progress_bar.value() == 50  # Should be floored to int
    progress_dialog.update_progress(100)
    assert progress_dialog.progress_bar.value() == 100
    progress_dialog.update_progress(0)
    assert progress_dialog.progress_bar.value() == 0

# Test append_message
def test_append_message(progress_dialog, qtbot):
    message = "Processing event 1..."
    progress_dialog.append_message(message)
    assert message in progress_dialog.text_area.toPlainText()
    
    # Check scrollbar is set to maximum
    scrollbar = progress_dialog.text_area.verticalScrollBar()
    assert scrollbar.value() == scrollbar.maximum()

    # Append another message
    progress_dialog.append_message("Event 2 completed.")
    assert "Processing event 1...\nEvent 2 completed." in progress_dialog.text_area.toPlainText()
    assert scrollbar.value() == scrollbar.maximum()

# Test enable_close_button
def test_enable_close_button(progress_dialog, qtbot):
    progress_dialog.enable_close_button()
    assert progress_dialog.windowFlags() == (Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
    # Verify show() was called implicitly via setCloseButtonEnabled
    # Since we can't directly test show(), we rely on the flags being set correctly

# Test setCloseButtonEnabled
def test_set_close_button_enabled(progress_dialog, qtbot):
    # Test enabling close button
    progress_dialog.setCloseButtonEnabled(True)
    assert progress_dialog.windowFlags() == (Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)

    # Test disabling close button
    progress_dialog.setCloseButtonEnabled(False)
    assert progress_dialog.windowFlags() == (Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)

# Test initial close button state
def test_initial_close_button_disabled(progress_dialog):
    assert progress_dialog.windowFlags() == (Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)
    progress_dialog.setCloseButtonEnabled(False)  # Explicitly set to disabled
    assert progress_dialog.windowFlags() == (Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)

# Test parent parameter
def test_parent_initialization(qapp):
    parent = QDialog()
    dialog = ProgressDialog(parent=parent)
    assert dialog.parent() == parent
    assert dialog.windowModality() == Qt.WindowModality.ApplicationModal
    dialog.close()
    parent.close()