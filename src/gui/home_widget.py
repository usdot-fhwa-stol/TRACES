from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QColor, QPainter, QBrush, QLinearGradient, QCursor


class _GradientWidget(QWidget):
    """Full-bleed background widget that paints the 135° purple gradient."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor("#667eea"))
        grad.setColorAt(1.0, QColor("#764ba2"))
        painter.fillRect(self.rect(), QBrush(grad))


class _ActionCard(QFrame):
    """Clickable card widget matching the .action-card CSS style.

    Emits ``clicked`` when the user presses and releases inside the card.
    Reproduces the hover lift (shadow deepens, border turns #667eea) and
    the active/press depression using QPropertyAnimation on geometry.
    """

    clicked = pyqtSignal()

    _HOVER_SHADOW_BLUR  = 25
    _NORMAL_SHADOW_BLUR = 15

    def __init__(self, icon: str, title: str, description: str, parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setObjectName("actionCard")

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(self._NORMAL_SHADOW_BLUR)
        self._shadow.setOffset(0, 5)
        self._shadow.setColor(QColor(0, 0, 0, 40))
        self.setGraphicsEffect(self._shadow)

        self._normal_ss = (
            "QFrame#actionCard {"
            "  background: white;"
            "  border-radius: 10px;"
            "  border: 2px solid transparent;"
            "}"
        )
        self._hover_ss = (
            "QFrame#actionCard {"
            "  background: white;"
            "  border-radius: 10px;"
            "  border: 2px solid #667eea;"
            "}"
        )
        self.setStyleSheet(self._normal_ss)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 22px; border: none;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #2c3e50; border: none;"
        )
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(title_lbl, stretch=1)
        layout.addLayout(title_row)

        desc_lbl = QLabel(description)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 12px; color: #7f8c8d; border: none;")
        layout.addWidget(desc_lbl)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(80)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._pressed = False

    def enterEvent(self, event):
        self.setStyleSheet(self._hover_ss)
        self._shadow.setBlurRadius(self._HOVER_SHADOW_BLUR)
        self._shadow.setOffset(0, 10)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._normal_ss)
        self._shadow.setBlurRadius(self._NORMAL_SHADOW_BLUR)
        self._shadow.setOffset(0, 5)
        self._pressed = False
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            r = self.geometry()
            self._anim.stop()
            self._anim.setStartValue(r)
            self._anim.setEndValue(r.translated(0, 2))
            self._anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            r = self.geometry()
            self._anim.stop()
            self._anim.setStartValue(r)
            self._anim.setEndValue(r.translated(0, -2))
            self._anim.start()
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _RecentFileItem(QFrame):
    """Single row in the recent-files list matching the .recent-files li style."""

    clicked = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setObjectName("recentItem")
        self._normal_ss = (
            "QFrame#recentItem { background: #f8f9fa; border-radius: 8px; border: none; }"
        )
        self._hover_ss = (
            "QFrame#recentItem { background: #e9ecef; border-radius: 8px; border: none; }"
        )
        self.setStyleSheet(self._normal_ss)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(3)

        name_lbl = QLabel(Path(file_path).name)
        name_lbl.setStyleSheet(
            "font-size: 13px; font-weight: 500; color: #667eea; border: none;"
        )
        layout.addWidget(name_lbl)

        path_lbl = QLabel(file_path)
        path_lbl.setStyleSheet("font-size: 11px; color: #6c757d; border: none;")
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)

    def enterEvent(self, event):
        self.setStyleSheet(self._hover_ss)
        r = self.geometry()
        self._anim.stop()
        self._anim.setStartValue(r)
        self._anim.setEndValue(QRect(r.x() + 5, r.y(), r.width() - 5, r.height()))
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._normal_ss)
        r = self.geometry()
        self._anim.stop()
        self._anim.setStartValue(r)
        self._anim.setEndValue(QRect(r.x() - 5, r.y(), r.width() + 5, r.height()))
        self._anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._file_path)
        super().mousePressEvent(event)


class HomeWidget(QWidget):
    """Pure-PyQt6 home screen replacing the QWebEngineView / home.html pair.

    Matches the visual design of home.html:
      - 135° linear gradient background (#667eea → #764ba2)
      - Centred white container (60% of available width) with rounded corners
        and drop shadow; height shrinks to fit content
      - Two action cards (Open Trajectories, Import Data) in a row
      - Recent-files section that grows as items are added

    Signals:
        open_file_requested:    user clicked "Open Trajectories"
        import_file_requested:  user clicked "Import Data"
        recent_file_requested:  user clicked a recent file; str = absolute path
    """

    open_file_requested   = pyqtSignal()
    import_file_requested = pyqtSignal()
    recent_file_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recent_items: list[_RecentFileItem] = []
        self._recent_layout: QVBoxLayout | None = None
        self._empty_label: QLabel | None = None
        self._container: QFrame | None = None
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def refresh(self, recent_files: list[str]) -> None:
        """Rebuild the recent-files section from *recent_files*.

        Called by MainWindow whenever the list changes.
        """
        for item in self._recent_items:
            self._recent_layout.removeWidget(item)
            item.deleteLater()
        self._recent_items.clear()

        if self._empty_label:
            self._recent_layout.removeWidget(self._empty_label)
            self._empty_label.deleteLater()
            self._empty_label = None

        if not recent_files:
            self._empty_label = QLabel("No recent files")
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setStyleSheet(
                "color: #6c757d; font-style: italic; padding: 20px; border: none;"
            )
            self._recent_layout.addWidget(self._empty_label)
            return

        for fp in recent_files:
            item = _RecentFileItem(fp)
            item.clicked.connect(self.recent_file_requested)
            self._recent_layout.addWidget(item)
            self._recent_items.append(item)

    def resizeEvent(self, event):
        """Keep the container at 60% of the available width."""
        super().resizeEvent(event)
        if self._container:
            self._container.setFixedWidth(max(300, int(self.width() * 0.6)))

    # ── internal build ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        # Outermost layer: gradient background fills the whole widget
        bg = _GradientWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(bg)

        # Padding layer inside the gradient
        pad = QVBoxLayout(bg)
        pad.setContentsMargins(30, 30, 30, 30)
        pad.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        # ── white container card ──────────────────────────────────────────────
        self._container = QFrame()
        self._container.setObjectName("container")
        self._container.setStyleSheet(
            "QFrame#container {"
            "  background: rgba(255, 255, 255, 242);"
            "  border-radius: 15px;"
            "  border: none;"
            "}"
        )
        # Fixed height is determined by content; only width is managed externally
        self._container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        container_shadow = QGraphicsDropShadowEffect(self._container)
        container_shadow.setBlurRadius(40)
        container_shadow.setOffset(0, 20)
        container_shadow.setColor(QColor(0, 0, 0, 26))
        self._container.setGraphicsEffect(container_shadow)

        pad.addWidget(self._container)

        # ── inner content layout (no scroll area — container hugs content) ────
        inner_layout = QVBoxLayout(self._container)
        inner_layout.setContentsMargins(40, 40, 40, 40)
        inner_layout.setSpacing(0)

        # ── action cards row ──────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(20)

        open_card = _ActionCard(
            icon="📂",
            title="Open Trajectories",
            description="Open previously imported trajectory data",
        )
        open_card.clicked.connect(self.open_file_requested)

        import_card = _ActionCard(
            icon="📥",
            title="Import Data",
            description="Import trajectory data. Supported file formats: CSV",
        )
        import_card.clicked.connect(self.import_file_requested)

        cards_row.addWidget(open_card)
        cards_row.addWidget(import_card)
        inner_layout.addLayout(cards_row)

        inner_layout.addSpacing(40)

        # ── recent files section ──────────────────────────────────────────────
        recent_card = QFrame()
        recent_card.setObjectName("recentCard")
        recent_card.setStyleSheet(
            "QFrame#recentCard {"
            "  background: white;"
            "  border-radius: 10px;"
            "  border: none;"
            "}"
        )
        recent_shadow = QGraphicsDropShadowEffect(recent_card)
        recent_shadow.setBlurRadius(15)
        recent_shadow.setOffset(0, 5)
        recent_shadow.setColor(QColor(0, 0, 0, 26))
        recent_card.setGraphicsEffect(recent_shadow)

        recent_outer = QVBoxLayout(recent_card)
        recent_outer.setContentsMargins(25, 25, 25, 25)
        recent_outer.setSpacing(15)

        recent_heading = QLabel("📈  Recent Trajectories")
        recent_heading.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #2c3e50; border: none;"
        )
        recent_outer.addWidget(recent_heading)

        self._recent_layout = QVBoxLayout()
        self._recent_layout.setSpacing(10)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_outer.addLayout(self._recent_layout)

        inner_layout.addWidget(recent_card)

        # Seed with empty state
        self.refresh([])