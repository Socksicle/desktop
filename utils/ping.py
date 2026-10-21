import asyncio
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from PySide6.QtCore import QRunnable
from .thp import (
    Thp,
    ProbeQuality,
    ThpResult,
    _configure_tcp_socket,
    _configure_udp_socket,
    _run_coroutine_sync,
    parse_target_endpoint,
)

log = logging.getLogger(__name__)

# Value reported to the UI when a ping failed; kept distinct from any
# real latency since ms values are always >= 0.
PING_ERROR_SENTINEL = -1.0

# Probe endpoint resolved through the tunnel. It answers with
# HTTP 204 No Content, i.e. the response carries no body at all,
# so header completion is the earliest reliable success signal.
PING_PROBE_HOST = "connectivitycheck.gstatic.com"
PING_PROBE_PATH = "/generate_204"
PING_PROBE_PORT = 80

DEFAULT_PING_METHOD = "thp"
DEFAULT_PING_TIMEOUT = 3.0
PING_TIMEOUTS = {"thp": 2.5, "tcp_connect": 2.0, "http_head": 3.0, "http_get": 3.0}
DEFAULT_PING_CONCURRENCY = 20


def _build_socks5_connect_request(host: str, port: int) -> bytes:
    """Build SOCKS5 CONNECT command request bytes (RFC 1928)."""
    try:
        ip_bytes = socket.inet_aton(host)
        return b"\x05\x01\x00\x01" + ip_bytes + port.to_bytes(2, "big")
    except OSError:
        pass
    try:
        ip6_bytes = socket.inet_pton(socket.AF_INET6, host)
        return b"\x05\x01\x00\x04" + ip6_bytes + port.to_bytes(2, "big")
    except (OSError, AttributeError):
        pass
    host_bytes = host.encode("idna")
    return b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + port.to_bytes(2, "big")


async def _read_socks5_reply(reader: asyncio.StreamReader) -> bool:
    """Read and parse a SOCKS5 server reply header (RFC 1928)."""
    resp = await reader.readexactly(4)
    if len(resp) < 4 or resp[0] != 0x05 or resp[1] != 0x00:
        return False
    atyp = resp[3]
    if atyp == 0x01:  # IPv4
        await reader.readexactly(4 + 2)
    elif atyp == 0x03:  # Domain
        domain_len_b = await reader.readexactly(1)
        domain_len = domain_len_b[0]
        await reader.readexactly(domain_len + 2)
    elif atyp == 0x04:  # IPv6
        await reader.readexactly(16 + 2)
    else:
        return False
    return True


HTTP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_PROBE_TARGETS: list[tuple[str, str, int, bool]] = [
    (PING_PROBE_HOST, PING_PROBE_PATH, PING_PROBE_PORT, False),
    ("www.google.com", "/generate_204", 80, False),
    ("cp.cloudflare.com", "/generate_204", 80, False),
    ("www.msftconnecttest.com", "/connecttest.txt", 80, False),
]
PING_PROBE_TARGETS = [(t[0], t[1]) for t in DEFAULT_PROBE_TARGETS]


