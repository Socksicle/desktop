"""Tests for server list virtualization, memory reduction, and lazy widget optimization."""
import pytest
from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
from PySide6.QtCore import QPoint

from ui.server_list_panel import ServerListPanel
from ui.server_item import ServerItem, AnimatedRadioButton
from ui.config_tag_badge import ConfigTagBadge
from utils.theme import M3Theme
from utils.server_model import Server, ProxyProtocol


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_shared_fonts_across_animated_radio_buttons():
    theme = M3Theme()
    r1 = AnimatedRadioButton(theme)
    r2 = AnimatedRadioButton(theme)

    assert r1._font_normal is r2._font_normal
    assert r1._font_bold is r2._font_bold
    assert r1._font_sub is r2._font_sub
    assert r1._font_flag is r2._font_flag


def test_lazy_timer_and_animation_allocation():
    theme = M3Theme()
    srv = Server(name="S1", host="1.1.1.1", port=443)
    item = ServerItem("S1", srv, theme)

    # Initially, no QTimer or QPropertyAnimation allocated
    assert item._ping_anim is None
    assert item.is_ping_animating() is False
    assert item.radio._bg_anim is None
    assert item.protocol_badge._anim is None

    # Starting animation creates timer lazily
    item.start_ping_animation()
    assert item._ping_anim is not None
    assert item.is_ping_animating() is True

    # Stopping stops timer
    item.stop_ping_animation()
    assert item.is_ping_animating() is False


def test_virtualization_with_500_servers_creates_minimal_widgets():
    theme = M3Theme()

    # Host inside a standard QScrollArea to simulate ScreenMain
    scroll = QScrollArea()
    scroll.resize(400, 600)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    panel = ServerListPanel(theme)
    content_layout.addWidget(panel)
    scroll.setWidget(content)
    scroll.show()

    # Create 500 servers
    servers = [Server(name=f"Server_{i}", host=f"10.0.{i // 256}.{i % 256}", port=443) for i in range(500)]
    panel.refresh(servers)

    # Total ServerItem widgets instantiated must be ~20-30, definitely NOT 500
    visible_items = panel._server_items
    assert len(visible_items) < 35
    all_server_items = panel.findChildren(ServerItem)
    assert len(all_server_items) < 40

    # Spacers must account for total height of 500 rows
    vg = panel._virtual_groups[0]
    total_h = vg.top_spacer.height() + len(vg.items) * vg.ROW_STEP + vg.bottom_spacer.height()
    assert total_h == 500 * vg.ROW_STEP


def test_virtualization_scrolling_updates_visible_window():
    theme = M3Theme()

    scroll = QScrollArea()
    scroll.resize(400, 600)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    panel = ServerListPanel(theme)
    content_layout.addWidget(panel)
    scroll.setWidget(content)
    scroll.show()

    servers = [Server(name=f"Server_{i}", host=f"10.0.0.{i}", port=443) for i in range(200)]
    panel.refresh(servers)

    initial_first_server = panel._server_items[0].server.name
    assert initial_first_server == "Server_0"

    # Simulate scroll down by simulating viewport offset change
    vg = panel._virtual_groups[0]
    panel._sync_virtual_group_widgets(vg, 50, 70)
    panel._server_items = list(vg.items)

    assert panel._server_items[0].server.name == "Server_50"
    assert panel._server_items[-1].server.name == "Server_69"
    assert vg.top_spacer.height() == 50 * vg.ROW_STEP
    assert vg.bottom_spacer.height() == (200 - 70) * vg.ROW_STEP


def test_virtualization_selection_persistence_outside_window():
    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [Server(name=f"Server_{i}", host=f"10.0.0.{i}", port=443) for i in range(100)]
    panel.refresh(servers)

    # Select an item outside the visible window (index 80)
    success = panel.set_selected_index(80)
    assert success is True
    assert panel.get_selected_index() == 80


def test_virtualization_ping_result_updates_when_scrolled_into_view():
    theme = M3Theme()
    panel = ServerListPanel(theme)
    servers = [Server(name=f"Server_{i}", host=f"10.0.0.{i}", port=443) for i in range(100)]
    panel.refresh(servers)

    # Deliver ping result for an off-screen server (server 60)
    srv_60 = servers[60]
    panel.pingResultReceived.emit(id(srv_60), 45.0)
    assert getattr(srv_60, "_last_ping", None) == 45.0

    # When server 60 is placed into a virtual window, it displays the latency
    vg = panel._virtual_groups[0]
    panel._sync_virtual_group_widgets(vg, 60, 70)
    panel._server_items = list(vg.items)

    assert "45ms" in panel._server_items[0].ping_label.text()
