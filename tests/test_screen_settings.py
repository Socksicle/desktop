"""Unit tests for SettingsScreen Material 3 mobile hub architecture and ping display modes."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeyEvent

from ui.screen_settings import SettingsScreen
from ui.server_item import ServerItem
from ui.server_list_panel import ServerListPanel
from utils.theme import M3Theme
from utils.server_model import Server, ProxyProtocol
from utils.i18n import SUPPORTED_LANGUAGES


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_settings_screen_hub_layout_navigation_and_live_values():
    theme = M3Theme(preset_key="lavender")
    settings = {
        "theme_preset": "lavender",
        "ping_method": "http_head",
        "ping_timeout": 3,
        "socks5_port": 2080,
        "killswitch": True,
        "engine": "xray",
        "tun_mode": True,
    }
    screen = SettingsScreen(settings, theme)
    screen.show()

    # 1. Groups, spacing, and roles
    assert hasattr(screen, "group_network")
    assert screen.group_network.layout().spacing() == 3
    assert screen.hub_row_connection.position == "top"
    assert screen.hub_row_connection.role == "primary"
    assert screen.hub_row_tunnel.position == "middle"
    assert screen.hub_row_tunnel.role == "primary"
    assert screen.hub_row_ping.position == "middle"
    assert screen.hub_row_ping.role == "primary"
    assert screen.hub_row_subs.position == "bottom"
    assert screen.hub_row_subs.role == "primary"

    assert hasattr(screen, "group_app")
    assert screen.group_app.layout().spacing() == 3
    assert screen.hub_row_appearance.position == "top"
    assert screen.hub_row_appearance.role == "tertiary"
    assert screen.hub_row_system.position == "bottom"
    assert screen.hub_row_system.role == "secondary"

    # 2. Live values and badges
    assert screen.hub_row_ping.subtitle_text == "HTTP-HEAD, 3 сек, Цифры"
    assert screen.hub_row_connection.subtitle_text == "2080, Kill Switch, xray"
    assert screen.hub_row_connection.badge_lbl.isHidden() is True

    screen._emit_setting("ping_method", "thp")
    assert "THP" in screen.hub_row_ping.subtitle_text

    screen._emit_setting("ping_display", 1)
    assert "Шкала" in screen.hub_row_ping.subtitle_text

    screen._emit_setting("tun_mode", False)
    assert screen.hub_row_connection.badge_lbl.isHidden() is True

    screen._emit_setting("killswitch", False)
    assert screen.hub_row_connection.subtitle_text == "2080, xray"

    screen._emit_setting("engine", "sing-box")
    assert screen.hub_row_connection.subtitle_text == "2080, sing-box"

    screen._emit_setting("local_port", 3080)
    assert screen.hub_row_connection.subtitle_text == "3080, sing-box"

    # 3. Hub navigation: initial state, enter section, back button, escape key
    assert screen.card_hub.isHidden() is False
    assert screen.card_appearance.isHidden() is True
    assert screen.card_connection.isHidden() is True
    assert screen.card_tunnel.isHidden() is True
    assert screen.card_ping.isHidden() is True
    assert screen.card_subs.isHidden() is True
    assert screen.card_system.isHidden() is True
    assert not hasattr(screen, "card_advanced")
    assert screen.btn_back.isHidden() is True
    assert screen.title_label.text() == "Настройки"

    # Navigate to Ping section
    screen.hub_row_ping.clicked.emit()
    assert screen.card_hub.isHidden() is True
    assert screen.card_ping.isHidden() is False
    assert screen.btn_back.isHidden() is False
    assert screen.title_label.text() == "Пинг"

    # Click Back button returns to Hub
    screen.btn_back.click()
    assert screen.card_hub.isHidden() is False
    assert screen.card_ping.isHidden() is True
    assert screen.btn_back.isHidden() is True
    assert screen.title_label.text() == "Настройки"

    # Navigate to Appearance, then press Escape to return
    screen.hub_row_appearance.clicked.emit()
    assert screen.card_appearance.isHidden() is False
    assert screen.btn_back.isHidden() is False

    ev = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    screen.keyPressEvent(ev)
    assert screen.card_hub.isHidden() is False
    assert screen.card_appearance.isHidden() is True
    assert screen.btn_back.isHidden() is True


def test_settings_and_server_item_ping_display_modes():
    theme = M3Theme(preset_key="lavender")
    settings = {"ping_display": 1}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("ping", "Пинг")

    assert hasattr(screen, "group_ping_display")
    assert screen.group_ping_display.current_index() == 1

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))

    # Select Mode 0 (Numbers)
    screen.group_ping_display.selectionChanged.emit(0)
    assert ("ping_display", 0) in emitted
    assert screen.settings["ping_display"] == 0

    # Select Mode 2 (Both)
    screen.group_ping_display.selectionChanged.emit(2)
    assert ("ping_display", 2) in emitted
    assert screen.settings["ping_display"] == 2

    # ServerItem rendering
    srv = Server(name="Test Server", host="1.2.3.4", port=443, protocol=ProxyProtocol.VLESS)

    # Mode 0: Numbers only
    item_numbers = ServerItem(srv.name, srv, theme, ping_display_mode=0)
    item_numbers.show()
    item_numbers.set_ping(45.0)
    assert item_numbers.ping_label.isHidden() is False
    assert item_numbers.ping_bars.isHidden() is True
    assert "45ms" in item_numbers.ping_label.text()

    # Mode 1: Bars only
    item_bars = ServerItem(srv.name, srv, theme, ping_display_mode=1)
    item_bars.show()
    item_bars.set_ping(80.0)
    assert item_bars.ping_label.isHidden() is True
    assert item_bars.ping_bars.isHidden() is False

    # Mode 2: Both
    item_both = ServerItem(srv.name, srv, theme, ping_display_mode=2)
    item_both.show()
    item_both.set_ping(120.0)
    assert item_both.ping_label.isHidden() is False
    assert item_both.ping_bars.isHidden() is False

    # Dynamic change via set_ping_display_mode
    item_both.set_ping_display_mode(1)
    assert item_both.ping_label.isHidden() is True
    assert item_both.ping_bars.isHidden() is False

    item_both.set_ping_display_mode(0)
    assert item_both.ping_label.isHidden() is False
    assert item_both.ping_bars.isHidden() is True


def test_connection_section_engine_and_dns_combos():
    from ui.settings_options import DNS_PRESET_URLS
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "engine": "sing-box", "dns_provider": "system", "custom_dns": ""}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("connection", "Connection")

    # Available engines
    values = [screen.engine_combo.itemData(i) for i in range(screen.engine_combo.count())]
    assert values == ["auto", "sing-box", "xray"]

    # Unknown engine heals to auto
    settings2 = {"theme_preset": "lavender", "engine": "unknown"}
    screen2 = SettingsScreen(settings2, theme)
    screen2.open_section("connection", "Connection")
    assert settings2["engine"] == "auto"
    assert screen2.engine_combo.currentData() == "auto"

    # DNS combo updates custom_dns
    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))

    # Select AdGuard
    adguard_idx = screen.dns_combo.findData("adguard")
    assert adguard_idx >= 0
    screen.dns_combo.setCurrentIndex(adguard_idx)
    assert ("dns_provider", "adguard") in emitted
    assert ("custom_dns", DNS_PRESET_URLS["adguard"]) in emitted
    assert screen.settings["custom_dns"] == DNS_PRESET_URLS["adguard"]
    assert screen.edit_custom_dns.isHidden() is True

    # Select System (clears custom_dns)
    sys_idx = screen.dns_combo.findData("system")
    screen.dns_combo.setCurrentIndex(sys_idx)
    assert ("dns_provider", "system") in emitted
    assert ("custom_dns", "") in emitted
    assert screen.settings["custom_dns"] == ""

    # Select Custom (shows edit field)
    custom_idx = screen.dns_combo.findData("custom")
    screen.dns_combo.setCurrentIndex(custom_idx)
    assert ("dns_provider", "custom") in emitted
    assert screen.edit_custom_dns.isHidden() is False

    # Type in custom field
    screen.edit_custom_dns.setText("https://my-custom-dns.com/dns-query")
    assert ("custom_dns", "https://my-custom-dns.com/dns-query") in emitted
    assert screen.settings["custom_dns"] == "https://my-custom-dns.com/dns-query"


def test_settings_architecture_lazy_build_and_option_keys():
    from ui import settings_options as opt
    from utils.ping import DEFAULT_PING_METHOD

    # Lazy building
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()

    assert screen.engine_combo is None
    assert screen.group_ping_display is None
    assert screen.sw_show_flags is None
    assert screen._built_sections == set()

    screen.open_section("ping", "Пинг")
    assert "ping" in screen._built_sections
    assert screen.group_ping_display is not None
    assert screen.engine_combo is None

    # Reopening does not rebuild
    group = screen.group_ping_display
    screen.show_hub()
    screen.open_section("ping", "Пинг")
    assert screen.group_ping_display is group

    # Option keys unique & ping methods match
    assert DEFAULT_PING_METHOD in [key for key, _, _ in opt.PING_METHOD_OPTIONS]
    for table in (opt.ENGINE_OPTIONS, opt.DNS_OPTIONS, opt.USER_AGENT_OPTIONS):
        keys = [key for key, _ in table]
        assert len(keys) == len(set(keys))
    swatches = [key for key, _, _, _ in opt.THEME_SWATCHES]
    assert len(swatches) == len(set(swatches))
    methods = [key for key, _, _ in opt.PING_METHOD_OPTIONS]
    assert len(methods) == len(set(methods))


def test_appearance_section_theme_spec_and_swatches(monkeypatch):
    from PySide6.QtGui import QColor
    from ui import screen_settings as ss

    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("appearance", "Appearance")

    # 1. Theme spec combo switches spec and emits themeChanged
    assert screen.theme_spec_combo is not None
    assert screen.theme_spec_combo.currentData() == "2021"

    emitted_spec = []
    screen.themeChanged.connect(emitted_spec.append)
    screen.theme_spec_combo.setCurrentIndex(screen.theme_spec_combo.findData("2025"))
    assert settings["theme_spec"] == "2025"
    assert emitted_spec == ["lavender"]

    # 2. Custom swatch accept stores color and emits
    monkeypatch.setattr(ss.QColorDialog, "getColor", lambda *a: QColor("#123456"))
    emitted_accept = []
    screen.themeChanged.connect(emitted_accept.append)
    custom = next(b for b in screen._swatches if b.key == "custom")
    custom.clicked.emit("custom")
    assert settings["theme_preset"] == "custom"
    assert settings["theme_custom_color"] == "#123456"
    assert emitted_accept == ["custom"]
    assert custom.color_hex == "#123456"

    # 3. Custom swatch cancel keeps preset
    settings["theme_preset"] = "lavender"
    del settings["theme_custom_color"]
    monkeypatch.setattr(ss.QColorDialog, "getColor", lambda *a: QColor())
    emitted_cancel = []
    screen.themeChanged.connect(emitted_cancel.append)
    custom.clicked.emit("custom")
    assert settings["theme_preset"] == "lavender"
    assert "theme_custom_color" not in settings
    assert emitted_cancel == []


def test_tunnel_section_controls_and_saved_values():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()

    assert screen.sw_block_udp is None
    assert screen.sw_tls_fragment is None
    assert screen.sw_mux is None
    assert screen.mux_settings is None

    screen.open_section("tunnel", "Tunnel")
    assert screen.card_tunnel.isHidden() is False
    assert screen.sw_block_udp is not None
    assert screen.sw_tls_fragment is not None
    assert screen.sw_mux is not None
    assert screen.mux_settings is not None
    assert screen.mux_settings.isHidden() is True
    assert screen.mux_protocol_combo.currentData() == "h2mux"
    assert screen.mux_max_conn_spin.value() == 4

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))

    # Toggle block UDP
    screen.sw_block_udp.toggled.emit(True)
    assert ("block_udp", True) in emitted
    assert screen.settings["block_udp"] is True

    # Toggle tls fragment
    screen.sw_tls_fragment.toggled.emit(True)
    assert ("tls_fragment", True) in emitted
    assert settings["tls_fragment"] is True

    # Toggle mux
    screen.sw_mux.toggled.emit(True)
    assert ("mux_enabled", True) in emitted
    assert settings["mux_enabled"] is True
    assert screen.mux_settings.isHidden() is False

    screen.mux_protocol_combo.setCurrentIndex(screen.mux_protocol_combo.findData("yamux"))
    assert ("mux_protocol", "yamux") in emitted

    screen.mux_max_conn_spin.setValue(8)
    assert ("mux_max_connections", 8) in emitted

    screen.sw_mux.toggled.emit(False)
    assert ("mux_enabled", False) in emitted
    assert screen.mux_settings.isHidden() is True

    # Restore saved values
    settings_saved = {
        "theme_preset": "lavender",
        "tls_fragment": True,
        "mux_enabled": True,
        "mux_protocol": "smux",
        "mux_max_connections": 12,
    }
    screen_saved = SettingsScreen(settings_saved, theme)
    screen_saved.show()
    screen_saved.open_section("tunnel", "Tunnel")
    assert screen_saved.sw_tls_fragment.isChecked() is True
    assert screen_saved.sw_mux.isChecked() is True
    assert screen_saved.mux_settings.isHidden() is False
    assert screen_saved.mux_protocol_combo.currentData() == "smux"
    assert screen_saved.mux_max_conn_spin.value() == 12


def test_subscription_section_and_user_agent_presets(monkeypatch):
    from ui.settings_options import USER_AGENT_OPTIONS
    from utils.subscription_store import USER_AGENT_PRESETS
    from utils import platform_utils as pu

    # Section controls
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()

    assert screen.sw_auto_update_subs is None
    assert screen.ua_combo is None
    screen.open_section("subs", "Подписки")
    assert screen.sw_auto_update_subs is not None
    assert screen.ua_combo is not None
    assert screen.card_subs.isHidden() is False

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    screen.ua_combo.setCurrentIndex(screen.ua_combo.findData("v2rayng"))
    assert ("user_agent_key", "v2rayng") in emitted
    assert settings["user_agent_key"] == "v2rayng"

    # User agent options match presets
    expected_labels = {
        "sing-box/1.14.3", "Mihomo/1.19.32", "ClashVerge/2.5.8", "Hiddify/4.1.1",
        "v2rayN/7.25.6", "FlClash/0.9.1", "karing/1.2.26", "NekoBox/4.0.1",
        "NekoRay/4.0.1", "MihomoParty/1.5.0", "v2rayNG/2.3.11", "Happ/4.6.0",
    }
    options_dict = dict(USER_AGENT_OPTIONS)
    labels = set(options_dict.values())
    assert expected_labels.issubset(labels)

    for key, label in USER_AGENT_OPTIONS:
        assert key in USER_AGENT_PRESETS
        assert USER_AGENT_PRESETS[key] == label

    assert options_dict["socksicle"].startswith("socksicle/")
    assert options_dict["socksicle"] == USER_AGENT_PRESETS["socksicle"]

    # Socksicle UA platforms
    monkeypatch.setattr(pu, "get_real_app_version", lambda: "1.2.3")
    monkeypatch.setattr(pu, "is_windows", lambda: True)
    monkeypatch.setattr(pu, "is_linux", lambda: False)
    assert pu.get_socksicle_user_agent() == "socksicle/1.2.3/windows"

    monkeypatch.setattr(pu, "is_windows", lambda: False)
    monkeypatch.setattr(pu, "is_linux", lambda: True)
    assert pu.get_socksicle_user_agent() == "socksicle/1.2.3/linux"
