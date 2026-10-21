import asyncio
import socket
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication

from utils.thp import Thp, ProbeQuality, ThpResult
from utils.ping import PING_ERROR_SENTINEL, async_ping_server_job
from utils.geo_utils import detect_flag, extract_name_and_emoji, clean_server_name_and_emoji
from utils.server_model import Server
from utils.subscription_manager import SubscriptionManager
from ui.edit_subscription_dialog import EditSubscriptionDialog
from utils.theme import M3Theme


# ─── THP Tests ──────────────────────────────────────────────────────────────

def test_thp_probe_unreachable():
    # Attempting to probe an unreachable local port should return FAILED quality and score -1
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    res = Thp.probe("127.0.0.1", port, timeout_sec=0.2)
    assert res.quality == ProbeQuality.FAILED
    assert res.thp_score_ms == -1
    assert res.success_rate == 0.0


def test_thp_probe_plain_tcp_accept():
    # A simple TCP server that accepts connections in a loop
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

    res = Thp.probe("127.0.0.1", port, timeout_sec=1.0)
    stop_evt.set()
    srv.close()
    t.join(timeout=1.0)

    assert res.quality == ProbeQuality.OK
    assert res.thp_score_ms > 0
    assert res.transport_rtt_ms >= 0
    assert res.success_rate == 1.0


def test_thp_probe_pure_tcp_no_http():
    # Ensure probe does pure TCP connection without sending any HTTP payload
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

    res = Thp.probe("127.0.0.1", port, timeout_sec=1.5)
    stop_evt.set()
    srv.close()
    t.join(timeout=1.0)

    assert received_bytes == []
    assert res.quality == ProbeQuality.OK
    assert res.success_rate == 1.0


def test_thp_unstable_on_partial_success():
    # Server accepts only 1 connection, then closes; remaining attempts fail -> UNSTABLE
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept_once():
        try:
            conn, _ = srv.accept()
            conn.close()
            srv.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept_once, daemon=True)
    t.start()

    res = Thp.probe("127.0.0.1", port, timeout_sec=1.0)
    t.join(timeout=1.0)

    assert res.quality == ProbeQuality.UNSTABLE
    assert res.success_rate in (pytest.approx(1.0 / 3.0), pytest.approx(2.0 / 3.0))
    assert res.median_ms > 0


def test_thp_multi_ip_fallback():
    # Fake getaddrinfo returning dead IP then working IP
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

    dead_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dead_sock.bind(("127.0.0.1", 0))
    dead_port = dead_sock.getsockname()[1]
    dead_sock.close()

    fake_addrs = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.2', dead_port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port)),
    ]

    import unittest.mock as mock
    with mock.patch("socket.getaddrinfo", return_value=fake_addrs):
        res = Thp.probe("test-node.com", port, timeout_sec=1.5)

    stop_evt.set()
    srv.close()
    t.join(timeout=1.0)

    assert res.quality == ProbeQuality.OK
    assert res.success_rate == 1.0


def test_thp_dns_timeout_sync():
    import unittest.mock as mock
    def _slow_dns(*args, **kwargs):
        time.sleep(1.0)
        return []

    with mock.patch("socket.getaddrinfo", side_effect=_slow_dns):
        old_cap = Thp.DNS_RESOLVE_CAP_SEC
        Thp.DNS_RESOLVE_CAP_SEC = 0.05
        try:
            res = Thp.probe("slow-domain.local", 443, timeout_sec=0.2)
            assert res.quality == ProbeQuality.FAILED
            assert "DNS" in (res.error_message or "")
        finally:
            Thp.DNS_RESOLVE_CAP_SEC = old_cap


def test_thp_async_probe():
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
    stop_evt.set()
    srv.close()
    t.join(timeout=1.0)

    assert res.quality == ProbeQuality.OK
    assert res.thp_score_ms > 0
    assert res.success_rate == 1.0


def test_thp_dns_concurrency_queue_bounded():
    # Verify that when many hosts are probed concurrently, DNS executor semaphore
    # prevents queue backlog from causing false "DNS timeout" failures.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
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
    failed = [r for r in results if r.quality == ProbeQuality.FAILED]
    assert len(failed) == 0, f"False failures found: {[r.error_message for r in failed]}"



def test_async_ping_server_job_thp():
    # An unreachable port returns PING_ERROR_SENTINEL through async_ping_server_job
    server = Server(name="Test THP", host="127.0.0.1", port=65530)
    srv, ms = asyncio.run(async_ping_server_job(server, "thp", 10808, timeout=0.2))
    assert ms == PING_ERROR_SENTINEL


