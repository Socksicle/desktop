"""Tests for Hysteria 2 protocol link parsing and sing-box config generation."""
import json
import unittest

import pytest

from utils.server_model import Server, ProxyProtocol
from utils.link_parser import parse_link, _parse_hysteria2
from utils.engines.singbox_engine import SingBoxEngine
from utils.engines.singbox_config import _generate_config
from utils.engines.singbox_outbounds import _build_singbox_hysteria2_outbound


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class TestHysteria2LinkParser(unittest.TestCase):

    def test_parse_basic_hysteria2(self):
        link = "hysteria2://secret123@hy2.example.com:443#MyHy2Server"
        server = parse_link(link)
        self.assertIsNotNone(server)
        self.assertEqual(server.protocol, ProxyProtocol.HYSTERIA2)
        self.assertEqual(server.host, "hy2.example.com")
        self.assertEqual(server.port, 443)
        self.assertEqual(server.password, "secret123")
        self.assertEqual(server.name, "MyHy2Server")

    def test_parse_hy2_short_scheme(self):
        link = "hy2://my-password@198.51.100.1:8443#ShortScheme"
        server = parse_link(link)
        self.assertIsNotNone(server)
        self.assertEqual(server.protocol, ProxyProtocol.HYSTERIA2)
        self.assertEqual(server.host, "198.51.100.1")
        self.assertEqual(server.port, 8443)
        self.assertEqual(server.password, "my-password")
        self.assertEqual(server.name, "ShortScheme")

    def test_parse_with_all_parameters(self):
        link = (
            "hysteria2://p%40ssw0rd@hy2.node.com:443"
            "?sni=custom.sni.com&insecure=1&obfs=salamander&obfs-password=obfspass"
            "&mport=443,10000-20000&up=50mbps&down=100mbps#FullFeatured"
        )
        server = parse_link(link)
        self.assertIsNotNone(server)
        self.assertEqual(server.protocol, ProxyProtocol.HYSTERIA2)
        self.assertEqual(server.password, "p@ssw0rd")
        self.assertEqual(server.server_name, "custom.sni.com")
        self.assertTrue(server.insecure)
        self.assertEqual(server.obfs, "salamander")
        self.assertEqual(server.obfs_password, "obfspass")
        self.assertEqual(server.ports, "443,10000-20000")
        self.assertEqual(server.up_mbps, 50)
        self.assertEqual(server.down_mbps, 100)
        self.assertEqual(server.name, "FullFeatured")

    def test_parse_ipv6_host(self):
        link = "hysteria2://pass@[2001:db8::1]:8443?sni=ipv6.com#IPv6Server"
        server = parse_link(link)
        self.assertIsNotNone(server)
        self.assertEqual(server.host, "2001:db8::1")
        self.assertEqual(server.port, 8443)

    def test_parse_no_password(self):
        link = "hy2://node.example.com:443#NoPass"
        server = parse_link(link)
        self.assertIsNotNone(server)
        self.assertEqual(server.password, "")
        self.assertEqual(server.host, "node.example.com")
        self.assertEqual(server.port, 443)

    def test_parse_auth_query_param(self):
        link = "hy2://node.example.com:443?auth=secret-pass&peer=custom.peer.com#AuthParam"
        server = parse_link(link)
        self.assertIsNotNone(server)
        self.assertEqual(server.password, "secret-pass")
        self.assertEqual(server.server_name, "custom.peer.com")
        self.assertEqual(server.host, "node.example.com")



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

    def test_hysteria2_outbound_basic(self):
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

    def test_hysteria2_explicit_insecure_false(self):
        link = "hy2://secret@hy2.server.com:443?insecure=0#Strict"
        server = parse_link(link)
        self.assertFalse(server.insecure)
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertNotIn("insecure", ob["tls"])

    def test_hysteria2_outbound_with_obfs_and_ports(self):
        server = Server(
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
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertEqual(ob["type"], "hysteria2")
        self.assertEqual(ob["server_ports"], ["443:443", "20000:30000"])
        self.assertEqual(ob["up_mbps"], 100)
        self.assertEqual(ob["down_mbps"], 200)
        self.assertEqual(ob["obfs"], {"type": "salamander", "password": "obfspassword123"})
        self.assertTrue(ob["tls"]["insecure"])

    def test_hysteria2_server_ports_accepts_colon_ranges(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="pw",
            ports="10000:20000,443",
        )
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertEqual(ob["server_ports"], ["10000:20000", "443:443"])

    def test_hysteria2_invalid_server_ports_raise(self):
        for ports in ("abc", "10000-", "-10000", "70000", "20000-10000", "1-2-3"):
            server = Server(
                protocol=ProxyProtocol.HYSTERIA2,
                host="hy2.server.com",
                port=443,
                password="pw",
                ports=ports,
            )
            with self.assertRaises(ValueError, msg=ports):
                _build_singbox_hysteria2_outbound(server)

    def test_hysteria2_obfs_password_only_defaults_to_salamander(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="pw",
            obfs_password="obfspassword",
        )
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertEqual(ob["obfs"], {"type": "salamander", "password": "obfspassword"})

    def test_hysteria2_unknown_obfs_raises(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="pw",
            obfs="rot13",
            obfs_password="obfspassword",
        )
        with self.assertRaises(ValueError):
            _build_singbox_hysteria2_outbound(server)

    def test_generate_full_config(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="1.2.3.4",
            port=8443,
            password="auth",
        )
        cfg = _generate_config(server, 1080)
        self.assertEqual(cfg["inbounds"][0]["type"], "mixed")
        self.assertEqual(cfg["inbounds"][0]["listen_port"], 1080)
        self.assertEqual(cfg["outbounds"][0]["type"], "hysteria2")
        self.assertEqual(cfg["outbounds"][0]["server"], "1.2.3.4")
        self.assertEqual(cfg["outbounds"][0]["server_port"], 8443)
        self.assertEqual(cfg["route"]["final"], "proxy")

    def test_hysteria2_strict_by_default(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="secretpassword",
        )
        ob = _build_singbox_hysteria2_outbound(server)
        self.assertNotIn("insecure", ob["tls"])
        cfg = _generate_config(server, 1080)
        self.assertNotIn("insecure", cfg["outbounds"][0]["tls"])

    def test_hysteria2_global_opt_in_sets_insecure(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="secretpassword",
        )
        ob = _build_singbox_hysteria2_outbound(server, allow_insecure=True)
        self.assertTrue(ob["tls"]["insecure"])
        cfg = _generate_config(server, 1080, hy2_insecure=True)
        self.assertTrue(cfg["outbounds"][0]["tls"]["insecure"])

    def test_engine_build_config_propagates_hy2_insecure(self):
        server = Server(
            protocol=ProxyProtocol.HYSTERIA2,
            host="hy2.server.com",
            port=443,
            password="secretpassword",
        )
        engine = SingBoxEngine()
        engine.local_port = 1080
        self.assertFalse(engine.hy2_insecure)
        self.assertNotIn("insecure", engine.build_config(server)["outbounds"][0]["tls"])
        engine.hy2_insecure = True
        self.assertTrue(engine.build_config(server)["outbounds"][0]["tls"]["insecure"])

    def test_tls_cert_failure_hint(self):
        engine = SingBoxEngine()
        line = ("ERROR [3820420302 8.21s] dns: exchange failed for google.com. "
                "IN A: CRYPTO_ERROR 0x12a (local): tls: failed to verify "
                "certificate: x509: certificate signed by unknown authority")
        hint = engine._extra_error_hint(line)
        self.assertIsNotNone(hint)
        self.assertFalse(engine.pop_tun_adapter_conflict())
