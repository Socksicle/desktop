from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QRectF, Slot, Signal
from PySide6.QtGui import QPainter, QBrush, QColor, QPen


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear color interpolation between c1 and c2 by factor t in [0.0, 1.0]."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
        int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
    )


class AnimatedToggleSwitch(QWidget):
    """Material 3 (Material You) Toggle Switch with smooth linear color interpolation."""

    toggled = Signal(bool)
    stateChanged = toggled

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedSize(52, 32)
        self.setCursor(Qt.PointingHandCursor)

        self._enabled = False
        self._hovered = False
        self._thumb_position = 4.0

        self._animation = QPropertyAnimation(self, b"thumbPosition")
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def apply_theme(self, theme):
        self.set_theme(theme)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Progress ratio 0.0 (OFF) -> 1.0 (ON)
        ratio = max(0.0, min(1.0, (self._thumb_position - 4.0) / 20.0))

        # Palette colors
        if self.theme:
            track_on = QColor(self.theme.primary)
            track_off = QColor(self.theme.surface_container_highest)
            thumb_on = QColor(self.theme.on_primary)
            thumb_off = QColor(self.theme.outline)
            track_border = QColor(self.theme.outline)
        else:
            track_on = QColor("#D0BCFF")
            track_off = QColor("#36343B")
            thumb_on = QColor("#381E72")
            thumb_off = QColor("#938F99")
            track_border = QColor("#938F99")

        # Smooth color blending
        cur_track = _lerp_color(track_off, track_on, ratio)
        cur_thumb = _lerp_color(thumb_off, thumb_on, ratio)
        cur_border = _lerp_color(track_border, track_on, ratio)

        # Hover brightening
        if self._hovered and ratio < 0.5:
            cur_border = cur_border.lighter(115)
            cur_thumb = cur_thumb.lighter(115)

        # 1. Draw Track
        border_width = 1.5 * (1.0 - ratio)
        if border_width > 0.05:
            p.setPen(QPen(cur_border, border_width))
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(QBrush(cur_track))
        p.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 15, 15)

        # 2. Draw Thumb (grows from 16px to 24px)
        thumb_size = 16.0 + 8.0 * ratio
        thumb_y = (self.height() - thumb_size) / 2.0

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(cur_thumb))
        p.drawEllipse(QRectF(self._thumb_position, thumb_y, thumb_size, thumb_size))

    @Slot()
    @Slot(bool)
    def toggle(self, enable=None, animated=True):
        """Toggle switch state and smoothly animate thumb to target position."""
        if enable is not None:
            self._enabled = bool(enable)
        else:
            self._enabled = not self._enabled

        end_pos = 24.0 if self._enabled else 4.0

        if not animated:
            self._animation.stop()
            self._thumb_position = end_pos
            self.update()
            return

        if abs(self._thumb_position - end_pos) < 0.01 and self._animation.state() != QPropertyAnimation.Running:
            return

        self._animation.stop()
        self._animation.setStartValue(self._thumb_position)
        self._animation.setEndValue(end_pos)
        self._animation.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()
            self.toggled.emit(self._enabled)
            event.accept()
        else:
            super().mousePressEvent(event)

    def setChecked(self, checked: bool, animated: bool = True):
        self.toggle(checked, animated=animated)

    def isChecked(self) -> bool:
        return self._enabled

    def getThumbPosition(self) -> float:
        return self._thumb_position

    def setThumbPosition(self, pos: float):
        self._thumb_position = pos
        self.update()

    thumbPosition = Property(float, getThumbPosition, setThumbPosition)

    @property
    def is_on(self) -> bool:
        return self._enabled

