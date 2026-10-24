"""Tests for updated utils.connection_manager (engine abstraction)."""
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
import pytest

from utils.engines.base import EngineType, DEFAULT_LOCAL_PORT
from utils.engines.xray_engine import XrayEngine
from utils.engines.engine_manager import get_engine
from utils.ping import ProxyPingJob, PING_PROBE_HOST
from utils.connection_manager import (
    ConnectionManager, DISCONNECTED, CONNECTING, CONNECTED,
)
from utils.server_model import Server


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class ConnectionManagerInitTest(unittest.TestCase):

    def test_init_defaults_and_port_handling(self):
        # Default state
        mgr = ConnectionManager()
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)
        self.assertEqual(mgr.state, DISCONNECTED)
        self.assertFalse(mgr.is_connected)
        self.assertFalse(mgr.is_connecting)

        # Settings overrides
        self.assertEqual(ConnectionManager({"engine": "xray"}).engine.engine_type, EngineType.XRAY)
        mgr_tun = ConnectionManager({"tun_mode": True})
        self.assertEqual(mgr_tun.engine.engine_type, EngineType.SINGBOX)
        self.assertEqual(mgr_tun.state, DISCONNECTED)

        # Port init & setter
        self.assertEqual(ConnectionManager({"local_port": 2080}).local_port, 2080)
        # Non-int settings values are invalid and fall back to the default
        self.assertEqual(
            ConnectionManager({"local_port": "2080"}).local_port,
            DEFAULT_LOCAL_PORT)
        mgr.local_port = 3080
        self.assertEqual(mgr.local_port, 3080)


class ConnectionManagerApplySettingsTest(unittest.TestCase):

    def test_apply_settings_port_handling_and_connected_state(self):
        mgr = ConnectionManager({"local_port": 2080})
        # Settings without port keeps current
        mgr.apply_settings({"engine": "xray"})
        self.assertEqual(mgr.local_port, 2080)
        self.assertEqual(mgr.engine.local_port, 2080)

        # Non-int values, invalid text, bool and out-of-range ports fall back
        for bad in ("3080", "abc", 2080.5, 2080.0, True, False, 0, -1,
                    65536, 70000, None):
            mgr.apply_settings({"local_port": bad})
            self.assertEqual(mgr.engine.local_port, DEFAULT_LOCAL_PORT)

        # Applying while connected keeps state and doesn't disconnect
        mgr.state = CONNECTED
        mgr._engine.is_connected = True
        with mock.patch.object(mgr._engine, "disconnect") as disc:
            mgr.apply_settings({"local_port": 2080})
            disc.assert_not_called()
        self.assertEqual(mgr.engine.local_port, 2080)
        self.assertEqual(mgr.state, CONNECTED)
        self.assertTrue(mgr.is_connected)

    def test_apply_settings_propagation_options(self):
        # hy2_insecure on sing-box, ignored on xray
        mgr_sb = ConnectionManager({"engine": "sing-box"})
        try:
            mgr_sb.apply_settings({"hy2_insecure": True})
            self.assertTrue(mgr_sb.engine.hy2_insecure)
            mgr_sb.apply_settings({"hy2_insecure": False})
            self.assertFalse(mgr_sb.engine.hy2_insecure)
        finally:
            mgr_sb.apply_settings({"hy2_insecure": False})

        mgr_xray = ConnectionManager({"engine": "xray"})
        mgr_xray.apply_settings({"hy2_insecure": True})
        self.assertFalse(hasattr(mgr_xray.engine, "hy2_insecure"))

        # block_udp on sing-box & xray
        for e_key in ("sing-box", "xray"):
            mgr = ConnectionManager({"engine": e_key})
            try:
                mgr.apply_settings({"block_udp": True})
                self.assertTrue(mgr.engine.block_udp)
                mgr.apply_settings({"block_udp": False})
                self.assertFalse(mgr.engine.block_udp)
            finally:
                mgr.apply_settings({"block_udp": False})

        # killswitch toggle
        mgr_ks = ConnectionManager({"killswitch": True})
        self.assertTrue(mgr_ks.kill_switch_enabled)
        mgr_ks.apply_settings({"killswitch": False})
        self.assertFalse(mgr_ks.kill_switch_enabled)
        mgr_ks.apply_settings({"killswitch": True})
        self.assertTrue(mgr_ks.kill_switch_enabled)

        # tunnel options (fragment, mux)
        try:
            mgr_sb.apply_settings({"tls_fragment": True, "mux_enabled": True, "mux_protocol": "smux", "mux_max_connections": 9})
            self.assertTrue(mgr_sb.engine.tls_fragment)
            self.assertTrue(mgr_sb.engine.mux_enabled)
            self.assertEqual(mgr_sb.engine.mux_protocol, "smux")
            self.assertEqual(mgr_sb.engine.mux_max_connections, 9)
        finally:
            mgr_sb.apply_settings({"tls_fragment": False, "mux_enabled": False, "mux_protocol": "h2mux", "mux_max_connections": 4})


