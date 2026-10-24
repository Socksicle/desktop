import threading

import pytest
from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtWidgets import QWidget

import ui.server_list_panel as panel_mod
from ui.server_list_panel import ServerListPanel
from ui.server_item import ServerItem
from ui.group_header_card import GroupHeaderCard
from utils.theme import M3Theme
from utils.server_model import Server


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def _srv(name="S", host="1.1.1.1", port=443):
    return Server(name=name, host=host, port=port, protocol="shadowsocks")


# ─── ServerItem animation ─────────────────────────────────────────────

def test_server_item_ping_animation_modes():
    theme = M3Theme()

    # Numbers mode (mode 0)
    item0 = ServerItem("S", _srv(), theme, ping_display_mode=0)
    item0.show()
    item0.start_ping_animation()
    assert item0.is_ping_animating()
    assert item0.ping_label.isHidden() is False
    assert item0.ping_bars.isHidden() is True
    assert item0.ping_label.text() == "."

    item0._on_ping_anim_tick()
    assert item0.ping_label.text() == ".."
    item0._on_ping_anim_tick()
    assert item0.ping_label.text() == "..."
    item0._on_ping_anim_tick()
    assert item0.ping_label.text() == "."

    item0.set_ping(42.0)
    assert not item0.is_ping_animating()
    assert "42ms" in item0.ping_label.text()

    # Rebind stops stale animation
    item0.start_ping_animation()
    assert item0.is_ping_animating()
    item0.update_server("S2", _srv(name="S2"))
    assert not item0.is_ping_animating()
    assert item0.ping_label.text() == ""

    # Bars mode (mode 1)
    item1 = ServerItem("S", _srv(), theme, ping_display_mode=1)
    item1.show()
    item1.start_ping_animation()
    assert item1.is_ping_animating()
    assert item1.ping_label.isHidden() is True
    assert item1.ping_bars.isHidden() is False
    assert item1.ping_bars._level == 1

    levels = []
    for _ in range(5):
        item1._on_ping_anim_tick()
        levels.append(item1.ping_bars._level)
    assert levels == [2, 3, 4, 1, 2]

    item1.set_ping(120.0)
    assert not item1.is_ping_animating()
    assert item1.ping_bars.isHidden() is False

    # Both mode (mode 2)
    item2 = ServerItem("S", _srv(), theme, ping_display_mode=2)
    item2.show()
    item2.start_ping_animation()
    assert item2.ping_label.isHidden() is False
    assert item2.ping_bars.isHidden() is False
    item2._on_ping_anim_tick()
    assert item2.ping_label.text() == ".."
    assert item2.ping_bars._level == 2


# ─── ServerListPanel async ping ───────────────────────────────────────

class _FakeBatchJob:
    def __init__(self, servers, callback=None, **kwargs):
        self.servers = list(servers)
        self.callback = callback

    def run(self):
        for i in range(len(self.servers)):
            self.callback(i, 50.0 + i)


def test_panel_ping_all_flow_and_hidden_row(qapp, monkeypatch):
    monkeypatch.setattr(panel_mod, "AsyncBatchPingJob", _FakeBatchJob)
    monkeypatch.setattr(QThreadPool.globalInstance(), "start", lambda job: job.run())

    theme = M3Theme()
    panel = ServerListPanel(theme, ping_display_mode=0)
    servers = [_srv("S1"), _srv("S2")]
    panel.refresh(servers)
    panel.show()

    finished = []
    panel.pingFinished.connect(finished.append)
    panel.ping_all("thp", None, servers=servers, group_key="g1")

    assert finished == ["g1"]
    assert "50ms" in panel._server_items[0].ping_label.text()
    assert "51ms" in panel._server_items[1].ping_label.text()
    assert not panel._server_items[0].is_ping_animating()
    assert not panel._ping_pending

    # Non-blocking async check
    monkeypatch.undo()
    started = threading.Event()
    release = threading.Event()

    class _BlockingJob(QRunnable):
        def __init__(self, servers, callback=None, **kwargs):
            super().__init__()
            self._callback = callback

        def run(self):
            started.set()
            assert release.wait(10)

    monkeypatch.setattr(panel_mod, "AsyncBatchPingJob", _BlockingJob)
    panel.ping_all("thp", None, servers=[servers[0]], group_key="g1")
    assert panel._server_items[0].is_ping_animating()
    assert started.wait(10)
    release.set()
    assert QThreadPool.globalInstance().waitForDone(10000)

    # Hidden row ping retention
    panel._apply_ping_result(id(servers[1]), 77.0)
    assert getattr(servers[1], "_last_ping", None) == 77.0


