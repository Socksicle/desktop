"""Unit tests for Xray config generation across all protocols."""
import json
import unittest
from pathlib import Path
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
    def __init__(self, **kwargs):
        self.protocol = kwargs.get("protocol", ProxyProtocol.VLESS)
        for k, v in kwargs.items():
            setattr(self, k, v)


class XrayVlessConfigTest(unittest.TestCase):

    def test_vless_outbound_variants(self):
        # Reality TCP
        s_reality = _FakeServer(
            host="1.2.3.4", port=443, uuid="uuid-r", flow="xtls-rprx-vision",
            security="reality", transport="tcp", server_name="sni.example.com",
            fingerprint="chrome", public_key="PUBKEY123", short_id="SHORT",
            path="", host_header="",
        )
        ob_r = _build_xray_vless_outbound(s_reality)
        self.assertEqual(ob_r["protocol"], "vless")
        self.assertEqual(ob_r["settings"]["vnext"][0]["address"], "1.2.3.4")
        self.assertEqual(ob_r["settings"]["vnext"][0]["port"], 443)
        user = ob_r["settings"]["vnext"][0]["users"][0]
        self.assertEqual(user["id"], "uuid-r")
        self.assertEqual(user["flow"], "xtls-rprx-vision")
        self.assertEqual(user["encryption"], "none")
        stream_r = ob_r["streamSettings"]
        self.assertEqual(stream_r["network"], "tcp")
        self.assertEqual(stream_r["security"], "reality")
        self.assertEqual(stream_r["realitySettings"]["serverName"], "sni.example.com")
        self.assertEqual(stream_r["realitySettings"]["fingerprint"], "chrome")
        self.assertEqual(stream_r["realitySettings"]["publicKey"], "PUBKEY123")
        self.assertEqual(stream_r["realitySettings"]["shortId"], "SHORT")

        # TLS WebSocket
        s_ws = _FakeServer(
            host="ws.host", port=8443, uuid="uuid-ws", flow="", security="tls",
            transport="ws", server_name="ws.example.com", fingerprint="firefox",
            public_key="", short_id="", path="/vless-ws", host_header="cdn.example.com",
        )
        ob_ws = _build_xray_vless_outbound(s_ws)
        self.assertEqual(ob_ws["streamSettings"]["security"], "tls")
        self.assertEqual(ob_ws["streamSettings"]["tlsSettings"]["serverName"], "ws.example.com")
        self.assertEqual(ob_ws["streamSettings"]["wsSettings"]["path"], "/vless-ws")
        self.assertEqual(ob_ws["streamSettings"]["wsSettings"]["headers"]["Host"], "cdn.example.com")

        # gRPC
        s_grpc = _FakeServer(
            host="grpc.host", port=443, uuid="uuid-g", flow="", security="tls",
            transport="grpc", server_name="grpc.server", fingerprint="chrome",
            public_key="", short_id="", path="my-service", host_header="",
        )
        ob_g = _build_xray_vless_outbound(s_grpc)
        self.assertEqual(ob_g["streamSettings"]["network"], "grpc")
        self.assertEqual(ob_g["streamSettings"]["grpcSettings"]["serviceName"], "my-service")

        # No encryption / none security
        s_none = _FakeServer(
            host="plain.host", port=80, uuid="uuid-plain", flow="", security="none",
            transport="tcp", server_name="", fingerprint="", public_key="", short_id="",
            path="", host_header="",
        )
        ob_none = _build_xray_vless_outbound(s_none)
        self.assertNotIn("security", ob_none["streamSettings"])
        self.assertEqual(ob_none["streamSettings"]["network"], "tcp")

        # Flow ignored on WS or none security
        s_flow_ws = _FakeServer(
            host="1.2.3.4", port=443, uuid="uuid-r", flow="xtls-rprx-vision",
            security="tls", transport="ws", server_name="sni.com", fingerprint="chrome",
        )
        self.assertNotIn("flow", _build_xray_vless_outbound(s_flow_ws)["settings"]["vnext"][0]["users"][0])
        s_flow_none = _FakeServer(
            host="1.2.3.4", port=443, uuid="uuid-r", flow="xtls-rprx-vision",
            security="none", transport="tcp", server_name="", fingerprint="",
        )
        self.assertNotIn("flow", _build_xray_vless_outbound(s_flow_none)["settings"]["vnext"][0]["users"][0])


