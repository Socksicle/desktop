"""Unit tests for SettingsScreen Material 3 mobile hub architecture and ping display modes."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

from ui.screen_settings import SettingsScreen
from ui.server_item import ServerItem
from ui.server_list_panel import ServerListPanel
from utils.theme import M3Theme
from utils.server_model import Server, ProxyProtocol


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_settings_screen_hub_navigation():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "ping_display": 0}
    screen = SettingsScreen(settings, theme)
    screen.show()

    # Initial state: on Hub
    assert screen.card_hub.isHidden() is False
    assert screen.card_appearance.isHidden() is True
    assert screen.card_connection.isHidden() is True
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

    # Simulate Escape key
    ev = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    screen.keyPressEvent(ev)
    assert screen.card_hub.isHidden() is False
    assert screen.card_appearance.isHidden() is True
    assert screen.btn_back.isHidden() is True


def test_settings_screen_ping_display_option_group():
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


def test_server_item_ping_display_modes():
    theme = M3Theme(preset_key="ocean")
    srv = Server(name="Test Server", host="1.2.3.4", port=443, protocol=ProxyProtocol.VLESS)

    # 1. Mode 0: Numbers only (ping_label visible, ping_bars hidden)
    item_numbers = ServerItem(srv.name, srv, theme, ping_display_mode=0)
    item_numbers.show()
    item_numbers.set_ping(45.0)
    assert item_numbers.ping_label.isHidden() is False
    assert item_numbers.ping_bars.isHidden() is True
    assert "45ms" in item_numbers.ping_label.text()

    # 2. Mode 1: Bars only (ping_bars visible, label hidden)
    item_bars = ServerItem(srv.name, srv, theme, ping_display_mode=1)
    item_bars.show()
    item_bars.set_ping(80.0)
    assert item_bars.ping_label.isHidden() is True
    assert item_bars.ping_bars.isHidden() is False

    # 3. Mode 2: Both (both visible)
    item_both = ServerItem(srv.name, srv, theme, ping_display_mode=2)
    item_both.show()
    item_both.set_ping(120.0)
    assert item_both.ping_label.isHidden() is False
    assert item_both.ping_bars.isHidden() is False

    # 4. Dynamic change via set_ping_display_mode
    item_both.set_ping_display_mode(1)
    assert item_both.ping_label.isHidden() is True
    assert item_both.ping_bars.isHidden() is False

    item_both.set_ping_display_mode(0)
    assert item_both.ping_label.isHidden() is False
    assert item_both.ping_bars.isHidden() is True


def test_server_list_panel_set_ping_display_mode():
    theme = M3Theme()
    panel = ServerListPanel(theme, ping_display_mode=0)
    panel.show()
    srv1 = Server(name="S1", host="1.1.1.1", port=443)
    srv2 = Server(name="S2", host="2.2.2.2", port=443)
    panel.refresh([srv1, srv2])

    assert len(panel._server_items) == 2
    # Apply ping to first item
    panel._server_items[0].set_ping(50.0)
    assert panel._server_items[0].ping_label.isHidden() is False
    assert panel._server_items[0].ping_bars.isHidden() is True

    # Switch entire panel to Bars mode (1)
    panel.set_ping_display_mode(1)
    assert panel._server_items[0].ping_label.isHidden() is True
    assert panel._server_items[0].ping_bars.isHidden() is False

    # Switch entire panel to Both mode (2)
    panel.set_ping_display_mode(2)
    assert panel._server_items[0].ping_label.isHidden() is False
    assert panel._server_items[0].ping_bars.isHidden() is False


def test_card_active_server_ping_display_modes():
    from ui.card_active_server import CardActiveServer
    theme = M3Theme()

    # 1. Mode 0: Numbers only (bars hidden, label visible)
    card0 = CardActiveServer(theme=theme, ping_display_mode=0)
    card0.show()
    card0.set_latency(65.0)
    assert card0.ping_label.isHidden() is False
    assert card0.ping_bars.isHidden() is True
    assert "65 ms" in card0.ping_label.text()

    # 2. Mode 1: Bars only (bars visible, label hidden)
    card1 = CardActiveServer(theme=theme, ping_display_mode=1)
    card1.show()
    card1.set_latency(65.0)
    assert card1.ping_label.isHidden() is True
    assert card1.ping_bars.isHidden() is False

    # 3. Mode 2: Both
    card2 = CardActiveServer(theme=theme, ping_display_mode=2)
    card2.show()
    card2.set_latency(65.0)
    assert card2.ping_label.isHidden() is False
    assert card2.ping_bars.isHidden() is False

    # 4. Dynamic change
    card0.set_ping_display_mode(1)
    assert card0.ping_label.isHidden() is True
    assert card0.ping_bars.isHidden() is False


def test_engine_combo_lists_only_available_engines():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "engine": "sing-box"}
    screen = SettingsScreen(settings, theme)
    screen.open_section("connection", "Соединение")
    values = [screen.engine_combo.itemData(i) for i in range(screen.engine_combo.count())]
    assert values == ["sing-box", "xray"]


def test_engine_combo_heals_unknown_engine_setting():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "engine": "unknown"}
    screen = SettingsScreen(settings, theme)
    screen.open_section("connection", "Соединение")
    assert settings["engine"] == "sing-box"
    assert screen.engine_combo.currentData() == "sing-box"


def test_settings_sections_build_lazily():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()

    # Nothing but the hub is built: no section widgets exist yet.
    assert screen.engine_combo is None
    assert screen.group_ping_display is None
    assert screen.sw_show_flags is None
    assert screen._built_sections == set()

    screen.open_section("ping", "Пинг")
    assert "ping" in screen._built_sections
    assert screen.group_ping_display is not None
    # Other sections stay unbuilt.
    assert screen.engine_combo is None

    # Reopening does not rebuild.
    group = screen.group_ping_display
    screen.show_hub()
    screen.open_section("ping", "Пинг")
    assert screen.group_ping_display is group


def test_settings_ping_methods_match_engine():
    from ui import settings_options as opt
    from utils.ping import DEFAULT_PING_METHOD
    assert DEFAULT_PING_METHOD in [key for key, _, _ in opt.PING_METHOD_OPTIONS]


def test_settings_option_keys_unique():
    from ui import settings_options as opt
    for table in (opt.RENDER_OPTIONS, opt.ENGINE_OPTIONS, opt.DNS_OPTIONS,
                  opt.USER_AGENT_OPTIONS):
        keys = [key for key, _ in table]
        assert len(keys) == len(set(keys))
    swatches = [key for key, _, _, _ in opt.THEME_SWATCHES]
    assert len(swatches) == len(set(swatches))
    methods = [key for key, _, _ in opt.PING_METHOD_OPTIONS]
    assert len(methods) == len(set(methods))


def test_language_lives_in_appearance_section():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "language": "ru"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("system", "System")
    assert screen.lang_combo is None
    screen.open_section("appearance", "Appearance")
    assert screen.lang_combo is not None
    assert screen.lang_combo.count() == 2


def test_hy2_insecure_toggle_in_connection_section():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    assert screen.sw_hy2_insecure is None
    screen.open_section("connection", "Connection")
    assert screen.sw_hy2_insecure is not None

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    screen.sw_hy2_insecure.toggled.emit(True)
    assert ("hy2_insecure", True) in emitted
    assert screen.settings["hy2_insecure"] is True


def test_block_udp_toggle_in_connection_section():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    assert screen.sw_block_udp is None
    screen.open_section("connection", "Connection")
    assert screen.sw_block_udp is not None

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    screen.sw_block_udp.toggled.emit(True)
    assert ("block_udp", True) in emitted
    assert screen.settings["block_udp"] is True


def test_socks5_port_lives_in_connection_section():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "local_port": 1080}
    screen = SettingsScreen(settings, theme)
    screen.show()
    assert screen.port_spin is None
    screen.open_section("connection", "Соединение")
    assert screen.port_spin is not None
    assert screen.port_spin.value() == 1080
    assert screen.card_connection.isHidden() is False


def test_subs_section_holds_autoupdate_and_user_agent():
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
    screen.ua_combo.setCurrentIndex(
        screen.ua_combo.findData("v2rayng"))
    assert ("user_agent_key", "v2rayng") in emitted
    assert settings["user_agent_key"] == "v2rayng"


def test_system_section_holds_logs_without_subs_widgets():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("system", "System")
    assert screen.btn_logs is not None
    assert screen.sw_auto_update_subs is None
    assert screen.card_system.isHidden() is False


def test_custom_swatch_accept_stores_color_and_emits(monkeypatch):
    from PySide6.QtGui import QColor
    from ui import screen_settings as ss

    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("appearance", "Appearance")
    monkeypatch.setattr(ss.QColorDialog, "getColor", lambda *a: QColor("#123456"))

    emitted = []
    screen.themeChanged.connect(emitted.append)
    custom = next(b for b in screen._swatches if b.key == "custom")
    custom.clicked.emit("custom")

    assert settings["theme_preset"] == "custom"
    assert settings["theme_custom_color"] == "#123456"
    assert emitted == ["custom"]
    assert custom.color_hex == "#123456"


def test_custom_swatch_cancel_keeps_preset(monkeypatch):
    from PySide6.QtGui import QColor
    from ui import screen_settings as ss

    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("appearance", "Appearance")
    monkeypatch.setattr(ss.QColorDialog, "getColor", lambda *a: QColor())

    emitted = []
    screen.themeChanged.connect(emitted.append)
    custom = next(b for b in screen._swatches if b.key == "custom")
    custom.clicked.emit("custom")

    assert settings["theme_preset"] == "lavender"
    assert "theme_custom_color" not in settings
    assert emitted == []


def test_custom_swatch_button_renders_plus_sign(qapp):
    """Verify that ThemeSwatchButton with key='custom' repaints without error when unselected and selected."""
    from ui.screen_settings import ThemeSwatchButton
    btn = ThemeSwatchButton("custom", "Custom", "#B388FF")
    btn.resize(38, 38)
    btn.set_selected(False)
    btn.repaint()

    btn.set_selected(True)
    btn.set_color("#FF5722")
    btn.repaint()
    assert btn.color_hex == "#FF5722"
    assert btn._selected is True


def test_dns_combo_preset_updates_custom_dns():
    from ui.settings_options import DNS_PRESET_URLS
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "dns_provider": "system", "custom_dns": ""}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("connection", "Connection")

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


def test_settings_hub_segmented_groups_and_roles():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "ping_method": "thp", "tun_mode": False}
    screen = SettingsScreen(settings, theme)
    screen.show()

    # Network group
    assert hasattr(screen, "group_network")
    assert screen.group_network.layout().spacing() == 3
    assert screen.hub_row_connection.position == "top"
    assert screen.hub_row_connection.role == "primary"
    assert screen.hub_row_ping.position == "middle"
    assert screen.hub_row_ping.role == "primary"
    assert screen.hub_row_subs.position == "bottom"
    assert screen.hub_row_subs.role == "primary"

    # Application group
    assert hasattr(screen, "group_app")
    assert screen.group_app.layout().spacing() == 3
    assert screen.hub_row_appearance.position == "top"
    assert screen.hub_row_appearance.role == "tertiary"
    assert screen.hub_row_system.position == "bottom"
    assert screen.hub_row_system.role == "secondary"


def test_settings_hub_live_values_ping_and_connection():
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

    # Check ping live value: method, timeout (seconds), display mode
    assert screen.hub_row_ping.subtitle_text == "TCP-HEAD, 3 сек, Цифры"

    # Check connection live value and TUN badge
    assert screen.hub_row_connection.subtitle_text == "2080, Kill Switch, xray"
    assert screen.hub_row_connection.badge_lbl.text() == "TUN"
    assert screen.hub_row_connection.badge_lbl.isHidden() is False

    # Dynamically change ping method
    screen._emit_setting("ping_method", "thp")
    assert "THP" in screen.hub_row_ping.subtitle_text

    # Dynamically change the ping display mode
    screen._emit_setting("ping_display", 1)
    assert "Шкала" in screen.hub_row_ping.subtitle_text

    # Dynamically change TUN mode to off (badge hides)
    screen._emit_setting("tun_mode", False)
    assert screen.hub_row_connection.badge_lbl.isHidden() is True

    # Kill Switch off hides its label; engine and port stay compact
    screen._emit_setting("killswitch", False)
    assert screen.hub_row_connection.subtitle_text == "2080, xray"

    screen._emit_setting("engine", "sing-box")
    assert screen.hub_row_connection.subtitle_text == "2080, sing-box"

    screen._emit_setting("local_port", 3080)
    assert screen.hub_row_connection.subtitle_text == "3080, sing-box"


def test_settings_killswitch_in_connection_section():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "killswitch": False}
    screen = SettingsScreen(settings, theme)
    screen.show()

    # Not on hub
    assert not hasattr(screen, "quick_status_card")

    # Lives in connection section
    assert screen.sw_killswitch is None
    screen.open_section("connection", "Connection")
    assert screen.sw_killswitch is not None
    assert screen.sw_killswitch.isChecked() is False

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    screen.sw_killswitch.toggled.emit(True)
    assert ("killswitch", True) in emitted
    assert screen.settings["killswitch"] is True


def test_settings_hub_chevrons_aligned():
    from ui.chevron_view import ChevronView
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender", "tun_mode": True}
    screen = SettingsScreen(settings, theme)
    screen.resize(440, 700)
    screen.show()

    rows = [
        screen.hub_row_connection,
        screen.hub_row_ping,
        screen.hub_row_subs,
        screen.hub_row_appearance,
        screen.hub_row_system,
    ]

    for row in rows:
        assert isinstance(row.chevron, ChevronView)
        assert row.chevron._angle == -90

    # Ensure all chevrons align to the exact same X and are vertically centered within each row
    xs = [r.chevron.x() for r in rows]
    assert len(set(xs)) == 1
    for r in rows:
        assert abs((r.chevron.y() + r.chevron.height() / 2) - (r.height() / 2)) <= 2

    # In connection row with TUN badge visible, badge and chevron share vertical center
    assert screen.hub_row_connection.badge_lbl.isVisible() is True
    assert abs(screen.hub_row_connection.badge_lbl.y() - screen.hub_row_connection.chevron.y()) <= 2


def test_settings_hidden_appearance_items():
    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.show()
    screen.open_section("appearance", "Внешний вид")
    assert hasattr(screen, "sw_show_server_card")
    assert hasattr(screen, "sw_show_flags")
    assert screen.sw_show_server_card.row_widget.isHidden() is True
    assert screen.sw_show_flags.row_widget.isHidden() is True
    assert screen.sw_show_ip_card.row_widget.isHidden() is False



