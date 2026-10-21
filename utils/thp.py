import asyncio
import concurrent.futures
import logging
import socket
import statistics
import time
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Union

log = logging.getLogger(__name__)

TargetType = Union[str, tuple[str, int], Any]


def _run_coroutine_sync(coro):
    """Run a coroutine from synchronous code, even inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


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


def parse_target_endpoint(target: TargetType, default_port: int = 80) -> tuple[str, int, Optional[str]]:
    """Extract (host, port, protocol) from Server object, dict, tuple, or string."""
    protocol = None
    if hasattr(target, "host"):
        host = getattr(target, "host", "") or ""
        port = getattr(target, "port", default_port)
        protocol = getattr(target, "protocol", None)
    elif isinstance(target, dict):
        host = target.get("host", "")
        port = target.get("port", default_port)
        protocol = target.get("protocol", None)
    elif isinstance(target, (list, tuple)):
        host = target[0] if len(target) > 0 else ""
        port = target[1] if len(target) > 1 else default_port
        protocol = target[2] if len(target) > 2 else None
    else:
        s = str(target).strip()
        if s.startswith("[") and "]:" in s:
            h, p = s.rsplit(":", 1)
            host = h
            port = int(p) if p.isdigit() else default_port
        elif s.count(":") == 1:
            h, p = s.split(":", 1)
            host = h
            port = int(p) if p.isdigit() else default_port
        else:
            host = s
            port = default_port

    try:
        port = int(port)
    except (ValueError, TypeError):
        port = default_port
    return str(host), port, protocol


class ProbeQuality(str, Enum):
    OK = "OK"
    UNSTABLE = "UNSTABLE"
    FAILED = "FAILED"
    # Backward compatibility aliases
    VERIFIED = "OK"
    REACHABLE = "UNSTABLE"


@dataclass(slots=True)
class ThpResult:
    host: str
    port: int
    transport_rtt_ms: float = -1.0
    median_ms: float = -1.0
    success_rate: float = 0.0
    thp_score_ms: int = -1
    quality: ProbeQuality = ProbeQuality.FAILED
    error_message: Optional[str] = None
    target: Any = None

    @property
    def status(self) -> ProbeQuality:
        return self.quality


class Thp:
    DNS_RESOLVE_CAP_SEC = 0.35
    TCP_CONNECT_CAP_SEC = 2.0
    PROBE_ATTEMPTS = 3
    DNS_MAX_WORKERS = 16

    _dns_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
    _loop_sems: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()

    @classmethod
    def _get_dns_executor(cls) -> concurrent.futures.ThreadPoolExecutor:
        if cls._dns_executor is None:
            cls._dns_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=cls.DNS_MAX_WORKERS,
                thread_name_prefix="thp-dns",
            )
        return cls._dns_executor

    @classmethod
    def _get_async_dns_sem(cls) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = cls._loop_sems.get(loop)
        if sem is None:
            sem = asyncio.Semaphore(cls.DNS_MAX_WORKERS)
            cls._loop_sems[loop] = sem
        return sem

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
    def _extract_ips(addrs: list) -> list[tuple[int, str]]:
        v4: list[tuple[int, str]] = []
        v6: list[tuple[int, str]] = []
        seen: set[str] = set()
        for family, _, _, _, sockaddr in addrs:
            ip = sockaddr[0]
            if ip in seen:
                continue
            seen.add(ip)
            if family == socket.AF_INET:
                v4.append((family, ip))
            elif family == socket.AF_INET6:
                v6.append((family, ip))
        return v4 + v6 if v4 else v6

    @staticmethod
    def _parse_target(target: TargetType, default_port: int) -> tuple[str, int]:
        h, p, _ = parse_target_endpoint(target, default_port)
        return h, p

    @staticmethod
    def _sort_key(res: ThpResult) -> tuple[int, float]:
        med = res.median_ms if res.median_ms > 0 else (float(res.thp_score_ms) if res.thp_score_ms > 0 else float("inf"))
        if res.quality == ProbeQuality.OK:
            return (0, med)
        if res.quality == ProbeQuality.UNSTABLE:
            return (1, med)
        return (2, float("inf"))

    @classmethod
    def _calc_result(
        cls,
        host: str,
        port: int,
        rtts: list[float],
        attempts: int,
        last_error: Optional[str],
    ) -> ThpResult:
        success_count = len(rtts)
        success_rate = (success_count / float(attempts)) if attempts > 0 else 0.0

        if success_count == attempts:
            quality, median_rtt, err = ProbeQuality.OK, float(statistics.median(rtts)), None
        elif success_count > 0:
            quality, median_rtt, err = ProbeQuality.UNSTABLE, float(statistics.median(rtts)), last_error
        else:
            quality, median_rtt, err = ProbeQuality.FAILED, -1.0, last_error or "All attempts failed"

        score = max(1, round(median_rtt)) if median_rtt > 0 else -1
        return ThpResult(
            host=host,
            port=port,
            transport_rtt_ms=median_rtt,
            median_ms=median_rtt,
            success_rate=success_rate,
            thp_score_ms=score,
            quality=quality,
            error_message=err,
        )

    @classmethod
    def probe(
        cls,
        host: str,
        port: int = 443,
        timeout_sec: float = 2.5,
        attempts: Optional[int] = None,
        **kwargs: Any,
    ) -> ThpResult:
        """Synchronous wrapper around :meth:`async_probe`."""
        return _run_coroutine_sync(
            cls.async_probe(
                host, port, timeout_sec=timeout_sec,
                attempts=attempts, **kwargs))

    @classmethod
    async def async_probe(
        cls,
        host: str,
        port: int = 443,
        timeout_sec: float = 2.5,
        attempts: Optional[int] = None,
        **_kwargs: Any,
    ) -> ThpResult:
        num_attempts = max(1, attempts if attempts is not None else cls.PROBE_ATTEMPTS)
        start_overall = time.perf_counter()
        deadline = start_overall + timeout_sec
        clean_host = host.strip().strip("[]")

        if cls.is_ip_literal(clean_host):
            fam = socket.AF_INET6 if ":" in clean_host else socket.AF_INET
            candidates = [(fam, clean_host)]
        else:
            rem_dns_wait = deadline - time.perf_counter()
            if rem_dns_wait <= 0:
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="Timeout before DNS")
            sem = cls._get_async_dns_sem()
            try:
                await asyncio.wait_for(sem.acquire(), timeout=max(0.01, rem_dns_wait))
            except (asyncio.TimeoutError, TimeoutError):
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="Timeout waiting for DNS slot")

            loop = asyncio.get_running_loop()
            fut = cls._get_dns_executor().submit(
                socket.getaddrinfo, clean_host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            )

            def _release(_f: Any) -> None:
                if not loop.is_closed():
                    try:
                        loop.call_soon_threadsafe(sem.release)
                    except RuntimeError:
                        pass

            fut.add_done_callback(_release)

            try:
                rem_dns = deadline - time.perf_counter()
                if rem_dns <= 0:
                    return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="Timeout before DNS")
                dns_timeout = min(max(0.05, rem_dns), cls.DNS_RESOLVE_CAP_SEC)
                addrs = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=dns_timeout)
                candidates = cls._extract_ips(addrs)
                if not candidates:
                    return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="No IP addresses resolved")
            except (asyncio.TimeoutError, TimeoutError):
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message="DNS timeout")
            except Exception as e:
                return ThpResult(host, port, quality=ProbeQuality.FAILED, error_message=f"DNS failed: {e}")

        active_candidates = list(candidates)
        rtts: list[float] = []
        last_error: Optional[str] = None

        for _ in range(num_attempts):
            if deadline - time.perf_counter() <= 0:
                last_error = last_error or "Timeout before attempt"
                break

            for idx, (_fam, ip_addr) in enumerate(active_candidates):
                rem_conn = deadline - time.perf_counter()
                if rem_conn <= 0:
                    last_error = "Timeout"
                    break
                rem_cands = len(active_candidates) - idx
                cand_cap = 0.5 if rem_cands > 1 else cls.TCP_CONNECT_CAP_SEC
                t0 = time.perf_counter()
                try:
                    _reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip_addr, port),
                        timeout=min(rem_conn, cand_cap),
                    )
                    rtts.append((time.perf_counter() - t0) * 1000.0)
                    sock = writer.get_extra_info("socket")
                    if sock is not None:
                        _configure_tcp_socket(sock)
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), timeout=0.05)
                    except Exception:
                        pass
                    if idx > 0:
                        active_candidates.insert(0, active_candidates.pop(idx))
                    break
                except Exception as e:
                    last_error = str(e)

        return cls._calc_result(host, port, rtts, num_attempts, last_error)

    @classmethod
    async def async_probe_many(
        cls,
        targets: Iterable[TargetType],
        default_port: int = 443,
        timeout_sec: float = 2.5,
        concurrency: int = 64,
        sort: bool = True,
        **kwargs: Any,
    ) -> list[ThpResult]:
        sem = asyncio.Semaphore(max(1, concurrency))
        target_list = list(targets)
        parsed = [cls._parse_target(t, default_port) for t in target_list]

        async def _worker(t_raw: Any, h: str, p: int) -> ThpResult:
            async with sem:
                res = await cls.async_probe(h, p, timeout_sec=timeout_sec, **kwargs)
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
        concurrency: int = 64,
        sort: bool = True,
        **kwargs: Any,
    ) -> list[ThpResult]:
        """Synchronous wrapper around :meth:`async_probe_many`."""
        return _run_coroutine_sync(
            cls.async_probe_many(
                targets=targets,
                default_port=default_port,
                timeout_sec=timeout_sec,
                concurrency=concurrency,
                sort=sort,
                **kwargs,
            ))