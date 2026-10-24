import math
from typing import Optional
from PySide6.QtWidgets import QRadioButton, QSizePolicy
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QBrush, QFont, QPen

_SHARED_FONTS: dict[str, QFont] = {}


def _get_shared_fonts() -> dict[str, QFont]:
    if not _SHARED_FONTS:
        fn = QFont()
        fn.setFamilies(["Segoe UI", "Noto Sans", "Liberation Sans", "Arial", "Noto Color Emoji"])
        fn.setPointSize(9)

        fb = QFont()
        fb.setFamilies(["Segoe UI", "Noto Sans", "Liberation Sans", "Arial", "Noto Color Emoji"])
        fb.setPointSize(9)
        fb.setWeight(QFont.DemiBold)

        fs = QFont()
        fs.setFamilies(["Segoe UI", "Noto Sans", "Liberation Sans", "Arial"])
        fs.setPointSize(8)

        ff = QFont()
        ff.setFamilies(["Segoe UI Emoji", "Noto Color Emoji"])
        ff.setPointSize(11)

        _SHARED_FONTS["normal"] = fn
        _SHARED_FONTS["bold"] = fb
        _SHARED_FONTS["sub"] = fs
        _SHARED_FONTS["flag"] = ff
    return _SHARED_FONTS


class AnimatedRadioButton(QRadioButton):
    """Material 3 Card Radio Button with flag squircle, active indicator dot, and host subtitle.

    Ported 1-to-1 from mobile item_server_node.xml & ServerNodeBinder.kt.
    """

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._show_flags = False
        self._bg_color = self._compute_bg(checked=False, hovered=False)
        self._bg_anim: Optional[QPropertyAnimation] = None

        self._actions_width = 170
        fonts = _get_shared_fonts()
        self._font_normal = fonts["normal"]
        self._font_bold = fonts["bold"]
        self._font_sub = fonts["sub"]
        self._font_flag = fonts["flag"]

        self.flag_emoji = "🌐"
        self.host_text = ""

        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "QRadioButton { border: none; background: transparent; } "
            "QRadioButton::indicator { width: 0px; height: 0px; }"
        )

        self._pulse_phase = 0.0
        self._pulse_anim: Optional[QPropertyAnimation] = None
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool):
        self.refresh_bg()
        self.update()

    def get_pulse_phase(self) -> float:
        return self._pulse_phase

    def set_pulse_phase(self, val: float):
        self._pulse_phase = float(val)

    pulsePhase = Property(float, get_pulse_phase, set_pulse_phase)

    def _start_pulse(self):
        pass

    def _stop_pulse(self):
        pass

    def _compute_bg(self, checked: bool, hovered: bool) -> QColor:
        theme = self.theme
        base = QColor(getattr(theme, "surface_container_high",
                              getattr(theme, "surface_variant", "#2A2018")))
        if not checked:
            if hovered:
                base = QColor(getattr(theme, "surface_container_highest", "#36343B"))
            return base
        accent = QColor(getattr(theme, "primary", "#D0BCFF"))
        blend = 0.18 if hovered else 0.12
        mixed = QColor(
            int(base.red() + (accent.red() - base.red()) * blend),
            int(base.green() + (accent.green() - base.green()) * blend),
            int(base.blue() + (accent.blue() - base.blue()) * blend),
            255)
        if hovered:
            return mixed.lighter(106)
        return mixed

    def refresh_bg(self, hovered: bool | None = None):
        if hovered is None:
            hovered = self.underMouse()
        self._animate_bg(self._compute_bg(self.isChecked(), hovered))

    def enterEvent(self, event):
        self._animate_bg(self._compute_bg(self.isChecked(), True))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_bg(self._compute_bg(self.isChecked(), False))
        super().leaveEvent(event)

    def _animate_bg(self, target_color):
        if self._bg_anim is None:
            self._bg_anim = QPropertyAnimation(self, b"bgColor")
            self._bg_anim.setDuration(200)
            self._bg_anim.setEasingCurve(QEasingCurve.OutCubic)
        else:
            self._bg_anim.stop()
        self._bg_anim.setStartValue(self._bg_color)
        self._bg_anim.setEndValue(target_color)
        self._bg_anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        w = float(self.width())
        h = float(self.height())
        is_active = self.isChecked()

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self._bg_color))
        p.drawRoundedRect(QRectF(0, 0, w, h), 14, 14)

        accent_color = QColor(self.theme.primary)
        muted_color = QColor(getattr(self.theme, "outline", "#938F99"))
        border_color = accent_color if is_active else QColor(
            muted_color.red(), muted_color.green(), muted_color.blue(), 0x3C)
        p.setPen(QPen(border_color, 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 14, 14)

        dot_cx = 16.0
        dot_cy = h / 2.0
        if is_active:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(accent_color))
            p.drawEllipse(QPointF(dot_cx, dot_cy), 5.0, 5.0)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 30)))
            p.drawEllipse(QPointF(dot_cx, dot_cy), 4.0, 4.0)

        if self._show_flags:
            flag_x = 28.0
            flag_y = (h - 28.0) / 2.0
            flag_rect = QRectF(flag_x, flag_y, 28.0, 28.0)

            p.setPen(QPen(QColor(147, 143, 153, 40), 1.0))
            p.setBrush(QBrush(QColor(255, 255, 255, 12)))
            p.drawRoundedRect(flag_rect, 9, 9)

            p.setFont(self._font_flag)
            p.setPen(QColor(255, 255, 255))
            p.drawText(flag_rect, Qt.AlignCenter, self.flag_emoji)
            text_x = 64.0
        else:
            text_x = 28.0

        text_avail_w = max(40.0, w - text_x - self._actions_width - 8.0)

        name_text = self.text()
        p.setFont(self._font_bold if is_active else self._font_normal)
        p.setPen(accent_color if is_active else QColor(self.theme.on_surface))

        elided_name = p.fontMetrics().elidedText(name_text, Qt.ElideRight, int(text_avail_w))

        if self.host_text:
            p.drawText(int(text_x), 5, int(text_avail_w), 20, Qt.AlignVCenter | Qt.AlignLeft, elided_name)

            p.setFont(self._font_sub)
            muted_color = QColor(getattr(self.theme, "outline", "#938F99"))
            p.setPen(muted_color)
            elided_host = p.fontMetrics().elidedText(self.host_text, Qt.ElideRight, int(text_avail_w))
            p.drawText(int(text_x), 23, int(text_avail_w), 18, Qt.AlignVCenter | Qt.AlignLeft, elided_host)
        else:
            p.drawText(int(text_x), 0, int(text_avail_w), int(h), Qt.AlignVCenter | Qt.AlignLeft, elided_name)

    def getBgColor(self):
        return self._bg_color

    def setBgColor(self, c):
        self._bg_color = c
        self.update()

    bgColor = Property(QColor, getBgColor, setBgColor)
