"""Tests for ConfigTagBadge and animated expandable configuration badges."""
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent

from ui.config_tag_badge import ConfigTagBadge, build_badge_texts
from ui.server_item import ServerItem
from utils.server_model import Server, ProxyProtocol
from utils.theme import M3Theme


def test_build_badge_texts_all_protocols():
    cases = [
        (
            Server(
                name="US VLESS",
                host="example.com",
                port=443,
                protocol=ProxyProtocol.VLESS,
                security="reality",
                transport="grpc",
                flow="xtls-rprx-vision",
            ),
            "VLESS · Reality",
            "VLESS · GRPC · Reality · Vision · :443",
        ),
        (
            Server(
                name="EU VLESS",
                host="example.com",
                port=8443,
                protocol=ProxyProtocol.VLESS,
                security="tls",
                transport="ws",
            ),
            "VLESS · TLS",
            "VLESS · WS · TLS · :8443",
        ),
        (
            Server(
                name="XHTTP Node",
                host="example.com",
                port=443,
                protocol=ProxyProtocol.VLESS,
                security="tls",
                transport="xhttp",
            ),
            "VLESS · TLS",
            "VLESS · XHTTP · TLS · :443",
        ),
        (
            Server(
                name="Plain VLESS",
                host="example.com",
                port=443,
                protocol=ProxyProtocol.VLESS,
                security="tls",
                transport="tcp",
            ),
            "VLESS · TLS",
            "VLESS · TLS · TCP:443",
        ),
        (
            Server(
                name="HY2 Fast",
                host="hy2.example.com",
                port=443,
                protocol=ProxyProtocol.HYSTERIA2,
                congestion_control="bbr",
            ),
            "HY2",
            "HY2 · UDP:443",
        ),
        (
            Server(
                name="HY2 Obfs",
                host="hy2.example.com",
                port=8443,
                protocol=ProxyProtocol.HYSTERIA2,
                congestion_control="cubic",
                obfs="salamander",
            ),
            "HY2",
            "HY2 · SALAMANDER · CUBIC · UDP:8443",
        ),
        (
            Server(
                name="SS Node",
                host="ss.example.com",
                port=8388,
                protocol=ProxyProtocol.SHADOWSOCKS,
                method="aes-256-gcm",
            ),
            "SS",
            "SS · aes-256-gcm · TCP:8388",
        ),
        (
            Server(
                name="SS Quiet",
                host="ss.example.com",
                port=41080,
                protocol=ProxyProtocol.SHADOWSOCKS,
                method="chacha20-ietf-poly1305",
                plugin="obfs-local;obfs=http;obfs-host=example.com",
            ),
            "SS",
            "SS · obfs-local · TCP:41080",
        ),
        (
            Server(
                name="Trojan Node",
                host="tr.example.com",
                port=443,
                protocol=ProxyProtocol.TROJAN,
                security="tls",
            ),
            "TROJAN",
            "TROJAN · TLS · TCP:443",
        ),
        (
            Server(
                name="VMess Node",
                host="vm.example.com",
                port=443,
                protocol=ProxyProtocol.VMESS,
                security="tls",
                transport="ws",
            ),
            "VMESS",
            "VMESS · WS · TLS · :443",
        ),
        (
            Server(
                name="VMess Plain",
                host="vm.example.com",
                port=10086,
                protocol=ProxyProtocol.VMESS,
                security="none",
                transport="tcp",
            ),
            "VMESS",
            "VMESS · TCP:10086",
        ),
        (
            Server(
                name="TUIC Node",
                host="tuic.example.com",
                port=8443,
                protocol=ProxyProtocol.TUIC,
                congestion_control="cubic",
            ),
            "TUIC",
            "TUIC · CUBIC · UDP:8443",
        ),
        (
            Server(
                name="TUIC Node",
                host="tuic.example.com",
                port=8443,
                protocol=ProxyProtocol.TUIC,
                congestion_control="bbr",
            ),
            "TUIC",
            "TUIC · UDP:8443",
        ),
    ]
    for server, expected_compact, expected_expanded in cases:
        compact, expanded = build_badge_texts(server)
        assert compact == expected_compact
        assert expanded == expected_expanded


def test_config_tag_badge_toggle_expanded(qapp):
    theme = M3Theme()
    badge = ConfigTagBadge(theme=theme)
    badge.set_texts("VLESS", "VLESS · Reality · GRPC · :443")

    assert not badge.is_expanded()
    assert badge.text() == "VLESS"
    compact_w = badge.width()

    # Expand non-animated for deterministic test
    badge.set_expanded(True, animated=False)
    assert badge.is_expanded()
    assert badge.text() == "VLESS · Reality · GRPC · :443"
    assert badge.width() > compact_w

    # Collapse non-animated
    badge.set_expanded(False, animated=False)
    assert not badge.is_expanded()
    assert badge.text() == "VLESS"
    assert badge.width() == compact_w


def test_config_tag_badge_progress_interpolation(qapp):
    theme = M3Theme()
    badge = ConfigTagBadge(theme=theme)
    badge.set_texts("SS", "SS · aes-256-gcm · :8388")

    widths = []
    badge.widthChanged.connect(widths.append)

    badge.setProgress(0.5)
    assert len(widths) == 1
    mid_w = widths[0]
    assert badge._compact_width < mid_w < badge._expanded_width


def test_config_tag_badge_mouse_click_isolation(qapp):
    from PySide6.QtCore import QPointF
    theme = M3Theme()
    badge = ConfigTagBadge(theme=theme)
    badge.set_texts("VLESS", "VLESS · Reality")

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5.0, 5.0),
        QPointF(5.0, 5.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    badge.mousePressEvent(event)
    assert event.isAccepted()
    assert badge.is_expanded()


def test_server_item_integration_with_badge(qapp):
    theme = M3Theme()
    server = Server(
        name="Integration Test",
        host="test.com",
        port=443,
        protocol=ProxyProtocol.VLESS,
        security="reality",
        transport="grpc",
    )
    item = ServerItem("Integration Test", server=server, theme=theme)

    assert not item.protocol_badge.isHidden()
    assert not item.protocol_badge.is_expanded()
    assert item.protocol_badge.text() == "VLESS · Reality"

    initial_actions_w = item._actions_width

    # Expand badge
    item.protocol_badge.set_expanded(True, animated=False)
    assert item.protocol_badge.is_expanded()
    assert item._actions_width > initial_actions_w

    # Check radio button is not checked by badge expand
    assert not item.radio.isChecked()
