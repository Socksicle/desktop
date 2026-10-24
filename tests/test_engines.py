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

    def test_check_local_port_validation(self):
        # Valid ports
        self.assertEqual(check_local_port(1080), 1080)
        self.assertEqual(check_local_port(1), 1)
        self.assertEqual(check_local_port(65535), 65535)

        # Invalid ports / types: strings and floats are not coerced
        for bad in (True, False, 0, -1, 65536, 70000, None, "abc",
                    "2080", 1080.0, 1080.9):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError, OverflowError)):
                    check_local_port(bad)


class EngineTypeTest(unittest.TestCase):

    def test_values_and_str(self):
        self.assertEqual(EngineType.XRAY.value, "xray")
        self.assertEqual(EngineType.SINGBOX.value, "sing-box")
        self.assertEqual(str(EngineType.SINGBOX), "EngineType.SINGBOX")
        self.assertIsInstance(EngineType.SINGBOX, str)


class ProxyEngineBaseTest(unittest.TestCase):
    """Test ProxyEngine base class behaviour (via SingBoxEngine)."""

    def test_base_engine_lifecycle_and_state(self):
        import subprocess

        engine = SingBoxEngine()
        self.assertTrue(hasattr(engine, 'statusChanged'))
        self.assertTrue(hasattr(engine, 'connectionStateChanged'))
        self.assertTrue(hasattr(engine, 'logUpdated'))
        self.assertIsNone(engine.process)
        self.assertEqual(engine.local_port, 1080)
        self.assertFalse(engine.is_connected)
        self.assertIsNone(engine.current_server)
        self.assertFalse(engine.is_running())
        engine.current_server = "fake"
        self.assertEqual(engine.get_current_server(), "fake")

        # Signals and state transitions
        signals = []
        engine.connectionStateChanged.connect(signals.append)
        engine.confirm_connected()
        self.assertTrue(engine.is_connected)
        self.assertEqual(signals, [True])

        engine.disconnect_from_server()
        self.assertFalse(engine.is_connected)
        self.assertIn(False, signals)

        # Normal terminate teardown
        fake_proc = SimpleNamespace(
            pid=123, poll=mock.Mock(return_value=None),
            terminate=mock.Mock(), wait=mock.Mock(return_value=0))
        engine.process = fake_proc
        engine.is_connected = True
        engine.teardown()
        self.assertIsNone(engine.process)
        self.assertFalse(engine.is_connected)
        fake_proc.terminate.assert_called_once()

        # Kill on timeout
        engine2 = SingBoxEngine()
        fake_proc2 = SimpleNamespace(
            pid=123, poll=mock.Mock(return_value=None),
            terminate=mock.Mock(), kill=mock.Mock(),
            wait=mock.Mock(side_effect=[subprocess.TimeoutExpired("", 2), 0]))
        engine2.process = fake_proc2
        with mock.patch("sys.platform", "linux"):
            engine2.teardown()
        fake_proc2.kill.assert_called_once()


class SecureConfigFileTest(unittest.TestCase):
    """Windows ACL hardening must fail closed, never leave a readable config."""

    def _make_config(self):
        fd, path = tempfile.mkstemp(prefix="proxy-", suffix=".json")
        os.close(fd)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        return path

    def test_windows_icacls_security_and_cleanup(self):
        path = self._make_config()
        # Failure returncode raises PermissionError
        with mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run", return_value=mock.Mock(returncode=5)):
            with self.assertRaises(PermissionError):
                _secure_config_file(path)

        # Unavailable icacls raises PermissionError
        with mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run", side_effect=FileNotFoundError("icacls")):
            with self.assertRaises(PermissionError):
                _secure_config_file(path)

        # Success keeps file
        with mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run", return_value=mock.Mock(returncode=0)):
            _secure_config_file(path)
        self.assertTrue(os.path.exists(path))

        # Build args removes temporary config when icacls fails
        engine = SingBoxEngine()
        engine.local_port = 1080
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="secret")
        real_mkstemp = tempfile.mkstemp
        created = []

        def spy(*args, **kwargs):
            fd, p = real_mkstemp(*args, **kwargs)
            created.append(p)
            return fd, p

        with mock.patch("utils.engines.base.tempfile.mkstemp", side_effect=spy), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("utils.engines.base.subprocess.run",
                        return_value=mock.Mock(returncode=5)):
            with self.assertRaises(PermissionError):
                engine.build_args(server)
        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())
        self.assertIsNone(engine._config_path)

    def test_windows_config_is_secured_before_content_is_written(self):
        engine = SingBoxEngine()
        engine.local_port = 1080
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="secret")
        snapshots = []

        def secure(path):
            snapshots.append(Path(path).read_text(encoding="utf-8"))

        with mock.patch("utils.engines.base._secure_config_file", side_effect=secure), \
             mock.patch("sys.platform", "win32"):
            args = engine.build_args(server)
        self.addCleanup(engine._cleanup_config)
        # The config file must still be empty when its ACLs are restricted;
        # otherwise the secret existed in %TEMP% with inherited permissions.
        self.assertEqual(snapshots, [""])
        self.assertIn("secret", Path(args[-1]).read_text(encoding="utf-8"))


