"""Tests for two-language UI support (utils.i18n + catalogs)."""
import pathlib
import re

import pytest

from utils import i18n
from utils.i18n import get_language, set_language, tr, trn


@pytest.fixture(autouse=True)
def _restore_language():
    prev = get_language()
    yield
    set_language(prev)


def test_default_language_is_russian():
    assert i18n.DEFAULT_LANGUAGE == "ru"
    assert set_language(None) == "ru"
    assert set_language("xx") == "ru"
    assert set_language(" EN ") == "en"
    assert get_language() == "en"


def test_tr_fallback_chain():
    set_language("ru")
    assert tr("nav.settings") == "Настройки"
    assert tr("missing.key") == "missing.key"
    set_language("en")
    assert tr("nav.settings") == "Settings"


def test_trn_plural_forms():
    set_language("ru")
    assert trn("tray.nodes_added", 1).format(n=1) == "Добавлен 1 новый узел."
    assert trn("tray.nodes_added", 3).format(n=3) == "Добавлено 3 новых узла."
    assert trn("tray.nodes_added", 5).format(n=5) == "Добавлено 5 новых узлов."
    assert trn("tray.nodes_added", 21).format(n=21) == "Добавлен 21 новый узел."
    assert trn("sub.days_left", 1).format(n=1) == "Остался 1 день"
    assert trn("sub.days_left", 2).format(n=2) == "Осталось 2 дня"
    set_language("en")
    assert trn("tray.nodes_added", 1).format(n=1) == "Added 1 new node."
    assert trn("tray.nodes_added", 2).format(n=2) == "Added 2 new nodes."


def _used_tr_keys():
    keys = set()
    for root in (pathlib.Path("utils"), pathlib.Path("ui")):
        for path in root.rglob("*.py"):
            if path.name.startswith("i18n_"):
                continue
            text = path.read_text(encoding="utf-8")
            keys.update(re.findall(r"\btr\(\s*[\"']([^\"']+)[\"']", text))
            keys.update(re.findall(r"\btrn\(\s*[\"']([^\"']+)[\"']", text))
    return keys


def test_catalogs_cover_every_used_key():
    from utils import i18n_en, i18n_ru
    used = _used_tr_keys()
    assert used, "no tr() calls found"
    assert not [k for k in used if k not in i18n_ru.STRINGS]
    assert not [k for k in used if k not in i18n_en.STRINGS]


def test_settings_screen_renders_english(qapp):
    from utils.theme import M3Theme
    from ui.screen_settings import SettingsScreen

    set_language("en")
    theme = M3Theme(preset_key="lavender")
    screen = SettingsScreen({"theme_preset": "lavender"}, theme)
    screen.show()
    assert screen.title_label.text() == "Settings"
    screen.open_section("ping", "Ping")
    titles = [c.title_text for c in screen.group_ping_methods._cards]
    assert "THP (recommended)" in titles
    screen.open_section("appearance", "Appearance")
    langs = [screen.lang_combo.itemText(i) for i in range(screen.lang_combo.count())]
    assert langs == ["Русский", "English"]
    assert screen.lbl_lang_hint.text() == "Applies after app restart"


def test_about_and_cards_render_english(qapp):
    from utils.theme import M3Theme
    from ui.screen_about import ScreenAbout
    from ui.card_active_server import CardActiveServer
    from ui.bento_telemetry_card import BentoTelemetryCard

    set_language("en")
    theme = M3Theme(preset_key="lavender")
    about = ScreenAbout(theme, settings={})
    from PySide6.QtWidgets import QLabel
    row_texts = [w.text() for w in about.findChildren(QLabel)]
    assert "Device name" in row_texts
    assert "Core" in row_texts
    assert about.btn_copy_hwid.text() == "Copy"

    card = CardActiveServer(theme=theme)
    card.set_server(None)
    assert card.server_name_label.text() == "No server selected"
    assert card.label_target.text() == "SELECTED SERVER"

    tele = BentoTelemetryCard(theme=theme)
    assert tele.label_ip.text() == "IP ADDRESS"


def test_tray_taskbar_filter_render_english(qapp):
    from utils.theme import M3Theme
    from ui.tray_manager import TrayManager
    from ui.taskbar_view import TaskbarView
    from ui.server_filter_bar import ServerFilterBar
    from ui.header_bar import HeaderBar

    set_language("en")
    theme = M3Theme(preset_key="lavender")
    taskbar = TaskbarView(theme=theme)
    assert [label for label, _key in taskbar.tabs] == [
        "Home", "Settings", "About"]
    assert ServerFilterBar(theme=theme).labels == ["All", "Subs", "Local"]
    assert taskbar.capsule.search_input.placeholderText() == "Search servers"
    assert taskbar.fab.toolTip() == "Add server / subscription"
    header = HeaderBar(theme)
    assert header.close_btn.toolTip() == "Close"
    tray = TrayManager(theme, "icon.svg")
    assert tray.show_action.text() == "Show/Hide"
    assert tray.servers_menu.title() == "Servers"
    assert tray.disconnect_action.text() == "Disconnect"
    assert tray.quit_action.text() == "Quit"


def test_dialogs_render_english(qapp):
    from utils.theme import M3Theme
    from ui.add_server_dialog import AddServerDialog
    from ui.edit_subscription_dialog import EditSubscriptionDialog
    from ui.connection_log_dialog import ConnectionLogDialog

    set_language("en")
    theme = M3Theme(preset_key="lavender")
    add = AddServerDialog(theme=theme)
    assert add.cancel_button.text() == "Cancel"
    assert add.add_button.text() == "Add"
    assert add.input_field.placeholderText().startswith("ss://")

    sub = {"name": "Sub", "url": "https://example.com/sub", "icon_emoji": "🌐"}
    edit = EditSubscriptionDialog(sub, None, theme)
    assert edit.btn_pin.text() == "📌 Pin to top"
    assert edit.edit_name.placeholderText() == "Subscription name"

    log_dlg = ConnectionLogDialog(theme=theme)
    assert log_dlg.title_label.text() == "📋  Connection Log"
    assert log_dlg.clear_button.text() == "Clear"
    assert log_dlg.close_button.text() == "Close"


def test_main_window_group_title_english(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    from utils.server_model import Server, ProxyProtocol
    import utils.twinsock as tw

    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()

    (tmp_path / "settings.json").write_text('{"language": "en"}', encoding="utf-8")
    win = RoundedWindow()
    manual = Server(key="m1", name="M", host="10.0.0.1", port=8388,
                    password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    win.server_manager.manual_servers = [manual]
    win.manual_servers = [manual]
    win._refresh_server_list()
    assert win._build_groups()[0]["title"] == "My servers"
    assert win.screen_main.status_label.text() == "Disconnected"
