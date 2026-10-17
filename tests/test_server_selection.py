"""Selection persistence (key-based) and subscription remote-title handling."""
import pytest

from ui.main_window import RoundedWindow
from utils.server_model import Server, ProxyProtocol
from utils.subscription_manager import SubscriptionManager
import utils.twinsock as tw


def _link(host, port, tag, password="secretpassword"):
    import base64
    userinfo = base64.urlsafe_b64encode(f"aes-256-gcm:{password}".encode()).decode().rstrip("=")
    return f"ss://{userinfo}@{host}:{port}#{tag}"




def _flat_names(win):
    return [s.name for s in win._current_servers()]


def test_selection_survives_collapse_of_other_group(main_win):
    win = main_win
    assert _flat_names(win) == ["Manual One", "Sub A", "Sub B"]

    # Select "Sub B" (flat index 2).
    win.server_panel._server_items[2].radio.setChecked(True)
    assert win.server_panel.get_selected_index() == 2
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()

    # Collapse the manual group: selection must stay on Sub B.
    win._on_group_collapsed("__manual__")
    assert _flat_names(win) == ["Manual One", "Sub A", "Sub B"]
    assert win._selected_server_key == "sub-b"
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == 2


def test_selection_survives_collapse_of_own_group(main_win):
    win = main_win
    win.server_panel._server_items[2].radio.setChecked(True)

    # Collapse the group containing the selection: the card keeps showing it...
    win._on_group_collapsed("TestSub")
    assert win._selected_server_key == "sub-b"
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()
    # ...even though no radio exists for it while collapsed.
    assert win.server_panel.get_selected_index() == -1

    # Expanding restores the checked radio.
    win._on_group_collapsed("TestSub")
    assert win.server_panel.get_selected_index() == 2
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()


def test_selection_survives_subscription_update_reorder(main_win):
    win = main_win
    win.server_panel._server_items[1].radio.setChecked(True)  # Sub A
    assert win._selected_server_key == "sub-a"

    # Feed update: new objects, changed order, one node gone.
    win.subscription_manager.subscriptions[0]["servers"] = [
        Server(key="sub-b", name="Sub B", host="10.0.0.3", port=8388,
               password="pw3", protocol=ProxyProtocol.SHADOWSOCKS),
        Server(key="sub-a", name="Sub A renamed", host="10.0.0.2", port=8388,
               password="pw3", protocol=ProxyProtocol.SHADOWSOCKS),
    ]
    win._refresh_server_list()

    assert win._selected_server_key == "sub-a"
    assert "Sub A renamed" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == 2  # moved with the node


def test_connect_uses_hidden_selection_in_collapsed_group(main_win, monkeypatch):
    import time
    win = main_win
    win.server_panel._server_items[2].radio.setChecked(True)
    win._on_group_collapsed("TestSub")

    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    toggled = []
    monkeypatch.setattr(win.connection_manager, "toggle",
                        lambda srv, connect: toggled.append((srv, connect)))

    win.toggle_connection(True)
    time.sleep(0.5)
    assert len(toggled) == 1
    srv, connect = toggled[0]
    assert connect is True
    assert getattr(srv, "key", "") == "sub-b"


def test_selection_falls_back_when_server_disappears(main_win):
    win = main_win
    win.server_panel._server_items[2].radio.setChecked(True)  # Sub B
    win.subscription_manager.subscriptions[0]["servers"] = [
        Server(key="sub-a", name="Sub A", host="10.0.0.2", port=8388,
               password="pw2", protocol=ProxyProtocol.SHADOWSOCKS),
    ]
    win._refresh_server_list()
    assert "Manual One" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == 0


# ─── Remote title / customization ────────────────────────────────────────────

def test_add_uses_remote_title(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "MabVpn"}),
    )
    mgr = SubscriptionManager()
    assert mgr.add("rawsq.space", "https://rawsq.space:8000/sub", use_remote_title=True) is True
    sub = mgr.get("MabVpn")
    assert sub is not None
    assert len(sub["servers"]) == 1


def test_add_without_remote_title_flag_keeps_given_name(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "MabVpn"}),
    )
    mgr = SubscriptionManager()
    assert mgr.add("Google Sub", "https://example.com/sub") is True
    assert mgr.get("Google Sub") is not None


def test_readd_same_url_keeps_custom_name_and_no_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "MabVpn"}),
    )
    mgr = SubscriptionManager()
    assert mgr.add("rawsq.space", "https://rawsq.space:8000/sub", use_remote_title=True) is True
    assert mgr.rename("MabVpn", "My Fav", "🔥", "") is True
    assert mgr.add("rawsq.space", "https://rawsq.space:8000/sub", use_remote_title=True) is True
    assert len(mgr.subscriptions) == 1
    kept = mgr.get("My Fav")
    assert kept is not None
    assert kept.get("icon_emoji") == "🔥"


