import pyqtgraph as pg
from pyqtgraph import QtCore, QtGui
from PyQt6.QtCore import pyqtSignal


class BoxPlotItem(pg.GraphicsObject):
    """A single box-and-whisker glyph for use inside a pyqtgraph PlotWidget.

    Visual structure (top to bottom)
    ---------------------------------
    ──────────────   upper whisker cap  (black, whisker_width wide)
        │                               vertical stem: whisker_high → q3
    ┌───────────┐   top of IQR box      (outlined, semi-transparent fill)
    │           │
    │ ───────── │   median line          (light-yellow, full box width)
    │           │
    └───────────┘   bottom of IQR box
        │                               vertical stem: q1 → whisker_low
    ──────────────   lower whisker cap  (black, whisker_width wide)

    When q1 == q3 (zero-height box) the outline rectangle degenerates to a
    horizontal line, but the whisker stems and caps above/below still give a
    clear visual indication that the item is present.

    Parameters
    ----------
    x             : float  – centre position on the x-axis
    q1            : float  – 25th-percentile value
    median        : float  – 50th-percentile value
    q3            : float  – 75th-percentile value
    whisker_low   : float  – lower fence (typically Q1 − 1.5·IQR, clipped to data)
    whisker_high  : float  – upper fence (typically Q3 + 1.5·IQR, clipped to data)
    outliers      : list[float] – individual points beyond the fences
    box_width     : float  – full width of the IQR box in data-space units
    whisker_width : float  – full width of the whisker caps in data-space units
    pen           : QPen   – pen used for the box outline (default: black, 1 px)
    brush         : QBrush – fill brush for the IQR box (default: blue, α = 150)
    """

    # Colours defined once so they are easy to customise.
    _WHISKER_COLOR  = QtGui.QColor(0,   0,   0)        # black
    _MEDIAN_COLOR   = QtGui.QColor(255, 255, 180)       # light yellow
    _OUTLINE_COLOR  = QtGui.QColor(0,   0,   0)        # black box outline
    # Outliers are intentionally NOT drawn here. Because QPicture operates in
    # data-space coordinates, any fixed radius becomes enormous when the y-axis
    # spans large value ranges (e.g. jerk in m/s³). Outliers must be rendered
    # by the caller using a ScatterPlotItem, which works in pixel space.

    # Emitted on hover enter (stats tuple) and hover leave (None).
    sigHovered = pyqtSignal(object)   # object = tuple[float,...] | None

    def __init__(self, x, q1, median, q3, whisker_low, whisker_high,
                 outliers=None, box_width=0.5, whisker_width=0.3,
                 pen=None, brush=None):
        super().__init__()
        self.x             = x
        self.q1            = q1
        self.median        = median
        self.q3            = q3
        self.whisker_low   = whisker_low
        self.whisker_high  = whisker_high
        self.outliers      = outliers or []
        self.box_width     = box_width
        self.whisker_width = whisker_width
        # Default box fill: semi-transparent blue
        self.pen   = pen   or pg.mkPen(self._OUTLINE_COLOR, width=1)
        self.brush = brush or pg.mkBrush(100, 100, 255, 150)
        self.picture = None
        self.setAcceptHoverEvents(True)   # ← enable hover
        self._generate_picture()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_picture(self):
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)

        half_box     = self.box_width     / 2.0
        half_whisker = self.whisker_width / 2.0

        # Degenerate case: all five values identical — draw a single black
        # horizontal line at that level and nothing else.
        if (self.q1 == self.q3 == self.median
                == self.whisker_low == self.whisker_high):
            p.setPen(pg.mkPen(self._WHISKER_COLOR, width=2))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawLine(
                QtCore.QPointF(self.x - half_box, self.median),
                QtCore.QPointF(self.x + half_box, self.median),
            )
            p.end()
            return

        # ── 1. IQR box (filled, outlined) ────────────────────────────────
        # Normalise so height is always non-negative; Qt fills a rect with a
        # negative height using an undefined/solid brush on some platforms.
        box_top    = max(self.q1, self.q3)
        box_bottom = min(self.q1, self.q3)
        p.setPen(self.pen)
        p.setBrush(self.brush)
        box_rect = QtCore.QRectF(
            self.x - half_box,
            box_bottom,
            self.box_width,
            box_top - box_bottom,   # always >= 0
        )
        p.drawRect(box_rect)

        # Clear the brush immediately so no subsequent line-draw accidentally
        # triggers a fill.
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        # ── 2. Median line (light yellow, full box width) ─────────────────
        median_pen = pg.mkPen(self._MEDIAN_COLOR, width=2)
        p.setPen(median_pen)
        p.drawLine(
            QtCore.QPointF(self.x - half_box, self.median),
            QtCore.QPointF(self.x + half_box, self.median),
        )

        # ── 3. Whisker stems (black, centred vertical lines) ──────────────
        whisker_pen = pg.mkPen(self._WHISKER_COLOR, width=1)
        p.setPen(whisker_pen)

        # Upper stem: top of box → upper whisker cap
        p.drawLine(
            QtCore.QPointF(self.x, self.q3),
            QtCore.QPointF(self.x, self.whisker_high),
        )
        # Lower stem: bottom of box → lower whisker cap
        p.drawLine(
            QtCore.QPointF(self.x, self.q1),
            QtCore.QPointF(self.x, self.whisker_low),
        )

        # ── 4. Whisker caps (black horizontal bars) ───────────────────────
        p.drawLine(
            QtCore.QPointF(self.x - half_whisker, self.whisker_high),
            QtCore.QPointF(self.x + half_whisker, self.whisker_high),
        )
        p.drawLine(
            QtCore.QPointF(self.x - half_whisker, self.whisker_low),
            QtCore.QPointF(self.x + half_whisker, self.whisker_low),
        )

        p.end()

    # ------------------------------------------------------------------
    # pyqtgraph GraphicsObject interface
    # ------------------------------------------------------------------

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        """Return the bounding rect computed analytically from data values.
    
        QPicture.boundingRect() is unreliable for zero-height or near-zero
        geometry because Qt pads the pen stroke width in device/pixel
        coordinates into the rect. For a plot whose y-range spans large
        data values this causes the hover area to be vastly larger than the
        visible item. Computing the rect from the stored values is exact.
        """
        half_box = self.box_width / 2.0
        half_whisker = self.whisker_width / 2.0
    
        left  = self.x - max(half_box, half_whisker)
        right = self.x + max(half_box, half_whisker)
    
        # In the degenerate case all five values equal self.median.
        top    = self.whisker_high
        bottom = self.whisker_low
    
        if top == bottom:
            # Zero-height: give a small epsilon so the rect is non-null and
            # pyqtgraph's scene graph doesn't cull it, but keep it tight.
            epsilon = 1e-6
            top    += epsilon
            bottom -= epsilon
    
        return QtCore.QRectF(left, bottom, right - left, top - bottom)

    def hoverEvent(self, ev):
        """Emit sigHovered with the five statistical values on enter, None on leave."""
        if ev.isExit():
            self.sigHovered.emit(None)
        else:
            self.sigHovered.emit((
                self.whisker_low,
                self.q1,
                self.median,
                self.q3,
                self.whisker_high,
            ))