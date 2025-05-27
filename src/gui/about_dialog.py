from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QTextEdit, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer


class AboutDialog(QDialog):
    """Dialog for displaying about information or splash screen.

    Displays content from trajectory_tools.html.
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
        """Setup the dialog UI."""
        layout = QVBoxLayout()
        text_area = QTextEdit()
        text_area.setReadOnly(True)
        try:
            with open("trajectory_tools.html", 'r', encoding='utf-8') as f:
                text_area.setHtml(f.read())
        except FileNotFoundError:
            text_area.setText("About content not found")
        layout.addWidget(text_area)

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

    def update_timer(self):
        """Update the splash screen timer."""
        self.seconds -= 1
        self.skip_button.setText(f"Skip ({self.seconds})")
        if self.seconds == 0:
            self.close()