def test_update_applies_remote_title_when_not_customized(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    mgr = SubscriptionManager()
    mgr.subscriptions = [{
        "name": "example.com",
        "url": "https://example.com/sub",
        "servers": [],
        "profile_title": "",
    }]
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "Provider X"}),
    )
    sub = mgr.subscriptions[0]
    mgr._update_worker(sub, emit_signal=False)
    assert sub["name"] == "Provider X"
    assert len(sub["servers"]) == 1


def test_update_keeps_custom_name_and_icon(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    mgr = SubscriptionManager()
    mgr.subscriptions = [{
        "name": "MabVpn",
        "url": "https://example.com/sub",
        "icon_emoji": "🚀",
        "servers": [],
        "profile_title": "MabVpn",
    }]
    assert mgr.rename("MabVpn", "My Fav", "🔥", "") is True
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "Renamed By Provider"}),
    )
    sub = mgr.subscriptions[0]
    mgr._update_worker(sub, emit_signal=False)
    assert sub["name"] == "My Fav"
    assert sub["icon_emoji"] == "🔥"


def test_update_detects_legacy_custom_name(tmp_path, monkeypatch, qapp):
    """Groups renamed before is_customized existed keep their name on update."""
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    mgr = SubscriptionManager()
    mgr.subscriptions = [{
        "name": "My Old Custom",
        "url": "https://example.com/sub",
        "icon_emoji": "🚀",
        "servers": [],
        "profile_title": "MabVpn",
    }]
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "MabVpn v2"}),
    )
    sub = mgr.subscriptions[0]
    mgr._update_worker(sub, emit_signal=False)
    assert sub["name"] == "My Old Custom"
    assert sub.get("is_customized") is True


# ─── row deletion (identity-based, not flat index) ───────────────────────────

def test_delete_manual_row_removes_only_that_server(main_win):
    from utils.server_model import Server as _Server
    win = main_win
    m2 = _Server(key="manual-2", name="Manual Two", host="10.0.0.9", port=8388,
                 password="pw9", protocol=ProxyProtocol.SHADOWSOCKS)
    win.server_manager.manual_servers.append(m2)
    win._refresh_server_list()
    assert [s.key for s in win.server_manager.manual_servers] == ["manual-1", "manual-2"]

    win.server_panel.serverDeleted.emit(m2)

    assert [s.key for s in win.server_manager.manual_servers] == ["manual-1"]


def test_delete_subscription_row_keeps_manual_servers(main_win, monkeypatch):
    """Deleting a sub row while filtered to subscriptions must not eat a manual server."""
    win = main_win
    win._on_filter_changed(1)  # subscriptions only: sub rows sit at flat index 0
    sub_a = win.subscription_manager.subscriptions[0]["servers"][0]
    infos = []
    monkeypatch.setattr("ui.main_window.MessageBox.information",
                        lambda *a, **k: infos.append(a))

    win.server_panel._emit_deleted(sub_a)

    assert [s.key for s in win.server_manager.manual_servers] == ["manual-1"]
    assert len(infos) == 1


def test_delete_manual_row_via_panel_signal(main_win, monkeypatch):
    """Server deletion via panel signal removes the manual server."""
    win = main_win
    manual = win.server_manager.manual_servers[0]
    infos = []
    monkeypatch.setattr("ui.main_window.MessageBox.information",
                        lambda *a, **k: infos.append(a))

    win.server_panel.serverDeleted.emit(manual)

    assert win.server_manager.manual_servers == []
    assert infos == []


# ─── server_identity ──────────────────────────────────────────────────────────

def test_server_identity_prefers_stable_key():
    from types import SimpleNamespace

    from utils.server_model import server_identity
    keyed = Server(key="sub-a", name="Sub A", host="10.0.0.2", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    assert server_identity(keyed) == "sub-a"
    assert server_identity(None) == "addr:none"
    assert server_identity(SimpleNamespace(host="h", port=1)) == "hp:h:1"


def test_server_identity_matches_recreated_servers_by_content():
    from utils.server_model import server_identity
    first = Server(key="", name="A", host="10.0.0.2", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    second = Server(key="", name="A renamed", host="10.0.0.2", port=8388,
                    password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    moved = Server(key="", name="A", host="10.0.0.9", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    assert server_identity(first) == server_identity(second)
    assert server_identity(first) != server_identity(moved)
