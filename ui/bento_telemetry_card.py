import time
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer

from utils.i18n import tr


class BentoTelemetryCard(QFrame):
    """Bento Telemetry Card (Connection Details & Session Timer).
    
    Ported 1-to-1 from mobile activity_main.xml (cardConnectionDetails).
    Shows:
    - Left Column: Tunnel Endpoint IP and Country/City
    - Subtle vertical separator
    - Right Column: Session timer with clock icon and digital monospace counter HH:MM:SS
    """

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self._connected_start_time = None
        self._last_timer_text = "00:00:00"
        self._show_flags = True
        self._last_flag = ""
        self._last_country = ""
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_timer_display)

        self._setup_ui()
        if theme:
            self.apply_theme(theme)

    def _setup_ui(self):
        self.setObjectName("cardConnectionDetails")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)

        # Left Column: Tunnel IP & Location
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        self.label_ip = QLabel(tr("telem.ip"))
        self.label_ip.setStyleSheet("font-size: 9.5px; font-weight: bold; letter-spacing: 0.8px;")
        left_layout.addWidget(self.label_ip)

        self.ip_text = QLabel("-")
        self.ip_text.setStyleSheet("font-size: 14.5px; font-weight: bold;")
        left_layout.addWidget(self.ip_text)

        self.country_text = QLabel("-")
        self.country_text.setStyleSheet("font-size: 11.5px;")
        left_layout.addWidget(self.country_text)

        layout.addLayout(left_layout, 1.1)

        # Subtle vertical separator (1x40dp, #15FFFFFF, margins 4/12)
        self.separator = QFrame()
        self.separator.setFixedWidth(1)
        self.separator.setFixedHeight(40)
        sep_box = QHBoxLayout()
        sep_box.setContentsMargins(4, 0, 12, 0)
        sep_box.addWidget(self.separator, 0, Qt.AlignVCenter)
        layout.addLayout(sep_box)
        layout.setSpacing(0)

        # Right Column: Session Timer Chip
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.timer_chip = QFrame()
        chip_layout = QHBoxLayout(self.timer_chip)
        chip_layout.setContentsMargins(8, 3, 8, 3)
        chip_layout.setSpacing(4)

        self.timer_icon = QLabel("⏱️")
        self.timer_icon.setStyleSheet("font-size: 11px; background: transparent;")
        chip_layout.addWidget(self.timer_icon)

        self.timer_label = QLabel("00:00:00")
        self.timer_label.setTextFormat(Qt.RichText)
        self.timer_label.setStyleSheet("font-size: 12px; font-weight: bold; font-family: monospace; background: transparent;")
        chip_layout.addWidget(self.timer_label)

        right_layout.addWidget(self.timer_chip, 0, Qt.AlignRight)
        layout.addLayout(right_layout, 1)

    def apply_theme(self, theme):
        self.theme = theme
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        surface_hex = getattr(theme, "surface_container", getattr(theme, "surface", "#1A1624"))
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")
        dim_hex = getattr(theme, "on_surface_variant", "#CAC4D0")
        self._accent_hex = accent_hex
        self._muted_hex = muted_hex

        self.setStyleSheet(f"""
            QFrame#cardConnectionDetails {{
                background-color: {surface_hex};
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.07);
            }}
        """)

        self.separator.setStyleSheet("background-color: rgba(255, 255, 255, 0.082); border: none;")

        self.label_ip.setStyleSheet(
            f"color: {muted_hex}; font-size: 9.5px; font-weight: bold; letter-spacing: 0.8px; background: transparent;")
        self.ip_text.setStyleSheet(
            f"color: {text_hex}; font-size: 14.5px; font-weight: bold; background: transparent;")
        self.country_text.setStyleSheet(
            f"color: {dim_hex}; font-size: 11.5px; background: transparent;")

        self.timer_chip.setStyleSheet(f"""
            background-color: {surface_high};
            border-radius: 10px;
            border: none;
        """)

        self.timer_label.setStyleSheet(
            f"color: {accent_hex}; font-size: 12px; font-weight: bold; font-family: monospace; background: transparent;")
        self._set_timer_text(self._last_timer_text or "00:00:00")

    def _set_timer_text(self, text: str):
        """Android renders digits in accent and ':' in muted (timerH1..timerS2)."""
        self._last_timer_text = text
        accent = getattr(self, "_accent_hex", "#D0BCFF")
        muted = getattr(self, "_muted_hex", "#938F99")
        parts = []
        for ch in text:
            color = muted if ch == ":" else accent
            parts.append(f'<span style="color: {color};">{ch}</span>')
        self.timer_label.setText("".join(parts))

    def set_geo_info(self, ip: str, country: str, flag: str = ""):
        self.ip_text.setText(ip or "-")
        self._last_country = country or ""
        self._last_flag = flag or ""
        self._render_location()

    def set_show_flags(self, show: bool):
        """Show the country flag next to the location or hide it."""
        self._show_flags = bool(show)
        self._render_location()

    def _render_location(self):
        flag = self._last_flag if self._show_flags else ""
        loc = f"{flag} {self._last_country}".strip() if (flag or self._last_country) else "-"
        self.country_text.setText(loc)

    def start_session(self):
        self._connected_start_time = time.time()
        self._set_timer_text("00:00:00")
        self._timer.start()

    def stop_session(self):
        self._timer.stop()
        self._connected_start_time = None
        self._set_timer_text("00:00:00")

    def _update_timer_display(self):
        if not self._connected_start_time:
            return
        elapsed = int(time.time() - self._connected_start_time)
        hrs = elapsed // 3600
        mins = (elapsed % 3600) // 60
        secs = elapsed % 60
        self._set_timer_text(f"{hrs:02d}:{mins:02d}:{secs:02d}")
