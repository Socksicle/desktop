"""Unit tests for sing-box config generation across all protocols."""
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock
import pytest

from utils.server_model import ProxyProtocol
from utils.engines.singbox_config import _generate_config, _build_singbox_dns_server, LAN_CIDRS
from utils.engines.singbox_engine import SingBoxEngine
from utils.engines.singbox_outbounds import (
    _build_singbox_vless_outbound,
    _build_singbox_vmess_outbound,
    _build_singbox_trojan_outbound,
    _build_singbox_tuic_outbound,
    _build_singbox_transport,
)


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class _FakeServer:
    def __init__(self, **kwargs):
        self.protocol = kwargs.get("protocol", ProxyProtocol.VLESS)
        for k, v in kwargs.items():
            setattr(self, k, v)


class SingBoxVlessConfigTest(unittest.TestCase):

    def test_vless_outbound_variants(self):
        # Basic
        s_basic = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443,
            uuid="test-uuid", flow="", security="none", transport="tcp",
            server_name="", fingerprint="", public_key="", short_id="", path="", host_header="",
        )
        ob = _build_singbox_vless_outbound(s_basic)
        self.assertEqual(ob["type"], "vless")
        self.assertEqual(ob["server"], "1.2.3.4")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["uuid"], "test-uuid")
        self.assertNotIn("flow", ob)
        self.assertNotIn("tls", ob)
        self.assertNotIn("transport", ob)

        # Reality
        s_reality = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="reality.host", port=443,
            uuid="uuid-r", flow="xtls-rprx-vision", security="reality",
            transport="tcp", server_name="sni.example.com", fingerprint="chrome",
            public_key="PUBKEY123", short_id="SHORT", path="", host_header="",
        )
        ob_r = _build_singbox_vless_outbound(s_reality)
        self.assertEqual(ob_r["flow"], "xtls-rprx-vision")
        self.assertTrue(ob_r["tls"]["enabled"])
        self.assertEqual(ob_r["tls"]["server_name"], "sni.example.com")
        self.assertEqual(ob_r["tls"]["utls"]["fingerprint"], "chrome")
        self.assertTrue(ob_r["tls"]["reality"]["enabled"])
        self.assertEqual(ob_r["tls"]["reality"]["public_key"], "PUBKEY123")
        self.assertEqual(ob_r["tls"]["reality"]["short_id"], "SHORT")

        # WebSocket TLS
        s_ws = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="ws.host", port=8443,
            uuid="uuid-ws", flow="", security="tls", transport="ws",
            server_name="ws.example.com", fingerprint="firefox",
            public_key="", short_id="", path="/vless-ws", host_header="cdn.example.com",
        )
        ob_ws = _build_singbox_vless_outbound(s_ws)
        self.assertIn("tls", ob_ws)
        self.assertNotIn("reality", ob_ws["tls"])
        self.assertEqual(ob_ws["tls"]["alpn"], ["http/1.1"])
        self.assertEqual(ob_ws["transport"]["type"], "ws")
        self.assertEqual(ob_ws["transport"]["path"], "/vless-ws")
        self.assertEqual(ob_ws["transport"]["headers"]["Host"], "cdn.example.com")

        # gRPC
        s_grpc = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="grpc.host", port=443,
            uuid="uuid-g", flow="", security="tls", transport="grpc",
            server_name="grpc.server", fingerprint="chrome",
            public_key="", short_id="", path="my-service", host_header="",
        )
        ob_g = _build_singbox_vless_outbound(s_grpc)
        self.assertEqual(ob_g["transport"]["type"], "grpc")
        self.assertEqual(ob_g["transport"]["service_name"], "my-service")

        # TLS options & insecure flags
        s_tls = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443,
            uuid="uuid-fp", flow="", security="tls", transport="tcp",
            server_name="tls.example.com", fingerprint="",
        )
        ob_tls = _build_singbox_vless_outbound(s_tls)
        self.assertTrue(ob_tls["tls"]["enabled"])
        self.assertEqual(ob_tls["tls"]["server_name"], "tls.example.com")
        self.assertEqual(ob_tls["tls"]["utls"]["fingerprint"], "chrome")
        self.assertNotIn("insecure", ob_tls["tls"])

        for kw in ({"allow_insecure": True}, {"insecure": True}):
            s = _FakeServer(
                protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443,
                uuid="u", flow="", security="tls", transport="tcp",
                server_name="tls.example.com", **kw,
            )
            self.assertTrue(_build_singbox_vless_outbound(s)["tls"]["insecure"])

        # A certificate pin must not disable verification: sing-box 1.14
        # cannot enforce the pin, so normal validation stays in place.
        s_pinned = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443,
            uuid="u", flow="", security="tls", transport="tcp",
            server_name="tls.example.com", pinned_peer_cert_sha256="08:1E:1A",
        )
        self.assertNotIn("insecure", _build_singbox_vless_outbound(s_pinned)["tls"])

    def test_vless_full_config_and_routing_rules(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443,
            uuid="uuid-full", flow="xtls-rprx-vision", security="reality",
            transport="tcp", server_name="sni.com", fingerprint="chrome",
            public_key="PBK", short_id="SID", path="", host_header="",
            method="", password="",
        )
        config = _generate_config(server, 1080)
        self.assertEqual(config["log"]["level"], "warn")
        self.assertEqual(config["inbounds"][0]["type"], "mixed")
        self.assertEqual(config["inbounds"][0]["listen_port"], 1080)
        self.assertEqual(config["outbounds"][0]["type"], "vless")
        self.assertEqual(config["outbounds"][0]["uuid"], "uuid-full")
        self.assertEqual(config["route"]["final"], "proxy")

        # Debug log level
        from utils.engines import singbox_config
        singbox_config.set_debug_logs(True)
        try:
            self.assertEqual(_generate_config(server, 1080)["log"]["level"], "debug")
        finally:
            singbox_config.set_debug_logs(False)

        # Route rules without geoip
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

        # Domain host direct route
        s_domain = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="vless.example.com", port=443,
            uuid="uuid-route", flow="", security="none", transport="tcp",
            server_name="", fingerprint="", public_key="", short_id="",
            path="", host_header="", method="", password="",
        )
        cfg_domain = _generate_config(s_domain, 1080)
        self.assertEqual(cfg_domain["route"]["rules"][3], {"domain": ["vless.example.com"], "action": "route", "outbound": "direct"})