def parse_ping_url(url: str | None) -> tuple[str, str, int, bool]:
    """Parse a ping URL into (host, path, port, is_ssl).

    Defaults to (PING_PROBE_HOST, PING_PROBE_PATH, PING_PROBE_PORT, False)
    if url is empty or invalid.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return (PING_PROBE_HOST, PING_PROBE_PATH, PING_PROBE_PORT, False)
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        host = parts.hostname or PING_PROBE_HOST
        is_ssl = parts.scheme.lower() == "https"
        default_port = 443 if is_ssl else 80
        port = parts.port or default_port
        path = parts.path if parts.path else PING_PROBE_PATH
        if parts.query:
            path = f"{path}?{parts.query}"
        return (host, path, port, is_ssl)
    except Exception:
        return (PING_PROBE_HOST, PING_PROBE_PATH, PING_PROBE_PORT, False)


def _build_http_request(host_header: str, path: str, method: str) -> bytes:
    return (
        f"{method} {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        f"User-Agent: {HTTP_USER_AGENT}\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")


def _http_probe_succeeded(buffer: bytes) -> bool | None:
    """Return True/False once headers are complete, or None while incomplete."""
    if not (buffer.startswith(b"HTTP/") and b"\r\n\r\n" in buffer):
        return None
    status_line = buffer.split(b"\r\n", 1)[0]
    parts = status_line.split(b" ")
    if len(parts) >= 2:
        try:
            code = int(parts[1])
        except ValueError:
            return True
        if code == 429:
            return False
    return True


def http_ping_via_socks5_once(host, socks5_port, timeout=3, method="GET", url=None):
    """Measure round-trip latency through a local SOCKS5 proxy."""
    return _run_coroutine_sync(
        async_http_ping_via_socks5_once(
            host, socks5_port, timeout=timeout, method=method, url=url))


def tcp_connect_ping_via_socks5(host, socks5_port, timeout=2, port=PING_PROBE_PORT):
    """Measure latency until the SOCKS5 CONNECT reply (one remote RTT)."""
    return _run_coroutine_sync(
        async_tcp_connect_ping_via_socks5(
            host, socks5_port, timeout=timeout, port=port))


def ping_via_socks5(host, socks5_port, method="http_get", timeout=None, url=None):
    """Dispatch active-ping by method: http_get | http_head | tcp_connect."""
    if method not in ("http_get", "http_head", "tcp_connect"):
        method = "http_get"
    if timeout is None:
        timeout = PING_TIMEOUTS.get(method, 3.0)
    if method == "tcp_connect":
        target_host = host
        target_port = PING_PROBE_PORT
        if url:
            u_host, _, u_port, _ = parse_ping_url(url)
            if u_host:
                target_host = u_host
                target_port = u_port
        return tcp_connect_ping_via_socks5(target_host, socks5_port, timeout, port=target_port)
    return http_ping_via_socks5_once(
        host, socks5_port, timeout=timeout,
        method="HEAD" if method == "http_head" else "GET",
        url=url)


def socks5_proxy_ready(port, timeout=0.08):
    """Verify a local SOCKS5 proxy accepts a handshake (lightweight probe)."""
    try:
        s = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout)
        _configure_tcp_socket(s)
        with s:
            s.sendall(b"\x05\x01\x00")
            return s.recv(2) == b"\x05\x00"
    except (socket.error, OSError, ConnectionRefusedError, TimeoutError) as e:
        log.debug("SOCKS5 proxy probe on port %s failed: %s", port, e)
        return False





class _QuicPingProtocol(asyncio.DatagramProtocol):
    """UDP protocol helper for async QUIC initial probing."""

    def __init__(self):
        self.future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr):
        if not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc):
        if not self.future.done():
            self.future.set_exception(exc)

    def connection_lost(self, exc):
        if not self.future.done():
            self.future.set_result(None)


async def async_direct_quic_ping(host: str, port: int, timeout: float = 2.0) -> float | None:
    """Direct QUIC/UDP ping asynchronously."""
    loop = asyncio.get_running_loop()
    transport = None
    try:
        dcid = os.urandom(8)
        scid = os.urandom(8)
        header = b"\xc0\x0a\x0a\x0a\x0a\x08" + dcid + b"\x08" + scid + b"\x00\x44\xb0"
        packet = header.ljust(1200, b"\x00")

        proto = _QuicPingProtocol()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: proto,
            remote_addr=(host, int(port))
        )
        sock = transport.get_extra_info("socket")
        if sock:
            _configure_udp_socket(sock)

        start = time.monotonic()
        transport.sendto(packet)
        data = await asyncio.wait_for(proto.future, timeout=timeout)
        if data:
            return (time.monotonic() - start) * 1000
    except Exception as e:
        log.debug("Async direct QUIC ping to %s:%s failed: %s", host, port, e)
    finally:
        if transport:
            transport.close()
    return None


async def async_direct_tcp_ping(host: str, port: int, timeout: float = 2.0) -> float | None:
    """Direct non-blocking TCP ping to server IP/Port asynchronously."""
    try:
        res = await Thp.async_probe(host, int(port), timeout_sec=timeout, attempts=1)
        if res.quality != ProbeQuality.FAILED and res.transport_rtt_ms >= 0:
            return res.transport_rtt_ms
        return None
    except Exception as e:
        log.debug("Async direct TCP ping to %s:%s failed: %s", host, port, e)
        return None


async def async_direct_http_ping(
    host: str,
    port: int,
    timeout: float = 3.0,
    method: str = "GET",
    path: str = "/generate_204",
) -> float | None:
    """Direct HTTP GET/HEAD to server's real host:port asynchronously."""
    method = "GET" if method.upper() not in ("GET", "HEAD") else method.upper()
    start = time.monotonic()
    writer = None
    try:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)),
                timeout=timeout
            )
        except (OSError, asyncio.TimeoutError) as e:
            log.debug("Async direct HTTP ping to %s:%s failed: %s", host, port, e)
            return None
        except Exception as e:
            log.debug("Async direct HTTP ping to %s:%s unexpected failure: %s", host, port, e)
            return None

        sock = writer.get_extra_info("socket")
        if sock:
            _configure_tcp_socket(sock)

        try:
            writer.write(_build_http_request(host, path, method))
            await writer.drain()

            buffer = b""
            while True:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                if not chunk:
                    break
                buffer += chunk
                if b"\r\n\r\n" in buffer:
                    return (time.monotonic() - start) * 1000
        except (OSError, asyncio.TimeoutError, RuntimeError) as e:
            log.debug("Async direct HTTP ping to %s:%s failed mid-request: %s", host, port, e)
            return None
        return (time.monotonic() - start) * 1000
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass


