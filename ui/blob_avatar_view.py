import math
import random
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QPainter, QColor, QBrush, QPainterPath, QFont


class BlobAvatarView(QWidget):
    """About-screen avatar: an accent blob drawn from one of 5 molds
    (pebble, squircle, arch, clover, flower) with the app glyph on top.
    
    Ported 1-to-1 from mobile BlobAvatarView.kt.
    Shape is picked at random and reshuffled on tap with scale bounce animation!
    """

    PEBBLE = 0
    SQUIRCLE = 1
    ARCH = 2
    CLOVER = 3
    FLOWER = 4
    SHAPE_COUNT = 5

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self._shape = random.randint(0, self.SHAPE_COUNT - 1)
        self._shape_bag = []
        self._scale = 1.0

        self._color_accent = QColor("#D0BCFF")
        self._color_on_accent = QColor("#381E72")

        self.setFixedSize(112, 112)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"scale_factor", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)

        if theme:
            self.apply_theme(theme)

    def get_scale_factor(self) -> float:
        return self._scale

    def set_scale_factor(self, s: float):
        self._scale = float(s)
        self.update()

    scale_factor = Property(float, get_scale_factor, set_scale_factor)

    def apply_theme(self, theme):
        self.theme = theme
        self._color_accent = QColor(getattr(theme, "primary", "#D0BCFF"))
        self._color_on_accent = QColor(getattr(theme, "on_primary", "#381E72"))
        self.update()

    def shuffle(self) -> int:
        if not self._shape_bag:
            self._shape_bag = [s for s in range(self.SHAPE_COUNT) if s != self._shape]
            random.shuffle(self._shape_bag)
        self._shape = self._shape_bag.pop(0)

        # Trigger bounce animation
        self._anim.stop()
        self._anim.setStartValue(0.85)
        self._anim.setEndValue(1.0)
        self._anim.start()

        self.update()
        return self._shape

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.shuffle()
            event.accept()
        else:
            super().mousePressEvent(event)

    def _build_path(self, shape: int, size: float) -> QPainterPath:
        inset = size * 0.04
        s = size - inset * 2.0

        path = QPainterPath()
        if shape == self.SQUIRCLE:
            path.addRoundedRect(QRectF(inset, inset, s, s), s * 0.30, s * 0.30)

        elif shape == self.ARCH:
            r = s / 2.0
            path.moveTo(inset, inset + s)
            path.lineTo(inset, inset + r)
            path.arcTo(QRectF(inset, inset, s, s), 180.0, -180.0)
            path.lineTo(inset + s, inset + s)
            path.closeSubpath()
            path.translate(0.0, -s * 0.045)

        elif shape == self.CLOVER:
            radii = [0.50, 0.35, 0.50, 0.35, 0.50, 0.35, 0.50, 0.35]
            path = self._smooth_blob(s, radii, phase_deg=0.0, inset=inset)

        elif shape == self.FLOWER:
            radii = [0.50 if i % 2 == 0 else 0.44 for i in range(16)]
            path = self._smooth_blob(s, radii, phase_deg=0.0, inset=inset)

        else:  # PEBBLE
            radii = [0.52, 0.38, 0.48, 0.35, 0.45, 0.37, 0.50, 0.41]
            path = self._smooth_blob(s, radii, phase_deg=-30.0, inset=inset)

        return path

    def _smooth_blob(self, size: float, radii: list[float], phase_deg: float, inset: float) -> QPainterPath:
        n = len(radii)
        cx = inset + size / 2.0
        cy = inset + size / 2.0
        samples = 96
        pts = []

        for i in range(samples):
            angle = (i / samples) * 360.0
            pos = (angle / 360.0) * n
            i0 = int(pos) % n
            i1 = (i0 + 1) % n
            f = pos - int(pos)
            blend = 0.5 - 0.5 * math.cos(f * math.pi)
            r = (radii[i0] + (radii[i1] - radii[i0]) * blend) * size
            rad = math.radians(angle + phase_deg)
            pts.append([cx + r * math.cos(rad), cy + r * math.sin(rad)])

        # Centroid alignment
        a2 = 0.0
        sx = 0.0
        sy = 0.0
        for i in range(samples):
            p = pts[i]
            q = pts[(i + 1) % samples]
            cross = p[0] * q[1] - q[0] * p[1]
            a2 += cross
            sx += (p[0] + q[0]) * cross
            sy += (p[1] + q[1]) * cross

        if a2 != 0.0:
            dx = cx - sx / (3.0 * a2)
            dy = cy - sy / (3.0 * a2)
            for p in pts:
                p[0] += dx
                p[1] += dy

        path = QPainterPath()
        mx = (pts[-1][0] + pts[0][0]) / 2.0
        my = (pts[-1][1] + pts[0][1]) / 2.0
        path.moveTo(mx, my)

        for i in range(samples):
            p = pts[i]
            q = pts[(i + 1) % samples]
            mx = (p[0] + q[0]) / 2.0
            my = (p[1] + q[1]) / 2.0
            path.quadTo(p[0], p[1], mx, my)

        path.closeSubpath()
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return

        size = min(w, h)
        cx = w / 2.0
        cy = h / 2.0

        painter.save()
        painter.translate(cx, cy)
        painter.scale(self._scale, self._scale)
        painter.translate(-cx, -cy)

        painter.translate((w - size) / 2.0, (h - size) / 2.0)
        path = self._build_path(self._shape, size)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._color_accent))
        painter.drawPath(path)

        # Draw glyph in the middle (launcher foreground icon matching mobile)
        glyph_size = int(size * 0.70)
        from utils.icons import pixmap
        pm = pixmap("launcher_foreground", self._color_on_accent.name(), glyph_size)
        if pm and not pm.isNull():
            gx = (size - glyph_size) / 2.0
            gy = (size - glyph_size) / 2.0
            painter.drawPixmap(int(gx), int(gy), pm)
        else:
            font = QFont("Segoe UI", int(size * 0.38), QFont.Bold)
            painter.setFont(font)
            painter.setPen(self._color_on_accent)
            painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "🧦")

        painter.restore()
