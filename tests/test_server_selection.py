import time
from types import SimpleNamespace
import pytest

from utils.connection_manager import CONNECTED
from utils.server_model import Server, server_identity, ProxyProtocol
from utils.subscription_manager import SubscriptionManager


def _link(host, port, tag, password="secretpassword"):
    import base64
    userinfo = base64.urlsafe_b64encode(f"aes-256-gcm:{password}".encode()).decode().rstrip("=")
    return f"ss://{userinfo}@{host}:{port}#{tag}"


def _flat_names(win):
    return [item.server.name for item in win.server_panel._server_items]


# ─── Group collapse & selection preservation ─────────────────────────────────

def test_selection_survives_group_collapse_and_connect(main_win, monkeypatch):
    win = main_win
    assert _flat_names(win) == ["Manual One", "Sub A", "Sub B"]

    # Select "Sub B" (flat index 2)
    win.server_panel._server_items[2].radio.setChecked(True)
    assert win.server_panel.get_selected_index() == 2
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()

    # Collapse other group: selection stays on Sub B
    win._on_group_collapsed("__manual__")
    assert _flat_names(win) == ["Sub A", "Sub B"]
    assert win._selected_server_key == "sub-b"
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == 2

    # Collapse own group: card keeps showing it, radio is -1
    win._on_group_collapsed("TestSub")
    assert win._selected_server_key == "sub-b"
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == -1

    # Connect while collapsed: uses hidden selection
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    toggled = []
    monkeypatch.setattr(win.connection_manager, "toggle", lambda srv, connect: toggled.append((srv, connect)))
    win.toggle_connection(True)
    time.sleep(0.5)
    assert len(toggled) == 1
    srv, connect = toggled[0]
    assert connect is True
    assert getattr(srv, "key", "") == "sub-b"

    # Expanding restores checked radio
    win._on_group_collapsed("TestSub")
    assert win.server_panel.get_selected_index() == 2
    assert "Sub B" in win.screen_main.card_active.server_name_label.text()


def test_selection_subscription_update_and_fallback(main_win):
    win = main_win
    win.server_panel._server_items[1].radio.setChecked(True)  # Sub A
    assert win._selected_server_key == "sub-a"

    # Feed update: new objects, changed order, one node gone
    win.subscription_manager.subscriptions[0]["servers"] = [
        Server(key="sub-b", name="Sub B", host="10.0.0.3", port=8388,
               password="pw3", protocol=ProxyProtocol.SHADOWSOCKS),
        Server(key="sub-a", name="Sub A renamed", host="10.0.0.2", port=8388,
               password="pw3", protocol=ProxyProtocol.SHADOWSOCKS),
    ]
    win._refresh_server_list()
    assert win._selected_server_key == "sub-a"
    assert "Sub A renamed" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == 2

    # Server disappears: falls back to default first server
    win.subscription_manager.subscriptions[0]["servers"] = [
        Server(key="sub-b", name="Sub B", host="10.0.0.3", port=8388,
               password="pw3", protocol=ProxyProtocol.SHADOWSOCKS),
    ]
    win._refresh_server_list()
    assert "Manual One" in win.screen_main.card_active.server_name_label.text()
    assert win.server_panel.get_selected_index() == 0


# ─── Remote title / customization ────────────────────────────────────────────

def test_subscription_add_remote_title_and_deduplication(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: ([_link("9.9.9.9", 8388, "Node1")], {"profile_title": "MabVpn"}),
    )
    mgr = SubscriptionManager()

    # Add using remote title
    assert mgr.add("rawsq.space", "https://rawsq.space:8000/sub", use_remote_title=True) is True
    sub = mgr.get("MabVpn")
    assert sub is not None
    assert len(sub["servers"]) == 1

    # Add without remote title keeps given name
    assert mgr.add("Google Sub", "https://example.com/sub") is True
    assert mgr.get("Google Sub") is not None

    # Re-adding same URL keeps custom name and doesn't duplicate
    assert mgr.rename("MabVpn", "My Fav", "🔥", "") is True
    assert mgr.add("rawsq.space", "https://rawsq.space:8000/sub", use_remote_title=True) is True
    assert len(mgr.subscriptions) == 2
    kept = mgr.get("My Fav")
    assert kept is not None
    assert kept.get("icon_emoji") == "🔥"


