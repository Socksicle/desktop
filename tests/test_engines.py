"""Tests for utils.engines (base, xray_engine, singbox_engine).

All subprocess and network activity is mocked.
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from utils.engines.base import (
    ProxyEngine, EngineType, CheckResult, set_pdeathsig, check_local_port,
    _secure_config_file,
)
from utils.engines import common
from utils.engines.xray_engine import XrayEngine, _detect_target as xray_detect
from utils.engines.singbox_engine import SingBoxEngine, _detect_target as sb_detect


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def _magic_for_this_platform():
    if sys.platform == "win32":
        return b"MZ"
    if sys.platform == "darwin":
        return b"\xfe\xed\xfa\xcf"
    return b"\x7fELF"


def _make_file(path, magic=None, size=2_000_000, executable=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    magic = magic if magic is not None else _magic_for_this_platform()
    payload = magic + b"\x00" * (size - len(magic))
    path.write_bytes(payload)
    if executable and os.name != "nt":
        path.chmod(0o755)
    return path


class CheckLocalPortTest(unittest.TestCase):

    def test_valid_ports_pass_through_as_int(self):
        self.assertEqual(check_local_port(1080), 1080)
        self.assertEqual(check_local_port(1), 1)
        self.assertEqual(check_local_port(65535), 65535)
        self.assertEqual(check_local_port("2080"), 2080)

    def test_bool_out_of_range_and_garbage_rejected(self):
        for bad in (True, False, 0, -1, 65536, 70000, None, "abc"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError, OverflowError)):
                    check_local_port(bad)


class EngineTypeTest(unittest.TestCase):

    def test_values(self):
        self.assertEqual(EngineType.XRAY.value, "xray")
        self.assertEqual(EngineType.SINGBOX.value, "sing-box")

    def test_string_enum(self):
        self.assertEqual(str(EngineType.SINGBOX), "EngineType.SINGBOX")
        self.assertIsInstance(EngineType.SINGBOX, str)


class ProxyEngineBaseTest(unittest.TestCase):
    """Test ProxyEngine base class behaviour (via SingBoxEngine)."""

    def test_signals_defined(self):
        engine = SingBoxEngine()
        self.assertTrue(hasattr(engine, 'statusChanged'))
        self.assertTrue(hasattr(engine, 'connectionStateChanged'))
        self.assertTrue(hasattr(engine, 'logUpdated'))

    def test_default_state(self):
        engine = SingBoxEngine()
        self.assertIsNone(engine.process)
        self.assertEqual(engine.local_port, 1080)
        self.assertFalse(engine.is_connected)
        self.assertIsNone(engine.current_server)
        self.assertIsNone(engine.last_exit_code)

    def test_is_running_false_when_no_process(self):
        engine = SingBoxEngine()
        self.assertFalse(engine.is_running())

    def test_confirm_connected(self):
        engine = SingBoxEngine()
        signals = []
        engine.connectionStateChanged.connect(lambda v: signals.append(v))
        engine.confirm_connected()
        self.assertTrue(engine.is_connected)
        self.assertEqual(signals, [True])

    def test_disconnect_resets_state(self):
        engine = SingBoxEngine()
        engine.is_connected = True
        signals = []
        engine.connectionStateChanged.connect(lambda v: signals.append(v))
        engine.disconnect_from_server()
        self.assertFalse(engine.is_connected)
        self.assertIn(False, signals)

    def test_get_current_server(self):
        engine = SingBoxEngine()
        self.assertIsNone(engine.get_current_server())
        engine.current_server = "fake"
        self.assertEqual(engine.get_current_server(), "fake")

    def test_teardown_stops_process(self):
        engine = SingBoxEngine()
        fake_proc = SimpleNamespace(
            pid=123, poll=mock.Mock(return_value=None),
            terminate=mock.Mock(), wait=mock.Mock(return_value=0))
        engine.process = fake_proc
        engine.is_connected = True
        engine.teardown()
        self.assertIsNone(engine.process)
        self.assertFalse(engine.is_connected)
        fake_proc.terminate.assert_called_once()

    def test_teardown_kills_on_timeout(self):
        engine = SingBoxEngine()
        import subprocess
        fake_proc = SimpleNamespace(
            pid=123, poll=mock.Mock(return_value=None),
            terminate=mock.Mock(), kill=mock.Mock(),
            wait=mock.Mock(side_effect=[subprocess.TimeoutExpired("", 2), 0]))
        engine.process = fake_proc
        with mock.patch("sys.platform", "linux"):
            engine.teardown()
        fake_proc.terminate.assert_called_once()
        fake_proc.kill.assert_called_once()


class SecureConfigFileTest(unittest.TestCase):
    """Windows ACL hardening must fail closed, never leave a readable config."""

    def _make_config(self):
        fd, path = tempfile.mkstemp(prefix="proxy-", suffix=".json")
        os.close(fd)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        return path

    def test_icacls_failure_raises(self):
        path = self._make_config()
        with mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run",
                        return_value=mock.Mock(returncode=5)):
            with self.assertRaises(PermissionError) as ctx:
                _secure_config_file(path)
        self.assertIn("icacls", str(ctx.exception))

    def test_icacls_unavailable_raises(self):
        path = self._make_config()
        with mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run",
                        side_effect=FileNotFoundError("icacls")):
            with self.assertRaises(PermissionError):
                _secure_config_file(path)

    def test_icacls_success_keeps_file(self):
        path = self._make_config()
        with mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run",
                        return_value=mock.Mock(returncode=0)):
            _secure_config_file(path)
        self.assertTrue(os.path.exists(path))

    def test_build_args_removes_config_when_icacls_fails(self):
        engine = SingBoxEngine()
        engine.local_port = 1080
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="secret")
        real_mkstemp = tempfile.mkstemp
        created = []

        def spy(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return fd, path

        with mock.patch("utils.engines.base.tempfile.mkstemp", side_effect=spy), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run",
                        return_value=mock.Mock(returncode=5)):
            with self.assertRaises(PermissionError):
                engine.build_args(server)
        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())
        self.assertIsNone(engine._config_path)


class XrayEngineTest(unittest.TestCase):

    def test_engine_metadata_and_teardown(self):
        engine = XrayEngine()
        self.assertEqual(engine.engine_type, EngineType.XRAY)
        self.assertEqual(engine.process_name(), "xray")
        self.assertEqual(engine.version_args(), ["version"])

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        engine._config_path = Path(path)
        self.assertTrue(Path(path).exists())
        engine.teardown()
        self.assertFalse(Path(path).exists())

    def test_detect_target_platform(self):
        result = xray_detect()
        valid_targets = ["windows-64", "windows-arm64-v8a",
                         "linux-64", "linux-arm64",
                         "darwin-amd64", "darwin-arm64"]
        self.assertIn(result, valid_targets)


class SingBoxEngineTest(unittest.TestCase):

    def test_engine_metadata_and_teardown(self):
        engine = SingBoxEngine()
        self.assertEqual(engine.engine_type, EngineType.SINGBOX)
        self.assertEqual(engine.process_name(), "sing-box")
        self.assertEqual(engine.version_args(), ["version"])

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        engine._config_path = Path(path)
        self.assertTrue(Path(path).exists())
        engine.teardown()
        self.assertFalse(Path(path).exists())



class XrayEngineBinaryTest(unittest.TestCase):
    """Test xray_engine binary finding and usability."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_check_usable_none(self):
        engine = XrayEngine()
        result = engine.check_usable(None)
        self.assertFalse(result.usable)
        self.assertIn("No xray path", result.reason)

    def test_find_binary_discovers_manual_subdir_app(self):
        fake = _make_file(self.root / "bin" / "xray"
                          / common._binary_name("xray"))
        with mock.patch("utils.platform_utils.get_app_dir",
                        return_value=self.root), \
             mock.patch("utils.platform_utils.get_config_dir",
                        return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            result = common._find_binary("xray")
        self.assertEqual(result, fake)

    def test_find_binary_discovers_manual_subdir_config(self):
        fake = _make_file(self.root / "config" / "bin" / "xray"
                          / common._binary_name("xray"))
        with mock.patch("utils.platform_utils.get_app_dir",
                        return_value=self.root / "app"), \
             mock.patch("utils.platform_utils.get_config_dir",
                        return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            result = common._find_binary("xray")
        self.assertEqual(result, fake)

    @unittest.skipIf(os.name != "nt",
                     "flat file and engine subdir share a name only on Windows "
                     "(bin/xray vs bin/xray/)")
    def test_find_binary_prefers_flat_bin_over_subdir(self):
        expected = _make_file(self.root / "bin"
                              / common._binary_name("xray"))
        _make_file(self.root / "bin" / "xray"
                   / common._binary_name("xray"))
        with mock.patch("utils.platform_utils.get_app_dir",
                        return_value=self.root), \
             mock.patch("utils.platform_utils.get_config_dir",
                        return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            result = common._find_binary("xray")
        self.assertEqual(result, expected)

    def test_check_usable_too_small(self):
        engine = XrayEngine()
        path = _make_file(self.root / "xray", size=1000)
        result = engine.check_usable(path)
        self.assertFalse(result.usable)
        self.assertIn("too small", result.reason)


class SingBoxEngineBinaryTest(unittest.TestCase):
    """Test singbox_engine binary finding and usability."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_check_usable_none(self):
        engine = SingBoxEngine()
        result = engine.check_usable(None)
        self.assertFalse(result.usable)
        self.assertIn("No sing-box path", result.reason)

    def test_find_binary_discovers_manual_singbox_subdir(self):
        fake = _make_file(self.root / "bin" / "singbox"
                          / common._binary_name("sing-box"))
        with mock.patch("utils.platform_utils.get_app_dir",
                        return_value=self.root), \
             mock.patch("utils.platform_utils.get_config_dir",
                        return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            result = common._find_binary("sing-box")
        self.assertEqual(result, fake)

    def test_local_bin_subdir_mapping(self):
        self.assertEqual(common._local_bin_subdir("sing-box"), "singbox")
        self.assertEqual(common._local_bin_subdir("xray"), "xray")

    def test_check_usable_too_small(self):
        engine = SingBoxEngine()
        path = _make_file(self.root / "sing-box", size=1000)
        result = engine.check_usable(path)
        self.assertFalse(result.usable)
        self.assertIn("too small", result.reason)


class SingBoxEngineTunCapabilitiesTest(unittest.TestCase):
    """Test Linux TUN capability checks and grants during SingBoxEngine start."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin_path = _make_file(self.root / "bin" / "sing-box")
        self.server = SimpleNamespace(
            host="1.2.3.4", port=8388, method="aes-256-gcm", password="pw"
        )

    def test_start_tun_linux_starts_after_grant_check(self):
        """Engine consults grant_tun_capabilities once before starting TUN."""
        engine = SingBoxEngine()
        engine.tun_mode = True
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None),
                              stdout=io.StringIO(""), stderr=io.StringIO(""))
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=True), \
             mock.patch("utils.platform_utils.grant_tun_capabilities", return_value=True) as grant_cap, \
              mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
              mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("subprocess.Popen", return_value=fake_proc):
            ok = engine.start(self.server)
            self.assertTrue(ok)
            grant_cap.assert_called_once_with(self.bin_path)
            engine.teardown()

    def test_start_tun_linux_grants_capabilities_when_missing(self):
        engine = SingBoxEngine()
        engine.tun_mode = True
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None),
                              stdout=io.StringIO(""), stderr=io.StringIO(""))
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=True), \
             mock.patch("utils.platform_utils.grant_tun_capabilities", return_value=True) as grant_cap, \
              mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
              mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("subprocess.Popen", return_value=fake_proc):
            ok = engine.start(self.server)
            self.assertTrue(ok)
            grant_cap.assert_called_once_with(self.bin_path)
            engine.teardown()

    def test_start_tun_linux_fails_when_grant_declined(self):
        engine = SingBoxEngine()
        engine.tun_mode = True
        statuses = []
        engine.statusChanged.connect(lambda msg, err: statuses.append((msg, err)))
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=True), \
             mock.patch("utils.platform_utils.grant_tun_capabilities", return_value=False) as grant_cap:
            ok = engine.start(self.server)
            self.assertFalse(ok)
            grant_cap.assert_called_once_with(self.bin_path)
            from utils.i18n import tr
            self.assertTrue(any(tr("tun.no_caps") in s[0] and s[1] for s in statuses))

    def test_start_tun_linux_fails_when_tun_device_missing(self):
        engine = SingBoxEngine()
        engine.tun_mode = True
        statuses = []
        engine.statusChanged.connect(lambda msg, err: statuses.append((msg, err)))
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=False), \
             mock.patch("utils.platform_utils.grant_tun_capabilities") as grant_cap:
            ok = engine.start(self.server)
            self.assertFalse(ok)
            grant_cap.assert_not_called()
            self.assertTrue(any("/dev/net/tun" in s[0] and s[1] for s in statuses))


