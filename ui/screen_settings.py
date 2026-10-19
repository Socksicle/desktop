from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QComboBox, QSpinBox, QPushButton, QLineEdit, QSizePolicy,
    QColorDialog, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QPointF, QSize
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPalette

from .toggle_switch import AnimatedToggleSwitch
from .m3_scroll_area import M3ScrollArea
from .chevron_view import ChevronView
from .settings_options import (
    DNS_OPTIONS,
    DNS_PRESET_URLS,
    ENGINE_FALLBACK,
    ENGINE_IDS,
    ENGINE_OPTIONS,
    LANGUAGE_OPTIONS,
    PING_DISPLAY_OPTIONS,
    PING_METHOD_OPTIONS,
    PING_TIMEOUT_DEFAULT,
    PING_TIMEOUT_MAX,
    PING_TIMEOUT_MIN,
    RENDER_OPTIONS,
    THEME_SWATCHES,
    USER_AGENT_OPTIONS,
)
from utils.i18n import tr
from utils.startup_utils import is_autostart_enabled, set_autostart
from utils import icons
from utils.window_utils import compositing_available


class ThemeSwatchButton(QWidget):
    """Circular/squircle color swatch button for Material 3 theme palette selection."""
    clicked = Signal(str)

    def __init__(self, key: str, name: str, color_hex: str, is_dynamic: bool = False, parent=None):
        super().__init__(parent)
        self.key = key
        self.name = name
        self.color_hex = color_hex
        self.is_dynamic = is_dynamic
        self._selected = False
        self.setFixedSize(38, 38)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def set_color(self, color_hex: str):
        if self.color_hex != color_hex:
            self.color_hex = color_hex
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = h / 2.0
        r = 14.0

        if self.is_dynamic:
            p.setPen(Qt.NoPen)
            colors = [QColor("#4BA3E3"), QColor("#D0BCFF"), QColor("#4CAF50"), QColor("#E2703C")]
            for i, c in enumerate(colors):
                p.setBrush(QBrush(c))
                p.drawPie(int(cx - r), int(cy - r), int(r * 2), int(r * 2), i * 90 * 16, 90 * 16)
        elif self.key == "custom":
            if self._selected:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(self.color_hex)))
                p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
                lum = QColor(self.color_hex).lightnessF()
                plus_col = QColor(0, 0, 0) if lum > 0.6 else QColor(255, 255, 255)
                pen = QPen(plus_col, 2.0, Qt.SolidLine, Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(int(cx - 5), int(cy), int(cx + 5), int(cy))
                p.drawLine(int(cx), int(cy - 5), int(cx), int(cy + 5))
            else:
                p.setPen(QPen(QColor(147, 143, 153, 120), 1.5))
                p.setBrush(QBrush(QColor(255, 255, 255, 18)))
                p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
                pen = QPen(QColor("#E6E0E9"), 2.0, Qt.SolidLine, Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(int(cx - 5), int(cy), int(cx + 5), int(cy))
                p.drawLine(int(cx), int(cy - 5), int(cx), int(cy + 5))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(self.color_hex)))
            p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        if self._selected:
            ring_pen = QPen(QColor(255, 255, 255), 2.5)
            p.setPen(ring_pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(int(cx - (r + 3.0)), int(cy - (r + 3.0)), int((r + 3.0) * 2), int((r + 3.0) * 2))


class HubRow(QFrame):
    """Clickable menu row inside the Settings hub styled as an M3 Expressive segmented item."""
    clicked = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str, theme, parent=None,
                 position: str = "single", role: str = "primary"):
        super().__init__(parent)
        self.icon_name = icon_name
        self.title_text = title
        self.subtitle_text = subtitle
        self.theme = theme
        self.position = position
        self.role = role
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._setup_ui()
        self.apply_theme(theme)

    def _setup_ui(self):
        self.setMinimumHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(40, 40)
        self.icon_box.setAlignment(Qt.AlignCenter)
        self.icon_box.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.icon_box)

        text_lay = QVBoxLayout()
        text_lay.setSpacing(2)
        text_lay.setAlignment(Qt.AlignVCenter)

        self.lbl_title = QLabel(self.title_text)
        self.lbl_title.setStyleSheet("font-size: 13.5px; font-weight: bold; background: transparent;")
        self.lbl_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_lay.addWidget(self.lbl_title)

        self.lbl_sub = QLabel(self.subtitle_text)
        self.lbl_sub.setStyleSheet("font-size: 11.5px; background: transparent;")
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_lay.addWidget(self.lbl_sub)

        lay.addLayout(text_lay, 1)

        right_lay = QHBoxLayout()
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)
        right_lay.setAlignment(Qt.AlignVCenter)

        self.badge_lbl = QLabel()
        self.badge_lbl.setAlignment(Qt.AlignCenter)
        self.badge_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge_lbl.hide()
        right_lay.addWidget(self.badge_lbl, 0, Qt.AlignVCenter)

        self.chevron = ChevronView(size=18, parent=self)
        self.chevron.set_angle(-90)
        self.chevron.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        right_lay.addWidget(self.chevron, 0, Qt.AlignVCenter)

        self.lbl_arrow = self.chevron

        lay.addLayout(right_lay, 0)

    def set_subtitle(self, text: str):
        self.subtitle_text = text
        self.lbl_sub.setText(text)

    def set_badge(self, text: str, bg_color: str = "", text_color: str = ""):
        if not text:
            self.badge_lbl.hide()
            return
        self.badge_lbl.setText(text)
        accent = getattr(self.theme, "primary", "#D0BCFF")
        bg = bg_color or "rgba(255, 255, 255, 0.08)"
        fg = text_color or accent
        self.badge_lbl.setFixedHeight(18)
        self.badge_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                font-size: 10px;
                font-weight: 700;
                border-radius: 6px;
                padding: 1px 7px;
                border: 1px solid rgba(255, 255, 255, 0.10);
            }}
        """)
        self.badge_lbl.show()

    def hide_badge(self):
        self.badge_lbl.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def apply_theme(self, theme):
        self.theme = theme
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_c = getattr(theme, "on_surface", "#E6E0E9")
        muted_c = getattr(theme, "outline", "#938F99")

        # Corner radii for M3 Expressive segmented shapes:
        # 20px outer corners, 4px inner joints
        if self.position == "top":
            corner_css = "border-top-left-radius: 20px; border-top-right-radius: 20px; border-bottom-left-radius: 4px; border-bottom-right-radius: 4px;"
        elif self.position == "bottom":
            corner_css = "border-top-left-radius: 4px; border-top-right-radius: 4px; border-bottom-left-radius: 20px; border-bottom-right-radius: 20px;"
        elif self.position == "middle":
            corner_css = "border-radius: 4px;"
        else:
            corner_css = "border-radius: 20px;"

        if self.role == "tertiary":
            icon_bg = getattr(theme, "tertiary_container", "#492532")
            icon_fg = getattr(theme, "on_tertiary_container", "#FFD8E4")
        elif self.role == "secondary":
            icon_bg = getattr(theme, "secondary_container", "#332D41")
            icon_fg = getattr(theme, "on_secondary_container", "#E8DEF8")
        else:  # "primary"
            icon_bg = getattr(theme, "primary_container", "#381E72")
            icon_fg = getattr(theme, "on_primary_container", "#EADDFF")

        self.setStyleSheet(f"""
            HubRow {{
                background-color: {surface_high};
                {corner_css}
                border: none;
            }}
            HubRow:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                {corner_css}
            }}
        """)
        self.icon_box.setStyleSheet(f"""
            QLabel {{
                background-color: {icon_bg};
                border-radius: 12px;
                border: none;
            }}
        """)
        pix = icons.pixmap(self.icon_name, icon_fg, 20)
        if pix:
            self.icon_box.setPixmap(pix)

        self.lbl_title.setStyleSheet(f"color: {text_c}; font-size: 13.5px; font-weight: bold; background: transparent;")
        self.lbl_sub.setStyleSheet(f"color: {muted_c}; font-size: 11.5px; background: transparent;")
        self.chevron.set_color(muted_c)


class OptionCard(QFrame):
    """Segmented option card with radio circle indicator matching Android buildPingOptionGroup."""
    clicked = Signal(int)

    def __init__(self, index: int, title: str, subtitle: str, theme, parent=None):
        super().__init__(parent)
        self.index = index
        self.title_text = title
        self.subtitle_text = subtitle
        self.theme = theme
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._setup_ui()
        self.apply_theme(theme)

    def _setup_ui(self):
        self.setMinimumHeight(54)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        self.radio_indicator = QWidget()
        self.radio_indicator.setFixedSize(20, 20)
        self.radio_indicator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.radio_indicator.paintEvent = self._paint_radio
        lay.addWidget(self.radio_indicator)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.setAlignment(Qt.AlignVCenter)

        self.title_lbl = QLabel(self.title_text)
        self.title_lbl.setStyleSheet("font-size: 13.5px; font-weight: bold; background: transparent;")
        self.title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_box.addWidget(self.title_lbl)

        if self.subtitle_text:
            self.sub_lbl = QLabel(self.subtitle_text)
            self.sub_lbl.setStyleSheet("font-size: 11px; background: transparent;")
            self.sub_lbl.setWordWrap(True)
            self.sub_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            text_box.addWidget(self.sub_lbl)
        else:
            self.sub_lbl = None

        lay.addLayout(text_box, 1)

    def _paint_radio(self, event):
        p = QPainter(self.radio_indicator)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = 10.0, 10.0
        accent = QColor(getattr(self.theme, "primary", "#D0BCFF"))
        muted = QColor(getattr(self.theme, "outline", "#938F99"))
        if self._selected:
            p.setPen(QPen(accent, 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 8.0, 8.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(accent))
            p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)
        else:
            p.setPen(QPen(QColor(muted.red(), muted.green(), muted.blue(), 140), 1.8))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 8.0, 8.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
            event.accept()
        else:
            super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        self._selected = bool(selected)
        self.apply_theme(self.theme)

    def apply_theme(self, theme):
        self.theme = theme
        accent = getattr(theme, "primary", "#D0BCFF")
        text_c = getattr(theme, "on_surface", "#E6E0E9")
        muted_c = getattr(theme, "outline", "#938F99")
        surface_c = getattr(theme, "surface_container_high", "#241F32")

        if self._selected:
            q_accent = QColor(accent)
            bg_rgba = f"rgba({q_accent.red()}, {q_accent.green()}, {q_accent.blue()}, 0.12)"
            self.setStyleSheet(f"""
                OptionCard {{
                    background-color: {bg_rgba};
                    border: 2px solid {accent};
                    border-radius: 14px;
                }}
            """)
            self.title_lbl.setStyleSheet(f"color: {accent}; font-size: 13.5px; font-weight: bold; background: transparent;")
        else:
            self.setStyleSheet(f"""
                OptionCard {{
                    background-color: {surface_c};
                    border: 1px solid rgba(147, 143, 153, 0.22);
                    border-radius: 14px;
                }}
                OptionCard:hover {{
                    border: 1px solid rgba(147, 143, 153, 0.4);
                }}
            """)
            self.title_lbl.setStyleSheet(f"color: {text_c}; font-size: 13.5px; font-weight: bold; background: transparent;")

        if self.sub_lbl:
            self.sub_lbl.setStyleSheet(f"color: {muted_c}; font-size: 11px; background: transparent;")
        self.radio_indicator.update()


class OptionCardGroup(QWidget):
    """Container managing single selection among OptionCards."""
    selectionChanged = Signal(int)

    def __init__(self, options: list[tuple[str, str]], initial_index: int = 0, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._cards = []
        self._current_index = initial_index

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        for idx, (title, sub) in enumerate(options):
            card = OptionCard(idx, title, sub, theme=self.theme, parent=self)
            card.clicked.connect(self._on_card_clicked)
            card.set_selected(idx == initial_index)
            lay.addWidget(card)
            self._cards.append(card)

    def _on_card_clicked(self, idx: int):
        if self._current_index != idx:
            self.set_selected_index(idx)
            self.selectionChanged.emit(idx)

    def set_selected_index(self, idx: int):
        self._current_index = idx
        for i, card in enumerate(self._cards):
            card.set_selected(i == idx)

    def current_index(self) -> int:
        return self._current_index

    def apply_theme(self, theme):
        self.theme = theme
        for card in self._cards:
            card.apply_theme(theme)


class SettingsScreen(QWidget):
    """Full-page Material 3 Settings view matching Android MainSettingsDelegate.kt."""

    themeChanged = Signal(str)
    settingChanged = Signal(str, object)
    showLogsRequested = Signal()

    def __init__(self, settings_dict: dict, theme, parent=None):
        super().__init__(parent)
        self.settings = settings_dict
        self.theme = theme
        self._swatches = []
        self._active_section = None
        # Sub-screen sections are built lazily on first open (P2 memory):
        # the hub shows ~5 rows, while all five sections together hold
        # hundreds of widgets. Builders fill these in; until then they stay
        # None and apply_theme() skips them.
        self._built_sections = set()
        self.sw_amoled = None
        self.sw_invert_scroll = None
        self.sw_show_server_card = None
        self.sw_show_ip_card = None
        self.sw_show_flags = None
        self.render_combo = None
        self.lbl_render_hint = None
        self.engine_combo = None
        self.sw_tun = None
        self.sw_killswitch = None
        self.sw_hy2_insecure = None
        self.sw_block_udp = None
        self.dns_combo = None
        self.edit_custom_dns = None
        self.ua_combo = None
        self.group_ping_methods = None
        self.group_ping_display = None
        self.stepper_box = None
        self.lbl_timeout_val = None
        self.edit_ping_url = None
        self.sw_autostart = None
        self.sw_tray = None
        self.sw_auto_update_subs = None
        self.lang_combo = None
        self.lbl_lang_hint = None
        self.port_spin = None
        self.btn_logs = None
        self._setup_ui()
        self.apply_theme(theme)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.header_widget = QWidget(self)
        header_lay = QHBoxLayout(self.header_widget)
        header_lay.setContentsMargins(18, 14, 18, 6)
        header_lay.setSpacing(10)

        self.btn_back = QPushButton(self.header_widget)
        self.btn_back.setFixedSize(36, 36)
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.clicked.connect(self.show_hub)
        self.btn_back.hide()
        header_lay.addWidget(self.btn_back)

        self.title_label = QLabel(tr("nav.settings"), self.header_widget)
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold; background: transparent;")
        header_lay.addWidget(self.title_label)
        header_lay.addStretch()

        root_layout.addWidget(self.header_widget)

        self.scroll_area = M3ScrollArea(self, invert_scroll=self.settings.get("invert_scroll", False))

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(16, 6, 16, 110)
        self.content_layout.setSpacing(12)

        self.card_hub = QFrame()
        self.card_hub.setObjectName("hubContainer")
        self.card_hub.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        hub_layout = QVBoxLayout(self.card_hub)
        hub_layout.setContentsMargins(0, 0, 0, 0)
        hub_layout.setSpacing(14)

        # 1. Semantic Group: Network (Connection, Ping, Subs)
        self.lbl_group_network = QLabel(tr("set.group_network"))
        hub_layout.addWidget(self.lbl_group_network)

        self.group_network = QFrame(self.card_hub)
        self.group_network.setStyleSheet("background: transparent; border: none;")
        net_layout = QVBoxLayout(self.group_network)
        net_layout.setContentsMargins(0, 0, 0, 0)
        net_layout.setSpacing(3)

        self.hub_row_connection = HubRow(
            "hub_connection", tr("set.hub_connection"),
            tr("set.hub_connection_sub"),
            self.theme, self.group_network,
            position="top", role="primary"
        )
        self.hub_row_connection.clicked.connect(lambda: self.open_section("connection", tr("set.hub_connection")))
        net_layout.addWidget(self.hub_row_connection)

        self.hub_row_ping = HubRow(
            "hub_ping", tr("set.hub_ping"),
            tr("set.hub_ping_sub"),
            self.theme, self.group_network,
            position="middle", role="primary"
        )
        self.hub_row_ping.clicked.connect(lambda: self.open_section("ping", tr("set.hub_ping")))
        net_layout.addWidget(self.hub_row_ping)

        self.hub_row_subs = HubRow(
            "hub_subs", tr("set.hub_subs"),
            tr("set.hub_subs_sub"),
            self.theme, self.group_network,
            position="bottom", role="primary"
        )
        self.hub_row_subs.clicked.connect(lambda: self.open_section("subs", tr("set.hub_subs")))
        net_layout.addWidget(self.hub_row_subs)

        hub_layout.addWidget(self.group_network)

        # 2. Semantic Group: Application (Appearance, System)
        self.lbl_group_app = QLabel(tr("set.group_app"))
        hub_layout.addWidget(self.lbl_group_app)

        self.group_app = QFrame(self.card_hub)
        self.group_app.setStyleSheet("background: transparent; border: none;")
        app_layout = QVBoxLayout(self.group_app)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.setSpacing(3)

        self.hub_row_appearance = HubRow(
            "hub_appearance", tr("set.hub_appearance"),
            tr("set.hub_appearance_sub"),
            self.theme, self.group_app,
            position="top", role="tertiary"
        )
        self.hub_row_appearance.clicked.connect(lambda: self.open_section("appearance", tr("set.hub_appearance")))
        app_layout.addWidget(self.hub_row_appearance)

        self.hub_row_system = HubRow(
            "tab_settings", tr("set.hub_system"),
            tr("set.hub_system_sub"),
            self.theme, self.group_app,
            position="bottom", role="secondary"
        )
        self.hub_row_system.clicked.connect(lambda: self.open_section("system", tr("set.hub_system")))
        app_layout.addWidget(self.hub_row_system)

        hub_layout.addWidget(self.group_app)

        self._update_hub_live_values()

        self.content_layout.addWidget(self.card_hub)

        self.card_appearance = self._create_card(tr("set.hub_appearance"))
        self.content_layout.addWidget(self.card_appearance)

        self.card_connection = self._create_card(tr("set.hub_connection"))
        self.content_layout.addWidget(self.card_connection)

        self.card_ping = self._create_card(tr("set.hub_ping"))
        self.content_layout.addWidget(self.card_ping)

        self.card_subs = self._create_card(tr("set.hub_subs"))
        self.content_layout.addWidget(self.card_subs)

        self.card_system = self._create_card(tr("set.hub_system"))
        self.content_layout.addWidget(self.card_system)

        self.content_layout.addStretch(1)

        self.scroll_area.setWidget(content)
        root_layout.addWidget(self.scroll_area)

        self.show_hub()

    def _ensure_section(self, section_name: str):
        """Build a sub-screen section on first open and theme it."""
        if section_name in self._built_sections:
            return
        builder = getattr(self, f"_build_{section_name}", None)
        if builder is None:
            return
        builder()
        self._built_sections.add(section_name)
        self.apply_theme(self.theme)

    def _build_appearance(self):
        app_layout = QVBoxLayout(self.card_appearance)
        app_layout.setContentsMargins(16, 16, 16, 16)
        app_layout.setSpacing(14)

        swatch_layout = QHBoxLayout()
        swatch_layout.setSpacing(10)

        current_theme = self.settings.get("theme_preset", "dynamic")
        for idx, (key, label_key, color, is_dyn) in enumerate(THEME_SWATCHES):
            if key == "custom":
                color = self._custom_color_hex(color)
            btn = ThemeSwatchButton(key, tr(label_key), color, is_dynamic=is_dyn)
            btn.set_selected(key == current_theme)
            btn.clicked.connect(self._on_swatch_clicked)
            swatch_layout.addWidget(btn)
            self._swatches.append(btn)
            if idx == 0:
                div = QFrame()
                div.setFrameShape(QFrame.VLine)
                div.setFixedWidth(2)
                div.setFixedHeight(22)
                div.setStyleSheet("background-color: rgba(147, 143, 153, 0.3); border: none;")
                swatch_layout.addWidget(div)

        swatch_layout.addStretch()
        app_layout.addLayout(swatch_layout)

        self.sw_amoled = self._add_toggle_row(
            app_layout,
            "AMOLED Pure Dark",
            tr("set.amoled_sub"),
            self.settings.get("theme_preset") == "amoled"
        )
        self.sw_amoled.toggled.connect(self._on_amoled_toggled)

        self.sw_invert_scroll = self._add_toggle_row(
            app_layout,
            tr("set.invert"),
            tr("set.invert_sub"),
            self.settings.get("invert_scroll", False)
        )
        self.sw_invert_scroll.toggled.connect(self._on_invert_scroll_toggled)

        self.sw_show_server_card = self._add_toggle_row(
            app_layout,
            tr("set.card_server"),
            tr("set.card_server_sub"),
            self.settings.get("show_active_server_card", False),
            visible=False,
        )
        self.sw_show_server_card.toggled.connect(lambda s: self._emit_setting("show_active_server_card", s))

        self.sw_show_ip_card = self._add_toggle_row(
            app_layout,
            tr("set.card_ip"),
            tr("set.card_ip_sub"),
            self.settings.get("show_ip_card", False),
            visible=False,
        )
        self.sw_show_ip_card.toggled.connect(lambda s: self._emit_setting("show_ip_card", s))

        self.sw_show_flags = self._add_toggle_row(
            app_layout,
            tr("set.flags"),
            tr("set.flags_sub"),
            self.settings.get("show_flags", False),
            visible=False,
        )
        self.sw_show_flags.toggled.connect(lambda s: self._emit_setting("show_flags", s))

        self.render_combo = self._add_combo_row(
            app_layout,
            tr("set.render_label"),
            [(val, tr(label_key)) for val, label_key in RENDER_OPTIONS],
            self.settings.get("render_backend", "auto")
        )
        self.render_combo.currentIndexChanged.connect(self._on_render_changed)

        self.lbl_render_hint = QLabel(tr("set.restart_hint"))
        self.lbl_render_hint.setStyleSheet("font-size: 11px; color: #938F99; background: transparent;")
        self.lbl_render_hint.setWordWrap(True)
        app_layout.addWidget(self.lbl_render_hint)

        self.lang_combo = self._add_combo_row(
            app_layout,
            tr("set.language_label"),
            LANGUAGE_OPTIONS,
            self.settings.get("language", "ru")
        )
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)

        self.lbl_lang_hint = QLabel(tr("set.restart_hint"))
        self.lbl_lang_hint.setStyleSheet("font-size: 11px; color: #938F99; background: transparent;")
        self.lbl_lang_hint.setWordWrap(True)
        app_layout.addWidget(self.lbl_lang_hint)

    def _build_connection(self):
        conn_layout = QVBoxLayout(self.card_connection)
        conn_layout.setContentsMargins(16, 16, 16, 16)
        conn_layout.setSpacing(14)

        # Fall back to default engine if stored id is unknown or removed
        if self.settings.get("engine", ENGINE_FALLBACK) not in ENGINE_IDS:
            self.settings["engine"] = ENGINE_FALLBACK
        self.engine_combo = self._add_combo_row(
            conn_layout,
            tr("set.engine_label"),
            [(val, tr(label_key)) for val, label_key in ENGINE_OPTIONS],
            self.settings.get("engine", ENGINE_FALLBACK)
        )
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)

        self.sw_tun = self._add_toggle_row(
            conn_layout,
            tr("set.tun"),
            tr("set.tun_sub"),
            self.settings.get("tun_mode", False)
        )
        self.sw_tun.toggled.connect(lambda s: self._emit_setting("tun_mode", s))

        self.sw_killswitch = self._add_toggle_row(
            conn_layout,
            tr("set.kill"),
            tr("set.kill_sub"),
            self.settings.get("killswitch", False)
        )
        self.sw_killswitch.toggled.connect(lambda s: self._emit_setting("killswitch", s))

        port_row = QHBoxLayout()
        port_row.setSpacing(12)
        port_lbl_box = QVBoxLayout()
        port_lbl_box.setSpacing(2)
        port_title = QLabel(tr("set.port_title"))
        port_title.setStyleSheet("font-size: 13.5px; font-weight: bold; background: transparent;")
        port_desc = QLabel(tr("set.port_sub"))
        port_desc.setStyleSheet("font-size: 11px; color: #938F99; background: transparent;")
        port_desc.setWordWrap(True)
        port_lbl_box.addWidget(port_title)
        port_lbl_box.addWidget(port_desc)
        port_row.addLayout(port_lbl_box, 1)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(self.settings.get("local_port", 1080)))
        self.port_spin.setFixedWidth(90)
        self.port_spin.setFixedHeight(38)
        self.port_spin.valueChanged.connect(lambda v: self._emit_setting("local_port", v))
        port_row.addWidget(self.port_spin)
        conn_layout.addLayout(port_row)

        self.sw_hy2_insecure = self._add_toggle_row(
            conn_layout,
            tr("set.hy2insecure"),
            tr("set.hy2insecure_sub"),
            bool(self.settings.get("hy2_insecure", False))
        )
        self.sw_hy2_insecure.toggled.connect(lambda s: self._emit_setting("hy2_insecure", bool(s)))

        self.sw_block_udp = self._add_toggle_row(
            conn_layout,
            tr("set.blockudp"),
            tr("set.blockudp_sub"),
            bool(self.settings.get("block_udp", False))
        )
        self.sw_block_udp.toggled.connect(lambda s: self._emit_setting("block_udp", bool(s)))

        current_provider = self.settings.get("dns_provider", "system")

        self.dns_combo = self._add_combo_row(
            conn_layout,
            tr("set.dns_label"),
            [(val, tr(label_key)) for val, label_key in DNS_OPTIONS],
            current_provider
        )
        self.dns_combo.currentIndexChanged.connect(self._on_dns_changed)

        self.edit_custom_dns = QLineEdit()
        self.edit_custom_dns.setPlaceholderText(tr("set.dns_custom_ph"))
        self._apply_placeholder_color(self.edit_custom_dns)
        self.edit_custom_dns.setFixedHeight(42)
        self.edit_custom_dns.setText(self.settings.get("custom_dns_manual", "") or (self.settings.get("custom_dns", "") if current_provider == "custom" else ""))
        self.edit_custom_dns.textChanged.connect(self._on_custom_dns_changed)
        self.edit_custom_dns.setVisible(current_provider == "custom")
        conn_layout.addWidget(self.edit_custom_dns)

    def _build_subs(self):
        subs_layout = QVBoxLayout(self.card_subs)
        subs_layout.setContentsMargins(16, 16, 16, 16)
        subs_layout.setSpacing(14)

        self.sw_auto_update_subs = self._add_toggle_row(
            subs_layout,
            tr("set.autoupdate"),
            tr("set.autoupdate_sub"),
            self.settings.get("auto_update_subs", True)
        )
        self.sw_auto_update_subs.toggled.connect(lambda s: self._emit_setting("auto_update_subs", s))

        self.ua_combo = self._add_combo_row(
            subs_layout,
            tr("set.ua_label"),
            USER_AGENT_OPTIONS,
            self.settings.get("user_agent_key", "socksicle")
        )
        self.ua_combo.currentIndexChanged.connect(self._on_ua_changed)

    def _build_ping(self):
        ping_layout = QVBoxLayout(self.card_ping)
        ping_layout.setContentsMargins(16, 16, 16, 16)
        ping_layout.setSpacing(14)

        pm_header_row = QHBoxLayout()
        lbl_pm = QLabel(tr("set.ping_method_label"))
        lbl_pm.setObjectName("subSectionLabel")
        pm_header_row.addWidget(lbl_pm, 1)

        btn_thp_help = QPushButton("?")
        btn_thp_help.setFixedSize(22, 22)
        btn_thp_help.setCursor(Qt.PointingHandCursor)
        btn_thp_help.setToolTip(tr("ping.thp_help_title"))
        btn_thp_help.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.08);
                color: {self.theme.primary};
                border-radius: 11px;
                font-weight: bold;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.16);
            }}
        """)
        btn_thp_help.clicked.connect(self._show_thp_help)
        pm_header_row.addWidget(btn_thp_help)
        ping_layout.addLayout(pm_header_row)

        pm_list = [(tr(title_key), tr(sub_key)) for _, title_key, sub_key in PING_METHOD_OPTIONS]
        pm_keys = [key for key, _, _ in PING_METHOD_OPTIONS]
        cur_pm = self.settings.get("ping_method", "thp")
        init_pm_idx = pm_keys.index(cur_pm) if cur_pm in pm_keys else 0

        self.group_ping_methods = OptionCardGroup(pm_list, init_pm_idx, theme=self.theme, parent=self.card_ping)
        self.group_ping_methods.selectionChanged.connect(
            lambda idx: self._emit_setting("ping_method", pm_keys[idx])
        )
        ping_layout.addWidget(self.group_ping_methods)

        lbl_pd = QLabel(tr("set.ping_display_label"))
        lbl_pd.setObjectName("subSectionLabel")
        ping_layout.addWidget(lbl_pd)

        pd_list = [(tr(title_key), tr(sub_key)) for title_key, sub_key in PING_DISPLAY_OPTIONS]
        init_pd_idx = int(self.settings.get("ping_display", 0)) % len(PING_DISPLAY_OPTIONS)
        self.group_ping_display = OptionCardGroup(pd_list, init_pd_idx, theme=self.theme, parent=self.card_ping)
        self.group_ping_display.selectionChanged.connect(
            lambda idx: self._emit_setting("ping_display", idx)
        )
        ping_layout.addWidget(self.group_ping_display)

        lbl_to = QLabel(tr("set.ping_timeout_label"))
        lbl_to.setObjectName("subSectionLabel")
        ping_layout.addWidget(lbl_to)

        self.stepper_box = QFrame()
        self.stepper_box.setObjectName("stepperBox")
        self.stepper_box.setFixedHeight(44)
        sb_lay = QHBoxLayout(self.stepper_box)
        sb_lay.setContentsMargins(14, 4, 10, 4)
        sb_lay.setSpacing(6)

        self.lbl_timeout_val = QLabel(tr("set.timeout_sec").format(
            n=int(self.settings.get('ping_timeout', PING_TIMEOUT_DEFAULT))))
        self.lbl_timeout_val.setStyleSheet("font-size: 13.5px; font-weight: bold; background: transparent; border: none;")
        sb_lay.addWidget(self.lbl_timeout_val, 1)

        btn_minus = QPushButton("−")
        btn_minus.setFixedSize(34, 34)
        btn_minus.setCursor(Qt.PointingHandCursor)
        btn_minus.clicked.connect(self._decrease_timeout)
        sb_lay.addWidget(btn_minus)

        btn_plus = QPushButton("+")
        btn_plus.setFixedSize(34, 34)
        btn_plus.setCursor(Qt.PointingHandCursor)
        btn_plus.clicked.connect(self._increase_timeout)
        sb_lay.addWidget(btn_plus)

        ping_layout.addWidget(self.stepper_box)

        lbl_url = QLabel(tr("set.ping_url_label"))
        lbl_url.setObjectName("subSectionLabel")
        ping_layout.addWidget(lbl_url)

        self.edit_ping_url = QLineEdit()
        self.edit_ping_url.setFixedHeight(42)
        self.edit_ping_url.setPlaceholderText("https://connectivitycheck.gstatic.com/generate_204")
        self._apply_placeholder_color(self.edit_ping_url)
        self.edit_ping_url.setText(self.settings.get("ping_url", "https://connectivitycheck.gstatic.com/generate_204"))
        self.edit_ping_url.textChanged.connect(lambda u: self._emit_setting("ping_url", u.strip()))
        ping_layout.addWidget(self.edit_ping_url)

    def _build_system(self):
        sys_layout = QVBoxLayout(self.card_system)
        sys_layout.setContentsMargins(16, 16, 16, 16)
        sys_layout.setSpacing(14)

        self.sw_autostart = self._add_toggle_row(
            sys_layout,
            tr("set.autostart"),
            tr("set.autostart_sub"),
            is_autostart_enabled()
        )
        self.sw_autostart.toggled.connect(self._on_autostart_toggled)

        self.sw_tray = self._add_toggle_row(
            sys_layout,
            tr("set.tray"),
            tr("set.tray_sub"),
            self.settings.get("minimize_to_tray", True)
        )
        self.sw_tray.toggled.connect(lambda s: self._emit_setting("minimize_to_tray", s))

        self.btn_logs = QPushButton(tr("set.logs_btn"))
        self.btn_logs.setCursor(Qt.PointingHandCursor)
        self.btn_logs.setFixedHeight(42)
        self.btn_logs.clicked.connect(self.showLogsRequested.emit)
        self._set_logs_icon()
        sys_layout.addWidget(self.btn_logs)

    def show_hub(self):
        """Show main Settings Hub menu and hide sub-screen section cards."""
        self._active_section = None
        self.header_widget.hide()
        self.btn_back.hide()
        self.title_label.setText(tr("nav.settings"))
        self._update_hub_live_values()
        self.card_hub.show()
        self.card_appearance.hide()
        self.card_connection.hide()
        self.card_ping.hide()
        self.card_subs.hide()
        self.card_system.hide()
        self.scroll_area.verticalScrollBar().setValue(0)

    def open_section(self, section_name: str, title_text: str):
        """Open a standalone sub-screen section matching Android openSettingsSection."""
        self._ensure_section(section_name)
        self._active_section = section_name
        self.header_widget.show()
        self.btn_back.show()
        self.title_label.setText(title_text)
        self.card_hub.hide()
        self.card_appearance.setVisible(section_name == "appearance")
        self.card_connection.setVisible(section_name == "connection")
        self.card_ping.setVisible(section_name == "ping")
        self.card_subs.setVisible(section_name == "subs")
        self.card_system.setVisible(section_name == "system")
        self.scroll_area.verticalScrollBar().setValue(0)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self._active_section is not None:
                self.show_hub()
                event.accept()
                return
        super().keyPressEvent(event)

    def _create_card(self, header_title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        return card

    def _add_toggle_row(self, parent_layout: QVBoxLayout, title: str, subtitle: str, checked: bool, visible: bool = True) -> AnimatedToggleSwitch:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        lbl_box = QVBoxLayout()
        lbl_box.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 13.5px; font-weight: bold; background: transparent;")
        t_lbl.setWordWrap(True)
        lbl_box.addWidget(t_lbl)

        if subtitle:
            s_lbl = QLabel(subtitle)
            s_lbl.setStyleSheet("font-size: 11px; color: #938F99; background: transparent;")
            s_lbl.setWordWrap(True)
            lbl_box.addWidget(s_lbl)

        row.addLayout(lbl_box, 1)

        sw = AnimatedToggleSwitch(theme=self.theme)
        sw.setChecked(checked)
        sw.setFixedWidth(52)
        sw.row_widget = row_widget
        row.addWidget(sw, 0, Qt.AlignVCenter)

        if not visible:
            row_widget.setVisible(False)

        parent_layout.addWidget(row_widget)
        return sw

    @staticmethod
    def _arrow_rule(name: str, color_hex: str, size: int = 20) -> str:
        """Stylesheet rule body for arrow subcontrols (combo/spin arrows).

        Qt always paints subcontrol borders as rectangles, so the CSS border
        triangle only works as a fallback when the SVG icon is unavailable.
        """
        path = icons.svg_file(name, color_hex)
        if path is not None:
            return f'image: url("{path}"); width: {size}px; height: {size}px;'
        half = size // 4
        base = (
            f"width: 0px; height: 0px;"
            f" border-left: {half}px solid transparent;"
            f" border-right: {half}px solid transparent;"
        )
        edge = "border-bottom" if name.endswith("_up") else "border-top"
        return f"{base} {edge}: {half}px solid {color_hex};"

    @staticmethod
    def _combobox_stylesheet(theme) -> str:
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        surface_highest = getattr(theme, "surface_container_highest", "#2E283E")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")
        return f"""
            QComboBox {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(147, 143, 153, 0.25);
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 13px;
                outline: none;
            }}
            QComboBox:hover {{
                border: 1px solid rgba(255, 255, 255, 0.22);
                background-color: {surface_highest};
            }}
            QComboBox:focus, QComboBox:on {{
                border: 1.5px solid {accent_hex};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 32px;
                border: none;
            }}
            QComboBox::down-arrow {{
                {SettingsScreen._arrow_rule("arrow_drop_down", muted_hex)}
                margin-right: 8px;
            }}
            QComboBox::down-arrow:on {{
                {SettingsScreen._arrow_rule("arrow_drop_down", accent_hex)}
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 14px;
                padding: 6px;
                outline: none;
                selection-background-color: transparent;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 36px;
                padding: 4px 12px;
                border-radius: 8px;
                color: {text_hex};
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: {accent_hex}33;
                color: {accent_hex};
                font-weight: 600;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QComboBox QAbstractItemView QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 6px 2px 6px 0;
            }}
            QComboBox QAbstractItemView QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.2);
                border-radius: 3px;
                min-height: 20px;
            }}
            QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 0.35);
            }}
            QComboBox QAbstractItemView QScrollBar::add-line:vertical,
            QComboBox QAbstractItemView QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QComboBox QAbstractItemView QScrollBar::add-page:vertical,
            QComboBox QAbstractItemView QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """

    def _style_combobox(self, combo: QComboBox, theme=None):
        t = theme or self.theme
        if t:
            combo.setStyleSheet(self._combobox_stylesheet(t))

    def _add_combo_row(self, parent_layout: QVBoxLayout, title: str, items: list[tuple[str, str]], current_val: str) -> QComboBox:
        row = QVBoxLayout()
        row.setSpacing(6)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 12.5px; font-weight: 600; color: #938F99; background: transparent;")
        row.addWidget(t_lbl)

        combo = QComboBox()
        combo.setFixedHeight(42)
        combo.setCursor(Qt.PointingHandCursor)

        view = combo.view()
        view.setMouseTracking(True)
        view.viewport().setAttribute(Qt.WA_Hover, True)
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

        popup_win = view.window()
        popup_win.setAttribute(Qt.WA_TranslucentBackground, compositing_available())
        popup_win.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)

        self._style_combobox(combo)

        for val, label in items:
            combo.addItem(label, val)

        idx = combo.findData(current_val)
        if idx >= 0:
            combo.setCurrentIndex(idx)

        row.addWidget(combo)
        parent_layout.addLayout(row)
        return combo

    def _custom_color_hex(self, fallback: str) -> str:
        cand = QColor(str(self.settings.get("theme_custom_color", "")))
        return cand.name() if cand.isValid() else fallback

    def _pick_custom_color(self) -> bool:
        """Open the system color dialog and store the choice; False on cancel."""
        current = QColor(self._custom_color_hex("#B388FF"))
        picked = QColorDialog.getColor(current, self, tr("opt.theme.custom"))
        if not picked.isValid():
            return False
        hex_code = picked.name()
        self.settings["theme_custom_color"] = hex_code
        for btn in self._swatches:
            if btn.key == "custom":
                btn.set_color(hex_code)
        return True

    def _on_swatch_clicked(self, key: str):
        if key == "custom" and not self._pick_custom_color():
            current = self.settings.get("theme_preset", "dynamic")
            for btn in self._swatches:
                btn.set_selected(btn.key == current)
            return
        for btn in self._swatches:
            btn.set_selected(btn.key == key)
        self.sw_amoled.setChecked(False)
        self.settings["theme_preset"] = key
        self.themeChanged.emit(key)

    def _on_amoled_toggled(self, checked: bool):
        if checked:
            for btn in self._swatches:
                btn.set_selected(False)
            self.settings["theme_preset"] = "amoled"
            self.themeChanged.emit("amoled")
        else:
            prev = "dynamic"
            self.settings["theme_preset"] = prev
            for btn in self._swatches:
                btn.set_selected(btn.key == prev)
            self.themeChanged.emit(prev)

    def _on_invert_scroll_toggled(self, checked: bool):
        self.scroll_area.set_invert_scroll(checked)
        self._emit_setting("invert_scroll", checked)

    def _on_engine_changed(self, idx: int):
        val = self.engine_combo.itemData(idx)
        if val:
            self._emit_setting("engine", val)

    def _on_render_changed(self, idx: int):
        val = self.render_combo.itemData(idx)
        if val:
            from utils.startup_utils import normalize_render_backend
            self._emit_setting("render_backend", normalize_render_backend(val))

    def _on_language_changed(self, idx: int):
        val = self.lang_combo.itemData(idx)
        if val:
            from utils.i18n import set_language
            self._emit_setting("language", set_language(val))

    def _on_dns_changed(self, idx: int):
        val = self.dns_combo.itemData(idx)
        if not val:
            return
        is_custom = (val == "custom")
        self.edit_custom_dns.setVisible(is_custom)
        self._emit_setting("dns_provider", val)
        if is_custom:
            target_dns = self.edit_custom_dns.text().strip()
            self._emit_setting("custom_dns", target_dns)
            self._emit_setting("custom_dns_manual", target_dns)
        else:
            url = DNS_PRESET_URLS.get(val, "")
            self._emit_setting("custom_dns", url)

    def _on_custom_dns_changed(self, text: str):
        val = text.strip()
        self._emit_setting("custom_dns_manual", val)
        if self.dns_combo.currentData() == "custom":
            self._emit_setting("custom_dns", val)

    def _on_ua_changed(self, idx: int):
        val = self.ua_combo.itemData(idx)
        if val:
            self._emit_setting("user_agent_key", val)

    def _show_thp_help(self):
        from ui.message_box import MessageBox
        MessageBox.information(self, tr("ping.thp_help_title"), tr("ping.thp_help_text"), theme=self.theme)

    def _decrease_timeout(self):
        curr = int(self.settings.get("ping_timeout", PING_TIMEOUT_DEFAULT))
        new_val = max(PING_TIMEOUT_MIN, curr - 1)
        self.lbl_timeout_val.setText(tr("set.timeout_sec").format(n=new_val))
        self._emit_setting("ping_timeout", new_val)

    def _increase_timeout(self):
        curr = int(self.settings.get("ping_timeout", PING_TIMEOUT_DEFAULT))
        new_val = min(PING_TIMEOUT_MAX, curr + 1)
        self.lbl_timeout_val.setText(tr("set.timeout_sec").format(n=new_val))
        self._emit_setting("ping_timeout", new_val)

    def _on_autostart_toggled(self, checked: bool):
        set_autostart(checked)
        self._emit_setting("auto_start", checked)

    def set_tun_mode(self, on: bool):
        """Sync the switch when TUN is toggled on the main screen.

        The Connection section (and sw_tun) may not be built yet; when it is
        built later it reads the (already updated) tun_mode from settings.
        """
        if self.sw_tun is not None:
            self.sw_tun.setChecked(bool(on))

    def _update_hub_live_values(self):
        pm = self.settings.get("ping_method", "thp")
        pm_names = {
            "thp": "THP",
            "tcp_connect": "TCP-Connect",
            "http_head": "TCP-HEAD",
        }
        method_str = pm_names.get(pm, pm.upper())
        sec = int(self.settings.get("ping_timeout", PING_TIMEOUT_DEFAULT))
        pd_idx = int(self.settings.get("ping_display", 0)) % len(PING_DISPLAY_OPTIONS)
        timeout_str = tr("set.timeout_sec").format(n=sec)
        display_str = tr(PING_DISPLAY_OPTIONS[pd_idx][0])
        self.hub_row_ping.set_subtitle(f"{method_str}, {timeout_str}, {display_str}")

        port = self.settings.get("local_port", self.settings.get("socks5_port", 1080))
        engine_str = self.settings.get("engine", ENGINE_FALLBACK)
        parts = [str(port)]
        if self.settings.get("killswitch", False):
            parts.append(tr("set.kill"))
        parts.append(engine_str)
        self.hub_row_connection.set_subtitle(", ".join(parts))

        tun_on = bool(self.settings.get("tun_mode", False))
        if tun_on:
            accent = getattr(self.theme, "primary", "#D0BCFF")
            self.hub_row_connection.set_badge("TUN", bg_color="rgba(255, 255, 255, 0.08)", text_color=accent)
        else:
            self.hub_row_connection.hide_badge()

    def _emit_setting(self, key: str, val):
        self.settings[key] = val
        self.settingChanged.emit(key, val)
        self._update_hub_live_values()

    def _set_logs_icon(self):
        text_hex = getattr(self.theme, "on_surface", "#E6E0E9")
        ic = icons.icon("description", text_hex, 20)
        if ic is not None:
            self.btn_logs.setIcon(ic)
            self.btn_logs.setIconSize(QSize(20, 20))

    @staticmethod
    def _apply_placeholder_color(edit, color: str = "#938F99"):
        pal = edit.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(color))
        edit.setPalette(pal)

    def apply_theme(self, theme):
        self.theme = theme
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        surface_hex = getattr(theme, "surface_container", getattr(theme, "surface", "#1A1624"))
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        surface_highest = getattr(theme, "surface_container_highest", "#2E283E")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")

        card_qss = f"""
            QFrame#hubContainer {{
                background-color: transparent;
                border: none;
            }}
            QFrame#settingsCard {{
                background-color: {surface_hex};
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }}
            QLabel {{
                color: {text_hex};
            }}
            QLabel#subSectionLabel {{
                color: {muted_hex};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                margin-top: 4px;
            }}
{self._combobox_stylesheet(theme)}
            QLineEdit {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(147, 143, 153, 0.25);
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 13px;
                outline: none;
            }}
            QLineEdit:hover {{
                border: 1px solid rgba(255, 255, 255, 0.22);
                background-color: {surface_highest};
            }}
            QLineEdit:focus {{
                border: 1.5px solid {accent_hex};
            }}
            QSpinBox {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(147, 143, 153, 0.25);
                border-radius: 12px;
                padding: 3px 10px 3px 14px;
                font-size: 13px;
                outline: none;
            }}
            QSpinBox:hover {{
                border: 1px solid rgba(255, 255, 255, 0.22);
                background-color: {surface_highest};
            }}
            QSpinBox:focus {{
                border: 1.5px solid {accent_hex};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                subcontrol-origin: border;
                background: transparent;
                border: none;
                width: 26px;
            }}
            QSpinBox::up-button {{
                subcontrol-position: top right;
                height: 17px;
            }}
            QSpinBox::down-button {{
                subcontrol-position: bottom right;
                height: 17px;
            }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
                background: rgba(255, 255, 255, 0.08);
                border-radius: 6px;
            }}
            QSpinBox::up-arrow {{
                {self._arrow_rule("arrow_drop_up", muted_hex, 18)}
            }}
            QSpinBox::down-arrow {{
                {self._arrow_rule("arrow_drop_down", muted_hex, 18)}
            }}
            QPushButton {{
                background-color: {surface_high};
                color: {accent_hex};
                border: 1px solid rgba(147, 143, 153, 0.2);
                border-radius: 12px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
        """

        self.setStyleSheet(card_qss)
        self.scroll_area.apply_theme(theme)

        self.title_label.setStyleSheet(f"color: {text_hex}; font-size: 20px; font-weight: bold; background: transparent;")

        back_pix = icons.pixmap("arrow_back", text_hex, 18)
        if back_pix:
            self.btn_back.setIcon(back_pix)
            self.btn_back.setText("")
        else:
            self.btn_back.setText("‹")
        self.btn_back.setStyleSheet(f"""
            QPushButton {{
                background-color: {surface_high};
                color: {text_hex};
                border: none;
                border-radius: 18px;
                font-size: 18px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.12);
                border: none;
            }}
        """)

        # Stepper box styling (ping section may not be built yet)
        if self.stepper_box is not None:
            self.stepper_box.setStyleSheet(f"""
                QFrame#stepperBox {{
                    background-color: {surface_high};
                    border: 1px solid rgba(147, 143, 153, 0.22);
                    border-radius: 12px;
                }}
                QLabel {{
                    border: none;
                    background: transparent;
                }}
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    color: {accent_hex};
                    font-size: 18px;
                    font-weight: bold;
                    border-radius: 17px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                }}
            """)

        if hasattr(self, "btn_logs") and self.btn_logs is not None:
            self._set_logs_icon()

        if hasattr(self, "lbl_group_network"):
            self.lbl_group_network.setStyleSheet(
                f"color: {muted_hex}; font-size: 11px; font-weight: bold; letter-spacing: 0.5px; background: transparent; padding-left: 6px;"
            )
        if hasattr(self, "lbl_group_app"):
            self.lbl_group_app.setStyleSheet(
                f"color: {muted_hex}; font-size: 11px; font-weight: bold; letter-spacing: 0.5px; background: transparent; padding-left: 6px;"
            )

        self._update_hub_live_values()

        for row in [self.hub_row_appearance, self.hub_row_connection, self.hub_row_ping, self.hub_row_subs, self.hub_row_system]:
            row.apply_theme(theme)

        for group in [self.group_ping_methods, self.group_ping_display]:
            if group is not None:
                group.apply_theme(theme)

        for sw in [self.sw_amoled, self.sw_invert_scroll, self.sw_show_server_card, self.sw_show_ip_card, self.sw_show_flags, self.sw_tun, self.sw_killswitch, self.sw_hy2_insecure, self.sw_block_udp, self.sw_autostart, self.sw_tray, self.sw_auto_update_subs]:
            if sw is not None:
                sw.apply_theme(theme)

        for cb in self.findChildren(QComboBox):
            self._style_combobox(cb, theme)
