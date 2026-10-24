"""Tests for VLESS protocol parser, generator and round-trip export."""

import pytest

from utils.server_model import ProxyProtocol
from utils.vless import (
    VlessNode,
    parse_vless_url,
    export_vless_url,
    generate_singbox_vless_outbound,
    generate_xray_vless_outbound,
    to_server,
    from_server,
)

TEST_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_parse_vless_variants_and_invalid_inputs():
    # Reality Vision
    raw_reality = (
        f"vless://{TEST_UUID}@speedtest.net:443"
        "?type=tcp&security=reality&pbk=aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ABCDEFG="
        "&sid=12345678&spx=%2F&flow=xtls-rprx-vision&fp=chrome&sni=speedtest.net#MyReality"
    )
    node = parse_vless_url(raw_reality)
    assert node.uuid == TEST_UUID
    assert node.host == "speedtest.net"
    assert node.port == 443
    assert node.name == "MyReality"
    assert node.security == "reality"
    assert node.transport == "tcp"
    assert node.flow == "xtls-rprx-vision"
    assert node.public_key == "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ABCDEFG="
    assert node.short_id == "12345678"
    assert node.spider_x == "/"
    assert node.fingerprint == "chrome"
    assert node.server_name == "speedtest.net"

    # WebSocket TLS
    raw_ws = (
        f"vless://{TEST_UUID}@example.com:443"
        "?type=ws&security=tls&path=%2Fws-path&host=ws.example.com&sni=example.com#WS-Node"
    )
    node_ws = parse_vless_url(raw_ws)
    assert node_ws.transport == "ws"
    assert node_ws.security == "tls"
    assert node_ws.path == "/ws-path"
    assert node_ws.host_header == "ws.example.com"
    assert node_ws.flow == ""

    # XHTTP
    raw_xhttp = (
        f"vless://{TEST_UUID}@1.2.3.4:443"
        "?type=xhttp&security=reality&pbk=fakekey&sid=1234&mode=stream-up&path=%2Fxhttp-path#XHTTP-Node"
    )
    node_xhttp = parse_vless_url(raw_xhttp)
    assert node_xhttp.transport == "xhttp"
    assert node_xhttp.xhttp_mode == "stream-up"
    assert node_xhttp.path == "/xhttp-path"

    # IPv6
    node_ipv6 = parse_vless_url(f"vless://{TEST_UUID}@[2001:db8::1]:8443?type=tcp&security=none#IPv6")
    assert node_ipv6.host == "2001:db8::1"
    assert node_ipv6.port == 8443

    # Invalid inputs
    with pytest.raises(ValueError, match="missing 'vless://' scheme"):
        parse_vless_url("vmess://something")
    with pytest.raises(ValueError, match="missing '@' delimiter"):
        parse_vless_url("vless://no-at-symbol")
    with pytest.raises(ValueError, match="Invalid UUID"):
        parse_vless_url("vless://not-a-valid-uuid@example.com:443")
    with pytest.raises(ValueError, match="Invalid port"):
        parse_vless_url(f"vless://{TEST_UUID}@example.com:99999")


def test_generate_singbox_vless_outbounds():
    # Reality
    node_reality = VlessNode(
        uuid=TEST_UUID, host="speedtest.net", port=443, security="reality",
        flow="xtls-rprx-vision", transport="tcp", server_name="speedtest.net",
        public_key="my-key", short_id="abcd", fingerprint="chrome",
    )
    ob_reality = generate_singbox_vless_outbound(node_reality, tag="custom-vless")
    assert ob_reality["type"] == "vless"
    assert ob_reality["tag"] == "custom-vless"
    assert ob_reality["server"] == "speedtest.net"
    assert ob_reality["server_port"] == 443
    assert ob_reality["uuid"] == TEST_UUID
    assert ob_reality["flow"] == "xtls-rprx-vision"
    assert ob_reality["packet_encoding"] == "xudp"
    tls = ob_reality["tls"]
    assert tls["enabled"] is True
    assert tls["reality"]["public_key"] == "my-key"
    assert tls["reality"]["short_id"] == "abcd"

    # WebSocket
    node_ws = VlessNode(
        uuid=TEST_UUID, host="cdn.example.com", port=443, security="tls",
        transport="ws", path="/ws", host_header="cdn.example.com",
    )
    ob_ws = generate_singbox_vless_outbound(node_ws)
    assert "flow" not in ob_ws
    assert ob_ws["transport"] == {"type": "ws", "path": "/ws", "headers": {"Host": "cdn.example.com"}}
    assert ob_ws["tls"]["alpn"] == ["http/1.1"]

    # A certificate pin must not disable TLS verification
    node_pinned = VlessNode(
        uuid=TEST_UUID, host="cdn.example.com", port=443, security="tls",
        transport="tcp", pinned_peer_cert_sha256="08:1E:1A",
    )
    assert "insecure" not in generate_singbox_vless_outbound(node_pinned)["tls"]

    # XHTTP unsupported raises ValueError
    node_xhttp = VlessNode(uuid=TEST_UUID, host="cdn.example.com", port=443, transport="xhttp")
    with pytest.raises(ValueError, match="does not support transport 'xhttp'"):
        generate_singbox_vless_outbound(node_xhttp)


