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


def _b64_encode(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


def _make_legacy_link(method, password, host, port, tag=""):
    payload = _b64_encode(f"{method}:{password}@{host}:{port}")
    link = f"ss://{payload}"
    if tag:
        link += f"#{quote(tag)}"
    return link


def _make_sip002_link(method, password, host, port, tag="", plugin=""):
    payload = _b64_encode(f"{method}:{password}")
    link = f"ss://{payload}@{host}:{port}"
    if plugin:
        link += f"/?plugin={quote(plugin, safe='')}"
    if tag:
        link += f"#{quote(tag)}"
    return link


def _make_aead2022_link(method, password, host, port, tag=""):
    link = f"ss://{method}:{quote(password, safe='')}@{host}:{port}"
    if tag:
        link += f"#{quote(tag)}"
    return link


def test_ss_parser_plugins_and_decoding():
    # 1. Unescape plugin string
    cases = [
        ("abc", "abc"),
        ("abc\\;def", "abc;def"),
        ("a\\=b", "a=b"),
        ("a\\\\b", "a\\b"),
        ("a\\;b\\=c\\\\d", "a;b=c\\d"),
    ]
    for inp, expected in cases:
        assert _unescape_plugin_string(inp) == expected

    # 2. Parse plugin variants
    assert _parse_plugin("v2ray-plugin;server") == ("v2ray-plugin", "server")
    assert _parse_plugin("obfs-local;obfs=http;obfs-host=example.com") == ("obfs-local", "obfs=http;obfs-host=example.com")
    assert _parse_plugin("obfs-local\\;obfs\\=http") == ("obfs-local", "obfs=http")

    # 3. Decode legacy links
    res1 = decode_ss_link(_make_legacy_link("aes-256-gcm", "testpass", "1.2.3.4", 8388))
    assert res1['method'] == "aes-256-gcm" and res1['password'] == "testpass" and res1['server'] == "1.2.3.4" and res1['port'] == 8388

    res2 = decode_ss_link(_make_legacy_link("chacha20-ietf-poly1305", "mypass", "10.0.0.1", 443, tag="My Server"))
    assert res2['tag'] == "My Server" and res2['method'] == "chacha20-ietf-poly1305"

    res3 = decode_ss_link(_make_legacy_link("aes-128-gcm", "pw", "host.com", 1080, tag="Server #1"))
    assert res3['tag'] == "Server #1"

    # 4. Decode SIP002 links & plugins
    r_sip = decode_ss_link(_make_sip002_link("aes-256-gcm", "testpass", "1.2.3.4", 8388, tag="Server #1"))
    assert r_sip['method'] == "aes-256-gcm" and r_sip['password'] == "testpass" and r_sip['tag'] == "Server #1"

    r_obfs = decode_ss_link(_make_sip002_link("aes-256-gcm", "pw", "host.com", 443, plugin="obfs-local;obfs=http;obfs-host=www.example.com"))
    assert r_obfs['plugin'] == "obfs-local" and r_obfs['plugin_opts'] == "obfs=http;obfs-host=www.example.com"

    r_v2 = decode_ss_link(_make_sip002_link("chacha20-ietf-poly1305", "secret", "my.server.com", 8888, plugin="v2ray-plugin;server;tls;host=github.com", tag="V2Ray"))
    assert r_v2['plugin'] == "v2ray-plugin" and r_v2['plugin_opts'] == "server;tls;host=github.com" and r_v2['tag'] == "V2Ray"

    # 5. Decode AEAD2022 links
    r_aead = decode_ss_link(_make_aead2022_link("2022-blake3-aes-256-gcm", "testPasswordPSK==", "1.2.3.4", 8388, tag="AEAD Server"))
    assert r_aead['method'] == "2022-blake3-aes-256-gcm" and r_aead['password'] == "testPasswordPSK==" and r_aead['tag'] == "AEAD Server"

    # 6. Invalid links and out-of-range ports return None
    for invalid in (
        "http://example.com", "", None, "ss://!!!invalid!!!", "ss://example.com:443#t", "ss://example.com:443",
        _make_legacy_link("aes-256-gcm", "pw", "1.2.3.4", 0),
        _make_legacy_link("aes-256-gcm", "pw", "1.2.3.4", 99999),
        _make_sip002_link("aes-256-gcm", "pw", "1.2.3.4", 0),
        _make_sip002_link("aes-256-gcm", "pw", "1.2.3.4", 99999),
    ):
        assert decode_ss_link(invalid) is None


def test_server_model_formats_and_serialization():
    from utils.server_model import Server

    # Legacy
    s1 = Server.from_link(_make_legacy_link("aes-256-gcm", "testpass", "1.2.3.4", 8388, tag="Test"))
    assert s1.method == "aes-256-gcm" and s1.password == "testpass" and s1.host == "1.2.3.4" and s1.port == 8388

    # SIP002 with plugin
    s2 = Server.from_link(_make_sip002_link("aes-256-gcm", "pw", "host.com", 443, plugin="obfs-local;opts", tag="Obfs"))
    assert s2.plugin == "obfs-local" and s2.plugin_opts == "opts"

    # AEAD2022
    s3 = Server.from_link(_make_aead2022_link("2022-blake3-aes-256-gcm", "psk123", "host.com", 443))
    assert s3.method == "2022-blake3-aes-256-gcm" and s3.password == "psk123"

    # Dict serialization and unique key
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


def test_sip008_json_parsing_and_security():
    from utils.subscription_store import _try_parse_sip008_json

    # 1. Basic valid with traffic meta
    data_basic = {
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
    ss_links, meta = _try_parse_sip008_json(json.dumps(data_basic))
    assert len(ss_links) == 1 and "example.com" in ss_links[0]
    assert meta['traffic']['used'] == 1073741824
    assert meta['traffic']['total'] == 1073741824 + 5368709120

    # 2. With plugin
    data_plugin = {
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
    ss_links, _ = _try_parse_sip008_json(json.dumps(data_plugin))
    assert len(ss_links) == 1 and "plugin=" in ss_links[0]

    # 3. IPv6 bracketed & raw
    data_ipv6 = {
        "version": 1,
        "servers": [
            {"remarks": "IPv6 Server", "server": "2001:db8::1", "server_port": 8388, "password": "pw", "method": "aes-256-gcm"},
            {"remarks": "Bracketed IPv6", "server": "[2001:db8::2]", "server_port": 8389, "password": "pw2", "method": "aes-256-gcm"},
        ],
    }
    ss_links, _ = _try_parse_sip008_json(json.dumps(data_ipv6))
    assert len(ss_links) == 2
    assert decode_ss_link(ss_links[0])["server"] == "2001:db8::1"
    assert decode_ss_link(ss_links[1])["server"] == "2001:db8::2"

    # 4. Graceful handling of non-string remarks
    data_grace = {
        "version": 1,
        "servers": [
            {"remarks": ["bad", "list"], "server": "server1.example.com", "server_port": 8388, "method": "aes-256-gcm", "password": "p1", "plugin": {"bad": "dict"}},
            {"remarks": 12345, "server": "server2.example.com", "server_port": 8389, "method": "aes-256-gcm", "password": "p2"},
        ],
    }
    ss_links, _ = _try_parse_sip008_json(json.dumps(data_grace))
    assert len(ss_links) == 2 and "server1.example.com" in ss_links[0] and "#12345" in ss_links[1]

    # 5. Invalid JSON / wrong version / empty
    assert _try_parse_sip008_json("not json at all") is None
    assert _try_parse_sip008_json(json.dumps({"version": 2, "servers": []})) is None
    ss_empty, _ = _try_parse_sip008_json(json.dumps({"version": 1, "servers": []}))
    assert len(ss_empty) == 0

    # 6. Reject crafted injections
    data = {
        "version": 1,
        "servers": [
            {"server": "evil.com:8388#frag", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
            {"server": "evil.com/path?foo=bar", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
            {"server": "evil.com?query", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
            {"server": "user@evil.com", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
            {"server": 12345, "server_port": 443, "method": "aes-256-gcm", "password": "p"},
            {"server": "good.com", "server_port": True, "method": "aes-256-gcm", "password": "p"},
            {"server": "good.com", "server_port": False, "method": "aes-256-gcm", "password": "p"},
            {"server": "good.com", "server_port": "not-a-port", "method": "aes-256-gcm", "password": "p"},
            {"server": "good.com", "server_port": 0, "method": "aes-256-gcm", "password": "p"},
            {"server": "good.com", "server_port": 70000, "method": "aes-256-gcm", "password": "p"},
            {"server": "", "server_port": 443, "method": "aes-256-gcm", "password": "p"},
            {"server": "good.com", "server_port": 443, "method": "", "password": "p"},
            {"server": "valid.example.com", "server_port": 8388, "method": "aes-256-gcm", "password": "p"},
        ],
    }
    ss_links, _ = _try_parse_sip008_json(json.dumps(data))
    assert len(ss_links) == 1 and "valid.example.com:8388" in ss_links[0]


def test_subscription_metadata_and_description(monkeypatch):
    import socket as std_socket
    import utils.subscription_fetch as sf
    from utils import subscription_store as sub_manager
    from utils.subscription_store import (
        _extract_metadata, _decode_maybe_base64, _extract_description,
    )

    monkeypatch.setattr(sf.socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])

    # 1. Extract metadata from headers
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

    # 2. Decode maybe base64
    cases = [
        ("My Provider", "My Provider"),
        ("", ""),
        ("base64:VGVzdA==", "Test"),
        ("base64:!!!not-b64!!!", "base64:!!!not-b64!!!"),
        ("base64:" + base64.b64encode("✦ Ínfinity".encode()).decode(), "✦ Ínfinity"),
    ]
    for inp, expected in cases:
        assert _decode_maybe_base64(inp) == expected

    # 3. Description extraction
    assert _extract_description({}, ['ss://a@b:443'], 'Announce', {'description': 'From header', 'announce': 'Announce'}) == 'From header'
    assert _extract_description({}, ['ss://a@b:443'], 'Announce', {'description': 'base64:!!!bad!!!', 'announce': 'Announce'}) == 'Announce'
    assert _extract_description({}, ['ss://a@b:443'], 'https://example.com/news', {'announce': 'https://example.com/news'}) == ''
    assert _extract_description({}, ['Welcome aboard', 'ss://a@b:443'], 'https://example.com/news', {'announce': 'https://example.com/news'}) == 'Welcome aboard'
    assert _extract_description({}, ['ss://a@b:443'], 'Server maintenance', {}) == 'Server maintenance'
    assert _extract_description({}, ['ss://a@b:443'], 'base64:!!!bad!!!', {}) == ''

    lines = ['First line', '', 'base64:!!!skip!!!', 'Second line', 'ss://a@b:443', 'ss://c@d:444']
    assert _extract_description({}, lines, '', {}) == 'First line Second line'
    lines_15 = [f"line {i}" for i in range(15)] + ['ss://a@b:443']
    assert _extract_description({}, lines_15, '', {}) == ' '.join(f"line {i}" for i in range(10))
    assert _extract_description({}, ['ss://a@b:443', 'vless://x@y:443'], '', {}) == ''

    # 4. Parse subscription metadata integration
    enc = lambda s: 'base64:' + base64.b64encode(s.encode()).decode()
    link = "ss://YWVzLTI1Ni1nY206cHc@1.2.3.4:8388"
    body = base64.b64encode(link.encode()).decode()

    def _mock_resp(b_text, hdrs):
        r = MagicMock()
        st = {"d": b_text.encode()}
        def _read(sz=-1):
            if sz is None or sz < 0:
                d, st["d"] = st["d"], b""
                return d
            d, st["d"] = st["d"][:sz], st["d"][sz:]
            return d
        r.read.side_effect = _read
        r.headers = hdrs
        r.__enter__.return_value = r
        return r

    headers = {
        'Profile-Title': enc('My Provider'),
        'Announce': enc('Server maintenance on Friday'),
        'Profile-Description': enc('Premium service, 4K ready'),
    }
    with patch.object(sub_manager, '_open_subscription_request', return_value=_mock_resp(body, headers)):
        links, meta = sub_manager.parse_subscription('https://example.com/sub')
    assert len(links) == 1
    assert meta['profile_title'] == 'My Provider'
    assert meta['announce'] == 'Server maintenance on Friday'
    assert meta['description'] == 'Premium service, 4K ready'

    with patch.object(sub_manager, '_open_subscription_request', return_value=_mock_resp(body, {'Announce': enc('Welcome to Infinity')})):
        _, meta = sub_manager.parse_subscription('https://example.com/sub')
    assert meta['announce'] == 'Welcome to Infinity' and meta['description'] == 'Welcome to Infinity'

    preamble_body = base64.b64encode(b"Welcome to the club\nss://a@b:443").decode()
    with patch.object(sub_manager, '_open_subscription_request', return_value=_mock_resp(preamble_body, {})):
        _, meta = sub_manager.parse_subscription('https://example.com/sub')
    assert meta['description'] == 'Welcome to the club'
