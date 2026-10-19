"""Material 3 Expressive linear progress indicator (determinate wavy).

Ports the Compose ``LinearWavyProgressIndicator`` metrics: a 10 dp container
with a 4 dp stroke and 3 dp wave amplitude, 40 dp wavelength, a straight 4 dp
track, a 4 dp gap between the active indicator and the track, and a 4 dp stop
dot that shrinks as the indicator reaches the end of the track.
"""

import math

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

_THICKNESS = 4.0
_AMPLITUDE = 3.0
_WAVELENGTH = 40.0
_GAP = 4.0
_STOP_SIZE = 4.0
_TICK_MS = 16
# The wave travels one wavelength per second, matching the default wave speed.
_PHASE_STEP = _WAVELENGTH * _TICK_MS / 1000.0


class WavyLinearProgressBar(QWidget):
    """Wavy M3 Expressive progress bar; ``value`` is a 0.0-1.0 fraction."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._phase = 0.0
        self._active = QColor(getattr(theme, "primary", "#D0BCFF"))
        self._track = QColor(getattr(theme, "secondary_container", "#4A4458"))
        self.setFixedHeight(int(_THICKNESS + 2 * _AMPLITUDE))
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._advance_phase)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    def value(self) -> float:
        return self._value

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _advance_phase(self):
        self._phase = (self._phase + _PHASE_STEP) % _WAVELENGTH
        self.update()

    def _amplitude(self) -> float:
        # The wave flattens at both ends of the track, as in the M3 spec.
        return 0.0 if self._value <= 0.1 or self._value >= 0.95 else _AMPLITUDE

    def _wave_y(self, x: float, cy: float, amplitude: float) -> float:
        if amplitude == 0.0:
            return cy
        return cy + amplitude * math.sin(2.0 * math.pi * (x + self._phase) / _WAVELENGTH)

    def _active_path(self, start: float, end: float, cy: float, amplitude: float) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(start, self._wave_y(start, cy, amplitude))
        x = start
        while x + 2.0 < end:
            x += 2.0
            path.lineTo(x, self._wave_y(x, cy, amplitude))
        path.lineTo(end, self._wave_y(end, cy, amplitude))
        return path

    def paintEvent(self, event):
        width = float(self.width())
        height = float(self.height())
        if width <= 0.0 or height <= 0.0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        cy = height / 2.0
        cap = _THICKNESS / 2.0
        bar_head = self._value * width

        active_end = min(max(bar_head, cap), width - cap)
        if bar_head < cap:
            # No room for the active cap yet: the track spans the whole width.
            track_start = cap
        else:
            track_start = max(cap, active_end + min(bar_head - cap, _GAP) + _THICKNESS)

        if active_end > cap:
            pen = QPen(self._active, _THICKNESS)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(self._active_path(cap, active_end, cy, self._amplitude()))

        if track_start < width - cap:
            pen = QPen(self._track, _THICKNESS)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(track_start, cy), QPointF(width - cap, cy))

        size = min(_THICKNESS, _STOP_SIZE)
        stop_x = width - size
        if stop_x <= bar_head:
            size = max(0.0, size - (bar_head - stop_x))
            stop_x = bar_head
        if size > 0.0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._active)
            painter.drawEllipse(QPointF(stop_x + size / 2.0, cy), size / 2.0, size / 2.0)