class ConnectionManagerSwitchEngineTest(unittest.TestCase):

    def setUp(self):
        for et in (EngineType.SINGBOX, EngineType.XRAY):
            eng = get_engine(et)
            for attr, val in (("custom_dns", None), ("block_udp", False), ("tun_mode", False), ("hy2_insecure", False), ("tls_fragment", False), ("mux_enabled", False)):
                if hasattr(eng, attr):
                    setattr(eng, attr, val)

    def test_switch_engine_lifecycle_and_errors(self):
        mgr = ConnectionManager()
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)
        new_engine = get_engine(EngineType.XRAY)
        mgr.switch_engine(new_engine)
        self.assertEqual(mgr.engine.engine_type, EngineType.XRAY)

        # Disconnects if connected
        mgr.state = CONNECTED
        mgr._engine.is_connected = True
        mgr.switch_engine(get_engine(EngineType.SINGBOX))
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)
        self.assertEqual(mgr.state, DISCONNECTED)

        # Preserves port
        mgr2 = ConnectionManager({"local_port": 2080})
        mgr2.switch_engine(get_engine(EngineType.XRAY))
        self.assertEqual(mgr2.local_port, 2080)

        # Carries engine settings
        mgr3 = ConnectionManager()
        mgr3.apply_settings({"custom_dns": "https://dns.quad9.net/dns-query", "block_udp": True})
        mgr3.switch_engine(get_engine(EngineType.XRAY))
        self.assertEqual(mgr3.engine.custom_dns, "https://dns.quad9.net/dns-query")
        self.assertTrue(mgr3.engine.block_udp)

        # Handles disconnect type error and runtime error safely
        fake_old = mock.Mock()
        fake_old.statusChanged.disconnect.side_effect = TypeError("Not connected")
        fake_old.connectionStateChanged.disconnect.side_effect = RuntimeError("Object deleted")
        fake_old.logUpdated.disconnect.side_effect = TypeError("Not connected")
        fake_old.local_port = 1080
        fake_old.is_connected = False
        mgr3._engine = fake_old
        fake_new = mock.Mock(engine_type=EngineType.XRAY, statusChanged=mock.Mock(), connectionStateChanged=mock.Mock(), logUpdated=mock.Mock())
        mgr3.switch_engine(fake_new)
        self.assertEqual(mgr3.engine, fake_new)

    def test_switch_engine_stops_engine_while_connecting(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr.is_connecting = True
        old = mgr._engine
        with mock.patch.object(old, "disconnect_from_server") as stop, \
             mock.patch("utils.killswitch.KillSwitchManager.get_instance") as get_ks:
            mgr.switch_engine(get_engine(EngineType.XRAY))
        stop.assert_called_once()
        get_ks.return_value.disable.assert_called_once()
        self.assertEqual(mgr.state, DISCONNECTED)
        self.assertFalse(mgr.is_connecting)
        self.assertEqual(mgr.engine.engine_type, EngineType.XRAY)

    def test_switch_keeps_custom_dns(self):
        # TUN switch keeps custom dns
        mgr = ConnectionManager({"engine": "xray", "custom_dns": "https://dns.quad9.net/dns-query"})
        mgr.apply_settings({"tun_mode": True})
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)
        self.assertEqual(mgr.engine.custom_dns, "https://dns.quad9.net/dns-query")

        # Engine switch keeps custom dns
        mgr2 = ConnectionManager({"custom_dns": "https://dns.quad9.net/dns-query"})
        mgr2.apply_settings({"engine": "xray"})
        self.assertEqual(mgr2.engine.engine_type, EngineType.XRAY)
        self.assertEqual(mgr2.engine.custom_dns, "https://dns.quad9.net/dns-query")


