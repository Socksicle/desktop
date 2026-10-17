"""Unit tests for subscription multi-node parsing and deduplication."""
import base64
import json
import urllib.request

import pytest

from utils.server_model import Server, ProxyProtocol
from utils.subscription_manager import SubscriptionManager
import utils.subscription_store as sm


@pytest.fixture(autouse=True)
def _stub_public_dns(monkeypatch):
    """Resolve test hosts to a public IP so the SSRF preflight stays off real DNS.

    These tests mock the HTTP transport to cover parsing/headers/errors;
    the guard itself is covered by dedicated blocking tests below, which
    override this stub with their own getaddrinfo fakes.
    """
    import socket as std_socket
    import utils.subscription_fetch as sf

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)


def test_installation_id_generated_once_and_stable():
    settings = {}
    first = sm.get_installation_id(settings)
    assert first and len(first) == 32
    assert settings["installation_id"] == first
    assert sm.get_installation_id(settings) == first


def test_installation_id_persisted_across_manager_instances(tmp_path, monkeypatch):
    from utils import server_manager as srm
    monkeypatch.setattr(srm, "get_config_dir", lambda: tmp_path)
    mgr = srm.ServerManager()
    assert mgr.settings.get("installation_id")
    assert len(mgr.settings["installation_id"]) == 32
    mgr2 = srm.ServerManager()
    assert mgr2.settings["installation_id"] == mgr.settings["installation_id"]


def _capture_req_urlopen(captured):
    class DummyResp:
        headers = {}
        def read(self, size=-1):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    def mock_urlopen(req, timeout=10):
        captured.append(req)
        return DummyResp()
    return mock_urlopen


def _header(req, name):
    hdrs = {k.lower(): v for k, v in req.header_items()}
    return hdrs.get(name.lower())


def test_xhwid_header_prefers_custom_value(monkeypatch):
    captured = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _capture_req_urlopen(captured))
    settings = {"fake_hwid": True, "hwid_value": "my-custom-hwid"}
    sm.parse_subscription("https://example.com/sub", settings)
    assert _header(captured[0], "X-hwid") == "my-custom-hwid"


def test_xhwid_header_uses_stable_installation_id(monkeypatch):
    captured = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _capture_req_urlopen(captured))
    settings = {"fake_hwid": True}
    sm.parse_subscription("https://example.com/sub", settings)
    hwid = _header(captured[0], "X-hwid")
    assert hwid == settings["installation_id"]
    assert len(hwid) == 32


def test_multinode_same_host_preserved(tmp_path, monkeypatch):
    """Ensure nodes with same host/uuid but different names/flags (e.g. Google DE, Google NL) are preserved."""
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    de_link = "vless://uuid-123@google.com:443?security=reality&sni=de.google.com#Google%20%F0%9F%87%A9%F0%9F%87%AA"
    nl_link = "vless://uuid-123@google.com:443?security=reality&sni=nl.google.com#Google%20%F0%9F%87%B3%F0%9F%87%B1"
    us_link = "vless://uuid-123@google.com:443?security=reality&sni=us.google.com#Google%20%F0%9F%87%BA%F0%9F%87%B8"

    raw_links = f"{de_link}\n{nl_link}\n{us_link}\n"
    b64_sub = base64.b64encode(raw_links.encode("utf-8")).decode("utf-8")

    # Mock parse_subscription to return our links
    monkeypatch.setattr("utils.subscription_manager.parse_subscription",
                        lambda url, settings: ([de_link, nl_link, us_link], {"profile_title": "Google VPN"}))

    sub_mgr = SubscriptionManager()
    ok = sub_mgr.add("Google Sub", "https://example.com/sub")
    assert ok is True

    sub = sub_mgr.get("Google Sub")
    assert sub is not None
    servers = sub["servers"]
    assert len(servers) == 3
    names = [s.name for s in servers]
    assert "Google 🇩🇪" in names
    assert "Google 🇳🇱" in names
    assert "Google 🇺🇸" in names


