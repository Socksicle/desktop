"""Tray servers menu rebuilds only when the server set changes."""
import pytest

from ui.main_window import RoundedWindow
from utils.server_model import Server, ProxyProtocol
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

    win = RoundedWindow()
    manual = Server(key="manual-1", name="Manual One", host="10.0.0.1", port=8388,
                    password="pw1", protocol=ProxyProtocol.SHADOWSOCKS)
    win.server_manager.manual_servers = [manual]
    win.manual_servers = [manual]
    win.subscription_manager.subscriptions = [{
        "name": "TestSub",
        "url": "https://example.com/sub",
        "icon_emoji": "🚀",
        "servers": [
            Server(key="sub-a", name="Sub A", host="10.0.0.2", port=8388,
                   password="pw2", protocol=ProxyProtocol.SHADOWSOCKS),
        ],
    }]
    win._refresh_server_list()
    yield win
    try:
        win.close()
        win.deleteLater()
        qapp.processEvents()
    except Exception:
        pass


def _menu_texts(win):
    texts = []

    def _walk(menu):
        for act in menu.actions():
            texts.append(act.text())
            if act.menu():
                _walk(act.menu())

    _walk(win.tray_manager.servers_menu)
    return texts


def test_collapse_toggle_does_not_rebuild_tray_menu(main_win, monkeypatch):
    win = main_win
    before = _menu_texts(win)
    assert any("Sub A" in t for t in before)

    rebuilt = []
    orig = win.tray_manager.rebuild_menu
    monkeypatch.setattr(win.tray_manager, "rebuild_menu",
                        lambda *a: (rebuilt.append(True), orig(*a)))

    win._on_group_collapsed("__manual__")
    win._on_group_collapsed("__manual__")

    assert rebuilt == []
    assert _menu_texts(win) == before


def test_subscription_update_rebuilds_tray_menu(main_win):
    win = main_win
    tm = win.tray_manager

    assert tm.rebuild_menu_if_changed(win.manual_servers,
                                     win.subscription_manager.subscriptions) is False

    win.subscription_manager.subscriptions[0]["servers"].append(
        Server(key="sub-b", name="Sub B", host="10.0.0.3", port=8388,
               password="pw3", protocol=ProxyProtocol.SHADOWSOCKS))
    assert tm.rebuild_menu_if_changed(win.manual_servers,
                                     win.subscription_manager.subscriptions) is True
    assert any("Sub B" in t for t in _menu_texts(win))

    renamed = win.subscription_manager.subscriptions[0]["servers"][0]
    renamed.name = "Sub A renamed"
    assert tm.rebuild_menu_if_changed(win.manual_servers,
                                     win.subscription_manager.subscriptions) is True
    assert any("Sub A renamed" in t for t in _menu_texts(win))


def test_tray_disconnect_action_requests_disconnect(main_win, monkeypatch):
    """Tray Disconnect must disconnect, never select-and-connect a server."""
    win = main_win
    calls = []
    monkeypatch.setattr(win, "toggle_connection",
                        lambda connect=None: calls.append(connect))

    win.tray_manager.set_disconnect_enabled(True)
    win.tray_manager.disconnect_action.trigger()

    assert calls == [False]


def test_tray_restore_re_adds_icon(main_win, monkeypatch):
    calls = []
    monkeypatch.setattr(main_win.tray_manager.tray_icon, "hide",
                        lambda: calls.append("hide"))
    monkeypatch.setattr(main_win.tray_manager.tray_icon, "show",
                        lambda: calls.append("show"))

    main_win.tray_manager.restore()

    assert calls == ["hide", "show"]


def test_tray_connect_to_unknown_server_is_noop(main_win, monkeypatch):
    """A stale tray server (gone from the list) must not touch selection or connect."""
    win = main_win
    scheduled = []
    monkeypatch.setattr("ui.main_window.QTimer.singleShot",
                        staticmethod(lambda ms, fn: scheduled.append(ms)))
    before = win.server_panel.get_selected_index()
    ghost = Server(key="ghost", name="Ghost", host="192.0.2.1", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)

    win._on_connect_from_tray(ghost)

    assert scheduled == []
    assert win.server_panel.get_selected_index() == before


def test_tray_status_action_top_disabled(main_win):
    tm = main_win.tray_manager
    assert tm.status_action.isEnabled() is False

    # Disconnected
    tm.update_status(False)
    assert not tm.status_action.isEnabled()
    assert tm.status_action.text() != ""

    # Connected to a server
    tm.update_status(True, "Frankfurt-01")
    assert not tm.status_action.isEnabled()
    assert "Frankfurt-01" in tm.status_action.text()
    assert not tm.status_action.icon().isNull()

    # Connecting
    tm.update_status(False, "Frankfurt-01", is_connecting=True)
    assert not tm.status_action.isEnabled()
    assert "Frankfurt-01" in tm.status_action.text()


def test_tray_mode_switch(main_win, monkeypatch):
    win = main_win
    monkeypatch.setattr(win, "_apply_tun_mode", lambda on: None)
    tm = win.tray_manager

    # Initially tun_mode is False
    assert tm.proxy_action.isChecked() is True
    assert tm.tun_action.isChecked() is False
    assert "TUN" not in tm.mode_menu.title() or "Proxy" in tm.mode_menu.title() or "прокси" in tm.mode_menu.title().lower()

    # Switch to TUN via action
    tm.tun_action.trigger()
    assert tm.tun_action.isChecked() is True
    assert tm.proxy_action.isChecked() is False
    assert "TUN" in tm.mode_menu.title()
    assert win.settings.get("tun_mode") is True

    # Switch back to Proxy
    tm.proxy_action.trigger()
    assert tm.proxy_action.isChecked() is True
    assert tm.tun_action.isChecked() is False
    assert win.settings.get("tun_mode") is False


def test_tray_server_checkboxes(main_win, monkeypatch):
    win = main_win
    monkeypatch.setattr(win, "_on_connect_from_tray", lambda srv: None)
    monkeypatch.setattr(win, "toggle_connection", lambda connect=None: None)
    monkeypatch.setattr("ui.main_window.QTimer.singleShot", staticmethod(lambda ms, fn: None))
    tm = win.tray_manager

    # All server actions should be checkable
    server_actions = [act for act, _ in tm._server_actions]
    assert len(server_actions) >= 2
    for act in server_actions:
        assert act.isCheckable() is True

    # Select manual-1
    manual_srv = win.manual_servers[0]
    sub_srv = win.subscription_manager.subscriptions[0]["servers"][0]

    tm.set_selected_server(manual_srv)
    act_manual = next(a for a, s in tm._server_actions if s.name == manual_srv.name)
    act_sub = next(a for a, s in tm._server_actions if s.name == sub_srv.name)

    assert act_manual.isChecked() is True
    assert act_sub.isChecked() is False

    # Triggering sub_srv checks it and emits connectRequested
    requested = []
    tm.connectRequested.connect(requested.append)
    act_sub.trigger()

    assert requested == [sub_srv]
    assert act_sub.isChecked() is True
    assert act_manual.isChecked() is False


def test_tray_menu_icons_and_styling(main_win):
    tm = main_win.tray_manager
    assert not tm.show_action.icon().isNull()
    assert not tm.mode_menu.icon().isNull()
    assert not tm.servers_menu.icon().isNull()
    assert not tm.quit_action.icon().isNull()
    assert "min-width: 170px" in tm._stylesheet

