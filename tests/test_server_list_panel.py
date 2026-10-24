"""Tests for ServerListPanel and AnimatedRadioButton optimizations."""
import pytest
from PySide6.QtGui import QColor, QFont

from ui.server_list_panel import ServerListPanel
from ui.server_item import ServerItem, AnimatedRadioButton
from utils.theme import M3Theme
from utils.server_model import Server, ProxyProtocol


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_server_item_and_animated_radio(qapp):
    theme = M3Theme()

    # 1. Cached fonts
    radio = AnimatedRadioButton(theme)
    assert hasattr(radio, "_font_normal") and hasattr(radio, "_font_bold")
    assert isinstance(radio._font_normal, QFont) and isinstance(radio._font_bold, QFont)
    assert radio._font_bold.bold() is True and radio._font_normal.bold() is False

    # 2. Ping threshold colors
    item = ServerItem("Test", Server(name="Test", host="1.1.1.1", port=443), theme)

    # Good (< 150ms) -> accent color
    item.set_ping(120.0)
    assert item.ping_label.text() == "120ms"
    assert theme.primary in item.ping_label.styleSheet()
    assert item.ping_bars._active_color.name() == QColor(theme.primary).name()

    # Medium (150-300ms) -> warning color
    item.set_ping(200.0)
    assert item.ping_label.text() == "200ms"
    assert theme.warning in item.ping_label.styleSheet()
    assert item.ping_bars._active_color.name() == QColor(theme.warning).name()

    # Poor (>= 300ms) -> error color
    item.set_ping(350.0)
    assert item.ping_label.text() == "350ms"
    assert theme.error in item.ping_label.styleSheet()
    assert item.ping_bars._active_color.name() == QColor(theme.error).name()

    # Failed (< 0) -> "н/в" and error color
    item.set_ping(-1.0)
    assert item.ping_label.text() == "н/в"
    assert theme.error in item.ping_label.styleSheet()

    # 3. Ping animation stop & row deletion
    panel = ServerListPanel(theme)
    keep = Server(name="Keep", host="1.1.1.1", port=443)
    gone = Server(name="Gone", host="2.2.2.2", port=443)
    panel.refresh([keep, gone])

    stale = panel._server_items[1]
    stale.start_ping_animation()
    delete_calls = []
    stale.deleteLater = lambda: delete_calls.append(1)

    panel.refresh([keep])
    assert stale.is_ping_animating() is False
    assert delete_calls == [1]
    assert all(it is not stale for it in panel._server_items)


def test_server_list_panel_ping_signal_and_animations(qapp):
    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [Server(name="S1", host="1.1.1.1", port=443),
               Server(name="S2", host="2.2.2.2", port=443)]
    panel.refresh(servers)
    panel.show()

    # 1. Signal updates row for success and failure
    panel.pingResultReceived.emit(id(servers[0]), 42.0)
    assert "42ms" in panel._server_items[0].ping_label.text()
    assert getattr(servers[0], "_last_ping", None) == 42.0
    assert panel._server_items[1].ping_label.text() == ""

    panel.pingResultReceived.emit(id(servers[1]), -1.0)
    assert panel._server_items[1].ping_label.text() == "н/в"
    assert getattr(servers[1], "_last_ping", None) == -1.0
    assert theme.error in panel._server_items[1].ping_label.styleSheet()

    # 2. 64-bit server ID signal survives
    panel.pingResultReceived.emit(2 ** 33 + 7, 42.0)
    assert getattr(servers[0], "_last_ping", None) == 42.0

    # 3. Fade out callback invocation & idempotence
    cb_called = []
    def on_done():
        cb_called.append(True)

    panel.fade_out(on_done)
    assert panel._fade_out_cb is on_done
    panel._on_fade_anim_finished()
    assert len(cb_called) == 1
    assert panel._fade_out_cb is None
    panel._on_fade_anim_finished()
    assert len(cb_called) == 1


def test_server_list_panel_refresh_empty_state_and_spacers(qapp):
    from utils.i18n import tr
    theme = M3Theme()
    panel = ServerListPanel(theme)
    panel.show()

    # 1. Empty list -> empty state shown
    panel.refresh([])
    assert panel.updatesEnabled() is True
    assert panel._empty_widget.isVisible() is True
    assert panel._empty_title.text() == tr("servers.empty_title")
    assert panel._empty_hint.text() == tr("servers.empty_hint")
    assert len(panel._server_items) == 0

    # 2. Server added -> empty state hidden
    srv = Server(name="Test Srv", host="1.1.1.1", port=443, protocol=ProxyProtocol.VLESS)
    panel.refresh([srv])
    assert panel._empty_widget.isVisible() is False
    assert len(panel._server_items) == 1

    # 3. Query with no matches -> search empty state shown
    panel.set_query("nonexistent_server_name")
    assert panel._empty_widget.isVisible() is True
    assert panel._empty_title.text() == tr("servers.search_empty")
    assert panel._empty_hint.text() == tr("servers.search_empty_hint")
    assert len(panel._server_items) == 0

    # 4. Clear query -> server restored, empty state hidden
    panel.set_query("")
    assert panel._empty_widget.isVisible() is False
    assert len(panel._server_items) == 1

    # 5. Group card exists -> empty state hidden even if 0 servers
    group = {"key": "sub1", "title": "Sub 1", "icon": "🌐", "subtitle": "", "is_direct": False, "collapsed": False, "servers": []}
    panel.refresh([], groups=[group])
    assert panel._empty_widget.isVisible() is False
    assert len(panel._group_cards) == 1

    # 6. Bottom spacer reuse and foreign spacer cleanup
    srv1 = Server(name="S1", host="1.1.1.1", port=443)
    srv2 = Server(name="S2", host="2.2.2.2", port=443)
    initial_spacer = panel._bottom_spacer

    for servers in [[srv1], [srv1, srv2], [srv2], []]:
        panel.refresh(servers)
        layout = panel.layout()
        spacers = [layout.itemAt(i).spacerItem() for i in range(layout.count()) if layout.itemAt(i).spacerItem() is not None]
        assert len(spacers) == 1 and spacers[0] is initial_spacer

    # Foreign spacer cleanup
    panel.refresh([srv1])
    panel.layout().addStretch()
    assert panel.layout().count() == 3
    panel.refresh([srv1])
    assert panel.layout().count() == 2
    assert panel.layout().itemAt(1).spacerItem() is panel._bottom_spacer
