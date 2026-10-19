import asyncio
import socket
import threading
import time

import pytest
from PySide6.QtCore import QThreadPool

_ACTIVE_SERVERS = set()


@pytest.fixture(autouse=True)
def _auto_cleanup_ping_servers():
    yield
    servers = list(_ACTIVE_SERVERS)
    _ACTIVE_SERVERS.clear()
    for s in servers:
        try:
            s.close()
        except Exception:
            pass

from utils.ping import (
    http_ping_via_socks5_once,
    ping_via_socks5, tcp_connect_ping_via_socks5,
    ProxyPingJob, AsyncBatchPingJob, PING_ERROR_SENTINEL,
    socks5_proxy_ready,
    async_direct_tcp_ping, async_direct_http_ping, async_direct_quic_ping,
    async_socks5_proxy_ready, async_http_ping_via_socks5_once,
    async_tcp_connect_ping_via_socks5,
    async_ping_server_job, async_ping_all,
    _configure_tcp_socket, _configure_udp_socket,
    parse_ping_url, DEFAULT_PING_TIMEOUT,
)

CONNECT_OK = b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50"
CONNECT_FAIL = b"\x05\x01"
HEADERS = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n"
NO_CONTENT = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
BODY = b"pong"


class _Socks5TestServer:
    """Minimal SOCKS5 relay that sends an HTTP response in TCP segments."""

    def __init__(self, header_delay=0.0, data_delay=0.0, reject_connect=False,
                 drop_after_connect=False, silent=False, no_body=False,
                 truncate_headers=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.header_delay = header_delay
        self.data_delay = data_delay
        self.reject_connect = reject_connect
        self.drop_after_connect = drop_after_connect
        self.silent = silent
        self.no_body = no_body
        self.truncate_headers = truncate_headers
        self.method = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        _ACTIVE_SERVERS.add(self)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                with conn:
                    conn.settimeout(10)
                    greeting = b""
                    while len(greeting) < 3:
                        chunk = conn.recv(4 - len(greeting))
                        if not chunk:
                            break
                        greeting += chunk
                    if greeting[:3] != b"\x05\x01\x00":
                        continue
                    conn.sendall(b"\x05\x00")
                    request = b""
                    while len(request) < 10:
                        chunk = conn.recv(64)
                        if not chunk:
                            break
                        request += chunk
                    if self.reject_connect:
                        conn.sendall(CONNECT_FAIL)
                        continue
                    conn.sendall(CONNECT_OK)
                    if self.drop_after_connect:
                        continue
                    http_request = b""
                    while b"\r\n\r\n" not in http_request:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        http_request += chunk
                    self.method = http_request.split(b" ", 1)[0]
                    if self.silent:
                        time.sleep(3)
                        continue
                    time.sleep(self.header_delay)
                    response = HEADERS if not self.no_body else NO_CONTENT
                    if self.truncate_headers:
                        conn.sendall(response[:24])
                        continue
                    conn.sendall(response)
                    time.sleep(self.data_delay)
                    if not self.no_body:
                        conn.sendall(BODY)
            except OSError:
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        _ACTIVE_SERVERS.discard(self)
        try:
            self.sock.close()
        except Exception:
            pass
        thread = getattr(self, "thread", None)
        if (thread is not None and thread.is_alive()
                and threading.current_thread() is not thread):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.05)
                    s.connect(("127.0.0.1", self.port))
            except Exception:
                pass
            try:
                thread.join(timeout=0.2)
            except RuntimeError:
                # __del__ fired on the server thread itself during GC:
                # a thread can never join itself.
                pass


class _RawListener:
    """Accepts one plain TCP connection and records the bytes received."""

    def __init__(self, respond: bytes | None = None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.data = None
        self.respond = respond
        self.thread = threading.Thread(target=self._serve, daemon=True)
        _ACTIVE_SERVERS.add(self)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        try:
            with conn:
                conn.settimeout(10)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                self.data = data
                if self.respond and b"\r\n\r\n" in data:
                    conn.sendall(self.respond)
        except OSError:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        _ACTIVE_SERVERS.discard(self)
        try:
            self.sock.close()
        except Exception:
            pass
        thread = getattr(self, "thread", None)
        if (thread is not None and thread.is_alive()
                and threading.current_thread() is not thread):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.05)
                    s.connect(("127.0.0.1", self.port))
            except Exception:
                pass
            try:
                thread.join(timeout=0.2)
            except RuntimeError:
                # __del__ fired on the server thread itself during GC:
                # a thread can never join itself.
                pass


