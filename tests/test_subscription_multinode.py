"""Unit tests for subscription multi-node parsing and deduplication."""
import base64
import json
import urllib.error
import urllib.request

import pytest

from utils.server_model import Server, ProxyProtocol
from utils.subscription_manager import SubscriptionManager
import utils.subscription_store as sm


@pytest.fixture(autouse=True)
def _stub_public_dns(monkeypatch):
    """Resolve test hosts to a public IP so the SSRF preflight stays off real DNS."""
    import socket as std_socket
    import utils.subscription_fetch as sf

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", fake_getaddrinfo)


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


def _refusing_open(calls):
    def mock_open(req, timeout=10):
        calls.append(req)
        raise AssertionError("blocked fetch must not hit the network")
    return mock_open


def test_installation_id_and_hwid_headers(tmp_path, monkeypatch):
    # 1. Lifecycle & persistence
    settings = {}
    first = sm.get_installation_id(settings)
    assert first and len(first) == 32
    assert settings["installation_id"] == first
    assert sm.get_installation_id(settings) == first

    from utils import server_manager as srm
    monkeypatch.setattr(srm, "get_config_dir", lambda: tmp_path)
    mgr = srm.ServerManager()
    assert mgr.settings.get("installation_id") == first or len(mgr.settings.get("installation_id", "")) == 32
    mgr2 = srm.ServerManager()
    assert mgr2.settings["installation_id"] == mgr.settings["installation_id"]

    # 2. X-hwid header custom & default modes
    captured = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        _capture_req_urlopen(captured))
    custom_settings = {"fake_hwid": True, "hwid_value": "my-custom-hwid"}
    sm.parse_subscription("https://example.com/sub", custom_settings)
    assert _header(captured[0], "X-hwid") == "my-custom-hwid"

    default_settings = {"fake_hwid": True}
    sm.parse_subscription("https://example.com/sub", default_settings)
    hwid = _header(captured[1], "X-hwid")
    assert hwid == default_settings["installation_id"]
    assert len(hwid) == 32


def test_subscription_manager_sends_stable_installation_hwid(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)
    body = base64.b64encode(b"vless://u1@h1:443#N1")
    captured = []

    class DummyResp:
        headers = {}

        def __init__(self, payload):
            self._body = payload

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

    def mock_open(req, timeout=10):
        captured.append(req)
        return DummyResp(body)

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", mock_open)

    settings = {"fake_hwid": True, "installation_id": "0" * 32}
    mgr = SubscriptionManager(settings)
    assert mgr.add("A", "https://example.com/sub-a") is True
    assert mgr.add("B", "https://example.com/sub-b") is True

    hwids = [_header(req, "X-hwid") for req in captured]
    assert hwids == [settings["installation_id"]] * 2


def test_multinode_same_host_and_format_parsing(tmp_path, monkeypatch):
    # 1. Same host/uuid but different names/flags preserved
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    de_link = "vless://uuid-123@google.com:443?security=reality&sni=de.google.com#Google%20%F0%9F%87%A9%F0%9F%87%AA"
    nl_link = "vless://uuid-123@google.com:443?security=reality&sni=nl.google.com#Google%20%F0%9F%87%B3%F0%9F%87%B1"
    us_link = "vless://uuid-123@google.com:443?security=reality&sni=us.google.com#Google%20%F0%9F%87%BA%F0%9F%87%B8"

    monkeypatch.setattr("utils.subscription_manager.parse_subscription",
                        lambda url, settings: ([de_link, nl_link, us_link], {"profile_title": "Google VPN"}))

    sub_mgr = SubscriptionManager()
    assert sub_mgr.add("Google Sub", "https://example.com/sub") is True
    sub = sub_mgr.get("Google Sub")
    assert sub is not None
    servers = sub["servers"]
    assert len(servers) == 3
    names = [s.name for s in servers]
    assert "Google 🇩🇪" in names and "Google 🇳🇱" in names and "Google 🇺🇸" in names

    # 2. Base64 multiline with whitespace
    raw_text = "vless://u1@h1:443#N1\nvless://u2@h2:443#N2\n"
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
    links, _ = sm.parse_subscription("https://valid-sub.example.com/sub")
    assert links == ["vless://u1@h1:443#N1", "vless://u2@h2:443#N2"]

    # 3. Trojan and TUIC nodes accepted
    trojan_body = "\n".join([
        "trojan://pass@trojan.example.com:443?sni=trojan.example.com#TrojanNode",
        "trojan-go://pass@1.2.3.4:8443?type=ws&path=/ws&sni=sni.com#TrojanGoNode",
        "tuic://uuid:pass@tuic.example.com:8443?sni=tuic.example.com#TuicNode",
    ])
    b64_trojan = base64.b64encode(trojan_body.encode("utf-8")).decode("ascii")
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: DummyResp(b64_trojan.encode("ascii")))
    links, _ = sm.parse_subscription("https://example.com/sub")
    assert links == trojan_body.split("\n")
    assert any(link.startswith("trojan-go://") for link in links)

    # 4. Description stops at trojan node line
    from utils.subscription_store import _extract_description
    assert _extract_description({}, ["Welcome aboard", "trojan://pass@trojan.example.com:443#N"], "", {}) == "Welcome aboard"