class ConnectionManagerToggleTest(unittest.TestCase):

    def setUp(self):
        for et in (EngineType.SINGBOX, EngineType.XRAY):
            eng = get_engine(et)
            for attr, val in (("custom_dns", None), ("block_udp", False), ("tun_mode", False), ("hy2_insecure", False), ("tls_fragment", False), ("mux_enabled", False)):
                if hasattr(eng, attr):
                    setattr(eng, attr, val)

    def test_switch_during_start_tears_down_started_engine(self):
        mgr = ConnectionManager()
        old = mgr._engine
        new = get_engine(EngineType.XRAY)
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")

        def start_then_switch(_server):
            mgr.switch_engine(new)
            return True

        with mock.patch.object(old, "start", side_effect=start_then_switch), \
             mock.patch.object(old, "disconnect_from_server"), \
             mock.patch.object(old, "teardown") as old_teardown, \
             mock.patch.object(new, "teardown") as new_teardown, \
             mock.patch("utils.killswitch.KillSwitchManager.get_instance"):
            self.assertFalse(mgr.toggle(server, True))

        old_teardown.assert_called_once()
        new_teardown.assert_not_called()
        self.assertIs(mgr.engine, new)
        self.assertEqual(mgr.state, DISCONNECTED)
        self.assertFalse(mgr.is_connecting)

    def test_toggle_lifecycle_and_cancellation(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388, method="aes-256-gcm", password="pw")

        # Connect
        with mock.patch.object(mgr._engine, "start", return_value=True):
            self.assertTrue(mgr.toggle(server, connect=True))
            self.assertEqual(mgr.state, CONNECTING)

        # While connecting cancels
        with mock.patch.object(mgr._engine, "disconnect"):
            self.assertFalse(mgr.toggle(None))
            self.assertEqual(mgr.state, DISCONNECTED)

        # Auto inverts from disconnected to connecting
        with mock.patch.object(mgr._engine, "start", return_value=True):
            self.assertTrue(mgr.toggle(server))

        # Disconnect
        mgr.state = CONNECTED
        with mock.patch.object(mgr._engine, "disconnect"):
            mgr.toggle(None, connect=False)
            self.assertEqual(mgr.state, DISCONNECTED)

        # Current server property
        mgr._engine.current_server = None
        self.assertIsNone(mgr.current_server)
        mgr._engine.current_server = "fake"
        self.assertEqual(mgr.current_server, "fake")

    def test_toggle_failure_and_autoswitch(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388, method="aes-256-gcm", password="pw")
        with mock.patch.object(mgr._engine, "start", return_value=False):
            self.assertFalse(mgr.toggle(server, connect=True))
            self.assertEqual(mgr.state, DISCONNECTED)
            self.assertFalse(mgr.probe_timer.isActive())
            self.assertFalse(mgr.ping_timer.isActive())

        # Auto-switch keeps custom DNS
        DNS_URL = "https://dns.quad9.net/dns-query"
        mgr_as = ConnectionManager({"custom_dns": DNS_URL})
        server_xhttp = SimpleNamespace(host="1.2.3.4", port=443, password="pw", transport="xhttp")
        xray = get_engine(EngineType.XRAY)
        with mock.patch.object(xray, "start", return_value=True):
            self.assertTrue(mgr_as.toggle(server_xhttp, connect=True))
            self.assertIs(mgr_as.engine, xray)
            self.assertEqual(xray.custom_dns, DNS_URL)


