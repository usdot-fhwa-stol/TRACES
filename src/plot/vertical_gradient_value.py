from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QLinearGradient, QColor, QFontMetrics, QPalette
from PyQt6.QtWidgets import QWidget, QApplication, QToolTip
import sys

class GradientConfig:
    """
    Configuration object for VerticalGradientValue.

    ranges: list of lists, each in the form [vmin, vmax, pmin, pmax, step?]
            - vmin, vmax: numeric value range (inclusive)
            - pmin, pmax: percent of gradient height from bottom (0-100)
            - step: optional step size (default 1)
    title: string title
    """
    def __init__(self, title: str = "", ranges=None):
        self.title = title
        self.ranges = ranges or [[0, 100, 0, 100, 10]]


class VerticalGradientValue(QWidget):
    def __init__(self, config: GradientConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setMouseTracking(True)
        self._cached_positions = []  # (value, y)

    # -------- Public API --------
    def value_to_color_hex(self, value: float) -> str:
        """Return interpolated color hex (#RRGGBB) for a numeric value."""
        pos = self._value_to_percent(value) / 100.0
        color = self._interpolated_color(pos)
        return color.name().upper()

    # -------- Internal helpers --------
    def _value_to_percent(self, value: float) -> float:
        for r in self.config.ranges:
            vmin, vmax, pmin, pmax, *rest = r
            if vmin <= value <= vmax:
                if vmax == vmin:
                    return pmin
                t = (value - vmin) / (vmax - vmin)
                return pmin + t * (pmax - pmin)
        # Clamp outside
        if value < self.config.ranges[0][0]:
            return self.config.ranges[0][2]
        return self.config.ranges[-1][3]

    def _interpolated_color(self, pos: float) -> QColor:
        """pos in [0,1], bottom=0 red -> top=1 blue via rainbow."""
        stops = [
            (0.0, QColor(255, 0, 0)),      # red
            (0.2, QColor(255, 165, 0)),    # orange
            (0.4, QColor(255, 255, 0)),    # yellow
            (0.6, QColor(0, 255, 0)),      # green
            (0.8, QColor(0, 255, 255)),    # cyan
            (1.0, QColor(0, 0, 255)),      # blue
        ]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= pos <= p1:
                t = (pos - p0) / (p1 - p0) if p1 != p0 else 0
                r = int(c0.red() + t * (c1.red() - c0.red()))
                g = int(c0.green() + t * (c1.green() - c0.green()))
                b = int(c0.blue() + t * (c1.blue() - c0.blue()))
                return QColor(r, g, b)
        return stops[-1][1]

    def _is_dark_theme(self) -> bool:
        pal = self.palette()
        bg = pal.color(QPalette.ColorRole.Window)
        # perceived luminance
        lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        return lum < 128

    # -------- Layout + Painting --------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        fm = QFontMetrics(self.font())
        title_h = fm.height()

        # Build value list and max width
        values = []
        for r in self.config.ranges:
            vmin, vmax, pmin, pmax, *rest = r
            step = rest[0] if rest else 1
            v = vmin
            while v <= vmax + 1e-9:
                values.append(v)
                v += step
        # Remove duplicates while preserving order
        seen = set()
        values = [v for v in values if not (v in seen or seen.add(v))]

        max_w = max((fm.horizontalAdvance(str(v)) for v in values), default=10)

        gradient_w = max_w
        content_top = title_h + 6

        pad = fm.height() * 0.6   # vertical padding above and below gradient
        
        content_top = title_h + 6
        
        gradient_h = self.height() - content_top - 6 - (2 * pad)
        
        gradient_x = (self.width() - (gradient_w + 6 + max_w)) / 2
        
        gradient_rect = QRectF(
            gradient_x,
            content_top + pad,
            gradient_w,
            gradient_h
        )
        
        # Draw title
        painter.drawText(QRectF(0, 0, self.width(), title_h), Qt.AlignmentFlag.AlignCenter, self.config.title)

        # Gradient
        grad = QLinearGradient(gradient_rect.topLeft(), gradient_rect.bottomLeft())
        grad.setColorAt(1.0, QColor(0, 0, 0))
        grad.setColorAt(0.8, QColor(255, 0, 0))
        grad.setColorAt(0.6, QColor(255, 255, 0))
        grad.setColorAt(0.4, QColor(0, 255, 0))
        grad.setColorAt(0.2, QColor(0, 255, 255))
        grad.setColorAt(0.0, QColor(0, 0, 255))
        painter.fillRect(gradient_rect, grad)

        # Text color based on theme
        text_color = QColor(255, 255, 255) if self._is_dark_theme() else QColor(0, 0, 0)
        painter.setPen(text_color)

        # Draw numeric labels
        self._cached_positions.clear()
        for r in self.config.ranges:
            vmin, vmax, pmin, pmax, *rest = r
            step = rest[0] if rest else 1
            v = vmin
            while v <= vmax + 1e-9:
                percent = self._value_to_percent(v) / 100.0
                # y_center is the vertical center where the label should be centered
                y_center = gradient_rect.bottom() - percent * gradient_rect.height()

                text = str(int(v) if v == int(v) else v)

                ascent = fm.ascent()
                descent = fm.descent()
                
                center_offset = (ascent - descent) / 2
                
                baseline_y = y_center + center_offset
                
                painter.drawText(
                    QPointF(gradient_rect.right() + 6, baseline_y),
                    text
                )
                self._cached_positions.append((v, y_center))
                v += step

    # -------- Tooltip handling --------
    def mouseMoveEvent(self, event):
        fm = QFontMetrics(self.font())
        title_h = fm.height()
        pad = fm.height() * 0.6
        content_top = title_h + 6
        gradient_h = self.height() - content_top - 6 - (2 * pad)

        # Recompute gradient rect width using last paint assumptions
        max_w = max((fm.horizontalAdvance(str(v)) for v, _ in self._cached_positions), default=10)
        gradient_w = max_w
        gradient_x = (self.width() - (gradient_w + 6 + max_w)) / 2
        gradient_rect = QRectF(gradient_x, content_top + pad, gradient_w, gradient_h)

        if gradient_rect.contains(event.position()):
            pos = (gradient_rect.bottom() - event.position().y()) / gradient_rect.height()
            pos = min(max(pos, 0.0), 1.0)
            percent = pos * 100.0
            # invert mapping to value
            value = self._percent_to_value(percent)
            QToolTip.showText(event.globalPosition().toPoint(), f"{value:.2f}", self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def _percent_to_value(self, percent: float) -> float:
        for r in self.config.ranges:
            vmin, vmax, pmin, pmax, *rest = r
            if pmin <= percent <= pmax:
                if pmax == pmin:
                    return vmin
                t = (percent - pmin) / (pmax - pmin)
                return vmin + t * (vmax - vmin)
        if percent < self.config.ranges[0][2]:
            return self.config.ranges[0][0]
        return self.config.ranges[-1][1]