def test_base64_multiline_with_whitespace_decoding(tmp_path, monkeypatch):
    """Test base64 subscription decoding with wrapped lines and whitespace."""
    raw_text = "vless://u1@h1:443#N1\nvless://u2@h2:443#N2\n"
    # Encode and insert linebreaks/spaces inside base64
    b64 = base64.b64encode(raw_text.encode("utf-8")).decode("utf-8")
    b64_with_spaces = b64[:10] + "\r\n  " + b64[10:]

    class DummyResp:
        headers = {}
        def __init__(self, body):
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

    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: DummyResp(b64_with_spaces.encode("utf-8")))

    links, meta = sm.parse_subscription("https://valid-sub.example.com/sub")
    assert len(links) == 2
    assert links[0] == "vless://u1@h1:443#N1"
    assert links[1] == "vless://u2@h2:443#N2"


def test_subscription_fetch_http_404_error_formatting(monkeypatch):
    """Test human-readable error formatting on HTTP 404 Not Found."""
    import urllib.error

    def mock_urlopen(req, timeout=10):
        raise urllib.error.HTTPError("https://example.com/sub", 404, "Not Found", {}, None)

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", mock_urlopen)

    links, meta = sm.parse_subscription("https://example.com/sub")
    assert links == []
    assert "error" in meta
    assert "404" in meta["error"]
    assert "не найдена" in meta["error"]
    assert sm.get_last_fetch_error("https://example.com/sub") == meta["error"]


def test_subscription_fetch_winerror_10061_error_formatting(monkeypatch):
    """Test human-readable error formatting on WinError 10061 connection refused."""
    import urllib.error

    def mock_urlopen(req, timeout=10):
        os_err = OSError()
        os_err.winerror = 10061
        raise urllib.error.URLError(os_err)

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", mock_urlopen)

    links, meta = sm.parse_subscription("https://example.com/sub")
    assert links == []
    assert "error" in meta
    assert "отклонил" in meta["error"] or "refused" in meta["error"]


def test_subscription_fetch_timeout_and_dns_formatting(monkeypatch):
    """Test user-friendly formatting for timeout, DNS, and 410 error responses."""
    import urllib.error
    from utils.i18n import set_language

    set_language("ru")
    # Timeout
    def mock_timeout(req, timeout=10):
        raise TimeoutError("timed out")

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", mock_timeout)
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert "не отвечает" in meta["error"] or "время ожидания" in meta["error"]

    # 410 Gone (deleted subscription)
    def mock_410(req, timeout=10):
        raise urllib.error.HTTPError("https://example.com/sub", 410, "Gone", {}, None)

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", mock_410)
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert "удалена" in meta["error"] or "не найдена" in meta["error"]


def test_subscription_manager_emits_update_failed(tmp_path, monkeypatch):
    """Ensure SubscriptionManager emits updateFailed with name, url, and reason."""
    import urllib.error
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    def mock_urlopen(req, timeout=10):
        raise urllib.error.HTTPError("https://zoomtx.shop:8000/sub", 404, "Not Found", {}, None)

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", mock_urlopen)

    sub_mgr = SubscriptionManager()
    sub_dict = {"name": "ZoomTx", "url": "https://zoomtx.shop:8000/sub", "servers": []}

    failed_signals = []
    sub_mgr.updateFailed.connect(lambda name, url, err: failed_signals.append((name, url, err)))

    sub_mgr._update_worker(sub_dict, emit_signal=True)

    assert len(failed_signals) == 1
    name, url, err = failed_signals[0]
    assert name == "ZoomTx"
    assert url == "https://zoomtx.shop:8000/sub"
    assert "404" in err


def test_subscription_manager_update_failed_without_error_meta(tmp_path, monkeypatch):
    """HTTP 200 with zero links and no recorded error must still emit updateFailed.

    A stuck foreign error in _last_fetch_errors must not replace sub.fetch_failed.
    """
    from utils.i18n import tr

    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_manager.parse_subscription",
                        lambda url, settings: ([], {}))
    monkeypatch.setattr(sm, "_last_fetch_errors", {"https://foreign-stale.com/sub": "Stale 502 Error"})

    sub_mgr = SubscriptionManager()
    sub_dict = {"name": "EmptySub", "url": "https://example.com/empty", "servers": []}

    failed_signals = []
    sub_mgr.updateFailed.connect(
        lambda name, url, err: failed_signals.append((name, url, err)))

    sub_mgr._update_worker(sub_dict, emit_signal=True)

    assert failed_signals == [
        ("EmptySub", "https://example.com/empty", tr("sub.fetch_failed"))
    ]