def test_ping_measures_until_headers_complete():
    srv = _Socks5TestServer(header_delay=0.2)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=3)
        assert ms is not None
        assert ms >= 150
        assert ms < 2000
    finally:
        srv.close()


def test_ping_empty_204_response_is_a_success():
    srv = _Socks5TestServer(header_delay=0.1, no_body=True)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=3)
        assert ms is not None
        assert ms >= 50
    finally:
        srv.close()


def test_ping_without_body_delay_still_returns_value():
    srv = _Socks5TestServer()
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=3)
        assert ms is not None
    finally:
        srv.close()


def test_ping_rejected_connect_returns_none():
    srv = _Socks5TestServer(reject_connect=True)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=2)
        assert ms is None
    finally:
        srv.close()


def test_ping_closed_after_connect_returns_none():
    srv = _Socks5TestServer(drop_after_connect=True)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=2)
        assert ms is None
    finally:
        srv.close()


def test_ping_timeout_returns_none():
    srv = _Socks5TestServer(silent=True)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=0.3)
        assert ms is None
    finally:
        srv.close()


def test_ping_truncated_headers_returns_none():
    srv = _Socks5TestServer(truncate_headers=True)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=2)
        assert ms is None
    finally:
        srv.close()


def test_ping_to_unreachable_proxy_returns_none():
    ms = http_ping_via_socks5_once("127.0.0.1", 1, timeout=0.5)
    assert ms is None





def test_ping_get_sends_get_request():
    srv = _Socks5TestServer()
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=3)
        assert ms is not None
        assert srv.method == b"GET"
    finally:
        srv.close()


def test_ping_head_sends_head_request():
    srv = _Socks5TestServer(header_delay=0.1)
    try:
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=3,
                                       method="HEAD")
        assert srv.method == b"HEAD"
        assert ms is not None
        assert ms >= 50
    finally:
        srv.close()


def test_tcp_connect_ping_measures_connect():
    srv = _Socks5TestServer()
    try:
        ms = tcp_connect_ping_via_socks5("127.0.0.1", srv.port, timeout=2)
        assert ms is not None
        assert ms >= 0
    finally:
        srv.close()


def test_tcp_connect_ping_to_unreachable_proxy_returns_none():
    ms = tcp_connect_ping_via_socks5("127.0.0.1", 1, timeout=0.5)
    assert ms is None


def test_tcp_connect_ping_rejected_connect_returns_none():
    srv = _Socks5TestServer(reject_connect=True)
    try:
        ms = tcp_connect_ping_via_socks5("127.0.0.1", srv.port, timeout=2)
        assert ms is None
    finally:
        srv.close()


def test_ping_via_socks5_dispatches_tcp_connect():
    srv = _Socks5TestServer()
    try:
        ms = ping_via_socks5("127.0.0.1", srv.port, method="tcp_connect")
        assert ms is not None
        assert ms >= 0
    finally:
        srv.close()


def test_ping_via_socks5_default_is_http_get():
    srv = _Socks5TestServer()
    try:
        ms = ping_via_socks5("127.0.0.1", srv.port)
        assert ms is not None
        assert srv.method == b"GET"
    finally:
        srv.close()


def test_ping_via_socks5_unknown_method_falls_back_to_get():
    srv = _Socks5TestServer()
    try:
        ms = ping_via_socks5("127.0.0.1", srv.port, method="bogus")
        assert ms is not None
        assert srv.method == b"GET"
    finally:
        srv.close()


def test_proxy_ping_job_reports_real_latency(qapp):
    srv = _Socks5TestServer(header_delay=0.1)
    results = []
    pool = QThreadPool()
    try:
        pool.start(ProxyPingJob("127.0.0.1", srv.port, results.append))
        assert pool.waitForDone(6000)
        assert len(results) == 1
        assert results[0] is not None
        assert results[0] >= 50
    finally:
        srv.close()


