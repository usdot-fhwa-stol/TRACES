"""Floating banner over a pg.PlotWidget, anchored top-center.

Transparent to mouse events, so clicks under it still reach the plot.
Parent must be the PlotWidget itself (like ShockwaveResultPanel) so it
overlays the plot surface and can track the plot's size.
"""

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QLabel


class PlotOverlayLabel(QLabel):

    _MARGIN = 8

    def __init__(self, plot_widget):
        super().__init__(plot_widget)
        self._base_text = ""
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self._set(self._base_text))

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { background-color: rgba(255, 255, 224, 235); color: #000;"
            " border: 1px solid #999; border-radius: 4px; padding: 6px 12px; }")
        plot_widget.installEventFilter(self)
        self.hide()

    def show_message(self, html: str) -> None:
        """Set the persistent message."""
        self._flash_timer.stop()
        self._base_text = html
        self._set(html)

    def flash(self, html: str, msec: int = 1500) -> None:
        """Show *html* briefly, then revert to the persistent message."""
        self._set(html)
        self._flash_timer.start(msec)

    def dispose(self) -> None:
        self._flash_timer.stop()
        if self.parent() is not None:
            self.parent().removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _set(self, html: str) -> None:
        self.setText(html)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()

    def _reposition(self) -> None:
        p = self.parent()
        if p is not None:
            self.move(max((p.width() - self.width()) // 2, 0), self._MARGIN)