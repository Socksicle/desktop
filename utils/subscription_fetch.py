"""HTTPS fetching for subscriptions: SSRF guard, pinning, and body limits."""
import http.client
import ipaddress
import logging
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .i18n import tr

log = logging.getLogger(__name__)

MAX_SUBSCRIPTION_BYTES = 2 * 1024 * 1024
_READ_CHUNK_SIZE = 64 * 1024
MAX_SUBSCRIPTION_REDIRECTS = 5
MAX_SUBSCRIPTION_NODES = 5000
MAX_SUBSCRIPTION_READ_TIMEOUT = 15.0


class _BlockedSubscription(ValueError):
    """A subscription fetch refused on security grounds; message is user-facing."""


def _resolve_public_ip(host: str) -> str:
    """Resolve once and require every answer to be global; return one IP.

    Raises _BlockedSubscription for literals and non-global answers.
    Raises OSError/UnicodeError when resolution itself fails, so the caller
    can fall back to a default connection and surface the usual DNS error.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not literal.is_global:
            raise _BlockedSubscription(tr("sub.blocked_nonpublic").format(host=host))
        return host
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addrs = []
    for _fam, _type, _proto, _canon, sockaddr in infos:
        try:
            addrs.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            raise _BlockedSubscription(tr("sub.blocked_bad_resolve").format(host=host))
    if any(not addr.is_global for addr in addrs):
        raise _BlockedSubscription(tr("sub.blocked_private").format(host=host))
    return str(addrs[0])


def _require_public_host(host):
    """Reject hosts resolving to non-global IPs (SSRF/DNS-rebinding guard)."""
    try:
        _resolve_public_ip(host)
    except _BlockedSubscription:
        raise
    except (OSError, UnicodeError) as e:
        raise urllib.error.URLError(e)


def _require_public_url(url):
    target = urlparse(url)
    if target.scheme.lower() != "https":
        raise _BlockedSubscription(tr("sub.http_only"))
    host = target.hostname
    if not host:
        raise _BlockedSubscription(tr("sub.empty_host"))
    _require_public_host(host)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to one validated IP (DNS-rebinding guard).

    TCP connects to ``pinned_ip``; TLS SNI/hostname verification and the
    HTTP Host header keep using the original hostname.
    """

    def __init__(self, host, *args, pinned_ip=None, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._pinned_ip = pinned_ip or host

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        server_hostname = self._tunnel_host if self._tunnel_host else self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPS handler resolving once and pinning the TCP connection to that IP."""

    def https_open(self, req):
        hostname = urlparse(req.full_url).hostname or ""
        try:
            pinned_ip = _resolve_public_ip(hostname)
        except _BlockedSubscription:
            raise
        except (OSError, UnicodeError, ValueError) as e:
            raise urllib.error.URLError(e)
        return self.do_open(
            lambda host, **kw: _PinnedHTTPSConnection(host, pinned_ip=pinned_ip, **kw),
            req,
        )


_SENSITIVE_REDIRECT_HEADERS = frozenset({
    "x-hwid",
    "authorization",
    "proxy-authorization",
    "cookie",
})


def _effective_port(parsed) -> int:
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme.lower() == "https" else 80


def _is_cross_origin(url1: str, url2: str) -> bool:
    p1 = urlparse(url1)
    p2 = urlparse(url2)
    return (
        p1.scheme.lower() != p2.scheme.lower()
        or (p1.hostname or "").lower() != (p2.hostname or "").lower()
        or _effective_port(p1) != _effective_port(p2)
    )


def _strip_sensitive_headers(req) -> None:
    for key in list(req.headers.keys()):
        if key.lower() in _SENSITIVE_REDIRECT_HEADERS:
            del req.headers[key]
    for key in list(getattr(req, "unredirected_hdrs", {}).keys()):
        if key.lower() in _SENSITIVE_REDIRECT_HEADERS:
            del req.unredirected_hdrs[key]


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTPRedirectHandler validating each hop and bounding their count."""

    def __init__(self):
        self._hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._hops += 1
        if self._hops > MAX_SUBSCRIPTION_REDIRECTS:
            raise _BlockedSubscription(tr("sub.too_many_redirects"))
        _require_public_url(newurl)
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and _is_cross_origin(req.full_url, newurl):
            _strip_sensitive_headers(new_req)
        return new_req


def _open_subscription_request(req, timeout=10):
    return urllib.request.build_opener(
        _SafeRedirectHandler, _PinnedHTTPSHandler).open(req, timeout=timeout)


def _read_limited_body(response, timeout=MAX_SUBSCRIPTION_READ_TIMEOUT):
    try:
        declared = response.headers.get("Content-Length")
    except AttributeError:
        declared = None
    try:
        over_limit = declared is not None and int(declared) > MAX_SUBSCRIPTION_BYTES
    except (ValueError, TypeError):
        over_limit = False
    if over_limit:
        raise _BlockedSubscription(tr("sub.body_too_big"))
    deadline = time.monotonic() + timeout
    body = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(tr("sub.net_timeout"))
        chunk = response.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        body += chunk
        if len(body) > MAX_SUBSCRIPTION_BYTES:
            raise _BlockedSubscription(tr("sub.body_too_big"))
    return bytes(body)
