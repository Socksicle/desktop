"""Tests for xray config generation for VLESS, VMess, and Shadowsocks protocols."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.server_model import ProxyProtocol
from utils.engines.xray_engine import (
    XrayEngine,
    _generate_config,
    _build_xray_vless_outbound,
    _build_xray_vmess_outbound,
    _build_xray_ss_outbound,
    _build_xray_trojan_outbound,
    _build_xray_stream_settings,
)


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class _FakeServer:
    """Minimal object mimicking Server fields for engine tests."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# VLESS outbound tests
class XrayVlessConfigTest(unittest.TestCase):

    def test_vless_reality_tcp(self):
        server = _FakeServer(
            host="1.2.3.4", port=443,
            uuid="uuid-r", flow="xtls-rprx-vision",
            security="reality", transport="tcp",
            server_name="sni.example.com", fingerprint="chrome",
            public_key="PUBKEY123", short_id="SHORT",
            path="", host_header="",
        )
        ob = _build_xray_vless_outbound(server)
        self.assertEqual(ob["protocol"], "vless")
        self.assertEqual(ob["settings"]["vnext"][0]["address"], "1.2.3.4")
        self.assertEqual(ob["settings"]["vnext"][0]["port"], 443)
        user = ob["settings"]["vnext"][0]["users"][0]
        self.assertEqual(user["id"], "uuid-r")
        self.assertEqual(user["flow"], "xtls-rprx-vision")
        self.assertEqual(user["encryption"], "none")

        stream = ob["streamSettings"]
        self.assertEqual(stream["network"], "tcp")
        self.assertEqual(stream["security"], "reality")
        reality = stream["realitySettings"]
        self.assertEqual(reality["serverName"], "sni.example.com")
        self.assertEqual(reality["fingerprint"], "chrome")
        self.assertEqual(reality["publicKey"], "PUBKEY123")
        self.assertEqual(reality["shortId"], "SHORT")

    def test_vless_tls_websocket(self):
        server = _FakeServer(
            host="ws.host", port=8443,
            uuid="uuid-ws", flow="", security="tls",
            transport="ws", server_name="ws.example.com",
            fingerprint="firefox",
            public_key="", short_id="",
            path="/vless-ws", host_header="cdn.example.com",
        )
        ob = _build_xray_vless_outbound(server)
        stream = ob["streamSettings"]
        self.assertEqual(stream["security"], "tls")
        self.assertEqual(stream["tlsSettings"]["serverName"], "ws.example.com")
        self.assertEqual(stream["tlsSettings"]["fingerprint"], "firefox")
        self.assertEqual(stream["network"], "ws")
        ws = stream["wsSettings"]
        self.assertEqual(ws["path"], "/vless-ws")
        self.assertEqual(ws["headers"]["Host"], "cdn.example.com")

    def test_vless_grpc(self):
        server = _FakeServer(
            host="grpc.host", port=443,
            uuid="uuid-g", flow="", security="tls",
            transport="grpc", server_name="grpc.server",
            fingerprint="chrome",
            public_key="", short_id="",
            path="my-service", host_header="",
        )
        ob = _build_xray_vless_outbound(server)
        stream = ob["streamSettings"]
        self.assertEqual(stream["network"], "grpc")
        self.assertEqual(stream["grpcSettings"]["serviceName"], "my-service")
        self.assertEqual(stream["security"], "tls")

    def test_vless_no_encryption_none_security(self):
        server = _FakeServer(
            host="plain.host", port=80,
            uuid="uuid-plain", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
            public_key="", short_id="",
            path="", host_header="",
        )
        ob = _build_xray_vless_outbound(server)
        stream = ob["streamSettings"]
        self.assertNotIn("security", stream)
        self.assertEqual(stream["network"], "tcp")
        self.assertNotIn("tlsSettings", stream)
        self.assertNotIn("realitySettings", stream)

    def test_vless_flow_ignored_on_ws_or_none_security(self):
        server_ws = _FakeServer(
            host="1.2.3.4", port=443,
            uuid="uuid-r", flow="xtls-rprx-vision",
            security="tls", transport="ws",
            server_name="sni.com", fingerprint="chrome",
        )
        ob_ws = _build_xray_vless_outbound(server_ws)
        self.assertNotIn("flow", ob_ws["settings"]["vnext"][0]["users"][0])

        server_none = _FakeServer(
            host="1.2.3.4", port=443,
            uuid="uuid-r", flow="xtls-rprx-vision",
            security="none", transport="tcp",
            server_name="", fingerprint="",
        )
        ob_none = _build_xray_vless_outbound(server_none)
        self.assertNotIn("flow", ob_none["settings"]["vnext"][0]["users"][0])


