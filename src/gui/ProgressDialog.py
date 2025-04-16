from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QProgressBar, QTextEdit)

class ProgressDialog(QDialog):
    def __init__(self, title="Progress", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(800, 600)  # Set dialog size to 800x600 pixels

        # Create layout
        layout = QVBoxLayout(self)

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Scrollable text area
        self.text_area = QTextEdit(self)
        self.text_area.setReadOnly(True)  # Make text area read-only
        layout.addWidget(self.text_area)

    def update_progress(self, percent):
        """Update the progress bar with the given percentage (0-100)."""
        self.progress_bar.setValue(max(0, min(100, percent)))

    def append_message(self, message):
        """Append a message to the text area and scroll to the bottom."""
        self.text_area.append(message)
        # Ensure the scroll bar moves to the bottom
        scroll_bar = self.text_area.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
