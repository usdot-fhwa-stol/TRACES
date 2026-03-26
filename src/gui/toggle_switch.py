from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QPainter, QColor

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 28)
        self._checked = False
        self._position = 2
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(120)

    def isChecked(self) -> bool:
        """Return the current checked state."""
        return self._checked

    def setChecked(self, checked: bool) -> None:
        """Programmatically set the checked state and emit toggled."""
        if checked == self._checked:
            return
        self._checked = checked
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(self.width() - 26 if self._checked else 2)
        self._animation.start()
        self.toggled.emit(self._checked)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Background
        painter.setBrush(QColor("#00bfff") if self._checked else QColor("#ccc"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 14, 14)
        # Circle
        painter.setBrush(QColor("#fff"))
        painter.drawEllipse(self._position, 2, 24, 24)

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(self.width() - 26 if self._checked else 2)
        self._animation.start()
        self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    @pyqtProperty(int)
    def position(self):
        return self._position

    @position.setter
    def position(self, pos):
        self._position = pos
        self.update()