# VMess outbound tests
class XrayVmessConfigTest(unittest.TestCase):

    def test_vmess_tls(self):
        server = _FakeServer(
            host="vm.host", port=443,
            uuid="vm-uuid", alter_id=0,
            vmess_security="auto", security="tls",
            transport="tcp", server_name="vm.sni",
            fingerprint="chrome",
            path="", host_header="",
        )
        ob = _build_xray_vmess_outbound(server)
        self.assertEqual(ob["protocol"], "vmess")
        self.assertEqual(ob["settings"]["vnext"][0]["address"], "vm.host")
        user = ob["settings"]["vnext"][0]["users"][0]
        self.assertEqual(user["id"], "vm-uuid")
        self.assertEqual(user["alterId"], 0)
        self.assertEqual(user["security"], "auto")

        stream = ob["streamSettings"]
        self.assertEqual(stream["security"], "tls")
        self.assertEqual(stream["tlsSettings"]["serverName"], "vm.sni")
        self.assertNotIn("realitySettings", stream)

    def test_vmess_no_tls(self):
        server = _FakeServer(
            host="vm.host", port=80,
            uuid="vm-uuid2", alter_id=2,
            vmess_security="chacha20-poly1305",
            security="none", transport="ws",
            server_name="", fingerprint="firefox",
            path="/vmess-ws", host_header="vm.host",
        )
        ob = _build_xray_vmess_outbound(server)
        stream = ob["streamSettings"]
        self.assertNotIn("security", stream)
        self.assertEqual(stream["network"], "ws")
        self.assertEqual(stream["wsSettings"]["path"], "/vmess-ws")
        self.assertEqual(stream["wsSettings"]["headers"]["Host"], "vm.host")

    def test_vmess_grpc(self):
        server = _FakeServer(
            host="g.host", port=443,
            uuid="uuid-grpc", alter_id=0,
            vmess_security="auto", security="tls",
            transport="grpc", server_name="g.sni",
            fingerprint="chrome",
            path="grpc-svc", host_header="",
        )
        ob = _build_xray_vmess_outbound(server)
        stream = ob["streamSettings"]
        self.assertEqual(stream["network"], "grpc")
        self.assertEqual(stream["grpcSettings"]["serviceName"], "grpc-svc")


# Shadowsocks outbound (backward compat)
class XraySSConfigTest(unittest.TestCase):

    def test_ss_outbound(self):
        server = _FakeServer(
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )
        ob = _build_xray_ss_outbound(server)
        self.assertEqual(ob["protocol"], "shadowsocks")
        srv = ob["settings"]["servers"][0]
        self.assertEqual(srv["address"], "1.2.3.4")
        self.assertEqual(srv["port"], 8388)
        self.assertEqual(srv["method"], "aes-256-gcm")
        self.assertEqual(srv["password"], "secret")
        self.assertEqual(ob["streamSettings"], {"network": "tcp"})


