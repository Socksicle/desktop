import asyncio
import socket
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication

from utils.thp import Thp, ProbeQuality, ThpResult
from utils.ping import PING_ERROR_SENTINEL, async_ping_server_job
from utils.geo_utils import detect_flag, extract_name_and_emoji, clean_server_name_and_emoji
from utils.server_model import Server, ProxyProtocol
from utils.subscription_manager import SubscriptionManager
from ui.edit_subscription_dialog import EditSubscriptionDialog
from utils.theme import M3Theme


# ─── THP Probe Tests ────────────────────────────────────────────────────────

def test_thp_probe_sync_scenarios():
    # 1. Unreachable port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()

    res = Thp.probe("127.0.0.1", dead_port, timeout_sec=0.2)
    assert res.quality == ProbeQuality.FAILED and res.thp_score_ms == -1 and res.success_rate == 0.0

    # 2. Plain TCP accept & pure TCP (no HTTP payload transmitted)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop_evt = threading.Event()
    received_bytes = []

    def _serve():
        srv.settimeout(0.5)
        while not stop_evt.is_set():
            try:
                conn, _ = srv.accept()
                conn.settimeout(0.05)
                try:
                    data = conn.recv(1024)
                    if data:
                        received_bytes.append(data)
                except OSError:
                    pass
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    res = Thp.probe("127.0.0.1", port, timeout_sec=1.0)
    stop_evt.set()
    srv.close()
    t.join(timeout=1.0)

    assert res.quality == ProbeQuality.OK and res.thp_score_ms > 0 and res.success_rate == 1.0
    assert received_bytes == []

    # 3. Partial success -> UNSTABLE
    srv_part = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_part.bind(("127.0.0.1", 0))
    srv_part.listen(1)
    port_part = srv_part.getsockname()[1]

    def _accept_once():
        try:
            conn, _ = srv_part.accept()
            conn.close()
            srv_part.close()
        except OSError:
            pass

    t_part = threading.Thread(target=_accept_once, daemon=True)
    t_part.start()
    res_part = Thp.probe("127.0.0.1", port_part, timeout_sec=1.0)
    t_part.join(timeout=1.0)
    assert res_part.quality == ProbeQuality.UNSTABLE
    assert res_part.success_rate in (pytest.approx(1.0 / 3.0), pytest.approx(2.0 / 3.0))

    # 4. Multi-IP fallback (dead IP followed by working IP)
    srv_live = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_live.bind(("127.0.0.1", 0))
    srv_live.listen(5)
    port_live = srv_live.getsockname()[1]
    stop_live = threading.Event()

    def _serve_live():
        srv_live.settimeout(0.5)
        while not stop_live.is_set():
            try:
                conn, _ = srv_live.accept()
                conn.close()
            except OSError:
                pass

    t_live = threading.Thread(target=_serve_live, daemon=True)
    t_live.start()

    fake_addrs = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.2', dead_port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port_live)),
    ]
    import unittest.mock as mock
    with mock.patch("socket.getaddrinfo", return_value=fake_addrs):
        res_fb = Thp.probe("test-node.com", port_live, timeout_sec=1.5)

    stop_live.set()
    srv_live.close()
    t_live.join(timeout=1.0)
    assert res_fb.quality == ProbeQuality.OK and res_fb.success_rate == 1.0

    # 5. DNS timeout sync
    def _slow_dns(*args, **kwargs):
        time.sleep(1.0)
        return []

    with mock.patch("socket.getaddrinfo", side_effect=_slow_dns):
        old_cap = Thp.DNS_RESOLVE_CAP_SEC
        Thp.DNS_RESOLVE_CAP_SEC = 0.05
        try:
            res_dns = Thp.probe("slow-domain.local", 443, timeout_sec=0.2)
            assert res_dns.quality == ProbeQuality.FAILED and "DNS" in (res_dns.error_message or "")
        finally:
            Thp.DNS_RESOLVE_CAP_SEC = old_cap


