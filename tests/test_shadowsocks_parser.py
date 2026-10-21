"""Unit tests for shadowsocks_parser and subscription_store."""
import base64
import json
import pytest
from unittest.mock import patch, MagicMock
from urllib.parse import quote

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.shadowsocks_parser import decode_ss_link, _parse_plugin, _unescape_plugin_string


# --- Helper to build ss:// links ---

def _b64_encode(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


def _make_legacy_link(method, password, host, port, tag=""):
    """Legacy whole-URI base64: ss://BASE64(method:password)@host:port#tag"""
    payload = _b64_encode(f"{method}:{password}@{host}:{port}")
    link = f"ss://{payload}"
    if tag:
        link += f"#{quote(tag)}"
    return link


def _make_sip002_link(method, password, host, port, tag="", plugin=""):
    """SIP002 base64 userinfo: ss://BASE64(method:password)@host:port/?plugin=...#tag"""
    payload = _b64_encode(f"{method}:{password}")
    link = f"ss://{payload}@{host}:{port}"
    if plugin:
        link += f"/?plugin={quote(plugin, safe='')}"
    if tag:
        link += f"#{quote(tag)}"
    return link


def _make_aead2022_link(method, password, host, port, tag=""):
    """AEAD-2022 plain userinfo: ss://method:password@host:port#tag"""
    link = f"ss://{method}:{quote(password, safe='')}"
    link += f"@{host}:{port}"
    if tag:
        link += f"#{quote(tag)}"
    return link


# --- Tests for _unescape_plugin_string ---

class TestUnescapePluginString:
    @pytest.mark.parametrize("inp,expected", [
        ("abc", "abc"),
        ("abc\\;def", "abc;def"),
        ("a\\=b", "a=b"),
        ("a\\\\b", "a\\b"),
        ("a\\;b\\=c\\\\d", "a;b=c\\d"),
    ])
    def test_unescape_plugin_string(self, inp, expected):
        assert _unescape_plugin_string(inp) == expected


# --- Tests for _parse_plugin ---

class TestParsePlugin:
    def test_parse_plugin_variants(self):
        assert _parse_plugin("v2ray-plugin;server") == ("v2ray-plugin", "server")
        assert _parse_plugin("obfs-local;obfs=http;obfs-host=example.com") == ("obfs-local", "obfs=http;obfs-host=example.com")
        assert _parse_plugin("obfs-local\\;obfs\\=http") == ("obfs-local", "obfs=http")


# --- Tests for decode_ss_link ---

class TestDecodeLegacyLink:
    def test_decode_legacy_link(self):
        res1 = decode_ss_link(_make_legacy_link("aes-256-gcm", "testpass", "1.2.3.4", 8388))
        assert res1['method'] == "aes-256-gcm" and res1['password'] == "testpass"
        assert res1['server'] == "1.2.3.4" and res1['port'] == 8388

        res2 = decode_ss_link(_make_legacy_link("chacha20-ietf-poly1305", "mypass", "10.0.0.1", 443, tag="My Server"))
        assert res2['tag'] == "My Server" and res2['method'] == "chacha20-ietf-poly1305"

        res3 = decode_ss_link(_make_legacy_link("aes-128-gcm", "pw", "host.com", 1080, tag="Server #1"))
        assert res3['tag'] == "Server #1"


class TestDecodeSIP002Link:
    def test_basic_sip002_and_tags(self):
        res1 = decode_ss_link(_make_sip002_link("aes-256-gcm", "testpass", "1.2.3.4", 8388, tag="Server #1"))
        assert res1['method'] == "aes-256-gcm" and res1['password'] == "testpass"
        assert res1['server'] == "1.2.3.4" and res1['port'] == 8388
        assert res1['tag'] == "Server #1"

    def test_sip002_with_plugins(self):
        plugin_obfs = "obfs-local;obfs=http;obfs-host=www.example.com"
        r1 = decode_ss_link(_make_sip002_link("aes-256-gcm", "pw", "host.com", 443, plugin=plugin_obfs))
        assert r1['plugin'] == "obfs-local" and r1['plugin_opts'] == "obfs=http;obfs-host=www.example.com"

        plugin_v2ray = "v2ray-plugin;server;tls;host=github.com"
        r2 = decode_ss_link(_make_sip002_link("chacha20-ietf-poly1305", "secret", "my.server.com", 8888, plugin=plugin_v2ray, tag="V2Ray"))
        assert r2['plugin'] == "v2ray-plugin" and r2['plugin_opts'] == "server;tls;host=github.com" and r2['tag'] == "V2Ray"

        r3 = decode_ss_link(_make_sip002_link("aes-256-gcm", "pw", "1.2.3.4", 443, plugin="v2ray-plugin;server"))
        assert r3['plugin'] == "v2ray-plugin" and r3['plugin_opts'] == "server"


class TestDecodeAEAD2022Link:
    def test_aead2022_decoding(self):
        r1 = decode_ss_link(_make_aead2022_link("2022-blake3-aes-256-gcm", "testPasswordPSK==", "1.2.3.4", 8388, tag="AEAD Server"))
        assert r1['method'] == "2022-blake3-aes-256-gcm" and r1['password'] == "testPasswordPSK=="
        assert r1['server'] == "1.2.3.4" and r1['port'] == 8388 and r1['tag'] == "AEAD Server"

        r2 = decode_ss_link(_make_aead2022_link("2022-blake3-chacha20-poly1305", "abc/def+ghi==", "server.net", 8443))
        assert r2['password'] == "abc/def+ghi=="


class TestDecodeEdgeCases:
    @pytest.mark.parametrize("invalid", [
        "http://example.com", "", None, "ss://!!!invalid!!!",
        "ss://example.com:443#t", "ss://example.com:443",
    ])
    def test_invalid_links_return_none(self, invalid):
        assert decode_ss_link(invalid) is None


# --- Tests for Server model ---

class TestServerModel:
    def _get_server_class(self):
        from utils.server_model import Server
        return Server

    def test_from_link_formats(self):
        Server = self._get_server_class()
        # Legacy
        s1 = Server.from_link(_make_legacy_link("aes-256-gcm", "testpass", "1.2.3.4", 8388, tag="Test"))
        assert s1.method == "aes-256-gcm" and s1.password == "testpass" and s1.host == "1.2.3.4" and s1.port == 8388

        # SIP002 with plugin
        s2 = Server.from_link(_make_sip002_link("aes-256-gcm", "pw", "host.com", 443, plugin="obfs-local;opts", tag="Obfs"))
        assert s2.plugin == "obfs-local" and s2.plugin_opts == "opts"

        # AEAD2022
        s3 = Server.from_link(_make_aead2022_link("2022-blake3-aes-256-gcm", "psk123", "host.com", 443))
        assert s3.method == "2022-blake3-aes-256-gcm" and s3.password == "psk123"

    def test_dict_serialization_and_unique_key(self):
        Server = self._get_server_class()
        s = Server(key="k", name="n", host="h", port=443, method="m", password="p",
                   plugin="v2ray-plugin", plugin_opts="server")
        d = s.to_dict()
        assert d['plugin'] == "v2ray-plugin" and d['plugin_opts'] == "server"

        restored = Server.from_dict(d)
        assert restored.plugin == "v2ray-plugin" and restored.plugin_opts == "server"

        s_no_plug = Server(key="k", name="n", host="h", port=443, method="m", password="p")
        assert 'plugin' not in s_no_plug.to_dict()

        s_dup = Server(host="h", port=443, method="m", password="p")
        s_other = Server(host="h", port=443, method="m", password="other")
        assert s_no_plug.unique_key == s_dup.unique_key
        assert s_no_plug.unique_key != s_other.unique_key



# --- Tests for SIP008 JSON parsing in subscription_store ---

class TestSIP008JSON:
    def test_valid_sip008(self):
        from utils.subscription_store import _try_parse_sip008_json
        data = {
            "version": 1,
            "servers": [
                {
                    "id": "test-id",
                    "remarks": "Test Server",
                    "server": "example.com",
                    "server_port": 8388,
                    "password": "testpass",
                    "method": "chacha20-ietf-poly1305",
                }
            ],
            "bytes_used": 1073741824,
            "bytes_remaining": 5368709120,
        }
        ss_links, meta = _try_parse_sip008_json(json.dumps(data))
        assert len(ss_links) == 1
        assert "example.com" in ss_links[0]
        assert meta['traffic']['used'] == 1073741824
        assert meta['traffic']['total'] == 1073741824 + 5368709120

    def test_sip008_with_plugin(self):
        from utils.subscription_store import _try_parse_sip008_json
        data = {
            "version": 1,
            "servers": [
                {
                    "remarks": "With Plugin",
                    "server": "host.com",
                    "server_port": 443,
                    "password": "pw",
                    "method": "aes-256-gcm",
                    "plugin": "obfs-local",
                    "plugin_opts": "obfs=http;obfs-host=cdn.com",
                }
            ],
        }
        ss_links, meta = _try_parse_sip008_json(json.dumps(data))
        assert len(ss_links) == 1
        # The link should contain plugin info
        assert "plugin=" in ss_links[0]

    def test_invalid_json(self):
        from utils.subscription_store import _try_parse_sip008_json
        result = _try_parse_sip008_json("not json at all")
        assert result is None

    def test_wrong_version(self):
        from utils.subscription_store import _try_parse_sip008_json
        data = {"version": 2, "servers": []}
        result = _try_parse_sip008_json(json.dumps(data))
        assert result is None

    def test_no_servers(self):
        from utils.subscription_store import _try_parse_sip008_json
        data = {"version": 1, "servers": []}
        ss_links, meta = _try_parse_sip008_json(json.dumps(data))
        assert len(ss_links) == 0

    def test_sip008_ipv6_bracketed(self):
        from utils.subscription_store import _try_parse_sip008_json
        from utils.shadowsocks_parser import decode_ss_link
        data = {
            "version": 1,
            "servers": [
                {
                    "remarks": "IPv6 Server",
                    "server": "2001:db8::1",
                    "server_port": 8388,
                    "password": "pw",
                    "method": "aes-256-gcm",
                },
                {
                    "remarks": "Bracketed IPv6",
                    "server": "[2001:db8::2]",
                    "server_port": 8389,
                    "password": "pw2",
                    "method": "aes-256-gcm",
                },
            ],
        }
        ss_links, meta = _try_parse_sip008_json(json.dumps(data))
        assert len(ss_links) == 2
        assert "@[2001:db8::1]:8388" in ss_links[0]
        assert "@[2001:db8::2]:8389" in ss_links[1]

        parsed0 = decode_ss_link(ss_links[0])
        assert parsed0["server"] == "2001:db8::1"
        assert parsed0["port"] == 8388

        parsed1 = decode_ss_link(ss_links[1])
        assert parsed1["server"] == "2001:db8::2"
        assert parsed1["port"] == 8389

    def test_sip008_rejects_crafted_injections(self):
        from utils.subscription_store import _try_parse_sip008_json
        data = {
            "version": 1,
            "servers": [
                # Crafted hosts
                {"server": "evil.com:8388#frag", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
                {"server": "evil.com/path?foo=bar", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
                {"server": "evil.com?query", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
                {"server": "user@evil.com", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
                {"server": 12345, "server_port": 443, "method": "aes-256-gcm", "password": "p"},
                # Invalid ports
                {"server": "good.com", "server_port": True, "method": "aes-256-gcm", "password": "p"},
                {"server": "good.com", "server_port": False, "method": "aes-256-gcm", "password": "p"},
                {"server": "good.com", "server_port": "not-a-port", "method": "aes-256-gcm", "password": "p"},
                {"server": "good.com", "server_port": 0, "method": "aes-256-gcm", "password": "p"},
                {"server": "good.com", "server_port": 70000, "method": "aes-256-gcm", "password": "p"},
                # Missing method or host
                {"server": "", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
                {"server": "good.com", "server_port": 443, "method": "", "password": "p"},
                # Valid server
                {"server": "valid.example.com", "server_port": 8388, "method": "aes-256-gcm", "password": "p"},
            ],
        }
        ss_links, meta = _try_parse_sip008_json(json.dumps(data))
        assert len(ss_links) == 1
        assert "valid.example.com:8388" in ss_links[0]

    def test_sip008_handles_non_string_remarks_and_plugin_gracefully(self):
        from utils.subscription_store import _try_parse_sip008_json
        data = {
            "version": 1,
            "servers": [
                {
                    "remarks": ["bad", "list"],
                    "server": "server1.example.com",
                    "server_port": 8388,
                    "method": "aes-256-gcm",
                    "password": "p1",
                    "plugin": {"bad": "dict"},
                },
                {
                    "remarks": 12345,
                    "server": "server2.example.com",
                    "server_port": 8389,
                    "method": "aes-256-gcm",
                    "password": "p2",
                },
            ],
        }
        ss_links, meta = _try_parse_sip008_json(json.dumps(data))
        assert len(ss_links) == 2
        assert "server1.example.com" in ss_links[0]
        assert "server2.example.com" in ss_links[1]
        assert "#12345" in ss_links[1]


# --- Tests for metadata extraction ---

class TestMetadataExtraction:
    def test_extract_metadata(self):
        from utils.subscription_store import _extract_metadata
        resp = MagicMock()
        resp.headers = {
            'Subscription-Userinfo': 'upload=1000; download=2000; total=5000; expire=1700000000',
            'Profile-Title': 'My Provider',
            'Profile-Update-Interval': '24',
            'Support-URL': 'https://support.example.com',
            'Announce': 'Server maintenance on Friday',
        }
        meta = _extract_metadata(resp)
        assert meta['traffic']['used'] == 3000
        assert meta['traffic']['total'] == 5000
        assert meta['traffic']['expire'] == 1700000000
        assert meta['profile_title'] == 'My Provider'
        assert meta['profile_update_interval'] == 24
        assert meta['support_url'] == 'https://support.example.com'
        assert meta['announce'] == 'Server maintenance on Friday'

        resp.headers = {}
        assert _extract_metadata(resp) == {}


# --- Tests for base64 header value decoding ---

class TestDecodeMaybeBase64:
    @pytest.mark.parametrize("inp, expected", [
        ("My Provider", "My Provider"),
        ("", ""),
        ("base64:VGVzdA==", "Test"),
        ("base64:!!!not-b64!!!", "base64:!!!not-b64!!!"),
        ("base64:" + base64.b64encode("✦ Ínfinity".encode()).decode(), "✦ Ínfinity"),
    ])
    def test_decode_maybe_base64(self, inp, expected):
        from utils.subscription_store import _decode_maybe_base64
        assert _decode_maybe_base64(inp) == expected


# --- Tests for description extraction ---

class TestExtractDescription:
    def test_extract_description_sources(self):
        from utils.subscription_store import _extract_description
        # Header description beats announce
        assert _extract_description({}, ['ss://a@b:443'], 'Announce', {'description': 'From header', 'announce': 'Announce'}) == 'From header'
        # Undecodable header skipped for announce
        assert _extract_description({}, ['ss://a@b:443'], 'Announce', {'description': 'base64:!!!bad!!!', 'announce': 'Announce'}) == 'Announce'
        # Announce URL skipped, fallback to preamble or empty
        assert _extract_description({}, ['ss://a@b:443'], 'https://example.com/news', {'announce': 'https://example.com/news'}) == ''
        assert _extract_description({}, ['Welcome aboard', 'ss://a@b:443'], 'https://example.com/news', {'announce': 'https://example.com/news'}) == 'Welcome aboard'
        # Valid announce text or invalid base64 announce
        assert _extract_description({}, ['ss://a@b:443'], 'Server maintenance', {}) == 'Server maintenance'
        assert _extract_description({}, ['ss://a@b:443'], 'base64:!!!bad!!!', {}) == ''
        # Body preamble (skipping base64 decode failures, capped at 10 lines)
        lines = ['First line', '', 'base64:!!!skip!!!', 'Second line', 'ss://a@b:443', 'ss://c@d:444']
        assert _extract_description({}, lines, '', {}) == 'First line Second line'
        lines_15 = [f"line {i}" for i in range(15)] + ['ss://a@b:443']
        assert _extract_description({}, lines_15, '', {}) == ' '.join(f"line {i}" for i in range(10))
        # All links -> empty
        assert _extract_description({}, ['ss://a@b:443', 'vless://x@y:443'], '', {}) == ''


# --- Tests for parse_subscription metadata integration ---

class TestParseSubscriptionMetadata:
    _LINK = "ss://YWVzLTI1Ni1nY206cHc@1.2.3.4:8388"

    @pytest.fixture(autouse=True)
    def _stub_public_dns(self, monkeypatch):
        """Keep the SSRF preflight off real DNS; the transport is mocked below."""
        import socket as std_socket
        import utils.subscription_fetch as sf

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)

    def _mock_urlopen(self, body, headers):
        from utils import subscription_store as sub_manager
        resp = MagicMock()
        state = {"data": body.encode()}

        def _read(size=-1):
            data = state["data"]
            if size is None or size < 0:
                state["data"] = b""
                return data
            out, state["data"] = data[:size], data[size:]
            return out

        resp.read.side_effect = _read
        resp.headers = headers
        resp.__enter__.return_value = resp
        return patch.object(sub_manager, '_open_subscription_request', return_value=resp)

    def test_parse_subscription_metadata(self):
        from utils import subscription_store as sub_manager
        enc = lambda s: 'base64:' + base64.b64encode(s.encode()).decode()

        # 1. Header description & title
        headers = {
            'Profile-Title': enc('My Provider'),
            'Announce': enc('Server maintenance on Friday'),
            'Profile-Description': enc('Premium service, 4K ready'),
        }
        body = base64.b64encode(self._LINK.encode()).decode()
        with self._mock_urlopen(body, headers):
            links, meta = sub_manager.parse_subscription('https://example.com/sub')
        assert len(links) == 1
        assert meta['profile_title'] == 'My Provider'
        assert meta['announce'] == 'Server maintenance on Friday'
        assert meta['description'] == 'Premium service, 4K ready'

        # 2. Announce fallback
        with self._mock_urlopen(body, {'Announce': enc('Welcome to Infinity')}):
            _, meta = sub_manager.parse_subscription('https://example.com/sub')
        assert meta['announce'] == 'Welcome to Infinity'
        assert meta['description'] == 'Welcome to Infinity'

        # 3. Preamble fallback
        preamble_body = base64.b64encode(b"Welcome to the club\nss://a@b:443").decode()
        with self._mock_urlopen(preamble_body, {}):
            _, meta = sub_manager.parse_subscription('https://example.com/sub')
        assert meta['description'] == 'Welcome to the club'