def test_thp_sort_key_order():
    r_fast = ThpResult(host="fast.com", port=443, median_ms=20.0, thp_score_ms=20, quality=ProbeQuality.OK)
    r_mid_ok = ThpResult(host="mid-ok.com", port=443, median_ms=50.0, thp_score_ms=50, quality=ProbeQuality.OK)
    r_mid_un = ThpResult(host="mid-un.com", port=443, median_ms=50.0, thp_score_ms=50, quality=ProbeQuality.UNSTABLE)
    r_slow_un = ThpResult(host="slow.com", port=443, median_ms=120.0, thp_score_ms=120, quality=ProbeQuality.UNSTABLE)
    r_fail = ThpResult(host="fail.com", port=443, median_ms=-1.0, thp_score_ms=-1, quality=ProbeQuality.FAILED)

    raw_list = [r_slow_un, r_fail, r_mid_un, r_fast, r_mid_ok]
    sorted_list = sorted(raw_list, key=Thp._sort_key)

    # Order must be: fast (OK, 20), mid_ok (OK, 50), mid_un (UNSTABLE, 50), slow_un (UNSTABLE, 120), fail (FAILED)
    assert sorted_list == [r_fast, r_mid_ok, r_mid_un, r_slow_un, r_fail]


def test_thp_probe_many_auto_sorting(monkeypatch):
    fake_responses = {
        ("1.1.1.1", 443): ThpResult("1.1.1.1", 443, median_ms=85.0, thp_score_ms=85, transport_rtt_ms=85.0, quality=ProbeQuality.OK),
        ("8.8.8.8", 443): ThpResult("8.8.8.8", 443, median_ms=15.0, thp_score_ms=15, transport_rtt_ms=15.0, quality=ProbeQuality.OK),
        ("cloudflare.com", 443): ThpResult("cloudflare.com", 443, median_ms=45.0, thp_score_ms=45, transport_rtt_ms=45.0, quality=ProbeQuality.UNSTABLE),
        ("unreachable.net", 443): ThpResult("unreachable.net", 443, median_ms=-1.0, thp_score_ms=-1, quality=ProbeQuality.FAILED),
    }

    async def _mock_async_probe(host, port, *args, **kwargs):
        return fake_responses.get((host, port), ThpResult(host, port, quality=ProbeQuality.FAILED))

    monkeypatch.setattr(Thp, "async_probe", _mock_async_probe)

    targets = ["1.1.1.1:443", ("8.8.8.8", 443), "cloudflare.com", "unreachable.net:443"]
    results = Thp.probe_many(targets)

    # Automatically sorted from best ping to worst ping:
    # 1. 8.8.8.8 (15ms, OK)
    # 2. 1.1.1.1 (85ms, OK)
    # 3. cloudflare.com (45ms, UNSTABLE)
    # 4. unreachable.net (failed, -1ms, FAILED)
    assert len(results) == 4
    assert [r.host for r in results] == ["8.8.8.8", "1.1.1.1", "cloudflare.com", "unreachable.net"]
    assert [r.thp_score_ms for r in results] == [15, 85, 45, -1]
    assert results[0].target == ("8.8.8.8", 443)
    assert results[1].target == "1.1.1.1:443"
    assert results[2].target == "cloudflare.com"
    assert results[3].target == "unreachable.net:443"


def test_probe_servers_thp_helper(monkeypatch):
    s1 = Server(name="Node A", host="node-a.com", port=443)
    s2 = Server(name="Node B", host="node-b.com", port=443)

    fake_responses = {
        ("node-a.com", 443): ThpResult("node-a.com", 443, median_ms=90.0, thp_score_ms=90, quality=ProbeQuality.UNSTABLE),
        ("node-b.com", 443): ThpResult("node-b.com", 443, median_ms=30.0, thp_score_ms=30, quality=ProbeQuality.OK),
    }

    async def _mock_async_probe(host, port, *args, **kwargs):
        return fake_responses.get((host, port), ThpResult(host, port, quality=ProbeQuality.FAILED))

    monkeypatch.setattr(Thp, "async_probe", _mock_async_probe)

    results = Thp.probe_many([s1, s2])
    assert len(results) == 2
    assert results[0].host == "node-b.com"
    assert results[0].thp_score_ms == 30
    assert results[0].target is s2
    assert results[1].host == "node-a.com"
    assert results[1].thp_score_ms == 90
    assert results[1].target is s1


# ─── Flag & Country Detection Tests ─────────────────────────────────────────