class XrayVmessConfigTest(unittest.TestCase):

    def test_vmess_outbound_variants(self):
        # TLS
        s_tls = _FakeServer(
            host="vm.host", port=443, uuid="vm-uuid", alter_id=0, vmess_security="auto",
            security="tls", transport="tcp", server_name="vm.sni", fingerprint="chrome",
            path="", host_header="",
        )
        ob = _build_xray_vmess_outbound(s_tls)
        self.assertEqual(ob["protocol"], "vmess")
        self.assertEqual(ob["settings"]["vnext"][0]["address"], "vm.host")
        user = ob["settings"]["vnext"][0]["users"][0]
        self.assertEqual(user["id"], "vm-uuid")
        self.assertEqual(user["alterId"], 0)
        self.assertEqual(user["security"], "auto")
        self.assertEqual(ob["streamSettings"]["security"], "tls")
        self.assertEqual(ob["streamSettings"]["tlsSettings"]["serverName"], "vm.sni")

        # No TLS + WS
        s_notls = _FakeServer(
            host="vm.host", port=80, uuid="vm-uuid2", alter_id=2,
            vmess_security="chacha20-poly1305", security="none", transport="ws",
            server_name="", fingerprint="firefox", path="/vmess-ws", host_header="vm.host",
        )
        ob_notls = _build_xray_vmess_outbound(s_notls)
        self.assertNotIn("security", ob_notls["streamSettings"])
        self.assertEqual(ob_notls["streamSettings"]["network"], "ws")
        self.assertEqual(ob_notls["streamSettings"]["wsSettings"]["path"], "/vmess-ws")

        # gRPC
        s_grpc = _FakeServer(
            host="g.host", port=443, uuid="uuid-grpc", alter_id=0, vmess_security="auto",
            security="tls", transport="grpc", server_name="g.sni", fingerprint="chrome",
            path="grpc-svc", host_header="",
        )
        ob_grpc = _build_xray_vmess_outbound(s_grpc)
        self.assertEqual(ob_grpc["streamSettings"]["network"], "grpc")
        self.assertEqual(ob_grpc["streamSettings"]["grpcSettings"]["serviceName"], "grpc-svc")


class XraySSAndTrojanConfigTest(unittest.TestCase):

    def test_ss_and_trojan_outbounds(self):
        # Shadowsocks
        s_ss = _FakeServer(host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        ob_ss = _build_xray_ss_outbound(s_ss)
        self.assertEqual(ob_ss["protocol"], "shadowsocks")
        srv_ss = ob_ss["settings"]["servers"][0]
        self.assertEqual(srv_ss["address"], "1.2.3.4")
        self.assertEqual(srv_ss["port"], 8388)
        self.assertEqual(srv_ss["method"], "aes-256-gcm")
        self.assertEqual(srv_ss["password"], "secret")
        self.assertEqual(ob_ss["streamSettings"], {"network": "tcp"})

        # Trojan TLS
        s_tr = _FakeServer(
            host="trojan.example.com", port=443, password="pass", security="tls",
            transport="tcp", server_name="sni.example.com", fingerprint="chrome", path="", host_header="",
        )
        ob_tr = _build_xray_trojan_outbound(s_tr)
        self.assertEqual(ob_tr["protocol"], "trojan")
        self.assertEqual(ob_tr["settings"]["servers"][0]["address"], "trojan.example.com")
        self.assertEqual(ob_tr["streamSettings"]["security"], "tls")
        self.assertEqual(ob_tr["streamSettings"]["tlsSettings"]["serverName"], "sni.example.com")

        # Trojan None
        s_tr_none = _FakeServer(
            host="185.188.30.219", port=32087, password="pass", security="none",
            transport="tcp", server_name="", fingerprint="", path="", host_header="",
        )
        ob_tr_none = _build_xray_trojan_outbound(s_tr_none)
        self.assertNotIn("security", ob_tr_none["streamSettings"])
        self.assertEqual(ob_tr_none["streamSettings"]["network"], "tcp")


class XrayFullConfigTest(unittest.TestCase):

    def test_full_configs_by_protocol(self):
        # VLESS
        s_vless = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443, uuid="uuid-full",
            flow="xtls-rprx-vision", security="reality", transport="tcp",
            server_name="sni.com", fingerprint="chrome", public_key="PBK", short_id="SID",
            path="", host_header="",
        )
        cfg_vless = _generate_config(s_vless, 1080)
        self.assertIn("log", cfg_vless)
        self.assertIn("inbounds", cfg_vless)
        self.assertNotIn("api", cfg_vless)
        self.assertEqual(cfg_vless["inbounds"][0]["protocol"], "socks")
        self.assertEqual(cfg_vless["inbounds"][0]["port"], 1080)
        self.assertEqual(cfg_vless["outbounds"][0]["protocol"], "vless")
        self.assertEqual(cfg_vless["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"], "uuid-full")
        self.assertEqual(cfg_vless["outbounds"][1]["protocol"], "freedom")
        self.assertEqual(cfg_vless["routing"]["domainStrategy"], "IPIfNonMatch")

        # VMess
        s_vmess = _FakeServer(
            protocol=ProxyProtocol.VMESS, host="5.6.7.8", port=443, uuid="uuid-vm-full",
            alter_id=0, vmess_security="auto", security="tls", transport="ws",
            server_name="sni.example.com", fingerprint="chrome", path="/path", host_header="host.example.com",
        )
        cfg_vmess = _generate_config(s_vmess, 1080)
        self.assertEqual(cfg_vmess["outbounds"][0]["protocol"], "vmess")
        self.assertEqual(cfg_vmess["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"], "uuid-vm-full")

        # SS
        s_ss = _FakeServer(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        self.assertEqual(_generate_config(s_ss, 1080)["outbounds"][0]["protocol"], "shadowsocks")

    def test_inbound_ports_and_uniqueness(self):
        server = _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443, uuid="uuid",
            flow="", security="none", transport="tcp", server_name="", fingerprint="",
        )
        for local_port in (1080, 10085, 1081):
            config = _generate_config(server, local_port)
            self.assertEqual(len(config["inbounds"]), 1)
            self.assertEqual(config["inbounds"][0]["port"], local_port)
            self.assertNotIn("api", config)
            self.assertNotIn("dokodemo-door", json.dumps(config))

        # String port conversion
        cfg_str = _generate_config(server, "1080")
        self.assertEqual(cfg_str["inbounds"][0]["port"], 1080)
        self.assertIsInstance(cfg_str["inbounds"][0]["port"], int)


