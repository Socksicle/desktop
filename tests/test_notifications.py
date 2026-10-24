"""Unit tests for NotificationManager and Notifications Settings UI."""
import pytest
from unittest import mock
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

from ui.notification import NotificationManager, NotificationType, NOTIFICATION_TYPES
from ui.screen_settings import SettingsScreen
from utils.theme import M3Theme
from utils.i18n import tr


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_notification_manager_all_types_enabled():
    tray_mock = mock.Mock()
    settings = {
        "notifications_enabled": True,
        "notify_connecting": True,
        "notify_connected": True,
        "notify_disconnected": True,
        "notify_connection_error": True,
        "notify_sub_updated": True,
        "notify_sub_error": True,
        "notify_killswitch": True,
        "notify_tray_minimized": True,
    }
    manager = NotificationManager(tray_mock, settings)

    assert manager.notify_connecting("Server 1", is_tun=False) is True
    assert tray_mock.notify.call_count == 1
    assert "Server 1" in tray_mock.notify.call_args[0][1]

    assert manager.notify_connecting("Server 1", is_tun=True) is True
    assert tray_mock.notify.call_count == 2

    assert manager.notify_connected("1.2.3.4", flag="🇩🇪") is True
    assert tray_mock.notify.call_count == 3
    assert "1.2.3.4" in tray_mock.notify.call_args[0][0]

    assert manager.notify_connected("1.2.3.4", flag="") is True
    assert tray_mock.notify.call_count == 4

    assert manager.notify_disconnected() is True
    assert tray_mock.notify.call_count == 5

    assert manager.notify_connection_error("Timeout connecting") is True
    assert tray_mock.notify.call_count == 6
    assert tray_mock.notify.call_args[0][1] == "Timeout connecting"

    assert manager.notify_killswitch_failed("iptables not found") is True
    assert tray_mock.notify.call_count == 7
    assert "iptables not found" in tray_mock.notify.call_args[0][1]

    assert manager.notify_sub_updated(5, is_manual=False) is True
    assert tray_mock.notify.call_count == 8

    assert manager.notify_sub_updated(0, is_manual=True) is True
    assert tray_mock.notify.call_count == 9

    assert manager.notify_sub_updated(0, is_manual=False) is False
    assert tray_mock.notify.call_count == 9

    assert manager.notify_sub_error("Sub1", "HTTP 404") is True
    assert tray_mock.notify.call_count == 10
    assert "Sub1" in tray_mock.notify.call_args[0][1]

    assert manager.notify_tray_minimized() is True
    assert tray_mock.notify.call_count == 11


def test_notification_manager_master_disabled():
    tray_mock = mock.Mock()
    settings = {
        "notifications_enabled": False,
        "notify_connecting": True,
        "notify_connected": True,
        "notify_disconnected": True,
        "notify_connection_error": True,
        "notify_sub_updated": True,
        "notify_sub_error": True,
        "notify_killswitch": True,
        "notify_tray_minimized": True,
    }
    manager = NotificationManager(tray_mock, settings)

    assert manager.notify_connecting("Server 1") is False
    assert manager.notify_connected("1.2.3.4") is False
    assert manager.notify_disconnected() is False
    assert manager.notify_connection_error("Error") is False
    assert manager.notify_killswitch_failed("Failed") is False
    assert manager.notify_sub_updated(3) is False
    assert manager.notify_sub_error("Sub1", "Err") is False
    assert manager.notify_tray_minimized() is False
    assert tray_mock.notify.call_count == 0


def test_notification_manager_individual_toggles():
    tray_mock = mock.Mock()
    settings = {
        "notifications_enabled": True,
        "notify_connecting": False,
        "notify_connected": True,
        "notify_disconnected": False,
        "notify_connection_error": True,
        "notify_sub_updated": False,
        "notify_sub_error": True,
        "notify_killswitch": False,
        "notify_tray_minimized": True,
    }
    manager = NotificationManager(tray_mock, settings)

    # Disabled
    assert manager.notify_connecting("Server 1") is False
    assert manager.notify_disconnected() is False
    assert manager.notify_sub_updated(3) is False
    assert manager.notify_killswitch_failed("Failed") is False
    assert tray_mock.notify.call_count == 0

    # Enabled
    assert manager.notify_connected("1.2.3.4") is True
    assert manager.notify_connection_error("Error") is True
    assert manager.notify_sub_error("Sub1", "Err") is True
    assert manager.notify_tray_minimized() is True
    assert tray_mock.notify.call_count == 4