def test_subscription_fetch_and_manager_errors(tmp_path, monkeypatch):
    from utils.i18n import set_language, tr

    set_language("ru")

    # 1. 404 Not Found
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError("https://example.com/sub", 404, "Not Found", {}, None)))
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert links == []
    assert "404" in meta["error"] and "не найдена" in meta["error"]
    assert sm.get_last_fetch_error("https://example.com/sub") == meta["error"]

    # 2. WinError 10061 connection refused
    os_err = OSError()
    os_err.winerror = 10061
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.URLError(os_err)))
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert "отклонил" in meta["error"] or "refused" in meta["error"]

    # 3. Timeout
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: (_ for _ in ()).throw(TimeoutError("timed out")))
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert "не отвечает" in meta["error"] or "время ожидания" in meta["error"]

    # 4. 410 Gone
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError("https://example.com/sub", 410, "Gone", {}, None)))
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert "удалена" in meta["error"] or "не найдена" in meta["error"]

    # 5. SubscriptionManager emits updateFailed with reason on error
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store._open_subscription_request",
                        lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.HTTPError("https://zoomtx.shop:8000/sub", 404, "Not Found", {}, None)))
    sub_mgr = SubscriptionManager()
    failed_signals = []
    sub_mgr.updateFailed.connect(lambda name, url, err: failed_signals.append((name, url, err)))
    sub_mgr._update_worker({"name": "ZoomTx", "url": "https://zoomtx.shop:8000/sub", "servers": []}, emit_signal=True)
    assert len(failed_signals) == 1
    assert failed_signals[0][0] == "ZoomTx"
    assert failed_signals[0][1] == "https://zoomtx.shop:8000/sub"
    assert "404" in failed_signals[0][2]

    # 6. HTTP 200 with zero links and no error emits updateFailed
    failed_signals.clear()
    monkeypatch.setattr("utils.subscription_manager.parse_subscription", lambda url, settings: ([], {}))
    monkeypatch.setattr(sm, "_last_fetch_errors", {"https://foreign-stale.com/sub": "Stale 502 Error"})
    sub_mgr._update_worker({"name": "EmptySub", "url": "https://example.com/empty", "servers": []}, emit_signal=True)
    assert failed_signals == [("EmptySub", "https://example.com/empty", tr("sub.fetch_failed"))]


def test_subscription_ssrf_and_ip_validation(monkeypatch):
    calls = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request", _refusing_open(calls))

    # 1. Plain HTTP blocked
    url = "http://example.com/sub?token=secret"
    links, meta = sm.parse_subscription(url, {"fake_hwid": True})
    assert links == [] and "HTTPS" in meta["error"] and calls == []
    assert sm.get_last_fetch_error(url) == meta["error"]

    # 2. Non-public IP literals blocked
    for host in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "0.0.0.0", "[::1]"):
        links, meta = sm.parse_subscription(f"https://{host}/sub")
        assert links == [] and "error" in meta
    assert calls == []

    # 3. DNS resolving to private IP blocked
    import socket as std_socket
    import utils.subscription_fetch as sf
    monkeypatch.setattr(sf.socket, "getaddrinfo",
                        lambda h, p, *a, **kw: [(std_socket.AF_INET, std_socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))])
    links, meta = sm.parse_subscription("https://evil.example/sub")
    assert links == [] and "приватный" in meta["error"] and calls == []

    # 4. Public IP literal allowed
    class Resp:
        headers = {}
        def __init__(self):
            self._body = base64.b64encode(b"vless://u@h:443#N")
        def read(self, size=-1):
            data, self._body = self._body, b""
            return data
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", lambda req, timeout=10: Resp())
    links, _ = sm.parse_subscription("https://93.184.216.34/sub")
    assert links == ["vless://u@h:443#N"]


