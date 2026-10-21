"""Tests for ServerListPanel and AnimatedRadioButton optimizations."""
import pytest
from unittest.mock import MagicMock
from PySide6.QtGui import QColor, QFont

from ui.server_list_panel import ServerListPanel
from ui.server_item import ServerItem, AnimatedRadioButton
from utils.theme import M3Theme
from utils.server_model import Server, ProxyProtocol


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_animated_radio_button_cached_fonts():
    theme = M3Theme()
    radio = AnimatedRadioButton(theme)
    assert hasattr(radio, "_font_normal")
    assert hasattr(radio, "_font_bold")
    assert isinstance(radio._font_normal, QFont)
    assert isinstance(radio._font_bold, QFont)
    assert radio._font_bold.bold() is True
    assert radio._font_normal.bold() is False


def test_ping_result_signal_updates_row():
    """pingResultReceived reaches _apply_ping_result (replaces invokeMethod)."""
    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [Server(name="S1", host="1.1.1.1", port=443),
               Server(name="S2", host="2.2.2.2", port=443)]
    panel.refresh(servers)
    panel.show()

    panel.pingResultReceived.emit(id(servers[0]), 42.0)

    assert "42ms" in panel._server_items[0].ping_label.text()
    assert getattr(servers[0], "_last_ping", None) == 42.0
    assert panel._server_items[1].ping_label.text() == ""

    # Failed ping sets "н/в" and error color
    panel.pingResultReceived.emit(id(servers[1]), -1.0)
    assert panel._server_items[1].ping_label.text() == "н/в"
    assert getattr(servers[1], "_last_ping", None) == -1.0
    assert theme.error in panel._server_items[1].ping_label.styleSheet()


def test_ping_result_signal_accepts_64bit_server_id():
    """64-bit id() values must survive the signal (C int would OverflowError)."""
    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [Server(name="S1", host="1.1.1.1", port=443)]
    panel.refresh(servers)
    panel.show()

    panel.pingResultReceived.emit(2 ** 33 + 7, 42.0)

    assert panel._server_items[0].ping_label.text() == ""
    assert getattr(servers[0], "_last_ping", None) is None


def test_ping_threshold_colors():
    theme = M3Theme()
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


def test_server_list_panel_fade_out_cb():
    theme = M3Theme()
    panel = ServerListPanel(theme)
    
    cb_called = []
    def on_done():
        cb_called.append(True)

    panel.fade_out(on_done)
    assert panel._fade_out_cb is on_done

    # Trigger fade animation finish
    panel._on_fade_anim_finished()
    assert len(cb_called) == 1
    assert panel._fade_out_cb is None

    # Triggering again when cb is None should not fail
    panel._on_fade_anim_finished()
    assert len(cb_called) == 1


def test_server_list_panel_refresh_updates_enabled():
    theme = M3Theme()
    panel = ServerListPanel(theme)

    srv = Server(name="Test Srv", host="1.1.1.1", port=443, protocol=ProxyProtocol.VLESS)
    
    # Verify refresh completes and updatesEnabled is True at the end
    panel.refresh([srv])
    assert panel.updatesEnabled() is True
    assert len(panel._server_items) == 1


def test_refresh_stops_stale_ping_and_deletes_row_once():
    theme = M3Theme()
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


def test_server_list_panel_empty_state_lifecycle():
    from utils.i18n import tr
    theme = M3Theme()
    panel = ServerListPanel(theme)
    panel.show()

    # 1. Empty list -> empty state shown
    panel.refresh([])
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

    # 5. Group card exists -> empty state hidden even if group has 0 servers
    group = {"key": "sub1", "title": "Sub 1", "icon": "🌐", "subtitle": "", "is_direct": False, "collapsed": False, "servers": []}
    panel.refresh([], groups=[group])
    assert panel._empty_widget.isVisible() is False
    assert len(panel._group_cards) == 1


def test_server_list_panel_bottom_spacer_reuse_and_cleanup():
    """Verify that repeated refreshes reuse the bottom spacer and do not accumulate spacers."""
    theme = M3Theme()
    panel = ServerListPanel(theme)
    srv1 = Server(name="S1", host="1.1.1.1", port=443)
    srv2 = Server(name="S2", host="2.2.2.2", port=443)

    initial_spacer = panel._bottom_spacer

    # Refresh several times with different data
    for servers in [[srv1], [srv1, srv2], [srv2], []]:
        panel.refresh(servers)
        layout = panel.layout()
        # Find all spacer items in the layout
        spacers = [layout.itemAt(i).spacerItem() for i in range(layout.count()) if layout.itemAt(i).spacerItem() is not None]
        assert len(spacers) == 1
        assert spacers[0] is initial_spacer
        assert layout.itemAt(layout.count() - 1).spacerItem() is initial_spacer


def test_server_list_panel_cleans_foreign_spacers():
    """Any orphaned spacer in the layout is cleanly removed and not retained."""
    theme = M3Theme()
    panel = ServerListPanel(theme)
    srv = Server(name="S1", host="1.1.1.1", port=443)
    panel.refresh([srv])

    # Inject a foreign stretch spacer directly into the layout
    panel.layout().addStretch()
    assert panel.layout().count() == 3

    # Refresh should clean out the foreign spacer and keep only the persistent one
    panel.refresh([srv])
    assert panel.layout().count() == 2
    assert panel.layout().itemAt(1).spacerItem() is panel._bottom_spacer

