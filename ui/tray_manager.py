from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from utils.i18n import tr


class TrayManager(QObject):
    connectRequested = Signal(object)
    disconnectRequested = Signal()
    showHideRequested = Signal()
    quitRequested = Signal()

    def __init__(self, theme, icon_path, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.icon_path = icon_path
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._menu_snapshot = None
        self._setup_tray()

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(self.icon_path))
        self.tray_menu = QMenu()
        self.tray_menu.setAttribute(Qt.WA_TranslucentBackground, True)
        self.tray_menu.setWindowFlags(self.tray_menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.apply_theme(self.theme)

        self.show_action = self.tray_menu.addAction(tr("tray.show_hide"))
        self.show_action.triggered.connect(self.showHideRequested)

        self.servers_menu = self.tray_menu.addMenu(tr("tray.servers"))
        self.servers_menu.setAttribute(Qt.WA_TranslucentBackground, True)
        self.servers_menu.setWindowFlags(self.servers_menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.tray_menu.addSeparator()

        self.disconnect_action = self.tray_menu.addAction(tr("tray.disconnect"))
        self.disconnect_action.triggered.connect(
            lambda: self.disconnectRequested.emit())
        self.disconnect_action.setEnabled(False)

        self.quit_action = self.tray_menu.addAction(tr("tray.quit"))
        self.quit_action.triggered.connect(self.quitRequested)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_activated)
        self.tray_icon.show()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.showHideRequested.emit()

    @staticmethod
    def _server_id(server) -> tuple:
        return (getattr(server, "key", "") or "",
                getattr(server, "name", "") or "",
                getattr(server, "host", "") or "",
                getattr(server, "port", "") or "")

    def rebuild_menu_if_changed(self, manual_servers, subscriptions) -> bool:
        """Rebuild the tray servers menu only when the set changed.

        Collapse toggles, search typing and ping updates all funnel through
        the list refresh — without this guard every one of them would
        destroy and recreate an action per server (including collapsed and
        hidden ones) for an identical menu.
        """
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
        manual_menu = self.servers_menu.addMenu("Manual")
        for server in manual_servers:
            action = manual_menu.addAction(server.name)
            action.triggered.connect(
                lambda checked=False, srv=server: self.connectRequested.emit(srv))
        for sub in subscriptions:
            sub_menu = self.servers_menu.addMenu(sub['name'])
            for server in sub['servers']:
                action = sub_menu.addAction(server.name)
                action.triggered.connect(
                    lambda checked=False, srv=server: self.connectRequested.emit(srv))

    def set_disconnect_enabled(self, enabled):
        self.disconnect_action.setEnabled(enabled)

    def notify(self, title, message):
        self.tray_icon.showMessage(title, message, QIcon(self.icon_path), 3000)

    def show(self):
        self.tray_icon.show()

    def restore(self):
        """Re-add the icon after Explorer recreated the notification area."""
        self.tray_icon.hide()
        self.tray_icon.show()

    def hide(self):
        self.tray_icon.hide()

    def apply_theme(self, theme):
        self.theme = theme
        if not hasattr(self, "tray_menu") or not self.tray_menu:
            return
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        self.tray_menu.setStyleSheet(f"""
            QMenu {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {accent_hex}33;
                color: {accent_hex};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: rgba(255, 255, 255, 0.08);
                margin: 4px 6px;
            }}
        """)