async def async_socks5_proxy_ready(port: int, timeout: float = 0.5) -> bool:
    """Verify a local SOCKS5 proxy accepts a handshake asynchronously."""
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", int(port)),
            timeout=timeout
        )
        sock = writer.get_extra_info("socket")
        if sock:
            _configure_tcp_socket(sock)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        resp = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        return resp == b"\x05\x00"
    except Exception as e:
        log.debug("Async SOCKS5 proxy probe on port %s failed: %s", port, e)
        return False
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass


async def async_http_ping_via_socks5_once(
    host: str,
    socks5_port: int,
    timeout: float = 3.0,
    method: str = "GET",
    url: str | None = None,
) -> float | None:
    """Measure round-trip latency through a local SOCKS5 proxy asynchronously."""
    method = "GET" if method.upper() not in ("GET", "HEAD") else method.upper()
    if url:
        custom_target = parse_ping_url(url)
        targets = [custom_target]
        for t in DEFAULT_PROBE_TARGETS:
            if t != custom_target:
                targets.append(t)
    else:
        targets = DEFAULT_PROBE_TARGETS

    for probe_host, probe_path, probe_port, is_ssl in targets:
        start = time.monotonic()
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", int(socks5_port)),
                timeout=timeout
            )
            sock = writer.get_extra_info("socket")
            if sock:
                _configure_tcp_socket(sock)

            # 1. Greeting
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            auth_resp = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
            if auth_resp != b"\x05\x00":
                continue

            # 2. Connect
            req = _build_socks5_connect_request(probe_host, probe_port)
            writer.write(req)
            await writer.drain()
            if not await asyncio.wait_for(_read_socks5_reply(reader), timeout=timeout):
                continue

            # 2.5 TLS handshake if needed
            if is_ssl:
                loop = asyncio.get_running_loop()
                ctx = ssl.create_default_context()
                tls_transport = await asyncio.wait_for(
                    loop.start_tls(writer.transport, writer._protocol, ctx, server_hostname=probe_host),
                    timeout=timeout,
                )
                writer._transport = tls_transport

            # 3. HTTP probe
            writer.write(_build_http_request(probe_host, probe_path, method))
            await writer.drain()

            buffer = b""
            success = False
            for _ in range(64):
                chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                if not chunk:
                    break
                buffer += chunk
                verdict = _http_probe_succeeded(buffer)
                if verdict is not None:
                    if not verdict:
                        log.debug("Async HTTP probe to %s returned 429 rate limit", probe_host)
                    success = verdict
                    break
            if success:
                return (time.monotonic() - start) * 1000
        except Exception as e:
            log.debug("Async SOCKS5 ping to %s via %s failed: %s", host, probe_host, e)
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, RuntimeError):
                    pass
    return None


