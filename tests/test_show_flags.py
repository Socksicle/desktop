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


def test_widgets_flags_toggle():
    theme = M3Theme(preset_key="lavender")
    # ServerItem
    item = ServerItem("Germany 🇩🇪", _srv(), theme)
    assert item.radio._show_flags is False
    assert "🇩🇪" in item.radio.text()
    item.set_show_flags(True)
    assert item.radio._show_flags is True
    assert "🇩🇪" not in item.radio.text()
    assert item.radio.flag_emoji == "🇩🇪"

    # Active Card
    card = CardActiveServer(theme=theme)
    card.set_server({"name": "N", "server": "h", "server_port": 443, "flag": "🇩🇪"})
    assert card.flag_label.text() == "🌐"
    card.set_show_flags(True)
    assert card.flag_label.text() == "🇩🇪"

    # Telemetry Card
    telemetry = BentoTelemetryCard(theme=theme)
    telemetry.set_geo_info("1.1.1.1", "Berlin, Germany", "🇩🇪")
    assert "🇩🇪" not in telemetry.country_text.text()
    telemetry.set_show_flags(True)
    assert "🇩🇪" in telemetry.country_text.text()


def test_main_window_applies_flags_setting(main_win):
    win = main_win
    assert win.screen_main.server_panel._show_flags is False
    win._on_setting_value_changed("show_flags", True)
    assert win.server_manager.settings["show_flags"] is True
    assert win.screen_main.server_panel._show_flags is True
    assert win.screen_main.card_active._show_flags is True
    assert win.screen_main.card_telemetry._show_flags is True
