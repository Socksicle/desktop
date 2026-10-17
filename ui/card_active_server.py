from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal
from .signal_bars_view import SignalBarsView
from .chevron_view import ChevronView
from .elided_label import ElidedLabel
from utils.i18n import tr


class CardActiveServer(QFrame):
    """Active target server card (flag, name, latency, protocol)."""

    clicked = Signal()

    def __init__(self, parent=None, theme=None, ping_display_mode: int = 0):
        super().__init__(parent)
        self.theme = theme
        self._ping_display_mode = int(ping_display_mode)
        self._show_flags = True
        self._last_flag = "🌐"
        self._last_ms = None
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui()
        if theme:
            self.apply_theme(theme)

    def _setup_ui(self):
        self.setObjectName("cardActiveServer")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        # Flag squircle
        self.flag_frame = QFrame()
        self.flag_frame.setFixedSize(36, 36)
        flag_layout = QVBoxLayout(self.flag_frame)
        flag_layout.setContentsMargins(0, 0, 0, 0)
        self.flag_label = QLabel("🌐")
        self.flag_label.setAlignment(Qt.AlignCenter)
        # Optical centering: color-emoji glyphs render ~1px low in this
        # frame (measured); compensate with padding.
        self.flag_label.setStyleSheet(
            "font-size: 20px; background: transparent; padding-bottom: 2px;")
        flag_layout.addWidget(self.flag_label)
        layout.addWidget(self.flag_frame)

        # Text column
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.label_target = ElidedLabel(tr("card.selected"))
        self.label_target.setStyleSheet("font-size: 10px; font-weight: bold; letter-spacing: 0.8px;")
        text_layout.addWidget(self.label_target)

        self.server_name_label = ElidedLabel(tr("card.no_server"))
        self.server_name_label.setStyleSheet("font-size: 14.5px; font-weight: bold;")
        text_layout.addWidget(self.server_name_label)

        self.server_host_label = ElidedLabel(tr("card.tap_select"))
        self.server_host_label.setStyleSheet("font-size: 11px;")
        text_layout.addWidget(self.server_host_label)

        layout.addLayout(text_layout, 1)

        # Right badges
        badges_layout = QHBoxLayout()
        badges_layout.setContentsMargins(0, 0, 0, 0)
        badges_layout.setSpacing(6)

        self.ping_bars = SignalBarsView(theme=self.theme)
        badges_layout.addWidget(self.ping_bars)

        self.ping_label = QLabel("-")
        self.ping_label.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #4CAF50; background: transparent;")
        badges_layout.addWidget(self.ping_label)
        self._update_ping_visibility()

        self.proto_badge = QLabel("-")
        self.proto_badge.setAlignment(Qt.AlignCenter)
        self.proto_badge.setStyleSheet(
            "font-size: 10px; font-weight: bold; padding: 2px 7px; border-radius: 8px;")
        badges_layout.addWidget(self.proto_badge)

        self.chevron_label = ChevronView(16)
        self.chevron_label.set_angle(-90)
        badges_layout.addWidget(self.chevron_label)

        layout.addLayout(badges_layout)

    def set_ping_display_mode(self, mode: int):
        self._ping_display_mode = int(mode)
        self._update_ping_visibility()

    def _update_ping_visibility(self):
        # 0 = numbers only, 1 = signal bars only, 2 = both
        mode = self._ping_display_mode
        show_numbers = (mode != 1)
        show_bars = (mode != 0)

        self.ping_label.setVisible(show_numbers)
        if self._last_ms is not None and self._last_ms > 0:
            self.ping_bars.setVisible(show_bars)
        else:
            self.ping_bars.setVisible(False)

    def apply_theme(self, theme):
        self.theme = theme
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        surface_hex = getattr(theme, "surface_container", getattr(theme, "surface", "#1A1624"))
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")
        dim_hex = getattr(theme, "on_surface_variant", "#CAC4D0")

        self.setStyleSheet(f"""
            QFrame#cardActiveServer {{
                background-color: {surface_hex};
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.125);
            }}
            QFrame#cardActiveServer:hover {{
                background-color: {surface_high};
                border: 1px solid rgba(255, 255, 255, 0.2);
            }}
        """)

        self.flag_frame.setStyleSheet("""
            background-color: rgba(255, 255, 255, 12);
            border-radius: 12px;
            border: 1px solid rgba(147, 143, 153, 40);
        """)

        self.label_target.setStyleSheet(
            f"color: {accent_hex}; font-size: 10px; font-weight: bold; letter-spacing: 0.8px; background: transparent;")
        self.server_name_label.setStyleSheet(
            f"color: {text_hex}; font-size: 14.5px; font-weight: bold; background: transparent;")
        self.server_host_label.setStyleSheet(
            f"color: {muted_hex}; font-size: 11px; background: transparent;")
        self.chevron_label.set_color(muted_hex)

        self.proto_badge.setStyleSheet(f"""
            background-color: {surface_high};
            color: {dim_hex};
            font-size: 10px;
            font-weight: bold;
            padding: 3px 7px;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.086);
        """)

        self.ping_bars.apply_theme(theme)
        if self._last_ms is not None:
            self.set_latency(self._last_ms)

    def set_server(self, server: dict | None):
        if not server:
            self.flag_label.setText("🌐")
            self._last_flag = "🌐"
            self.server_name_label.setText(tr("card.no_server"))
            self.server_host_label.setText(tr("card.tap_select"))
            self.proto_badge.setText("-")
            self.proto_badge.setVisible(False)
            self.ping_label.setText("-")
            self.ping_bars.set_level(0)
            self._last_ms = None
            self._update_ping_visibility()
            return

        name = server.get("name") or server.get("remarks") or tr("item.unknown_proto")
        host = server.get("server") or server.get("host") or ""
        port = server.get("server_port") or server.get("port") or ""
        host_str = f"{host}:{port}" if host and port else (host or "")

        flag = server.get("flag") or server.get("flag_emoji") or "🌐"
        proto = (server.get("type") or server.get("proto") or "SS").upper()

        self._last_flag = flag
        self._render_flag()
        self.server_name_label.setText(name)
        self.server_host_label.setText(host_str or tr("card.ready"))
        self.proto_badge.setText(proto[:6])
        self.proto_badge.setVisible(True)

    def set_show_flags(self, show: bool):
        """Show country flags or fall back to the globe (mobile parity)."""
        self._show_flags = bool(show)
        self._render_flag()

    def _render_flag(self):
        # Mobile updateConnectionCard: emoji when showFlags, "🌐" otherwise.
        self.flag_label.setText(self._last_flag if self._show_flags else "🌐")

    def set_latency(self, ms: float | None):
        theme = self.theme
        ok_hex = getattr(theme, "success", "#4CAF50")
        warn_hex = getattr(theme, "warning", "#FFC107")
        poor_hex = getattr(theme, "error", "#F44336")

        self._last_ms = ms
        self._update_ping_visibility()

        if ms is None or ms == 0:
            self.ping_label.setText("-")
            self.ping_label.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {ok_hex}; background: transparent;")
            self.ping_bars.set_level(0)
            return

        if ms < 0:
            self.ping_label.setText(tr("item.unreachable"))
            self.ping_label.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {poor_hex}; background: transparent;")
            self.ping_bars.set_latency(-1)
            return

        self.ping_label.setText(f"{ms:.0f} ms")
        self.ping_bars.set_latency(ms)
        if ms < 150:
            color = ok_hex
        elif ms < 300:
            color = warn_hex
        else:
            color = poor_hex
        self.ping_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {color}; background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)
