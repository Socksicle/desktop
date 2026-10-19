"""Focused tests for utils.platform_utils is_admin / elevate_restart platform guards.

All platform detection and subprocess activity is mocked; nothing is executed.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import platform_utils


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxIsAdminTest(unittest.TestCase):

    def test_is_admin_true_when_root(self):
        with mock.patch("utils.platform_utils.os.geteuid",
                        return_value=0, create=True):
            self.assertTrue(platform_utils.is_admin())

    def test_is_admin_false_when_not_root(self):
        with mock.patch("utils.platform_utils.os.geteuid",
                        return_value=1000, create=True):
            self.assertFalse(platform_utils.is_admin())

    def test_is_admin_false_when_no_geteuid(self):
        with mock.patch("utils.platform_utils.os.geteuid", create=True, new=None):
            self.assertFalse(platform_utils.is_admin())


@mock.patch("utils.platform_utils.sys.platform", "darwin")
class UnsupportedPlatformTest(unittest.TestCase):

    def test_is_admin_false_off_platform(self):
        self.assertFalse(platform_utils.is_admin())

    def test_elevate_restart_false_off_platform(self):
        self.assertFalse(platform_utils.elevate_restart())


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxElevateRestartTest(unittest.TestCase):

    def test_pkexec_used_when_available(self):
        exit_ = mock.Mock(side_effect=SystemExit(0))
        argv = ["/opt/socksicle/app.py", "--flag"]
        with mock.patch("shutil.which",
                        return_value="/usr/bin/pkexec"), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable",
                                "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit", exit_):
            with self.assertRaises(SystemExit):
                platform_utils.elevate_restart()
        popen.assert_called_once_with(
            ["pkexec", "/usr/bin/python3", argv[0], "--flag"])

    def test_sudo_fallback_when_pkexec_missing(self):
        exit_ = mock.Mock(side_effect=SystemExit(0))
        argv = ["/opt/socksicle/app.py"]
        def _which(cmd):
            return "/usr/bin/sudo" if cmd == "sudo" else None
        with mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable",
                                "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit", exit_):
            with self.assertRaises(SystemExit):
                platform_utils.elevate_restart()
        popen.assert_called_once_with(
            ["sudo", "/usr/bin/python3", argv[0]])

    def test_returns_false_when_no_elevator(self):
        argv = ["/opt/socksicle/app.py"]
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable",
                                "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit") as exit_:
            result = platform_utils.elevate_restart()
        self.assertFalse(result)
        popen.assert_not_called()
        exit_.assert_not_called()

    def test_sudo_fallback_when_pkexec_exec_fails(self):
        exit_ = mock.Mock(side_effect=SystemExit(0))
        argv = ["/opt/socksicle/app.py"]
        calls = []

        def _which(cmd):
            return f"/usr/bin/{cmd}"

        def _popen(args, **kwargs):
            calls.append(args)
            if args[0] == "pkexec":
                raise OSError("pkexec launch failed")
            return mock.Mock()

        with mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.Popen", side_effect=_popen), \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable",
                                "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit", exit_):
            with self.assertRaises(SystemExit):
                platform_utils.elevate_restart()
        self.assertEqual(calls[0][0], "pkexec")
        self.assertEqual(calls[1][0], "sudo")


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxCheckTunCapabilitiesTest(unittest.TestCase):

    def test_check_tun_capabilities_true_when_cap_present(self):
        fake_proc = mock.Mock(returncode=0, stdout="/path/to/sing-box cap_net_admin,cap_net_bind_service=ep\n")
        bin_path = "/path/to/sing-box"
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=fake_proc) as run_mock:
            res = platform_utils.check_tun_capabilities(bin_path)
            self.assertTrue(res)
            run_mock.assert_called_once_with(
                ["/usr/sbin/getcap", str(Path(bin_path))],
                capture_output=True,
                text=True,
                timeout=5,
            )

    def test_check_tun_capabilities_false_when_cap_missing(self):
        fake_proc = mock.Mock(returncode=0, stdout="/path/to/sing-box cap_net_bind_service=ep\n")
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=fake_proc):
            res = platform_utils.check_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_check_tun_capabilities_false_when_getcap_fails(self):
        fake_proc = mock.Mock(returncode=1, stdout="", stderr="error")
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=fake_proc):
            res = platform_utils.check_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_check_tun_capabilities_false_when_getcap_missing(self):
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value=None):
            res = platform_utils.check_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_check_tun_capabilities_false_when_file_not_found(self):
        with mock.patch("pathlib.Path.is_file", return_value=False):
            res = platform_utils.check_tun_capabilities("/nonexistent/sing-box")
            self.assertFalse(res)

    def test_check_tun_capabilities_false_when_none(self):
        res = platform_utils.check_tun_capabilities(None)
        self.assertFalse(res)

    def test_check_tun_capabilities_handles_subprocess_error(self):
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", side_effect=OSError("getcap exec failed")):
            res = platform_utils.check_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxGrantTunCapabilitiesTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_grant_tun_capabilities_success_with_verification(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        bin_path = "/path/to/sing-box"
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities",
                        side_effect=[False, True]) as check_mock, \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            res = platform_utils.grant_tun_capabilities(bin_path)
            self.assertTrue(res)
            run_mock.assert_called_once_with(
                ["/usr/bin/pkexec", "/bin/sh", "-c",
                 "setcap cap_net_admin,cap_net_bind_service+eip /path/to/sing-box"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(check_mock.call_count, 2)

    def test_grant_tun_capabilities_fast_path_skips_pkexec(self):
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("subprocess.run") as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertTrue(res)
            run_mock.assert_not_called()

    def test_grant_tun_capabilities_installs_polkit_rule_for_resolved(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=True), \
             mock.patch("utils.platform_utils._tun_rule_source",
                        return_value=Path("/fake/data/polkit/20-socksicle-tun.rules")), \
             mock.patch("utils.platform_utils._tun_rule_in_sync",
                        side_effect=[False, True]):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertTrue(res)
            script = run_mock.call_args[0][0][3]
            self.assertIn("install -m 0644 /fake/data/polkit/20-socksicle-tun.rules",
                          script)
            self.assertIn("/etc/polkit-1/rules.d/20-socksicle-tun.rules", script)

    def test_grant_tun_capabilities_fails_when_rule_not_installed(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run), \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=True), \
             mock.patch("utils.platform_utils._tun_rule_source",
                        return_value=Path("/fake/data/polkit/20-socksicle-tun.rules")), \
             mock.patch("utils.platform_utils._tun_rule_in_sync",
                        side_effect=[False, False]):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_marks_rule_install(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        src = self.root / "polkit" / "20-socksicle-tun.rules"
        src.parent.mkdir(parents=True)
        src.write_text("polkit.addRule(function(action) {});", encoding="utf-8")
        marker_path = self.root / "marker" / "tun_polkit_rule.marker"
        with mock.patch("pathlib.Path.is_file", side_effect=lambda *a: True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run), \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=True), \
             mock.patch("utils.platform_utils._tun_rule_source", return_value=src), \
             mock.patch("utils.platform_utils._rule_marker_path", return_value=marker_path):
            res = platform_utils.grant_tun_capabilities(str(self.root / "bin" / "sing-box"))
            self.assertTrue(res)
            self.assertEqual(marker_path.read_text(encoding="utf-8"),
                             hashlib.sha256(src.read_bytes()).hexdigest())

    def test_grant_tun_capabilities_without_bundled_rule_is_silent(self):
        """Packaged setups shipping the rule via the distro have nothing to install."""
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("subprocess.run") as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=True), \
             mock.patch("utils.platform_utils._tun_rule_source", return_value=None):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertTrue(res)
            run_mock.assert_not_called()

    def test_grant_tun_capabilities_success_without_getcap(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        def _which(x):
            return f"/usr/bin/{x}" if x in ("pkexec", "setcap") else None
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.run", return_value=fake_run), \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=False), \
             mock.patch("utils.platform_utils._find_getcap", return_value=None), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertTrue(res)

    def test_grant_tun_capabilities_failure_on_user_cancellation(self):
        fake_run = mock.Mock(returncode=126, stdout="", stderr="Authorization dismissed")
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_failure_when_pkexec_missing(self):
        def _which(x):
            return "/usr/bin/setcap" if x == "setcap" else None
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=_which):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_failure_when_binary_missing(self):
        with mock.patch("pathlib.Path.is_file", return_value=False):
            res = platform_utils.grant_tun_capabilities("/nonexistent/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_handles_timeout(self):
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pkexec", 60)), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxTunRuleMarkerTest(unittest.TestCase):
    """Hardened distros make /etc/polkit-1/rules.d unreadable for users;
    the install marker written after a confirmed pkexec run is authoritative
    then."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.src = self.root / "data" / "polkit" / "20-socksicle-tun.rules"
        self.src.parent.mkdir(parents=True)
        self.src.write_text("polkit.addRule(function(action) {});", encoding="utf-8")
        self.marker = self.root / "cfg" / "tun_polkit_rule.marker"

    def _with_rule_env(self, rule_content=None):
        """Patch dest/source/marker paths and prepare the marker parent dir."""
        hidden_dir = self.root / "rules.d"
        hidden_dir.mkdir()
        os.chmod(hidden_dir, 0o000)
        if rule_content is not None:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
        return (
            mock.patch.object(platform_utils, "_TUN_RULE_DEST", str(hidden_dir / "x.rules")),
            mock.patch.object(platform_utils, "_tun_rule_source", return_value=self.src),
            mock.patch.object(platform_utils, "_rule_marker_path", return_value=self.marker),
        )

    def test_rule_in_sync_falls_back_to_marker(self):
        patch_dest, patch_src, patch_marker = self._with_rule_env()
        with patch_dest, patch_src, patch_marker:
            platform_utils._write_rule_marker(self.src)
            self.assertTrue(platform_utils._tun_rule_in_sync())

    def test_rule_in_sync_false_when_marker_mismatch(self):
        patch_dest, patch_src, patch_marker = self._with_rule_env()
        with patch_dest, patch_src, patch_marker:
            platform_utils._write_rule_marker(self.src)
            self.marker.write_text("stale", encoding="utf-8")
            self.assertFalse(platform_utils._tun_rule_in_sync())

    def test_rule_in_sync_false_without_marker(self):
        patch_dest, patch_src, patch_marker = self._with_rule_env()
        with patch_dest, patch_src, patch_marker:
            self.assertFalse(platform_utils._tun_rule_in_sync())


