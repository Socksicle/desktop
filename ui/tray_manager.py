from typing import Any
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QIcon, QColor, QActionGroup, QAction
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from utils.i18n import tr
from utils.icons import icon, pixmap
from utils.server_model import server_identity


class TrayManager(QObject):
    connectRequested = Signal(object)
    disconnectRequested = Signal()
    showHideRequested = Signal()
    quitRequested = Signal()
    tunModeRequested = Signal(bool)

    def __init__(self, theme, icon_path, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.icon_path = icon_path
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._menu_snapshot = None
        self._is_tun = False
        self._is_connected = False
        self._is_connecting = False
        self._connected_server_name = None
        self._selected_server = None
        self._server_actions: list[tuple[QAction, Any]] = []
        self._stylesheet = ""
        self._text_hex = "#E6E0E9"
        self._muted_hex = "#938F99"
        self._accent_hex = "#D0BCFF"
        self._setup_tray()

    @staticmethod
    def _make_action_icon(name: str, color_hex: str, size: int = 16) -> QIcon | None:
        ic = icon(name, color_hex, size)
        if not ic:
            return None
        pm = pixmap(name, color_hex, size)
        if pm and not pm.isNull():
            ic.addPixmap(pm, QIcon.Mode.Disabled)
        return ic

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(self.icon_path))
        self.tray_menu = QMenu()

        # 1. Connected server status (disabled top banner)
        self.status_action = self.tray_menu.addAction("")
        self.status_action.setEnabled(False)
        self.tray_menu.addSeparator()

        # 2. Show / Hide
        self.show_action = self.tray_menu.addAction(tr("tray.show_hide"))
        self.show_action.triggered.connect(self.showHideRequested)

        # 3. Mode submenu (Proxy vs TUN)
        self.mode_menu = self.tray_menu.addMenu(tr("tray.mode"))
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)

        self.proxy_action = self.mode_menu.addAction(tr("tray.mode_proxy"))
        self.proxy_action.setCheckable(True)
        self.proxy_action.setChecked(not self._is_tun)
        self.mode_group.addAction(self.proxy_action)
        self.proxy_action.triggered.connect(self._on_proxy_action_triggered)

        self.tun_action = self.mode_menu.addAction(tr("tray.mode_tun"))
        self.tun_action.setCheckable(True)
        self.tun_action.setChecked(self._is_tun)
        self.mode_group.addAction(self.tun_action)
        self.tun_action.triggered.connect(self._on_tun_action_triggered)

        # 4. Servers submenu
        self.servers_menu = self.tray_menu.addMenu(tr("tray.servers"))

        self.tray_menu.addSeparator()

        # 5. Disconnect
        self.disconnect_action = self.tray_menu.addAction(tr("tray.disconnect"))
        self.disconnect_action.triggered.connect(lambda: self.disconnectRequested.emit())
        self.disconnect_action.setEnabled(False)

        # 6. Quit
        self.quit_action = self.tray_menu.addAction(tr("tray.quit"))
        self.quit_action.triggered.connect(self.quitRequested)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_activated)

        self.apply_theme(self.theme)
        self._refresh_status_action()
        self._update_mode_menu_title()
        self.tray_icon.show()

    def _style_menu(self, menu: QMenu):
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        if self._stylesheet:
            menu.setStyleSheet(self._stylesheet)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.showHideRequested.emit()

    def _on_proxy_action_triggered(self):
        if self._is_tun:
            self.set_tun_mode(False)
            self.tunModeRequested.emit(False)
        else:
            self.proxy_action.setChecked(True)

    def _on_tun_action_triggered(self):
        if not self._is_tun:
            self.set_tun_mode(True)
            self.tunModeRequested.emit(True)
        else:
            self.tun_action.setChecked(True)

    def set_tun_mode(self, is_tun: bool):
        self._is_tun = bool(is_tun)
        if hasattr(self, "proxy_action") and hasattr(self, "tun_action"):
            self.proxy_action.setChecked(not self._is_tun)
            self.tun_action.setChecked(self._is_tun)
        self._update_mode_menu_title()

    def _update_mode_menu_title(self):
        if not hasattr(self, "mode_menu") or not self.mode_menu:
            return
        mode_label = "TUN" if self._is_tun else tr("tray.mode_proxy")
        self.mode_menu.setTitle(f"{tr('tray.mode')}: {mode_label}")

    def update_status(self, is_connected: bool, server_name: str | None = None, is_connecting: bool = False):
        self._is_connected = bool(is_connected)
        self._is_connecting = bool(is_connecting)
        self._connected_server_name = server_name
        self._refresh_status_action()

    def _refresh_status_action(self):
        if not hasattr(self, "status_action") or not self.status_action:
            return
        if self._is_connected and self._connected_server_name:
            self.status_action.setText(f"{tr('status.connected')}: {self._connected_server_name}")
            ic = self._make_action_icon("hub_tunnel", self._accent_hex, 16)
        elif self._is_connecting:
            name_part = f": {self._connected_server_name}" if self._connected_server_name else ""
            self.status_action.setText(f"{tr('status.connecting')}{name_part}")
            ic = self._make_action_icon("hourglass", self._accent_hex, 16)
        else:
            self.status_action.setText(tr("status.disconnected"))
            ic = self._make_action_icon("tab_servers", self._muted_hex, 16)
        if ic:
            self.status_action.setIcon(ic)

    def _is_server_selected(self, server) -> bool:
        if server is None or self._selected_server is None:
            return False
        if isinstance(self._selected_server, str):
            ident = server_identity(server)
            key = getattr(server, "key", "") or ""
            return ident == self._selected_server or key == self._selected_server
        return server_identity(server) == server_identity(self._selected_server)

    def set_selected_server(self, server_or_key):
        self._selected_server = server_or_key
        for act, srv in self._server_actions:
            act.setChecked(self._is_server_selected(srv))

    def _on_server_action_triggered(self, srv):
        self.set_selected_server(srv)
        self.connectRequested.emit(srv)

    @staticmethod
    def _server_id(server) -> tuple:
        return (getattr(server, "key", "") or "",
                getattr(server, "name", "") or "",
                getattr(server, "host", "") or "",
                getattr(server, "port", "") or "")

    def rebuild_menu_if_changed(self, manual_servers, subscriptions) -> bool:
        snapshot = (
            tuple(self._server_id(s) for s in manual_servers),
            tuple(
                (sub.get("name", "") if isinstance(sub, dict) else "",
                 tuple(self._server_id(s) for s in
                       (sub.get("servers", []) if isinstance(sub, dict) else [])))
                for sub in subscriptions
            ),
        )
        if snapshot == self._menu_snapshot:
            return False
        self._menu_snapshot = snapshot
        self.rebuild_menu(manual_servers, subscriptions)
        return True

    def rebuild_menu(self, manual_servers, subscriptions):
        for act in self.servers_menu.actions():
            menu = act.menu()
            if menu:
                menu.deleteLater()
            act.deleteLater()
        self.servers_menu.clear()
        self._server_actions = []

        if manual_servers:
            manual_menu = self.servers_menu.addMenu(tr("groups.manual"))
            self._style_menu(manual_menu)
            ic_manual = self._make_action_icon("pencil", self._text_hex, 16)
            if ic_manual:
                manual_menu.setIcon(ic_manual)
            for server in manual_servers:
                action = manual_menu.addAction(server.name)
                action.setCheckable(True)
                action.setChecked(self._is_server_selected(server))
                action.triggered.connect(
                    lambda checked=False, srv=server: self._on_server_action_triggered(srv))
                self._server_actions.append((action, server))

        for sub in subscriptions:
            sub_name = sub.get("name", "") if isinstance(sub, dict) else getattr(sub, "name", "")
            sub_menu = self.servers_menu.addMenu(sub_name)
            self._style_menu(sub_menu)
            ic_sub = self._make_action_icon("hub_subs", self._text_hex, 16)
            if ic_sub:
                sub_menu.setIcon(ic_sub)
            servers = sub.get("servers", []) if isinstance(sub, dict) else getattr(sub, "servers", [])
            for server in servers:
                action = sub_menu.addAction(server.name)
                action.setCheckable(True)
                action.setChecked(self._is_server_selected(server))
                action.triggered.connect(
                    lambda checked=False, srv=server: self._on_server_action_triggered(srv))
                self._server_actions.append((action, server))

        if not manual_servers and not subscriptions:
            empty_act = self.servers_menu.addAction(tr("servers.empty_title"))
            empty_act.setEnabled(False)

    def set_disconnect_enabled(self, enabled):
        self.disconnect_action.setEnabled(bool(enabled))
        color = "#FF6B6B" if enabled else getattr(self, "_muted_hex", "#938F99")
        ic = self._make_action_icon("close", color, 16)
        if ic:
            self.disconnect_action.setIcon(ic)

    def notify(self, title, message):
        self.tray_icon.showMessage(title, message, QIcon(self.icon_path), 3000)

    def show(self):
        self.tray_icon.show()

    def restore(self):
        self.tray_icon.hide()
        self.tray_icon.show()

    def hide(self):
        self.tray_icon.hide()

    def _update_all_icons(self):
        ic_show = self._make_action_icon("tab_main", self._text_hex, 16)
        if ic_show:
            self.show_action.setIcon(ic_show)

        if hasattr(self, "mode_menu") and self.mode_menu:
            ic_mode = self._make_action_icon("hub_tunnel", self._text_hex, 16)
            if ic_mode:
                self.mode_menu.setIcon(ic_mode)
        if hasattr(self, "proxy_action") and self.proxy_action:
            ic_proxy = self._make_action_icon("hub_connection", self._text_hex, 16)
            if ic_proxy:
                self.proxy_action.setIcon(ic_proxy)
        if hasattr(self, "tun_action") and self.tun_action:
            ic_tun = self._make_action_icon("hub_tunnel", self._text_hex, 16)
            if ic_tun:
                self.tun_action.setIcon(ic_tun)

        if hasattr(self, "servers_menu") and self.servers_menu:
            ic_servers = self._make_action_icon("tab_servers", self._text_hex, 16)
            if ic_servers:
                self.servers_menu.setIcon(ic_servers)

        dis_color = "#FF6B6B" if self.disconnect_action.isEnabled() else self._muted_hex
        ic_dis = self._make_action_icon("close", dis_color, 16)
        if ic_dis:
            self.disconnect_action.setIcon(ic_dis)

        ic_quit = self._make_action_icon("close", self._text_hex, 16)
        if ic_quit:
            self.quit_action.setIcon(ic_quit)

    def apply_theme(self, theme):
        self.theme = theme
        if not hasattr(self, "tray_menu") or not self.tray_menu:
            return
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "on_surface_variant", "#938F99")
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        c = QColor(accent_hex)
        sel_bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.25)"

        self._text_hex = text_hex
        self._muted_hex = muted_hex
        self._accent_hex = accent_hex

        self._stylesheet = f"""
            QMenu {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 20px 8px 36px;
                min-width: 170px;
                border-radius: 8px;
                font-size: 13px;
                color: {text_hex};
            }}
            QMenu::item:disabled {{
                color: {muted_hex};
                font-weight: 600;
            }}
            QMenu::icon {{
                left: 10px;
            }}
            QMenu::indicator {{
                left: 10px;
                width: 16px;
                height: 16px;
            }}
            QMenu::item:selected {{
                background-color: {sel_bg};
                color: #FFFFFF;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: rgba(255, 255, 255, 0.08);
                margin: 6px 8px;
            }}
        """

        self._style_menu(self.tray_menu)
        if hasattr(self, "mode_menu") and self.mode_menu:
            self._style_menu(self.mode_menu)
        if hasattr(self, "servers_menu") and self.servers_menu:
            self._style_menu(self.servers_menu)
            for sub in self.servers_menu.findChildren(QMenu):
                self._style_menu(sub)

        self._update_all_icons()
        self._refresh_status_action()
        self._update_mode_menu_title()
