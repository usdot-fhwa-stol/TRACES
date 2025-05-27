from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QTextEdit, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QSize
import os


class AboutDialog(QDialog):
    """Dialog for displaying about information or splash screen.

    Displays content from trajectory_tools.html and sizes itself to fit the content.
    """

    def __init__(self, parent=None, splash=False):
        """Initialize the about dialog or splash screen.

        Args:
            parent: Parent widget, typically the main window.
            splash: True to show as splash screen, False for about dialog.
        """
        super().__init__(parent)
        self.splash = splash
        self.setWindowTitle("About Trajectory Tools" if not splash else "")
        if splash:
            self.setWindowFlags(Qt.WindowType.SplashScreen)
        else:
            self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)
        self.setup_ui()

    def setup_ui(self):
        """Setup the dialog UI and size it to fit the HTML content."""
        layout = QVBoxLayout()
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        try:
            with open("trajectory_tools.html", 'r', encoding='utf-8') as f:
                self.text_area.setHtml(f.read())
        except FileNotFoundError:
            self.text_area.setText("About content not found")
        layout.addWidget(self.text_area)

        button_layout = QHBoxLayout()
        if self.splash:
            self.skip_button = QPushButton("Skip (3)")
            self.skip_button.clicked.connect(self.close)
            button_layout.addWidget(self.skip_button)
            self.timer = QTimer()
            self.timer.timeout.connect(self.update_timer)
            self.timer.start(1000)
            self.seconds = 3
        else:
            close_button = QPushButton("Close")
            close_button.clicked.connect(self.close)
            button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Size the dialog to fit the HTML content
        self.adjust_to_content()

        # Center the dialog in the parent window
        if self.parent():
            parent_geo = self.parent().geometry()
            self.move(parent_geo.center() - self.rect().center())

    def adjust_to_content(self):
        """Adjust the dialog size to fit the HTML content of the text area."""
        # Get the ideal size of the HTML content
        document = self.text_area.document()
        content_size = document.size()

        # Estimate additional space for layout margins and button area
        margins = self.layout().contentsMargins()
        button_height = 40  # Approximate height for button layout
        padding = margins.left() + margins.right(), margins.top() + margins.bottom() + button_height

        # Calculate desired dialog size
        desired_width = int(content_size.width()) + padding[0]
        desired_height = int(content_size.height()) + padding[1]

        # Constrain size to reasonable bounds
        max_width = 800  # Arbitrary max to prevent overly wide dialogs
        max_height = 600  # Arbitrary max to prevent overly tall dialogs
        if self.parent():
            parent_size = self.parent().size()
            max_width = min(max_width, parent_size.width() - 40)
            max_height = min(max_height, parent_size.height() - 40)

        desired_width = min(desired_width, max_width)
        desired_height = min(desired_height, max_height)

        # Set minimum size to ensure content is not clipped
        min_width = min(300, desired_width)  # Minimum width for usability
        min_height = min(200, desired_height)  # Minimum height for usability
        self.setMinimumSize(QSize(min_width, min_height))

        # Resize the dialog
        self.resize(desired_width, desired_height)

    def update_timer(self):
        """Update the splash screen timer."""
        self.seconds -= 1
        self.skip_button.setText(f"Skip ({self.seconds})")
        if self.seconds == 0:
            self.close()