def test_generate_xray_vless_outbounds():
    # Reality Vision
    node_reality = VlessNode(
        uuid=TEST_UUID, host="target.com", port=443, security="reality",
        flow="xtls-rprx-vision", transport="tcp", server_name="target.com",
        public_key="pbk123", short_id="sid123", spider_x="/spider",
    )
    out_reality = generate_xray_vless_outbound(node_reality, tag="proxy")
    assert out_reality["protocol"] == "vless"
    assert out_reality["tag"] == "proxy"
    user = out_reality["settings"]["vnext"][0]["users"][0]
    assert user["id"] == TEST_UUID
    assert user["flow"] == "xtls-rprx-vision"
    stream = out_reality["streamSettings"]
    assert stream["network"] == "tcp"
    assert stream["security"] == "reality"
    assert stream["realitySettings"]["publicKey"] == "pbk123"

    # XHTTP
    node_xhttp = VlessNode(
        uuid=TEST_UUID, host="cdn.example.com", port=443, security="tls",
        transport="xhttp", xhttp_mode="stream-one", path="/xhttp", host_header="cdn.example.com",
    )
    out_xhttp = generate_xray_vless_outbound(node_xhttp)
    assert out_xhttp["streamSettings"]["network"] == "xhttp"
    assert out_xhttp["streamSettings"]["xhttpSettings"] == {"mode": "stream-one", "path": "/xhttp", "host": "cdn.example.com"}

    # TCP with HTTP camouflage, mKCP and QUIC
    node_tcp_http = parse_vless_url(f"vless://{TEST_UUID}@1.1.1.1:80?type=tcp&headerType=http#TCP-HTTP")
    assert generate_xray_vless_outbound(node_tcp_http)["streamSettings"]["tcpSettings"]["header"]["type"] == "http"

    node_kcp = parse_vless_url(f"vless://{TEST_UUID}@1.1.1.1:10086?type=kcp&headerType=wechat-video&seed=myseed#KCP")
    assert generate_xray_vless_outbound(node_kcp)["streamSettings"]["kcpSettings"] == {"header": {"type": "wechat-video"}, "seed": "myseed"}

    node_quic = parse_vless_url(f"vless://{TEST_UUID}@1.1.1.1:443?type=quic&quicSecurity=aes-128-gcm&key=pass123&headerType=srtp#QUIC")
    assert generate_xray_vless_outbound(node_quic)["streamSettings"]["quicSettings"] == {"security": "aes-128-gcm", "key": "pass123", "header": {"type": "srtp"}}


def test_round_trip_export_and_parse():
    orig = VlessNode(
        uuid=TEST_UUID, host="node.example.org", port=8443, name="RoundTripNode",
        security="reality", transport="tcp", flow="xtls-rprx-vision",
        server_name="sni.example.org", fingerprint="firefox", public_key="test-key",
        short_id="12ab", spider_x="/home",
    )
    url = export_vless_url(orig)
    parsed = parse_vless_url(url)
    assert parsed.uuid == orig.uuid
    assert parsed.host == orig.host
    assert parsed.port == orig.port
    assert parsed.name == orig.name
    assert parsed.security == orig.security
    assert parsed.transport == orig.transport
    assert parsed.flow == orig.flow
    assert parsed.server_name == orig.server_name
    assert parsed.fingerprint == orig.fingerprint
    assert parsed.public_key == orig.public_key
    assert parsed.short_id == orig.short_id
    assert parsed.spider_x == orig.spider_x


def test_to_server_and_from_server_bridge():
    node = VlessNode(
        uuid=TEST_UUID, host="vpn.server.net", port=443, name="BridgeTest",
        security="reality", transport="tcp", flow="xtls-rprx-vision",
        public_key="pbk-key", short_id="sid-key",
    )
    server = to_server(node)
    assert server.protocol == ProxyProtocol.VLESS
    assert server.host == "vpn.server.net"
    assert server.uuid == TEST_UUID
    assert server.flow == "xtls-rprx-vision"
    assert server.public_key == "pbk-key"

    node_back = from_server(server)
    assert node_back.uuid == node.uuid
    assert node_back.host == node.host
    assert node_back.flow == node.flow
    assert node_back.public_key == node.public_key
