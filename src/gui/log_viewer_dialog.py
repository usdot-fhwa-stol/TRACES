import logging
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont
from utils import get_user_data_path

logger = logging.getLogger(__name__)


class LogViewerDialog(QDialog):
    """Modeless dialog that displays the application log file and updates live.

    The dialog polls the log file on a short interval and appends any new lines
    to the text area, so it stays current whether an import is running or not.
    Because both ImportDialog and LogViewerDialog read the same file on disk,
    they naturally show the same content without any inter-object coupling.

    The dialog is modeless (no setModal call) and carries only the standard
    OS window buttons (minimize, maximize, close).  Geometry is kept in memory
    for the lifetime of the application and is never written to the config file.

    Attributes:
        _log_path (Path):        Absolute path to trajectory_tools.log.
        _text_edit (QTextEdit):  Read-only scrolling text area.
        _poll_timer (QTimer):    Fires every 500 ms to check for new log lines.
        _last_size (int):        Byte offset of the last character read, used to
                                 detect new content without re-reading the whole file.
    """

    # Polling interval in milliseconds
    _POLL_INTERVAL_MS = 500

    def __init__(self, parent=None):
        """Initialise the LogViewerDialog.

        Args:
            parent: Parent widget (typically MainWindow).  Used only for
                    initial geometry calculations; the dialog is modeless.
        """
        super().__init__(parent, Qt.WindowType.Window)   # Window flag gives all three OS buttons
        self.setWindowTitle("System Log Viewer")

        self._log_path = get_user_data_path("trajectory_tools.log")
        self._last_size = 0
        self._poll_timer = QTimer(self)

        self._setup_ui()
        self._apply_initial_geometry()
        self._load_existing_content()
        self._start_polling()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build the single-widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._text_edit.setFont(QFont("Courier New", 9))
        layout.addWidget(self._text_edit)

    def _apply_initial_geometry(self) -> None:
        """Size to 50% of the parent width and height, anchored to the top-left
        corner of the parent's content area (below the menu bar)."""
        parent = self.parent()
        if parent is None:
            self.resize(600, 400)
            return

        pr = parent.geometry()
        w = max(300, pr.width() // 2)
        h = max(200, pr.height() // 2)

        # Anchor to the top-left of the parent content area.
        # menuBar().height() moves the dialog below the menu bar so it does
        # not overlap it on first open.
        menu_h = parent.menuBar().height() if hasattr(parent, "menuBar") else 0
        x = pr.x()
        y = pr.y() + menu_h
        self.setGeometry(x, y, w, h)

    # ── log reading ───────────────────────────────────────────────────────────

    def _load_existing_content(self) -> None:
        """Read whatever is already in the log file on first open."""
        if not self._log_path.exists():
            self._last_size = 0
            return

        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
            self._last_size = self._log_path.stat().st_size
            if text:
                self._text_edit.setPlainText(text)
                self._scroll_to_bottom()
        except OSError as e:
            logger.warning(f"LogViewerDialog: could not read log file on open: {e}")

    def _poll_log_file(self) -> None:
        """Append any new bytes written to the log file since the last poll.

        Called by _poll_timer every _POLL_INTERVAL_MS milliseconds.  Uses the
        stored byte offset so only the new tail is read, keeping the operation
        cheap even for large log files.
        """
        if not self._log_path.exists():
            return

        try:
            current_size = self._log_path.stat().st_size
        except OSError:
            return

        if current_size == self._last_size:
            return

        # File was truncated (e.g. ImportDialog cleared it for a new run)
        if current_size < self._last_size:
            self._last_size = 0
            self._text_edit.clear()

        try:
            with self._log_path.open("rb") as fh:
                fh.seek(self._last_size)
                new_bytes = fh.read()
            self._last_size += len(new_bytes)
            new_text = new_bytes.decode("utf-8", errors="replace")
            if new_text:
                # Move cursor to end and insert without disturbing scroll position
                # unless the user is already at the bottom
                sb = self._text_edit.verticalScrollBar()
                at_bottom = sb.value() >= sb.maximum() - 4
                self._text_edit.moveCursor(self._text_edit.textCursor().MoveOperation.End)
                self._text_edit.insertPlainText(new_text)
                if at_bottom:
                    self._scroll_to_bottom()
        except OSError as e:
            logger.warning(f"LogViewerDialog: poll read error: {e}")

    def _scroll_to_bottom(self) -> None:
        sb = self._text_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── timer lifecycle ───────────────────────────────────────────────────────

    def _start_polling(self) -> None:
        self._poll_timer.timeout.connect(self._poll_log_file)
        self._poll_timer.start(self._POLL_INTERVAL_MS)

    # ── dialog lifecycle ──────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Stop the poll timer before the dialog is destroyed."""
        self._poll_timer.stop()
        super().closeEvent(event)