def test_proxy_ping_job_failure_reports_none(qapp):
    srv = _Socks5TestServer(reject_connect=True)
    results = []
    pool = QThreadPool()
    try:
        pool.start(ProxyPingJob("127.0.0.1", srv.port, results.append))
        assert pool.waitForDone(6000)
        assert results == [None]
    finally:
        srv.close()





def test_async_hysteria2_falls_back_to_tcp_when_quic_fails():
    import utils.ping as ping_mod
    from unittest import mock

    assert not hasattr(ping_mod, "direct_icmp_ping")

    async def _run():
        with mock.patch.object(ping_mod, "async_direct_quic_ping", return_value=None), \
             mock.patch.object(ping_mod, "async_direct_tcp_ping", return_value=88.0):
            return await async_ping_server_job(5, "127.0.0.1", 443, method="thp",
                                               socks5_port=None, protocol="hysteria2")

    assert asyncio.run(_run()) == (5, 88.0)


def test_socket_options_applied():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _configure_tcp_socket(sock)
        # Check TCP_NODELAY is enabled (1)
        val = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
        assert val != 0
        # Check SO_REUSEADDR is enabled (1)
        val_reuse = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert val_reuse != 0
    finally:
        sock.close()

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _configure_udp_socket(udp_sock)
        val_reuse = udp_sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert val_reuse != 0
    finally:
        udp_sock.close()


def test_async_direct_tcp_ping_success():
    srv = _Socks5TestServer()
    try:
        ms = asyncio.run(async_direct_tcp_ping("127.0.0.1", srv.port, timeout=2.0))
        assert ms is not None
        assert ms >= 0
    finally:
        srv.close()


def test_async_direct_tcp_ping_failure():
    ms = asyncio.run(async_direct_tcp_ping("127.0.0.1", 1, timeout=0.2))
    assert ms is None


def test_async_direct_http_ping_success():
    srv = _RawListener(respond=HEADERS)
    try:
        ms = asyncio.run(async_direct_http_ping("127.0.0.1", srv.port, timeout=2.0))
        assert ms is not None
        assert ms >= 0
    finally:
        srv.close()


def test_async_direct_http_ping_failure():
    ms = asyncio.run(async_direct_http_ping("127.0.0.1", 1, timeout=0.2))
    assert ms is None


def test_async_direct_quic_ping_success():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(("127.0.0.1", 0))
    port = udp_sock.getsockname()[1]

    def _mock_udp_responder():
        try:
            udp_sock.settimeout(2.0)
            data, addr = udp_sock.recvfrom(2048)
            udp_sock.sendto(b"\x80\x00\x00\x00\x00\x08" + b"\x00" * 8 + b"\x08" + b"\x00" * 8, addr)
        except Exception:
            pass
        finally:
            udp_sock.close()

    t = threading.Thread(target=_mock_udp_responder, daemon=True)
    t.start()

    ms = asyncio.run(async_direct_quic_ping("127.0.0.1", port, timeout=1.0))
    assert ms is not None
    assert ms >= 0


def test_async_direct_quic_ping_failure():
    ms = asyncio.run(async_direct_quic_ping("127.0.0.1", 1, timeout=0.2))
    assert ms is None


def test_async_socks5_proxy_ready_success():
    srv = _Socks5TestServer()
    try:
        ready = asyncio.run(async_socks5_proxy_ready(srv.port, timeout=1.0))
        assert ready is True
    finally:
        srv.close()


def test_async_socks5_proxy_ready_failure():
    ready = asyncio.run(async_socks5_proxy_ready(1, timeout=0.2))
    assert ready is False


def test_async_http_ping_via_socks5_success():
    srv = _Socks5TestServer(header_delay=0.05)
    try:
        ms = asyncio.run(async_http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=2.0))
        assert ms is not None
        assert ms >= 30
    finally:
        srv.close()


def test_async_http_ping_via_socks5_rejected():
    srv = _Socks5TestServer(reject_connect=True)
    try:
        ms = asyncio.run(async_http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=1.0))
        assert ms is None
    finally:
        srv.close()


