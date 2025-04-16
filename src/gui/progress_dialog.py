from PyQt6.QtWidgets import QDialog, QProgressBar, QTextEdit, QVBoxLayout
from PyQt6.QtCore import Qt

class ProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Progress")
        self.setFixedSize(640, 400)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.init_ui()
        self.setCloseButtonEnabled(False)

    def init_ui(self):
        layout = QVBoxLayout()

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        layout.addWidget(self.progress_bar)

        # Text area
        self.text_area = QTextEdit(self)
        self.text_area.setReadOnly(True)
        layout.addWidget(self.text_area)

        self.setLayout(layout)

    def update_progress(self, value):
        self.progress_bar.setValue(int(value))

    def append_message(self, message):
        self.text_area.append(message)
        scrollbar = self.text_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def enable_close_button(self):
        self.setCloseButtonEnabled(True)

    def setCloseButtonEnabled(self, enabled):
        if enabled:
            self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        else:
            self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint)
        self.show()  # Update window flags