class SingBoxVmessConfigTest(unittest.TestCase):

    def test_vmess_outbound_variants(self):
        # Basic TLS
        s_basic = _FakeServer(
            protocol=ProxyProtocol.VMESS, host="vm.host", port=443,
            uuid="vm-uuid", alter_id=0, vmess_security="auto", security="tls",
            transport="tcp", server_name="vm.sni", fingerprint="chrome",
            path="", host_header="",
        )
        ob = _build_singbox_vmess_outbound(s_basic)
        self.assertEqual(ob["type"], "vmess")
        self.assertEqual(ob["server"], "vm.host")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["uuid"], "vm-uuid")
        self.assertEqual(ob["alter_id"], 0)
        self.assertEqual(ob["security"], "auto")
        self.assertIn("tls", ob)
        self.assertNotIn("transport", ob)

        # No TLS + WS
        s_notls = _FakeServer(
            protocol=ProxyProtocol.VMESS, host="vm.host", port=80,
            uuid="vm-uuid2", alter_id=2, vmess_security="chacha20-poly1305",
            security="none", transport="ws", server_name="", fingerprint="firefox",
            path="/vmess-ws", host_header="vm.host",
        )
        ob_notls = _build_singbox_vmess_outbound(s_notls)
        self.assertNotIn("tls", ob_notls)
        self.assertEqual(ob_notls["transport"]["type"], "ws")
        self.assertEqual(ob_notls["transport"]["path"], "/vmess-ws")
        self.assertEqual(ob_notls["transport"]["headers"]["Host"], "vm.host")

        # gRPC
        s_grpc = _FakeServer(
            protocol=ProxyProtocol.VMESS, host="g.host", port=443,
            uuid="uuid-grpc", alter_id=0, vmess_security="auto", security="tls",
            transport="grpc", server_name="g.sni", fingerprint="chrome",
            path="grpc-svc", host_header="",
        )
        ob_grpc = _build_singbox_vmess_outbound(s_grpc)
        self.assertEqual(ob_grpc["transport"]["type"], "grpc")
        self.assertEqual(ob_grpc["transport"]["service_name"], "grpc-svc")

        # TLS options
        s_tls = _FakeServer(
            protocol=ProxyProtocol.VMESS, host="vm.host", port=443,
            uuid="vm-uuid-fp", alter_id=0, vmess_security="auto", security="tls",
            transport="tcp", server_name="vm.sni", fingerprint="",
        )
        ob_tls = _build_singbox_vmess_outbound(s_tls)
        self.assertTrue(ob_tls["tls"]["enabled"])
        self.assertEqual(ob_tls["tls"]["server_name"], "vm.sni")
        self.assertEqual(ob_tls["tls"]["utls"]["fingerprint"], "chrome")
        self.assertNotIn("insecure", ob_tls["tls"])

        for kw in ({"allow_insecure": True}, {"insecure": True}):
            s = _FakeServer(
                protocol=ProxyProtocol.VMESS, host="vm.host", port=443,
                uuid="u", alter_id=0, vmess_security="auto", security="tls",
                transport="tcp", server_name="vm.sni", **kw,
            )
            self.assertTrue(_build_singbox_vmess_outbound(s)["tls"]["insecure"])

    def test_vmess_full_config_and_private_rules(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VMESS, host="5.6.7.8", port=443,
            uuid="uuid-vm-full", alter_id=0, vmess_security="auto", security="tls",
            transport="ws", server_name="sni.example.com", fingerprint="chrome",
            path="/path", host_header="host.example.com", method="", password="",
        )
        config = _generate_config(server, 1080)
        self.assertEqual(config["outbounds"][0]["type"], "vmess")
        self.assertEqual(config["outbounds"][0]["uuid"], "uuid-vm-full")
        self.assertEqual(config["outbounds"][0]["alter_id"], 0)
        self.assertEqual(config["route"]["final"], "proxy")

        rule = [r for r in config["route"]["rules"] if r.get("ip_is_private")][0]
        self.assertEqual(rule["ip_is_private"], True)
        self.assertNotIn("ip_cidr", rule)


