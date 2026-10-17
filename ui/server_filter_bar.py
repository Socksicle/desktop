from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, Property, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QFontMetricsF

from .anim_utils import fluid_easing
from utils.i18n import tr


class ServerFilterBar(QWidget):
    """Filter tabs bar for subscriptions and servers (All / Subs / Local)."""

    filterChanged = Signal(int)

    BASE_TEXT_SIZE = 12.5
    MIN_TEXT_SIZE = 10.0

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self.labels = [tr("filter.all"), tr("filter.subs"), tr("filter.local")]
        self._filter_mode = 0
        self._indicator_x = 0.0
        self._text_size = self.BASE_TEXT_SIZE

        self._color_bg = QColor("#1E1612")
        self._color_border = QColor(147, 143, 153, 38)
        self._color_indicator = QColor("#E2703C")
        self._color_active_text = QColor("#141210")
        self._color_inactive_text = QColor("#877A6B")

        self.setFixedHeight(36)
        self.setFixedWidth(220)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"indicator_x")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(fluid_easing())

        if theme:
            self.apply_theme(theme)

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
        surface_elevated_hex = getattr(theme, "surface_container_high", getattr(theme, "surface_elevated", "#211913"))
        muted_hex = getattr(theme, "outline", "#938F99")

        self._color_indicator = QColor(accent_hex)
        self._color_active_text = QColor(on_accent_hex)
        self._color_bg = QColor(surface_elevated_hex)

        muted = QColor(muted_hex)
        self._color_border = QColor(muted.red(), muted.green(), muted.blue(), 38)
        self._color_inactive_text = muted
        self.update()

    def filter_mode(self) -> int:
        return self._filter_mode

    def set_filter_mode(self, mode: int, animated: bool = True):
        mode = max(0, min(len(self.labels) - 1, int(mode)))
        if mode == self._filter_mode and not animated:
            return

        self._filter_mode = mode
        target_x = self._calc_tab_x(mode)

        if not animated:
            self._anim.stop()
            self.set_indicator_x(target_x)
        else:
            self._anim.stop()
            self._anim.setStartValue(self._indicator_x)
            self._anim.setEndValue(target_x)
            self._anim.start()

        self.filterChanged.emit(mode)

    def _get_tab_width(self) -> float:
        pad_h = 4.0
        usable = self.width() - (pad_h * 2.0)
        return usable / len(self.labels) if len(self.labels) > 0 else 0.0

    def _calc_tab_x(self, index: int) -> float:
        pad_h = 4.0
        tw = self._get_tab_width()
        return pad_h + (index * tw)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_labels_to_tabs()
        self.set_indicator_x(self._calc_tab_x(self._filter_mode))

    def _fit_labels_to_tabs(self):
        """Shrink labels to fit the tab width (min 10px)."""
        tw = self._get_tab_width()
        if tw <= 0:
            return
        probe_size = 25
        probe = QFont("Segoe UI")
        probe.setBold(True)
        probe.setPixelSize(probe_size)
        metrics = QFontMetricsF(probe)
        widest = 0.0
        for label in self.labels:
            widest = max(widest, metrics.horizontalAdvance(label))
        size = self.BASE_TEXT_SIZE
        avail = tw - 18.0
        if widest > 0 and widest * (size / probe_size) > avail:
            size = probe_size * avail / widest
        size = max(self.MIN_TEXT_SIZE, min(self.BASE_TEXT_SIZE, size))
        if abs(size - self._text_size) > 0.05:
            self._text_size = size
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            pad_h = 4.0
            tw = self._get_tab_width()
            if tw > 0:
                idx = int((x - pad_h) / tw)
                idx = max(0, min(len(self.labels) - 1, idx))
                self.set_filter_mode(idx, animated=True)
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

        tw = self._get_tab_width()
        pad_v = 4.0
        ind_h = max(24.0, h - (pad_v * 2.0))
        ind_r = min(14.0, ind_h / 2.0)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._color_indicator))
        painter.drawRoundedRect(QRectF(self._indicator_x, pad_v, tw, ind_h), ind_r, ind_r)

        font = QFont("Segoe UI")
        font.setPixelSize(int(round(self._text_size)))
        pad_h = 4.0
        for i, text in enumerate(self.labels):
            tab_x = pad_h + (i * tw)
            tab_rect = QRectF(tab_x, 0, tw, h)

            is_active = (i == self._filter_mode)
            font.setBold(is_active)
            painter.setFont(font)
            painter.setPen(self._color_active_text if is_active else self._color_inactive_text)
            painter.drawText(tab_rect, Qt.AlignCenter, text)
