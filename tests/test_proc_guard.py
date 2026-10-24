"""Tests for utils.engines.proc_guard.

Covers free-port selection, pid marker read/write/remove and stale
process cleanup, using real short-lived subprocesses plus a mocked
taskkill path so every platform branch is exercised. Windows liveness
goes through native ctypes seams (no child processes spawned).
"""
import io
import json
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from utils.engines import proc_guard as pg
from utils.server_model import ProxyProtocol
from utils.engines.xray_engine import XrayEngine
from utils.i18n import tr


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sleep_proc():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def importlib_rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class FreePortTest(unittest.TestCase):

    def test_port_availability_and_selection(self):
        # Free port check
        port = _free_port()
        self.assertTrue(pg.port_available("127.0.0.1", port))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            self.assertFalse(pg.port_available("127.0.0.1", port))

        # Pick free port
        p2 = _free_port()
        self.assertEqual(pg.pick_free_port(p2), p2)
        busy = _free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", busy))
            s.listen(1)
            a = pg.pick_free_port(busy)
            b = pg.pick_free_port(busy)
            self.assertNotEqual(a, busy)
            self.assertEqual(a, b)
            self.assertTrue(pg.port_available("127.0.0.1", a))
        self.assertEqual(pg.pick_free_port(busy), busy)

        # Wait for port available
        p3 = _free_port()
        self.assertTrue(pg.wait_for_port_available("127.0.0.1", p3, timeout=0.1))
        s3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s3.bind(("127.0.0.1", p3))
        s3.listen(1)
        self.assertFalse(pg.wait_for_port_available("127.0.0.1", p3, timeout=0.05))

        import threading
        def _delayed_close():
            import time
            time.sleep(0.02)
            s3.close()
        t = threading.Thread(target=_delayed_close)
        t.start()
        res = pg.wait_for_port_available("127.0.0.1", p3, timeout=0.5)
        t.join()
        self.assertTrue(res)


class MarkerTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="socksicle-marker-"))
        self.addCleanup(importlib_rmtree, self.root)
        tmp = mock.patch.object(pg, "get_config_dir", return_value=self.root)
        tmp.start()
        self.addCleanup(tmp.stop)

    def _marker(self, name="xray"):
        return self.root / pg.STATE_DIR_NAME / f"{name}.pid"

    def test_marker_crud_and_validation(self):
        # Write, read, remove
        pg.write_pid_marker("xray", 4242, "C:/bin/xray.exe")
        marker = pg.read_pid_marker("xray")
        self.assertEqual(marker["pid"], 4242)
        self.assertEqual(marker["binary"], "C:/bin/xray.exe")
        self.assertEqual(marker["engine"], "xray")

        pg.remove_pid_marker("xray")
        self.assertFalse(self._marker().exists())

        # Invalid or missing
        self.assertIsNone(pg.read_pid_marker("nonexistent"))
        pg.marker_path("xray").parent.mkdir(parents=True, exist_ok=True)
        self._marker().write_text("not json", encoding="utf-8")
        self.assertIsNone(pg.read_pid_marker("xray"))
        pg.write_pid_marker("xray", 0, "C:/bin/xray.exe")
        self.assertIsNone(pg.read_pid_marker("xray"))

        for invalid_pid in (True, False):
            self._marker().write_text(json.dumps({"pid": invalid_pid, "binary": "C:/bin/xray.exe"}), encoding="utf-8")
            self.assertIsNone(pg.read_pid_marker("xray"))

        # Paths match validation
        for bad in (["bad", "list"], {"bad": "dict"}, 1234, True, None):
            self.assertFalse(pg._paths_match(bad, "C:/bin/xray.exe"))
            self.assertFalse(pg._paths_match("C:/bin/xray.exe", bad))


class CleanupTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="socksicle-cleanup-"))
        self.addCleanup(importlib_rmtree, self.root)
        tmp = mock.patch.object(pg, "get_config_dir", return_value=self.root)
        tmp.start()
        self.addCleanup(tmp.stop)

    def _best_effort_kill(self, proc):
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass

    def test_cleans_stale_engine_process_and_kill_failure(self):
        proc = _sleep_proc()
        self.addCleanup(self._best_effort_kill, proc)
        pg.write_pid_marker("xray", proc.pid, pg.process_executable_path(proc.pid))
        actions = pg.cleanup_stale_engines(["xray"])
        self.assertTrue(any("killed stale xray" in a for a in actions))
        self.assertIsNotNone(proc.poll())
        self.assertFalse((self.root / pg.STATE_DIR_NAME / "xray.pid").exists())

        # Kill failure preserves marker
        proc2 = _sleep_proc()
        self.addCleanup(self._best_effort_kill, proc2)
        marker_dir = self.root / pg.STATE_DIR_NAME
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / "xray.pid").write_text(
            json.dumps({"pid": proc2.pid, "binary": pg.process_executable_path(proc2.pid)}),
            encoding="utf-8")
        with mock.patch.object(pg, "_kill_process", return_value=False):
            actions = pg.cleanup_stale_engines(["xray"])
        self.assertEqual(actions, [])
        self.assertTrue((marker_dir / "xray.pid").exists())

    def test_foreign_dead_and_corrupt_markers_handling(self):
        # Missing marker is noop
        self.assertEqual(pg.cleanup_stale_engines(["xray", "sing-box"]), [])

        # Foreign process marker not killed
        proc = _sleep_proc()
        self.addCleanup(self._best_effort_kill, proc)
        pg.write_pid_marker("xray", proc.pid, "C:/nowhere/xray.exe")
        actions = pg.cleanup_stale_engines(["xray"])
        self.assertTrue(any("recycled pid marker" in a for a in actions))
        self.assertIsNone(proc.poll())
        self.assertFalse((self.root / pg.STATE_DIR_NAME / "xray.pid").exists())

        # Dead marker removed without kill
        proc2 = _sleep_proc()
        pid2 = proc2.pid
        proc2.kill()
        proc2.wait(timeout=10)
        pg.write_pid_marker("xray", pid2, "C:/bin/xray.exe")
        with mock.patch.object(pg, "process_alive", return_value=False) as alive:
            actions = pg.cleanup_stale_engines(["xray"])
            alive.assert_called_once_with(pid2)
        self.assertTrue(any("removed stale pid marker" in a for a in actions))
        self.assertFalse((self.root / pg.STATE_DIR_NAME / "xray.pid").exists())

        # Corrupted / non-string binary marker does not abort cleanup
        proc3 = _sleep_proc()
        self.addCleanup(self._best_effort_kill, proc3)
        marker_dir = self.root / pg.STATE_DIR_NAME
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / "xray.pid").write_text(
            json.dumps({"pid": proc3.pid, "binary": ["invalid", "list"], "engine": "xray"}),
            encoding="utf-8")
        (marker_dir / "singbox.pid").write_text(
            json.dumps({"pid": 9999999, "binary": "C:/bin/singbox.exe", "engine": "singbox"}),
            encoding="utf-8")
        with mock.patch.object(pg, "process_alive", side_effect=lambda pid: pid == proc3.pid):
            actions = pg.cleanup_stale_engines(["xray", "singbox"])
        self.assertTrue(any("recycled pid marker xray" in a for a in actions))
        self.assertTrue(any("removed stale pid marker singbox" in a for a in actions))


class ProcessLivenessPlatformTest(unittest.TestCase):

    import contextlib

    @contextlib.contextmanager
    def _win_ctx(self, **seams):
        open_proc = seams.get("open", 0x1234)
        exit_code = seams.get("exit", pg.STILL_ACTIVE)
        last_error = seams.get("err", 0)
        ctx = [
            mock.patch.object(pg, "is_windows", return_value=True),
            mock.patch.object(pg, "_win_open_process", return_value=open_proc),
            mock.patch.object(pg, "_win_process_exit_code", return_value=exit_code),
            mock.patch.object(pg, "_win_last_error", return_value=last_error),
            mock.patch.object(pg, "_win_close_handle"),
        ]
        for c in ctx:
            c.start()
        try:
            yield
        finally:
            for c in ctx:
                c.stop()

    def test_process_alive_platform_implementations(self):
        # Windows via ctypes seams
        with mock.patch.object(pg, "is_windows", return_value=True), \
             mock.patch.object(pg.subprocess, "run", side_effect=AssertionError("must not spawn")) as run, \
             mock.patch.object(pg, "_win_open_process", return_value=0x1234) as open_proc, \
             mock.patch.object(pg, "_win_process_exit_code", return_value=259) as exit_code, \
             mock.patch.object(pg, "_win_close_handle") as close:
            self.assertTrue(pg.process_alive(4242))
        open_proc.assert_called_once_with(4242)
        exit_code.assert_called_once_with(0x1234)
        close.assert_called_once_with(0x1234)
        run.assert_not_called()

        with self._win_ctx(exit=1):
            self.assertFalse(pg.process_alive(4242))
        with self._win_ctx(open=None, err=2):
            self.assertFalse(pg.process_alive(4242))
        with self._win_ctx(open=None, err=5):
            self.assertTrue(pg.process_alive(4242))

        # POSIX alive uses sig0
        with mock.patch.object(pg, "is_windows", return_value=False), \
             mock.patch.object(pg.os, "kill", return_value=None) as kill:
            self.assertTrue(pg.process_alive(9))
        kill.assert_called_once_with(9, 0)

        # Real process
        proc = _sleep_proc()
        try:
            self.assertTrue(pg.process_alive(proc.pid))
        finally:
            proc.kill()
            proc.wait(timeout=10)
        self.assertFalse(pg.process_alive(proc.pid))

    def test_kill_win32_uses_taskkill(self):
        fake = mock.Mock(returncode=0, stdout="SUCCESS")
        with mock.patch.object(pg, "is_windows", return_value=True), \
             mock.patch.object(pg.subprocess, "run", return_value=fake) as run, \
             mock.patch.object(pg, "process_alive", return_value=False):
            self.assertTrue(pg._kill_process(4242))
        args = run.call_args[0][0]
        self.assertEqual(args[0], "taskkill")
        self.assertIn("/F", args)
        self.assertIn("/T", args)
        self.assertIn("4242", args)


class EngineIntegrationTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="socksicle-start-"))
        self.addCleanup(importlib_rmtree, self.root)
        tmp = mock.patch.object(pg, "get_config_dir", return_value=self.root)
        tmp.start()
        self.addCleanup(tmp.stop)

    def test_xray_build_config_ignores_busy_canonical_port(self):
        engine = XrayEngine()
        engine.local_port = _free_port()
        server = SimpleNamespace(protocol="shadowsocks", host="1.2.3.4", port=8388, method="aes-256-gcm", password="secret")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", 10085))
                s.listen(1)
            except OSError:
                pass
            config = engine.build_config(server)
        self.assertEqual(len(config["inbounds"]), 1)
        self.assertNotIn(10085, [i["port"] for i in config["inbounds"]])
        self.assertNotIn("api", config)
        self.assertNotIn("stats", config)
        self.assertNotIn("policy", config)
        self.assertEqual(config["inbounds"][0]["port"], engine.local_port)

    def test_start_marker_lifecycle_and_busy_port_guard(self):
        # Writes marker and teardown removes it
        port = 10800
        engine = XrayEngine()
        engine.local_port = port
        fake = mock.Mock(pid=4242)
        fake.poll.return_value = None
        fake.wait.side_effect = [0, 0]
        fake.stdout = io.StringIO("")
        fake.stderr = io.StringIO("")
        server = SimpleNamespace(protocol=ProxyProtocol.SHADOWSOCKS, host="1.2.3.4", port=8388, method="aes-256-gcm", password="pw")
        with mock.patch.object(engine, "find_binary", return_value=Path("C:/fake/xray.exe")), \
             mock.patch("utils.engines.base._secure_config_file"), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.proc_guard.wait_for_port_available", return_value=True), \
             mock.patch.object(pg.subprocess, "Popen", return_value=fake):
            ok = engine.start(server)
        self.assertTrue(ok)
        marker = pg.read_pid_marker("xray")
        self.assertEqual(marker["pid"], 4242)
        self.assertEqual(marker["binary"], str(Path("C:/fake/xray.exe")))
        engine.teardown()
        self.assertIsNone(pg.read_pid_marker("xray"))

        # Refuses busy local port without spawning
        busy = 10801
        engine_busy = XrayEngine()
        engine_busy.local_port = busy
        errors = []
        engine_busy.statusChanged.connect(lambda msg, err: errors.append((msg, err)))
        with mock.patch.object(engine_busy, "find_binary", return_value=Path("C:/fake/xray.exe")), \
             mock.patch("utils.engines.proc_guard.wait_for_port_available", return_value=False), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=False), \
             mock.patch("utils.engines.base._reserve_port", return_value=None), \
             mock.patch.object(pg.subprocess, "Popen") as popen:
            ok = engine_busy.start(server)
        self.assertFalse(ok)
        popen.assert_not_called()
        self.assertTrue(errors)
        msg, is_err = errors[-1]
        self.assertTrue(is_err)
        self.assertIn(tr("engine.port_in_use").format(port=busy), msg)
        self.assertIsNone(pg.read_pid_marker("xray"))


if __name__ == "__main__":
    unittest.main()
