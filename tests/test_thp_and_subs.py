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


def test_thp_probe_plain_tcp_accept():
    # A simple TCP server that accepts and closes connection
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept():
        try:
            conn, _ = srv.accept()
            time.sleep(0.05)
            conn.close()
        except Exception:
            pass

    t = threading.Thread(target=_accept, daemon=True)
    t.start()

    res = Thp.probe("127.0.0.1", port, timeout_sec=1.0)
    srv.close()
    t.join(timeout=1.0)

    # In THP invariant: once TCP connects, the node is considered reachable;
    # the HTTP status only decides between VERIFIED and REACHABLE scores.
    assert res.quality == ProbeQuality.REACHABLE
    assert res.thp_score_ms > 0
    assert res.transport_rtt_ms >= 0


def test_thp_probe_uses_plain_http_without_tls():
    # The probe must speak plain HTTP first: no TLS ClientHello may precede
    # the HEAD request (regression: unverified CERT_NONE TLS was removed).
    import socket as std_socket
    srv = std_socket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    srv.setsockopt(std_socket.SOL_SOCKET, std_socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def _serve():
        try:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received["data"] = data
                conn.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        except Exception:
            pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    res = Thp.probe("127.0.0.1", port, timeout_sec=2.0)
    srv.close()
    t.join(timeout=2.0)

    assert received["data"].startswith(b"HEAD")
    assert res.quality == ProbeQuality.VERIFIED
    assert res.status_code == 204


def test_thp_async_probe():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept():
        try:
            conn, _ = srv.accept()
            time.sleep(0.02)
            conn.close()
        except Exception:
            pass

    t = threading.Thread(target=_accept, daemon=True)
    t.start()

    res = asyncio.run(Thp.async_probe("127.0.0.1", port, timeout_sec=1.0))
    srv.close()
    t.join(timeout=1.0)

    assert res.quality == ProbeQuality.REACHABLE
    assert res.thp_score_ms > 0


def test_async_ping_server_job_thp():
    # An unreachable port returns PING_ERROR_SENTINEL through async_ping_server_job
    server = Server(name="Test THP", host="127.0.0.1", port=65530)
    srv, ms = asyncio.run(async_ping_server_job(server, "thp", 10808, timeout=0.2))
    assert ms == PING_ERROR_SENTINEL


def test_thp_sort_key_order():
    r_fast = ThpResult(host="fast.com", port=443, thp_score_ms=20, quality=ProbeQuality.VERIFIED)
    r_mid_v = ThpResult(host="mid-v.com", port=443, thp_score_ms=50, quality=ProbeQuality.VERIFIED)
    r_mid_r = ThpResult(host="mid-r.com", port=443, thp_score_ms=50, quality=ProbeQuality.REACHABLE)
    r_slow = ThpResult(host="slow.com", port=443, thp_score_ms=120, quality=ProbeQuality.REACHABLE)
    r_fail = ThpResult(host="fail.com", port=443, thp_score_ms=-1, quality=ProbeQuality.FAILED)

    raw_list = [r_slow, r_fail, r_mid_r, r_fast, r_mid_v]
    sorted_list = sorted(raw_list, key=Thp._sort_key)

    # Order must be: fast (20), mid_v (50 VERIFIED), mid_r (50 REACHABLE), slow (120), fail (FAILED)
    assert sorted_list == [r_fast, r_mid_v, r_mid_r, r_slow, r_fail]


def test_thp_probe_many_auto_sorting(monkeypatch):
    # Test one-liner Thp.probe_many(["1.1.1.1:443", ("8.8.8.8", 443), "cloudflare.com"])
    fake_responses = {
        ("1.1.1.1", 443): ThpResult("1.1.1.1", 443, thp_score_ms=85, transport_rtt_ms=80.0, quality=ProbeQuality.VERIFIED),
        ("8.8.8.8", 443): ThpResult("8.8.8.8", 443, thp_score_ms=15, transport_rtt_ms=12.0, quality=ProbeQuality.VERIFIED),
        ("cloudflare.com", 443): ThpResult("cloudflare.com", 443, thp_score_ms=45, transport_rtt_ms=40.0, quality=ProbeQuality.REACHABLE),
        ("unreachable.net", 443): ThpResult("unreachable.net", 443, thp_score_ms=-1, quality=ProbeQuality.FAILED),
    }

    async def _mock_async_probe(host, port, *args, **kwargs):
        return fake_responses.get((host, port), ThpResult(host, port, quality=ProbeQuality.FAILED))

    monkeypatch.setattr(Thp, "async_probe", _mock_async_probe)

    targets = ["1.1.1.1:443", ("8.8.8.8", 443), "cloudflare.com", "unreachable.net:443"]
    results = Thp.probe_many(targets)

    # Automatically sorted from best ping to worst ping:
    # 1. 8.8.8.8 (15ms)
    # 2. cloudflare.com (45ms)
    # 3. 1.1.1.1 (85ms)
    # 4. unreachable.net (failed, -1ms)
    assert len(results) == 4
    assert [r.host for r in results] == ["8.8.8.8", "cloudflare.com", "1.1.1.1", "unreachable.net"]
    assert [r.thp_score_ms for r in results] == [15, 45, 85, -1]
    assert results[0].target == ("8.8.8.8", 443)
    assert results[1].target == "cloudflare.com"
    assert results[2].target == "1.1.1.1:443"
    assert results[3].target == "unreachable.net:443"


def test_probe_servers_thp_helper(monkeypatch):
    s1 = Server(name="Node A", host="node-a.com", port=443)
    s2 = Server(name="Node B", host="node-b.com", port=443)

    fake_responses = {
        ("node-a.com", 443): ThpResult("node-a.com", 443, thp_score_ms=90, quality=ProbeQuality.REACHABLE),
        ("node-b.com", 443): ThpResult("node-b.com", 443, thp_score_ms=30, quality=ProbeQuality.VERIFIED),
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
