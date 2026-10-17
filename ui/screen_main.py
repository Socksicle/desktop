from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSizePolicy, QSpacerItem
)
from PySide6.QtCore import Qt, Signal, QPoint

from .hero_power_button import HeroPowerButton
from .card_active_server import CardActiveServer
from .bento_telemetry_card import BentoTelemetryCard
from .server_filter_bar import ServerFilterBar
from .server_list_panel import ServerListPanel
from .m3_scroll_area import M3ScrollArea
from utils.i18n import tr


class ScreenMain(QWidget):
    """Main (Home) screen - 1:1 port of Android screenMain (activity_main.xml).

    One QScrollArea holds the hero block (padding: sides 20, top 8 + hero
    mt14, bottom 14 + first group card top margin 6 = 20) and the server
    list (side 20, bottom 96 for the floating taskbar), so scrolling
    collapses the hero just like the AppBarLayout in the app. The header
    bar lives above the scroll area (main_window layout), so scroll minimum
    is the true top: nothing hides behind the header.

    Hero vertical rhythm (layoutMainHero children):
      power button mt14, status mt14, status hint mt3 (only while
      connecting), active server card mt18, telemetry card mt10, filter bar
      mt20.
    """

    connectClicked = Signal()
    filterChanged = Signal(int)

    def __init__(self, theme, parent=None, invert_scroll: bool = False, ping_display_mode: int = 0):
        super().__init__(parent)
        self.theme = theme
        self._invert_scroll = bool(invert_scroll)
        self._ping_display_mode = int(ping_display_mode)
        self._status_state = {"text": tr("status.disconnected"), "connected": False,
                              "connecting": False, "error": False}
        self._setup_ui()
        self.set_status(tr("status.disconnected"), False)

    def _setup_ui(self):
        self._show_active_card = True
        self._show_ip_card = True
        self._telemetry_connected = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = M3ScrollArea(self, invert_scroll=self._invert_scroll)
        root.addWidget(self.scroll)

        self.content = QWidget()
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.scroll.setWidget(self.content)

        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(20, 8, 20, 0)  # hero insets: side 20, top 8 (+ hero mt14)
        layout.setSpacing(0)

        hero = QVBoxLayout()
        hero.setContentsMargins(0, 0, 0, 14)  # 14 + first card top 6 = 20
        hero.setSpacing(0)
        self._hero_layout = hero

        self.hero_btn = HeroPowerButton(theme=self.theme)
        self.hero_btn.clicked.connect(self.connectClicked.emit)
        hero.addSpacing(14)
        hero.addWidget(self.hero_btn, 0, Qt.AlignCenter)

        status_box = QVBoxLayout()
        status_box.setContentsMargins(0, 0, 0, 0)
        status_box.setSpacing(3)
        self.status_label = QLabel(tr("status.disconnected"))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setTextFormat(Qt.PlainText)
        status_box.addWidget(self.status_label, 0, Qt.AlignCenter)

        self.status_hint = QLabel(tr("status.tunnel"))
        self.status_hint.setAlignment(Qt.AlignCenter)
        self.status_hint.setTextFormat(Qt.PlainText)
        self.status_hint.hide()
        status_box.addWidget(self.status_hint, 0, Qt.AlignCenter)

        status_wrapper = QWidget()
        sw_lay = QVBoxLayout(status_wrapper)
        sw_lay.setContentsMargins(0, 0, 0, 0)
        sw_lay.addLayout(status_box)
        hero.addSpacing(14)
        hero.addWidget(status_wrapper)

        self.card_active = CardActiveServer(theme=self.theme, ping_display_mode=self._ping_display_mode)
        self._active_spacer = QSpacerItem(1, 18, QSizePolicy.Minimum, QSizePolicy.Fixed)
        hero.addItem(self._active_spacer)
        hero.addWidget(self.card_active)

        self.card_telemetry = BentoTelemetryCard(theme=self.theme)
        self._telemetry_spacer = QSpacerItem(1, 10, QSizePolicy.Minimum, QSizePolicy.Fixed)
        hero.addItem(self._telemetry_spacer)
        hero.addWidget(self.card_telemetry)
        self.card_telemetry.hide()
        self._telemetry_spacer.changeSize(1, 0, QSizePolicy.Minimum, QSizePolicy.Fixed)

        self.filter_bar = ServerFilterBar(theme=self.theme)
        self.filter_bar.filterChanged.connect(self.filterChanged.emit)
        hero.addSpacing(20)
        hero.addWidget(self.filter_bar, 0, Qt.AlignCenter)

        layout.addLayout(hero)

        self.server_panel = ServerListPanel(self.theme, ping_display_mode=self._ping_display_mode)
        layout.addWidget(self.server_panel)

        bottom_pad = QWidget()
        bottom_pad.setFixedHeight(96)  # floating taskbar clearance
        layout.addWidget(bottom_pad)

        self._apply_status_style()

    def set_ping_display_mode(self, mode: int):
        self._ping_display_mode = int(mode)
        self.card_active.set_ping_display_mode(mode)
        self.server_panel.set_ping_display_mode(mode)

    def set_show_flags(self, show: bool):
        """Show or hide country flags (mobile showFlags parity)."""
        self.card_active.set_show_flags(show)
        self.card_telemetry.set_show_flags(show)
        self.server_panel.set_show_flags(show)

    def set_status(self, text: str, is_connected: bool,
                   is_connecting: bool = False, is_error: bool = False):
        self._status_state = {"text": text, "connected": bool(is_connected),
                              "connecting": bool(is_connecting),
                              "error": bool(is_error)}
        if is_connecting:
            self.hero_btn.set_connecting(True)
        else:
            self.hero_btn.set_connecting(False)
            self.hero_btn.set_connected(is_connected, animated=True)

        if is_error:
            self.status_label.setText(text or tr("status.error"))
            self.status_hint.setText(tr("status.error_hint"))
            self.status_hint.show()
            self._set_telemetry(False, stop=True)
        elif is_connected:
            self.status_label.setText(text or tr("status.connected"))
            self.status_hint.hide()
            self._set_telemetry(True)
        elif is_connecting:
            self.status_label.setText(text or tr("status.connecting_alt"))
            self.status_hint.setText(tr("status.tunnel"))
            self.status_hint.show()
            self._set_telemetry(False)
        else:
            self.status_label.setText(tr("status.disconnected"))
            self.status_hint.hide()
            self._set_telemetry(False, stop=True)

        self._apply_status_style()

    def _set_telemetry(self, visible: bool, stop: bool = False):
        self._telemetry_connected = bool(visible)
        shown = bool(visible and self._show_ip_card)
        self.card_telemetry.setVisible(shown)
        self._telemetry_spacer.changeSize(1, 10 if shown else 0, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._hero_layout.invalidate()
        if not visible and stop:
            self.card_telemetry.stop_session()

    def set_cards_visibility(self, show_active: bool = True, show_ip: bool = True):
        """Apply Settings toggles for the main-screen cards visibility."""
        self._show_active_card = bool(show_active)
        self._show_ip_card = bool(show_ip)
        self.card_active.setVisible(self._show_active_card)
        self._active_spacer.changeSize(
            1, 18 if self._show_active_card else 0, QSizePolicy.Minimum, QSizePolicy.Fixed)
        telem_shown = bool(self._telemetry_connected and self._show_ip_card)
        self.card_telemetry.setVisible(telem_shown)
        self._telemetry_spacer.changeSize(
            1, 10 if telem_shown else 0, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._hero_layout.invalidate()

    def _apply_status_style(self):
        state = self._status_state
        theme = self.theme
        muted_hex = getattr(theme, "outline", "#938F99")
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        error_hex = getattr(theme, "error", "#F2B8B5")

        if state["error"]:
            color = error_hex
        elif state["connected"]:
            color = accent_hex
        else:
            color = muted_hex

        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 20px; font-weight: bold; background: transparent;")
        self.status_hint.setStyleSheet(
            f"color: {muted_hex}; font-size: 12px; background: transparent;")



    def scroll_to_list(self):
        """Jump so the first server-list row sits at the top of the view."""
        target = self.server_panel.mapTo(self.content, QPoint(0, 0)).y()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(target, bar.maximum())))

    def apply_theme(self, theme):
        self.theme = theme
        self.scroll.apply_theme(theme)
        self.hero_btn.apply_theme(theme)
        self.card_active.apply_theme(theme)
        self.card_telemetry.apply_theme(theme)
        self.filter_bar.apply_theme(theme)
        self.server_panel.apply_theme(theme)
        self._apply_status_style()
