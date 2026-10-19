"""Interactive expandable configuration badge for server items.

Animates horizontally between compact and expanded states using fluid_easing()
to display technical proxy configuration details without causing vertical layout shifts.
"""
from typing import Optional, Tuple
from PySide6.QtCore import Qt, Signal, Property, QPropertyAnimation, QRectF
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget

from .anim_utils import fluid_easing


_TCP_LIKE = ("", "tcp", "none")


def _security_label(security: str) -> str:
    return "Reality" if security == "reality" else ("TLS" if security == "tls" else "")


def build_badge_texts(server) -> Tuple[str, str]:
    """Return (compact_text, expanded_text) for a server configuration.

    Expanded segments follow the config chain:
    PROTOCOL · TRANSPORT (non-tcp) · TLS/Reality · extras · ADDR:PORT.
    """
    if not server:
        return "", ""

    proto = getattr(server, "protocol", None)
    proto_val = getattr(proto, "value", str(proto or "")).lower()
    port = getattr(server, "port", None)

    if proto_val == "vless":
        security = (getattr(server, "security", "") or "none").lower()
        transport = (getattr(server, "transport", "") or "tcp").lower()
        flow = (getattr(server, "flow", "") or "").lower()
        tcp_like = transport in _TCP_LIKE

        exp = ["VLESS"]
        if not tcp_like:
            exp.append(transport.upper())
        sec_label = _security_label(security)
        if sec_label:
            exp.append(sec_label)
        if "vision" in flow:
            exp.append("Vision")
        if port:
            exp.append(f"TCP:{port}" if tcp_like else f":{port}")
        compact = f"VLESS · {sec_label}" if sec_label else "VLESS"
        return compact, " · ".join(exp)

    if proto_val == "vmess":
        security = (getattr(server, "security", "") or "none").lower()
        transport = (getattr(server, "transport", "") or "tcp").lower()
        cipher = (getattr(server, "vmess_security", "") or "").lower()
        tcp_like = transport in _TCP_LIKE

        exp = ["VMESS"]
        if not tcp_like:
            exp.append(transport.upper())
        sec_label = _security_label(security)
        if sec_label:
            exp.append(sec_label)
        if cipher and cipher != "auto":
            exp.append(cipher.upper())
        if port:
            exp.append(f"TCP:{port}" if tcp_like else f":{port}")
        return "VMESS", " · ".join(exp)

    if proto_val == "trojan":
        security = (getattr(server, "security", "") or "tls").lower()
        transport = (getattr(server, "transport", "") or "tcp").lower()
        tcp_like = transport in _TCP_LIKE

        exp = ["TROJAN"]
        if not tcp_like:
            exp.append(transport.upper())
        sec_label = _security_label(security)
        if sec_label:
            exp.append(sec_label)
        if port:
            exp.append(f"TCP:{port}" if tcp_like else f":{port}")
        return "TROJAN", " · ".join(exp)

    if proto_val in ("hysteria2", "hy2"):
        obfs = (getattr(server, "obfs", "") or "").lower()
        cc = (getattr(server, "congestion_control", "bbr") or "").lower()

        exp = ["HY2"]
        if obfs:
            exp.append(obfs.upper())
        if cc and cc != "bbr":
            exp.append(cc.upper())
        if port:
            exp.append(f"UDP:{port}")
        return "HY2", " · ".join(exp)

    if proto_val == "tuic":
        cc = (getattr(server, "congestion_control", "bbr") or "").lower()

        exp = ["TUIC"]
        if cc and cc != "bbr":
            exp.append(cc.upper())
        if port:
            exp.append(f"UDP:{port}")
        return "TUIC", " · ".join(exp)

    # Shadowsocks: always TCP; distinguishing modules are the SIP003 plugin
    # and the cipher method (canonical lowercase, as it appears in links).
    plugin_raw = (getattr(server, "plugin", "") or "").strip()
    method = (getattr(server, "method", "") or "").strip()

    exp = ["SS"]
    if plugin_raw:
        plugin_name = plugin_raw.split(";")[0].replace("\\", "/").split("/")[-1].replace(".exe", "")
        exp.append(plugin_name)
    elif method:
        exp.append(method)
    if port:
        exp.append(f"TCP:{port}")
    return "SS", " · ".join(exp)


