"""Tests for the subscription fetch security boundary (utils.subscription_fetch)."""
import base64
import logging
import urllib.request
from unittest import mock

import pytest

import utils.subscription_store as sm
import utils.subscription_fetch as sf
from utils.subscription_manager import SubscriptionManager


def test_redirect_hop_count_capped():
    handler = sf._SafeRedirectHandler()
    followed = 0
    with pytest.raises(sf._BlockedSubscription, match="перенаправлений"):
        for i in range(sf.MAX_SUBSCRIPTION_REDIRECTS + 2):
            req = urllib.request.Request("https://8.8.8.8/")
            handler.redirect_request(req, None, 302, "Found", {}, f"https://8.8.8.8/hop{i}")
            followed += 1
    assert followed == sf.MAX_SUBSCRIPTION_REDIRECTS


def test_redirect_to_private_blocked(monkeypatch):
    import socket as std_socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)
    handler = sf._SafeRedirectHandler()
    req = urllib.request.Request("https://example.com/sub")
    with pytest.raises(ValueError, match="приватный"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/next")


def test_refuse_logs_host_not_token(caplog):
    url = "https://panel.example/sub?token=secret-token-123"
    with caplog.at_level(logging.WARNING, logger="utils.subscription_store"):
        links, meta = sm._refuse(url, "nope")
    assert links == []
    assert meta == {"error": "nope"}
    assert "secret-token-123" not in caplog.text
    assert "panel.example" in caplog.text


def test_resolve_public_ip_literal_and_private():
    assert sf._resolve_public_ip("8.8.8.8") == "8.8.8.8"
    with pytest.raises(sf._BlockedSubscription):
        sf._resolve_public_ip("127.0.0.1")
    with pytest.raises(sf._BlockedSubscription):
        sf._resolve_public_ip("10.0.0.1")


def test_pinned_connection_tcp_to_ip_tls_to_hostname(monkeypatch):
    import socket as std_socket

    calls = {}

    class FakeSock:
        def setsockopt(self, *args):
            pass

    def fake_create_connection(addr, timeout=None, source_address=None):
        calls["addr"] = addr
        return FakeSock()

    context = mock.Mock()

    def fake_wrap(sock, server_hostname=None):
        calls["sni"] = server_hostname
        return sock

    context.wrap_socket.side_effect = fake_wrap
    monkeypatch.setattr(std_socket, "create_connection", fake_create_connection)

    conn = sf._PinnedHTTPSConnection("example.com", pinned_ip="8.8.8.8", context=context)
    conn.connect()
    assert calls["addr"] == ("8.8.8.8", 443)
    assert calls["sni"] == "example.com"


def _sized_resp(body: bytes):
    class Resp:
        headers = {}

        def __init__(self):
            self._body = body

        def read(self, size=-1):
            if size is None or size < 0:
                data, self._body = self._body, b""
                return data
            data, self._body = self._body[:size], self._body[size:]
            return data

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    return Resp()


def test_too_many_nodes_blocked(monkeypatch):
    links = [f"vless://u@h:443#N{i}" for i in range(sf.MAX_SUBSCRIPTION_NODES + 1)]
    body = base64.b64encode("\n".join(links).encode()).decode()
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: _sized_resp(body.encode()))
    out_links, meta = sm.parse_subscription("https://8.8.8.8/sub")
    assert out_links == []
    assert "узлов" in meta["error"]


