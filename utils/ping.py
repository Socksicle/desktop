import asyncio
import logging
import os
import re
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Iterable

import socks
from PySide6.QtCore import QRunnable
from .thp import Thp, ProbeQuality, ThpResult

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
PING_TIMEOUTS = {"thp": 2.5, "tcp_connect": 2.0, "http_head": 3.0, "http_get": 3.0}
# Max concurrent probes in a batch ping. Each THP probe holds a TCP socket +
# a TLS session + an HTTP exchange, so 20 (instead of 50) halves the peak
# socket/TLS/memory pressure; the full-list sweep just takes ~2x longer.
DEFAULT_PING_CONCURRENCY = 20


def _configure_tcp_socket(sock: socket.socket) -> None:
    """Apply low-latency socket optimizations (TCP_NODELAY, SO_REUSEADDR)."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (OSError, AttributeError):
        pass


def _configure_udp_socket(sock: socket.socket) -> None:
    """Apply socket optimizations for UDP/QUIC datagrams."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (OSError, AttributeError):
        pass


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
PING_PROBE_TARGETS = [
    (PING_PROBE_HOST, PING_PROBE_PATH),
    ("www.google.com", "/generate_204"),
    ("cp.cloudflare.com", "/generate_204"),
    ("www.msftconnecttest.com", "/connecttest.txt"),
]


def _http_probe_target(host_header, path, s, method, timeout):
    req = (
        f"{method} {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        f"User-Agent: {HTTP_USER_AGENT}\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    s.sendall(req)
    buffer = b""
    for _ in range(64):
        chunk = s.recv(1024)
        if not chunk:
            break
        buffer += chunk
        if buffer.startswith(b"HTTP/") and b"\r\n\r\n" in buffer:
            status_line = buffer.split(b"\r\n", 1)[0]
            parts = status_line.split(b" ")
            if len(parts) >= 2:
                try:
                    code = int(parts[1])
                    if code == 429:
                        log.debug("HTTP probe to %s returned 429 rate limit", host_header)
                        return False
                    if 200 <= code < 400 or code == 204:
                        return True
                except ValueError:
                    pass
            return True
    return False


def http_ping_via_socks5_once(host, socks5_port, timeout=3, method="GET"):
    """Measure round-trip latency through a local SOCKS5 proxy."""
    method = "GET" if method.upper() not in ("GET", "HEAD") else method.upper()
    targets = PING_PROBE_TARGETS
    for probe_host, probe_path in targets:
        s = socks.socksocket()
        _configure_tcp_socket(s)
        s.set_proxy(socks.SOCKS5, "127.0.0.1", socks5_port)
        s.settimeout(timeout)
        try:
            start = time.monotonic()
            s.connect((probe_host, PING_PROBE_PORT))
            if _http_probe_target(probe_host, probe_path, s, method, timeout):
                return (time.monotonic() - start) * 1000
        except (socket.error, OSError, socks.SOCKS5Error, TimeoutError) as e:
            log.debug("SOCKS5 ping to %s via %s failed: %s", host, probe_host, e)
        except Exception as e:
            log.debug("SOCKS5 ping to %s via %s unexpected failure: %s", host, probe_host, e)
        finally:
            s.close()
    return None


def tcp_connect_ping_via_socks5(host, socks5_port, timeout=2):
    """Measure latency until the SOCKS5 CONNECT reply (one remote RTT)."""
    s = socks.socksocket()
    _configure_tcp_socket(s)
    s.set_proxy(socks.SOCKS5, "127.0.0.1", socks5_port)
    s.settimeout(timeout)
    try:
        start = time.monotonic()
        s.connect((host, PING_PROBE_PORT))
        return (time.monotonic() - start) * 1000
    except (socket.error, OSError, socks.SOCKS5Error, TimeoutError) as e:
        log.debug("SOCKS5 TCP ping to %s failed: %s", host, e)
        return None
    except Exception as e:
        log.debug("SOCKS5 TCP ping to %s unexpected failure: %s", host, e)
        return None
    finally:
        s.close()


def ping_via_socks5(host, socks5_port, method="http_get", timeout=None):
    """Dispatch active-ping by method: http_get | http_head | tcp_connect."""
    if method not in ("http_get", "http_head", "tcp_connect"):
        method = "http_get"
    if timeout is None:
        timeout = PING_TIMEOUTS.get(method, 3.0)
    if method == "tcp_connect":
        return tcp_connect_ping_via_socks5(host, socks5_port, timeout)
    return http_ping_via_socks5_once(
        host, socks5_port, timeout=timeout,
        method="HEAD" if method == "http_head" else "GET")


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
    start = time.monotonic()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)),
            timeout=timeout
        )
        sock = writer.get_extra_info("socket")
        if sock:
            _configure_tcp_socket(sock)
        return (time.monotonic() - start) * 1000
    except Exception as e:
        log.debug("Async direct TCP ping to %s:%s failed: %s", host, port, e)
        return None
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass


async def async_direct_http_ping(
    host: str,
    port: int,
    timeout: float = 3.0,
    method: str = "GET"
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
            req = (
                f"{method} /generate_204 HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {HTTP_USER_AGENT}\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(req)
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
    method: str = "GET"
) -> float | None:
    """Measure round-trip latency through a local SOCKS5 proxy asynchronously."""
    method = "GET" if method.upper() not in ("GET", "HEAD") else method.upper()
    targets = PING_PROBE_TARGETS
    for probe_host, probe_path in targets:
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
            req = _build_socks5_connect_request(probe_host, PING_PROBE_PORT)
            writer.write(req)
            await writer.drain()
            if not await asyncio.wait_for(_read_socks5_reply(reader), timeout=timeout):
                continue

            # 3. HTTP probe
            http_req = (
                f"{method} {probe_path} HTTP/1.1\r\n"
                f"Host: {probe_host}\r\n"
                f"User-Agent: {HTTP_USER_AGENT}\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(http_req)
            await writer.drain()

            buffer = b""
            success = False
            for _ in range(64):
                chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                if not chunk:
                    break
                buffer += chunk
                if buffer.startswith(b"HTTP/") and b"\r\n\r\n" in buffer:
                    status_line = buffer.split(b"\r\n", 1)[0]
                    parts = status_line.split(b" ")
                    if len(parts) >= 2:
                        try:
                            code = int(parts[1])
                            if code == 429:
                                log.debug("Async HTTP probe to %s returned 429 rate limit", probe_host)
                                success = False
                                break
                            if 200 <= code < 400 or code == 204:
                                success = True
                                break
                        except ValueError:
                            pass
                    success = True
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
    timeout: float = 2.0
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
        req = _build_socks5_connect_request(host, PING_PROBE_PORT)
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
    timeout: float | None = None
) -> tuple[int, float]:
    """Asynchronously probe a single server and return (index, latency_ms or SENTINEL)."""
    try:
        proto_val = getattr(protocol, "value", str(protocol or "")).lower()
        is_quic_proto = proto_val == "hysteria2"
        eff_timeout = timeout or PING_TIMEOUTS.get(method, 3.0)

        if is_quic_proto:
            # 1. If SOCKS5 is connected, test HTTP through tunnel
            if socks5_port is not None and await async_socks5_proxy_ready(socks5_port, timeout=0.5):
                ms = await async_http_ping_via_socks5_once(
                    host, socks5_port,
                    timeout=eff_timeout,
                    method="HEAD" if method == "http_head" else "GET"
                )
            else:
                ms = None
            # 2. Try direct QUIC Initial probe (UDP)
            if ms is None:
                ms = await async_direct_quic_ping(host, port, timeout=2.0)
            # 3. Fallback to TCP if port is also open
            if ms is None:
                ms = await async_direct_tcp_ping(host, port, timeout=1.5)
        elif method == "thp":
            res = await Thp.async_probe(host, int(port), timeout_sec=eff_timeout)
            ms = float(res.thp_score_ms) if res.quality != ProbeQuality.FAILED else None
        elif method == "tcp_connect":
            if socks5_port is not None and await async_socks5_proxy_ready(socks5_port, timeout=0.5):
                ms = await async_tcp_connect_ping_via_socks5(host, socks5_port, timeout=eff_timeout)
            else:
                ms = await async_direct_tcp_ping(host, port, timeout=eff_timeout)
        elif socks5_port is not None and await async_socks5_proxy_ready(socks5_port, timeout=0.5):
            ms = await async_http_ping_via_socks5_once(
                host, socks5_port,
                timeout=eff_timeout,
                method="HEAD" if method == "http_head" else "GET"
            )
        else:
            ms = await async_direct_http_ping(
                host, port,
                timeout=eff_timeout,
                method="HEAD" if method == "http_head" else "GET"
            )
    except Exception as e:
        log.debug("Async ping server (%s:%s) error: %s", host, port, e)
        ms = None

    return index, (ms if ms is not None else PING_ERROR_SENTINEL)


def _extract_server_info(srv: Any) -> tuple[str, int, Any]:
    """Extract (host, port, protocol) from Server object, dict, or tuple."""
    if hasattr(srv, "host"):
        host = getattr(srv, "host", "") or ""
        port = getattr(srv, "port", 80)
        protocol = getattr(srv, "protocol", None)
    elif isinstance(srv, dict):
        host = srv.get("host", "")
        port = srv.get("port", 80)
        protocol = srv.get("protocol", None)
    elif isinstance(srv, (list, tuple)):
        host = srv[0] if len(srv) > 0 else ""
        port = srv[1] if len(srv) > 1 else 80
        protocol = srv[2] if len(srv) > 2 else None
    else:
        host = str(srv)
        port = 80
        protocol = None
    try:
        port = int(port)
    except (ValueError, TypeError):
        port = 80
    return str(host), port, protocol


async def async_ping_all(
    servers: list,
    callback: Callable[[int, float], None] | None = None,
    method: str = DEFAULT_PING_METHOD,
    socks5_port: int | None = None,
    concurrency: int = DEFAULT_PING_CONCURRENCY,
    timeout: float | None = None
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
                 timeout=None):
        super().__init__()
        self.servers = list(servers)
        self.callback = callback
        self.method = method
        self.socks5_port = socks5_port
        self.concurrency = concurrency
        self.timeout = timeout

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
                        timeout=self.timeout
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

    def __init__(self, host, port, callback, method=DEFAULT_PING_METHOD):
        super().__init__()
        self.host = host
        self.port = port
        self.callback = callback
        self.method = method

    def run(self):
        try:
            ms = ping_via_socks5(self.host, self.port, self.method)
        except Exception as e:
            log.debug("Proxy ping on port %s raised: %s", self.port, e)
            ms = None
        self.callback(ms)