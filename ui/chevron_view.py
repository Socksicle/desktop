from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath


class ChevronView(QWidget):
    """ic_chevron_down.xml port: path M6,9 l6,6 l6,-6 on a 24x24 viewport.

    angle 0 points down (expanded), -90 points right (collapsed / active
    server card chevron uses rotation 270deg in Android, same direction).
    """

    def __init__(self, size: int = 20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0.0
        self._color = QColor("#877A6B")
        self._stroke = 2.2

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def set_angle(self, degrees: float):
        self._angle = float(degrees)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return

        scale = w / 24.0
        p.translate(w / 2.0, h / 2.0)
        p.rotate(self._angle)
        p.scale(scale, scale)
        p.translate(-12.0, -12.0)

        path = QPainterPath(QPointF(6.0, 9.0))
        path.lineTo(12.0, 15.0)
        path.lineTo(18.0, 9.0)

        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