class ConnectionManagerDisconnectTest(unittest.TestCase):

    def test_disconnect_resets_state_and_timers(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr.is_connecting = True
        mgr.probe_timer.start()
        mgr.ping_timer.start()
        with mock.patch.object(mgr._engine, "disconnect"):
            mgr.disconnect()
            self.assertEqual(mgr.state, DISCONNECTED)
            self.assertFalse(mgr.is_connecting)
            self.assertFalse(mgr.probe_timer.isActive())
            self.assertFalse(mgr.ping_timer.isActive())


class ConnectionManagerProbeTest(unittest.TestCase):

    def test_probe_detection_and_timeouts(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr.probe_timer.start()

        # Handle process stopped when not running
        with mock.patch.object(mgr._engine, "is_running", return_value=False), \
             mock.patch.object(mgr._engine, "disconnect"):
            mgr._probe()
            self.assertEqual(mgr.state, DISCONNECTED)

        # Detects proxy ready
        mgr.state = CONNECTING
        mgr._probe_deadline = time.monotonic() + 10
        fake_pool = SimpleNamespace(start=lambda job: job.run())
        with mock.patch.object(mgr._engine, "is_running", return_value=True), \
             mock.patch("utils.connection_manager.socks5_proxy_ready", return_value=True), \
             mock.patch.object(mgr._engine, "confirm_connected"), \
             mock.patch("utils.connection_manager.fetch_ip_info_via_proxy", return_value=None), \
             mock.patch("utils.connection_manager.QThreadPool.globalInstance", return_value=fake_pool):
            try:
                mgr._probe()
                self.assertEqual(mgr.state, CONNECTED)
            finally:
                mgr.state = DISCONNECTED
                mgr.ping_timer.stop()
                mgr.probe_timer.stop()

        # Timeout fails
        mgr.state = CONNECTING
        mgr._probe_deadline = time.monotonic() - 1
        with mock.patch.object(mgr._engine, "is_running", return_value=True), \
             mock.patch.object(mgr._engine, "teardown"):
            mgr._probe()
            self.assertEqual(mgr.state, DISCONNECTED)


class ConnectionManagerPingTest(unittest.TestCase):

    def test_update_ping_dispatch_and_invariants(self):
        mgr = ConnectionManager({"ping_url": "https://cp.cloudflare.com/generate_204", "ping_timeout": 6})
        # Skips when disconnected
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance") as gi:
            mgr._update_ping()
            gi.assert_not_called()

        # Starts proxy ping job with http_head regardless of settings ping_method
        mgr.state = CONNECTED
        mgr._geo_last_attempt = time.monotonic()
        started = []
        fake_pool = SimpleNamespace(start=started.append)
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance", return_value=fake_pool):
            mgr._update_ping()
        self.assertEqual(len(started), 1)
        job = started[0]
        self.assertIsInstance(job, ProxyPingJob)
        self.assertEqual(job.host, "cp.cloudflare.com")
        self.assertEqual(job.port, mgr.local_port)
        self.assertEqual(job.method, "http_head")
        self.assertEqual(job.timeout, 6.0)
        self.assertEqual(job.url, "https://cp.cloudflare.com/generate_204")

    def test_update_ping_invalid_url_reports_error(self):
        mgr = ConnectionManager({"ping_url": "ftp://example.com/check"})
        mgr.state = CONNECTED
        results = []
        mgr.pingResultReady.connect(results.append)
        started = []
        fake_pool = SimpleNamespace(start=started.append)
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance",
                        return_value=fake_pool):
            mgr._update_ping()
        self.assertEqual(results, [None])
        self.assertEqual(started, [])

    def test_ping_result_invalidation(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTED
        emitted = []
        mgr.pingResultReady.connect(emitted.append)

        # Stale generation ignored
        mgr._generation = 7
        mgr._on_ping_result(6, 12.0)
        self.assertEqual(emitted, [])
        mgr._on_ping_result(7, 34.0)
        self.assertEqual(emitted, [34.0])

        # Ignored after disconnect
        mgr.state = DISCONNECTED
        emitted.clear()
        mgr._on_ping_result(mgr._generation, 12.0)
        self.assertEqual(emitted, [])


class ConnectionManagerToggleSerializationTest(unittest.TestCase):

    def test_concurrent_toggles_serialize_on_toggle_lock(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388, method="aes-256-gcm", password="pw")
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow_start(srv):
            calls.append("enter")
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            calls.append("exit")
            return True

        with mock.patch.object(mgr._engine, "start", side_effect=slow_start), \
             mock.patch.object(mgr._engine, "teardown"), \
             mock.patch.object(mgr._engine, "disconnect_from_server"), \
             mock.patch.object(mgr._engine, "is_running", return_value=False):
            t1 = threading.Thread(target=mgr.toggle, args=(server, True))
            t1.start()
            self.assertTrue(entered.wait(timeout=5))
            t2 = threading.Thread(target=mgr.toggle, args=(server, True))
            t2.start()
            time.sleep(0.3)
            self.assertEqual(calls, ["enter"])
            release.set()
            t1.join(timeout=5)
            t2.join(timeout=5)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(mgr.state, CONNECTING)


def test_async_probe_job_delivers_result_via_signal(qapp):
    from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
    from utils.connection_manager import _AsyncProbeJob

    mgr = ConnectionManager()
    received = []
    loop = QEventLoop()
    mgr.asyncProbeFinished.connect(lambda gen, ready: (received.append((gen, ready)), loop.quit()))
    with mock.patch("utils.connection_manager.socks5_proxy_ready", return_value=True):
        QThreadPool.globalInstance().start(_AsyncProbeJob(mgr, 7, 1080))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
    assert received == [(7, True)]


def test_connection_manager_hot_switch_without_disconnect(qapp):
    mgr = ConnectionManager()
    s1 = Server(name="Node 1", host="1.1.1.1", port=443)
    s2 = Server(name="Node 2", host="2.2.2.2", port=443)

    mgr.state = CONNECTED
    mgr._last_connected_server = s1
    mgr.engine.can_switch_server = mock.MagicMock(return_value=True)
    mgr.engine.switch_server = mock.MagicMock(return_value=True)
    mgr.engine.disconnect_from_server = mock.MagicMock()

    with mock.patch.object(mgr, "_fetch_geo"), \
         mock.patch.object(mgr, "_update_ping"):
        res = mgr.toggle(s2, True)

    assert res is True
    mgr.engine.can_switch_server.assert_called_once_with(s2)
    mgr.engine.switch_server.assert_called_once_with(s2)
    mgr.engine.disconnect_from_server.assert_not_called()
    assert mgr._last_connected_server == s2


def test_process_stopped_killswitch_handling(qapp):
    from utils.killswitch import KillSwitchManager

    # 1. Connected engine crashes and exhausts reconnects -> must disable killswitch
    mgr = ConnectionManager()
    server = Server(name="Test", host="1.2.3.4", port=443)
    mgr.state = CONNECTED
    mgr._last_connected_server = server
    mgr._auto_reconnect_attempts = mgr.MAX_AUTO_RECONNECTS

    with mock.patch.object(KillSwitchManager, "get_instance") as mock_get_ks:
        mock_ks = mock.Mock()
        mock_get_ks.return_value = mock_ks
        mgr._handle_process_stopped()
        mock_ks.disable.assert_called_once()
    assert mgr.state == DISCONNECTED
    assert mgr.is_connecting is False

    # 2. Reconnecting keeps killswitch active to avoid leak during reconnect
    mgr = ConnectionManager()
    mgr.state = CONNECTED
    mgr._last_connected_server = server
    mgr._auto_reconnect_attempts = 0

    with mock.patch.object(KillSwitchManager, "get_instance") as mock_get_ks, \
         mock.patch("PySide6.QtCore.QTimer.singleShot"):
        mock_ks = mock.Mock()
        mock_get_ks.return_value = mock_ks
        mgr._handle_process_stopped()
        mock_ks.disable.assert_not_called()
    assert mgr.is_connecting is True

    # 3. Crash during connecting cleans up killswitch
    mgr = ConnectionManager()
    mgr.state = CONNECTING
    with mock.patch.object(KillSwitchManager, "get_instance") as mock_get_ks:
        mock_ks = mock.Mock()
        mock_get_ks.return_value = mock_ks
        mgr._handle_process_stopped()
        mock_ks.disable.assert_called_once()
    assert mgr.state == DISCONNECTED


if __name__ == "__main__":
    unittest.main()
