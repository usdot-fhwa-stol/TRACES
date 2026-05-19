import pyqtgraph as pg
from pyqtgraph import QtCore, QtGui

class BoxPlotItem(pg.GraphicsObject):
    def __init__(self, x, q1, median, q3, whisker_low, whisker_high,
                 outliers=None, box_width=0.5, whisker_width=0.2,
                 pen=None, brush=None):
        super().__init__()
        self.x = x                          # center position on x-axis
        self.q1 = q1                        # 25th percentile
        self.median = median                # 50th percentile
        self.q3 = q3                        # 75th percentile
        self.whisker_low = whisker_low      # lower whisker end
        self.whisker_high = whisker_high    # upper whisker end
        self.outliers = outliers or []      # individual points beyond whiskers
        self.box_width = box_width          # width of the IQR box
        self.whisker_width = whisker_width  # width of whisker caps (as fraction of box_width or absolute)
        self.pen = pen or pg.mkPen('w')
        self.brush = brush or pg.mkBrush(100, 100, 255, 150)
        self.picture = None
        self._generate_picture()
    
    def _generate_picture(self):
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        p.setPen(self.pen)
        p.setBrush(self.brush)
        
        w = self.box_width / 2
        ww = self.whisker_width / 2
        
        # IQR box
        p.drawRect(QtCore.QRectF(self.x - w, self.q1, self.box_width, self.q3 - self.q1))
        # Median line
        p.drawLine(QtCore.QPointF(self.x - w, self.median), 
                   QtCore.QPointF(self.x + w, self.median))
        # Whiskers (vertical lines)
        p.drawLine(QtCore.QPointF(self.x, self.q3), 
                   QtCore.QPointF(self.x, self.whisker_high))
        p.drawLine(QtCore.QPointF(self.x, self.q1), 
                   QtCore.QPointF(self.x, self.whisker_low))
        # Whisker caps
        p.drawLine(QtCore.QPointF(self.x - ww, self.whisker_high),
                   QtCore.QPointF(self.x + ww, self.whisker_high))
        p.drawLine(QtCore.QPointF(self.x - ww, self.whisker_low),
                   QtCore.QPointF(self.x + ww, self.whisker_low))
        
        p.end()
    
    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)
    
    def boundingRect(self):
        return QtCore.QRectF(self.picture.boundingRect())