class EngineMetadataAndTargetTest(unittest.TestCase):

    def test_engine_metadata_teardown_and_targets(self):
        for engine_cls, expected_type, expected_name, detect_fn, valid_targets in [
            (
                XrayEngine,
                EngineType.XRAY,
                "xray",
                xray_detect,
                ["windows-64", "windows-arm64-v8a", "linux-64", "linux-arm64", "darwin-amd64", "darwin-arm64"],
            ),
            (
                SingBoxEngine,
                EngineType.SINGBOX,
                "sing-box",
                sb_detect,
                ["windows-amd64", "windows-arm64", "linux-amd64", "linux-arm64", "darwin-amd64", "darwin-arm64"],
            ),
        ]:
            engine = engine_cls()
            self.assertEqual(engine.engine_type, expected_type)
            self.assertEqual(engine.process_name(), expected_name)
            self.assertEqual(engine.version_args(), ["version"])

            fd, path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            engine._config_path = Path(path)
            self.assertTrue(Path(path).exists())
            engine.teardown()
            self.assertFalse(Path(path).exists())

            self.assertIn(detect_fn(), valid_targets)


class EngineBinaryDiscoveryAndUsabilityTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_binary_finding_and_mapping(self):
        self.assertEqual(common._local_bin_subdir("sing-box"), "singbox")
        self.assertEqual(common._local_bin_subdir("xray"), "xray")

        # Discover in app dir
        fake_xray_app = _make_file(self.root / "bin" / "xray" / common._binary_name("xray"))
        with mock.patch("utils.platform_utils.get_app_dir", return_value=self.root), \
             mock.patch("utils.platform_utils.get_config_dir", return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            self.assertEqual(common._find_binary("xray"), fake_xray_app)

        # Discover in config dir
        fake_xray_cfg = _make_file(self.root / "config" / "bin" / "xray" / common._binary_name("xray"))
        with mock.patch("utils.platform_utils.get_app_dir", return_value=self.root / "app"), \
             mock.patch("utils.platform_utils.get_config_dir", return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            self.assertEqual(common._find_binary("xray"), fake_xray_cfg)

        # Discover singbox subdir
        fake_sb = _make_file(self.root / "bin" / "singbox" / common._binary_name("sing-box"))
        with mock.patch("utils.platform_utils.get_app_dir", return_value=self.root), \
             mock.patch("utils.platform_utils.get_config_dir", return_value=self.root / "config"), \
             mock.patch.object(common.shutil, "which", return_value=None):
            self.assertEqual(common._find_binary("sing-box"), fake_sb)

        # Flat binary preference over subdir (Windows)
        if os.name == "nt":
            expected = _make_file(self.root / "bin" / common._binary_name("xray"))
            with mock.patch("utils.platform_utils.get_app_dir", return_value=self.root), \
                 mock.patch("utils.platform_utils.get_config_dir", return_value=self.root / "config"), \
                 mock.patch.object(common.shutil, "which", return_value=None):
                self.assertEqual(common._find_binary("xray"), expected)

    def test_check_usable_validation(self):
        for engine, name in [(XrayEngine(), "xray"), (SingBoxEngine(), "sing-box")]:
            # None path
            res_none = engine.check_usable(None)
            self.assertFalse(res_none.usable)
            self.assertIn(f"No {name} path", res_none.reason)

            # Too small
            small_path = _make_file(self.root / f"small_{name}", size=1000)
            res_small = engine.check_usable(small_path)
            self.assertFalse(res_small.usable)
            self.assertIn("too small", res_small.reason)


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

    def test_start_tun_linux_capabilities_and_device_handling(self):
        from utils.i18n import tr
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None),
                              stdout=io.StringIO(""), stderr=io.StringIO(""))

        # Success path (grants check & startup)
        engine = SingBoxEngine()
        engine.tun_mode = True
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=True), \
             mock.patch("utils.platform_utils.grant_tun_capabilities", return_value=True) as grant_cap, \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("subprocess.Popen", return_value=fake_proc):
            self.assertTrue(engine.start(self.server))
            grant_cap.assert_called_once_with(self.bin_path)
            engine.teardown()

        # Failure: grant declined
        engine2 = SingBoxEngine()
        engine2.tun_mode = True
        statuses = []
        engine2.statusChanged.connect(lambda msg, err: statuses.append((msg, err)))
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine2, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=True), \
             mock.patch("utils.platform_utils.grant_tun_capabilities", return_value=False) as grant_cap:
            self.assertFalse(engine2.start(self.server))
            grant_cap.assert_called_once_with(self.bin_path)
            self.assertTrue(any(tr("tun.no_caps") in s[0] and s[1] for s in statuses))

        # Failure: tun device missing
        engine3 = SingBoxEngine()
        engine3.tun_mode = True
        statuses3 = []
        engine3.statusChanged.connect(lambda msg, err: statuses3.append((msg, err)))
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(engine3, "find_binary", return_value=self.bin_path), \
             mock.patch("utils.engines.singbox_engine.os.path.exists", return_value=False), \
             mock.patch("utils.platform_utils.grant_tun_capabilities") as grant_cap:
            self.assertFalse(engine3.start(self.server))
            grant_cap.assert_not_called()
            self.assertTrue(any("/dev/net/tun" in s[0] and s[1] for s in statuses3))