class XrayBlockUdpTest(unittest.TestCase):

    def _vless_server(self):
        return _FakeServer(
            protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443, uuid="uuid",
            flow="", security="none", transport="tcp", server_name="", fingerprint="",
        )

    def test_block_udp_lifecycle(self):
        # Off by default
        cfg_def = _generate_config(self._vless_server(), 1080)
        self.assertEqual(cfg_def["routing"]["rules"], [])
        self.assertNotIn("blocked", [o.get("tag") for o in cfg_def["outbounds"]])

        # Blackhole and rules added (port 53 exempted before UDP block)
        cfg_block = _generate_config(self._vless_server(), 1080, block_udp=True)
        rules = cfg_block["routing"]["rules"]
        self.assertIn({"type": "field", "port": "53", "outboundTag": "proxy"}, rules)
        self.assertIn({"type": "field", "network": "udp", "outboundTag": "blocked"}, rules)
        dns_idx = next(i for i, r in enumerate(rules) if r.get("port") == "53")
        block_idx = next(i for i, r in enumerate(rules) if r.get("outboundTag") == "blocked")
        self.assertLess(dns_idx, block_idx)
        blocked = next(o for o in cfg_block["outbounds"] if o.get("tag") == "blocked")
        self.assertEqual(blocked["protocol"], "blackhole")

        # Engine propagation
        engine = XrayEngine()
        self.assertFalse(engine.block_udp)
        engine.block_udp = True
        engine_rules = engine.build_config(self._vless_server())["routing"]["rules"]
        self.assertIn({"type": "field", "port": "53", "outboundTag": "proxy"}, engine_rules)
        self.assertIn({"type": "field", "network": "udp", "outboundTag": "blocked"}, engine_rules)


