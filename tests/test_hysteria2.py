"""Tests for Hysteria 2 protocol link parsing and sing-box config generation."""
import unittest
import pytest

from utils.server_model import Server, ProxyProtocol
from utils.link_parser import parse_link
from utils.engines.singbox_engine import SingBoxEngine
from utils.engines.singbox_config import _generate_config
from utils.engines.singbox_outbounds import _build_singbox_hysteria2_outbound


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class TestHysteria2LinkParser(unittest.TestCase):

    def test_parse_hysteria2_variants(self):
        cases = [
            (
                "hysteria2://secret123@hy2.example.com:443#MyHy2Server",
                {"protocol": ProxyProtocol.HYSTERIA2, "host": "hy2.example.com", "port": 443, "password": "secret123", "name": "MyHy2Server"},
            ),
            (
                "hy2://my-password@198.51.100.1:8443#ShortScheme",
                {"protocol": ProxyProtocol.HYSTERIA2, "host": "198.51.100.1", "port": 8443, "password": "my-password", "name": "ShortScheme"},
            ),
            (
                "hysteria2://p%40ssw0rd@hy2.node.com:443?sni=custom.sni.com&insecure=1&obfs=salamander&obfs-password=obfspass&mport=443,10000-20000&up=50mbps&down=100mbps#FullFeatured",
                {"protocol": ProxyProtocol.HYSTERIA2, "host": "hy2.node.com", "port": 443, "password": "p@ssw0rd", "server_name": "custom.sni.com", "insecure": True, "obfs": "salamander", "obfs_password": "obfspass", "ports": "443,10000-20000", "up_mbps": 50, "down_mbps": 100, "name": "FullFeatured"},
            ),
            (
                "hysteria2://pass@[2001:db8::1]:8443?sni=ipv6.com#IPv6Server",
                {"protocol": ProxyProtocol.HYSTERIA2, "host": "2001:db8::1", "port": 8443, "password": "pass", "server_name": "ipv6.com", "name": "IPv6Server"},
            ),
            (
                "hy2://node.example.com:443#NoPass",
                {"protocol": ProxyProtocol.HYSTERIA2, "host": "node.example.com", "port": 443, "password": "", "name": "NoPass"},
            ),
            (
                "hy2://node.example.com:443?auth=secret-pass&peer=custom.peer.com#AuthParam",
                {"protocol": ProxyProtocol.HYSTERIA2, "host": "node.example.com", "port": 443, "password": "secret-pass", "server_name": "custom.peer.com", "name": "AuthParam"},
            ),
        ]
        for link, expected in cases:
            srv = parse_link(link)
            self.assertIsNotNone(srv, link)
            for attr, val in expected.items():
                self.assertEqual(getattr(srv, attr), val, f"{attr} mismatch for {link}")

    def test_server_from_dict_and_to_dict(self):
        s1 = Server(
            key="hy2://pass@host:443#Node",
            name="Node",
            host="host",
            port=443,
            protocol=ProxyProtocol.HYSTERIA2,
            password="pass",
            server_name="sni.host",
            insecure=True,
            obfs="salamander",
            obfs_password="obfspassword",
            ports="10000-20000",
            up_mbps=30,
            down_mbps=60,
        )
        d = s1.to_dict()
        self.assertEqual(d["protocol"], "hysteria2")
        self.assertTrue(d["insecure"])
        self.assertEqual(d["obfs"], "salamander")
        self.assertEqual(d["obfs_password"], "obfspassword")
        self.assertEqual(d["ports"], "10000-20000")
        self.assertEqual(d["up_mbps"], 30)
        self.assertEqual(d["down_mbps"], 60)

        s2 = Server.from_dict(d)
        self.assertEqual(s2.protocol, ProxyProtocol.HYSTERIA2)
        self.assertEqual(s2.host, "host")
        self.assertEqual(s2.password, "pass")
        self.assertTrue(s2.insecure)
        self.assertEqual(s2.obfs, "salamander")
        self.assertEqual(s2.obfs_password, "obfspassword")
        self.assertEqual(s2.ports, "10000-20000")
        self.assertEqual(s2.up_mbps, 30)
        self.assertEqual(s2.down_mbps, 60)
        self.assertEqual(s2.unique_key, "hysteria2:pass@host:443")
        self.assertEqual(s2.display_protocol, "HYSTERIA2")


