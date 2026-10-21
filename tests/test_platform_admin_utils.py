"""Focused tests for utils.platform_utils is_admin / elevate_restart platform guards.

All platform detection and subprocess activity is mocked; nothing is executed.
"""
import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import platform_utils


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxIsAdminTest(unittest.TestCase):

    def test_is_admin_linux(self):
        with mock.patch("utils.platform_utils.os.geteuid", return_value=0, create=True):
            self.assertTrue(platform_utils.is_admin())
        with mock.patch("utils.platform_utils.os.geteuid", return_value=1000, create=True):
            self.assertFalse(platform_utils.is_admin())
        with mock.patch("utils.platform_utils.os.geteuid", create=True, new=None):
            self.assertFalse(platform_utils.is_admin())


@mock.patch("utils.platform_utils.sys.platform", "darwin")
class UnsupportedPlatformTest(unittest.TestCase):

    def test_off_platform_defaults(self):
        self.assertFalse(platform_utils.is_admin())
        self.assertFalse(platform_utils.elevate_restart())


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxElevateRestartTest(unittest.TestCase):

    def test_pkexec_and_sudo_fallback(self):
        exit_ = mock.Mock(side_effect=SystemExit(0))
        argv = ["/opt/socksicle/app.py", "--flag"]

        # Case 1: pkexec available
        with mock.patch("shutil.which", return_value="/usr/bin/pkexec"), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable", "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit", exit_):
            with self.assertRaises(SystemExit):
                platform_utils.elevate_restart()
            popen.assert_called_once_with(["pkexec", "/usr/bin/python3", argv[0], "--flag"])

        # Case 2: pkexec fails, fallback to sudo
        calls = []
        def _popen(args, **kwargs):
            calls.append(args)
            if args[0] == "pkexec":
                raise OSError("pkexec launch failed")
            return mock.Mock()

        with mock.patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}"), \
             mock.patch("subprocess.Popen", side_effect=_popen), \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable", "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit", exit_):
            with self.assertRaises(SystemExit):
                platform_utils.elevate_restart()
            self.assertEqual(calls[0][0], "pkexec")
            self.assertEqual(calls[1][0], "sudo")

    def test_returns_false_when_no_elevator(self):
        argv = ["/opt/socksicle/app.py"]
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable", "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit") as exit_:
            self.assertFalse(platform_utils.elevate_restart())
            popen.assert_not_called()
            exit_.assert_not_called()


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxCheckTunCapabilitiesTest(unittest.TestCase):

    def test_check_tun_capabilities_success(self):
        fake_proc = mock.Mock(returncode=0, stdout="/path/to/sing-box cap_net_admin,cap_net_bind_service=ep\n")
        bin_path = "/path/to/sing-box"
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=fake_proc) as run_mock:
            self.assertTrue(platform_utils.check_tun_capabilities(bin_path))
            run_mock.assert_called_once_with(
                ["/usr/sbin/getcap", str(Path(bin_path))],
                capture_output=True, text=True, timeout=5,
            )

    def test_check_tun_capabilities_failure_modes(self):
        # 1. Missing cap
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="cap_net_bind_service=ep\n")):
            self.assertFalse(platform_utils.check_tun_capabilities("/path/to/sing-box"))

        # 2. Getcap nonzero exit
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="err")):
            self.assertFalse(platform_utils.check_tun_capabilities("/path/to/sing-box"))

        # 3. Missing getcap / missing file / None / OSError
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value=None):
            self.assertFalse(platform_utils.check_tun_capabilities("/path/to/sing-box"))

        with mock.patch("pathlib.Path.is_file", return_value=False):
            self.assertFalse(platform_utils.check_tun_capabilities("/nonexistent"))

        self.assertFalse(platform_utils.check_tun_capabilities(None))

        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", side_effect=OSError("exec failed")):
            self.assertFalse(platform_utils.check_tun_capabilities("/path/to/sing-box"))


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxGrantTunCapabilitiesTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_grant_tun_capabilities_success_and_fast_path(self):
        bin_path = "/path/to/sing-box"
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")

        # Fast path: already granted
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("subprocess.run") as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            self.assertTrue(platform_utils.grant_tun_capabilities(bin_path))
            run_mock.assert_not_called()

        # Elevation flow: missing caps -> pkexec setcap -> verified True
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", side_effect=[False, True]), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            self.assertTrue(platform_utils.grant_tun_capabilities(bin_path))
            self.assertIn("setcap cap_net_admin,cap_net_bind_service+eip", run_mock.call_args[0][0][3])

    def test_grant_tun_capabilities_polkit_rule_integration(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        src = self.root / "polkit" / "20-socksicle-tun.rules"
        src.parent.mkdir(parents=True)
        src.write_text("polkit.addRule(function(action) {});", encoding="utf-8")
        marker_path = self.root / "marker" / "tun_polkit_rule.marker"

        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=True), \
             mock.patch("utils.platform_utils._tun_rule_source", return_value=src), \
             mock.patch("utils.platform_utils._tun_rule_in_sync", side_effect=[False, True]), \
             mock.patch("utils.platform_utils._rule_marker_path", return_value=marker_path):
            self.assertTrue(platform_utils.grant_tun_capabilities(str(self.root / "bin" / "sing-box")))
            script = run_mock.call_args[0][0][3]
            self.assertIn(f"install -m 0644 {shlex.quote(str(src))}", script)
            self.assertEqual(marker_path.read_text(encoding="utf-8"),
                             hashlib.sha256(src.read_bytes()).hexdigest())

    def test_grant_tun_capabilities_failure_cases(self):
        # User canceled (returncode 126)
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=126, stderr="dismissed")), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            self.assertFalse(platform_utils.grant_tun_capabilities("/path/to/sing-box"))

        # Missing pkexec
        def _is_file(path):
            return str(path) == "/path/to/sing-box"
        with mock.patch("pathlib.Path.is_file", autospec=True, side_effect=_is_file), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.run") as run_mock:
            self.assertFalse(platform_utils.grant_tun_capabilities("/path/to/sing-box"))
            run_mock.assert_not_called()


        # Missing binary
        with mock.patch("pathlib.Path.is_file", return_value=False):
            self.assertFalse(platform_utils.grant_tun_capabilities("/nonexistent"))

        # Timeout
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pkexec", 60)), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            self.assertFalse(platform_utils.grant_tun_capabilities("/path/to/sing-box"))


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxTunRuleMarkerTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.src = self.root / "data" / "polkit" / "20-socksicle-tun.rules"
        self.src.parent.mkdir(parents=True)
        self.src.write_text("polkit.addRule(function(action) {});", encoding="utf-8")
        self.marker = self.root / "cfg" / "tun_polkit_rule.marker"

    def test_rule_in_sync_marker_verification(self):
        hidden_dir = self.root / "rules.d"
        hidden_dir.mkdir()
        os.chmod(hidden_dir, 0o000)

        with mock.patch.object(platform_utils, "_TUN_RULE_DEST", str(hidden_dir / "x.rules")), \
             mock.patch.object(platform_utils, "_tun_rule_source", return_value=self.src), \
             mock.patch.object(platform_utils, "_rule_marker_path", return_value=self.marker):
            # No marker yet
            self.assertFalse(platform_utils._tun_rule_in_sync())
            # Write marker
            platform_utils._write_rule_marker(self.src)
            self.assertTrue(platform_utils._tun_rule_in_sync())
            # Stale marker
            self.marker.write_text("stale", encoding="utf-8")
            self.assertFalse(platform_utils._tun_rule_in_sync())


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxSystemdResolvedTest(unittest.TestCase):

    def test_systemd_resolved_detection(self):
        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)):
            self.assertTrue(platform_utils._systemd_resolved_active())

        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=3)):
            self.assertFalse(platform_utils._systemd_resolved_active())

        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(platform_utils._systemd_resolved_active())


@mock.patch("utils.platform_utils.sys.platform", "win32")
class NonLinuxCapabilitiesTest(unittest.TestCase):

    def test_tun_capabilities_noop_on_windows(self):
        self.assertFalse(platform_utils.check_tun_capabilities("C:\\bin\\sing-box.exe"))
        self.assertFalse(platform_utils.grant_tun_capabilities("C:\\bin\\sing-box.exe"))
