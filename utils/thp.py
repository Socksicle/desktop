import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Union

log = logging.getLogger(__name__)

TargetType = Union[str, tuple[str, int], Any]


class ProbeQuality(str, Enum):
    VERIFIED = "VERIFIED"
    REACHABLE = "REACHABLE"
    FAILED = "FAILED"


@dataclass(slots=True)
class ThpResult:
    host: str
    port: int
    transport_rtt_ms: float = -1.0
    http_ttfb_ms: float = -1.0
    thp_score_ms: int = -1
    status_code: int = 0
    quality: ProbeQuality = ProbeQuality.FAILED
    error_message: Optional[str] = None
    target: Any = None


class Thp:
    DNS_RESOLVE_CAP_SEC = 0.35
    TCP_CONNECT_CAP_SEC = 2.0
    HTTP_READ_CAP_SEC = 0.35

    @staticmethod
    def is_ip_literal(host: str) -> bool:
        clean = host.strip().strip("[]")
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                socket.inet_pton(family, clean)
                return True
            except OSError:
                pass
        return False

    @staticmethod
    def _pick_ip(addrs: list) -> str:
        for family, _, _, _, sockaddr in addrs:
            if family == socket.AF_INET:
                return sockaddr[0]
        return addrs[0][4][0]

    @staticmethod
    def _build_request(clean_host: str, probe_path: str) -> bytes:
        return (
            f"HEAD {probe_path} HTTP/1.1\r\n"
            f"Host: {clean_host}\r\n"
            f"User-Agent: Socksicle-THP/2.0\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii", "ignore")

    @staticmethod
    def _parse_status(buf: bytes) -> int:
        if buf.startswith(b"HTTP/"):
            parts = buf.split(None, 2)
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        return 0

    @staticmethod
    def _parse_target(target: TargetType, default_port: int) -> tuple[str, int]:
        if hasattr(target, "host") and hasattr(target, "port"):
            try:
                return str(target.host), int(target.port)
            except (ValueError, TypeError):
                return str(target.host), default_port
        if isinstance(target, dict):
            try:
                return str(target.get("host", "")), int(target.get("port", default_port))
            except (ValueError, TypeError):
                return str(target.get("host", "")), default_port
        if isinstance(target, (tuple, list)):
            return str(target[0]), int(target[1])
        s = str(target).strip()
        if s.startswith("[") and "]:" in s:
            h, p = s.rsplit(":", 1)
            if p.isdigit():
                return h, int(p)
        elif s.count(":") == 1:
            h, p = s.split(":", 1)
            if p.isdigit():
                return h, int(p)
        return s, default_port

    @staticmethod
    def _sort_key(res: ThpResult) -> tuple[int, int, float, float]:
        if res.quality == ProbeQuality.FAILED or res.thp_score_ms <= 0:
            return (1, 999999, float("inf"), float("inf"))
        q_order = 0 if res.quality == ProbeQuality.VERIFIED else 1
        score = res.thp_score_ms
        rtt = res.transport_rtt_ms if res.transport_rtt_ms >= 0 else float("inf")
        return (0, score, q_order, rtt)

    @classmethod
    def _finalize(
        cls,
        host: str,
        port: int,
        transport_rtt: float,
        http_ttfb: float,
        status_code: int,
        weight_http: float,
    ) -> ThpResult:
        if 100 <= status_code < 500:
            app_part = max(0.0, http_ttfb) * weight_http
            score = max(1, int(transport_rtt + app_part))
            quality = ProbeQuality.VERIFIED
        else:
            score = max(1, int(transport_rtt))
            quality = ProbeQuality.REACHABLE

        return ThpResult(
            host=host,
            port=port,
            transport_rtt_ms=transport_rtt,
            http_ttfb_ms=http_ttfb,
            thp_score_ms=score,
            status_code=status_code,
            quality=quality,
        )

    @classmethod
    def probe(
        cls,
        host: str,
        port: int = 443,
        timeout_sec: float = 2.5,
        weight_http: float = 0.30,
        probe_path: str = "/generate_204",
    ) -> ThpResult:
        start_overall = time.perf_counter()
        deadline = start_overall + timeout_sec
        clean_host = host.strip().strip("[]")

        if cls.is_ip_literal(clean_host):
            ip_addr = clean_host
        else:
            try:
                addrs = socket.getaddrinfo(clean_host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                ip_addr = cls._pick_ip(addrs)
            except (OSError, UnicodeError, ValueError) as e:
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message=f"DNS failed: {e}")

        rem_tcp = deadline - time.perf_counter()
        if rem_tcp <= 0:
            return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="Timeout before TCP")

        family = socket.AF_INET6 if ":" in ip_addr else socket.AF_INET
        status_code = 0
        http_ttfb = -1.0

        with socket.socket(family, socket.SOCK_STREAM) as sock:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                pass
            sock.settimeout(min(rem_tcp, cls.TCP_CONNECT_CAP_SEC))

            start_tcp = time.perf_counter()
            try:
                sock.connect((ip_addr, port))
                transport_rtt = (time.perf_counter() - start_tcp) * 1000.0
            except (OSError, ValueError) as e:
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message=f"TCP connect failed: {e}")

            rem_http = min(max(0.05, deadline - time.perf_counter()), cls.HTTP_READ_CAP_SEC)
            if rem_http > 0:
                try:
                    sock.settimeout(rem_http)
                    start_http = time.perf_counter()
                    sock.sendall(cls._build_request(clean_host, probe_path))
                    buf = sock.recv(128)
                    if buf:
                        http_ttfb = (time.perf_counter() - start_http) * 1000.0
                        status_code = cls._parse_status(buf)
                except OSError:
                    pass

        return cls._finalize(host, port, transport_rtt, http_ttfb, status_code, weight_http)

    @classmethod
    async def async_probe(
        cls,
        host: str,
        port: int = 443,
        timeout_sec: float = 2.5,
        weight_http: float = 0.30,
        probe_path: str = "/generate_204",
    ) -> ThpResult:
        start_overall = time.perf_counter()
        deadline = start_overall + timeout_sec
        clean_host = host.strip().strip("[]")

        if cls.is_ip_literal(clean_host):
            ip_addr = clean_host
        else:
            dns_timeout = min(max(0.05, deadline - time.perf_counter()), cls.DNS_RESOLVE_CAP_SEC)
            loop = asyncio.get_running_loop()
            try:
                addrs = await asyncio.wait_for(
                    loop.getaddrinfo(clean_host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
                    timeout=dns_timeout,
                )
                ip_addr = cls._pick_ip(addrs)
            except Exception as e:
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message=f"DNS failed: {e}")

        rem_tcp = deadline - time.perf_counter()
        if rem_tcp <= 0:
            return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="Timeout before TCP")

        start_tcp = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_addr, port),
                timeout=min(rem_tcp, cls.TCP_CONNECT_CAP_SEC),
            )
            transport_rtt = (time.perf_counter() - start_tcp) * 1000.0
        except Exception as e:
            return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message=f"TCP connect failed: {e}")

        status_code = 0
        http_ttfb = -1.0
        rem_http = min(max(0.05, deadline - time.perf_counter()), cls.HTTP_READ_CAP_SEC)

        try:
            sock = writer.get_extra_info("socket")
            if sock is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except (OSError, AttributeError):
                    pass

            if rem_http > 0:
                start_http = time.perf_counter()
                writer.write(cls._build_request(clean_host, probe_path))
                await asyncio.wait_for(writer.drain(), timeout=rem_http)
                rem_read = max(0.01, rem_http - (time.perf_counter() - start_http))
                buf = await asyncio.wait_for(reader.read(128), timeout=rem_read)
                if buf:
                    http_ttfb = (time.perf_counter() - start_http) * 1000.0
                    status_code = cls._parse_status(buf)
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.05)
            except Exception:
                pass

        return cls._finalize(host, port, transport_rtt, http_ttfb, status_code, weight_http)

    @classmethod
    async def async_probe_many(
        cls,
        targets: Iterable[TargetType],
        default_port: int = 443,
        timeout_sec: float = 2.5,
        weight_http: float = 0.30,
        probe_path: str = "/generate_204",
        concurrency: int = 64,
        sort: bool = True,
    ) -> list[ThpResult]:
        sem = asyncio.Semaphore(max(1, concurrency))
        target_list = list(targets)
        parsed = [cls._parse_target(t, default_port) for t in target_list]

        async def _worker(t_raw: Any, h: str, p: int) -> ThpResult:
            async with sem:
                res = await cls.async_probe(h, p, timeout_sec, weight_http, probe_path)
                res.target = t_raw
                return res

        results = list(await asyncio.gather(*(_worker(t, h, p) for t, (h, p) in zip(target_list, parsed))))
        if sort:
            results.sort(key=cls._sort_key)
        return results

    @classmethod
    def probe_many(
        cls,
        targets: Iterable[TargetType],
        default_port: int = 443,
        timeout_sec: float = 2.5,
        weight_http: float = 0.30,
        probe_path: str = "/generate_204",
        concurrency: int = 64,
        sort: bool = True,
    ) -> list[ThpResult]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    cls.async_probe_many(
                        targets=targets,
                        default_port=default_port,
                        timeout_sec=timeout_sec,
                        weight_http=weight_http,
                        probe_path=probe_path,
                        concurrency=concurrency,
                        sort=sort,
                    ),
                ).result()

        return asyncio.run(
            cls.async_probe_many(
                targets=targets,
                default_port=default_port,
                timeout_sec=timeout_sec,
                weight_http=weight_http,
                probe_path=probe_path,
                concurrency=concurrency,
                sort=sort,
            )
        )