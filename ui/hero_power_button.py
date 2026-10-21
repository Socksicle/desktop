from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, Property, QRectF, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor, QBrush, QPen


class HeroPowerButton(QWidget):
    """Custom 120 FPS Hero Power Button for Socksicle.
    
    Ported 1-to-1 from mobile HeroPowerButton.kt.
    Features:
    - Upright static orientation
    - Vector Power (I/O) icon in center with smooth color morphing
    - Disconnected: solid accent circle with on_accent power icon
    - Connecting: continuous smooth spinning arcs at 60 FPS while connecting
    - Animated border line: 2 arcs that spin around circumference, transition from
      white to theme accent, and seamlessly join into a solid ring
    - Connected: glowing accent ring, inner soft glow, ambient halo, and accent power icon
    """

    clicked = Signal()

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self._is_connected = False
        self._is_connecting = False
        self._spin_angle = 0.0
        self._settle_start_angle = -90.0
        self._progress = 0.0  # 0.0 = Disconnected, 1.0 = Connected

        self._color_accent = QColor("#D0BCFF")
        self._color_on_accent = QColor("#381E72")
        self._color_muted = QColor("#938F99")
        self._color_surface = QColor("#1A1624")

        self.setFixedSize(150, 150)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"progress")
        self._anim.setDuration(340)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._on_spin_tick)

        if theme:
            self.apply_theme(theme)

    def _on_spin_tick(self):
        # Decreasing angle: Qt arcs run counter-clockwise, so this spins
        # the connecting arcs clockwise (to the right).
        self._spin_angle = (self._spin_angle - 4.0) % 360.0
        self.update()

    def _ring_frame(self, progress: float) -> tuple:
        """Rotation and arc length of the settling ring at ``progress``.

        Angle smoothly advances clockwise from _settle_start_angle,
        and arcs seamlessly expand from 115 deg to 180 deg to close the ring.
        """
        rot_deg = self._settle_start_angle - (progress - 0.4) / 0.6 * 90.0
        if progress <= 0.4:
            arc_len = 115.0
        else:
            join_frac = min(1.0, max(0.0, (progress - 0.4) / 0.6))
            arc_len = 115.0 + (180.0 - 115.0) * join_frac
        return rot_deg, arc_len

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, val: float):
        self._progress = max(0.0, min(1.0, float(val)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def apply_theme(self, theme):
        self.theme = theme
        self._color_accent = QColor(getattr(theme, "primary", "#D0BCFF"))
        self._color_on_accent = QColor(getattr(theme, "on_primary", "#381E72"))
        self._color_muted = QColor(getattr(theme, "outline", "#938F99"))
        self._color_surface = QColor(getattr(theme, "surface", "#1A1624"))
        self.update()

    def is_connected(self) -> bool:
        return self._is_connected

    def is_connecting(self) -> bool:
        return self._is_connecting

    def set_connecting(self, connecting: bool):
        if self._is_connecting == connecting:
            return
        self._is_connecting = connecting
        if connecting:
            self._is_connected = False
            self._anim.stop()
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.setDuration(240)
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(0.4)
            self._anim.start()
            self._spin_timer.start()
        else:
            self._settle_start_angle = self._spin_angle
            self._spin_timer.stop()
            self.update()

    def set_connected(self, connected: bool, animated: bool = True):
        if self._is_connecting:
            self.set_connecting(False)
        if self._is_connected == connected and not self._anim.state() == QPropertyAnimation.Running:
            return
        self._is_connected = connected
        target = 1.0 if connected else 0.0

        if not animated:
            self._anim.stop()
            self.set_progress(target)
            return

        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(340 if connected else 280)
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def toggle(self, animated: bool = True):
        self.set_connected(not self._is_connected, animated=animated)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def _blend_color(self, c1: QColor, c2: QColor, factor: float) -> QColor:
        f = max(0.0, min(1.0, factor))
        r = int(c1.red() + (c2.red() - c1.red()) * f)
        g = int(c1.green() + (c2.green() - c1.green()) * f)
        b = int(c1.blue() + (c2.blue() - c1.blue()) * f)
        a = int(c1.alpha() + (c2.alpha() - c1.alpha()) * f)
        return QColor(r, g, b, a)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return

        cx = w / 2.0
        cy = h / 2.0
        density = 1.0
        stroke_width = 3.5 * density
        radius = min(cx, cy) - stroke_width - 4.0 * density

        p = self._progress

        # 1. Draw Disconnected State Fill & Crisp Edge Outline
        if p < 1.0:
            fill_alpha = int((1.0 - p) * 255)
            if fill_alpha > 0:
                fill_color = QColor(self._color_accent.red(), self._color_accent.green(), self._color_accent.blue(), min(255, fill_alpha))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(fill_color))
                painter.drawEllipse(QPointF(cx, cy), radius, radius)

            edge_alpha = int((1.0 - p) * 0x18)
            if edge_alpha > 0:
                edge_pen = QPen(QColor(255, 255, 255, min(255, edge_alpha)), 1.0)
                painter.setPen(edge_pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), radius, radius)

        # 2. Draw Connecting / Connected Ring Animation
        ring_rect = QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0)

        if p > 0.0:
            # Subtle translucent inner glow when connected (strictly inside ring)
            glow_alpha = int(p * 0x22)
            if glow_alpha > 0:
                glow_color = QColor(self._color_accent.red(), self._color_accent.green(), self._color_accent.blue(), min(255, glow_alpha))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(glow_color))
                painter.drawEllipse(QPointF(cx, cy), radius - stroke_width / 2.0, radius - stroke_width / 2.0)

            if not self._is_connecting and not self._is_connected:
                # Disconnecting state: outer ring stays solid and smoothly fades out
                # without breaking into spinning arcs or reversing.
                ring_alpha = int(p * 255)
                if ring_alpha > 0:
                    ring_pen = QPen(QColor(self._color_accent.red(), self._color_accent.green(), self._color_accent.blue(), min(255, ring_alpha)), stroke_width)
                    painter.setPen(ring_pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawEllipse(QPointF(cx, cy), radius, radius)
            elif p >= 0.988 and not self._is_connecting:
                # Completed solid ring
                ring_pen = QPen(self._color_accent, stroke_width)
                ring_pen.setCapStyle(Qt.RoundCap)
                painter.setPen(ring_pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), radius, radius)
            else:
                # Spinning / settling arc lines that transition from white to accent
                stroke_color = self._blend_color(QColor(255, 255, 255), self._color_accent, p)
                ring_pen = QPen(stroke_color, stroke_width)
                ring_pen.setCapStyle(Qt.RoundCap)
                painter.setPen(ring_pen)
                painter.setBrush(Qt.NoBrush)

                # Rotation angle
                if self._is_connecting:
                    rot_deg = self._spin_angle
                    arc_len = 115.0
                else:
                    rot_deg, arc_len = self._ring_frame(p)

                # Draw two complementary rotating arcs (Qt uses 1/16th of a degree)
                painter.drawArc(ring_rect, int(rot_deg * 16), int(arc_len * 16))
                painter.drawArc(ring_rect, int((rot_deg + 180.0) * 16), int(arc_len * 16))

        # 3. Draw Center Power Icon (I/O Symbol)
        self._draw_power_icon(painter, cx, cy, radius, p)

    def _draw_power_icon(self, painter: QPainter, cx: float, cy: float, radius: float, progress: float):
        icon_stroke = 3.8
        icon_radius = 24.0

        cur_color = self._blend_color(self._color_on_accent, self._color_accent, progress)
        icon_pen = QPen(cur_color, icon_stroke)
        icon_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(icon_pen)
        painter.setBrush(Qt.NoBrush)

        # Smooth, natural icon scaling without awkward freeze at progress 0.4
        if self._is_connected and not self._is_connecting:
            # Gentle settling pop on connect: 0.98 -> 1.02 -> 1.0
            t = min(1.0, max(0.0, (progress - 0.4) / 0.6))
            if t < 0.7:
                scale = 0.98 + 0.05 * (t / 0.7)
            else:
                scale = 1.03 - 0.03 * ((t - 0.7) / 0.3)
        elif not self._is_connected and not self._is_connecting and progress < 1.0:
            # Smooth scale on disconnect: 0.97 -> 1.0
            scale = 0.97 + 0.03 * (1.0 - progress)
        else:
            scale = 1.0

        painter.save()
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.translate(-cx, -cy)

        icon_rect = QRectF(cx - icon_radius, cy - icon_radius, icon_radius * 2.0, icon_radius * 2.0)

        # Draw open circular arc (top is open: 300 deg span, starting at -60 deg from 3 o'clock)
        # In Qt, 0 deg is 3 o'clock counter-clockwise.
        # Android drawArc: 0 is 3 o'clock clockwise.
        # In Android: -60 deg clockwise is 60 deg counter-clockwise.
        # Start angle -60 deg in Android = 60 deg in Qt.
        # Sweep angle 300 deg clockwise in Android = -300 deg in Qt.
        painter.drawArc(icon_rect, int(60 * 16), int(-300 * 16))

        # Draw top vertical power bar (I)
        bar_top = cy - icon_radius - 2.0
        bar_bottom = cy - 2.0
        painter.drawLine(QPointF(cx, bar_top), QPointF(cx, bar_bottom))

        painter.restore()