def test_thp_async_probe_and_queue():
    # 1. Async probe
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop_evt = threading.Event()

    def _accept():
        srv.settimeout(0.5)
        while not stop_evt.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=_accept, daemon=True)
    t.start()

    res = asyncio.run(Thp.async_probe("127.0.0.1", port, timeout_sec=1.0))
    assert res.quality == ProbeQuality.OK and res.thp_score_ms > 0 and res.success_rate == 1.0

    # 2. Bounded DNS concurrency queue
    import unittest.mock as mock
    def _simulated_dns(host, p, *args, **kwargs):
        time.sleep(0.01)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]

    targets = [f"host-{i}.example.com:{port}" for i in range(20)]
    with mock.patch("socket.getaddrinfo", side_effect=_simulated_dns):
        results = Thp.probe_many(targets, concurrency=20, timeout_sec=2.5)

    stop_evt.set()
    srv.close()
    t.join(timeout=1.0)

    assert len(results) == 20
    assert len([r for r in results if r.quality == ProbeQuality.FAILED]) == 0

    # 3. Sentinel return on unreachable port in async_ping_server_job
    server = Server(name="Test THP", host="127.0.0.1", port=65530)
    srv_res, ms = asyncio.run(async_ping_server_job(server, "thp", 10808, timeout=0.2))
    assert ms == PING_ERROR_SENTINEL


def test_thp_sort_key_order(monkeypatch):
    r_fast = ThpResult(host="fast.com", port=443, median_ms=20.0, thp_score_ms=20, quality=ProbeQuality.OK)
    r_mid_ok = ThpResult(host="mid-ok.com", port=443, median_ms=50.0, thp_score_ms=50, quality=ProbeQuality.OK)
    r_mid_un = ThpResult(host="mid-un.com", port=443, median_ms=50.0, thp_score_ms=50, quality=ProbeQuality.UNSTABLE)
    r_slow_un = ThpResult(host="slow.com", port=443, median_ms=120.0, thp_score_ms=120, quality=ProbeQuality.UNSTABLE)
    r_fail = ThpResult(host="fail.com", port=443, median_ms=-1.0, thp_score_ms=-1, quality=ProbeQuality.FAILED)

    raw_list = [r_slow_un, r_fail, r_mid_un, r_fast, r_mid_ok]
    assert sorted(raw_list, key=Thp._sort_key) == [r_fast, r_mid_ok, r_mid_un, r_slow_un, r_fail]

    # Probe many targets and Servers
    fake_responses = {
        ("1.1.1.1", 443): ThpResult("1.1.1.1", 443, median_ms=85.0, thp_score_ms=85, transport_rtt_ms=85.0, quality=ProbeQuality.OK),
        ("8.8.8.8", 443): ThpResult("8.8.8.8", 443, median_ms=15.0, thp_score_ms=15, transport_rtt_ms=15.0, quality=ProbeQuality.OK),
        ("cloudflare.com", 443): ThpResult("cloudflare.com", 443, median_ms=45.0, thp_score_ms=45, transport_rtt_ms=45.0, quality=ProbeQuality.UNSTABLE),
        ("unreachable.net", 443): ThpResult("unreachable.net", 443, median_ms=-1.0, thp_score_ms=-1, quality=ProbeQuality.FAILED),
    }
    async def fake_probe(h, p, *a, **k):
        return fake_responses.get((h, p), ThpResult(h, p, quality=ProbeQuality.FAILED))
    monkeypatch.setattr(Thp, "async_probe", fake_probe)
    targets = ["1.1.1.1:443", ("8.8.8.8", 443), "cloudflare.com", "unreachable.net:443"]
    results = Thp.probe_many(targets)
    assert [r.host for r in results] == ["8.8.8.8", "1.1.1.1", "cloudflare.com", "unreachable.net"]

    s1 = Server(name="Node A", host="1.1.1.1", port=443)
    s2 = Server(name="Node B", host="8.8.8.8", port=443)
    res_srv = Thp.probe_many([s1, s2])
    assert res_srv[0].target is s2
    assert res_srv[1].target is s1


# ─── Flag & Country Detection Tests ─────────────────────────────────────────