def test_detect_flag_iso_tokens():
    assert detect_flag("DE Frankfurt 01", "") == ("🇩🇪", "DE")
    assert detect_flag("US Miami", "") == ("🇺🇸", "US")
    assert detect_flag("NL Amsterdam", "") == ("🇳🇱", "NL")
    assert detect_flag("JP Tokyo Fast", "") == ("🇯🇵", "JP")
    assert detect_flag("HK Node 5", "") == ("🇭🇰", "HK")
    assert detect_flag("SG Singapore", "") == ("🇸🇬", "SG")
    assert detect_flag("FI Helsinki", "") == ("🇫🇮", "FI")
    assert detect_flag("FR Paris", "") == ("🇫🇷", "FR")


def test_detect_flag_full_names():
    assert detect_flag("Germany Ultra", "") == ("🇩🇪", "DE")
    assert detect_flag("Netherlands 10G", "") == ("🇳🇱", "NL")
    assert detect_flag("United States 02", "") == ("🇺🇸", "US")
    assert detect_flag("Japan Premium", "") == ("🇯🇵", "JP")
    assert detect_flag("Россия Москва", "") == ("🇷🇺", "RU")


def test_detect_flag_no_false_positives():
    # "Sweden" starts with "SE", but should NOT match "DE" from Frankfurt or "US"
    assert detect_flag("Sweden Server", "") == ("🇸🇪", "SE")
    # Random text should fallback to 🌐 and UN
    assert detect_flag("RandomServer123", "") == ("🌐", "UN")


def test_extract_and_clean_server_name():
    name, flag = extract_name_and_emoji("🇺🇸 US Fast Server", "🌐")
    assert flag == "🇺🇸"
    assert name == "US Fast Server"

    name2, flag2 = clean_server_name_and_emoji("🇩🇪 Frankfurt VIP", "🌐")
    assert flag2 == "🇩🇪"
    assert "Frankfurt VIP" in name2


def test_server_model_flag_auto_detection():
    srv = Server(name="DE Frankfurt", host="1.2.3.4", port=443)
    assert srv.flag_emoji == "🇩🇪"
    assert srv.country_code == "DE"

    # Serialization preserves them
    d = srv.to_dict()
    assert d["flag_emoji"] == "🇩🇪"
    assert d["country_code"] == "DE"

    srv_restored = Server.from_dict(d)
    assert srv_restored.flag_emoji == "🇩🇪"
    assert srv_restored.country_code == "DE"


# ─── EditSubscriptionDialog Tests ───────────────────────────────────────────

def test_edit_subscription_dialog_flow(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    mgr = SubscriptionManager()
    mgr.subscriptions = [{
        "name": "Test Provider",
        "url": "https://example.com/sub",
        "icon_emoji": "⚡",
        "is_pinned": False,
        "servers": [],
        "traffic": {
            "used": 10 * (1024 ** 3),
            "total": 100 * (1024 ** 3),
            "expire": int(time.time() + 86400 * 15),
        },
        "support_url": "https://t.me/testprovider",
    }]

    theme = M3Theme()
    dlg = EditSubscriptionDialog(mgr.subscriptions[0], mgr, theme)

    assert dlg.edit_name.text() == "Test Provider"
    assert dlg.edit_url.text() == "https://example.com/sub"
    assert dlg.selected_icon == "⚡"
    assert hasattr(dlg, "bar_traffic")
    assert dlg.bar_traffic.value() == pytest.approx(0.1)
    assert dlg.lbl_tr_val.text() == "10 GB / 100 GB"
    from PySide6.QtWidgets import QPushButton
    assert not any("Telegram" in btn.text() for btn in dlg.findChildren(QPushButton))

    # Test toggling pin
    dlg._toggle_pin()
    assert mgr.subscriptions[0].get("is_pinned") is True
    assert "Открепить" in dlg.btn_pin.text()

    dlg._toggle_pin()
    assert mgr.subscriptions[0].get("is_pinned") is False
    assert "Закрепить" in dlg.btn_pin.text()

    # Test selecting icon
    dlg.selected_icon = "🔥"
    dlg.btn_icon.setText("🔥")

    # Test renaming and saving
    dlg.edit_name.setText("Renamed Provider")
    dlg.edit_url.setText("https://example.com/new_sub")
    dlg._save()

    saved = mgr.get("Renamed Provider")
    assert saved is not None
    assert saved["url"] == "https://example.com/new_sub"
    assert saved["icon_emoji"] == "🔥"

    # Test deleting subscription
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dlg_delete = EditSubscriptionDialog(saved, mgr, theme)
    dlg_delete._delete_sub()

    assert mgr.get("Renamed Provider") is None
    assert dlg_delete.deleted is True


def test_wavy_progress_bar_clamps_and_paints(qapp):
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