class PdeathsigAndProcessLifecycleTest(unittest.TestCase):
    """Test Linux PR_SET_PDEATHSIG and platform-specific process launch options."""

    def test_set_pdeathsig_noop_on_windows(self):
        with mock.patch("sys.platform", "win32"):
            self.assertFalse(set_pdeathsig())

    def test_set_pdeathsig_noop_on_darwin(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertFalse(set_pdeathsig())

    def test_set_pdeathsig_linux_success(self):
        fake_libc = mock.MagicMock()
        fake_libc.prctl.return_value = 0
        with mock.patch("sys.platform", "linux"), \
             mock.patch("ctypes.CDLL", return_value=fake_libc), \
             mock.patch("ctypes.util.find_library", return_value="libc.so.6"):
            res = set_pdeathsig()
            self.assertTrue(res)
            import signal
            fake_libc.prctl.assert_called_once()
            args = fake_libc.prctl.call_args[0]
            self.assertEqual(args[0], 1)  # PR_SET_PDEATHSIG
            self.assertEqual(args[1].value, signal.SIGTERM)

    def test_set_pdeathsig_linux_handles_failure(self):
        fake_libc = mock.MagicMock()
        fake_libc.prctl.return_value = -1
        with mock.patch("sys.platform", "linux"), \
             mock.patch("ctypes.CDLL", return_value=fake_libc):
            res = set_pdeathsig()
            self.assertFalse(res)

    def test_set_pdeathsig_linux_handles_exception(self):
        with mock.patch("sys.platform", "linux"), \
             mock.patch("ctypes.CDLL", side_effect=OSError("libc not found")):
            res = set_pdeathsig()
            self.assertFalse(res)

    def test_engine_start_linux_passes_preexec_fn(self):
        engine = SingBoxEngine()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None),
                              stdout=io.StringIO(""), stderr=io.StringIO(""))
        with mock.patch.object(engine, "find_binary", return_value=Path("/fake/bin/sing-box")), \
              mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
              mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("sys.platform", "linux"), \
             mock.patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
            ok = engine.start(server)
            self.assertTrue(ok)
            popen_mock.assert_called_once()
            kwargs = popen_mock.call_args[1]
            self.assertIn("preexec_fn", kwargs)
            self.assertEqual(kwargs["preexec_fn"], set_pdeathsig)
            self.assertNotIn("creationflags", kwargs)
            self.assertTrue(kwargs["close_fds"])
            engine.teardown()

    def test_engine_start_windows_passes_creationflags_not_preexec(self):
        engine = SingBoxEngine()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None),
                              stdout=io.StringIO(""), stderr=io.StringIO(""))
        with mock.patch.object(engine, "find_binary", return_value=Path("C:/fake/bin/sing-box.exe")), \
              mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
              mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("utils.engines.base._secure_config_file"), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
            ok = engine.start(server)
            self.assertTrue(ok)
            popen_mock.assert_called_once()
            kwargs = popen_mock.call_args[1]
            self.assertNotIn("preexec_fn", kwargs)
            self.assertIn("creationflags", kwargs)
            self.assertFalse(kwargs["close_fds"])
            engine.teardown()

    def test_engine_start_fails_when_config_cannot_be_secured(self):
        engine = SingBoxEngine()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        status = []
        engine.statusChanged.connect(lambda msg, err: status.append((msg, err)))
        with mock.patch.object(engine, "find_binary", return_value=Path("C:/fake/bin/sing-box.exe")), \
              mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
              mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
              mock.patch("utils.engines.base.cleanup_stale_engines"), \
              mock.patch("utils.engines.base.write_pid_marker"), \
              mock.patch("utils.engines.base._secure_config_file",
                         side_effect=PermissionError("icacls failed")), \
              mock.patch("sys.platform", "win32"), \
              mock.patch("subprocess.Popen") as popen_mock:
            ok = engine.start(server)
        self.assertFalse(ok)
        popen_mock.assert_not_called()
        self.assertIsNone(engine.process)
        self.assertTrue(any(err for _, err in status))

    def test_engine_start_darwin_no_creationflags_no_preexec(self):
        engine = SingBoxEngine()
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw")
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None),
                              stdout=io.StringIO(""), stderr=io.StringIO(""))
        with mock.patch.object(engine, "find_binary", return_value=Path("/fake/bin/sing-box")), \
              mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
              mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("sys.platform", "darwin"), \
             mock.patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
            ok = engine.start(server)
            self.assertTrue(ok)
            popen_mock.assert_called_once()
            kwargs = popen_mock.call_args[1]
            self.assertNotIn("preexec_fn", kwargs)
            self.assertNotIn("creationflags", kwargs)
            self.assertTrue(kwargs["close_fds"])
            engine.teardown()


if __name__ == "__main__":
    unittest.main()