def test_flag_detection_and_server_names():
    # ISO tokens
    assert detect_flag("DE Frankfurt 01", "") == ("🇩🇪", "DE")
    assert detect_flag("US Miami", "") == ("🇺🇸", "US")
    assert detect_flag("NL Amsterdam", "") == ("🇳🇱", "NL")
    assert detect_flag("JP Tokyo Fast", "") == ("🇯🇵", "JP")

    # Full names
    assert detect_flag("Germany Ultra", "") == ("🇩🇪", "DE")
    assert detect_flag("Россия Москва", "") == ("🇷🇺", "RU")

    # No false positives & fallback
    assert detect_flag("Sweden Server", "") == ("🇸🇪", "SE")
    assert detect_flag("RandomServer123", "") == ("🌐", "UN")

    # Clean server name
    name, flag = extract_name_and_emoji("🇺🇸 US Fast Server", "🌐")
    assert flag == "🇺🇸" and name == "US Fast Server"

    # Server model auto detection & serialization roundtrip
    srv = Server(name="DE Frankfurt", host="1.2.3.4", port=443)
    assert srv.flag_emoji == "🇩🇪" and srv.country_code == "DE"
    srv_restored = Server.from_dict(srv.to_dict())
    assert srv_restored.flag_emoji == "🇩🇪" and srv_restored.country_code == "DE"


# ─── Subscription UI & Management Flow ──────────────────────────────────────

def test_subscription_ui_and_management_flow(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)

    # 1. EditSubscriptionDialog flow
    mgr = SubscriptionManager()
    server1 = Server(name="Node1", host="example.com", port=443, protocol=ProxyProtocol.VLESS, insecure=False)
    sub = {
        "name": "Test Provider",
        "url": "https://example.com/sub",
        "icon_emoji": "⚡",
        "is_pinned": False,
        "allow_insecure": False,
        "servers": [server1],
        "traffic": {
            "used": 10 * (1024 ** 3),
            "total": 100 * (1024 ** 3),
            "expire": int(time.time() + 86400 * 15),
        },
        "support_url": "https://t.me/testprovider",
        "profile_web_page_url": "https://example.com/panel",
        "description": "Special test subscription description",
    }
    mgr.subscriptions = [sub]

    theme = M3Theme()
    dlg = EditSubscriptionDialog(mgr.subscriptions[0], mgr, theme)

    assert dlg.edit_name.text() == "Test Provider"
    assert dlg.edit_url.text() == "https://example.com/sub"
    assert dlg.selected_icon == "⚡"
    assert hasattr(dlg, "bar_traffic")
    assert dlg.bar_traffic.value() == pytest.approx(0.1)
    assert dlg.lbl_tr_val.text() == "10 GB / 100 GB"

    # Spoiler toggle
    assert dlg.spoiler_desc.text() == "Special test subscription description"
    assert dlg.spoiler_desc.isHidden() is True
    dlg.spoiler_toggle()
    assert dlg.spoiler_desc.isHidden() is False
    dlg.spoiler_toggle()
    assert dlg.spoiler_desc.isHidden() is True

    # Desktop open URLs
    opened_urls = []
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QPushButton, QMessageBox
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString()))
    dlg.btn_tg.click()
    dlg.btn_web.click()
    assert opened_urls == ["https://t.me/testprovider", "https://example.com/panel"]
    assert not any("Telegram" in btn.text() for btn in dlg.findChildren(QPushButton))

    # Toggle pin
    dlg._toggle_pin()
    assert mgr.subscriptions[0].get("is_pinned") is True
    dlg._toggle_pin()
    assert mgr.subscriptions[0].get("is_pinned") is False

    # Insecure toggle & rename save
    dlg.sw_insecure.setChecked(True)
    dlg.selected_icon = "🔥"
    dlg.btn_icon.setText("🔥")
    dlg.edit_name.setText("Renamed Provider")
    dlg.edit_url.setText("https://example.com/new_sub")
    dlg._save()

    saved = mgr.get("Renamed Provider")
    assert saved is not None
    assert saved["url"] == "https://example.com/new_sub"
    assert saved["icon_emoji"] == "🔥"
    assert saved.get("allow_insecure") is True

    # Allow insecure manager logic
    mgr.set_allow_insecure("Renamed Provider", True)
    assert server1.insecure is True
    mgr.set_allow_insecure("Renamed Provider", False)
    assert server1.insecure is False

    # Delete dialog
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dlg_delete = EditSubscriptionDialog(saved, mgr, theme)
    dlg_delete._delete_sub()
    assert mgr.get("Renamed Provider") is None and dlg_delete.deleted is True

    # 2. Wavy linear progress bar
    from ui.wavy_progress_bar import WavyLinearProgressBar
    bar = WavyLinearProgressBar(M3Theme(preset_key="lavender"))
    assert bar.value() == 0.0
    bar.set_value(0.1)
    assert bar.value() == pytest.approx(0.1)
    bar.set_value(2.0)
    assert bar.value() == 1.0
    bar.set_value(-1.0)
    assert bar.value() == 0.0
    bar.resize(300, 12)
    assert not bar.grab().isNull()

    # 3. GroupHeaderCard three dots and pin
    from ui.group_header_card import GroupHeaderCard
    card = GroupHeaderCard(
        key="test_group",
        title="Test Group",
        icon="🌐",
        is_direct=False,
        is_pinned=False,
        theme=theme,
    )
    assert hasattr(card, "btn_more") and card.btn_more.property("iconName") == "more_vert"
    assert card.btn_ping.width() == 36 and card.btn_ping.height() == 36
    assert card.btn_refresh.width() == 36 and card.btn_refresh.height() == 36
    assert card.btn_more.width() == 36 and card.btn_more.height() == 36
    card.update_group("Test Group", "🌐", is_direct=False, is_pinned=True)
    assert card.is_pinned is True