def test_subscription_update_remote_title_and_custom_overrides(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    mgr = SubscriptionManager()

    # Case 1: uncustomized sub updates to provider title
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

    # Case 2: customized sub keeps custom name and icon
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

    # Case 3: legacy customized group keeps name
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

def test_server_row_deletion_semantics(main_win, monkeypatch):
    from utils.server_model import Server as _Server
    win = main_win
    m2 = _Server(key="manual-2", name="Manual Two", host="10.0.0.9", port=8388,
                 password="pw9", protocol=ProxyProtocol.SHADOWSOCKS)
    win.server_manager.manual_servers.append(m2)
    win._refresh_server_list()
    assert [s.key for s in win.server_manager.manual_servers] == ["manual-1", "manual-2"]

    # Deleting m2 removes only m2
    win.server_panel.serverDeleted.emit(m2)
    assert [s.key for s in win.server_manager.manual_servers] == ["manual-1"]

    # Deleting sub row while filtered to subscriptions does not touch manual servers
    win._on_filter_changed(1)
    sub_a = win.subscription_manager.subscriptions[0]["servers"][0]
    infos = []
    monkeypatch.setattr("ui.main_window.MessageBox.information", lambda *a, **k: infos.append(a))
    win.server_panel._emit_deleted(sub_a)
    assert [s.key for s in win.server_manager.manual_servers] == ["manual-1"]
    assert len(infos) == 1

    # Deleting remaining manual row via signal clears manual servers
    win._on_filter_changed(0)
    manual = win.server_manager.manual_servers[0]
    infos.clear()
    win.server_panel.serverDeleted.emit(manual)
    assert win.server_manager.manual_servers == []
    assert infos == []


# ─── server_identity ──────────────────────────────────────────────────────────

def test_server_identity_rules():
    keyed = Server(key="sub-a", name="Sub A", host="10.0.0.2", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    assert server_identity(keyed) == "sub-a"
    assert server_identity(None) == "addr:none"
    assert server_identity(SimpleNamespace(host="h", port=1)) == "hp:h:1"

    # Matches recreated servers by content
    first = Server(key="", name="A", host="10.0.0.2", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    second = Server(key="", name="A renamed", host="10.0.0.2", port=8388,
                    password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    moved = Server(key="", name="A", host="10.0.0.9", port=8388,
                   password="pw", protocol=ProxyProtocol.SHADOWSOCKS)
    assert server_identity(first) == server_identity(second)
    assert server_identity(first) != server_identity(moved)


def test_hotswap_xhttp_shows_info_dialog(main_win, monkeypatch):
    win = main_win
    # Create servers in the list
    curr_server = Server(key="s1", name="Normal Server", host="1.1.1.1", port=443)
    xhttp_server = Server(
        key="s_xh", name="XHTTP Server", host="2.2.2.2", port=443,
        protocol=ProxyProtocol.VLESS, transport="xhttp",
    )
    win.server_manager.manual_servers.extend([curr_server, xhttp_server])
    win._refresh_server_list()

    win.connection_manager.engine.current_server = curr_server
    win.connection_manager.state = CONNECTED

    infos = []
    toggled = []
    monkeypatch.setattr("ui.main_window.MessageBox.information", lambda *a, **k: infos.append(a))
    monkeypatch.setattr(win, "toggle_connection", lambda *a, **k: toggled.append(a))

    # Find index of xhttp server
    servers = win._current_servers()
    idx = next(i for i, s in enumerate(servers) if s.key == "s_xh")

    win._on_server_selected(idx)

    # Info dialog must be shown, and toggle_connection must NOT be called
    assert len(infos) == 1
    assert len(toggled) == 0

    # UI selection must NOT fall on xhttp_server; it must remain on curr_server
    assert win._selected_server_key == server_identity(curr_server)
    assert "Normal Server" in win.screen_main.card_active.server_name_label.text()
    curr_idx = next(i for i, s in enumerate(servers) if s.key == "s1")
    assert win.server_panel.get_selected_index() == curr_idx
    assert "Xray" in infos[0][2]