def test_settings_screen_notifications_hub_and_navigation():
    theme = M3Theme(preset_key="lavender")
    settings = {
        "theme_preset": "lavender",
        "notifications_enabled": True,
        "notify_connecting": True,
        "notify_connected": True,
        "notify_disconnected": True,
        "notify_connection_error": True,
        "notify_sub_updated": True,
        "notify_sub_error": True,
        "notify_killswitch": True,
        "notify_tray_minimized": True,
    }
    screen = SettingsScreen(settings, theme)
    screen.show()

    # 1. HubRow verification
    assert hasattr(screen, "hub_row_notifications")
    assert screen.hub_row_notifications.position == "middle"
    assert screen.hub_row_notifications.role == "warning"
    assert screen.hub_row_notifications.icon_name == "hub_notifications"
    assert screen.hub_row_notifications.subtitle_text == tr("set.notify_status_on")

    # 2. Open section lazy-build
    assert screen.sw_notif_master is None
    assert "notifications" not in screen._built_sections
    screen.open_section("notifications", "Notifications")
    assert "notifications" in screen._built_sections
    assert screen.sw_notif_master is not None
    assert screen.card_notifications.isVisible() is True
    assert screen.card_hub.isHidden() is True

    # 3. All switches created with initial state
    assert screen.sw_notif_master.isChecked() is True
    assert screen.sw_notif_connecting.isChecked() is True
    assert screen.sw_notif_connected.isChecked() is True
    assert screen.sw_notif_disconnected.isChecked() is True
    assert screen.sw_notif_connection_error.isChecked() is True
    assert screen.sw_notif_sub_updated.isChecked() is True
    assert screen.sw_notif_sub_error.isChecked() is True
    assert screen.sw_notif_killswitch.isChecked() is True
    assert screen.sw_notif_tray_minimized.isChecked() is True
    assert screen.notif_types_box.isEnabled() is True

    # 4. Escape returns to hub
    key_ev = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    screen.keyPressEvent(key_ev)
    assert screen.card_hub.isVisible() is True
    assert screen.card_notifications.isHidden() is True


def test_settings_screen_notifications_toggles_and_live_values():
    theme = M3Theme(preset_key="lavender")
    settings = {
        "theme_preset": "lavender",
        "notifications_enabled": True,
    }
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("notifications", "Notifications")

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))

    # Toggle one off
    screen.sw_notif_connecting.toggled.emit(False)
    assert ("notify_connecting", False) in emitted
    assert screen.settings["notify_connecting"] is False
    assert screen.hub_row_notifications.subtitle_text == tr("set.notify_status_count").format(enabled=7, total=8)

    # Master switch toggled off turns off all child toggles and updates subtitle
    screen.sw_notif_master.toggled.emit(False)
    assert ("notifications_enabled", False) in emitted
    assert screen.sw_notif_master.isChecked() is False
    assert screen.sw_notif_connecting.isChecked() is False
    assert screen.sw_notif_connected.isChecked() is False
    assert screen.sw_notif_disconnected.isChecked() is False
    assert screen.hub_row_notifications.subtitle_text == tr("set.notify_status_off")

    # One child toggle turned on turns master back on
    screen.sw_notif_connected.toggled.emit(True)
    assert ("notify_connected", True) in emitted
    assert ("notifications_enabled", True) in emitted
    assert screen.sw_notif_master.isChecked() is True
    assert screen.sw_notif_connected.isChecked() is True
    assert screen.sw_notif_connecting.isChecked() is False
    assert screen.hub_row_notifications.subtitle_text == tr("set.notify_status_count").format(enabled=1, total=8)

    # Master switch turned off turns all off again
    screen.sw_notif_master.toggled.emit(False)
    assert screen.sw_notif_master.isChecked() is False
    assert screen.sw_notif_connected.isChecked() is False

    # Master switch turned on when all were off turns all on
    screen.sw_notif_master.toggled.emit(True)
    assert screen.sw_notif_master.isChecked() is True
    assert screen.sw_notif_connecting.isChecked() is True
    assert screen.sw_notif_connected.isChecked() is True
    assert screen.sw_notif_disconnected.isChecked() is True
    assert screen.sw_notif_connection_error.isChecked() is True
    assert screen.sw_notif_sub_updated.isChecked() is True
    assert screen.sw_notif_sub_error.isChecked() is True
    assert screen.sw_notif_killswitch.isChecked() is True
    assert screen.sw_notif_tray_minimized.isChecked() is True
    assert screen.hub_row_notifications.subtitle_text == tr("set.notify_status_on")