@mock.patch("utils.platform_utils.sys.platform", "linux")
class LinuxSystemdResolvedTest(unittest.TestCase):

    def test_systemd_resolved_active(self):
        fake_run = mock.Mock(returncode=0)
        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock:
            self.assertTrue(platform_utils._systemd_resolved_active())
            run_mock.assert_called_once_with(
                ["/usr/bin/systemctl", "is-active", "--quiet", "systemd-resolved"],
                capture_output=True,
                timeout=5,
            )

    def test_systemd_resolved_inactive(self):
        fake_run = mock.Mock(returncode=3)
        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"), \
             mock.patch("subprocess.run", return_value=fake_run):
            self.assertFalse(platform_utils._systemd_resolved_active())

    def test_systemd_resolved_missing_systemctl(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(platform_utils._systemd_resolved_active())


@mock.patch("utils.platform_utils.sys.platform", "win32")
class NonLinuxCapabilitiesTest(unittest.TestCase):

    def test_check_tun_capabilities_false_on_windows(self):
        self.assertFalse(platform_utils.check_tun_capabilities("C:\\bin\\sing-box.exe"))

    def test_grant_tun_capabilities_false_on_windows(self):
        self.assertFalse(platform_utils.grant_tun_capabilities("C:\\bin\\sing-box.exe"))