def test_thp_dns_cache_and_single_flight():
    from unittest import mock
    Thp.clear_dns_cache()
    call_counts = {}

    def mock_getaddrinfo(host, port, *args, **kwargs):
        call_counts[host] = call_counts.get(host, 0) + 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    with mock.patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        # 1. Two probes of the same host -> 1 call
        fut1 = Thp._resolve_shared("shared-host.com")
        fut2 = Thp._resolve_shared("shared-host.com")
        res1 = fut1.result(timeout=2.0)
        res2 = fut2.result(timeout=2.0)
        assert res1 == res2
        assert call_counts["shared-host.com"] == 1

        # 2. Different host -> 2 calls total
        fut3 = Thp._resolve_shared("other-host.com")
        res3 = fut3.result(timeout=2.0)
        assert len(res3) > 0
        assert call_counts["shared-host.com"] == 1
        assert call_counts["other-host.com"] == 1

        # 3. After TTL expiration -> another call
        with Thp._dns_lock:
            Thp._dns_cache["shared-host.com"] = (time.monotonic() - 1.0, res1)

        fut4 = Thp._resolve_shared("shared-host.com")
        res4 = fut4.result(timeout=2.0)
        assert res4 == res1
        assert call_counts["shared-host.com"] == 2
    Thp.clear_dns_cache()


def test_thp_dns_timeout_does_not_poison_cache():
    from unittest import mock
    Thp.clear_dns_cache()
    resolve_started = threading.Event()
    resolve_allow = threading.Event()
    getaddrinfo_calls = 0

    def slow_getaddrinfo(host, port, *args, **kwargs):
        nonlocal getaddrinfo_calls
        getaddrinfo_calls += 1
        resolve_started.set()
        resolve_allow.wait(timeout=2.0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    with mock.patch("socket.getaddrinfo", side_effect=slow_getaddrinfo):
        old_cap = Thp.DNS_RESOLVE_CAP_SEC
        Thp.DNS_RESOLVE_CAP_SEC = 0.05
        try:
            # Probe 1 times out because DNS takes longer than 0.05s
            res1 = asyncio.run(Thp.async_probe("slow-domain.com", 443, timeout_sec=0.1))
            assert res1.quality == ProbeQuality.FAILED
            assert "DNS" in (res1.error_message or "")
            assert getaddrinfo_calls == 1

            # Allow background getaddrinfo to complete
            resolve_allow.set()

            # Wait for future in threadpool to complete and cache to be populated
            for _ in range(50):
                time.sleep(0.02)
                with Thp._dns_lock:
                    if "slow-domain.com" in Thp._dns_cache:
                        break

            # Probe 2 on the same domain: uses cache without calling getaddrinfo again
            res2 = asyncio.run(Thp.async_probe("slow-domain.com", 443, timeout_sec=1.0))
            assert getaddrinfo_calls == 1  # No second call!
            assert res2.error_message != "DNS timeout"
            assert "DNS failed" not in (res2.error_message or "")
        finally:
            Thp.DNS_RESOLVE_CAP_SEC = old_cap
            Thp.clear_dns_cache()
