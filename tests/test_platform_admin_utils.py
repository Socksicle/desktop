"""Focused tests for utils.platform_utils is_admin / elevate_restart platform guards.

All platform detection and subprocess activity is mocked; nothing is executed.
"""
import os
import subprocess
import sys
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

    def test_grant_tun_capabilities_success_with_verification(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        bin_path = "/path/to/sing-box"
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True):
            res = platform_utils.grant_tun_capabilities(bin_path)
            self.assertTrue(res)
            run_mock.assert_called_once_with(
                ["/usr/bin/pkexec", "/usr/bin/setcap", "cap_net_admin,cap_net_bind_service+ep", str(Path(bin_path))],
                capture_output=True,
                text=True,
                timeout=60,
            )

    def test_grant_tun_capabilities_success_without_getcap(self):
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")
        def _which(x):
            return f"/usr/bin/{x}" if x in ("pkexec", "setcap") else None
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=_which), \
             mock.patch("subprocess.run", return_value=fake_run), \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=False), \
             mock.patch("utils.platform_utils._find_getcap", return_value=None):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertTrue(res)

    def test_grant_tun_capabilities_failure_on_user_cancellation(self):
        fake_run = mock.Mock(returncode=126, stdout="", stderr="Authorization dismissed")
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_failure_when_pkexec_missing(self):
        def _which(x):
            return "/usr/bin/setcap" if x == "setcap" else None
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=_which), \
             mock.patch("pathlib.Path.is_file", side_effect=lambda: False):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_failure_when_binary_missing(self):
        with mock.patch("pathlib.Path.is_file", return_value=False):
            res = platform_utils.grant_tun_capabilities("/nonexistent/sing-box")
            self.assertFalse(res)

    def test_grant_tun_capabilities_handles_timeout(self):
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pkexec", 60)):
            res = platform_utils.grant_tun_capabilities("/path/to/sing-box")
            self.assertFalse(res)


@mock.patch("utils.platform_utils.sys.platform", "win32")
class NonLinuxCapabilitiesTest(unittest.TestCase):

    def test_check_tun_capabilities_false_on_windows(self):
        self.assertFalse(platform_utils.check_tun_capabilities("C:\\bin\\sing-box.exe"))

    def test_grant_tun_capabilities_false_on_windows(self):
        self.assertFalse(platform_utils.grant_tun_capabilities("C:\\bin\\sing-box.exe"))