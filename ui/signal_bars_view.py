from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QBrush


class SignalBarsView(QWidget):
    """Material-style signal bars (4 ascending rounded bars).
    
    Ported 1-to-1 from mobile SignalBarsView.kt.
    Filled bars take active_color, the rest stay inactive_color (semi-transparent muted).
    """

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self._level = 0
        self._last_ms = None
        self._active_color = QColor(getattr(theme, "primary", "#D0BCFF") if theme else "#D0BCFF")
        self._inactive_color = QColor(147, 143, 153, 70)
        self.setFixedHeight(14)
        self.setFixedWidth(28)
        if theme:
            self.apply_theme(theme)

    def sizeHint(self):
        return QSize(28, 14)

    def apply_theme(self, theme):
        self.theme = theme
        muted_hex = getattr(theme, "outline", "#938F99")
        muted = QColor(muted_hex)
        self._inactive_color = QColor(muted.red(), muted.green(), muted.blue(), 70)
        if self._last_ms is not None:
            self.set_latency(self._last_ms)
        else:
            self.update()

    def set_level(self, level: int):
        clamped = max(0, min(4, int(level)))
        if clamped != self._level:
            self._level = clamped
            self.update()

    def set_active_color(self, color):
        if isinstance(color, str):
            c = QColor(color)
        else:
            c = color
        if c != self._active_color:
            self._active_color = c
            self.update()

    def set_latency(self, ms: float | None):
        self._last_ms = ms
        theme = self.theme
        ok_color = getattr(theme, "primary", "#D0BCFF") if theme else "#D0BCFF"
        warn_color = getattr(theme, "warning", "#FFC107") if theme else "#FFC107"
        err_color = getattr(theme, "error", "#F44336") if theme else "#F44336"
        muted_color = getattr(theme, "outline", "#938F99") if theme else "#938F99"

        if ms is None or ms < 0:
            self.set_level(0)
            self.set_active_color(muted_color)
        elif ms < 80:
            self.set_level(4)
            self.set_active_color(ok_color)
        elif ms < 150:
            self.set_level(3)
            self.set_active_color(ok_color)
        elif ms < 300:
            self.set_level(2)
            self.set_active_color(warn_color)
        else:
            self.set_level(1)
            self.set_active_color(err_color)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return

        fractions = [0.32, 0.55, 0.78, 1.0]
        bar_w = w / 7.0
        gap = (w - bar_w * 4.0) / 3.0
        radius = bar_w / 3.0

        painter.setPen(Qt.NoPen)
        bottom = h
        for i in range(4):
            bh = h * fractions[i]
            left = i * (bar_w + gap)
            # QRectF (not the int x/y/w/h overload): float geometry would
            # otherwise truncate, giving each bar a different bottom edge.
            rect = QRectF(left, bottom - bh, bar_w, bh)

            color = self._active_color if i < self._level else self._inactive_color
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(rect, radius, radius)
