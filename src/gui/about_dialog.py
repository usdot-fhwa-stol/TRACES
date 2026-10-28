from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QFrame, QPushButton, QHBoxLayout
from PyQt6.QtCore import QTimer, pyqtSignal, Qt

from ui_kit.brand import APP_NAME, APP_VERSION, HEADING_COLOR, logo_pixmap

# Logo width in logical pixels; the logo is about as tall as it is wide.
_LOGO_WIDTH = 170


class AboutDialog(QDialog):
    """About dialog, also used as a splash screen.

    Shows the TRACES logo (lettering in brand.LOGO_LETTERING, readable on
    light and dark themes), the version, and short
    About / Features / Requirements sections. As a splash screen it shows
    a countdown on the Close button and closes itself after 5 seconds.

    Attributes:
        _is_splash (bool): Whether the dialog is being used as a splash screen.
        _countdown_timer (QTimer): Timer for splash screen countdown.
        _countdown_value (int): Current countdown value in seconds.
        _close_button (QPushButton): The close button widget.
        _logo (QLabel): Holds the logo, redrawn for the screen's pixel density.
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
        self._logo = None

        self._setup_ui()
        self._setup_positioning()

        if self._is_splash:
            self._setup_splash_mode()

    def _setup_ui(self):
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(420, 700)
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Logo (includes the TRACES lettering, so no separate title)
        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setStyleSheet("margin-top: 10px;")
        self._logo.setAccessibleName(APP_NAME)
        self._update_logo()
        layout.addWidget(self._logo)

        # Version
        version = QLabel(f"<b>Version {APP_VERSION} (beta)</b>")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        def add_section(heading, body):
            rule = QFrame()
            rule.setFrameShape(QFrame.Shape.HLine)
            rule.setStyleSheet("color: #cccccc;")
            layout.addWidget(rule)
            h = QLabel(heading)
            h.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {HEADING_COLOR}; margin-top: 6px;")
            layout.addWidget(h)
            b = QLabel(body)
            b.setWordWrap(True)
            b.setStyleSheet("line-height: 1.6;")
            layout.addWidget(b)

        add_section("About",
            f"{APP_NAME} is a PyQt6-based application for analyzing, correcting and "
            "visualizing vehicle trajectory data stored in SQLite databases.")

        add_section("Features",
            "• Import trajectory data from CSV\n"
            "• Time-space, speed and acceleration plots\n"
            "• Noise analyses: jerk, missing data, internal consistency\n"
            "• Corrections: spline fitting, gap interpolation, trajectory\n"
            "   stitching, lane-level anomalies\n"
            "• Export of data and analysis results")

        add_section("Requirements",
            "• Python 3.12\n"
            "• PyQt6, pyqtgraph, NumPy, SciPy\n"
            "• SQLite3")

        footer = QLabel("<i>For more information, please refer to the documentation.</i>")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # No fixed color: the theme's text color reads on light and dark themes.
        footer.setStyleSheet("margin-top: 20px;")
        layout.addWidget(footer)

        layout.addStretch()

        # Close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setMinimumWidth(80)
        button_layout.addWidget(self._close_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

    def _update_logo(self):
        """Draw the logo at the current screen's pixel density, so it stays
        sharp on high-DPI displays (called again once the dialog is shown
        and knows which screen it is on)."""
        if self._logo is not None:
            self._logo.setPixmap(logo_pixmap(_LOGO_WIDTH, self.devicePixelRatioF()))

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
        self.setWindowTitle(APP_NAME)

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
        """Handle show event to ensure proper positioning and a sharp logo."""
        super().showEvent(event)
        self._update_logo()
        if self.parent():
            self._setup_positioning()
