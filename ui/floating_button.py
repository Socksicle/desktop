"""Material 3 Expressive 2026 Floating Squircle Action Button.

Used for the floating Add FAB and Search buttons next to the navigation capsule.
Supports smooth width and opacity collapse/expand animations (220ms, OutCubic).
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import (
    Qt, Signal, Property, QPropertyAnimation, QEasingCurve, QRectF, QPointF
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen
)

from utils import icons as _icons


def _mix(c1: QColor, c2: QColor, ratio: float) -> QColor:
    """Linear blend of two colors (ratio = weight of c2)."""
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * ratio),
        int(c1.green() + (c2.green() - c1.green()) * ratio),
        int(c1.blue() + (c2.blue() - c1.blue()) * ratio),
        255,
    )


class FloatingButton(QWidget):
    """Animatable squircle action button matching M3 Expressive 2026."""
    clicked = Signal()

    def __init__(
        self,
        icon_name: str = "add",
        bg_role: str = "primary",
        fg_role: str = "on_primary",
        default_width: int = 64,
        height: int = 64,
        radius: float = 20.0,
        theme=None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.icon_name = icon_name
        self.bg_role = bg_role
        self.fg_role = fg_role
        self._default_width = float(default_width)
        self._default_height = float(height)
        self._radius = float(radius)

        self._anim_width = float(default_width)
        self._anim_opacity = 1.0
        self._is_close_mode = False
        self._hovered = False
        self._pressed = False

        self.setFixedSize(default_width, height)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        self._width_anim = QPropertyAnimation(self, b"anim_width")
        self._width_anim.setDuration(220)
        self._width_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._width_anim.finished.connect(self._on_width_anim_finished)

        self._opacity_anim = QPropertyAnimation(self, b"anim_opacity")
        self._opacity_anim.setDuration(220)
        self._opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _on_width_anim_finished(self):
        if self._anim_width <= 0.1:
            self.setVisible(False)

    def get_anim_width(self) -> float:
        return self._anim_width

    def set_anim_width(self, val: float):
        self._anim_width = float(val)
        w = max(0, int(round(val)))
        self.setFixedWidth(w)
        self.update()

    anim_width = Property(float, get_anim_width, set_anim_width)

    def get_anim_opacity(self) -> float:
        return self._anim_opacity

    def set_anim_opacity(self, val: float):
        self._anim_opacity = max(0.0, min(1.0, float(val)))
        self.update()

    anim_opacity = Property(float, get_anim_opacity, set_anim_opacity)

    def set_expanded(self, expanded: bool, animated: bool = True):
        target_w = self._default_width if expanded else 0.0
        target_op = 1.0 if expanded else 0.0
        # Already at the target: re-showing a collapsed button would re-add its
        # layout spacing for one animation cycle, jerking the centered toolbar.
        if (abs(self._anim_width - target_w) < 0.5
                and abs(self._anim_opacity - target_op) < 0.01
                and self.isHidden() == (not expanded)):
            return
        if not animated:
            self._width_anim.stop()
            self._opacity_anim.stop()
            self.set_anim_width(target_w)
            self.set_anim_opacity(target_op)
            self.setVisible(expanded)
            return

        self.setVisible(True)
        self._width_anim.stop()
        self._opacity_anim.stop()
        self._width_anim.setStartValue(self._anim_width)
        self._width_anim.setEndValue(target_w)
        self._opacity_anim.setStartValue(self._anim_opacity)
        self._opacity_anim.setEndValue(target_op)
        self._width_anim.start()
        self._opacity_anim.start()

    def set_close_mode(self, is_close: bool):
        self._is_close_mode = bool(is_close)
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            was_pressed = self._pressed
            self._pressed = False
            self.update()
            if was_pressed and self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        w = float(self.width())
        h = float(self.height())
        if w <= 1.0 or self._anim_opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(self._anim_opacity)

        if self._is_close_mode:
            bg_hex = getattr(self.theme, "secondary_container", "#4A3F28")
            fg_hex = getattr(self.theme, "on_secondary_container", "#E6E0E9")
        else:
            bg_hex = getattr(self.theme, self.bg_role, "#E5C158")
            fg_hex = getattr(self.theme, self.fg_role, "#1C1710")

        bg_color = QColor(bg_hex)
        if self._pressed:
            bg_color = _mix(bg_color, QColor(0, 0, 0), 0.2)
        elif self._hovered:
            bg_color = _mix(bg_color, QColor(255, 255, 255), 0.15)

        radius = min(self._radius, w / 2.0)
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        if self.bg_role == "primary" and not self._is_close_mode:
            painter.setPen(Qt.NoPen)
        else:
            border_hex = getattr(self.theme, "outline_variant", "#52443d")
            border_c = QColor(border_hex)
            border_c.setAlpha(120)
            painter.setPen(QPen(border_c, 1.0))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(rect, radius - 0.5, radius - 0.5)

        # Draw icon
        cx = w / 2.0
        cy = h / 2.0
        if w >= 36.0:
            current_icon = "close" if self._is_close_mode else self.icon_name
            pm = _icons.pixmap(current_icon, fg_hex, 22)
            if pm and not pm.isNull():
                logical = pm.width() / max(1.0, pm.devicePixelRatio())
                painter.drawPixmap(QPointF(cx - logical / 2.0, cy - logical / 2.0), pm)
            else:
                pen = QPen(QColor(fg_hex), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                arm = 8.0
                if current_icon == "close":
                    painter.drawLine(QPointF(cx - arm, cy - arm), QPointF(cx + arm, cy + arm))
                    painter.drawLine(QPointF(cx + arm, cy - arm), QPointF(cx - arm, cy + arm))
                elif current_icon == "add":
                    painter.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
                    painter.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))


from utils.i18n import tr


class FloatingAddButton(FloatingButton):
    """Vibrant squircle FAB for adding server links (+)."""
    def __init__(self, theme=None, parent=None):
        super().__init__(
            icon_name="add",
            bg_role="primary",
            fg_role="on_primary",
            default_width=64,
            height=64,
            radius=20.0,
            theme=theme,
            parent=parent,
        )
        self.setToolTip(tr("header.add_tooltip"))


class FloatingSearchButton(FloatingButton):
    """Squircle button for triggering search next to the FAB."""
    def __init__(self, theme=None, parent=None):
        super().__init__(
            icon_name="search",
            bg_role="surface_container_lowest",
            fg_role="primary",
            default_width=56,
            height=64,
            radius=20.0,
            theme=theme,
            parent=parent,
        )
        self.setToolTip(tr("header.search_tooltip"))

