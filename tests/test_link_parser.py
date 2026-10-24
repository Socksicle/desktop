"""Unit tests for vless:// and vmess:// link parsing."""
import base64
import json
from unittest import mock
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.link_parser import parse_link, _parse_vless, _parse_vmess
from utils.server_model import Server, ProxyProtocol


def _b64_encode(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


def _b64_encode_json(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')


# --- VLESS link tests ---

class TestParseVlessLink:
    def test_vless_link_parsing(self):
        # 1. Reality
        link_reality = (
            "vless://abc123-uuid@1.2.3.4:443"
            "?security=reality&type=tcp&sni=example.com"
            "&fp=chrome&pbk=PUBLIC_KEY&sid=SHORT_ID"
            "&flow=xtls-rprx-vision#My%20Server"
        )
        s = parse_link(link_reality)
        assert s is not None
        assert s.protocol == ProxyProtocol.VLESS
        assert s.uuid == "abc123-uuid"
        assert s.host == "1.2.3.4"
        assert s.port == 443
        assert s.security == "reality"
        assert s.transport == "tcp"
        assert s.server_name == "example.com"
        assert s.fingerprint == "chrome"
        assert s.public_key == "PUBLIC_KEY"
        assert s.short_id == "SHORT_ID"
        assert s.flow == "xtls-rprx-vision"
        assert s.name == "My Server"

        # 2. TLS WS
        link_ws = (
            "vless://uuid-tls@host.com:8443"
            "?security=tls&type=ws&path=/ws"
            "&host=cdn.example.com&sni=cdn.example.com"
            "#TLS%20WS"
        )
        s_ws = parse_link(link_ws)
        assert s_ws is not None
        assert s_ws.security == "tls"
        assert s_ws.transport == "ws"
        assert s_ws.path == "/ws"
        assert s_ws.host_header == "cdn.example.com"

        # 3. gRPC
        link_grpc = "vless://uuid@grpc.server:443?security=tls&type=grpc&serviceName=grpc-service&sni=grpc.server&fp=firefox#Grpc"
        s_grpc = parse_link(link_grpc)
        assert s_grpc.transport == "grpc"
        assert s_grpc.path == "grpc-service"

        # 4. No security
        s_nosec = parse_link("vless://uuid@1.2.3.4:1080?security=none&type=tcp#NoSec")
        assert s_nosec.security == "none"

        # 5. Pinned peer cert (PCS and pinSHA256)
        link_pcs = (
            "vless://a40247ab-6288-4b7b-8f47-1903ea90752f@185.188.30.219:506"
            "?encryption=none&security=tls&sni=www.google.com&fp=chrome"
            "&pcs=08%3A1E%3A1A%3A29%3A3E%3A9A%3AE9%3AEE%3AE8%3AF3%3AEF%3A00"
            "&pcn=test.name&type=tcp#Finland"
        )
        s_pcs = parse_link(link_pcs)
        assert s_pcs.pinned_peer_cert_sha256 == "08:1E:1A:29:3E:9A:E9:EE:E8:F3:EF:00"
        assert s_pcs.verify_peer_cert_by_name == "test.name"

        link_alias = "vless://uuid-test@65.109.190.99:507?encryption=none&security=tls&type=tcp&sni=www.google.com&pinSHA256=aabbcc#TestPin"
        assert parse_link(link_alias).pinned_peer_cert_sha256 == "aabbcc"

        # 6. XHTTP with packet-up and ALPN
        link_xhttp = (
            "vless://a40247ab-6288-4b7b-8f47-1903ea90752f@172.66.146.49:443"
            "?encryption=none&security=tls&type=xhttp&headerType=none"
            "&path=%2F59c82b693d3f60fb&host=xxx.clodtm.shop&mode=packet-up"
            "&sni=xxx.clodtm.shop&fp=chrome&alpn=h3#Finland%20CF"
        )
        s_xh = parse_link(link_xhttp)
        assert s_xh.transport == "xhttp"
        assert s_xh.xhttp_mode == "packet-up"
        assert s_xh.alpn == ["h3"]
        assert s_xh.server_name == "xxx.clodtm.shop"

        # 7. Edge cases
        assert parse_link("vless://@1.2.3.4:443?security=tls#Empty").uuid == ""
        assert parse_link("vless://uuid@1.2.3.4:443?security=tcp", default_name="Default").name == "Default"
        assert parse_link("vless://uuid@1.2.3.4?security=tls#Bad") is None
        assert parse_link("vless://") is None
        assert parse_link("vless://garbage") is None


# --- VMess link tests ---

class TestParseVmessLink:
    def test_vmess_link_parsing(self):
        # Basic TLS
        vmess_obj = {
            "v": "2", "ps": "VMess Server", "add": "5.6.7.8", "port": "443",
            "id": "uuid-vmess-123", "aid": "0", "scy": "auto", "net": "tcp",
            "tls": "tls", "sni": "sni.example.com", "fp": "chrome",
        }
        s = parse_link("vmess://" + _b64_encode_json(vmess_obj))
        assert s.protocol == ProxyProtocol.VMESS
        assert s.name == "VMess Server"
        assert s.host == "5.6.7.8"
        assert s.port == 443
        assert s.uuid == "uuid-vmess-123"
        assert s.security == "tls"

        # WS with custom transport & security
        vmess_ws = {
            "v": "2", "ps": "WS VMess", "add": "ws.host.com", "port": "80",
            "id": "uuid-ws", "aid": "2", "scy": "chacha20-poly1305",
            "net": "ws", "tls": "", "host": "ws.host.com", "path": "/vmess",
        }
        s_ws = parse_link("vmess://" + _b64_encode_json(vmess_ws))
        assert s_ws.transport == "ws"
        assert s_ws.security == "none"
        assert s_ws.host_header == "ws.host.com"
        assert s_ws.alter_id == 2

        # No PS falls back to default_name
        vmess_nops = {"v": "2", "ps": "", "add": "1.1.1.1", "port": "443", "id": "u", "aid": "0"}
        assert parse_link("vmess://" + _b64_encode_json(vmess_nops), default_name="Fallback").name == "Fallback"

        # Null port and aid
        vmess_null = {"v": "2", "ps": "Null", "add": "1.2.3.4", "port": None, "id": "u", "aid": None}
        s_null = parse_link("vmess://" + _b64_encode_json(vmess_null))
        assert s_null.port == 443
        assert s_null.alter_id == 0

        # Invalid cases
        assert parse_link("vmess://" + _b64_encode("not valid json {{{")) is None
        assert parse_link("vmess://!!!invalid!!!") is None
        assert parse_link("vmess://" + _b64_encode_json({"v": "2", "port": [1, 2, 3]})) is None
        assert parse_link("vmess://" + _b64_encode_json([1, 2, 3])) is None


# --- Backward compatibility ---

class TestFromLinkBackwardCompat:
    def test_from_link_all_protocols(self):
        # SS
        inner = _b64_encode("aes-256-gcm:testpass@1.2.3.4:8388")
        s_ss = Server.from_link(f"ss://{inner}#SS%20Test")
        assert s_ss.protocol == ProxyProtocol.SHADOWSOCKS
        assert s_ss.method == "aes-256-gcm"

        # VLESS
        assert Server.from_link("vless://uuid@1.2.3.4:443?security=tls#Vless").protocol == ProxyProtocol.VLESS

        # VMess
        vmess_obj = {"v": "2", "ps": "VM", "add": "1.2.3.4", "port": "443", "id": "u", "aid": "0"}
        assert Server.from_link("vmess://" + _b64_encode_json(vmess_obj)).protocol == ProxyProtocol.VMESS

        # None / empty
        assert Server.from_link(None) is None
        assert Server.from_link("") is None


# --- Server model round-trip tests ---

class TestServerModelRoundTrip:
    def test_server_model_serialization_and_helpers(self):
        # 1. VLESS round-trip
        s_vless = Server(
            key="vless://...", name="V", host="h", port=443,
            protocol=ProxyProtocol.VLESS, uuid="u",
            security="reality", transport="tcp", flow="xtls-rprx-vision",
            server_name="sni", fingerprint="chrome",
            public_key="pbk", short_id="sid",
            xhttp_mode="packet-up", alpn=["h3"],
        )
        d_v = s_vless.to_dict()
        assert d_v["protocol"] == "vless" and d_v["uuid"] == "u" and d_v["security"] == "reality"
        assert d_v["xhttp_mode"] == "packet-up" and d_v["alpn"] == ["h3"]
        s2_v = Server.from_dict(d_v)
        assert s2_v.protocol == ProxyProtocol.VLESS and s2_v.short_id == "sid"
        assert s2_v.xhttp_mode == "packet-up" and s2_v.alpn == ["h3"]

        # 2. VMess round-trip
        s_vmess = Server(
            key="vmess://...", name="V", host="h", port=443,
            protocol=ProxyProtocol.VMESS, uuid="u",
            alter_id=2, vmess_security="chacha20-poly1305",
            transport="ws", path="/ws", host_header="cdn.com",
        )
        d_m = s_vmess.to_dict()
        assert d_m["protocol"] == "vmess" and d_m["alter_id"] == 2
        s2_m = Server.from_dict(d_m)
        assert s2_m.alter_id == 2 and s2_m.path == "/ws"

        # 3. SS unchanged
        s_ss = Server(key="ss://...", name="S", host="h", port=443, method="aes-256-gcm", password="pw")
        d_ss = s_ss.to_dict()
        assert "protocol" not in d_ss and "uuid" not in d_ss and d_ss["method"] == "aes-256-gcm"

        # 4. Unique key
        s1 = Server(protocol=ProxyProtocol.VLESS, uuid="u", host="h", port=443)
        s2 = Server(protocol=ProxyProtocol.VLESS, uuid="u", host="h", port=443)
        s3 = Server(protocol=ProxyProtocol.VLESS, uuid="other", host="h", port=443)
        assert s1.unique_key == s2.unique_key
        assert s1.unique_key != s3.unique_key

        # 5. Display protocol
        assert Server(protocol=ProxyProtocol.VLESS).display_protocol == "VLESS"
        assert Server(protocol=ProxyProtocol.VMESS).display_protocol == "VMESS"
        assert Server(protocol=ProxyProtocol.SHADOWSOCKS).display_protocol == "SHADOWSOCKS"

        # 6. Pinned cert roundtrip
        s_cert = Server(
            key="vless://...", name="V", host="h", port=443,
            protocol=ProxyProtocol.VLESS, uuid="u",
            security="tls", pinned_peer_cert_sha256="aa:bb:cc",
            verify_peer_cert_by_name="cert.name",
        )
        d_cert = s_cert.to_dict()
        assert d_cert["pinned_peer_cert_sha256"] == "aa:bb:cc"
        assert d_cert["verify_peer_cert_by_name"] == "cert.name"
        s2_cert = Server.from_dict(d_cert)
        assert s2_cert.pinned_peer_cert_sha256 == "aa:bb:cc"
        assert s2_cert.verify_peer_cert_by_name == "cert.name"

        # 7. From dict heals from link key
        raw_vless = "vless://u@1.2.3.4:443?security=tls&pcs=11:22:33&pcn=cert#Test"
        d_legacy = {"key": raw_vless, "name": "Test", "host": "1.2.3.4", "port": 443, "protocol": "vless"}
        s_healed = Server.from_dict(d_legacy)
        assert s_healed.pinned_peer_cert_sha256 == "11:22:33"
        assert s_healed.verify_peer_cert_by_name == "cert"

        raw_trojan = "trojan://pass@1.2.3.4:443?security=none#TrojanNoSec"
        d_trojan = {"key": raw_trojan, "name": "Trojan", "host": "1.2.3.4", "port": 443, "protocol": "trojan", "security": "tls"}
        s_tr = Server.from_dict(d_trojan)
        assert s_tr.security == "none"

        # 8. TLS pin recovered when parse_link fails
        with mock.patch("utils.link_parser.parse_link", side_effect=RuntimeError("parse boom")):
            d_err = {"key": "custom://host:443?pcs=99:88:77", "name": "Err", "host": "1.2.3.4", "port": 443}
            s_recovered = Server.from_dict(d_err)
            assert s_recovered.pinned_peer_cert_sha256 == "99:88:77"


# --- Trojan and TUIC tests ---

class TestParseTrojanLink:
    def test_parse_trojan_variants(self):
        # Basic
        s = parse_link("trojan://password123@trojan.example.com:443?sni=trojan.example.com#TrojanServer")
        assert s.protocol == ProxyProtocol.TROJAN
        assert s.password == "password123"
        assert s.name == "TrojanServer"

        # WS with allowInsecure
        s_ws = parse_link("trojan-go://pass@1.2.3.4:8443?type=ws&host=cdn.com&path=%2Fws&sni=sni.com&allowInsecure=1#TrojanWS")
        assert s_ws.transport == "ws"
        assert s_ws.insecure is True
        assert s_ws.host_header == "cdn.com"

        # Security none
        s_none = parse_link("trojan://RNNbIv83Iuq87vdO1Es2kFUcQFaw-2Ys@185.188.30.219:32087?security=none&type=tcp#Netherlands")
        assert s_none.security == "none"
        assert s_none.password == "RNNbIv83Iuq87vdO1Es2kFUcQFaw-2Ys"


class TestParseHysteria2Link:
    def test_hy2_pcs_pinsha256(self):
        link = "hy2://secret@1.2.3.4:443?sni=www.google.com&pinSHA256=11:22:33&pcn=cert.name#Hy2Pin"
        s = parse_link(link)
        assert s is not None
        assert s.protocol == ProxyProtocol.HYSTERIA2
        assert s.pinned_peer_cert_sha256 == "11:22:33"
        assert s.verify_peer_cert_by_name == "cert.name"


class TestParseTuicLink:
    def test_tuic_link_parsing(self):
        # Basic TUIC v5
        link = "tuic://2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365:my_pass@tuic.example.com:8443?congestion_control=bbr&udp_relay_mode=native&sni=tuic.example.com#TUIC5"
        s = parse_link(link)
        assert s is not None
        assert s.protocol == ProxyProtocol.TUIC
        assert s.uuid == "2DD61D93-75D8-4DA4-AC0E-6AECE7EAC365"
        assert s.password == "my_pass"
        assert s.host == "tuic.example.com"
        assert s.port == 8443
        assert s.congestion_control == "bbr"
        assert s.udp_relay_mode == "native"
        assert s.server_name == "tuic.example.com"
        assert s.name == "TUIC5"

        # Token fallback (TUIC v4)
        link4 = "tuic://token_only@1.2.3.4:443?congestion=cubic#TUIC4"
        s4 = parse_link(link4)
        assert s4 is not None
        assert s4.protocol == ProxyProtocol.TUIC
        assert s4.password == "token_only"
        assert s4.uuid == ""
        assert s4.congestion_control == "cubic"


# --- Optimizations tests ---

class TestOptimizations:
    def test_link_parser_optimizations_and_private_host(self):
        # LRU cache for private host check
        from utils.server_model import is_private_host
        is_private_host.cache_clear()
        assert is_private_host("127.0.0.1") is True
        assert is_private_host("127.0.0.1") is True
        info = is_private_host.cache_info()
        assert info.hits >= 1
        assert info.maxsize == 1024

        # Parse Mbps formats
        from utils.link_parser import _parse_mbps
        assert _parse_mbps("100Mbps") == 100
        assert _parse_mbps("50mb/s") == 50
        assert _parse_mbps("10M") == 10
        assert _parse_mbps("2000kbps") == 2000
        assert _parse_mbps("500k") == 500
        assert _parse_mbps("") == 0
        assert _parse_mbps(None) == 0
        assert _parse_mbps("invalid") == 0

        # Private flag in parsed link
        vless_priv = parse_link("vless://uuid@192.168.1.1:443?security=none#Priv")
        assert vless_priv is not None and vless_priv.is_private is True
        vless_pub = parse_link("vless://uuid@8.8.8.8:443?security=none#Pub")
        assert vless_pub is not None and vless_pub.is_private is False

    def test_link_parser_rejects_out_of_range_ports(self):
        assert parse_link("vless://uuid@1.2.3.4:0?security=none#S") is None
        assert parse_link("vless://uuid@1.2.3.4:99999?security=none#S") is None
        assert parse_link("vless://uuid@[::1]:0?security=none#S") is None
        assert parse_link("vless://uuid@[::1]:99999?security=none#S") is None

        assert parse_link("hysteria2://pass@1.2.3.4:0#S") is None
        assert parse_link("hysteria2://pass@1.2.3.4:99999#S") is None
        assert parse_link("hy2://pass@[::1]:0#S") is None
        assert parse_link("hy2://pass@[::1]:99999#S") is None

        assert parse_link("trojan://pass@1.2.3.4:0#S") is None
        assert parse_link("trojan://pass@1.2.3.4:99999#S") is None

        assert parse_link("tuic://uuid:pass@1.2.3.4:0#S") is None
        assert parse_link("tuic://uuid:pass@1.2.3.4:99999#S") is None

        vmess_zero = _b64_encode_json({"add": "1.2.3.4", "port": 0, "id": "uuid"})
        assert parse_link(f"vmess://{vmess_zero}") is None
        vmess_huge = _b64_encode_json({"add": "1.2.3.4", "port": 99999, "id": "uuid"})
        assert parse_link(f"vmess://{vmess_huge}") is None


