import logging
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QCursor

logger = logging.getLogger(__name__)


class _ClickableTitleLabel(QLabel):
    """QLabel that reports left-clicks via a callback.

    QLabel has no built-in clicked signal; overriding mousePressEvent here
    is simpler than installing an event filter since the panel has exactly
    one clickable widget.
    """

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip("Click to view all shockwaves")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


class ShockwaveResultPanel(QFrame):
    """Floating result panel anchored to the top-right of the scatter plot.

    Displays the shockwave ID, equation, speed, and R² for the most
    recently fitted shockwave. The title label ("Shockwave N") is
    clickable and opens the full results table dialog (on-demand view,
    independent of the save-prompt path triggered on mode exit).

    Parent must be the scatter plot widget (pg.PlotWidget) — not
    TimeSpacePlotWidget — so the panel overlays the plot surface and
    _reposition() can measure the plot's own dimensions.  This matches
    the pattern used by the vehicle tooltip label.

    The panel is hidden on construction and on clear().  It is shown and
    repositioned by update_result() after each successful fit.  It should
    not be visible outside Shockwave mode; ShockwaveModeHandler.on_exit
    calls clear() then deleteLater() to ensure clean teardown.
    """

    _MARGIN = 10   # pixels from plot edge

    def __init__(self, parent, on_title_clicked=None):
        """
        Args:
            parent: pg.PlotWidget (the scatter plot) — not TimeSpacePlotWidget.
            on_title_clicked: optional callable invoked when the title label
                is left-clicked. Typically opens the results table dialog.
                Can also be set later via set_on_title_clicked().
        """
        super().__init__(parent)
        self._on_title_clicked = on_title_clicked
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        # Scope the background/border to QFrame only so child QLabels
        # are not affected.  Set explicit color on QLabel to guarantee
        # visibility regardless of the application palette.
        self.setStyleSheet(
            "ShockwaveResultPanel {"
            "  background-color: rgba(255, 255, 255, 220);"
            "  border: 1px solid #999;"
            "  border-radius: 4px;"
            "}"
            "ShockwaveResultPanel QLabel {"
            "  background-color: transparent;"
            "  border: none;"
            "  color: #000000;"
            "}"
        )
        self._setup_ui()
        self.hide()

    # ── Layout ────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(10)
        bold.setUnderline(True)

        small = QFont()
        small.setPointSize(9)

        # Title doubles as the on-demand "view all shockwaves" control.
        # Underline (via font, not stylesheet) + pointing-hand cursor +
        # tooltip are all visible/discoverable without requiring the user
        # to already be hovering to learn the label is clickable.
        self._title_label = _ClickableTitleLabel(self._handle_title_clicked)
        self._title_label.setFont(bold)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._title_label)

        self._eq_label = QLabel()
        self._eq_label.setFont(small)
        self._eq_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._eq_label)

        self._speed_label = QLabel()
        self._speed_label.setFont(small)
        self._speed_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._speed_label)

        self._r2_label = QLabel()
        self._r2_label.setFont(small)
        self._r2_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._r2_label)

    # ── Click handling ───────────────────────────────────────────────────

    def set_on_title_clicked(self, callback) -> None:
        """Set or replace the callback invoked when the title is clicked."""
        self._on_title_clicked = callback

    def _handle_title_clicked(self) -> None:
        if self._on_title_clicked is not None:
            logger.debug("Result panel title clicked — opening results table")
            self._on_title_clicked()

    # ── Public API ────────────────────────────────────────────────────────

    def update_result(self, result) -> None:
        """Populate labels from result, reposition, and show the panel.

        Args:
            result: ShockwaveResult
        """
        self._title_label.setText(f"Shockwave {result.id}")
        self._eq_label.setText(result.equation)
        self._speed_label.setText(f"Speed: {result.slope:.4f} dist/time")
        self._r2_label.setText(f"R\u00b2 = {result.r_squared:.4f}")
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        logger.debug(f"Result panel shown for shockwave {result.id}")

    def enter_edit_mode(self, shockwave_id: int) -> None:
        """Show the panel with the id preserved but the fit fields blank.

        Called when a committed shockwave is re-opened for editing
        (Ctrl+Left-click on it) -- there's no fit yet for whatever the
        active chain becomes next, so equation/speed/R² have nothing
        meaningful to display until the chain is re-committed (Enter)
        or the edit is reverted (Escape), at which point update_result()
        takes over again with real values.
        """
        self._title_label.setText(f"Editing Shockwave {shockwave_id}")
        self._eq_label.setText("")
        self._speed_label.setText("")
        self._r2_label.setText("")
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        logger.debug(f"Result panel in edit mode for shockwave {shockwave_id}")

    def clear(self) -> None:
        """Hide the panel.  Call deleteLater() separately for full teardown."""
        self.hide()
        logger.debug("Result panel hidden")

    # ── Positioning ───────────────────────────────────────────────────────

    def _reposition(self) -> None:
        """Anchor the panel to the top-right corner of the parent plot."""
        parent = self.parent()
        if parent is None:
            return
        x = parent.width() - self.width() - self._MARGIN
        y = self._MARGIN
        self.move(max(0, x), y)

    def resizeEvent(self, event):
        """Keep the panel anchored when the plot is resized."""
        super().resizeEvent(event)
        if self.isVisible():
            self._reposition()