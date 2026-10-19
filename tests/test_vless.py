"""Unit tests for utils/vless.py VLESS protocol parser and config generator."""
import pytest
from utils.vless import (
    VlessNode,
    parse_vless_url,
    effective_sni,
    generate_singbox_vless_outbound,
    generate_xray_vless_outbound,
    export_vless_url,
    to_server,
    from_server,
)
from utils.server_model import ProxyProtocol

TEST_UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"


def test_parse_vless_reality_vision():
    raw = (
        f"vless://{TEST_UUID}@speedtest.net:443"
        "?type=tcp&security=reality&pbk=aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ABCDEFG="
        "&sid=12345678&spx=%2F&flow=xtls-rprx-vision&fp=chrome&sni=speedtest.net#MyReality"
    )
    node = parse_vless_url(raw)
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
    assert not node.is_private


def test_parse_vless_websocket_tls():
    raw = (
        f"vless://{TEST_UUID}@example.com:443"
        "?type=ws&security=tls&path=%2Fws-path&host=ws.example.com&sni=example.com#WS-Node"
    )
    node = parse_vless_url(raw)
    assert node.transport == "ws"
    assert node.security == "tls"
    assert node.path == "/ws-path"
    assert node.host_header == "ws.example.com"
    assert node.server_name == "example.com"
    assert node.flow == ""  # flow is incompatible with WS and must be cleared


def test_parse_vless_xhttp():
    raw = (
        f"vless://{TEST_UUID}@1.2.3.4:443"
        "?type=xhttp&security=reality&pbk=fakekey&sid=1234&mode=stream-up&path=%2Fxhttp-path#XHTTP-Node"
    )
    node = parse_vless_url(raw)
    assert node.transport == "xhttp"
    assert node.xhttp_mode == "stream-up"
    assert node.path == "/xhttp-path"
    assert node.public_key == "fakekey"


def test_parse_vless_ipv6():
    raw = f"vless://{TEST_UUID}@[2001:db8::1]:8443?type=tcp&security=none#IPv6"
    node = parse_vless_url(raw)
    assert node.host == "2001:db8::1"
    assert node.port == 8443
    assert node.name == "IPv6"


def test_parse_vless_invalid_inputs():
    with pytest.raises(ValueError, match="missing 'vless://' scheme"):
        parse_vless_url("vmess://something")

    with pytest.raises(ValueError, match="missing '@' delimiter"):
        parse_vless_url("vless://no-at-symbol")

    with pytest.raises(ValueError, match="Invalid UUID"):
        parse_vless_url("vless://not-a-valid-uuid@example.com:443")

    with pytest.raises(ValueError, match="Invalid port"):
        parse_vless_url(f"vless://{TEST_UUID}@example.com:99999")


def test_generate_singbox_vless_outbound_reality():
    node = VlessNode(
        uuid=TEST_UUID,
        host="speedtest.net",
        port=443,
        security="reality",
        flow="xtls-rprx-vision",
        transport="tcp",
        server_name="speedtest.net",
        public_key="my-key",
        short_id="abcd",
        fingerprint="chrome",
    )
    outbound = generate_singbox_vless_outbound(node, tag="custom-vless")
    assert outbound["type"] == "vless"
    assert outbound["tag"] == "custom-vless"
    assert outbound["server"] == "speedtest.net"
    assert outbound["server_port"] == 443
    assert outbound["uuid"] == TEST_UUID
    assert outbound["flow"] == "xtls-rprx-vision"
    assert outbound["packet_encoding"] == "xudp"

    tls = outbound["tls"]
    assert tls["enabled"] is True
    assert tls["server_name"] == "speedtest.net"
    assert tls["utls"]["fingerprint"] == "chrome"
    assert tls["reality"]["public_key"] == "my-key"
    assert tls["reality"]["short_id"] == "abcd"


def test_generate_singbox_vless_outbound_ws():
    node = VlessNode(
        uuid=TEST_UUID,
        host="cdn.example.com",
        port=443,
        security="tls",
        transport="ws",
        path="/ws",
        host_header="cdn.example.com",
    )
    outbound = generate_singbox_vless_outbound(node)
    assert "flow" not in outbound
    assert outbound["transport"] == {"type": "ws", "path": "/ws", "headers": {"Host": "cdn.example.com"}}
    assert outbound["tls"]["alpn"] == ["http/1.1"]