async def async_tcp_connect_ping_via_socks5(
    host: str,
    socks5_port: int,
    timeout: float = 2.0,
    port: int = PING_PROBE_PORT,
) -> float | None:
    """Measure latency until the SOCKS5 CONNECT reply asynchronously."""
    start = time.monotonic()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", int(socks5_port)),
            timeout=timeout
        )
        sock = writer.get_extra_info("socket")
        if sock:
            _configure_tcp_socket(sock)

        # 1. Greeting
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        auth_resp = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        if auth_resp != b"\x05\x00":
            return None

        # 2. Connect
        req = _build_socks5_connect_request(host, port)
        writer.write(req)
        await writer.drain()
        if not await asyncio.wait_for(_read_socks5_reply(reader), timeout=timeout):
            return None

        return (time.monotonic() - start) * 1000
    except Exception as e:
        log.debug("Async SOCKS5 TCP ping to %s failed: %s", host, e)
        return None
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass





async def async_ping_server_job(
    index: int,
    host: str,
    port: int,
    method: str = DEFAULT_PING_METHOD,
    socks5_port: int | None = None,
    protocol: Any = None,
    timeout: float | None = None,
    url: str | None = None,
) -> tuple[int, float]:
    """Asynchronously probe a single server and return (index, latency_ms or SENTINEL)."""
    try:
        proto_val = getattr(protocol, "value", str(protocol or "")).lower()
        is_quic_proto = proto_val == "hysteria2"
        eff_timeout = float(timeout) if timeout is not None else PING_TIMEOUTS.get(method, 3.0)

        if is_quic_proto:
            # 1. If SOCKS5 is connected, test HTTP through tunnel
            if socks5_port is not None and await async_socks5_proxy_ready(socks5_port, timeout=0.5):
                ms = await async_http_ping_via_socks5_once(
                    host, socks5_port,
                    timeout=eff_timeout,
                    method="HEAD" if method == "http_head" else "GET",
                    url=url,
                )
            else:
                ms = None
            # 2. Try direct QUIC Initial probe (UDP)
            if ms is None:
                ms = await async_direct_quic_ping(host, port, timeout=eff_timeout)
            # 3. Fallback to TCP if port is also open
            if ms is None:
                ms = await async_direct_tcp_ping(host, port, timeout=min(eff_timeout, 1.5))
        elif method == "thp":
            _, probe_path, _, _ = parse_ping_url(url)
            res = await Thp.async_probe(host, int(port), timeout_sec=eff_timeout, probe_path=probe_path)
            ms = float(res.thp_score_ms) if res.quality != ProbeQuality.FAILED else None
        elif method == "tcp_connect":
            if socks5_port is not None and await async_socks5_proxy_ready(socks5_port, timeout=0.5):
                _, _, probe_port, _ = parse_ping_url(url)
                ms = await async_tcp_connect_ping_via_socks5(
                    host, socks5_port, timeout=eff_timeout, port=probe_port)
            else:
                ms = await async_direct_tcp_ping(host, port, timeout=eff_timeout)
        elif socks5_port is not None and await async_socks5_proxy_ready(socks5_port, timeout=0.5):
            ms = await async_http_ping_via_socks5_once(
                host, socks5_port,
                timeout=eff_timeout,
                method="HEAD" if method == "http_head" else "GET",
                url=url,
            )
        else:
            _, probe_path, _, _ = parse_ping_url(url)
            ms = await async_direct_http_ping(
                host, port,
                timeout=eff_timeout,
                method="HEAD" if method == "http_head" else "GET",
                path=probe_path,
            )
    except Exception as e:
        log.debug("Async ping server (%s:%s) error: %s", host, port, e)
        ms = None

    return index, (ms if ms is not None else PING_ERROR_SENTINEL)


