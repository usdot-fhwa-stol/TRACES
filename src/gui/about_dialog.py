from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout
from PyQt6.QtCore import QTimer, pyqtSignal
from utils import get_resource_path

class AboutDialog(QDialog):
    """About dialog that displays HTML content and can function as a splash screen.
    
    The dialog reads content from about.html and displays it in a text browser.
    When used as a splash screen, it shows a countdown timer and auto-closes after 5 seconds.
    
    Attributes:
        _is_splash (bool): Whether the dialog is being used as a splash screen.
        _countdown_timer (QTimer): Timer for splash screen countdown.
        _countdown_value (int): Current countdown value in seconds.
        _close_button (QPushButton): The close button widget.
    """
    
    # Signal emitted when dialog is closed (useful for splash screen mode)
    closed = pyqtSignal()
    
    def __init__(self, parent=None, is_splash=False):
        """Initialize the AboutDialog.
        
        Args:
            parent: Parent widget (typically MainWindow).
            is_splash (bool): Whether to show as splash screen with countdown.
        """
        super().__init__(parent)
        self._is_splash = is_splash
        self._countdown_timer = None
        self._countdown_value = 5
        self._close_button = None
        
        self._setup_ui()
        self._load_content()
        self._setup_positioning()
        
        if self._is_splash:
            self._setup_splash_mode()
    
    def _setup_ui(self):
        """Set up the dialog UI components."""
        self.setWindowTitle("About Trajectory Tools")
        self.setFixedSize(400, 600)
        
        # Create main layout
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Create text browser for HTML content
        self._text_browser = QTextBrowser()
        self._text_browser.setOpenExternalLinks(True)
        layout.addWidget(self._text_browser)
        
        # Create button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # Create close button
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setMinimumWidth(80)
        button_layout.addWidget(self._close_button)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
    
    def _load_content(self):
        """Load HTML content from about.html file."""
        # Use get_resource_path to handle both development and PyInstaller environments
        html_path = Path(get_resource_path("about.html"))
        
        if html_path.exists():
            try:
                with html_path.open('r', encoding='utf-8') as f:
                    content = f.read()
                self._text_browser.setHtml(content)
            except (IOError, UnicodeDecodeError) as e:
                # Fallback content if file can't be read
                print(e)
                self._text_browser.setHtml("<html><body></body></html>")
        else:
            # Fallback content if file doesn't exist
            self._text_browser.setHtml("<html><body><h2>Trajectory Tools</h2><p>About information not available.</p></body></html>")
    
    def _setup_positioning(self):
        """Position the dialog centered within the parent window."""
        if self.parent():
            parent_rect = self.parent().geometry()
            dialog_width = self.width()
            dialog_height = self.height()
            
            # Center within parent window
            x = parent_rect.x() + (parent_rect.width() - dialog_width) // 2
            y = parent_rect.y() + (parent_rect.height() - dialog_height) // 2
            
            self.move(x, y)
    
    def _setup_splash_mode(self):
        """Set up splash screen mode with countdown timer."""
        self.setWindowTitle("Trajectory Tools")
        
        # Create and configure countdown timer
        self._countdown_timer = QTimer()
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start(1000)  # Update every second
        
        # Update button text with initial countdown
        self._update_button_text()
    
    def _update_countdown(self):
        """Update the countdown timer and button text."""
        self._countdown_value -= 1
        
        if self._countdown_value <= 0:
            # Timer expired, close dialog
            self._countdown_timer.stop()
            self.accept()
        else:
            # Update button text
            self._update_button_text()
    
    def _update_button_text(self):
        """Update the close button text with countdown."""
        if self._is_splash:
            self._close_button.setText(f"Close ({self._countdown_value})")
        else:
            self._close_button.setText("Close")
    
    def accept(self):
        """Handle dialog acceptance (close)."""
        if self._countdown_timer:
            self._countdown_timer.stop()
        self.closed.emit()
        super().accept()
    
    def reject(self):
        """Handle dialog rejection (close via X button)."""
        if self._countdown_timer:
            self._countdown_timer.stop()
        self.closed.emit()
        super().reject()
    
    def showEvent(self, event):
        """Handle show event to ensure proper positioning."""
        super().showEvent(event)
        if self.parent():
            self._setup_positioning()