class ConfigTagBadge(QWidget):
    """Clickable protocol badge that smoothly expands on click."""

    widthChanged = Signal(int)

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._compact_text = ""
        self._expanded_text = ""
        self._expanded = False
        self._hovered = False
        self._progress = 0.0

        self._font = QFont()
        self._font.setPointSize(9)
        self._font.setWeight(QFont.DemiBold)

        self._compact_width = 0
        self._expanded_width = 0

        self.setFixedHeight(20)
        self.setAttribute(Qt.WA_Hover, True)

        self._anim = QPropertyAnimation(self, b"progress")
        self._anim.setDuration(240)
        self._anim.setEasingCurve(fluid_easing())

    def getProgress(self) -> float:
        return self._progress

    def setProgress(self, val: float):
        self._progress = max(0.0, min(1.0, float(val)))
        current_w = int(self._compact_width + (self._expanded_width - self._compact_width) * self._progress)
        self.setFixedWidth(current_w)
        self.widthChanged.emit(current_w)
        self.update()

    progress = Property(float, getProgress, setProgress)

    def is_expanded(self) -> bool:
        return self._expanded

    def text(self) -> str:
        return self._expanded_text if self._expanded else self._compact_text

    def set_texts(self, compact: str, expanded: str = ""):
        compact_str = str(compact or "")
        expanded_str = str(expanded or compact_str)
        if self._compact_text == compact_str and self._expanded_text == expanded_str:
            return

        self._anim.stop()
        self._compact_text = compact_str
        self._expanded_text = expanded_str
        self._expanded = False
        self._progress = 0.0
        self._recalculate_widths()
        self.setFixedWidth(self._compact_width)
        self.update()



    def setStyleSheet(self, style: str):
        super().setStyleSheet(style)
        self.update()

    def apply_theme(self, theme):
        self.theme = theme
        self.update()

    def _recalculate_widths(self):
        fm = QFontMetrics(self._font)
        h_padding = 12
        self._compact_width = (fm.horizontalAdvance(self._compact_text) + h_padding) if self._compact_text else 0
        self._expanded_width = (fm.horizontalAdvance(self._expanded_text) + h_padding) if self._expanded_text else self._compact_width

    def toggle_expanded(self):
        if not self._compact_text or self._compact_text == self._expanded_text:
            return
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool, animated: bool = True):
        if not self._compact_text or self._compact_text == self._expanded_text:
            return
        self._expanded = bool(expanded)
        target = 1.0 if self._expanded else 0.0

        if not animated:
            self._anim.stop()
            self.setProgress(target)
            return

        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event):
        self._hovered = True
        if self._compact_text and self._compact_text != self._expanded_text:
            self.setCursor(Qt.PointingHandCursor)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._compact_text and self._compact_text != self._expanded_text:
                self.toggle_expanded()
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        if not self._compact_text and not self._expanded_text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        rect = QRectF(0.5, 0.5, max(0.0, w - 1.0), max(0.0, h - 1.0))

        # Pill background
        bg_alpha = 28 if self._hovered else 16
        if self._expanded:
            bg_alpha += 10
        painter.setBrush(QColor(255, 255, 255, bg_alpha))

        # Border
        if self._hovered or self._expanded:
            border_alpha = 50 if self._hovered else 32
            painter.setPen(QColor(255, 255, 255, border_alpha))
        else:
            painter.setPen(Qt.NoPen)

        painter.drawRoundedRect(rect, 6.0, 6.0)

        # Text with crossfade
        painter.setFont(self._font)
        text_color_hex = getattr(self.theme, "on_surface_variant", "#CAC4D0") if self.theme else "#CAC4D0"
        base_color = QColor(text_color_hex)

        text_rect = self.rect()

        if self._progress <= 0.001:
            painter.setPen(base_color)
            painter.drawText(text_rect, Qt.AlignCenter, self._compact_text)
        elif self._progress >= 0.999:
            painter.setPen(base_color)
            painter.drawText(text_rect, Qt.AlignCenter, self._expanded_text)
        else:
            if self._progress < 0.5:
                alpha = int(255 * (1.0 - self._progress * 2.0))
                col = QColor(base_color.red(), base_color.green(), base_color.blue(), alpha)
                painter.setPen(col)
                painter.drawText(text_rect, Qt.AlignCenter, self._compact_text)
            else:
                alpha = int(255 * ((self._progress - 0.5) * 2.0))
                col = QColor(base_color.red(), base_color.green(), base_color.blue(), alpha)
                painter.setPen(col)
                painter.drawText(text_rect, Qt.AlignCenter, self._expanded_text)

        painter.end()