def _refusing_open(calls):
    def mock_open(req, timeout=10):
        calls.append(req)
        raise AssertionError("blocked fetch must not hit the network")
    return mock_open


def test_http_subscription_blocked_without_request(monkeypatch):
    calls = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _refusing_open(calls))
    url = "http://example.com/sub?token=secret"
    links, meta = sm.parse_subscription(url, {"fake_hwid": True})
    assert links == []
    assert "HTTPS" in meta["error"]
    assert calls == []
    assert sm.get_last_fetch_error(url) == meta["error"]


@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "0.0.0.0", "[::1]",
])
def test_ip_literal_non_public_blocked(monkeypatch, host):
    calls = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _refusing_open(calls))
    url = f"https://{host}/sub"
    links, meta = sm.parse_subscription(url)
    assert links == []
    assert "error" in meta
    assert calls == []


def test_dns_resolving_to_private_blocked(monkeypatch):
    import socket as std_socket
    import utils.subscription_fetch as sf

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)
    calls = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _refusing_open(calls))
    links, meta = sm.parse_subscription("https://evil.example/sub")
    assert links == []
    assert "приватный" in meta["error"]
    assert calls == []



def test_oversize_body_blocked(monkeypatch):
    import utils.subscription_fetch as sf

    class BigResp:
        headers = {}

        def __init__(self):
            self._left = sf.MAX_SUBSCRIPTION_BYTES + 1

        def read(self, size=-1):
            if self._left <= 0:
                return b""
            n = self._left if size is None or size < 0 else min(size, self._left)
            self._left -= n
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: BigResp())
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert links == []
    assert "превышает" in meta["error"]


def test_public_ip_literal_allowed(monkeypatch):
    body = base64.b64encode(b"vless://u@h:443#N").decode()

    class Resp:
        headers = {}

        def __init__(self):
            self._body = body.encode()

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

    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: Resp())
    links, _meta = sm.parse_subscription("https://93.184.216.34/sub")
    assert links == ["vless://u@h:443#N"]


def test_dns_resolution_failure_fails_closed(monkeypatch):
    import utils.subscription_fetch as sf
    import urllib.error

    def fail_getaddrinfo(host, port, *args, **kwargs):
        raise OSError(11001, "getaddrinfo failed")

    monkeypatch.setattr(sf.socket, "getaddrinfo", fail_getaddrinfo)

    # 1. _require_public_host raises URLError on DNS failure (no fail-open)
    with pytest.raises(urllib.error.URLError) as exc_info:
        sf._require_public_host("unresolvable.example")
    assert isinstance(exc_info.value.reason, OSError)

    # 2. _PinnedHTTPSHandler raises URLError on DNS failure (no unpinned fallback)
    handler = sf._PinnedHTTPSHandler()
    req = urllib.request.Request("https://unresolvable.example/sub")
    with pytest.raises(urllib.error.URLError) as exc_info2:
        handler.https_open(req)
    assert isinstance(exc_info2.value.reason, OSError)

    # 3. parse_subscription surfaces the DNS error without connecting
    calls = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _refusing_open(calls))
    links, meta = sm.parse_subscription("https://unresolvable.example/sub")
    assert links == []
    assert calls == []
    assert "DNS" in meta["error"] or "getaddrinfo" in meta["error"]


def test_dns_unicode_error_fails_closed(monkeypatch):
    import utils.subscription_fetch as sf
    import urllib.error

    def unicode_fail_getaddrinfo(host, port, *args, **kwargs):
        raise UnicodeError("invalid domain name")

    monkeypatch.setattr(sf.socket, "getaddrinfo", unicode_fail_getaddrinfo)

    with pytest.raises(urllib.error.URLError):
        sf._require_public_host("bad-unicode.example")

    handler = sf._PinnedHTTPSHandler()
    req = urllib.request.Request("https://bad-unicode.example/sub")
    with pytest.raises(urllib.error.URLError):
        handler.https_open(req)

