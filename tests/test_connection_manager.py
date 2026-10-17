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

    def test_default_engine_is_singbox(self):
        mgr = ConnectionManager()
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)

    def test_settings_engine_used(self):
        mgr = ConnectionManager({"engine": "xray"})
        self.assertEqual(mgr.engine.engine_type, EngineType.XRAY)

    def test_default_state(self):
        mgr = ConnectionManager()
        self.assertEqual(mgr.state, DISCONNECTED)
        self.assertFalse(mgr.is_connected)
        self.assertFalse(mgr.is_connecting)

    def test_init_with_tun_mode(self):
        mgr = ConnectionManager({"tun_mode": True})
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)
        self.assertEqual(mgr.state, DISCONNECTED)
        self.assertFalse(mgr.is_connected)

    def test_local_port_from_settings(self):
        mgr = ConnectionManager({"local_port": 2080})
        self.assertEqual(mgr.local_port, 2080)

    def test_local_port_from_string_settings(self):
        mgr = ConnectionManager({"local_port": "2080"})
        self.assertEqual(mgr.local_port, 2080)

    def test_local_port_setter(self):
        mgr = ConnectionManager()
        mgr.local_port = 3080
        self.assertEqual(mgr.local_port, 3080)


class ConnectionManagerApplySettingsTest(unittest.TestCase):

    def test_apply_settings_sets_engine_port(self):
        mgr = ConnectionManager()
        mgr.apply_settings({"local_port": 2080})
        self.assertEqual(mgr.engine.local_port, 2080)
        self.assertEqual(mgr.local_port, 2080)

    def test_apply_settings_with_string_port(self):
        mgr = ConnectionManager()
        mgr.apply_settings({"local_port": "2080"})
        self.assertEqual(mgr.engine.local_port, 2080)

    def test_apply_settings_invalid_port_falls_back(self):
        mgr = ConnectionManager()
        mgr.apply_settings({"local_port": "abc"})
        self.assertEqual(mgr.engine.local_port, DEFAULT_LOCAL_PORT)

    def test_apply_settings_rejects_bool_and_out_of_range_port(self):
        mgr = ConnectionManager()
        for bad in (True, False, 0, -1, 65536, 70000, None):
            with self.subTest(bad=bad):
                mgr.apply_settings({"local_port": bad})
                self.assertEqual(mgr.engine.local_port, DEFAULT_LOCAL_PORT)

    def test_apply_settings_without_port_keeps_current(self):
        mgr = ConnectionManager({"local_port": 2080})
        mgr.apply_settings({"engine": "xray"})
        self.assertEqual(mgr.local_port, 2080)
        self.assertEqual(mgr.engine.local_port, 2080)

    def test_apply_settings_while_connected_keeps_state(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTED
        mgr._engine.is_connected = True
        with mock.patch.object(mgr._engine, "disconnect") as disc:
            mgr.apply_settings({"local_port": 2080})
            disc.assert_not_called()
        self.assertEqual(mgr.engine.local_port, 2080)
        self.assertEqual(mgr.state, CONNECTED)
        self.assertTrue(mgr.is_connected)

    def test_apply_settings_propagates_hy2_insecure(self):
        mgr = ConnectionManager({"engine": "sing-box"})
        try:
            mgr.apply_settings({"hy2_insecure": True})
            self.assertTrue(mgr.engine.hy2_insecure)
            mgr.apply_settings({"hy2_insecure": False})
            self.assertFalse(mgr.engine.hy2_insecure)
        finally:
            mgr.apply_settings({"hy2_insecure": False})

    def test_apply_settings_hy2_insecure_ignored_by_xray(self):
        mgr = ConnectionManager({"engine": "xray"})
        mgr.apply_settings({"hy2_insecure": True})
        self.assertFalse(hasattr(mgr.engine, "hy2_insecure"))

    def test_apply_settings_propagates_block_udp(self):
        for engine_key in ("sing-box", "xray"):
            mgr = ConnectionManager({"engine": engine_key})
            try:
                mgr.apply_settings({"block_udp": True})
                self.assertTrue(mgr.engine.block_udp)
                mgr.apply_settings({"block_udp": False})
                self.assertFalse(mgr.engine.block_udp)
            finally:
                mgr.apply_settings({"block_udp": False})

    def test_apply_settings_honors_killswitch_toggle(self):
        mgr = ConnectionManager({"killswitch": True})
        self.assertTrue(mgr.kill_switch_enabled)
        mgr.apply_settings({"killswitch": False})
        self.assertFalse(mgr.kill_switch_enabled)
        mgr.apply_settings({"killswitch": True})
        self.assertTrue(mgr.kill_switch_enabled)


class ConnectionManagerSwitchEngineTest(unittest.TestCase):

    def test_switch_engine(self):
        mgr = ConnectionManager()
        self.assertEqual(mgr.engine.engine_type, EngineType.SINGBOX)
        new_engine = get_engine(EngineType.XRAY)
        mgr.switch_engine(new_engine)
        self.assertEqual(mgr.engine.engine_type, EngineType.XRAY)

    def test_switch_engine_disconnects_if_connected(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTED
        mgr._engine.is_connected = True
        new_engine = get_engine(EngineType.XRAY)
        mgr.switch_engine(new_engine)
        self.assertEqual(mgr.engine.engine_type, EngineType.XRAY)
        self.assertEqual(mgr.state, DISCONNECTED)

    def test_switch_engine_preserves_port(self):
        mgr = ConnectionManager({"local_port": 2080})
        new_engine = get_engine(EngineType.XRAY)
        mgr.switch_engine(new_engine)
        self.assertEqual(mgr.local_port, 2080)

    def test_switch_engine_handles_disconnect_type_error_and_runtime_error(self):
        mgr = ConnectionManager()
        fake_old = mock.Mock()
        fake_old.statusChanged.disconnect.side_effect = TypeError("Not connected")
        fake_old.connectionStateChanged.disconnect.side_effect = RuntimeError("Object deleted")
        fake_old.logUpdated.disconnect.side_effect = TypeError("Not connected")
        fake_old.local_port = 1080
        fake_old.is_connected = False
        mgr._engine = fake_old

        fake_new = mock.Mock()
        fake_new.engine_type = EngineType.XRAY
        fake_new.statusChanged = mock.Mock()
        fake_new.connectionStateChanged = mock.Mock()
        fake_new.logUpdated = mock.Mock()

        # Should not raise exception
        mgr.switch_engine(fake_new)
        self.assertEqual(mgr.engine, fake_new)


class ConnectionManagerToggleTest(unittest.TestCase):

    def test_toggle_connect(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        with mock.patch.object(mgr._engine, "start", return_value=True):
            result = mgr.toggle(server, connect=True)
            self.assertTrue(result)
            self.assertEqual(mgr.state, CONNECTING)

    def test_toggle_disconnect(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTED
        with mock.patch.object(mgr._engine, "disconnect"):
            mgr.toggle(None, connect=False)
            self.assertEqual(mgr.state, DISCONNECTED)

    def test_toggle_while_connecting_cancels(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        with mock.patch.object(mgr._engine, "disconnect"):
            result = mgr.toggle(None)
            self.assertFalse(result)
            self.assertEqual(mgr.state, DISCONNECTED)

    def test_toggle_connect_failure(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        with mock.patch.object(mgr._engine, "start", return_value=False):
            result = mgr.toggle(server, connect=True)
            self.assertFalse(result)
            self.assertEqual(mgr.state, DISCONNECTED)

    def test_toggle_connect_failure_does_not_start_probe_retries(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        with mock.patch.object(mgr._engine, "start", return_value=False):
            result = mgr.toggle(server, connect=True)
        self.assertFalse(result)
        self.assertFalse(mgr.probe_timer.isActive())
        self.assertFalse(mgr.ping_timer.isActive())

    def test_toggle_auto_inverts(self):
        mgr = ConnectionManager()
        self.assertEqual(mgr.state, DISCONNECTED)
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        with mock.patch.object(mgr._engine, "start", return_value=True):
            result = mgr.toggle(server)
            self.assertTrue(result)

    def test_current_server(self):
        mgr = ConnectionManager()
        # The engine registry is process-wide; another test may have left a
        # server on it, so start from a known state.
        mgr._engine.current_server = None
        self.assertIsNone(mgr.current_server)
        mgr._engine.current_server = "fake"
        self.assertEqual(mgr.current_server, "fake")


class ConnectionManagerDisconnectTest(unittest.TestCase):

    def test_disconnect_stops_timers(self):
        mgr = ConnectionManager()
        mgr.probe_timer.start()
        mgr.ping_timer.start()
        with mock.patch.object(mgr._engine, "disconnect"):
            mgr.disconnect()
            self.assertFalse(mgr.probe_timer.isActive())
            self.assertFalse(mgr.ping_timer.isActive())

    def test_disconnect_resets_state(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr.is_connecting = True
        with mock.patch.object(mgr._engine, "disconnect"):
            mgr.disconnect()
            self.assertEqual(mgr.state, DISCONNECTED)
            self.assertFalse(mgr.is_connecting)


class ConnectionManagerProbeTest(unittest.TestCase):

    def test_probe_calls_handle_process_stopped_when_not_running(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr.probe_timer.start()
        with mock.patch.object(mgr._engine, "is_running", return_value=False), \
             mock.patch.object(mgr._engine, "disconnect"):
            mgr._probe()
            self.assertEqual(mgr.state, DISCONNECTED)

    def test_probe_detects_proxy_ready(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr._probe_deadline = time.monotonic() + 10
        fake_pool = SimpleNamespace(start=lambda job: job.run())
        with mock.patch.object(mgr._engine, "is_running", return_value=True), \
             mock.patch("utils.connection_manager.socks5_proxy_ready", return_value=True), \
             mock.patch.object(mgr._engine, "confirm_connected"), \
             mock.patch("utils.connection_manager.fetch_ip_info_via_proxy", return_value=None), \
             mock.patch("utils.connection_manager.QThreadPool.globalInstance",
                        return_value=fake_pool):
            try:
                mgr._probe()
                self.assertEqual(mgr.state, CONNECTED)
            finally:
                # Drop back to DISCONNECTED while mocks are still active so
                # the daemon _fetch_geo thread exits instead of leaking real
                # network retries (ip-api.com) across subsequent tests.
                mgr.state = DISCONNECTED
                mgr.ping_timer.stop()
                mgr.probe_timer.stop()

    def test_probe_timeout_fails(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTING
        mgr._probe_deadline = time.monotonic() - 1  # already expired
        with mock.patch.object(mgr._engine, "is_running", return_value=True), \
             mock.patch.object(mgr._engine, "teardown"):
            mgr._probe()
            self.assertEqual(mgr.state, DISCONNECTED)


class ConnectionManagerPingTest(unittest.TestCase):

    def test_update_ping_skips_when_disconnected(self):
        mgr = ConnectionManager()
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance") as gi:
            mgr._update_ping()
            gi.assert_not_called()

    def test_update_ping_starts_proxy_ping_job(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTED
        mgr._geo_last_attempt = time.monotonic()
        started = []
        fake_pool = SimpleNamespace(start=started.append)
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance",
                        return_value=fake_pool):
            mgr._update_ping()
        self.assertEqual(len(started), 1)
        job = started[0]
        self.assertIsInstance(job, ProxyPingJob)
        self.assertEqual(job.host, PING_PROBE_HOST)
        self.assertEqual(job.port, mgr.local_port)
        self.assertEqual(job.method, "http_head")

    def test_update_ping_always_uses_http_head_method(self):
        mgr = ConnectionManager({"ping_method": "tcp_connect"})
        mgr.state = CONNECTED
        mgr._geo_last_attempt = time.monotonic()
        started = []
        fake_pool = SimpleNamespace(start=started.append)
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance",
                        return_value=fake_pool):
            mgr._update_ping()
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].method, "http_head")

    def test_update_ping_ignores_settings_ping_method_for_active_ping(self):
        mgr = ConnectionManager({"ping_method": "bogus"})
        mgr.state = CONNECTED
        mgr._geo_last_attempt = time.monotonic()
        started = []
        fake_pool = SimpleNamespace(start=started.append)
        with mock.patch("utils.connection_manager.QThreadPool.globalInstance",
                        return_value=fake_pool):
            mgr._update_ping()
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].method, "http_head")

    def test_ping_result_stale_generation_ignored(self):
        mgr = ConnectionManager()
        mgr.state = CONNECTED
        emitted = []
        mgr.pingResultReady.connect(emitted.append)
        mgr._generation = 7
        mgr._on_ping_result(6, 12.0)
        self.assertEqual(emitted, [])
        mgr._on_ping_result(7, 34.0)
        self.assertEqual(emitted, [34.0])

    def test_ping_result_ignored_after_disconnect(self):
        mgr = ConnectionManager()
        mgr.state = DISCONNECTED
        emitted = []
        mgr.pingResultReady.connect(emitted.append)
        mgr._on_ping_result(mgr._generation, 12.0)
        self.assertEqual(emitted, [])


class ConnectionManagerToggleSerializationTest(unittest.TestCase):
    """Rapid connect/disconnect clicks must serialize, not race for the engine."""

    def test_concurrent_toggles_serialize_on_toggle_lock(self):
        mgr = ConnectionManager()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
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
            # The second toggle waits on the lock instead of entering
            # engine.start() while the first one is still inside it.
            self.assertEqual(calls, ["enter"])
            release.set()
            t1.join(timeout=5)
            t2.join(timeout=5)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(mgr.state, CONNECTING)


def test_async_probe_job_delivers_result_via_signal(qapp):
    """_AsyncProbeJob reports through ConnectionManager.asyncProbeFinished.

    Native cross-thread signal emission is queued to the GUI thread,
    replacing the old QMetaObject.invokeMethod/Q_ARG call.
    """
    from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
    from utils.connection_manager import _AsyncProbeJob

    mgr = ConnectionManager()
    received = []
    loop = QEventLoop()
    mgr.asyncProbeFinished.connect(
        lambda gen, ready: (received.append((gen, ready)), loop.quit()))
    with mock.patch("utils.connection_manager.socks5_proxy_ready",
                     return_value=True):
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

    # Mock engine supporting hot switch
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


if __name__ == "__main__":
    unittest.main()
