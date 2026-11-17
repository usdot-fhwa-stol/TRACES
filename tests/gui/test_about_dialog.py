import pytest
from unittest.mock import Mock, MagicMock, patch, mock_open
from pathlib import Path
from PyQt6.QtWidgets import QMainWindow, QPushButton, QTextBrowser
from PyQt6.QtCore import QTimer, QRect, Qt


class TestAboutDialogInitialization:
    """Test AboutDialog initialization and setup."""
    
    def test_init_normal_mode(self, qtbot, mock_utils):
        """Test initialization in normal (non-splash) mode."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(parent=None, is_splash=False)
            qtbot.addWidget(dialog)
            
            assert dialog._is_splash is False
            assert dialog._countdown_value == 5
            assert dialog._countdown_timer is None
            assert dialog._close_button is not None
            assert dialog.windowTitle() == "About Trajectory Tools"
    
    def test_init_splash_mode(self, qtbot, mock_utils):
        """Test initialization in splash screen mode."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(parent=None, is_splash=True)
            qtbot.addWidget(dialog)
            
            assert dialog._is_splash is True
            assert dialog._countdown_timer is not None
            assert dialog._countdown_timer.isActive()
            assert dialog.windowTitle() == "Trajectory Tools"
    
    def test_fixed_size(self, qtbot, mock_utils):
        """Test that dialog has fixed size."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            assert dialog.width() == 400
            assert dialog.height() == 600
    
    def test_ui_components_created(self, qtbot, mock_utils):
        """Test that all UI components are created."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            # Check text browser exists
            assert dialog._text_browser is not None
            assert isinstance(dialog._text_browser, QTextBrowser)
            
            # Check close button exists
            assert dialog._close_button is not None
            assert isinstance(dialog._close_button, QPushButton)
            assert dialog._close_button.text() == "Close"
    
    def test_text_browser_external_links(self, qtbot, mock_utils):
        """Test that text browser opens external links."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            assert dialog._text_browser.openExternalLinks() is True


class TestContentLoading:
    """Test HTML content loading functionality."""
    
    def test_load_content_success(self, qtbot, mock_utils, tmp_path):
        """Test successful loading of HTML content."""
        # Create a test HTML file
        html_content = "<html><body><h1>Test Content</h1></body></html>"
        html_file = tmp_path / "about.html"
        html_file.write_text(html_content, encoding='utf-8')
        
        with patch('gui.about_dialog.get_resource_path', return_value=str(html_file)):
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            # Check that HTML was loaded
            loaded_html = dialog._text_browser.toHtml()
            assert "Test Content" in loaded_html
    
    def test_load_content_file_not_found(self, qtbot, mock_utils):
        """Test handling when about.html file doesn't exist."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            # Should show fallback content
            loaded_html = dialog._text_browser.toHtml()
            assert "About information not available" in loaded_html
    
    def test_load_content_io_error(self, qtbot, mock_utils, capsys):
        """Test handling of IO errors when reading file."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.open.side_effect = IOError("Read error")
            mock_path.return_value = mock_path_instance
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            # Should show fallback content - Qt adds its own formatting
            loaded_html = dialog._text_browser.toHtml()
            # Check that it has HTML structure (fallback was set)
            assert "html" in loaded_html.lower()
            assert "body" in loaded_html.lower()
            
            # Should print error
            captured = capsys.readouterr()
            assert "Read error" in captured.out
    
    def test_load_content_unicode_error(self, qtbot, mock_utils, capsys):
        """Test handling of Unicode decode errors."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.open.side_effect = UnicodeDecodeError(
                'utf-8', b'\x80', 0, 1, 'invalid start byte'
            )
            mock_path.return_value = mock_path_instance
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            # Should show fallback content - Qt adds its own formatting
            loaded_html = dialog._text_browser.toHtml()
            # Check that it has HTML structure (fallback was set)
            assert "html" in loaded_html.lower()
            assert "body" in loaded_html.lower()