class XrayStreamSettingsTest(unittest.TestCase):

    def test_security_variants(self):
        # Reality TCP, gRPC, xHTTP, spider_x
        s_r = _build_xray_stream_settings(_FakeServer(
            security="reality", transport="tcp", server_name="sni.com",
            fingerprint="chrome", public_key="PBK", short_id="SID", path="", host_header="",
        ))
        self.assertEqual(s_r["security"], "reality")
        self.assertEqual(s_r["realitySettings"]["serverName"], "sni.com")
        self.assertEqual(s_r["realitySettings"]["publicKey"], "PBK")

        s_r_grpc = _build_xray_stream_settings(_FakeServer(
            security="reality", transport="grpc", server_name="sni.com",
            fingerprint="chrome", public_key="PBK", short_id="SID", path="svc", host_header="",
        ))
        self.assertEqual(s_r_grpc["grpcSettings"]["serviceName"], "svc")

        s_r_spx = _build_xray_stream_settings(_FakeServer(
            security="reality", transport="tcp", server_name="sni.com",
            fingerprint="chrome", public_key="PBK", short_id="SID", spider_x="/spx", path="", host_header="",
        ))
        self.assertEqual(s_r_spx["realitySettings"]["spiderX"], "/spx")

        # TLS variants & pinned cert
        s_tls = _build_xray_stream_settings(_FakeServer(
            security="tls", transport="tcp", server_name="sni.com", fingerprint="firefox", path="", host_header="",
        ))
        self.assertEqual(s_tls["security"], "tls")
        self.assertEqual(s_tls["tlsSettings"]["serverName"], "sni.com")

        s_pin = _build_xray_stream_settings(_FakeServer(
            security="tls", transport="tcp", server_name="sni.com", fingerprint="chrome",
            pinned_peer_cert_sha256="08:1E:1A:29", verify_peer_cert_by_name="peer.example.com", path="", host_header="",
        ))
        self.assertEqual(s_pin["tlsSettings"]["pinnedPeerCertSha256"], "08:1E:1A:29")
        self.assertEqual(s_pin["tlsSettings"]["verifyPeerCertByName"], "peer.example.com")

        # Insecure does not emit allowInsecure
        s_insec = _build_xray_stream_settings(_FakeServer(
            security="tls", transport="tcp", server_name="sni.com", fingerprint="firefox", insecure=True, path="", host_header="",
        ))
        self.assertNotIn("allowInsecure", s_insec.get("tlsSettings", {}))

        # None security
        s_none = _build_xray_stream_settings(_FakeServer(security="none", transport="tcp", server_name="", fingerprint="", path="", host_header=""))
        self.assertNotIn("security", s_none)
        self.assertNotIn("tlsSettings", s_none)

    def test_transport_variants(self):
        # WS with & without host
        s_ws1 = _build_xray_stream_settings(_FakeServer(security="none", transport="ws", server_name="", fingerprint="", path="/ws", host_header="cdn.com"))
        self.assertEqual(s_ws1["wsSettings"]["path"], "/ws")
        self.assertEqual(s_ws1["wsSettings"]["headers"]["Host"], "cdn.com")

        s_ws2 = _build_xray_stream_settings(_FakeServer(security="none", transport="ws", server_name="", fingerprint="", path="/ws", host_header=""))
        self.assertNotIn("headers", s_ws2["wsSettings"])

        # gRPC with & without service
        s_g1 = _build_xray_stream_settings(_FakeServer(security="none", transport="grpc", server_name="", fingerprint="", path="svc", host_header=""))
        self.assertEqual(s_g1["grpcSettings"]["serviceName"], "svc")
        s_g2 = _build_xray_stream_settings(_FakeServer(security="none", transport="grpc", server_name="", fingerprint="", path="", host_header=""))
        self.assertNotIn("serviceName", s_g2["grpcSettings"])

        # xHTTP
        s_xh = _build_xray_stream_settings(_FakeServer(security="none", transport="xhttp", server_name="", fingerprint="", path="/xh", host_header="xh.host"))
        self.assertEqual(s_xh["xhttpSettings"]["path"], "/xh")
        self.assertEqual(s_xh["xhttpSettings"]["mode"], "auto")

        s_xh_mode = _build_xray_stream_settings(_FakeServer(security="none", transport="xhttp", server_name="", fingerprint="", path="/xh", host_header="", xhttp_mode="stream-up"))
        self.assertEqual(s_xh_mode["xhttpSettings"]["mode"], "stream-up")

        s_xh_h3 = _build_xray_stream_settings(_FakeServer(
            security="tls", transport="xhttp", server_name="sni.com",
            fingerprint="chrome", path="/xh", host_header="sni.com",
            xhttp_mode="packet-up", alpn=["h3"]
        ))
        self.assertEqual(s_xh_h3["xhttpSettings"]["mode"], "packet-up")
        self.assertEqual(s_xh_h3["tlsSettings"]["alpn"], ["h3"])

        with self.assertRaises(ValueError):
            _build_xray_stream_settings(_FakeServer(security="none", transport="xhttp", server_name="", fingerprint="", path="/xh", host_header="", xhttp_mode="invalid-mode"))

        # TCP & defaults
        s_tcp = _build_xray_stream_settings(_FakeServer(security="none", transport="tcp", server_name="", fingerprint="", path="", host_header=""))
        self.assertEqual(s_tcp["network"], "tcp")
        self.assertEqual(_build_xray_stream_settings(_FakeServer(transport="tcp"))["network"], "tcp")

        # kcp & httpupgrade
        s_kcp = _build_xray_stream_settings(_FakeServer(security="none", transport="kcp", server_name="", fingerprint="", path="", host_header="", header_type="srtp", seed="kcp-seed"))
        self.assertEqual(s_kcp["kcpSettings"]["header"]["type"], "srtp")
        self.assertEqual(s_kcp["kcpSettings"]["seed"], "kcp-seed")

        s_hu = _build_xray_stream_settings(_FakeServer(security="none", transport="httpupgrade", server_name="", fingerprint="", path="/hu", host_header="hu.host"))
        self.assertEqual(s_hu["httpupgradeSettings"]["path"], "/hu")

        # Removed transports raise error
        for bad in ("http", "h2", "h3", "quic"):
            with self.assertRaises(ValueError):
                _build_xray_stream_settings(_FakeServer(security="none", transport=bad, server_name="", fingerprint="", path="", host_header=""))


