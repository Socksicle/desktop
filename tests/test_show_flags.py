"""Country-flags visibility toggle (mobile showFlags parity)."""
import pytest

from ui.card_active_server import CardActiveServer
from ui.bento_telemetry_card import BentoTelemetryCard
from ui.main_window import RoundedWindow
from ui.screen_main import ScreenMain
from ui.screen_settings import SettingsScreen
from ui.server_item import ServerItem
from ui.server_list_panel import ServerListPanel
from utils.server_model import Server, ProxyProtocol
from utils.theme import M3Theme
import utils.twinsock as tw


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


@pytest.fixture
def main_win(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    return RoundedWindow()


def _srv(name="Germany 🇩🇪", host="1.2.3.4"):
    return Server(name=name, host=host, port=443, password="pw",
                  protocol=ProxyProtocol.SHADOWSOCKS, flag_emoji="🇩🇪")


def test_server_item_flags_toggle():
    theme = M3Theme(preset_key="lavender")
    item = ServerItem("Germany 🇩🇪", _srv(), theme)
    assert item.radio._show_flags is False
    item.set_show_flags(True)
    assert item.radio._show_flags is True
    item.set_show_flags(False)
    assert item.radio._show_flags is False


def test_server_list_panel_flags_propagate():
    theme = M3Theme(preset_key="lavender")
    panel = ServerListPanel(theme)
    panel.show()
    panel.refresh([_srv("A 🇩🇪"), _srv("B 🇫🇷", host="5.6.7.8")])
    assert all(not i.radio._show_flags for i in panel._server_items)

    panel.set_show_flags(True)
    assert all(i.radio._show_flags for i in panel._server_items)

    # New rows inherit the current mode.
    panel.refresh([_srv("C 🇩🇪")])
    assert panel._server_items[0].radio._show_flags is True


def test_card_active_server_flags_fallback():
    theme = M3Theme(preset_key="lavender")
    card = CardActiveServer(theme=theme)
    card.set_server({"name": "N", "server": "h", "server_port": 443, "flag": "🇩🇪"})
    assert card.flag_label.text() == "🌐"
    card.set_show_flags(True)
    assert card.flag_label.text() == "🇩🇪"
    card.set_show_flags(False)
    assert card.flag_label.text() == "🌐"


def test_telemetry_card_flags_toggle():
    theme = M3Theme(preset_key="lavender")
    card = BentoTelemetryCard(theme=theme)
    card.set_geo_info("1.1.1.1", "Berlin, Germany", "🇩🇪")
    assert "🇩🇪" not in card.country_text.text()
    assert "Berlin" in card.country_text.text()
    card.set_show_flags(True)
    assert "🇩🇪" in card.country_text.text()
    card.set_show_flags(False)
    assert "🇩🇪" not in card.country_text.text()


def test_screen_main_set_show_flags():
    theme = M3Theme(preset_key="lavender")
    screen = ScreenMain(theme)
    screen.show()
    screen.server_panel.refresh([_srv("A 🇩🇪")])
    screen.set_show_flags(False)
    assert screen.server_panel._show_flags is False
    assert screen.server_panel._server_items[0].radio._show_flags is False
    assert screen.card_active.flag_label.text() == "🌐"
    screen.set_show_flags(True)
    assert screen.server_panel._server_items[0].radio._show_flags is True


def test_settings_screen_flags_toggle_emits():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("appearance", "Тема и интерфейс")
    assert hasattr(screen, "sw_show_flags")
    assert screen.sw_show_flags.isChecked() is False

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    # AnimatedToggleSwitch emits `toggled` on mouse click; emulate it.
    screen.sw_show_flags.toggled.emit(True)
    assert ("show_flags", True) in emitted
    assert settings["show_flags"] is True


def test_main_window_applies_flags_setting(main_win):
    win = main_win
    assert win.screen_main.server_panel._show_flags is False
    win._on_setting_value_changed("show_flags", True)
    assert win.server_manager.settings["show_flags"] is True
    assert win.screen_main.server_panel._show_flags is True
    assert win.screen_main.card_active._show_flags is True
    assert win.screen_main.card_telemetry._show_flags is True