class SingBoxPortsConfigTest(unittest.TestCase):

    def test_inbound_invariants_and_ports(self):
        for proto in (ProxyProtocol.VLESS, ProxyProtocol.VMESS, ProxyProtocol.SHADOWSOCKS):
            s = _FakeServer(protocol=proto, host="1.2.3.4", port=443, uuid="u", method="aes-256-gcm", password="p")
            config = _generate_config(s, 1080)
            self.assertIsInstance(config["inbounds"][0]["listen_port"], int)
            self.assertEqual(config["inbounds"][0]["listen_port"], 1080)
            self.assertEqual(len(config["inbounds"]), 1)
            self.assertEqual(config["inbounds"][0]["type"], "mixed")

    def test_engine_build_config_invariants(self):
        engine = SingBoxEngine()
        engine.local_port = 1080
        s = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="p")
        config = engine.build_config(s)
        self.assertEqual(config["inbounds"][0]["listen_port"], 1080)


class SingBoxSSConfigTest(unittest.TestCase):

    def test_ss_outbound_and_full_config(self):
        s = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        config = _generate_config(s, 1080)
        ob = config["outbounds"][0]
        self.assertEqual(ob["type"], "shadowsocks")
        self.assertEqual(ob["server"], "1.2.3.4")
        self.assertEqual(ob["server_port"], 8388)
        self.assertEqual(ob["method"], "aes-256-gcm")
        self.assertEqual(ob["password"], "secret")
        self.assertNotIn("tls", ob)
        self.assertEqual(config["route"]["final"], "proxy")

    def test_ss_build_args_creates_config_file(self):
        from types import SimpleNamespace
        engine = SingBoxEngine()
        engine.local_port = 1080
        server = SimpleNamespace(host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        args = engine.build_args(server)
        config_path = Path(args[2])
        self.assertTrue(config_path.exists())
        with open(config_path) as f:
            config = json.load(f)
        self.assertEqual(config["outbounds"][0]["type"], "shadowsocks")
        config_path.unlink(missing_ok=True)


class SingBoxTransportTest(unittest.TestCase):

    def test_transports_supported(self):
        # TCP returns None
        self.assertIsNone(_build_singbox_transport(_FakeServer(transport="tcp", path="", host_header="")))

        # WS variants
        tr1 = _build_singbox_transport(_FakeServer(transport="ws", path="/ws", host_header="cdn.com"))
        self.assertEqual(tr1, {"type": "ws", "path": "/ws", "headers": {"Host": "cdn.com"}})

        tr2 = _build_singbox_transport(_FakeServer(transport="ws", path="/ws", host_header="", server_name="sni.example.com"))
        self.assertEqual(tr2, {"type": "ws", "path": "/ws", "headers": {"Host": "sni.example.com"}})

        tr3 = _build_singbox_transport(_FakeServer(transport="ws", path="/ws", host_header="", server_name=""))
        self.assertEqual(tr3, {"type": "ws", "path": "/ws"})

        # gRPC & httpupgrade
        tr_grpc = _build_singbox_transport(_FakeServer(transport="grpc", path="my-svc", host_header=""))
        self.assertEqual(tr_grpc, {"type": "grpc", "service_name": "my-svc"})

        tr_up = _build_singbox_transport(_FakeServer(transport="httpupgrade", path="/up", host_header="h.com"))
        self.assertEqual(tr_up, {"type": "httpupgrade", "path": "/up", "host": "h.com"})

    def test_unsupported_transport_raises(self):
        for transport in ("xhttp", "kcp", "h2", "splithttp", "unknown"):
            with self.assertRaises(ValueError):
                _build_singbox_transport(_FakeServer(transport=transport, path="/p", host_header=""))


class SingBoxTrojanConfigTest(unittest.TestCase):

    def test_trojan_outbound_variants_and_full_config(self):
        # Basic
        s_basic = _FakeServer(
            protocol=ProxyProtocol.TROJAN, host="trojan.example.com", port=443,
            password="my_password", transport="tcp", server_name="trojan.example.com", fingerprint="chrome",
        )
        ob = _build_singbox_trojan_outbound(s_basic)
        self.assertEqual(ob["type"], "trojan")
        self.assertEqual(ob["server"], "trojan.example.com")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["password"], "my_password")
        self.assertTrue(ob["tls"]["enabled"])
        self.assertEqual(ob["tls"]["server_name"], "trojan.example.com")
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")
        self.assertFalse(ob["multiplex"]["enabled"])

        # WS
        s_ws = _FakeServer(
            protocol=ProxyProtocol.TROJAN, host="1.2.3.4", port=443, password="pass",
            transport="ws", path="/trojan-ws", host_header="cdn.trojan.com", server_name="sni.trojan.com",
        )
        ob_ws = _build_singbox_trojan_outbound(s_ws)
        self.assertEqual(ob_ws["tls"]["server_name"], "sni.trojan.com")
        self.assertEqual(ob_ws["tls"]["alpn"], ["http/1.1"])
        self.assertEqual(ob_ws["transport"]["type"], "ws")
        self.assertEqual(ob_ws["transport"]["path"], "/trojan-ws")
        self.assertEqual(ob_ws["transport"]["headers"]["Host"], "cdn.trojan.com")

        # Security None
        s_none = _FakeServer(protocol=ProxyProtocol.TROJAN, host="185.188.30.219", port=32087, password="pass", security="none", transport="tcp")
        self.assertNotIn("tls", _build_singbox_trojan_outbound(s_none))

        # Full config
        cfg = _generate_config(s_basic, 1080)
        self.assertEqual(cfg["outbounds"][0]["type"], "trojan")
        self.assertEqual(cfg["outbounds"][0]["password"], "my_password")
        self.assertEqual(cfg["route"]["final"], "proxy")