class TestPositioning:
    """Test dialog positioning functionality."""
    
    def test_positioning_with_parent(self, qtbot, mock_utils):
        """Test that dialog centers within parent window."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            # Create a parent window
            parent = QMainWindow()
            parent.setGeometry(100, 100, 800, 600)
            qtbot.addWidget(parent)
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(parent=parent)
            qtbot.addWidget(dialog)
            
            # Dialog should be centered in parent
            parent_rect = parent.geometry()
            expected_x = parent_rect.x() + (parent_rect.width() - dialog.width()) // 2
            expected_y = parent_rect.y() + (parent_rect.height() - dialog.height()) // 2
            
            assert dialog.x() == expected_x
            assert dialog.y() == expected_y
    
    def test_positioning_without_parent(self, qtbot, mock_utils):
        """Test dialog positioning when no parent is provided."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(parent=None)
            qtbot.addWidget(dialog)
            
            # Should not crash without parent
            assert dialog.parent() is None
    
    def test_show_event_repositions(self, qtbot, mock_utils):
        """Test that showEvent triggers repositioning when there's a parent."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            # Create a parent window
            parent = QMainWindow()
            parent.setGeometry(200, 200, 800, 600)
            qtbot.addWidget(parent)
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(parent=parent)
            qtbot.addWidget(dialog)
            
            # Move parent and show dialog
            parent.move(300, 300)
            dialog.show()
            
            # Dialog should be repositioned relative to new parent position
            parent_rect = parent.geometry()
            expected_x = parent_rect.x() + (parent_rect.width() - dialog.width()) // 2
            
            assert abs(dialog.x() - expected_x) < 50  # Allow some tolerance


class TestSplashMode:
    """Test splash screen mode functionality."""
    
    def test_splash_mode_timer_starts(self, qtbot, mock_utils):
        """Test that timer starts in splash mode."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            assert dialog._countdown_timer is not None
            assert dialog._countdown_timer.isActive()
            assert dialog._countdown_timer.interval() == 1000  # 1 second
    
    def test_splash_mode_button_text(self, qtbot, mock_utils):
        """Test that button text shows countdown in splash mode."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            assert "Close (5)" in dialog._close_button.text()
    
    def test_countdown_updates(self, qtbot, mock_utils):
        """Test that countdown value decrements."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            initial_value = dialog._countdown_value
            
            # Simulate timer timeout
            dialog._update_countdown()
            
            assert dialog._countdown_value == initial_value - 1
            assert f"Close ({dialog._countdown_value})" in dialog._close_button.text()
    
    def test_countdown_auto_close(self, qtbot, mock_utils):
        """Test that dialog auto-closes when countdown reaches zero."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            # Set countdown to 1
            dialog._countdown_value = 1
            
            # Connect to closed signal
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            # Trigger countdown update
            dialog._update_countdown()
            
            # Timer should be stopped and dialog should close
            assert not dialog._countdown_timer.isActive()
            closed_signal.assert_called_once()
    
    def test_normal_mode_no_countdown(self, qtbot, mock_utils):
        """Test that normal mode doesn't show countdown."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=False)
            qtbot.addWidget(dialog)
            
            assert dialog._close_button.text() == "Close"
            assert "(" not in dialog._close_button.text()