def test_async_tcp_connect_ping_via_socks5_success():
    srv = _Socks5TestServer()
    try:
        ms = asyncio.run(async_tcp_connect_ping_via_socks5("127.0.0.1", srv.port, timeout=2.0))
        assert ms is not None
        assert ms >= 0
    finally:
        srv.close()


def test_async_tcp_connect_ping_via_socks5_rejected():
    srv = _Socks5TestServer(reject_connect=True)
    try:
        ms = asyncio.run(async_tcp_connect_ping_via_socks5("127.0.0.1", srv.port, timeout=1.0))
        assert ms is None
    finally:
        srv.close()





def test_async_ping_server_job():
    srv = _Socks5TestServer()
    try:
        idx, ms = asyncio.run(async_ping_server_job(3, "127.0.0.1", srv.port, method="tcp_connect"))
        assert idx == 3
        assert ms >= 0

        idx2, ms2 = asyncio.run(async_ping_server_job(4, "127.0.0.1", 1, method="tcp_connect", timeout=0.2))
        assert idx2 == 4
        assert ms2 == PING_ERROR_SENTINEL
    finally:
        srv.close()


def test_async_ping_all_batch():
    srv1 = _Socks5TestServer()
    srv2 = _Socks5TestServer()
    try:
        servers = [
            {"host": "127.0.0.1", "port": srv1.port},
            {"host": "127.0.0.1", "port": srv2.port},
            {"host": "127.0.0.1", "port": 1},  # failed
        ]
        callbacks = []
        res = asyncio.run(async_ping_all(
            servers,
            callback=lambda i, ms: callbacks.append((i, ms)),
            method="tcp_connect",
            timeout=0.5
        ))
        assert len(res) == 3
        assert res[0] >= 0
        assert res[1] >= 0
        assert res[2] == PING_ERROR_SENTINEL
        assert len(callbacks) == 3
    finally:
        srv1.close()
        srv2.close()


def test_async_batch_ping_job_in_qthreadpool(qapp):
    srv1 = _Socks5TestServer()
    srv2 = _Socks5TestServer()
    try:
        servers = [
            {"host": "127.0.0.1", "port": srv1.port},
            {"host": "127.0.0.1", "port": srv2.port},
            {"host": "127.0.0.1", "port": 1},
        ]
        results = {}
        pool = QThreadPool()
        job = AsyncBatchPingJob(
            servers,
            callback=lambda i, ms: results.__setitem__(i, ms),
            method="tcp_connect",
            timeout=0.5
        )
        pool.start(job)
        assert pool.waitForDone(6000)
        assert len(results) == 3
        assert results[0] >= 0
        assert results[1] >= 0
        assert results[2] == PING_ERROR_SENTINEL
    finally:
        srv1.close()
        srv2.close()


def test_http_ping_sends_user_agent_header():
    srv = _RawListener()
    try:
        asyncio.run(async_direct_http_ping("127.0.0.1", srv.port, timeout=1.0))
        deadline = time.monotonic() + 1.5
        while srv.data is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert srv.data is not None
        assert b"User-Agent: Mozilla/5.0" in srv.data
        assert b"Accept: */*" in srv.data
    finally:
        srv.close()


class _Socks5RateLimitedServer:
    """SOCKS5 server that returns 429 Too Many Requests."""
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        _ACTIVE_SERVERS.add(self)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                with conn:
                    conn.settimeout(5)
                    greeting = conn.recv(10)
                    if not greeting:
                        continue
                    conn.sendall(b"\x05\x00")
                    req = conn.recv(64)
                    if not req:
                        continue
                    conn.sendall(CONNECT_OK)
                    http_req = conn.recv(1024)
                    conn.sendall(b"HTTP/1.1 429 Too Many Requests\r\nRetry-After: 60\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass

    def __del__(self):
        self.close()

    def close(self):
        _ACTIVE_SERVERS.discard(self)
        try:
            self.sock.close()
        except Exception:
            pass
        if hasattr(self, "thread") and self.thread.is_alive():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.05)
                    s.connect(("127.0.0.1", self.port))
            except Exception:
                pass
            self.thread.join(timeout=0.2)


def test_http_ping_handles_429_rate_limit():
    srv = _Socks5RateLimitedServer()
    try:
        # Since all probe attempts return 429, http ping returns None gracefully
        ms = http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=1.0)
        assert ms is None
    finally:
        srv.close()