# Trojan outbound
class XrayTrojanConfigTest(unittest.TestCase):

    def test_trojan_outbound_tls(self):
        server = _FakeServer(
            host="trojan.example.com", port=443,
            password="pass", security="tls",
            transport="tcp", server_name="sni.example.com",
            fingerprint="chrome", path="", host_header="",
        )
        ob = _build_xray_trojan_outbound(server)
        self.assertEqual(ob["protocol"], "trojan")
        srv = ob["settings"]["servers"][0]
        self.assertEqual(srv["address"], "trojan.example.com")
        self.assertEqual(srv["port"], 443)
        self.assertEqual(srv["password"], "pass")
        self.assertEqual(ob["streamSettings"]["security"], "tls")
        self.assertEqual(ob["streamSettings"]["tlsSettings"]["serverName"], "sni.example.com")

    def test_trojan_outbound_security_none(self):
        server = _FakeServer(
            host="185.188.30.219", port=32087,
            password="pass", security="none",
            transport="tcp", server_name="",
            fingerprint="", path="", host_header="",
        )
        ob = _build_xray_trojan_outbound(server)
        self.assertEqual(ob["protocol"], "trojan")
        self.assertNotIn("security", ob["streamSettings"])
        self.assertNotIn("tlsSettings", ob["streamSettings"])
        self.assertEqual(ob["streamSettings"]["network"], "tcp")


# Full config generation
class XrayFullConfigTest(unittest.TestCase):

    def test_vless_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid-full", flow="xtls-rprx-vision",
            security="reality", transport="tcp",
            server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID",
            path="", host_header="",
        )
        config = _generate_config(server, 1080)
        self.assertIn("log", config)
        self.assertIn("inbounds", config)
        self.assertIn("outbounds", config)
        self.assertIn("routing", config)
        self.assertNotIn("api", config)
        self.assertNotIn("stats", config)
        self.assertNotIn("policy", config)

        self.assertEqual(len(config["inbounds"]), 1)
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["tag"], "socks-in")
        self.assertEqual(inbound["protocol"], "socks")
        self.assertEqual(inbound["listen"], "127.0.0.1")
        self.assertEqual(inbound["port"], 1080)
        self.assertTrue(inbound["settings"]["udp"])

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["protocol"], "vless")
        self.assertEqual(outbound["settings"]["vnext"][0]["users"][0]["id"], "uuid-full")

        direct = config["outbounds"][1]
        self.assertEqual(direct["tag"], "direct")
        self.assertEqual(direct["protocol"], "freedom")

        self.assertEqual(config["routing"]["domainStrategy"], "IPIfNonMatch")
        self.assertEqual(config["routing"]["rules"], [])

    def test_vmess_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="5.6.7.8", port=443,
            uuid="uuid-vm-full", alter_id=0,
            vmess_security="auto", security="tls",
            transport="ws", server_name="sni.example.com",
            fingerprint="chrome",
            path="/path", host_header="host.example.com",
        )
        config = _generate_config(server, 1080)
        outbound = config["outbounds"][0]
        self.assertEqual(outbound["protocol"], "vmess")
        self.assertEqual(outbound["settings"]["vnext"][0]["users"][0]["id"], "uuid-vm-full")
        stream = outbound["streamSettings"]
        self.assertEqual(stream["security"], "tls")
        self.assertEqual(stream["wsSettings"]["path"], "/path")

    def test_ss_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )
        config = _generate_config(server, 1080)
        self.assertEqual(config["outbounds"][0]["protocol"], "shadowsocks")

    def test_single_inbound_and_no_duplicate_ports(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
        )
        for local_port in (1080, 10085, 1081):
            config = _generate_config(server, local_port)
            self.assertEqual(len(config["inbounds"]), 1)
            inbound = config["inbounds"][0]
            self.assertEqual(inbound["tag"], "socks-in")
            self.assertEqual(inbound["port"], local_port)
            self.assertNotEqual(inbound["protocol"], "dokodemo-door")
            self.assertNotIn("api", config)
            self.assertNotIn("stats", config)
            self.assertNotIn("policy", config)
            self.assertNotIn("dokodemo-door", json.dumps(config))

    def test_inbound_port_is_int_of_local_port(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
        )
        config = _generate_config(server, "1080")
        self.assertEqual(config["inbounds"][0]["port"], 1080)
        self.assertIsInstance(config["inbounds"][0]["port"], int)


