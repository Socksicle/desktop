"""Tests for sing-box config generation for VLESS and VMess protocols."""
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from utils.server_model import Server, ProxyProtocol
from utils.engines.singbox_engine import SingBoxEngine
from utils.engines.singbox_config import (
    _generate_config,
    _build_singbox_dns_server,
)
from utils.engines.singbox_outbounds import (
    _build_singbox_vless_outbound,
    _build_singbox_vmess_outbound,
    _build_singbox_ss_outbound,
    _build_singbox_trojan_outbound,
    _build_singbox_tuic_outbound,
    _build_singbox_transport,
)


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class _FakeServer:
    """Minimal object mimicking Server fields for engine tests."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class SingBoxVlessConfigTest(unittest.TestCase):

    def test_vless_outbound_basic(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="test-uuid", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
            public_key="", short_id="", path="", host_header="",
        )
        ob = _build_singbox_vless_outbound(server)
        self.assertEqual(ob["type"], "vless")
        self.assertEqual(ob["server"], "1.2.3.4")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["uuid"], "test-uuid")
        self.assertNotIn("flow", ob)
        self.assertNotIn("tls", ob)
        self.assertNotIn("transport", ob)

    def test_vless_outbound_reality(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="reality.host", port=443,
            uuid="uuid-r", flow="xtls-rprx-vision",
            security="reality",
            transport="tcp", server_name="sni.example.com",
            fingerprint="chrome",
            public_key="PUBKEY123", short_id="SHORT",
            path="", host_header="",
        )
        ob = _build_singbox_vless_outbound(server)
        self.assertEqual(ob["flow"], "xtls-rprx-vision")
        self.assertIn("tls", ob)
        tls = ob["tls"]
        self.assertTrue(tls["enabled"])
        self.assertEqual(tls["server_name"], "sni.example.com")
        self.assertEqual(tls["utls"]["fingerprint"], "chrome")
        self.assertTrue(tls["reality"]["enabled"])
        self.assertEqual(tls["reality"]["public_key"], "PUBKEY123")
        self.assertEqual(tls["reality"]["short_id"], "SHORT")

    def test_vless_outbound_tls_ws(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="ws.host", port=8443,
            uuid="uuid-ws", flow="", security="tls",
            transport="ws", server_name="ws.example.com",
            fingerprint="firefox",
            public_key="", short_id="",
            path="/vless-ws", host_header="cdn.example.com",
        )
        ob = _build_singbox_vless_outbound(server)
        self.assertIn("tls", ob)
        self.assertNotIn("reality", ob["tls"])
        self.assertEqual(ob["tls"]["alpn"], ["http/1.1"])
        self.assertIn("transport", ob)
        tr = ob["transport"]
        self.assertEqual(tr["type"], "ws")
        self.assertEqual(tr["path"], "/vless-ws")
        self.assertEqual(tr["headers"]["Host"], "cdn.example.com")

    def test_vless_outbound_grpc(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="grpc.host", port=443,
            uuid="uuid-g", flow="", security="tls",
            transport="grpc", server_name="grpc.server",
            fingerprint="chrome",
            public_key="", short_id="",
            path="my-service", host_header="",
        )
        ob = _build_singbox_vless_outbound(server)
        self.assertIn("transport", ob)
        tr = ob["transport"]
        self.assertEqual(tr["type"], "grpc")
        self.assertEqual(tr["service_name"], "my-service")

    def test_vless_outbound_tls_options(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid-fp", flow="", security="tls",
            transport="tcp", server_name="tls.example.com",
            fingerprint="",
        )
        ob = _build_singbox_vless_outbound(server)
        self.assertTrue(ob["tls"]["enabled"])
        self.assertEqual(ob["tls"]["server_name"], "tls.example.com")
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")
        self.assertNotIn("insecure", ob["tls"])

        for kw in ({"allow_insecure": True}, {"insecure": True},
                   {"pinned_peer_cert_sha256": "08:1E:1A"}):
            s = _FakeServer(
                protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443,
                uuid="u", flow="", security="tls", transport="tcp",
                server_name="tls.example.com", **kw,
            )
            self.assertTrue(_build_singbox_vless_outbound(s)["tls"]["insecure"])


    def test_vless_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid-full", flow="xtls-rprx-vision",
            security="reality", transport="tcp",
            server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID",
            path="", host_header="",
            method="", password="",
        )
        config = _generate_config(server, 1080)
        self.assertIn("log", config)
        self.assertEqual(config["log"]["level"], "warn")
        self.assertIn("inbounds", config)
        self.assertIn("outbounds", config)
        self.assertIn("route", config)
        self.assertEqual(config["inbounds"][0]["type"], "mixed")
        self.assertEqual(config["inbounds"][0]["listen_port"], 1080)
        ob = config["outbounds"][0]
        self.assertEqual(ob["type"], "vless")
        self.assertEqual(ob["uuid"], "uuid-full")
        self.assertEqual(config["route"]["final"], "proxy")

    def test_debug_log_level(self):
        from utils.engines import singbox_config
        singbox_config.set_debug_logs(True)
        self.addCleanup(singbox_config.set_debug_logs, False)
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid-dbg", flow="", security="tls",
            transport="tcp", server_name="sni.com",
            insecure=False,
        )
        config = _generate_config(server, 1080)
        self.assertEqual(config["log"]["level"], "debug")

    def test_route_rules_no_geoip_cidr(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="1.2.3.4", port=443,
            uuid="uuid-route", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
            public_key="", short_id="", path="", host_header="",
            method="", password="",
        )
        config = _generate_config(server, 1080)
        rules = config["route"]["rules"]
        self.assertEqual(len(rules), 5)
        self.assertEqual(rules[0], {"port": 53, "action": "sniff"})
        self.assertEqual(rules[1], {"protocol": "dns", "action": "route", "outbound": "proxy"})
        self.assertEqual(rules[2], {"port": 123, "action": "route", "outbound": "direct"})
        self.assertEqual(rules[3], {"ip_cidr": ["1.2.3.4/32"], "action": "route", "outbound": "direct"})
        self.assertEqual(rules[4], {"ip_is_private": True, "action": "route", "outbound": "direct"})
        self.assertEqual(config["route"]["default_domain_resolver"], "remote-dns")
        self.assertEqual(config["dns"]["servers"][0]["detour"], "proxy")
        for rule in rules:
            self.assertNotIn("geoip", json.dumps(rule))

    def test_route_rules_domain_host_direct(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="vless.example.com", port=443,
            uuid="uuid-route", flow="", security="none",
            transport="tcp", server_name="", fingerprint="",
            public_key="", short_id="", path="", host_header="",
            method="", password="",
        )
        config = _generate_config(server, 1080)
        rules = config["route"]["rules"]
        self.assertEqual(len(rules), 5)
        self.assertEqual(rules[0], {"port": 53, "action": "sniff"})
        self.assertEqual(rules[1], {"protocol": "dns", "action": "route", "outbound": "proxy"})
        self.assertEqual(rules[2], {"port": 123, "action": "route", "outbound": "direct"})
        self.assertEqual(rules[3], {"domain": ["vless.example.com"], "action": "route", "outbound": "direct"})
        self.assertEqual(rules[4], {"ip_is_private": True, "action": "route", "outbound": "direct"})


class SingBoxVmessConfigTest(unittest.TestCase):

    def test_vmess_outbound_basic(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="vm.host", port=443,
            uuid="vm-uuid", alter_id=0,
            vmess_security="auto", security="tls",
            transport="tcp", server_name="vm.sni",
            fingerprint="chrome",
            path="", host_header="",
        )
        ob = _build_singbox_vmess_outbound(server)
        self.assertEqual(ob["type"], "vmess")
        self.assertEqual(ob["server"], "vm.host")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["uuid"], "vm-uuid")
        self.assertEqual(ob["alter_id"], 0)
        self.assertEqual(ob["security"], "auto")
        self.assertIn("tls", ob)
        self.assertNotIn("transport", ob)

    def test_vmess_outbound_no_tls(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="vm.host", port=80,
            uuid="vm-uuid2", alter_id=2,
            vmess_security="chacha20-poly1305",
            security="none", transport="ws",
            server_name="", fingerprint="firefox",
            path="/vmess-ws", host_header="vm.host",
        )
        ob = _build_singbox_vmess_outbound(server)
        self.assertNotIn("tls", ob)
        self.assertIn("transport", ob)
        tr = ob["transport"]
        self.assertEqual(tr["type"], "ws")
        self.assertEqual(tr["path"], "/vmess-ws")
        self.assertEqual(tr["headers"]["Host"], "vm.host")

    def test_vmess_outbound_grpc(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="g.host", port=443,
            uuid="uuid-grpc", alter_id=0,
            vmess_security="auto", security="tls",
            transport="grpc", server_name="g.sni",
            fingerprint="chrome",
            path="grpc-svc", host_header="",
        )
        ob = _build_singbox_vmess_outbound(server)
        self.assertIn("transport", ob)
        self.assertEqual(ob["transport"]["type"], "grpc")
        self.assertEqual(ob["transport"]["service_name"], "grpc-svc")

    def test_vmess_outbound_tls_options(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="vm.host", port=443,
            uuid="vm-uuid-fp", alter_id=0,
            vmess_security="auto", security="tls",
            transport="tcp", server_name="vm.sni",
            fingerprint="",
        )
        ob = _build_singbox_vmess_outbound(server)
        self.assertTrue(ob["tls"]["enabled"])
        self.assertEqual(ob["tls"]["server_name"], "vm.sni")
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")
        self.assertNotIn("insecure", ob["tls"])

        for kw in ({"allow_insecure": True}, {"insecure": True}):
            s = _FakeServer(
                protocol=ProxyProtocol.VMESS, host="vm.host", port=443,
                uuid="u", alter_id=0, vmess_security="auto", security="tls",
                transport="tcp", server_name="vm.sni", **kw,
            )
            self.assertTrue(_build_singbox_vmess_outbound(s)["tls"]["insecure"])


    def test_vmess_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="5.6.7.8", port=443,
            uuid="uuid-vm-full", alter_id=0,
            vmess_security="auto", security="tls",
            transport="ws", server_name="sni.example.com",
            fingerprint="chrome",
            path="/path", host_header="host.example.com",
            method="", password="",
        )
        config = _generate_config(server, 1080)
        self.assertEqual(config["outbounds"][0]["type"], "vmess")
        self.assertEqual(config["outbounds"][0]["uuid"], "uuid-vm-full")
        self.assertEqual(config["outbounds"][0]["alter_id"], 0)
        self.assertEqual(config["route"]["final"], "proxy")

    def test_route_rule_uses_ip_is_private(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="5.6.7.8", port=443,
            uuid="uuid-vm-route", alter_id=0,
            vmess_security="auto", security="tls",
            transport="ws", server_name="sni.example.com",
            fingerprint="chrome",
            path="/path", host_header="host.example.com",
            method="", password="",
        )
        config = _generate_config(server, 1080)
        rule = [r for r in config["route"]["rules"] if r.get("ip_is_private")][0]
        self.assertIn("ip_is_private", rule)
        self.assertEqual(rule["ip_is_private"], True)
        self.assertNotIn("ip_cidr", rule)


class SingBoxPortsConfigTest(unittest.TestCase):
    """Inbound invariants: one mixed listener on the local port only."""

    def _server(self):
        return _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )

    def test_inbound_invariants_and_ports(self):
        for port in (1080, 2080, 9090):
            config = _generate_config(self._server(), port)
            self.assertEqual(len(config["inbounds"]), 1)
            inbound = config["inbounds"][0]
            self.assertEqual(inbound["type"], "mixed")
            self.assertEqual(inbound["tag"], "mixed-in")
            self.assertEqual(inbound["listen"], "127.0.0.1")
            self.assertEqual(inbound["listen_port"], port)
            self.assertNotIn("experimental", config)
            self.assertEqual(json.dumps(config).count('"listen_port"'), 1)

    def test_engine_build_config_invariants(self):
        engine = SingBoxEngine()
        engine.local_port = 2080
        config = engine.build_config(self._server())
        self.assertEqual(len(config["inbounds"]), 1)
        self.assertEqual(config["inbounds"][0]["listen_port"], 2080)
        self.assertNotIn("experimental", config)


class SingBoxSSConfigTest(unittest.TestCase):
    """Ensure Shadowsocks config generation is not broken."""

    def test_ss_outbound_and_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )
        ob = _build_singbox_ss_outbound(server)
        self.assertEqual(ob["type"], "shadowsocks")
        self.assertEqual(ob["server"], "1.2.3.4")
        self.assertEqual(ob["server_port"], 8388)
        self.assertEqual(ob["method"], "aes-256-gcm")
        self.assertEqual(ob["password"], "secret")
        self.assertFalse(ob["multiplex"]["enabled"])

        engine = SingBoxEngine()
        engine.local_port = 1080
        config = engine.build_config(server)
        self.assertEqual(config["outbounds"][0]["type"], "shadowsocks")
        self.assertEqual(config["route"]["final"], "proxy")

        rule = [r for r in config["route"]["rules"] if r.get("ip_is_private")][0]
        self.assertEqual(rule["ip_is_private"], True)

    def test_ss_build_args_creates_config_file(self):
        engine = SingBoxEngine()
        engine.local_port = 1080
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="secret")
        args = engine.build_args(server)
        config_path = Path(args[2])
        self.assertTrue(config_path.exists())
        with open(config_path) as f:
            config = json.load(f)
        self.assertEqual(config["outbounds"][0]["type"], "shadowsocks")
        config_path.unlink(missing_ok=True)



class SingBoxTransportTest(unittest.TestCase):

    def test_tcp_returns_none(self):
        server = _FakeServer(transport="tcp", path="", host_header="")
        self.assertIsNone(_build_singbox_transport(server))

    def test_ws_with_path_and_host(self):
        server = _FakeServer(transport="ws", path="/ws", host_header="cdn.com")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "ws")
        self.assertEqual(tr["path"], "/ws")
        self.assertEqual(tr["headers"]["Host"], "cdn.com")

    def test_ws_fallback_to_server_name(self):
        server = _FakeServer(transport="ws", path="/ws", host_header="", server_name="sni.example.com")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "ws")
        self.assertEqual(tr["path"], "/ws")
        self.assertEqual(tr["headers"]["Host"], "sni.example.com")

    def test_ws_without_host(self):
        server = _FakeServer(transport="ws", path="/ws", host_header="", server_name="")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "ws")
        self.assertEqual(tr["path"], "/ws")
        self.assertNotIn("headers", tr)

    def test_grpc(self):
        server = _FakeServer(transport="grpc", path="svc1", host_header="")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "grpc")
        self.assertEqual(tr["service_name"], "svc1")

    def test_grpc_empty_service(self):
        server = _FakeServer(transport="grpc", path="", host_header="")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "grpc")
        self.assertNotIn("service_name", tr)

    def test_httpupgrade_with_path_and_host(self):
        server = _FakeServer(transport="httpupgrade", path="/hu", host_header="cdn.com")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "httpupgrade")
        self.assertEqual(tr["path"], "/hu")
        self.assertEqual(tr["host"], "cdn.com")

    def test_http_with_path_and_host(self):
        server = _FakeServer(transport="http", path="/h", host_header="cdn.com")
        tr = _build_singbox_transport(server)
        self.assertEqual(tr["type"], "http")
        self.assertEqual(tr["path"], "/h")
        self.assertEqual(tr["host"], ["cdn.com"])

    def test_quic_transport(self):
        server = _FakeServer(transport="quic", path="", host_header="")
        self.assertEqual(_build_singbox_transport(server), {"type": "quic"})

    def test_unsupported_transport_raises(self):
        for transport in ("xhttp", "kcp", "h2", "splithttp"):
            server = _FakeServer(transport=transport, path="/p", host_header="")
            with self.assertRaises(ValueError):
                _build_singbox_transport(server)


class SingBoxTrojanConfigTest(unittest.TestCase):

    def test_trojan_outbound_basic(self):
        server = _FakeServer(
            protocol=ProxyProtocol.TROJAN,
            host="trojan.example.com",
            port=443,
            password="my_password",
            transport="tcp",
            server_name="trojan.example.com",
            fingerprint="chrome",
        )
        ob = _build_singbox_trojan_outbound(server)
        self.assertEqual(ob["type"], "trojan")
        self.assertEqual(ob["server"], "trojan.example.com")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["password"], "my_password")
        self.assertIn("tls", ob)
        self.assertTrue(ob["tls"]["enabled"])
        self.assertEqual(ob["tls"]["server_name"], "trojan.example.com")
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")
        self.assertFalse(ob["multiplex"]["enabled"])

    def test_trojan_outbound_ws(self):
        server = _FakeServer(
            protocol=ProxyProtocol.TROJAN,
            host="1.2.3.4",
            port=443,
            password="pass",
            transport="ws",
            path="/trojan-ws",
            host_header="cdn.trojan.com",
            server_name="sni.trojan.com",
        )
        ob = _build_singbox_trojan_outbound(server)
        self.assertEqual(ob["type"], "trojan")
        self.assertEqual(ob["tls"]["server_name"], "sni.trojan.com")
        self.assertEqual(ob["tls"]["alpn"], ["http/1.1"])
        self.assertIn("transport", ob)
        self.assertEqual(ob["transport"]["type"], "ws")
        self.assertEqual(ob["transport"]["path"], "/trojan-ws")
        self.assertEqual(ob["transport"]["headers"]["Host"], "cdn.trojan.com")

    def test_trojan_outbound_security_none(self):
        server = _FakeServer(
            protocol=ProxyProtocol.TROJAN,
            host="185.188.30.219",
            port=32087,
            password="pass",
            security="none",
            transport="tcp",
        )
        ob = _build_singbox_trojan_outbound(server)
        self.assertEqual(ob["type"], "trojan")
        self.assertNotIn("tls", ob)

    def test_trojan_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.TROJAN,
            host="trojan.server",
            port=443,
            password="pass",
            transport="tcp",
        )
        cfg = _generate_config(server, 1080)
        self.assertEqual(cfg["outbounds"][0]["type"], "trojan")
        self.assertEqual(cfg["outbounds"][0]["password"], "pass")
        self.assertEqual(cfg["route"]["final"], "proxy")


class SingBoxTuicConfigTest(unittest.TestCase):

    def test_tuic_outbound_basic(self):
        server = _FakeServer(
            protocol=ProxyProtocol.TUIC,
            host="tuic.example.com",
            port=8443,
            uuid="2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365",
            password="tuic_password",
            server_name="tuic.example.com",
            congestion_control="bbr",
            udp_relay_mode="native",
        )
        ob = _build_singbox_tuic_outbound(server)
        self.assertEqual(ob["type"], "tuic")
        self.assertEqual(ob["server"], "tuic.example.com")
        self.assertEqual(ob["server_port"], 8443)
        self.assertEqual(ob["uuid"], "2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365")
        self.assertEqual(ob["password"], "tuic_password")
        self.assertEqual(ob["congestion_control"], "bbr")
        self.assertEqual(ob["udp_relay_mode"], "native")
        self.assertIn("tls", ob)
        self.assertEqual(ob["tls"]["server_name"], "tuic.example.com")
        self.assertEqual(ob["tls"]["alpn"], ["h3"])

    def test_tuic_full_config(self):
        server = _FakeServer(
            protocol=ProxyProtocol.TUIC,
            host="tuic.server",
            port=8443,
            uuid="uuid123",
            password="pass",
            congestion_control="cubic",
            udp_relay_mode="quic",
        )
        cfg = _generate_config(server, 1080)
        self.assertEqual(cfg["outbounds"][0]["type"], "tuic")
        self.assertEqual(cfg["outbounds"][0]["uuid"], "uuid123")
        self.assertEqual(cfg["outbounds"][0]["congestion_control"], "cubic")
        self.assertEqual(cfg["outbounds"][0]["udp_relay_mode"], "quic")
        self.assertEqual(cfg["route"]["final"], "proxy")


class SingBoxDnsServerTest(unittest.TestCase):

    def test_doh_domain_sets_local_resolver(self):
        srv = _build_singbox_dns_server("https://dns.quad9.net/dns-query", detour="proxy")
        self.assertEqual(srv["type"], "https")
        self.assertEqual(srv["server"], "dns.quad9.net")
        self.assertEqual(srv["domain_resolver"], "local-dns")

    def test_doh_ip_no_resolver(self):
        srv = _build_singbox_dns_server("https://1.1.1.1/dns-query", detour="proxy")
        self.assertEqual(srv["type"], "https")
        self.assertEqual(srv["server"], "1.1.1.1")
        self.assertNotIn("domain_resolver", srv)

    def test_dot_server(self):
        srv = _build_singbox_dns_server("tls://1.1.1.1", detour="proxy")
        self.assertEqual(srv["type"], "tls")
        self.assertEqual(srv["server"], "1.1.1.1")
        self.assertEqual(srv["server_port"], 853)
        self.assertEqual(srv["detour"], "proxy")

    def test_udp_bare_ipv6_host(self):
        srv = _build_singbox_dns_server("2001:db8::1")
        self.assertEqual(srv["type"], "udp")
        self.assertEqual(srv["server"], "2001:db8::1")
        self.assertEqual(srv["server_port"], 53)

    def test_udp_bracketed_ipv6_with_port(self):
        srv = _build_singbox_dns_server("[2001:db8::1]:5353")
        self.assertEqual(srv["server"], "2001:db8::1")
        self.assertEqual(srv["server_port"], 5353)

    def test_udp_domain_gets_local_resolver(self):
        srv = _build_singbox_dns_server("dns.example.com")
        self.assertEqual(srv["type"], "udp")
        self.assertEqual(srv["server"], "dns.example.com")
        self.assertEqual(srv["server_port"], 53)
        self.assertEqual(srv["domain_resolver"], "local-dns")

    def test_invalid_dns_input_raises(self):
        for target in ("http://1.1.1.1", "9.9.9.9:70000", "9.9.9.9:notaport", ""):
            with self.assertRaises(ValueError, msg=target):
                _build_singbox_dns_server(target)


class SingBoxBlockUdpTest(unittest.TestCase):

    def _ss_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )

    def test_block_udp_off_by_default(self):
        for tun in (False, True):
            config = _generate_config(self._ss_server(), 1080, tun_mode=tun)
            self.assertNotIn(
                {"network": "udp", "action": "reject"},
                config["route"]["rules"],
            )
            self.assertNotIn("block", [o.get("tag") for o in config["outbounds"]])

    def test_block_udp_plain_mode_keeps_dns_first(self):
        config = _generate_config(self._ss_server(), 1080, block_udp=True)
        rules = config["route"]["rules"]
        self.assertEqual(rules[0], {"port": 53, "action": "sniff"})
        self.assertEqual(rules[1], {"protocol": "dns", "action": "route", "outbound": "proxy"})
        self.assertEqual(rules[-1], {"network": "udp", "action": "reject"})
        self.assertNotIn("block", [o.get("tag") for o in config["outbounds"]])

    def test_block_udp_tun_mode_keeps_dns_hijack(self):
        config = _generate_config(self._ss_server(), 1080, tun_mode=True, block_udp=True)
        rules = config["route"]["rules"]
        hijack = next(i for i, r in enumerate(rules) if r.get("action") == "hijack-dns")
        reject = next(i for i, r in enumerate(rules) if r.get("action") == "reject")
        self.assertLess(hijack, reject)
        self.assertEqual(rules[reject], {"network": "udp", "action": "reject"})
        self.assertNotIn("block", [o.get("tag") for o in config["outbounds"]])

    def test_engine_build_config_propagates_block_udp(self):
        engine = SingBoxEngine()
        self.assertFalse(engine.block_udp)
        engine.block_udp = True
        config = engine.build_config(self._ss_server())
        self.assertIn(
            {"network": "udp", "action": "reject"},
            config["route"]["rules"],
        )


class SingBoxTunnelOptionsTest(unittest.TestCase):

    def _ss_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )

    def _vless_server(self, flow=""):
        return _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="vless.example.com", port=443,
            uuid="uuid-vless", flow=flow, security="tls",
            transport="tcp", server_name="vless.example.com",
            fingerprint="chrome",
        )

    def _vmess_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.VMESS,
            host="vmess.example.com", port=443,
            uuid="uuid-vmess", alter_id=0, vmess_security="auto",
            security="tls", transport="tcp",
            server_name="vmess.example.com", fingerprint="chrome",
        )

    def _trojan_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.TROJAN,
            host="trojan.example.com", port=443,
            password="pass", transport="tcp",
            server_name="trojan.example.com", fingerprint="chrome",
        )

    def _hy2_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.example.com", port=443,
            password="pass", server_name="hy2.example.com",
        )

    def _tuic_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.TUIC,
            host="tuic.example.com", port=8443,
            uuid="uuid-tuic", password="pass",
            server_name="tuic.example.com",
        )

    def test_tunnel_options_off_by_default(self):
        config = _generate_config(self._vless_server(), 1080)
        outbound = config["outbounds"][0]
        self.assertNotIn("fragment", outbound["tls"])
        self.assertNotIn("record_fragment", outbound["tls"])
        self.assertNotIn("multiplex", outbound)

    def test_tls_fragment_uses_stock_singbox_tls_fields(self):
        for server in (self._vless_server(), self._vmess_server(),
                       self._trojan_server(), self._hy2_server(),
                       self._tuic_server()):
            with self.subTest(protocol=server.protocol):
                config = _generate_config(server, 1080, tls_fragment=True)
                tls = config["outbounds"][0]["tls"]
                self.assertIs(tls["fragment"], True)
                self.assertIs(tls["record_fragment"], True)
                self.assertEqual(tls["fragment_fallback_delay"], "500ms")

    def test_tls_fragment_needs_a_tls_block(self):
        config = _generate_config(self._ss_server(), 1080, tls_fragment=True)
        self.assertNotIn("tls", config["outbounds"][0])

    def test_mux_added_to_supported_outbounds(self):
        expected = {
            "enabled": True,
            "protocol": "yamux",
            "max_connections": 8,
            "padding": False,
        }
        for server in (self._ss_server(), self._vless_server(),
                       self._vmess_server(), self._trojan_server()):
            with self.subTest(protocol=server.protocol):
                config = _generate_config(
                    server, 1080, mux_enabled=True,
                    mux_protocol="yamux", mux_max_connections=8)
                self.assertEqual(config["outbounds"][0]["multiplex"], expected)

    def test_mux_skips_vless_vision_flow(self):
        config = _generate_config(
            self._vless_server(flow="xtls-rprx-vision"), 1080,
            mux_enabled=True)
        self.assertNotIn("multiplex", config["outbounds"][0])

    def test_mux_skips_quic_outbounds(self):
        for server in (self._hy2_server(), self._tuic_server()):
            with self.subTest(protocol=server.protocol):
                config = _generate_config(server, 1080, mux_enabled=True)
                self.assertNotIn("multiplex", config["outbounds"][0])

    def test_invalid_mux_options_raise(self):
        cases = (
            {"mux_protocol": "quic"},
            {"mux_max_connections": 0},
            {"mux_max_connections": 17},
            {"mux_max_connections": "abc"},
            {"mux_max_connections": True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    _generate_config(self._ss_server(), 1080,
                                     mux_enabled=True, **kwargs)

    def test_engine_build_config_propagates_tunnel_options(self):
        engine = SingBoxEngine()
        engine.tls_fragment = True
        engine.mux_enabled = True
        engine.mux_protocol = "smux"
        engine.mux_max_connections = 6
        config = engine.build_config(self._vless_server())
        outbound = config["outbounds"][0]
        self.assertIs(outbound["tls"]["fragment"], True)
        self.assertEqual(outbound["multiplex"]["protocol"], "smux")
        self.assertEqual(outbound["multiplex"]["max_connections"], 6)


class SingBoxTunConfigTest(unittest.TestCase):

    def test_tun_mode_generates_tun_inbound_and_dns(self):
        server = _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="1.2.3.4", port=8388,
            method="aes-256-gcm", password="secret",
        )
        config = _generate_config(server, 1080, tun_mode=True)
        self.assertIn("dns", config)
        self.assertEqual(config["dns"]["servers"][0]["type"], "https")
        self.assertEqual(config["dns"]["servers"][0]["server"], "1.1.1.1")
        self.assertEqual(config["dns"]["servers"][1]["type"], "local")
        self.assertEqual(len(config["dns"]["servers"]), 2)
        self.assertEqual(config["dns"]["strategy"], "prefer_ipv4")
        self.assertEqual(config["dns"]["reverse_mapping"], True)
        self.assertEqual(len(config["inbounds"]), 2)
        tun_in = config["inbounds"][0]
        self.assertEqual(tun_in["type"], "tun")
        self.assertEqual(tun_in["address"], ["172.19.0.1/30"])
        self.assertEqual(tun_in["dns_mode"], "hijack")
        self.assertTrue(tun_in["auto_route"])
        self.assertNotIn("endpoint_independent_nat", tun_in)
        self.assertNotIn("route_address", tun_in)
        self.assertEqual(tun_in["route_exclude_address"], ["1.2.3.4/32"])
        mixed_in = config["inbounds"][1]
        self.assertEqual(mixed_in["type"], "mixed")
        self.assertEqual(mixed_in["listen_port"], 1080)
        self.assertEqual(config["route"]["default_domain_resolver"], "remote-dns")
        if sys.platform.startswith("linux"):
            # sdelay.txt: kernel bypass pre-match fires before sniffing.
            rules = config["route"]["rules"]
            self.assertEqual(tun_in["interface_name"], "sb_tun0")
            self.assertTrue(tun_in["auto_redirect"])
            self.assertTrue(tun_in["strict_route"])
            self.assertEqual(tun_in["stack"], "mixed")
            self.assertEqual(rules[0],
                             {"ip_is_private": True, "action": "bypass"})
            self.assertEqual(rules[1], {"action": "sniff"})
            self.assertEqual(rules[2],
                             {"inbound": ["tun-in"], "protocol": "dns",
                              "action": "hijack-dns"})
            rest = rules[3:]
        else:
            self.assertTrue(tun_in["interface_name"].startswith("socksicle-"))
            self.assertFalse(tun_in["strict_route"])
            self.assertEqual(tun_in["stack"], "mixed")
            self.assertNotIn("auto_redirect", tun_in)
            rest = config["route"]["rules"][2:]
        self.assertEqual(rest[0]["port"], 123)
        self.assertEqual(rest[1]["ip_cidr"], ["1.2.3.4/32"])
        self.assertEqual(rest[2]["ip_is_private"], True)
        self.assertEqual(config["outbounds"][0]["domain_resolver"], "local-dns")
        self.assertEqual(config["outbounds"][1]["domain_resolver"], "local-dns")

    def test_tun_mode_domain_host_exclusion(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS,
            host="vpn.example.com", port=443,
            uuid="11111111-1111-1111-1111-111111111111",
            security="reality", public_key="pbk", short_id="sid",
        )
        config = _generate_config(server, 1080, tun_mode=True)
        tun_in = config["inbounds"][0]
        if sys.platform.startswith("linux"):
            self.assertTrue(tun_in["strict_route"])
        else:
            self.assertFalse(tun_in["strict_route"])
        self.assertNotIn("route_address", tun_in)
        self.assertNotIn("route_exclude_address", tun_in)
        # Server domain is routed direct in route rules
        domain_rule = next(r for r in config["route"]["rules"] if "domain" in r)
        self.assertEqual(domain_rule["domain"], ["vpn.example.com"])
        self.assertEqual(domain_rule["outbound"], "direct")

    def test_tun_mode_ipv6_host_exclusion(self):
        server = _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS,
            host="2001:db8::1", port=8388,
            method="aes-256-gcm", password="secret",
        )
        config = _generate_config(server, 1080, tun_mode=True)
        tun_in = config["inbounds"][0]
        self.assertEqual(tun_in["route_exclude_address"], ["2001:db8::1/128"])
        ip_rule = next(r for r in config["route"]["rules"] if "ip_cidr" in r)
        self.assertEqual(ip_rule["ip_cidr"], ["2001:db8::1/128"])
        self.assertEqual(ip_rule["outbound"], "direct")

    def test_singbox_engine_process_name_with_tun(self):
        engine = SingBoxEngine()
        self.assertEqual(engine.process_name(), "sing-box")
        engine.tun_mode = True
        self.assertEqual(engine.process_name(), "sing-box (TUN)")

    def test_singbox_binary_validates_configs(self):
        """If sing-box.exe binary is present, validate generated configs using `sing-box check`."""
        engine = SingBoxEngine()
        binary = engine.find_binary()
        if not binary or not binary.exists():
            return
        import subprocess
        for proto, tun in ((ProxyProtocol.SHADOWSOCKS, False), (ProxyProtocol.VLESS, True)):
            srv = _FakeServer(
                protocol=proto, host="1.2.3.4", port=443,
                password="pass", method="aes-256-gcm", uuid="11111111-1111-1111-1111-111111111111",
                flow="", security="none", transport="tcp", server_name="", fingerprint="",
                public_key="", short_id="", path="", host_header="",
                congestion_control="bbr", udp_relay_mode="native"
            )
            cfg = _generate_config(srv, 1080, tun_mode=tun)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(cfg, f)
                temp_name = f.name
            try:
                res = subprocess.run([str(binary), "check", "-c", temp_name],
                                     capture_output=True, text=True,
                                     encoding="utf-8", errors="replace")
                self.assertEqual(res.returncode, 0, f"sing-box check failed: {res.stderr}")
            finally:
                if os.path.exists(temp_name):
                    os.remove(temp_name)


    def test_multiserver_selector_config(self):
        s1 = _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS, host="1.1.1.1", port=8388,
            password="pass", method="aes-256-gcm", key="ss-1"
        )
        s2 = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="2.2.2.2", port=443,
            uuid="11111111-1111-1111-1111-111111111111", flow="", security="none",
            transport="tcp", server_name="", fingerprint="", public_key="", short_id="",
            path="", host_header="", key="vless-2"
        )
        s3 = _FakeServer(
            protocol=ProxyProtocol.HYSTERIA2, host="hy.example.com", port=443,
            password="pass", ports="", up_mbps=0, down_mbps=0, obfs="", obfs_password="",
            server_name="hy.example.com", insecure=False, key="hy-3"
        )

        tag_map = {}
        cfg = _generate_config(
            server=s2,
            local_port=1080,
            tun_mode=True,
            available_servers=[s1, s2, s3],
            api_port=9098,
            api_secret="secret_abc",
            server_tag_map_out=tag_map,
        )

        self.assertIn("experimental", cfg)
        self.assertEqual(cfg["experimental"]["clash_api"]["external_controller"], "127.0.0.1:9098")
        self.assertEqual(cfg["experimental"]["clash_api"]["secret"], "secret_abc")

        outbounds = cfg["outbounds"]
        selector = outbounds[0]
        self.assertEqual(selector["type"], "selector")
        self.assertEqual(selector["tag"], "proxy")
        self.assertEqual(selector["interrupt_exist_connections"], True)
        self.assertEqual(selector["default"], tag_map["vless-2"])
        self.assertEqual(len(selector["outbounds"]), 3)

        # TUN inbound excludes direct server IPs
        tun_in = cfg["inbounds"][0]
        self.assertIn("1.1.1.1/32", tun_in["route_exclude_address"])
        self.assertIn("2.2.2.2/32", tun_in["route_exclude_address"])

        # Check with sing-box binary if available
        engine = SingBoxEngine()
        binary = engine.find_binary()
        if binary and binary.exists():
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(cfg, f)
                temp_name = f.name
            try:
                import subprocess
                res = subprocess.run([str(binary), "check", "-c", temp_name],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(res.returncode, 0, f"sing-box check failed: {res.stderr}")
            finally:
                if os.path.exists(temp_name):
                    os.remove(temp_name)

    def test_singbox_engine_switch_server(self):
        from unittest.mock import patch, MagicMock

        engine = SingBoxEngine()
        s1 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.1.1.1", port=8388, key="s1")
        s2 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="2.2.2.2", port=8388, key="s2")

        engine.available_servers = [s1, s2]
        engine._api_port = 9099
        engine._api_secret = "tok123"
        engine._server_tag_map = {"s1": "node_0", "s2": "node_1"}
        engine.current_server = s1

        # Not running yet
        self.assertFalse(engine.can_switch_server(s2))

        # Pretend process is alive
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        engine.process = mock_proc

        self.assertTrue(engine.can_switch_server(s2))
        s_unknown = _FakeServer(key="unknown")
        self.assertFalse(engine.can_switch_server(s_unknown))

        # Test switch_server sends PUT request
        captured_requests = []
        class _FakeResponse:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): pass

        def _mock_urlopen(req, timeout=2.0):
            captured_requests.append(req)
            return _FakeResponse()

        mock_opener = MagicMock()
        mock_opener.open.side_effect = _mock_urlopen
        with patch("urllib.request.build_opener", return_value=mock_opener) as mock_build:
            ok = engine.switch_server(s2)

        self.assertTrue(ok)
        mock_build.assert_called_once()
        build_args, _ = mock_build.call_args
        self.assertEqual(len(build_args), 1)
        self.assertIsInstance(build_args[0], urllib.request.ProxyHandler)
        self.assertEqual(build_args[0].proxies, {})
        self.assertEqual(engine.current_server, s2)
        self.assertTrue(len(captured_requests) >= 1)
        put_req = captured_requests[0]
        self.assertEqual(put_req.get_method(), "PUT")
        self.assertEqual(put_req.full_url, "http://127.0.0.1:9099/proxies/proxy")
        self.assertIn("Bearer tok123", put_req.headers.get("Authorization", ""))
        body = json.loads(put_req.data.decode("utf-8"))
        self.assertEqual(body, {"name": "node_1"})

class SingBoxSelectorActiveServerTest(unittest.TestCase):
    """The selected server must never silently fall back to another node."""

    def _ss(self, key, host, port=8388):
        return _FakeServer(
            protocol=ProxyProtocol.SHADOWSOCKS, host=host, port=port,
            password="pass", method="aes-256-gcm", key=key, name=key,
        )

    def _broken(self, key):
        return _FakeServer(protocol="no-such-proto", host="broken.invalid",
                           port=1, key=key, name=key)

    def test_invalid_active_server_raises(self):
        for bad in (self._broken("active"), self._ss("active", "9.9.9.9", port=None)):
            with self.assertRaises(ValueError):
                _generate_config(bad, 1080,
                                 available_servers=[self._ss("bg", "1.1.1.1")])

    def test_broken_background_node_is_skipped(self):
        active = self._ss("active", "9.9.9.9")
        cfg = _generate_config(active, 1080,
                               available_servers=[active, self._broken("bg")])
        self.assertEqual(cfg["outbounds"][0]["server"], "9.9.9.9")

    def test_selector_default_is_active_server(self):
        bg = self._ss("bg", "1.1.1.1")
        active = self._ss("active", "2.2.2.2")
        tag_map = {}
        cfg = _generate_config(active, 1080,
                               available_servers=[bg, active],
                               server_tag_map_out=tag_map)
        selector = cfg["outbounds"][0]
        self.assertEqual(selector["type"], "selector")
        self.assertEqual(selector["default"], tag_map["active"])
        self.assertEqual(selector["default"], "node_1")



if __name__ == "__main__":
    unittest.main()