class SingBoxTuicConfigTest(unittest.TestCase):

    def test_tuic_outbound_and_full_config(self):
        s = _FakeServer(
            protocol=ProxyProtocol.TUIC, host="tuic.example.com", port=8443,
            uuid="2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365", password="tuic_password",
            server_name="tuic.example.com", congestion_control="bbr", udp_relay_mode="native",
        )
        ob = _build_singbox_tuic_outbound(s)
        self.assertEqual(ob["type"], "tuic")
        self.assertEqual(ob["server"], "tuic.example.com")
        self.assertEqual(ob["server_port"], 8443)
        self.assertEqual(ob["uuid"], "2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365")
        self.assertEqual(ob["password"], "tuic_password")
        self.assertEqual(ob["congestion_control"], "bbr")
        self.assertEqual(ob["udp_relay_mode"], "native")
        self.assertEqual(ob["tls"]["server_name"], "tuic.example.com")
        self.assertEqual(ob["tls"]["alpn"], ["h3"])

        # Full config
        cfg = _generate_config(s, 1080)
        self.assertEqual(cfg["outbounds"][0]["type"], "tuic")
        self.assertEqual(cfg["outbounds"][0]["uuid"], "2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365")
        self.assertEqual(cfg["route"]["final"], "proxy")


class SingBoxDnsServerTest(unittest.TestCase):

    def test_build_dns_server_variants(self):
        # DoH domain sets local resolver
        srv_doh = _build_singbox_dns_server("https://dns.quad9.net/dns-query", detour="proxy")
        self.assertEqual(srv_doh["type"], "https")
        self.assertEqual(srv_doh["server"], "dns.quad9.net")
        self.assertEqual(srv_doh["domain_resolver"], "local-dns")

        # DoH IP has no resolver
        srv_ip = _build_singbox_dns_server("https://1.1.1.1/dns-query", detour="proxy")
        self.assertEqual(srv_ip["type"], "https")
        self.assertEqual(srv_ip["server"], "1.1.1.1")
        self.assertNotIn("domain_resolver", srv_ip)

        # DoT server
        srv_dot = _build_singbox_dns_server("tls://1.1.1.1", detour="proxy")
        self.assertEqual(srv_dot["type"], "tls")
        self.assertEqual(srv_dot["server"], "1.1.1.1")
        self.assertEqual(srv_dot["server_port"], 853)
        self.assertEqual(srv_dot["detour"], "proxy")

        # UDP IPv6
        srv_ipv6 = _build_singbox_dns_server("2001:db8::1")
        self.assertEqual(srv_ipv6["type"], "udp")

    def test_invalid_dns_input_raises(self):
        for target in ("http://1.1.1.1", "9.9.9.9:70000", "9.9.9.9:notaport", ""):
            with self.assertRaises(ValueError, msg=target):
                _build_singbox_dns_server(target)