class PdeathsigAndProcessLifecycleTest(unittest.TestCase):
    """Test Linux PR_SET_PDEATHSIG and platform-specific process launch options."""

    def test_set_pdeathsig_platforms_and_error_handling(self):
        # Windows & Darwin no-op
        with mock.patch("sys.platform", "win32"):
            self.assertFalse(set_pdeathsig())
        with mock.patch("sys.platform", "darwin"):
            self.assertFalse(set_pdeathsig())

        # Linux success
        fake_libc = mock.MagicMock()
        fake_libc.prctl.return_value = 0
        with mock.patch("sys.platform", "linux"), \
             mock.patch("ctypes.CDLL", return_value=fake_libc), \
             mock.patch("ctypes.util.find_library", return_value="libc.so.6"):
            self.assertTrue(set_pdeathsig())
            fake_libc.prctl.assert_called_once()

        # Linux handles failure & exception
        fake_libc.prctl.return_value = -1
        with mock.patch("sys.platform", "linux"), mock.patch("ctypes.CDLL", return_value=fake_libc):
            self.assertFalse(set_pdeathsig())

        with mock.patch("sys.platform", "linux"), mock.patch("ctypes.CDLL", side_effect=OSError("libc not found")):
            self.assertFalse(set_pdeathsig())

    def test_engine_start_platform_popen_flags_and_failure(self):
        engine = SingBoxEngine()
        server = SimpleNamespace(host="1.2.3.4", port=8388, method="aes-256-gcm", password="pw")
        fake_proc = mock.Mock(pid=1234, poll=mock.Mock(return_value=None), stdout=io.StringIO(""), stderr=io.StringIO(""))

        # Linux: preexec_fn + close_fds
        with mock.patch.object(engine, "find_binary", return_value=Path("/fake/bin/sing-box")), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("sys.platform", "linux"), \
             mock.patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
            self.assertTrue(engine.start(server))
            kwargs = popen_mock.call_args_list[0][1]
            self.assertEqual(kwargs["preexec_fn"], set_pdeathsig)
            self.assertTrue(kwargs["close_fds"])
            engine.teardown()

        # Windows: creationflags, not preexec
        with mock.patch.object(engine, "find_binary", return_value=Path("C:/fake/bin/sing-box.exe")), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("utils.engines.base._secure_config_file"), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
            self.assertTrue(engine.start(server))
            kwargs = popen_mock.call_args[1]
            self.assertNotIn("preexec_fn", kwargs)
            self.assertIn("creationflags", kwargs)
            engine.teardown()

        # Darwin: no preexec, no creationflags
        with mock.patch.object(engine, "find_binary", return_value=Path("/fake/bin/sing-box")), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("sys.platform", "darwin"), \
             mock.patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
            self.assertTrue(engine.start(server))
            kwargs = popen_mock.call_args[1]
            self.assertNotIn("preexec_fn", kwargs)
            self.assertNotIn("creationflags", kwargs)
            engine.teardown()

        # Failure: config cannot be secured
        status = []
        engine.statusChanged.connect(lambda msg, err: status.append((msg, err)))
        with mock.patch.object(engine, "find_binary", return_value=Path("C:/fake/bin/sing-box.exe")), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("utils.engines.base._secure_config_file", side_effect=PermissionError("icacls failed")), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("subprocess.Popen") as popen_mock:
            self.assertFalse(engine.start(server))
            popen_mock.assert_not_called()
            self.assertIsNone(engine.process)
            self.assertTrue(any(err for _, err in status))


if __name__ == "__main__":
    unittest.main()