def _extract_server_info(srv: Any) -> tuple[str, int, Any]:
    """Extract (host, port, protocol) from Server object, dict, or tuple."""
    return parse_target_endpoint(srv, default_port=80)


async def async_ping_all(
    servers: list,
    callback: Callable[[int, float], None] | None = None,
    method: str = DEFAULT_PING_METHOD,
    socks5_port: int | None = None,
    concurrency: int = DEFAULT_PING_CONCURRENCY,
    timeout: float | None = None,
    url: str | None = None,
) -> dict[int, float]:
    """Concurrently ping a list of servers with bounded concurrency via asyncio.

    Calls callback(index, latency_ms) as each probe finishes (or returns SENTINEL on failure).
    Returns a mapping of index -> latency_ms.
    """
    results: dict[int, float] = {}
    if not servers:
        return results

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _ping_one(idx: int, srv: Any):
        host, port, protocol = _extract_server_info(srv)
        if not host:
            ms = PING_ERROR_SENTINEL
        else:
            async with sem:
                _, ms = await async_ping_server_job(
                    idx, host, port,
                    method=method,
                    socks5_port=socks5_port,
                    protocol=protocol,
                    timeout=timeout,
                    url=url,
                )
        results[idx] = ms
        if callback is not None:
            try:
                callback(idx, ms)
            except Exception as cb_err:
                log.debug("Async ping callback error at index %d: %s", idx, cb_err)

    tasks = [asyncio.create_task(_ping_one(i, s)) for i, s in enumerate(servers)]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results


class AsyncBatchPingJob(QRunnable):
    """Batch ping multiple servers concurrently using asyncio inside QThreadPool."""

    def __init__(self, servers, callback, method=DEFAULT_PING_METHOD,
                 socks5_port=None, concurrency=DEFAULT_PING_CONCURRENCY,
                 timeout=None, url=None):
        super().__init__()
        self.servers = list(servers)
        self.callback = callback
        self.method = method
        self.socks5_port = socks5_port
        self.concurrency = concurrency
        self.timeout = timeout
        self.url = url

    def run(self):
        if not self.servers:
            return
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    async_ping_all(
                        self.servers,
                        callback=self.callback,
                        method=self.method,
                        socks5_port=self.socks5_port,
                        concurrency=self.concurrency,
                        timeout=self.timeout,
                        url=self.url,
                    )
                )
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                finally:
                    loop.close()
        except Exception as e:
            log.debug("AsyncBatchPingJob run failed: %s", e)


class ProxyPingJob(QRunnable):
    """Ping the active connection through its local SOCKS5 proxy off the GUI thread."""

    def __init__(self, host, port, callback, method=DEFAULT_PING_METHOD, timeout=None, url=None):
        super().__init__()
        self.host = host
        self.port = port
        self.callback = callback
        self.method = method
        self.timeout = timeout
        self.url = url

    def run(self):
        try:
            ms = ping_via_socks5(self.host, self.port, self.method, timeout=self.timeout, url=self.url)
        except Exception as e:
            log.debug("Proxy ping on port %s raised: %s", self.port, e)
            ms = None
        self.callback(ms)