class SingBoxBlockUdpTest(unittest.TestCase):

    def _ss_server(self):
        return _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")

    def test_block_udp_lifecycle_and_modes(self):
        # Off by default in plain and TUN modes
        for tun in (False, True):
            cfg = _generate_config(self._ss_server(), 1080, tun_mode=tun)
            self.assertNotIn({"network": "udp", "action": "reject"}, cfg["route"]["rules"])
            self.assertNotIn("block", [o.get("tag") for o in cfg["outbounds"]])

        # Plain mode with block_udp keeps DNS first
        cfg_plain = _generate_config(self._ss_server(), 1080, block_udp=True)
        rules_plain = cfg_plain["route"]["rules"]
        self.assertEqual(rules_plain[0], {"port": 53, "action": "sniff"})
        self.assertEqual(rules_plain[1], {"protocol": "dns", "action": "route", "outbound": "proxy"})
        self.assertEqual(rules_plain[-1], {"network": "udp", "action": "reject"})

        # TUN mode keeps DNS hijack before reject
        cfg_tun = _generate_config(self._ss_server(), 1080, tun_mode=True, block_udp=True)
        rules_tun = cfg_tun["route"]["rules"]
        hijack = next(i for i, r in enumerate(rules_tun) if r.get("action") == "hijack-dns")
        reject = next(i for i, r in enumerate(rules_tun) if r.get("action") == "reject")
        self.assertLess(hijack, reject)
        self.assertEqual(rules_tun[reject], {"network": "udp", "action": "reject"})

        # Engine propagation
        engine = SingBoxEngine()
        self.assertFalse(engine.block_udp)
        engine.block_udp = True
        self.assertIn({"network": "udp", "action": "reject"}, engine.build_config(self._ss_server())["route"]["rules"])


class SingBoxTunnelOptionsTest(unittest.TestCase):

    def _ss_server(self):
        return _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")

    def _vless_server(self, flow=""):
        return _FakeServer(protocol=ProxyProtocol.VLESS, host="vless.example.com", port=443, uuid="uuid-vless", flow=flow, security="tls", transport="tcp", server_name="vless.example.com", fingerprint="chrome")

    def _vmess_server(self):
        return _FakeServer(protocol=ProxyProtocol.VMESS, host="vmess.example.com", port=443, uuid="uuid-vmess", alter_id=0, vmess_security="auto", security="tls", transport="tcp", server_name="vmess.example.com", fingerprint="chrome")

    def _trojan_server(self):
        return _FakeServer(protocol=ProxyProtocol.TROJAN, host="trojan.example.com", port=443, password="pass", transport="tcp", server_name="trojan.example.com", fingerprint="chrome")

    def _hy2_server(self):
        return _FakeServer(protocol=ProxyProtocol.HYSTERIA2, host="hy2.example.com", port=443, password="pass", server_name="hy2.example.com")

    def _tuic_server(self):
        return _FakeServer(protocol=ProxyProtocol.TUIC, host="tuic.example.com", port=8443, uuid="uuid-tuic", password="pass", server_name="tuic.example.com")

    def test_tls_fragment_options(self):
        # Off by default
        cfg_def = _generate_config(self._vless_server(), 1080)
        self.assertNotIn("fragment", cfg_def["outbounds"][0]["tls"])
        self.assertNotIn("multiplex", cfg_def["outbounds"][0])

        # Enabled on TLS outbounds
        for srv in (self._vless_server(), self._vmess_server(), self._trojan_server(), self._hy2_server(), self._tuic_server()):
            cfg = _generate_config(srv, 1080, tls_fragment=True)
            tls = cfg["outbounds"][0]["tls"]
            self.assertIs(tls["fragment"], True)
            self.assertIs(tls["record_fragment"], True)
            self.assertEqual(tls["fragment_fallback_delay"], "500ms")

        # SS has no TLS block
        cfg_ss = _generate_config(self._ss_server(), 1080, tls_fragment=True)
        self.assertNotIn("tls", cfg_ss["outbounds"][0])

    def test_mux_options_and_exclusions(self):
        expected = {"enabled": True, "protocol": "yamux", "max_connections": 8, "padding": False}
        for srv in (self._ss_server(), self._vless_server(), self._vmess_server(), self._trojan_server()):
            cfg = _generate_config(srv, 1080, mux_enabled=True, mux_protocol="yamux", mux_max_connections=8)
            self.assertEqual(cfg["outbounds"][0]["multiplex"], expected)

        # Skips vless vision flow and quic
        cfg_flow = _generate_config(self._vless_server(flow="xtls-rprx-vision"), 1080, mux_enabled=True)
        self.assertNotIn("multiplex", cfg_flow["outbounds"][0])
        for q_srv in (self._hy2_server(), self._tuic_server()):
            self.assertNotIn("multiplex", _generate_config(q_srv, 1080, mux_enabled=True)["outbounds"][0])

        # Invalid options raise ValueError
        for kw in ({"mux_protocol": "quic"}, {"mux_max_connections": 0}, {"mux_max_connections": 17}, {"mux_max_connections": "abc"}, {"mux_max_connections": True}):
            with self.assertRaises(ValueError):
                _generate_config(self._ss_server(), 1080, mux_enabled=True, **kw)

        # Engine propagation
        engine = SingBoxEngine()
        engine.tls_fragment = True
        engine.mux_enabled = True
        engine.mux_protocol = "smux"
        engine.mux_max_connections = 6
        ob = engine.build_config(self._vless_server())["outbounds"][0]
        self.assertIs(ob["tls"]["fragment"], True)
        self.assertEqual(ob["multiplex"]["protocol"], "smux")
        self.assertEqual(ob["multiplex"]["max_connections"], 6)


