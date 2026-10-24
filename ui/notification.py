"""System notification handling with user preferences."""
from __future__ import annotations

import logging
from typing import Any

from utils.i18n import tr, trn

log = logging.getLogger(__name__)


class NotificationType:
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTION_ERROR = "connection_error"
    SUB_UPDATED = "sub_updated"
    SUB_ERROR = "sub_error"
    KILLSWITCH = "killswitch"
    TRAY_MINIMIZED = "tray_minimized"


NOTIFICATION_TYPES = (
    NotificationType.CONNECTING,
    NotificationType.CONNECTED,
    NotificationType.DISCONNECTED,
    NotificationType.CONNECTION_ERROR,
    NotificationType.SUB_UPDATED,
    NotificationType.SUB_ERROR,
    NotificationType.KILLSWITCH,
    NotificationType.TRAY_MINIMIZED,
)


class NotificationManager:
    """Central notification dispatcher respecting user notification preferences."""

    def __init__(self, tray_manager=None, settings: dict[str, Any] | None = None):
        self.tray_manager = tray_manager
        self.settings: dict[str, Any] = settings if settings is not None else {}

    def is_type_enabled(self, ntype: str) -> bool:
        """Check whether notifications globally and this type specifically are enabled."""
        if not self.settings.get("notifications_enabled", True):
            return False
        return bool(self.settings.get(f"notify_{ntype}", True))

    def notify(self, ntype: str, title: str, message: str) -> bool:
        """Deliver a notification via tray_manager if allowed by preferences."""
        if not self.is_type_enabled(ntype):
            log.debug("Notification [%s] suppressed by settings: %s - %s", ntype, title, message)
            return False
        if self.tray_manager and hasattr(self.tray_manager, "notify"):
            self.tray_manager.notify(title, message)
            return True
        return False

    def notify_connecting(self, server_name: str, is_tun: bool = False) -> bool:
        if is_tun:
            msg = tr("tray.tunnel_for").format(name=server_name)
        else:
            msg = tr("tray.connect_to").format(name=server_name)
        return self.notify(NotificationType.CONNECTING, tr("tray.connecting"), msg)

    def notify_connected(self, ip: str, flag: str = "") -> bool:
        if flag:
            title = tr("tray.connected_flag").format(flag=flag, ip=ip)
        else:
            title = tr("tray.connected_plain").format(ip=ip)
        msg = tr("tray.connected_msg").format(ip=ip)
        return self.notify(NotificationType.CONNECTED, title, msg)

    def notify_disconnected(self) -> bool:
        return self.notify(
            NotificationType.DISCONNECTED,
            tr("status.disconnected"),
            tr("tray.conn_closed"),
        )

    def notify_connection_error(self, message: str) -> bool:
        return self.notify(
            NotificationType.CONNECTION_ERROR,
            tr("status.error"),
            message,
        )

    def notify_killswitch_failed(self, reason: str) -> bool:
        msg = tr("killswitch.failed").format(reason=reason)
        return self.notify(
            NotificationType.KILLSWITCH,
            tr("killswitch.title"),
            msg,
        )

    def notify_sub_updated(self, new_count: int, is_manual: bool = False) -> bool:
        if new_count > 0:
            msg = trn("tray.nodes_added", new_count).format(n=new_count)
        elif is_manual:
            msg = tr("tray.sub_uptodate")
        else:
            return False
        return self.notify(
            NotificationType.SUB_UPDATED,
            tr("tray.sub_updated"),
            msg,
        )

    def notify_sub_error(self, sub_name: str, err_msg: str) -> bool:
        return self.notify(
            NotificationType.SUB_ERROR,
            tr("tray.sub_error"),
            tr("tray.sub_error_msg").format(sub=sub_name, err=err_msg),
        )

    def notify_tray_minimized(self) -> bool:
        return self.notify(
            NotificationType.TRAY_MINIMIZED,
            "Socksicle",
            tr("tray.running"),
        )