class XrayDnsAndProtocolTest(unittest.TestCase):

    def _make_server(self, proto=ProxyProtocol.VLESS):
        return _FakeServer(protocol=proto, host="1.2.3.4", port=443, uuid="u-1", flow="", security="none", transport="tcp", server_name="", fingerprint="")

    def test_dns_and_unsupported_protocols(self):
        # HTTPS, UDP stripped, TCP
        cfg_doh = _generate_config(self._make_server(), 1080, custom_dns="https://1.1.1.1/dns-query")
        self.assertEqual(cfg_doh["dns"]["servers"], ["https://1.1.1.1/dns-query", "localhost"])

        cfg_udp = _generate_config(self._make_server(), 1080, custom_dns="udp://8.8.8.8")
        self.assertEqual(cfg_udp["dns"]["servers"], ["8.8.8.8", "localhost"])

        cfg_tcp = _generate_config(self._make_server(), 1080, custom_dns="tcp://8.8.8.8")
        self.assertEqual(cfg_tcp["dns"]["servers"], ["tcp://8.8.8.8", "localhost"])

        for val in (None, "", "   ", "default", "DEFAULT"):
            self.assertNotIn("dns", _generate_config(self._make_server(), 1080, custom_dns=val))

        with self.assertRaises(ValueError):
            _generate_config(self._make_server(), 1080, custom_dns="tls://1.1.1.1")

        with self.assertRaises(ValueError):
            _generate_config(self._make_server(proto="unsupported_proto"), 1080)


class XrayEngineClassTest(unittest.TestCase):

    def test_xray_engine_properties_and_configs(self):
        engine = XrayEngine()
        engine.local_port = 1080

        # Build config vless & vmess
        s_vless = _FakeServer(protocol=ProxyProtocol.VLESS, host="1.2.3.4", port=443, uuid="uuid", flow="", security="none", transport="tcp", server_name="", fingerprint="", public_key="", short_id="")
        self.assertEqual(engine.build_config(s_vless)["outbounds"][0]["protocol"], "vless")

        s_vmess = _FakeServer(protocol=ProxyProtocol.VMESS, host="1.2.3.4", port=443, uuid="uuid", alter_id=0, vmess_security="auto", security="tls", transport="tcp", server_name="sni", fingerprint="chrome")
        self.assertEqual(engine.build_config(s_vmess)["outbounds"][0]["protocol"], "vmess")

        # Build args creates valid config file
        args = engine.build_args(s_vless)
        config_path = Path(args[2])
        self.assertTrue(config_path.exists())
        with open(config_path) as f:
            self.assertEqual(json.load(f)["outbounds"][0]["protocol"], "vless")
        config_path.unlink(missing_ok=True)

        # Version & process name
        self.assertEqual(engine.version_args(), ["version"])
        self.assertEqual(engine.process_name(), "xray")


if __name__ == "__main__":
    unittest.main()