class SingBoxTunConfigTest(unittest.TestCase):

    def test_tun_mode_generates_tun_inbound_and_dns(self):
        server = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
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
        self.assertEqual(tun_in["route_exclude_address"], ["1.2.3.4/32", *LAN_CIDRS])

        mixed_in = config["inbounds"][1]
        self.assertEqual(mixed_in["type"], "mixed")
        self.assertEqual(mixed_in["listen_port"], 1080)
        self.assertEqual(config["route"]["default_domain_resolver"], "remote-dns")

        if sys.platform.startswith("linux"):
            rules = config["route"]["rules"]
            self.assertEqual(tun_in["interface_name"], "sb_tun0")
            self.assertTrue(tun_in["auto_redirect"])
            self.assertTrue(tun_in["strict_route"])
            self.assertEqual(rules[0], {"ip_is_private": True, "action": "bypass"})
            self.assertEqual(rules[1], {"action": "sniff"})
            self.assertEqual(rules[2], {"inbound": ["tun-in"], "protocol": "dns", "action": "hijack-dns"})
            rest = rules[3:]
        else:
            self.assertTrue(tun_in["interface_name"].startswith("socksicle-"))
            self.assertTrue(tun_in["strict_route"])
            rest = config["route"]["rules"][2:]
        self.assertEqual(rest[0]["port"], 123)
        self.assertEqual(rest[1]["ip_cidr"], ["1.2.3.4/32"])
        self.assertEqual(rest[2]["ip_is_private"], True)
        self.assertEqual(config["outbounds"][0]["domain_resolver"], "local-dns")
        self.assertEqual(config["outbounds"][1]["domain_resolver"], "local-dns")

    def test_tun_mode_windows_strict_route(self):
        server = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        with mock.patch("sys.platform", "win32"):
            config = _generate_config(server, 1080, tun_mode=True)
        tun_in = config["inbounds"][0]
        self.assertEqual(tun_in["type"], "tun")
        self.assertTrue(tun_in["strict_route"])
        self.assertTrue(tun_in["auto_route"])
        self.assertEqual(tun_in["dns_mode"], "hijack")

    def test_tun_mode_host_exclusions_and_engine_process_name(self):
        # Domain host
        s_domain = _FakeServer(protocol=ProxyProtocol.VLESS, host="vpn.example.com", port=443, uuid="11111111-1111-1111-1111-111111111111", security="reality", public_key="pbk", short_id="sid")
        cfg_domain_no_bypass = _generate_config(s_domain, 1080, tun_mode=True, bypass_lan=False)
        self.assertNotIn("route_address", cfg_domain_no_bypass["inbounds"][0])
        self.assertNotIn("route_exclude_address", cfg_domain_no_bypass["inbounds"][0])
        # Domain host with default bypass_lan: excludes LAN CIDRs
        cfg_domain = _generate_config(s_domain, 1080, tun_mode=True)
        self.assertEqual(cfg_domain["inbounds"][0]["route_exclude_address"], list(LAN_CIDRS))
        domain_rule = next(r for r in cfg_domain["route"]["rules"] if "domain" in r)
        self.assertEqual(domain_rule["domain"], ["vpn.example.com"])
        self.assertEqual(domain_rule["outbound"], "direct")

        # IPv6 host
        s_ipv6 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="2001:db8::1", port=8388, method="aes-256-gcm", password="secret")
        cfg_ipv6_no_bypass = _generate_config(s_ipv6, 1080, tun_mode=True, bypass_lan=False)
        self.assertEqual(cfg_ipv6_no_bypass["inbounds"][0]["route_exclude_address"], ["2001:db8::1/128"])
        cfg_ipv6 = _generate_config(s_ipv6, 1080, tun_mode=True)
        self.assertEqual(cfg_ipv6["inbounds"][0]["route_exclude_address"], ["2001:db8::1/128", *LAN_CIDRS])
        ip_rule = next(r for r in cfg_ipv6["route"]["rules"] if "ip_cidr" in r)
        self.assertEqual(ip_rule["ip_cidr"], ["2001:db8::1/128"])
        self.assertEqual(ip_rule["outbound"], "direct")

        # Process name with TUN
        engine = SingBoxEngine()
        self.assertEqual(engine.process_name(), "sing-box")
        engine.tun_mode = True
        self.assertEqual(engine.process_name(), "sing-box (TUN)")

    def test_multiserver_selector_config(self):
        s1 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.1.1.1", port=8388, password="pass", method="aes-256-gcm", key="ss-1")
        s2 = _FakeServer(protocol=ProxyProtocol.VLESS, host="2.2.2.2", port=443, uuid="11111111-1111-1111-1111-111111111111", flow="", security="none", transport="tcp", server_name="", fingerprint="", public_key="", short_id="", path="", host_header="", key="vless-2")
        s3 = _FakeServer(protocol=ProxyProtocol.HYSTERIA2, host="hy.example.com", port=443, password="pass", ports="", up_mbps=0, down_mbps=0, obfs="", obfs_password="", server_name="hy.example.com", insecure=False, key="hy-3")

        tag_map = {}
        cfg = _generate_config(server=s2, local_port=1080, tun_mode=True, available_servers=[s1, s2, s3], api_port=9098, api_secret="secret_abc", server_tag_map_out=tag_map)

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

        tun_in = cfg["inbounds"][0]
        self.assertIn("1.1.1.1/32", tun_in["route_exclude_address"])
        self.assertIn("2.2.2.2/32", tun_in["route_exclude_address"])

    def test_duplicate_host_deduplication(self):
        s1 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="shared.example.com", port=8381, password="p", method="aes-256-gcm", key="s1")
        s2 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="shared.example.com", port=8382, password="p", method="aes-256-gcm", key="s2")
        s3 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="shared.example.com", port=8383, password="p", method="aes-256-gcm", key="s3")
        cfg = _generate_config(server=s1, local_port=1080, available_servers=[s1, s2, s3])
        domain_rules = [r for r in cfg["route"]["rules"] if "domain" in r]
        self.assertEqual(len(domain_rules), 1)
        self.assertEqual(domain_rules[0]["domain"], ["shared.example.com"])

    def test_tun_mode_xhttp_xray_bridge_and_proxy_ip_block(self):
        s_xh = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="172.66.146.49", port=443,
            uuid="11111111-1111-1111-1111-111111111111", transport="xhttp",
            xhttp_mode="packet-up", alpn=["h3"], security="tls", server_name="xxx.clodtm.shop"
        )
        # Without xray_port, raises ValueError
        with self.assertRaises(ValueError):
            _generate_config(s_xh, 1080, tun_mode=True)

        # With xray_port, generates socks outbound, route_exclude_address, and reject rule on proxy IP
        cfg = _generate_config(s_xh, 1080, tun_mode=True, xray_port=10808)
        outbound = cfg["outbounds"][0]
        self.assertEqual(outbound["type"], "socks")
        self.assertEqual(outbound["server"], "127.0.0.1")
        self.assertEqual(outbound["server_port"], 10808)

        # TUN inbound excludes proxy IP
        self.assertIn("172.66.146.49/32", cfg["inbounds"][0]["route_exclude_address"])

        # Route rules block proxy IP inside TUN
        reject_rule = next(r for r in cfg["route"]["rules"] if r.get("action") == "reject" and "ip_cidr" in r)
        self.assertEqual(reject_rule["ip_cidr"], ["172.66.146.49/32"])

    def test_tun_mode_bypass_lan_toggle(self):
        server = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        # bypass_lan=True (default)
        cfg_on = _generate_config(server, 1080, tun_mode=True, bypass_lan=True)
        self.assertIn("1.2.3.4/32", cfg_on["inbounds"][0]["route_exclude_address"])
        for cidr in LAN_CIDRS:
            self.assertIn(cidr, cfg_on["inbounds"][0]["route_exclude_address"])
        self.assertTrue(any(r.get("ip_is_private") for r in cfg_on["route"]["rules"]))

        # bypass_lan=False
        cfg_off = _generate_config(server, 1080, tun_mode=True, bypass_lan=False)
        self.assertEqual(cfg_off["inbounds"][0]["route_exclude_address"], ["1.2.3.4/32"])
        self.assertFalse(any(r.get("ip_is_private") for r in cfg_off["route"]["rules"]))

        # Engine attribute propagation
        engine = SingBoxEngine()
        self.assertTrue(engine.bypass_lan)
        engine.bypass_lan = False
        engine.tun_mode = True
        cfg_engine = engine.build_config(server)
        self.assertEqual(cfg_engine["inbounds"][0]["route_exclude_address"], ["1.2.3.4/32"])
        self.assertFalse(any(r.get("ip_is_private") for r in cfg_engine["route"]["rules"]))

    def test_singbox_engine_xray_bridge_lifecycle(self):
        engine = SingBoxEngine()
        s_xh = _FakeServer(protocol=ProxyProtocol.VLESS, host="172.66.146.49", port=443, transport="xhttp")
        s_tcp = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.1.1.1", port=8388, transport="tcp")
        self.assertTrue(engine._needs_xray_bridge(s_xh))
        self.assertFalse(engine._needs_xray_bridge(s_tcp))
        engine.available_servers = [s_xh, s_tcp]
        self.assertFalse(engine._needs_xray_bridge(s_tcp))
        self.assertTrue(engine._needs_xray_bridge(s_xh))
        self.assertFalse(engine.can_switch_server(s_xh))

    def test_selector_excludes_xhttp_candidates(self):
        s_tcp1 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.1.1.1", port=8388, method="aes-256-gcm", password="pass", key="s1")
        s_xh = _FakeServer(protocol=ProxyProtocol.VLESS, host="2.2.2.2", port=443, transport="xhttp", key="s2")
        s_tcp2 = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="3.3.3.3", port=8388, method="aes-256-gcm", password="pass", key="s3")

        tag_map = {}
        cfg = _generate_config(
            s_tcp1, 1080, available_servers=[s_tcp1, s_xh, s_tcp2],
            api_port=9099, api_secret="sec", server_tag_map_out=tag_map
        )
        # s_xh must not be in tag_map or selector
        self.assertNotIn("s2", tag_map)
        self.assertIn("s1", tag_map)
        self.assertIn("s3", tag_map)
        selector = next(o for o in cfg["outbounds"] if o.get("type") == "selector")
        self.assertEqual(selector["outbounds"], ["node_0", "node_1"])

    def test_singbox_start_cleans_previous_session_before_xray_bridge(self):
        from unittest.mock import MagicMock, patch

        engine = SingBoxEngine()
        engine.is_connected = True
        engine.process = MagicMock()
        s_xh = _FakeServer(protocol=ProxyProtocol.VLESS, host="2.2.2.2", port=443, transport="xhttp")

        order_of_calls = []

        def fake_disconnect():
            order_of_calls.append("disconnect")
            engine.is_connected = False
            engine.process = None

        def fake_start_xray(server):
            order_of_calls.append("start_xray")
            engine._xray_port = 12345
            return True

        def fake_super_start(server):
            order_of_calls.append("super_start")
            return True

        with patch.object(engine, "disconnect_from_server", side_effect=fake_disconnect), \
             patch.object(engine, "_start_xray_bridge", side_effect=fake_start_xray), \
             patch.object(SingBoxEngine.__bases__[0], "start", side_effect=fake_super_start):
            ok = engine.start(s_xh)

        self.assertTrue(ok)
        self.assertEqual(order_of_calls, ["disconnect", "start_xray", "super_start"])
        self.assertEqual(engine._xray_port, 12345)

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

        self.assertFalse(engine.can_switch_server(s2))
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        engine.process = mock_proc

        self.assertTrue(engine.can_switch_server(s2))
        s_unknown = _FakeServer(key="unknown")
        self.assertFalse(engine.can_switch_server(s_unknown))

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

    def _ss(self, key, host, port=8388):
        return _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host=host, port=port, password="pass", method="aes-256-gcm", key=key, name=key)

    def _broken(self, key):
        return _FakeServer(protocol="no-such-proto", host="broken.invalid", port=1, key=key, name=key)

    def test_selector_active_server_rules(self):
        # Invalid active server raises
        for bad in (self._broken("active"), self._ss("active", "9.9.9.9", port=None)):
            with self.assertRaises(ValueError):
                _generate_config(bad, 1080, available_servers=[self._ss("bg", "1.1.1.1")])

        # Broken background node is skipped
        active = self._ss("active", "9.9.9.9")
        cfg = _generate_config(active, 1080, available_servers=[active, self._broken("bg")])
        self.assertEqual(cfg["outbounds"][0]["server"], "9.9.9.9")

        # Selector default is active server
        bg = self._ss("bg", "1.1.1.1")
        active2 = self._ss("active", "2.2.2.2")
        tag_map = {}
        cfg2 = _generate_config(active2, 1080, available_servers=[bg, active2], server_tag_map_out=tag_map)
        selector = cfg2["outbounds"][0]
        self.assertEqual(selector["type"], "selector")
        self.assertEqual(selector["default"], tag_map["active"])
        self.assertEqual(selector["default"], "node_1")


if __name__ == "__main__":
    unittest.main()