class XrayBlockUdpTest(unittest.TestCase):

    def _vless_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
        )

    def test_block_udp_off_by_default(self):
        config = _generate_config(self._vless_server(), 1080)
        self.assertEqual(config["routing"]["rules"], [])
        self.assertNotIn("blocked", [o.get("tag") for o in config["outbounds"]])

    def test_block_udp_adds_blackhole_and_routing_rule(self):
        config = _generate_config(self._vless_server(), 1080, block_udp=True)
        self.assertIn(
            {"type": "field", "network": "udp", "outboundTag": "blocked"},
            config["routing"]["rules"],
        )
        blocked = next(o for o in config["outbounds"] if o.get("tag") == "blocked")
        self.assertEqual(blocked["protocol"], "blackhole")

    def test_engine_build_config_propagates_block_udp(self):
        engine = XrayEngine()
        self.assertFalse(engine.block_udp)
        engine.block_udp = True
        config = engine.build_config(self._vless_server())
        self.assertIn(
            {"type": "field", "network": "udp", "outboundTag": "blocked"},
            config["routing"]["rules"],
        )


# _build_xray_stream_settings tests
class XrayStreamSettingsTest(unittest.TestCase):

    def test_reality(self):
        server = _FakeServer(
            security="reality", transport="tcp",
            server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["security"], "reality")
        self.assertEqual(s["realitySettings"]["serverName"], "sni.com")
        self.assertEqual(s["realitySettings"]["fingerprint"], "chrome")
        self.assertEqual(s["realitySettings"]["publicKey"], "PBK")
        self.assertEqual(s["realitySettings"]["shortId"], "SID")
        self.assertEqual(s["network"], "tcp")

    def test_tls(self):
        server = _FakeServer(
            security="tls", transport="tcp",
            server_name="sni.com", fingerprint="firefox",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["security"], "tls")
        self.assertEqual(s["tlsSettings"]["serverName"], "sni.com")
        self.assertEqual(s["tlsSettings"]["fingerprint"], "firefox")
        self.assertNotIn("realitySettings", s)

    def test_none_security(self):
        server = _FakeServer(
            security="none", transport="tcp",
            server_name="", fingerprint="",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertNotIn("security", s)
        self.assertNotIn("tlsSettings", s)
        self.assertNotIn("realitySettings", s)

    def test_ws_transport_with_path_and_host(self):
        server = _FakeServer(
            security="none", transport="ws",
            server_name="", fingerprint="",
            path="/ws-path", host_header="cdn.example.com",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "ws")
        self.assertEqual(s["wsSettings"]["path"], "/ws-path")
        self.assertEqual(s["wsSettings"]["headers"]["Host"], "cdn.example.com")

    def test_ws_transport_without_host(self):
        server = _FakeServer(
            security="none", transport="ws",
            server_name="", fingerprint="",
            path="/ws-path", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["wsSettings"]["path"], "/ws-path")
        self.assertNotIn("headers", s["wsSettings"])

    def test_grpc_transport(self):
        server = _FakeServer(
            security="none", transport="grpc",
            server_name="", fingerprint="",
            path="my-service", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "grpc")
        self.assertEqual(s["grpcSettings"]["serviceName"], "my-service")

    def test_grpc_transport_empty_service(self):
        server = _FakeServer(
            security="none", transport="grpc",
            server_name="", fingerprint="",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "grpc")
        self.assertNotIn("serviceName", s["grpcSettings"])

    def test_xhttp_transport(self):
        server = _FakeServer(
            security="none", transport="xhttp",
            server_name="", fingerprint="",
            path="/xhttp", host_header="xhttp.host",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "xhttp")
        self.assertEqual(s["xhttpSettings"]["path"], "/xhttp")
        self.assertEqual(s["xhttpSettings"]["host"], "xhttp.host")

    def test_xhttp_transport_no_host(self):
        server = _FakeServer(
            security="none", transport="xhttp",
            server_name="", fingerprint="",
            path="/xhttp", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["xhttpSettings"]["path"], "/xhttp")
        self.assertNotIn("host", s["xhttpSettings"])

    def test_tcp_transport(self):
        server = _FakeServer(
            security="none", transport="tcp",
            server_name="", fingerprint="",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "tcp")
        self.assertNotIn("wsSettings", s)
        self.assertNotIn("grpcSettings", s)
        self.assertNotIn("xhttpSettings", s)

    def test_reality_grpc(self):
        server = _FakeServer(
            security="reality", transport="grpc",
            server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID",
            path="svc", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["security"], "reality")
        self.assertEqual(s["network"], "grpc")
        self.assertEqual(s["realitySettings"]["publicKey"], "PBK")
        self.assertEqual(s["grpcSettings"]["serviceName"], "svc")

    def test_tls_ws(self):
        server = _FakeServer(
            security="tls", transport="ws",
            server_name="sni.com", fingerprint="firefox",
            path="/ws", host_header="cdn.com",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["security"], "tls")
        self.assertEqual(s["network"], "ws")
        self.assertEqual(s["tlsSettings"]["fingerprint"], "firefox")
        self.assertEqual(s["wsSettings"]["path"], "/ws")
        self.assertEqual(s["wsSettings"]["headers"]["Host"], "cdn.com")

    def test_reality_xhttp(self):
        server = _FakeServer(
            security="reality", transport="xhttp",
            server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID",
            path="/xh", host_header="xh.host",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["security"], "reality")
        self.assertEqual(s["network"], "xhttp")
        self.assertEqual(s["xhttpSettings"]["path"], "/xh")
        self.assertEqual(s["xhttpSettings"]["host"], "xh.host")

    def test_defaults_when_attrs_missing(self):
        server = _FakeServer(transport="tcp")
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "tcp")
        self.assertNotIn("security", s)

    def test_kcp_transport(self):
        server = _FakeServer(
            security="none", transport="kcp",
            server_name="", fingerprint="",
            path="", host_header="",
            header_type="srtp", seed="kcp-seed",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "kcp")
        self.assertEqual(s["kcpSettings"]["header"]["type"], "srtp")
        self.assertEqual(s["kcpSettings"]["seed"], "kcp-seed")

    def test_httpupgrade_transport(self):
        server = _FakeServer(
            security="none", transport="httpupgrade",
            server_name="", fingerprint="",
            path="/hu-path", host_header="hu.host",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["network"], "httpupgrade")
        self.assertEqual(s["httpupgradeSettings"]["path"], "/hu-path")
        self.assertEqual(s["httpupgradeSettings"]["host"], "hu.host")

    def test_removed_transports_raise_error(self):
        for bad in ("http", "h2", "h3", "quic"):
            server = _FakeServer(
                security="none", transport=bad,
                server_name="", fingerprint="",
                path="", host_header="",
            )
            with self.subTest(transport=bad):
                with self.assertRaises(ValueError) as ctx:
                    _build_xray_stream_settings(server)
                self.assertIn("removed", str(ctx.exception).lower())
                self.assertIn("xhttp", str(ctx.exception).lower())

    def test_xhttp_mode_default_and_custom(self):
        server_auto = _FakeServer(
            security="none", transport="xhttp",
            server_name="", fingerprint="",
            path="/xh", host_header="",
        )
        s_auto = _build_xray_stream_settings(server_auto)
        self.assertEqual(s_auto["xhttpSettings"]["mode"], "auto")

        for mode in ("packet-up", "stream-up", "stream-one"):
            server_mode = _FakeServer(
                security="none", transport="xhttp",
                server_name="", fingerprint="",
                path="/xh", host_header="",
                xhttp_mode=mode,
            )
            s_mode = _build_xray_stream_settings(server_mode)
            self.assertEqual(s_mode["xhttpSettings"]["mode"], mode)

    def test_xhttp_invalid_mode_raises(self):
        server = _FakeServer(
            security="none", transport="xhttp",
            server_name="", fingerprint="",
            path="/xh", host_header="",
            xhttp_mode="invalid-mode",
        )
        with self.assertRaises(ValueError):
            _build_xray_stream_settings(server)

    def test_reality_spider_x(self):
        server = _FakeServer(
            security="reality", transport="tcp",
            server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID", spider_x="/spx",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertEqual(s["realitySettings"]["spiderX"], "/spx")

    def test_tls_never_emits_allow_insecure(self):
        server = _FakeServer(
            security="tls", transport="tcp",
            server_name="sni.com", fingerprint="firefox",
            insecure=True,
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        self.assertNotIn("allowInsecure", s.get("tlsSettings", {}))

    def test_tls_pinned_peer_cert_and_name(self):
        server = _FakeServer(
            security="tls", transport="tcp",
            server_name="sni.com", fingerprint="chrome",
            pinned_peer_cert_sha256="08:1E:1A:29",
            verify_peer_cert_by_name="peer.example.com",
            path="", host_header="",
        )
        s = _build_xray_stream_settings(server)
        tls = s.get("tlsSettings", {})
        self.assertEqual(tls["pinnedPeerCertSha256"], "08:1E:1A:29")
        self.assertEqual(tls["verifyPeerCertByName"], "peer.example.com")


# DNS and unknown protocol tests
class XrayDnsAndProtocolTest(unittest.TestCase):

    def _make_server(self, proto=ProxyProtocol.VLESS):
        return _FakeServer(
            protocol=proto,
            host="1.2.3.4", port=443,
            uuid="u-1", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
        )

    def test_dns_https(self):
        cfg = _generate_config(self._make_server(), 1080, custom_dns="https://1.1.1.1/dns-query")
        self.assertEqual(cfg["dns"]["servers"], ["https://1.1.1.1/dns-query", "localhost"])

    def test_dns_udp_prefix_stripped(self):
        cfg = _generate_config(self._make_server(), 1080, custom_dns="udp://8.8.8.8")
        self.assertEqual(cfg["dns"]["servers"], ["8.8.8.8", "localhost"])

    def test_dns_tcp(self):
        cfg = _generate_config(self._make_server(), 1080, custom_dns="tcp://8.8.8.8")
        self.assertEqual(cfg["dns"]["servers"], ["tcp://8.8.8.8", "localhost"])

    def test_dns_tls_raises_error(self):
        with self.assertRaises(ValueError) as ctx:
            _generate_config(self._make_server(), 1080, custom_dns="tls://1.1.1.1")
        self.assertIn("DNS-over-TLS", str(ctx.exception))

    def test_dns_default_and_none_omits_dns(self):
        for val in (None, "", "   ", "default", "DEFAULT"):
            cfg = _generate_config(self._make_server(), 1080, custom_dns=val)
            self.assertNotIn("dns", cfg)

    def test_unsupported_protocol_raises_value_error(self):
        server = self._make_server(proto="unsupported_proto")
        with self.assertRaises(ValueError):
            _generate_config(server, 1080)


# XrayEngine class tests
class XrayEngineClassTest(unittest.TestCase):

    def test_build_config_vless(self):
        engine = XrayEngine()
        engine.local_port = 1080
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
            public_key="", short_id="",
        )
        config = engine.build_config(server)
        self.assertEqual(config["outbounds"][0]["protocol"], "vless")

    def test_build_config_vmess(self):
        engine = XrayEngine()
        engine.local_port = 1080
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="1.2.3.4", port=443,
            uuid="uuid", alter_id=0,
            vmess_security="auto", security="tls",
            transport="tcp", server_name="sni",
            fingerprint="chrome",
        )
        config = engine.build_config(server)
        self.assertEqual(config["outbounds"][0]["protocol"], "vmess")

    def test_build_args_creates_config_file(self):
        engine = XrayEngine()
        engine.local_port = 1080
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
        )
        args = engine.build_args(server)
        config_path = Path(args[2])
        self.assertTrue(config_path.exists())
        with open(config_path) as f:
            config = json.load(f)
        self.assertEqual(config["outbounds"][0]["protocol"], "vless")
        config_path.unlink(missing_ok=True)

    def test_version_args(self):
        engine = XrayEngine()
        self.assertEqual(engine.version_args(), ["version"])

    def test_process_name(self):
        engine = XrayEngine()
        self.assertEqual(engine.process_name(), "xray")


if __name__ == "__main__":
    unittest.main()
