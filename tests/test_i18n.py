"""Tests for multilingual UI support (utils.i18n + catalogs)."""
import pathlib
import re
import string

import pytest

from utils import i18n
from utils import i18n_en, i18n_he, i18n_ru, i18n_zh
from utils.i18n import get_language, set_language, tr, trn


@pytest.fixture(autouse=True)
def _restore_language():
    prev = get_language()
    yield
    set_language(prev)


def test_i18n_core_and_fallbacks(monkeypatch):
    # 1. Default language & normalization
    assert i18n.DEFAULT_LANGUAGE == "ru"
    assert set_language(None) == "ru"
    assert set_language("xx") == "ru"
    assert set_language(" EN ") == "en"
    assert get_language() == "en"

    # 2. Fallback chain across languages
    set_language("ru")
    assert tr("nav.settings") == "Настройки"
    assert tr("missing.key") == "missing.key"
    set_language("en")
    assert tr("nav.settings") == "Settings"
    set_language("zh")
    assert tr("nav.settings") == "设置"
    assert tr("missing.key") == "missing.key"
    set_language("he")
    assert tr("nav.settings") == "Настройки"  # Deactivated language falls back to default ru
    assert tr("missing.key") == "missing.key"

    # 3. Plural forms (trn)
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
    set_language("zh")
    assert trn("tray.nodes_added", 1).format(n=1) == "已添加 1 个新节点。"
    assert trn("tray.nodes_added", 5).format(n=5) == "已添加 5 个新节点。"

    # 4. Chinese fallback to English then Russian
    set_language("zh")
    monkeypatch.delitem(i18n_zh.STRINGS, "nav.settings")
    assert tr("nav.settings") == "Settings"
    monkeypatch.delitem(i18n_en.STRINGS, "nav.settings")
    assert tr("nav.settings") == "Настройки"


def _used_tr_keys():
    keys = set()
    base = pathlib.Path(__file__).resolve().parent.parent
    for sub in ("utils", "ui"):
        root = base / sub
        for path in root.rglob("*.py"):
            if path.name.startswith("i18n_"):
                continue
            text = path.read_text(encoding="utf-8")
            keys.update(re.findall(r"\btr\(\s*[\"']([^\"']+)[\"']", text))
            keys.update(re.findall(r"\btrn\(\s*[\"']([^\"']+)[\"']", text))
    return keys


def _placeholders(text: str) -> list[str]:
    return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)


def test_i18n_catalogs_integrity():
    used = _used_tr_keys()
    assert used, "no tr() calls found"
    assert not [k for k in used if k not in i18n_ru.STRINGS]
    assert not [k for k in used if k not in i18n_en.STRINGS]
    assert not [k for k in used if k not in i18n_zh.STRINGS]
    assert not [k for k in used if k not in i18n_he.STRINGS]

    # zh matches en and ru keys and placeholders
    assert set(i18n_zh.STRINGS) == set(i18n_en.STRINGS)
    assert set(i18n_zh.STRINGS) == set(i18n_ru.STRINGS)
    for key, value in i18n_en.STRINGS.items():
        other = i18n_zh.STRINGS[key]
        if isinstance(value, dict):
            assert set(other) == set(value), key
            for form, text in value.items():
                assert _placeholders(text) == _placeholders(other[form]), key
        else:
            assert isinstance(other, str), key
            assert _placeholders(value) == _placeholders(other), key

    # he matches en and ru keys and placeholders
    assert set(i18n_he.STRINGS) == set(i18n_en.STRINGS)
    assert set(i18n_he.STRINGS) == set(i18n_ru.STRINGS)
    for key, value in i18n_en.STRINGS.items():
        other = i18n_he.STRINGS[key]
        if isinstance(value, dict):
            assert set(other) == set(value), key
            for form, text in value.items():
                assert _placeholders(text) == _placeholders(other[form]), key
        else:
            assert isinstance(other, str), key
            assert _placeholders(value) == _placeholders(other), key


def test_widgets_and_dialogs_render_english(qapp):
    from utils.theme import M3Theme
    from ui.screen_settings import SettingsScreen
    from ui.screen_about import ScreenAbout
    from ui.card_active_server import CardActiveServer
    from ui.bento_telemetry_card import BentoTelemetryCard
    from ui.tray_manager import TrayManager
    from ui.taskbar_view import TaskbarView
    from ui.server_filter_bar import ServerFilterBar
    from ui.header_bar import HeaderBar
    from ui.add_server_dialog import AddServerDialog
    from ui.edit_subscription_dialog import EditSubscriptionDialog
    from ui.connection_log_dialog import ConnectionLogDialog
    from PySide6.QtWidgets import QLabel

    set_language("en")
    theme = M3Theme(preset_key="lavender")

    # 1. Settings screen
    screen = SettingsScreen({"theme_preset": "lavender"}, theme)
    screen.show()
    assert screen.title_label.text() == "Settings"
    screen.open_section("ping", "Ping")
    titles = [c.title_text for c in screen.group_ping_methods._cards]
    assert "THP (recommended)" in titles
    screen.open_section("appearance", "Appearance")
    langs = [screen.lang_combo.itemText(i) for i in range(screen.lang_combo.count())]
    assert langs == ["Русский", "English", "中文"]
    assert screen.lbl_lang_hint.text() == "Applies after app restart"

    # 2. About and telemetry cards
    about = ScreenAbout(theme, settings={})
    row_texts = [w.text() for w in about.findChildren(QLabel)]
    assert "Device name" in row_texts and "Core" in row_texts
    assert about.btn_copy_hwid.text() == "Copy"

    card = CardActiveServer(theme=theme)
    card.set_server(None)
    assert card.server_name_label.text() == "No server selected"
    assert card.label_target.text() == "SELECTED SERVER"

    tele = BentoTelemetryCard(theme=theme)
    assert tele.label_ip.text() == "IP ADDRESS"

    # 3. Tray, taskbar, header bar, filter bar
    taskbar = TaskbarView(theme=theme)
    assert [label for label, _key in taskbar.tabs] == ["Home", "Settings", "About"]
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

    # 4. Dialogs
    add = AddServerDialog(theme=theme)
    assert add.cancel_button.text() == "Cancel"
    assert add.add_button.text() == "Add"
    assert add.input_field.placeholderText().startswith("ss://")

    sub = {"name": "Sub", "url": "https://example.com/sub", "icon_emoji": "🌐"}
    edit = EditSubscriptionDialog(sub, None, theme)
    assert edit.sw_insecure is not None
    assert edit.edit_name.placeholderText() == "Subscription name"

    log_dlg = ConnectionLogDialog(theme=theme)
    assert log_dlg.title_label.text() == "📋  Connection Log"
    assert log_dlg.clear_button.text() == "Clear"
    assert log_dlg.close_button.text() == "Close"


def test_main_window_renders_english(qapp, monkeypatch, tmp_path):
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