def test_subscription_oversize_and_dns_errors(monkeypatch):
    import utils.subscription_fetch as sf

    # 1. Oversize body blocked
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

    monkeypatch.setattr("utils.subscription_store._open_subscription_request", lambda req, timeout=10: BigResp())
    links, meta = sm.parse_subscription("https://example.com/sub")
    assert links == [] and "превышает" in meta["error"]

    # 2. DNS fail-closed on socket error and unicode error
    def fail_getaddrinfo(host, port, *args, **kwargs):
        raise OSError(11001, "getaddrinfo failed")

    monkeypatch.setattr(sf.socket, "getaddrinfo", fail_getaddrinfo)
    with pytest.raises(urllib.error.URLError) as exc_info:
        sf._require_public_host("unresolvable.example")
    assert isinstance(exc_info.value.reason, OSError)

    handler = sf._PinnedHTTPSHandler()
    req = urllib.request.Request("https://unresolvable.example/sub")
    with pytest.raises(urllib.error.URLError) as exc_info2:
        handler.https_open(req)
    assert isinstance(exc_info2.value.reason, OSError)

    calls = []
    monkeypatch.setattr("utils.subscription_store._open_subscription_request", _refusing_open(calls))
    links, meta = sm.parse_subscription("https://unresolvable.example/sub")
    assert links == [] and calls == [] and ("DNS" in meta["error"] or "getaddrinfo" in meta["error"])

    # Unicode error in domain
    monkeypatch.setattr(sf.socket, "getaddrinfo", lambda *a, **kw: (_ for _ in ()).throw(UnicodeError("invalid domain name")))
    with pytest.raises(urllib.error.URLError):
        sf._require_public_host("bad-unicode.example")
    with pytest.raises(urllib.error.URLError):
        handler.https_open(urllib.request.Request("https://bad-unicode.example/sub"))


def test_subscription_update_all_emits_updated(qapp, tmp_path, monkeypatch):
    import threading
    import utils.subscription_manager as sub_mod
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_manager.parse_subscription",
                        lambda url, settings: (["ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpwYXNz@1.2.3.4:8388#S1"], {}))

    mgr = sub_mod.SubscriptionManager()
    mgr.subscriptions = [{"name": "Sub1", "url": "https://example.com/sub", "servers": []}]

    signals = []
    mgr.updated.connect(lambda ok, cnt: signals.append((ok, cnt)))

    threads = []
    real_thread = threading.Thread

    def tracked_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        threads.append(t)
        return t

    monkeypatch.setattr(sub_mod.threading, "Thread", tracked_thread)
    mgr._orig_update_all()
    for t in threads:
        t.join(timeout=5)
    qapp.processEvents()

    assert len(signals) == 1
    assert signals[0] == (True, 1)
    assert len(mgr.subscriptions[0]["servers"]) == 1


def test_subscription_update_all_coalesces_save(qapp, tmp_path, monkeypatch):
    import threading
    import utils.subscription_manager as sub_mod

    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "utils.subscription_manager.parse_subscription",
        lambda url, settings: (["ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpwYXNz@1.2.3.4:8388#S1"], {}),
    )

    save_count = 0

    def mock_save_subscriptions(data):
        nonlocal save_count
        save_count += 1

    monkeypatch.setattr("utils.subscription_manager.save_subscriptions", mock_save_subscriptions)

    mgr = sub_mod.SubscriptionManager()
    mgr.subscriptions = [
        {"name": f"Sub{i}", "url": f"https://example.com/sub{i}", "servers": []}
        for i in range(1, 4)
    ]

    threads = []
    real_thread = threading.Thread

    def tracked_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        threads.append(t)
        return t

    monkeypatch.setattr(sub_mod.threading, "Thread", tracked_thread)
    mgr._orig_update_all()
    for t in threads:
        t.join(timeout=5)
    qapp.processEvents()

    assert save_count == 1
    assert len(mgr.subscriptions) == 3
    for sub in mgr.subscriptions:
        assert len(sub["servers"]) == 1
