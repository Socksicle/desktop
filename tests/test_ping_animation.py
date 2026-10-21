"""Ping busy animation, async batch ping and subscription busy guards."""
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
    return Server(name=name, host=host, port=port)


# ─── ServerItem animation ─────────────────────────────────────────────

def test_numbers_mode_runs_dots_until_result():
    theme = M3Theme()
    item = ServerItem("S", _srv(), theme, ping_display_mode=0)
    item.show()

    item.start_ping_animation()
    assert item.is_ping_animating()
    assert item.ping_label.isHidden() is False
    assert item.ping_bars.isHidden() is True
    assert item.ping_label.text() == "."

    item._on_ping_anim_tick()
    assert item.ping_label.text() == ".."
    item._on_ping_anim_tick()
    assert item.ping_label.text() == "..."
    item._on_ping_anim_tick()
    assert item.ping_label.text() == "."

    item.set_ping(42.0)
    assert not item.is_ping_animating()
    assert "42ms" in item.ping_label.text()


def test_bars_mode_cycles_fill_levels():
    theme = M3Theme()
    item = ServerItem("S", _srv(), theme, ping_display_mode=1)
    item.show()

    item.start_ping_animation()
    assert item.is_ping_animating()
    assert item.ping_label.isHidden() is True
    assert item.ping_bars.isHidden() is False
    assert item.ping_bars._level == 1

    levels = []
    for _ in range(5):
        item._on_ping_anim_tick()
        levels.append(item.ping_bars._level)
    assert levels == [2, 3, 4, 1, 2]

    item.set_ping(120.0)
    assert not item.is_ping_animating()
    assert item.ping_bars.isHidden() is False


def test_both_mode_animates_label_and_bars():
    theme = M3Theme()
    item = ServerItem("S", _srv(), theme, ping_display_mode=2)
    item.show()

    item.start_ping_animation()
    assert item.ping_label.isHidden() is False
    assert item.ping_bars.isHidden() is False
    item._on_ping_anim_tick()
    assert item.ping_label.text() == ".."
    assert item.ping_bars._level == 2


def test_rebind_stops_stale_animation():
    theme = M3Theme()
    item = ServerItem("S", _srv(), theme, ping_display_mode=0)
    item.show()
    item.start_ping_animation()
    assert item.is_ping_animating()
    item.update_server("S2", _srv(name="S2"))
    assert not item.is_ping_animating()
    assert item.ping_label.text() == ""


# ─── ServerListPanel async ping ───────────────────────────────────────

class _FakeBatchJob:
    """Synchronous stand-in for AsyncBatchPingJob (no network)."""

    def __init__(self, servers, callback=None, **kwargs):
        self.servers = list(servers)
        self.callback = callback

    def run(self):
        for i in range(len(self.servers)):
            self.callback(i, 50.0 + i)


def test_panel_ping_all_animates_and_reports_finished(qapp, monkeypatch):
    monkeypatch.setattr(panel_mod, "AsyncBatchPingJob", _FakeBatchJob)
    monkeypatch.setattr(
        QThreadPool.globalInstance(), "start", lambda job: job.run())

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


def test_panel_ping_all_returns_while_job_runs(qapp, monkeypatch):
    """ping_all must not block the UI thread waiting for probes."""
    started = threading.Event()
    release = threading.Event()

    class _BlockingJob(QRunnable):
        def __init__(self, servers, callback=None, **kwargs):
            super().__init__()
            self._callback = callback
            self._n = len(servers)

        def run(self):
            started.set()
            assert release.wait(10)

    monkeypatch.setattr(panel_mod, "AsyncBatchPingJob", _BlockingJob)

    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [_srv("S1")]
    panel.refresh(servers)
    panel.show()

    panel.ping_all("thp", None, servers=servers, group_key="g1")
    # Returned while the worker is still blocked: animation already visible.
    assert panel._server_items[0].is_ping_animating()
    assert started.wait(10)
    release.set()
    assert QThreadPool.globalInstance().waitForDone(10000)


def test_panel_keeps_result_for_hidden_row(qapp):
    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [_srv("S1"), _srv("S2")]
    panel.refresh(servers)

    panel._apply_ping_result(id(servers[1]), 77.0)
    assert getattr(servers[1], "_last_ping", None) == 77.0


# ─── MainWindow re-entry guards ─────────────────────────────────────────

def _make_window(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    return RoundedWindow()


def test_main_window_ignores_double_ping(qapp, monkeypatch, tmp_path):
    win = _make_window(qapp, monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        win.server_panel, "ping_all", lambda *a, **k: calls.append((a, k)))
    win._group_servers = lambda key: [_srv("S1")]

    win._on_ping_group("g1")
    win._on_ping_group("g1")
    assert len(calls) == 1
    assert calls[0][1].get("group_key") == "g1"

    win._on_group_ping_finished("g1")
    win._on_ping_group("g1")
    assert len(calls) == 2


def test_main_window_ignores_double_refresh(qapp, monkeypatch, tmp_path):
    win = _make_window(qapp, monkeypatch, tmp_path)
    win.subscription_manager.subscriptions.append(
        {"name": "Sub1", "url": "https://example.com/sub", "servers": []})
    updates = []
    monkeypatch.setattr(
        win.subscription_manager, "update", lambda sub: updates.append(sub))

    win._refresh_subscription("Sub1")
    win._refresh_subscription("Sub1")
    assert len(updates) == 1
    assert win._refreshing_key == "Sub1"

    win._on_sub_updated(True, 0)
    assert win._refreshing_key is None
    win._refresh_subscription("Sub1")
    assert len(updates) == 2


# ─── GroupHeaderCard busy states ──────────────────────────────────────

def test_group_card_busy_states(qapp):
    theme = M3Theme()
    card = GroupHeaderCard(key="k", title="T", icon="🌐",
                           subtitle="sub", theme=theme)
    card.show()

    card.set_pinging(True)
    assert card.btn_ping.isEnabled() is False
    card.set_pinging(False)
    assert card.btn_ping.isEnabled() is True

    card.set_refreshing(True)
    assert card.btn_refresh.isEnabled() is False
    assert card.sub_label.text() != "sub"
    assert card._spin_timer.isActive() is True

    card._spin_tick()
    card._spin_tick()
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() != card._refresh_pixmap.toImage()

    card.set_refreshing(False)
    assert card.btn_refresh.isEnabled() is True
    assert card.sub_label.text() == "sub"
    assert card._spin_timer.isActive() is False
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() == card._refresh_pixmap.toImage()


def test_group_card_spin_stops_when_hidden(qapp):
    theme = M3Theme()
    parent = QWidget()
    card = GroupHeaderCard(key="k", title="T", icon="🌐",
                           subtitle="sub", theme=theme, parent=parent)
    parent.show()
    card.set_refreshing(True)
    assert card._spin_timer.isActive() is True

    card._spin_tick()
    visible_icon = card.btn_refresh.icon().pixmap(36, 36).toImage()

    parent.hide()
    assert card._spin_timer.isActive() is False
    card._spin_tick()
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() == visible_icon

    parent.show()
    assert card._spin_timer.isActive() is True
    card._spin_tick()
    assert card.btn_refresh.icon().pixmap(36, 36).toImage() != visible_icon


def test_group_card_refresh_while_hidden_waits_for_show(qapp):
    theme = M3Theme()
    card = GroupHeaderCard(key="k", title="T", icon="🌐",
                           subtitle="sub", theme=theme)
    card.set_refreshing(True)
    assert card._spin_timer.isActive() is False

    card.show()
    assert card._spin_timer.isActive() is True
