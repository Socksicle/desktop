"""Soft gradient fade overlay at the bottom of the window.

Ensures that scrolling server cards smoothly fade out behind the floating
navigation bar, preventing visual collision or jagged text behind the bar.
All mouse events pass through transparently to the underlying scroll area.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QLinearGradient


class BottomFadeOverlay(QWidget):
    """Bottom gradient fade into theme.surface."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        painter = QPainter(self)
        w = float(self.width())
        h = float(self.height())
        if w <= 0.0 or h <= 0.0:
            return

        surf_hex = getattr(self.theme, "surface", "#1b110b")
        base = QColor(surf_hex)

        c0 = QColor(base)
        c0.setAlpha(0)
        c1 = QColor(base)
        c1.setAlpha(160)
        c2 = QColor(base)
        c2.setAlpha(245)

        grad = QLinearGradient(0.0, 0.0, 0.0, h)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(0.45, c1)
        grad.setColorAt(1.0, c2)

        painter.fillRect(QRectF(0.0, 0.0, w, h), QBrush(grad))