def test_async_http_ping_handles_429_rate_limit():
    srv = _Socks5RateLimitedServer()
    try:
        ms = asyncio.run(async_http_ping_via_socks5_once("127.0.0.1", srv.port, timeout=1.0))
        assert ms is None
    finally:
        srv.close()





def test_async_direct_http_ping_mid_request_failure_returns_none():
    """Same as above for the async variant: no fake latency on failure."""
    import utils.ping as ping_mod
    from unittest import mock

    async def _run():
        reader = mock.AsyncMock()
        reader.read.side_effect = OSError("recv broken")
        writer = mock.MagicMock()
        writer.get_extra_info.return_value = None
        writer.write.return_value = None
        writer.drain = mock.AsyncMock()
        writer.wait_closed = mock.AsyncMock()

        async def _fake_open(*args, **kwargs):
            return reader, writer

        with mock.patch.object(ping_mod.asyncio, "open_connection", side_effect=_fake_open):
            return await async_direct_http_ping("127.0.0.1", 80, timeout=0.5)

    assert asyncio.run(_run()) is None


def test_parse_ping_url_variants():
    # Defaults on empty / None / invalid
    assert parse_ping_url(None) == ("connectivitycheck.gstatic.com", "/generate_204", 80, False)
    assert parse_ping_url("") == ("connectivitycheck.gstatic.com", "/generate_204", 80, False)
    assert parse_ping_url("   ") == ("connectivitycheck.gstatic.com", "/generate_204", 80, False)

    # HTTP URL with path
    host, path, port, is_ssl = parse_ping_url("http://cp.cloudflare.com/generate_204")
    assert host == "cp.cloudflare.com"
    assert path == "/generate_204"
    assert port == 80
    assert is_ssl is False

    # HTTPS URL with path
    host, path, port, is_ssl = parse_ping_url("https://connectivitycheck.gstatic.com/generate_204")
    assert host == "connectivitycheck.gstatic.com"
    assert path == "/generate_204"
    assert port == 443
    assert is_ssl is True

    # Custom port and query
    host, path, port, is_ssl = parse_ping_url("https://my-server.net:8443/ping?token=123")
    assert host == "my-server.net"
    assert path == "/ping?token=123"
    assert port == 8443
    assert is_ssl is True

    # Bare domain without scheme
    host, path, port, is_ssl = parse_ping_url("example.com/check")
    assert host == "example.com"
    assert path == "/check"
    assert port == 80
    assert is_ssl is False


def test_proxy_ping_job_honors_timeout_and_url(monkeypatch):
    calls = []
    def fake_ping_via_socks5(host, port, method="http_get", timeout=None, url=None):
        calls.append({"host": host, "port": port, "method": method, "timeout": timeout, "url": url})
        return 42.0

    monkeypatch.setattr("utils.ping.ping_via_socks5", fake_ping_via_socks5)
    results = []
    job = ProxyPingJob("custom.host", 1080, results.append, method="http_head", timeout=5.0, url="http://custom.host/check")
    job.run()

    assert results == [42.0]
    assert len(calls) == 1
    assert calls[0]["host"] == "custom.host"
    assert calls[0]["port"] == 1080
    assert calls[0]["method"] == "http_head"
    assert calls[0]["timeout"] == 5.0
    assert calls[0]["url"] == "http://custom.host/check"


def test_async_batch_ping_job_honors_timeout_and_url(monkeypatch):
    calls = []
    async def fake_async_ping_all(servers, callback=None, method=None, socks5_port=None, concurrency=20, timeout=None, url=None):
        calls.append({"servers": servers, "method": method, "timeout": timeout, "url": url})
        if callback:
            callback(0, 30.0)
        return {0: 30.0}

    monkeypatch.setattr("utils.ping.async_ping_all", fake_async_ping_all)
    results = []
    job = AsyncBatchPingJob(["127.0.0.1"], results.append, method="http_head", timeout=7.0, url="http://check.me/204")
    job.run()

    assert len(calls) == 1
    assert calls[0]["timeout"] == 7.0
    assert calls[0]["url"] == "http://check.me/204"