def test_generate_singbox_vless_outbound_xhttp_fails():
    node = VlessNode(
        uuid=TEST_UUID,
        host="cdn.example.com",
        port=443,
        transport="xhttp",
    )
    with pytest.raises(ValueError, match="does not support transport 'xhttp'"):
        generate_singbox_vless_outbound(node)


def test_generate_xray_vless_outbound_reality_vision():
    node = VlessNode(
        uuid=TEST_UUID,
        host="target.com",
        port=443,
        security="reality",
        flow="xtls-rprx-vision",
        transport="tcp",
        server_name="target.com",
        public_key="pbk123",
        short_id="sid123",
        spider_x="/spider",
    )
    out = generate_xray_vless_outbound(node, tag="proxy")
    assert out["protocol"] == "vless"
    assert out["tag"] == "proxy"

    user = out["settings"]["vnext"][0]["users"][0]
    assert user["id"] == TEST_UUID
    assert user["flow"] == "xtls-rprx-vision"
    assert user["encryption"] == "none"

    stream = out["streamSettings"]
    assert stream["network"] == "tcp"
    assert stream["security"] == "reality"
    assert stream["realitySettings"]["publicKey"] == "pbk123"
    assert stream["realitySettings"]["shortId"] == "sid123"
    assert stream["realitySettings"]["spiderX"] == "/spider"


def test_generate_xray_vless_outbound_xhttp():
    node = VlessNode(
        uuid=TEST_UUID,
        host="cdn.example.com",
        port=443,
        security="tls",
        transport="xhttp",
        xhttp_mode="stream-one",
        path="/xhttp",
        host_header="cdn.example.com",
    )
    out = generate_xray_vless_outbound(node)
    stream = out["streamSettings"]
    assert stream["network"] == "xhttp"
    assert stream["security"] == "tls"
    assert stream["xhttpSettings"] == {
        "mode": "stream-one",
        "path": "/xhttp",
        "host": "cdn.example.com",
    }


def test_round_trip_export_and_parse():
    orig = VlessNode(
        uuid=TEST_UUID,
        host="node.example.org",
        port=8443,
        name="RoundTripNode",
        security="reality",
        transport="tcp",
        flow="xtls-rprx-vision",
        server_name="sni.example.org",
        fingerprint="firefox",
        public_key="test-key",
        short_id="12ab",
        spider_x="/home",
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
        uuid=TEST_UUID,
        host="vpn.server.net",
        port=443,
        name="BridgeTest",
        security="reality",
        transport="tcp",
        flow="xtls-rprx-vision",
        public_key="pbk-key",
        short_id="sid-key",
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


def test_parse_and_generate_xray_kcp_quic_and_header_type():
    # TCP with HTTP camouflage
    node_tcp_http = parse_vless_url(
        f"vless://{TEST_UUID}@1.1.1.1:80?type=tcp&headerType=http#TCP-HTTP"
    )
    assert node_tcp_http.transport == "tcp"
    assert node_tcp_http.header_type == "http"
    out_tcp = generate_xray_vless_outbound(node_tcp_http)
    assert out_tcp["streamSettings"]["tcpSettings"]["header"]["type"] == "http"

    # mKCP
    node_kcp = parse_vless_url(
        f"vless://{TEST_UUID}@1.1.1.1:10086?type=kcp&headerType=wechat-video&seed=myseed#KCP-Node"
    )
    assert node_kcp.transport == "kcp"
    assert node_kcp.header_type == "wechat-video"
    assert node_kcp.seed == "myseed"
    out_kcp = generate_xray_vless_outbound(node_kcp)
    assert out_kcp["streamSettings"]["kcpSettings"] == {
        "header": {"type": "wechat-video"},
        "seed": "myseed",
    }

    # QUIC in Xray
    node_quic = parse_vless_url(
        f"vless://{TEST_UUID}@1.1.1.1:443?type=quic&quicSecurity=aes-128-gcm&key=pass123&headerType=srtp#QUIC-Node"
    )
    assert node_quic.transport == "quic"
    assert node_quic.quic_security == "aes-128-gcm"
    assert node_quic.quic_key == "pass123"
    out_quic = generate_xray_vless_outbound(node_quic)
    assert out_quic["streamSettings"]["quicSettings"] == {
        "security": "aes-128-gcm",
        "key": "pass123",
        "header": {"type": "srtp"},
    }