# ─── MainWindow re-entry guards ─────────────────────────────────────────

def _make_window(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    return RoundedWindow()


def test_main_window_reentry_guards(qapp, monkeypatch, tmp_path):
    win = _make_window(qapp, monkeypatch, tmp_path)

    # Ping re-entry guard
    calls = []
    monkeypatch.setattr(win.server_panel, "ping_all", lambda *a, **k: calls.append((a, k)))
    win._group_servers = lambda key: [_srv("S1")]

    win._on_ping_group("g1")
    win._on_ping_group("g1")
    assert len(calls) == 1
    assert calls[0][1].get("group_key") == "g1"

    win._on_group_ping_finished("g1")
    win._on_ping_group("g1")
    assert len(calls) == 2

    # Refresh re-entry guard
    win.subscription_manager.subscriptions.append(
        {"name": "Sub1", "url": "https://example.com/sub", "servers": []})
    updates = []
    monkeypatch.setattr(win.subscription_manager, "update", lambda sub: updates.append(sub))

    win._refresh_subscription("Sub1")
    win._refresh_subscription("Sub1")
    assert len(updates) == 1
    assert win._refreshing_key == "Sub1"

    win._on_sub_updated(True, 0)
    assert win._refreshing_key is None
    win._refresh_subscription("Sub1")
    assert len(updates) == 2


# ─── GroupHeaderCard busy states ──────────────────────────────────────

def test_group_card_busy_and_visibility_lifecycle(qapp):
    theme = M3Theme()
    parent = QWidget()
    card = GroupHeaderCard(key="k", title="T", icon="🌐", subtitle="sub", theme=theme, parent=parent)
    parent.show()

    # Ping busy state
    card.set_pinging(True)
    assert card.btn_ping.isEnabled() is False
    card.set_pinging(False)
    assert card.btn_ping.isEnabled() is True

    # Refresh busy state
    card.set_refreshing(True)
    assert card.btn_refresh.isEnabled() is False
    assert card.sub_label.text() != "sub"
    assert card._spin_timer.isActive() is True

    card._spin_tick()
    card._spin_tick()
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() != card._refresh_pixmap.toImage()

    # Spin stops when hidden
    visible_icon = card.btn_refresh.icon().pixmap(36, 36).toImage()
    parent.hide()
    assert card._spin_timer.isActive() is False
    card._spin_tick()
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() == visible_icon

    parent.show()
    assert card._spin_timer.isActive() is True
    card._spin_tick()
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() != visible_icon

    card.set_refreshing(False)
    assert card.btn_refresh.isEnabled() is True
    assert card.sub_label.text() == "sub"
    assert card._spin_timer.isActive() is False
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() == card._refresh_pixmap.toImage()

    # Refresh while hidden waits for show
    card2 = GroupHeaderCard(key="k2", title="T2", icon="🌐", subtitle="sub", theme=theme)
    card2.set_refreshing(True)
    assert card2._spin_timer.isActive() is False
    card2.show()
    assert card2._spin_timer.isActive() is True


def test_ping_result_matches_by_identity_across_rebind(qapp):
    theme = M3Theme()
    panel = ServerListPanel(theme, ping_display_mode=0)
    s1 = _srv("S1")
    s2 = _srv("S2")
    panel.refresh([s1, s2])
    panel.show()

    # Start ping for s1
    panel.ping_all("thp", None, servers=[s1], group_key="g1")

    # Simulate subscription refresh creating new Server instances for same endpoints
    s1_new = _srv("S1")
    s2_new = _srv("S2")
    assert id(s1_new) != id(s1)
    panel.refresh([s1_new, s2_new])

    # Result arrives referencing the old object's ID
    panel._apply_ping_result(id(s1), 42.0)
    assert "42ms" in panel._server_items[0].ping_label.text()
    assert getattr(s1_new, "_last_ping", None) == 42.0
