import logging
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QDialog,
    QApplication, QStackedWidget,
)
from .message_box import MessageBox
from PySide6.QtCore import (
    Qt, QTimer, Slot, QRunnable, QThreadPool,
)
from PySide6.QtGui import QKeySequence, QShortcut, QRegion, QPainterPath, QColor
from urllib.parse import urlparse

from .header_bar import HeaderBar
from .tray_manager import TrayManager
from .add_server_dialog import AddServerDialog
from .connection_log_dialog import ConnectionLogDialog
from .taskbar_view import TaskbarView
from .screen_main import ScreenMain
from .screen_settings import SettingsScreen
from .screen_about import ScreenAbout
from .edit_subscription_dialog import EditSubscriptionDialog
from .bottom_fade_overlay import BottomFadeOverlay

from utils import twinsock
from utils.connection_manager import ConnectionManager
from utils.i18n import set_language, tr, trn
from utils.server_manager import ServerManager
from utils.server_model import server_identity
from utils.subscription_manager import SubscriptionManager
from utils.subscription_store import get_last_fetch_error
from utils.ping import DEFAULT_PING_METHOD
from utils.theme import M3Theme
from utils.platform_utils import get_app_dir
from utils.engines.engine_manager import get_engine, EngineType
from utils.startup_utils import show_backend_missing, show_singbox_outdated
from .theme_transition import (
    ThemeColorTransition,
    apply_theme_tokens,
    theme_tokens,
)

log = logging.getLogger("main_window")


class _ToggleJob(QRunnable):
    """Run one ConnectionManager.toggle() off the GUI thread."""

    def __init__(self, manager, server, connect):
        super().__init__()
        self._manager = manager
        self._server = server
        self._connect = connect

    def run(self):
        try:
            self._manager.toggle(self._server, self._connect)
        except Exception:
            log.exception("Toggle job failed")


class RoundedWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.server_manager = ServerManager()
        self.manual_servers = self.server_manager.manual_servers
        self.settings = self.server_manager.settings
        set_language(self.settings.get("language", "ru"))
        self.theme = M3Theme(
            preset_key=self.settings.get("theme_preset", "dynamic"),
            seed=self._custom_theme_seed(),
        )
        self._filter_mode = 0
        self._selected_server_key = None

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(440, 800)

        icon_file = get_app_dir() / "icon.svg"
        self.icon_path = str(icon_file) if icon_file.exists() else "icon.svg"

        self.tray_manager = TrayManager(self.theme, self.icon_path)
        self.tray_manager.showHideRequested.connect(self._toggle_visibility)
        self.tray_manager.quitRequested.connect(self.quit_app)
        self.tray_manager.connectRequested.connect(self._on_connect_from_tray)
        self.tray_manager.disconnectRequested.connect(self._on_disconnect_from_tray)

        self.subscription_manager = SubscriptionManager(self.settings)
        self.connection_manager = ConnectionManager(self.settings)
        self.connection_manager.apply_settings(self.settings)

        if self.settings.get("auto_update_subs", True):
            QTimer.singleShot(3000, self.subscription_manager.update_all)

        self.connection_manager.statusChanged.connect(self.on_status_changed)
        self.connection_manager.connectionStateChanged.connect(self.on_connection_state_changed)
        self.connection_manager.logUpdated.connect(self.add_log)
        self.connection_manager.geoInfoReady.connect(self.update_geo_ui)
        self.connection_manager.geoError.connect(self.on_geo_error)
        self.connection_manager.pingResultReady.connect(self.update_ping_ui)
        self.connection_manager.killSwitchFailed.connect(self._on_killswitch_failed)
        self.subscription_manager.updated.connect(self._on_sub_updated)
        self.subscription_manager.updateFailed.connect(self._on_sub_update_failed)

        self.log_dialog = ConnectionLogDialog(self, self.theme)
        self._ping_all_generation = 0
        self._theme_transition = ThemeColorTransition(self)
        self._theme_transition.frame.connect(self._on_theme_frame)
        self._theme_transition.finished.connect(self._on_theme_transition_finished)
        self._theme_target_tokens: dict = {}
        self._pinging_keys: set = set()
        self._refreshing_key = None

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("rootContainer")
        self.container.setStyleSheet(
            f"QFrame#rootContainer {{ background-color: {self.theme.surface}; border-radius: 32px; border: none; outline: none; }}")
        self.main_layout.addWidget(self.container)

        self.inner_layout = QVBoxLayout(self.container)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.inner_layout.setSpacing(0)

        self.header_bar = HeaderBar(self.theme, self.container)
        self.header_bar.closeRequested.connect(self.close)
        shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        shortcut.activated.connect(self._toggle_search_bar)
        self.inner_layout.addWidget(self.header_bar)

        self.screen_stack = QStackedWidget()

        self.screen_main = ScreenMain(
            self.theme,
            invert_scroll=self.settings.get("invert_scroll", False),
            ping_display_mode=self.settings.get("ping_display", 0),
        )
        self.screen_main.connectClicked.connect(self.on_vpn_switch_clicked)
        self.screen_main.filterChanged.connect(self._on_filter_changed)
        self.screen_main.card_active.clicked.connect(self._on_card_active_clicked)
        self.screen_main.set_cards_visibility(
            self.settings.get("show_active_server_card", True),
            self.settings.get("show_ip_card", True),
        )
        self.screen_main.set_show_flags(self.settings.get("show_flags", True))

        self.server_panel = self.screen_main.server_panel
        self.server_panel.serverSelected.connect(self._on_server_selected)
        self.server_panel.serverDeleted.connect(self._on_server_deleted)
        self.server_panel.groupCollapseToggled.connect(self._on_group_collapsed)
        self.server_panel.groupPingRequested.connect(self._on_ping_group)
        self.server_panel.groupRefreshRequested.connect(self._refresh_subscription)
        self.server_panel.groupEditRequested.connect(self._edit_subscription)
        self.server_panel.pingFinished.connect(self._on_group_ping_finished)

        self.screen_stack.addWidget(self.screen_main)

        self.screen_settings = SettingsScreen(self.settings, self.theme)
        self.screen_settings.themeChanged.connect(self._on_settings_theme_changed)
        self.screen_settings.settingChanged.connect(self._on_setting_value_changed)
        self.screen_settings.showLogsRequested.connect(self.show_log_dialog)
        self.screen_stack.addWidget(self.screen_settings)

        self.screen_about = ScreenAbout(self.theme, settings=self.settings)
        self.screen_stack.addWidget(self.screen_about)

        self.inner_layout.addWidget(self.screen_stack, 1)

        self.bottom_fade = BottomFadeOverlay(self.theme, self.container)

        self.taskbar_container = QWidget(self.container)
        self.taskbar_container.setObjectName("taskbarContainer")
        self.taskbar_container.setAttribute(Qt.WA_TranslucentBackground, True)
        self.taskbar_container.setStyleSheet("background: transparent; border: none;")
        tc_layout = QHBoxLayout(self.taskbar_container)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.setAlignment(Qt.AlignCenter)
        self.taskbar = TaskbarView(parent=self.taskbar_container, theme=self.theme)
        self.taskbar.tabChanged.connect(self._on_taskbar_tab_changed)
        self.taskbar.addClicked.connect(self.show_add_dialog)
        self.taskbar.searchTextChanged.connect(self._on_search_text)
        tc_layout.addWidget(self.taskbar)

        self._dragging = False

        self.apply_theme_styles(refresh=False)
        self._refresh_server_list()
        self._layout_overlays()

    def _layout_overlays(self):
        w = self.container.width()
        h = self.container.height()
        if w <= 0 or h <= 0:
            return

        self._apply_container_mask()
        fade_h = 110
        self.bottom_fade.setGeometry(0, max(0, h - fade_h), w, fade_h)
        self.bottom_fade.raise_()

        self.taskbar_container.adjustSize()
        th = self.taskbar_container.height()
        self.taskbar_container.setGeometry(16, h - th - 22, max(0, w - 32), th)
        self.taskbar_container.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_overlays()

    def _apply_container_mask(self):
        """Clip the container (and its square-cornered children) to the
        rounded window shape.

        Stylesheet border-radius only rounds the container's own background:
        child widgets paint square corners that poke out of the rounded
        window. A rounded-region mask clips the whole subtree, so the
        window rounding itself stays clean. Radius must match the
        rootContainer stylesheet (32px).
        """
        w = self.container.width()
        h = self.container.height()
        if w <= 0 or h <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(w), float(h), 32.0, 32.0)
        self.container.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _toggle_search_bar(self):
        self.taskbar.toggle_search()

    def _on_search_text(self, text: str):
        self.server_panel.set_query(text or "")

    def _on_taskbar_tab_changed(self, idx: int):
        self.screen_stack.setCurrentIndex(idx)
        if idx == 0:
            self.header_bar.set_title("Socksicle")
        elif idx == 1:
            self.header_bar.set_title(tr("nav.settings"))
        elif idx == 2:
            self.header_bar.set_title(tr("nav.about"))
            self.screen_about.refresh_data()

    def _on_filter_changed(self, mode: int):
        self._filter_mode = mode
        self._refresh_server_list()

    def _on_card_active_clicked(self):
        # Tap on the active card scrolls to the server list (Android behavior)
        self.screen_main.scroll_to_list()

    def _custom_theme_seed(self):
        cand = QColor(str(self.settings.get("theme_custom_color", "")))
        return cand if cand.isValid() else None

    def _on_settings_theme_changed(self, key: str):
        self.settings["theme_preset"] = key
        self.server_manager.save_settings()
        start = theme_tokens(self.theme)
        probe = M3Theme(preset_key=key, seed=self._custom_theme_seed())
        end = theme_tokens(probe)
        self.theme.preset_key = probe.preset_key
        self.theme.is_amoled = probe.is_amoled
        if start == end:
            apply_theme_tokens(self.theme, end)
            self._clear_icon_cache()
            self.apply_theme_styles()
            return
        self._theme_target_tokens = end
        self._theme_transition.start(start, end)

    def _clear_icon_cache(self):
        try:
            from utils import icons as _icons
            _icons.clear_cache()
        except (ImportError, AttributeError, RuntimeError) as e:
            log.debug("Icon cache clear skipped: %s", e)

    @Slot(object)
    def _on_theme_frame(self, tokens):
        apply_theme_tokens(self.theme, tokens)
        self.apply_theme_styles(refresh=False)

    @Slot()
    def _on_theme_transition_finished(self):
        if self._theme_target_tokens:
            apply_theme_tokens(self.theme, self._theme_target_tokens)
            self._theme_target_tokens = {}
        self._clear_icon_cache()
        self.apply_theme_styles()

    def _on_setting_value_changed(self, key: str, val):
        self.settings[key] = val
        self.server_manager.save_settings()

        if key == "invert_scroll":
            self.screen_main.scroll.set_invert_scroll(val)
            self.screen_settings.scroll_area.set_invert_scroll(val)
            self.screen_about.scroll_area.set_invert_scroll(val)
        elif key == "ping_display":
            self.screen_main.set_ping_display_mode(int(val))
        elif key in ("show_active_server_card", "show_ip_card"):
            self.screen_main.set_cards_visibility(
                self.settings.get("show_active_server_card", True),
                self.settings.get("show_ip_card", True),
            )
        elif key == "show_flags":
            self.screen_main.set_show_flags(bool(val))
        elif key == "engine":
            if not self.settings.get("tun_mode", False):
                engine = get_engine(EngineType(val))
                self.connection_manager.switch_engine(engine)
        elif key == "tun_mode":
            if val:
                engine = get_engine(EngineType.SINGBOX)
            else:
                engine = get_engine(EngineType(self.settings.get("engine", "sing-box")))
            self.connection_manager.switch_engine(engine)

        self.connection_manager.apply_settings(self.settings)

    def on_vpn_switch_clicked(self):
        self.toggle_connection()

    def _ensure_backend(self):
        engine = self.connection_manager.engine
        binary = engine.find_binary()
        if binary is None or not binary.exists():
            show_backend_missing(parent=self)
            return False
        if engine.engine_type == EngineType.SINGBOX:
            try:
                from utils.engines.singbox_engine import (
                    get_singbox_version,
                    is_singbox_version_supported,
                )
                version_str, version = get_singbox_version(binary)
                if not is_singbox_version_supported(version):
                    show_singbox_outdated(parent=self, version_str=version_str)
            except Exception as e:
                log.debug("sing-box version check skipped: %s", e)
        return True

    @Slot(dict)
    def update_geo_ui(self, info):
        if self.connection_manager.is_connected:
            # The card stores the raw flag and applies show_flags itself, so
            # toggling the setting restores it without waiting for new geo.
            self.screen_main.card_telemetry.set_geo_info(
                info.get("ip", "-"),
                f"{info.get('city', '')}, {info.get('country', '')}".strip(" ,"),
                info.get("flag", "🌐")
            )
            flag = info.get("flag", "🌐") if self.settings.get("show_flags", True) else ""
            if flag:
                title = tr("tray.connected_flag").format(flag=flag, ip=info['ip'])
            else:
                title = tr("tray.connected_plain").format(ip=info['ip'])
            self.tray_manager.notify(
                title,
                tr("tray.connected_msg").format(ip=info['ip']))

    @Slot(str)
    def on_geo_error(self, reason):
        pass

    @Slot(str)
    def _on_killswitch_failed(self, reason: str):
        """Stop the session when the requested Kill Switch cannot be enabled."""
        message = tr("killswitch.failed").format(reason=reason)
        log.warning("Kill Switch failed: %s", reason)
        if self.connection_manager.is_connected:
            self.connection_manager.disconnect()
            self.screen_main.set_status(tr("status.error"), False, is_error=True)
        self.tray_manager.notify(tr("killswitch.title"), message)
        MessageBox.warning(self, tr("killswitch.title"), message)

    @Slot(object)
    def update_ping_ui(self, ms):
        if not self.connection_manager.is_connected:
            return
        self.screen_main.card_active.set_latency(ms)

    def on_status_changed(self, msg, err):
        if not self.connection_manager.is_connected and not self.connection_manager.is_connecting:
            self._ping_all_generation += 1
        self.tray_manager.set_disconnect_enabled(
            self.connection_manager.is_connected or self.connection_manager.is_connecting
        )
        if err:
            self.screen_main.set_status(tr("status.error"), False, is_error=True)
            self.tray_manager.notify(tr("status.error"), msg)
        elif self.connection_manager.is_connected:
            self.screen_main.set_status(tr("status.connected"), True)
            self.screen_main.card_telemetry.start_session()
            if self.connection_manager.current_geo:
                geo = self.connection_manager.current_geo
                self.screen_main.card_telemetry.set_geo_info(
                    geo.get("ip", "-"),
                    f"{geo.get('city', '')}, {geo.get('country', '')}".strip(" ,"),
                    geo.get("flag", "🌐")
                )
        elif self.connection_manager.is_connecting or self.connection_manager.is_reconnecting or "Reconnecting" in msg:
            # A hot-reconnect emits "Disconnected" internally first: it must
            # never reset the hero button mid-handshake (spin would stop).
            if msg == tr("engine.disconnected"):
                msg = tr("status.connecting")
            self.screen_main.set_status(msg or tr("status.connecting"), False, is_connecting=True)
        else:
            self.screen_main.set_status(tr("status.disconnected"), False)
            self.screen_main.card_telemetry.stop_session()

    def on_connection_state_changed(self, conn):
        if conn:
            self.screen_main.set_status(tr("status.connected"), True)
        elif not self.connection_manager.is_connecting and not self.connection_manager.is_reconnecting:
            self.screen_main.set_status(tr("status.disconnected"), False)
        for item in self.server_panel._server_items:
            item.radio.update()

    def _on_server_selected(self, idx):
        servers = self._current_servers()
        if 0 <= idx < len(servers):
            selected = servers[idx]
            self._selected_server_key = server_identity(selected)
            self.screen_main.card_active.set_server({
                "name": selected.name,
                "server": selected.host,
                "server_port": selected.port,
                "flag": selected.flag_emoji,
                "proto": selected.protocol.value,
            })
            if self.connection_manager.is_connected or self.connection_manager.is_connecting:
                curr = self.connection_manager.current_server
                if curr and curr.key == selected.key:
                    return
                self.toggle_connection(True)

    def toggle_connection(self, connect=None):
        if connect is None:
            connect = not (self.connection_manager.is_connected or self.connection_manager.is_connecting)
        if connect:
            if not self._ensure_backend():
                return
            tun_mode = bool(self.settings.get("tun_mode", False))
            kill_switch = bool(self.settings.get("killswitch", False))
            if tun_mode or kill_switch:
                from utils.platform_utils import (
                    is_admin, elevate_restart, is_windows, is_linux,
                    check_tun_capabilities, grant_tun_capabilities
                )
                if is_windows() and tun_mode and kill_switch:
                    MessageBox.warning(
                        self, tr("dlg.ks_tun_title"), tr("dlg.ks_tun_text"))
                    return
                if is_windows() and not is_admin():
                    text = tr("dlg.admin_text") if tun_mode else tr("dlg.admin_ks_text")
                    reply = MessageBox.question(
                        self, tr("dlg.admin_title"), text,
                        MessageBox.Yes | MessageBox.No,
                        MessageBox.Yes)
                    if reply == MessageBox.Yes:
                        elevate_restart()
                    return
                if kill_switch and is_linux() and not is_admin():
                    MessageBox.warning(
                        self, tr("dlg.admin_title"), tr("dlg.admin_ks_linux_text"))
                    return
                if tun_mode and is_linux():
                    engine = self.connection_manager.engine
                    binary = engine.find_binary()
                    if binary and not check_tun_capabilities(binary):
                        granted = grant_tun_capabilities(binary, parent_window=self)
                        if not granted or not check_tun_capabilities(binary):
                            MessageBox.warning(
                                self, tr("dlg.tun_title"),
                                tr("dlg.tun_text").format(binary=binary)
                            )
                            return
            idx = self.server_panel.get_selected_index()
            servers = self._current_servers()
            if idx < 0 and self._selected_server_key is not None:
                # The chosen row is hidden inside a collapsed group: the radio
                # has no button, but the stored identity still points at it.
                idx = next((i for i, s in enumerate(servers)
                            if server_identity(s) == self._selected_server_key), -1)
            if 0 <= idx < len(servers):
                server = servers[idx]
                if server.is_expired:
                    MessageBox.warning(
                        self, tr("dlg.server_expired_title"),
                        tr("dlg.server_expired_text").format(name=server.name))
                    self.screen_main.set_status(tr("status.disconnected"), False)
                    return
                self.connection_manager.set_available_servers(servers)
                log.info("Connecting to server index %d...", idx)
                self.screen_main.set_status(tr("status.connecting"), False, is_connecting=True)
                is_tun = self.settings.get("tun_mode", False)
                if is_tun:
                    self.tray_manager.notify(tr("tray.connecting"), tr("tray.tunnel_for").format(name=server.name))
                else:
                    self.tray_manager.notify(tr("tray.connecting"), tr("tray.connect_to").format(name=server.name))

                QThreadPool.globalInstance().start(
                    _ToggleJob(self.connection_manager, server, True))
            else:
                MessageBox.warning(self, tr("dlg.error"), tr("dlg.no_server"))
        else:
            log.info("Disconnecting...")
            self.screen_main.set_status(tr("status.disconnected"), False)
            self.tray_manager.notify(tr("status.disconnected"), tr("tray.conn_closed"))
            QThreadPool.globalInstance().start(
                _ToggleJob(self.connection_manager, None, False))

    def _build_groups(self):
        """Return group cards in stable order for the server list."""
        mode = self._filter_mode
        collapsed = self.settings.get("collapsed_groups", []) or []
        groups = []

        if mode in (0, 2) and self.manual_servers:
            groups.append({
                "key": "__manual__",
                "title": tr("groups.manual"),
                "icon": "⚡",
                "subtitle": "",
                "is_direct": True,
                "collapsed": "__manual__" in collapsed,
                "servers": list(self.manual_servers),
            })

        if mode in (0, 1):
            subs = list(self.subscription_manager.subscriptions)
            subs.sort(key=lambda s: not bool(s.get("is_pinned", False)))
            for sub in subs:
                name = sub.get("name", "")
                groups.append({
                    "key": name,
                    "title": name,
                    "icon": sub.get("icon_emoji", "🌐"),
                    "subtitle": self._group_subtitle(sub),
                    "is_direct": False,
                    "collapsed": name in collapsed,
                    "servers": list(sub.get("servers", [])),
                })
        return groups

    @staticmethod
    def _group_subtitle(sub: dict) -> str:
        """Expiry line for a subscription group card (display-time formatting)."""
        from utils.locale import format_subscription_subtitle
        return format_subscription_subtitle(sub.get("expires_at"))

    def _current_servers(self):
        return [s for g in self._build_groups() for s in g["servers"]]

    def _group_servers(self, key: str):
        for g in self._build_groups():
            if g["key"] == key:
                return g["servers"]
        return []

    def reconnect_after_resume(self):
        log.info("System resumed or network restored, reconnecting proxy...")
        if not self.connection_manager.is_connected:
            return
        self.connection_manager.disconnect()
        self.screen_main.set_status(tr("status.disconnected"), False)
        QTimer.singleShot(1500, lambda: self.toggle_connection(True))

    def _refresh_server_list(self):
        self.manual_servers = self.server_manager.manual_servers
        groups = self._build_groups()
        servers = [s for g in groups for s in g["servers"]]
        self.connection_manager.set_available_servers(servers)
        connected_key = None
        curr = self.connection_manager.current_server
        if self.connection_manager.is_connected and curr:
            connected_key = curr.key
        self.server_panel.refresh(servers, connected_key, groups=groups)

        if servers:
            target_idx = 0
            if self._selected_server_key is not None:
                for i, s in enumerate(servers):
                    if server_identity(s) == self._selected_server_key:
                        target_idx = i
                        break
                else:
                    self._selected_server_key = server_identity(servers[0])
            else:
                self._selected_server_key = server_identity(servers[0])
            target_srv = servers[target_idx]
            self.server_panel.set_selected_index(target_idx)
            self.screen_main.card_active.set_server({
                "name": target_srv.name,
                "server": target_srv.host,
                "server_port": target_srv.port,
                "flag": target_srv.flag_emoji,
                "proto": target_srv.protocol.value,
            })
        else:
            self._selected_server_key = None
            self.screen_main.card_active.set_server(None)

        self.tray_manager.rebuild_menu_if_changed(
            self.manual_servers, self.subscription_manager.subscriptions)

        # Rebuilding reuses or recreates cards/rows, dropping transient busy
        # visuals: restore them for operations still running off-thread.
        if self._refreshing_key is not None:
            self._set_group_refresh_enabled(False)
            self._set_card_refreshing(self._refreshing_key, True)
        for key in self._pinging_keys:
            self._set_card_pinging(key, True)

    def _on_group_collapsed(self, key: str):
        collapsed = list(self.settings.get("collapsed_groups", []) or [])
        if key in collapsed:
            collapsed.remove(key)
        else:
            collapsed.append(key)
        self.settings["collapsed_groups"] = collapsed
        self.server_manager.save_settings()
        self._refresh_server_list()

    def _on_ping_group(self, key: str):
        if key in self._pinging_keys:
            return
        servers = self._group_servers(key)
        if not servers:
            return
        self._pinging_keys.add(key)
        self._set_card_pinging(key, True)
        method = self.settings.get("ping_method", DEFAULT_PING_METHOD)
        self.server_panel.ping_all(
            method, self.connection_manager.local_port,
            servers=servers, group_key=key)

    @Slot(str)
    def _on_group_ping_finished(self, key: str):
        self._pinging_keys.discard(key)
        self._set_card_pinging(key, False)

    def _set_group_refresh_enabled(self, enabled: bool):
        for card in self.server_panel._group_cards:
            card.btn_refresh.setEnabled(enabled)

    def _card_for_key(self, key: str):
        for card in self.server_panel._group_cards:
            if card.key == key:
                return card
        return None

    def _set_card_pinging(self, key: str, busy: bool):
        card = self._card_for_key(key)
        if card is not None:
            card.set_pinging(busy)

    def _set_card_refreshing(self, key: str, refreshing: bool):
        card = self._card_for_key(key)
        if card is not None:
            card.set_refreshing(refreshing)

    def _clear_refresh_state(self):
        key = self._refreshing_key
        self._refreshing_key = None
        self._set_group_refresh_enabled(True)
        if key:
            self._set_card_refreshing(key, False)

    def _refresh_subscription(self, key: str):
        if self._refreshing_key is not None:
            return
        sub = self.subscription_manager.get(key)
        if not sub:
            return
        self._refreshing_key = key
        self._set_group_refresh_enabled(False)
        self._set_card_refreshing(key, True)
        # SubscriptionManager.update() fetches off the GUI thread and
        # reports back via updated/updateFailed, so the UI never blocks.
        self.subscription_manager.update(sub)

    def _edit_subscription(self, key: str):
        sub = self.subscription_manager.get(key)
        if not sub:
            return
        old_url = sub.get("url", "")
        old_name = sub.get("name", "")
        dlg = EditSubscriptionDialog(sub, self.subscription_manager, self.theme, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_server_list()
            if not dlg.deleted:
                new_name = dlg.edit_name.text().strip()
                current_sub = self.subscription_manager.get(new_name or old_name)
                if current_sub and current_sub.get("url", "") != old_url:
                    self._refresh_subscription(current_sub.get("name", ""))

    def _on_connect_from_tray(self, server):
        # The tray menu and the panel list share the same server objects,
        # but group-local menu positions mean nothing to the flat panel
        # list: resolve by identity. Syncing _selected_server_key first
        # keeps the hidden-row fallback in toggle_connection() on target
        # when the row lives in a collapsed group.
        target = server_identity(server)
        for i, s in enumerate(self._current_servers()):
            if server_identity(s) == target:
                self._selected_server_key = target
                self.server_panel.set_selected_index(i)
                QTimer.singleShot(100, lambda: self.toggle_connection(True))
                return

    def _on_disconnect_from_tray(self):
        self.toggle_connection(False)

    def show_log_dialog(self):
        if self.log_dialog:
            self.log_dialog.set_theme(self.theme)
            self.log_dialog.show()
            self.log_dialog.raise_()
            self.log_dialog.activateWindow()

    def add_log(self, msg):
        if self.log_dialog:
            timestamp = time.strftime("%H:%M:%S")
            self.log_dialog.add_log(f"[{timestamp}] {msg}")

    @Slot(str, str, str)
    def _on_sub_update_failed(self, sub_name, url, err_msg):
        self._clear_refresh_state()
        self.tray_manager.notify(
            tr("tray.sub_error"),
            tr("tray.sub_error_msg").format(sub=sub_name, err=err_msg)
        )
        MessageBox.warning(
            self,
            tr("dlg.sub_failed_title"),
            tr("dlg.sub_failed_text").format(sub=sub_name, err=err_msg, url=url)
        )

    @Slot(bool, int)
    def _on_sub_updated(self, success, new_count):
        self._clear_refresh_state()
        if success:
            self._refresh_server_list()
            if new_count > 0:
                self.tray_manager.notify(
                    tr("tray.sub_updated"),
                    trn("tray.nodes_added", new_count).format(n=new_count))
            else:
                self.tray_manager.notify(
                    tr("tray.sub_updated"), tr("tray.sub_uptodate"))

    def _on_server_deleted(self, server):
        target = server_identity(server)
        for i, s in enumerate(self.server_manager.manual_servers):
            if server_identity(s) == target:
                self.server_manager.delete_manual(i)
                self._refresh_server_list()
                return
        MessageBox.information(
            self, tr("dlg.info"), tr("dlg.delete_sub_info"))

    def show_add_dialog(self):
        d = AddServerDialog(self, self.theme)
        if d.exec() != QDialog.Accepted:
            return
        raw = d.get_server_key()
        if not raw:
            MessageBox.warning(
                self, tr("dlg.empty_link_title"), tr("dlg.empty_link_text"))
            return
        link = raw
        metadata = {}
        if link.startswith(("tws3://", "tws3.")):
            share_key = self.server_manager.get_share_key()
            try:
                link, metadata = twinsock.decrypt_share_payload(share_key, raw)
            except (twinsock.VaultError, ValueError) as e:
                MessageBox.warning(
                    self, "TwinSock",
                    tr("dlg.decrypt_failed").format(e=e))
                return

        lock_export = metadata.get("lock_export", False)
        expires_at = metadata.get("expires_at")
        is_expired = False
        if expires_at is not None and expires_at > 0 and time.time() >= expires_at:
            is_expired = True

        links_to_import = []
        if isinstance(link, list):
            links_to_import = [str(x).strip() for x in link if str(x).strip()]
        elif isinstance(link, str):
            lines = [l.strip() for l in link.splitlines() if l.strip()]
            links_to_import = lines if len(lines) > 1 else [link.strip()]
        else:
            links_to_import = [str(link).strip()]

        imported_servers_count = 0
        imported_subs_count = 0
        attempted_urls = []

        for single_link in links_to_import:
            if single_link.startswith("http://") or single_link.startswith("https://"):
                attempted_urls.append(single_link)
                name = urlparse(single_link).hostname or urlparse(single_link).netloc or tr("sub.default_name")
                if self.subscription_manager.add(
                        name, single_link, lock_export=lock_export,
                        expires_at=expires_at, use_remote_title=True):
                    imported_subs_count += 1
            else:
                srv = self.server_manager.add_from_link(
                    single_link,
                    lock_export=lock_export,
                    expires_at=expires_at
                )
                if srv:
                    imported_servers_count += 1

        if imported_subs_count > 0 or imported_servers_count > 0:
            self._refresh_server_list()

        if imported_servers_count == 0 and imported_subs_count == 0:
            err = next((get_last_fetch_error(u) for u in attempted_urls if get_last_fetch_error(u)), None)
            if err:
                MessageBox.warning(
                    self, tr("dlg.load_failed_title"),
                    tr("dlg.load_failed_text").format(err=err)
                )
            else:
                MessageBox.warning(
                    self, tr("dlg.bad_link_title"),
                    tr("dlg.bad_link_text")
                )
            return

        if is_expired:
            MessageBox.information(
                self, tr("dlg.expired_title"),
                tr("dlg.expired_text"))

    def apply_theme_styles(self, refresh: bool = True):
        app = QApplication.instance()
        tt_style = self.theme.get_tooltip_style()
        if app and tt_style:
            app.setStyleSheet(tt_style)
        self.theme.apply_tooltip_palette()
        self.container.setStyleSheet(
            f"QFrame#rootContainer {{ background-color: {self.theme.surface}; border-radius: 32px; border: none; outline: none; }}\n{tt_style}")
        self.taskbar_container.setStyleSheet("background: transparent; border: none;")
        self.header_bar.apply_theme(self.theme)
        self.screen_main.apply_theme(self.theme)
        self.screen_settings.apply_theme(self.theme)
        self.screen_about.apply_theme(self.theme)
        self.taskbar.apply_theme(self.theme)
        if hasattr(self, "bottom_fade") and self.bottom_fade:
            self.bottom_fade.theme = self.theme
            self.bottom_fade.update()
        if self.log_dialog:
            self.log_dialog.set_theme(self.theme)
        if refresh:
            self._refresh_server_list()

    def quit_app(self):
        self.connection_manager.disconnect()
        try:
            from utils.killswitch import KillSwitchManager
            KillSwitchManager.get_instance().cleanup()
        except (ImportError, AttributeError, OSError, RuntimeError) as e:
            log.debug("Kill switch cleanup skipped on quit: %s", e)
        self.tray_manager.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self.settings.get("minimize_to_tray", True) and self.tray_manager.tray_available:
            event.ignore()
            self.hide()
            self.tray_manager.notify("Socksicle", tr("tray.running"))
        else:
            self.quit_app()

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 60:
            self._dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._dragging = False