class TestSingBoxHysteria2Config(unittest.TestCase):

    def test_hysteria2_outbound_variants(self):
        # Basic with insecure=True
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="secretpassword",
            server_name="sni.server.com",
            insecure=True,
        )
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertEqual(ob["type"], "hysteria2")
        self.assertEqual(ob["tag"], "proxy")
        self.assertEqual(ob["server"], "hy2.server.com")
        self.assertEqual(ob["server_port"], 443)
        self.assertEqual(ob["password"], "secretpassword")
        self.assertEqual(ob["tls"]["server_name"], "sni.server.com")
        self.assertTrue(ob["tls"]["insecure"])
        self.assertNotIn("obfs", ob)
        self.assertNotIn("server_ports", ob)

        # Insecure=False
        link = "hy2://secret@hy2.server.com:443?insecure=0#Strict"
        server_strict = parse_link(link)
        self.assertFalse(server_strict.insecure)
        ob_strict = _build_singbox_hysteria2_outbound(server_strict)
        self.assertNotIn("insecure", ob_strict["tls"])

        # A certificate pin must not disable TLS verification
        server_pinned = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="secretpassword",
            pinned_peer_cert_sha256="08:1E:1A",
        )
        ob_pinned = _build_singbox_hysteria2_outbound(server_pinned)
        self.assertNotIn("insecure", ob_pinned["tls"])

        # With obfs and port ranges
        server_full = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="secretpassword",
            server_name="sni.server.com",
            insecure=True,
            obfs="salamander",
            obfs_password="obfspassword123",
            ports="443,20000-30000",
            up_mbps=100,
            down_mbps=200,
        )
        ob_full = _build_singbox_hysteria2_outbound(server_full)
        self.assertEqual(ob_full["server_ports"], ["443:443", "20000:30000"])
        self.assertEqual(ob_full["up_mbps"], 100)
        self.assertEqual(ob_full["down_mbps"], 200)
        self.assertEqual(ob_full["obfs"], {"type": "salamander", "password": "obfspassword123"})
        self.assertTrue(ob_full["tls"]["insecure"])

    def test_hysteria2_server_ports_validation(self):
        # Valid port ranges
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="pw",
            ports="10000:20000,443",
        )
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertEqual(ob["server_ports"], ["10000:20000", "443:443"])

        # Invalid port formats raise ValueError
        for ports in ("abc", "10000-", "-10000", "70000", "20000-10000", "1-2-3"):
            srv = Server(protocol=ProxyProtocol.HYSTERIA2, host="hy2.server.com", port=443, password="pw", ports=ports)
            with self.assertRaises(ValueError, msg=ports):
                _build_singbox_hysteria2_outbound(srv)

    def test_hysteria2_obfs_validation(self):
        # Password only defaults to salamander
        server = Server(protocol=ProxyProtocol.HYSTERIA2, host="hy2.server.com", port=443, password="pw", obfs_password="obfspassword")
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertEqual(ob["obfs"], {"type": "salamander", "password": "obfspassword"})

        # Unknown obfs type raises ValueError
        server_bad = Server(protocol=ProxyProtocol.HYSTERIA2, host="hy2.server.com", port=443, password="pw", obfs="rot13", obfs_password="obfspassword")
        with self.assertRaises(ValueError):
            _build_singbox_hysteria2_outbound(server_bad)

    def test_generate_full_config_and_strict_defaults(self):
        server = Server(protocol=ProxyProtocol.HYSTERIA2, host="1.2.3.4", port=8443, password="auth")
        cfg = _generate_config(server, 1080)
        self.assertEqual(cfg["inbounds"][0]["type"], "mixed")
        self.assertEqual(cfg["inbounds"][0]["listen_port"], 1080)
        self.assertEqual(cfg["outbounds"][0]["type"], "hysteria2")
        self.assertEqual(cfg["outbounds"][0]["server"], "1.2.3.4")
        self.assertEqual(cfg["outbounds"][0]["server_port"], 8443)
        self.assertEqual(cfg["route"]["final"], "proxy")
        # Strict by default: no insecure flag
        self.assertNotIn("insecure", cfg["outbounds"][0]["tls"])

        # Opt-in insecure
        cfg_insecure = _generate_config(server, 1080, hy2_insecure=True)
        self.assertTrue(cfg_insecure["outbounds"][0]["tls"]["insecure"])

    def test_engine_propagation_and_cert_hint(self):
        server = Server(protocol=ProxyProtocol.HYSTERIA2, host="hy2.server.com", port=443, password="secretpassword")
        engine = SingBoxEngine()
        engine.local_port = 1080
        self.assertFalse(engine.hy2_insecure)
        self.assertNotIn("insecure", engine.build_config(server)["outbounds"][0]["tls"])
        engine.hy2_insecure = True
        self.assertTrue(engine.build_config(server)["outbounds"][0]["tls"]["insecure"])

        line = ("ERROR [3820420302 8.21s] dns: exchange failed for google.com. "
                "IN A: CRYPTO_ERROR 0x12a (local): tls: failed to verify "
                "certificate: x509: certificate signed by unknown authority")
        hint = engine._extra_error_hint(line)
        self.assertIsNotNone(hint)
        self.assertFalse(engine.pop_tun_adapter_conflict())
