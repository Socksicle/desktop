"""Compact TUN-mode switch badge for the hero row, styled after ServerFilterBar."""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, Property, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QFont

from .anim_utils import fluid_easing
from utils.i18n import tr


class TunToggleBar(QWidget):
    """Two-state TUN pill ('Off' / 'TUN') with a sliding indicator.

    Visual language matches ServerFilterBar, but the highlighted state is
    accent-colored only when TUN mode is on; off keeps a muted indicator.
    """

    toggled = Signal(bool)

    def __init__(self, parent=None, theme=None, checked=False):
        super().__init__(parent)
        self.theme = theme
        self.labels = [tr("tun.off"), "TUN"]
        self._checked = bool(checked)
        self._indicator_x = 0.0

        self._color_bg = QColor("#1E1612")
        self._color_border = QColor(147, 143, 153, 38)
        self._color_on_indicator = QColor("#E2703C")
        self._color_on_text = QColor("#141210")
        self._color_muted_text = QColor("#877A6B")
        self._color_off_indicator = QColor("#36343B")
        self._color_off_text = QColor("#E6E0E9")

        self.setFixedSize(88, 28)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"indicator_x")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(fluid_easing())

        if theme:
            self.apply_theme(theme)
        self._snap_indicator()

    def get_indicator_x(self) -> float:
        return self._indicator_x

    def set_indicator_x(self, val: float):
        self._indicator_x = float(val)
        self.update()

    indicator_x = Property(float, get_indicator_x, set_indicator_x)

    def apply_theme(self, theme):
        self.theme = theme
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        on_accent_hex = getattr(theme, "on_primary", "#141210")
        surface_elevated_hex = getattr(theme, "surface_container_high", "#211913")
        surface_bright_hex = getattr(theme, "surface_container_highest", "#36343B")
        muted_hex = getattr(theme, "outline", "#938F99")
        on_surface_hex = getattr(theme, "on_surface", "#E6E0E9")

        self._color_on_indicator = QColor(accent_hex)
        self._color_on_text = QColor(on_accent_hex)
        self._color_bg = QColor(surface_elevated_hex)
        self._color_muted_text = QColor(muted_hex)
        self._color_off_indicator = QColor(surface_bright_hex)
        self._color_off_text = QColor(on_surface_hex)

        border = QColor(muted_hex)
        self._color_border = QColor(border.red(), border.green(), border.blue(), 38)
        self.update()

    def is_checked(self) -> bool:
        return self._checked

    def _snap_indicator(self):
        self._anim.stop()
        self.set_indicator_x(self._indicator_x_for(self._checked))

    def _indicator_x_for(self, checked: bool) -> float:
        pad_h = 4.0
        tw = self._tab_width()
        return pad_h + tw if checked else pad_h

    def _tab_width(self) -> float:
        return (self.width() - 8.0) / len(self.labels)

    def set_checked(self, checked: bool, animated: bool = True):
        """Set state without emitting toggled (used for sync from settings)."""
        if self._checked == bool(checked):
            return
        self._checked = bool(checked)
        target = self._indicator_x_for(self._checked)
        if not animated:
            self._anim.stop()
            self.set_indicator_x(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._indicator_x)
        self._anim.setEndValue(target)
        self._anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._checked = not self._checked
            self._anim.stop()
            self._anim.setStartValue(self._indicator_x)
            self._anim.setEndValue(self._indicator_x_for(self._checked))
            self._anim.start()
            self.toggled.emit(self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return

        radius = h / 2.0
        painter.setPen(QPen(self._color_border, 1.0))
        painter.setBrush(QBrush(self._color_bg))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)

        tw = self._tab_width()
        pad_v = 3.0
        ind_h = h - (pad_v * 2.0)
        ind_r = ind_h / 2.0

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._color_on_indicator if self._checked
                                else self._color_off_indicator))
        painter.drawRoundedRect(QRectF(self._indicator_x, pad_v, tw, ind_h), ind_r, ind_r)

        font = QFont("Segoe UI")
        font.setPixelSize(10)
        for i, text in enumerate(self.labels):
            tab_rect = QRectF(4.0 + (i * tw), 0, tw, h)
            is_active = (i == (1 if self._checked else 0))
            font.setBold(is_active)
            painter.setFont(font)
            if is_active:
                painter.setPen(self._color_on_text if self._checked
                               else self._color_off_text)
            else:
                painter.setPen(self._color_muted_text)
            painter.drawText(tab_rect, Qt.AlignCenter, text)