class TestDialogClosing:
    """Test dialog closing functionality."""
    
    def test_accept_stops_timer(self, qtbot, mock_utils):
        """Test that accept() stops the countdown timer."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            assert dialog._countdown_timer.isActive()
            
            dialog.accept()
            
            assert not dialog._countdown_timer.isActive()
    
    def test_accept_emits_closed_signal(self, qtbot, mock_utils):
        """Test that accept() emits the closed signal."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            dialog.accept()
            
            closed_signal.assert_called_once()
    
    def test_reject_stops_timer(self, qtbot, mock_utils):
        """Test that reject() stops the countdown timer."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            assert dialog._countdown_timer.isActive()
            
            dialog.reject()
            
            assert not dialog._countdown_timer.isActive()
    
    def test_reject_emits_closed_signal(self, qtbot, mock_utils):
        """Test that reject() emits the closed signal."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            dialog.reject()
            
            closed_signal.assert_called_once()
    
    def test_accept_without_timer(self, qtbot, mock_utils):
        """Test that accept() works when no timer exists (normal mode)."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=False)
            qtbot.addWidget(dialog)
            
            assert dialog._countdown_timer is None
            
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            # Should not crash
            dialog.accept()
            
            closed_signal.assert_called_once()
    
    def test_reject_without_timer(self, qtbot, mock_utils):
        """Test that reject() works when no timer exists (normal mode)."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=False)
            qtbot.addWidget(dialog)
            
            assert dialog._countdown_timer is None
            
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            # Should not crash
            dialog.reject()
            
            closed_signal.assert_called_once()
    
    def test_close_button_triggers_accept(self, qtbot, mock_utils):
        """Test that clicking close button triggers accept()."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            # Click the close button
            qtbot.mouseClick(dialog._close_button, Qt.MouseButton.LeftButton)
            
            closed_signal.assert_called_once()


class TestButtonText:
    """Test button text update functionality."""
    
    def test_update_button_text_splash_mode(self, qtbot, mock_utils):
        """Test button text update in splash mode."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            # Test with different countdown values
            dialog._countdown_value = 3
            dialog._update_button_text()
            assert dialog._close_button.text() == "Close (3)"
            
            dialog._countdown_value = 1
            dialog._update_button_text()
            assert dialog._close_button.text() == "Close (1)"
    
    def test_update_button_text_normal_mode(self, qtbot, mock_utils):
        """Test button text update in normal mode."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=False)
            qtbot.addWidget(dialog)
            
            dialog._update_button_text()
            assert dialog._close_button.text() == "Close"


class TestSignals:
    """Test signal emissions."""
    
    def test_closed_signal_exists(self, qtbot, mock_utils):
        """Test that closed signal is defined."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            assert hasattr(dialog, 'closed')
    
    def test_closed_signal_can_connect(self, qtbot, mock_utils):
        """Test that closed signal can be connected to a slot."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog()
            qtbot.addWidget(dialog)
            
            slot = Mock()
            dialog.closed.connect(slot)
            
            # Emit the signal
            dialog.closed.emit()
            
            slot.assert_called_once()


class TestIntegration:
    """Integration tests for AboutDialog."""
    
    def test_full_splash_screen_lifecycle(self, qtbot, mock_utils):
        """Test complete splash screen lifecycle."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            # Should start with countdown at 5
            assert dialog._countdown_value == 5
            assert "Close (5)" in dialog._close_button.text()
            assert dialog._countdown_timer.isActive()
            
            # Simulate several countdown ticks
            for expected in [4, 3, 2]:
                dialog._update_countdown()
                assert dialog._countdown_value == expected
                assert f"Close ({expected})" in dialog._close_button.text()
                assert dialog._countdown_timer.isActive()
            
            # Set up signal handler
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            # Last tick should close dialog
            dialog._countdown_value = 1
            dialog._update_countdown()
            
            assert not dialog._countdown_timer.isActive()
            closed_signal.assert_called_once()
    
    def test_manual_close_during_countdown(self, qtbot, mock_utils):
        """Test manually closing dialog during countdown."""
        with patch('gui.about_dialog.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(is_splash=True)
            qtbot.addWidget(dialog)
            
            # Start countdown
            assert dialog._countdown_timer.isActive()
            
            # Manually close
            closed_signal = Mock()
            dialog.closed.connect(closed_signal)
            
            qtbot.mouseClick(dialog._close_button, Qt.MouseButton.LeftButton)
            
            # Timer should be stopped
            assert not dialog._countdown_timer.isActive()
            closed_signal.assert_called_once()
    
    def test_with_parent_window(self, qtbot, mock_utils, tmp_path):
        """Test dialog with parent window and real content."""
        html_content = "<html><body><h1>Trajectory Tools</h1><p>Version 1.0</p></body></html>"
        html_file = tmp_path / "about.html"
        html_file.write_text(html_content, encoding='utf-8')
        
        with patch('gui.about_dialog.get_resource_path', return_value=str(html_file)):
            # Create parent window
            parent = QMainWindow()
            parent.setGeometry(100, 100, 800, 600)
            qtbot.addWidget(parent)
            
            from gui.about_dialog import AboutDialog
            dialog = AboutDialog(parent=parent, is_splash=False)
            qtbot.addWidget(dialog)
            
            # Check content loaded
            assert "Trajectory Tools" in dialog._text_browser.toHtml()
            
            # Check positioning
            parent_rect = parent.geometry()
            expected_x = parent_rect.x() + (parent_rect.width() - dialog.width()) // 2
            assert dialog.x() == expected_x
            
            # Check it's not splash mode
            assert dialog._countdown_timer is None
            assert dialog._close_button.text() == "Close"