def test_subscription_import_drops_insecure_flag(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    hy2 = "hysteria2://secret@9.9.9.9:443?sni=x.com&insecure=1#N1"
    monkeypatch.setattr("utils.subscription_manager.parse_subscription",
                        lambda url, settings: ([hy2], {}))
    mgr = SubscriptionManager()
    assert mgr.add("Sub", "https://example.com/sub") is True
    sub = mgr.get("Sub")
    assert sub is not None
    servers = sub["servers"]
    assert len(servers) == 1
    assert servers[0].insecure is False


def test_subscription_pinned_cert_does_not_disable_singbox_tls(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    link = "vless://u@1.2.3.4:443?security=tls&pcs=08%3A1E%3A1A#Pinned"
    monkeypatch.setattr("utils.subscription_manager.parse_subscription",
                        lambda url, settings: ([link], {}))
    mgr = SubscriptionManager()
    assert mgr.add("Sub", "https://example.com/sub") is True

    server = mgr.get("Sub")["servers"][0]
    assert server.pinned_peer_cert_sha256 == "08:1E:1A"

    from utils.engines.singbox_outbounds import _build_singbox_vless_outbound
    assert "insecure" not in _build_singbox_vless_outbound(server)["tls"]


def test_read_limited_body_chunked():
    read_sizes = []

    class ChunkedResp:
        headers = {}

        def __init__(self, total_bytes):
            self._left = total_bytes

        def read(self, size=-1):
            read_sizes.append(size)
            if self._left <= 0:
                return b""
            chunk_size = min(size, self._left)
            self._left -= chunk_size
            return b"a" * chunk_size

    resp = ChunkedResp(100 * 1024)
    data = sf._read_limited_body(resp)
    assert len(data) == 100 * 1024
    assert all(s == sf._READ_CHUNK_SIZE for s in read_sizes)

    resp_big = ChunkedResp(sf.MAX_SUBSCRIPTION_BYTES + 1024)
    with pytest.raises(sf._BlockedSubscription):
        sf._read_limited_body(resp_big)


def test_read_limited_body_slowloris_timeout():
    import time

    class SlowlorisResp:
        headers = {}

        def __init__(self):
            self.count = 0

        def read(self, size=-1):
            self.count += 1
            time.sleep(0.02)
            return b"x"

    resp = SlowlorisResp()
    with pytest.raises(TimeoutError):
        sf._read_limited_body(resp, timeout=0.05)



def test_redirect_cross_origin_strips_xhwid(monkeypatch):
    import socket as std_socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)
    handler = sf._SafeRedirectHandler()
    req = urllib.request.Request(
        "https://provider.example/sub",
        headers={
            "X-hwid": "secret-device-hwid",
            "User-Agent": "socksicle-test",
            "Authorization": "Bearer token",
            "Cookie": "uid=123",
        },
    )

    # Cross-origin redirect to third-party CDN / mirror
    new_req = handler.redirect_request(
        req, None, 302, "Found", {}, "https://cdn.mirror.example/download"
    )
    assert new_req is not None
    hdrs = {k.lower(): v for k, v in new_req.headers.items()}
    assert "x-hwid" not in hdrs
    assert "authorization" not in hdrs
    assert "cookie" not in hdrs
    assert hdrs.get("user-agent") == "socksicle-test"


def test_redirect_same_origin_preserves_xhwid(monkeypatch):
    import socket as std_socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)
    handler = sf._SafeRedirectHandler()
    req = urllib.request.Request(
        "https://provider.example/sub",
        headers={
            "X-hwid": "secret-device-hwid",
            "User-Agent": "socksicle-test",
        },
    )

    # Same-origin redirect
    new_req = handler.redirect_request(
        req, None, 302, "Found", {}, "https://provider.example/sub/v2"
    )
    assert new_req is not None
    hdrs = {k.lower(): v for k, v in new_req.headers.items()}
    assert hdrs.get("x-hwid") == "secret-device-hwid"
    assert hdrs.get("user-agent") == "socksicle-test"


def test_is_cross_origin():
    assert not sf._is_cross_origin("https://example.com/a", "https://example.com/b")
    assert not sf._is_cross_origin("https://example.com:443/a", "https://example.com/b")
    assert not sf._is_cross_origin("https://EXAMPLE.COM/a", "https://example.com/b")
    assert sf._is_cross_origin("https://example.com/a", "https://cdn.example.com/a")
    assert sf._is_cross_origin("https://example.com/a", "https://example.com:8443/a")
    assert sf._is_cross_origin("https://example.com/a", "http://example.com/a")


