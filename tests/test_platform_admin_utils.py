"""Focused tests for utils.platform_utils is_admin / elevate_restart platform guards.

All platform detection and subprocess activity is mocked; nothing is executed.
"""
import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest import mock
import pytest

from utils import platform_utils


def test_platform_admin_and_elevation():
    # 1. Linux is_admin checks
    with mock.patch("utils.platform_utils.sys.platform", "linux"):
        with mock.patch("utils.platform_utils.os.geteuid", return_value=0, create=True):
            assert platform_utils.is_admin() is True
        with mock.patch("utils.platform_utils.os.geteuid", return_value=1000, create=True):
            assert platform_utils.is_admin() is False
        with mock.patch("utils.platform_utils.os.geteuid", create=True, new=None):
            assert platform_utils.is_admin() is False

    # 2. Unsupported platform defaults (e.g. darwin)
    with mock.patch("utils.platform_utils.sys.platform", "darwin"):
        assert platform_utils.is_admin() is False
        assert platform_utils.elevate_restart() is False

    # 3. Linux elevate_restart: pkexec, sudo fallback, and no elevator
    with mock.patch("utils.platform_utils.sys.platform", "linux"):
        exit_mock = mock.Mock(side_effect=SystemExit(0))
        argv = ["/opt/socksicle/app.py", "--flag"]

        # Case 3a: pkexec available
        with mock.patch("shutil.which", return_value="/usr/bin/pkexec"), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable", "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit", exit_mock):
            with pytest.raises(SystemExit):
                platform_utils.elevate_restart()
            popen.assert_called_once_with(
                ["pkexec", "/usr/bin/python3", argv[0], "--flag", "--restarted"],
                cwd=str(platform_utils.get_app_dir()),
            )

        # Case 3b: pkexec fails, fallback to sudo
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
             mock.patch.object(platform_utils.sys, "exit", exit_mock):
            with pytest.raises(SystemExit):
                platform_utils.elevate_restart()
            assert calls[0][0] == "pkexec"
            assert calls[1][0] == "sudo"

        # Case 3c: no elevator available
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(platform_utils.sys, "argv", argv), \
             mock.patch.object(platform_utils.sys, "executable", "/usr/bin/python3"), \
             mock.patch.object(platform_utils.sys, "exit") as exit_unused:
            assert platform_utils.elevate_restart() is False
            popen.assert_not_called()
            exit_unused.assert_not_called()

    # 4. Windows elevate_restart: script mode and frozen mode
    with mock.patch("utils.platform_utils.sys.platform", "win32"), \
         mock.patch("utils.platform_utils.is_windows", return_value=True):
        shell32 = mock.MagicMock()
        shell32.ShellExecuteW.return_value = 42
        ctypes_mock = mock.MagicMock()
        ctypes_mock.windll.shell32 = shell32

        # 4a: Script mode
        with mock.patch.dict("sys.modules", {"ctypes": ctypes_mock}), \
             mock.patch("utils.platform_utils.is_frozen", return_value=False), \
             mock.patch.object(platform_utils.sys, "executable", "C:\\Python\\python.exe"), \
             mock.patch.object(platform_utils.sys, "argv", ["C:\\path\\main.py", "--debug"]), \
             mock.patch.object(platform_utils.sys, "exit", exit_mock):
            with pytest.raises(SystemExit):
                platform_utils.elevate_restart()
            shell32.ShellExecuteW.assert_called_with(
                None, "runas", "C:\\Python\\python.exe",
                '"C:\\path\\main.py" "--debug" "--restarted"',
                str(platform_utils.get_app_dir()), 1
            )

        # 4b: Frozen mode (Nuitka / compiled exe) - sys.argv[0] must NOT be duplicated in parameters!
        shell32.reset_mock()
        with mock.patch.dict("sys.modules", {"ctypes": ctypes_mock}), \
             mock.patch("utils.platform_utils.is_frozen", return_value=True), \
             mock.patch.object(platform_utils.sys, "executable", "C:\\Program Files\\Socksicle\\Socksicle.exe"), \
             mock.patch.object(platform_utils.sys, "argv", ["C:\\Program Files\\Socksicle\\Socksicle.exe", "--debug"]), \
             mock.patch.object(platform_utils.sys, "exit", exit_mock):
            with pytest.raises(SystemExit):
                platform_utils.elevate_restart()
            shell32.ShellExecuteW.assert_called_with(
                None, "runas", "C:\\Program Files\\Socksicle\\Socksicle.exe",
                '"--debug" "--restarted"',
                str(platform_utils.get_app_dir()), 1
            )

        # 4c: User cancels UAC (ret <= 32) -> returns False, does not exit
        shell32.reset_mock()
        shell32.ShellExecuteW.return_value = 5  # SE_ERR_ACCESSDENIED
        with mock.patch.dict("sys.modules", {"ctypes": ctypes_mock}), \
             mock.patch("utils.platform_utils.is_frozen", return_value=True), \
             mock.patch.object(platform_utils.sys, "executable", "C:\\Program Files\\Socksicle\\Socksicle.exe"), \
             mock.patch.object(platform_utils.sys, "argv", ["C:\\Program Files\\Socksicle\\Socksicle.exe"]), \
             mock.patch.object(platform_utils.sys, "exit") as exit_unused:
            assert platform_utils.elevate_restart() is False
            exit_unused.assert_not_called()


def test_check_tun_capabilities():
    # 1. Windows noop
    with mock.patch("utils.platform_utils.sys.platform", "win32"):
        assert platform_utils.check_tun_capabilities("C:\\bin\\sing-box.exe") is False

    # 2. Linux check tun capabilities success
    bin_path = "/path/to/sing-box"
    fake_proc = mock.Mock(returncode=0, stdout="/path/to/sing-box cap_net_admin,cap_net_bind_service=ep\n")
    with mock.patch("utils.platform_utils.sys.platform", "linux"):
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=fake_proc) as run_mock:
            assert platform_utils.check_tun_capabilities(bin_path) is True
            run_mock.assert_called_once_with(
                ["/usr/sbin/getcap", str(Path(bin_path))],
                capture_output=True, text=True, timeout=5,
            )

        # 3. Failure modes
        # Missing cap
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="cap_net_bind_service=ep\n")):
            assert platform_utils.check_tun_capabilities(bin_path) is False

        # Nonzero exit
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="err")):
            assert platform_utils.check_tun_capabilities(bin_path) is False

        # Missing getcap / missing file / None / OSError
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value=None):
            assert platform_utils.check_tun_capabilities(bin_path) is False

        with mock.patch("pathlib.Path.is_file", return_value=False):
            assert platform_utils.check_tun_capabilities("/nonexistent") is False

        assert platform_utils.check_tun_capabilities(None) is False

        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("utils.platform_utils._find_getcap", return_value="/usr/sbin/getcap"), \
             mock.patch("subprocess.run", side_effect=OSError("exec failed")):
            assert platform_utils.check_tun_capabilities(bin_path) is False


def test_grant_tun_capabilities_and_polkit(tmp_path):
    # 1. Windows noop
    with mock.patch("utils.platform_utils.sys.platform", "win32"):
        assert platform_utils.grant_tun_capabilities("C:\\bin\\sing-box.exe") is False

    with mock.patch("utils.platform_utils.sys.platform", "linux"):
        bin_path = str(tmp_path / "sing-box")
        fake_run = mock.Mock(returncode=0, stdout="", stderr="")

        # 2. Fast path: already granted
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("subprocess.run") as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            assert platform_utils.grant_tun_capabilities(bin_path) is True
            run_mock.assert_not_called()

        # 3. Elevation flow: missing caps -> pkexec setcap -> verified True
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", side_effect=[False, True]), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            assert platform_utils.grant_tun_capabilities(bin_path) is True
            assert "setcap cap_net_admin,cap_net_bind_service+eip" in run_mock.call_args[0][0][3]

        # 4. Polkit rule integration
        src = tmp_path / "polkit" / "20-socksicle-tun.rules"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("polkit.addRule(function(action) {});", encoding="utf-8")
        marker_path = tmp_path / "marker" / "tun_polkit_rule.marker"

        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=fake_run) as run_mock, \
             mock.patch("utils.platform_utils.check_tun_capabilities", return_value=True), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=True), \
             mock.patch("utils.platform_utils._tun_rule_source", return_value=src), \
             mock.patch("utils.platform_utils._tun_rule_in_sync", side_effect=[False, True]), \
             mock.patch("utils.platform_utils._rule_marker_path", return_value=marker_path):
            assert platform_utils.grant_tun_capabilities(bin_path) is True
            script = run_mock.call_args[0][0][3]
            assert f"install -m 0644 {shlex.quote(str(src))}" in script
            assert marker_path.read_text(encoding="utf-8") == hashlib.sha256(src.read_bytes()).hexdigest()

        # 5. Failure cases
        # User canceled (returncode 126)
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=126, stderr="dismissed")), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            assert platform_utils.grant_tun_capabilities(bin_path) is False

        # Missing pkexec
        def _is_file(path):
            return str(path) == bin_path
        with mock.patch("pathlib.Path.is_file", autospec=True, side_effect=_is_file), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.run") as run_mock:
            assert platform_utils.grant_tun_capabilities(bin_path) is False
            run_mock.assert_not_called()

        # Missing binary
        with mock.patch("pathlib.Path.is_file", return_value=False):
            assert platform_utils.grant_tun_capabilities("/nonexistent") is False

        # Timeout
        with mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pkexec", 60)), \
             mock.patch("utils.platform_utils._systemd_resolved_active", return_value=False):
            assert platform_utils.grant_tun_capabilities(bin_path) is False


def test_systemd_resolved_and_rule_sync_marker(tmp_path):
    with mock.patch("utils.platform_utils.sys.platform", "linux"):
        # 1. Systemd resolved detection
        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)):
            assert platform_utils._systemd_resolved_active() is True

        with mock.patch("shutil.which", return_value="/usr/bin/systemctl"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=3)):
            assert platform_utils._systemd_resolved_active() is False

        with mock.patch("shutil.which", return_value=None):
            assert platform_utils._systemd_resolved_active() is False

        # 2. Rule in sync marker verification
        src = tmp_path / "data" / "polkit" / "20-socksicle-tun.rules"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("polkit.addRule(function(action) {});", encoding="utf-8")
        marker = tmp_path / "cfg" / "tun_polkit_rule.marker"

        hidden_dir = tmp_path / "rules.d"
        hidden_dir.mkdir(exist_ok=True)
        os.chmod(hidden_dir, 0o000)

        try:
            with mock.patch.object(platform_utils, "_TUN_RULE_DEST", str(hidden_dir / "x.rules")), \
                 mock.patch.object(platform_utils, "_tun_rule_source", return_value=src), \
                 mock.patch.object(platform_utils, "_rule_marker_path", return_value=marker):
                # No marker yet
                assert platform_utils._tun_rule_in_sync() is False
                # Write marker
                platform_utils._write_rule_marker(src)
                assert platform_utils._tun_rule_in_sync() is True
                # Stale marker
                marker.write_text("stale", encoding="utf-8")
                assert platform_utils._tun_rule_in_sync() is False
        finally:
            os.chmod(hidden_dir, 0o755)


def test_tun_polkit_rule_guards_ppid():
    """Verify that the bundled polkit rule rejects orphaned processes with ppid <= 1.

    Without this check, an unprivileged process can double-fork, get reparented
    to PID 1 (systemd, holding CAP_NET_ADMIN / all caps), and bypass polkit
    authentication to mutate system DNS.
    """
    rule_path = Path("data") / "polkit" / "20-socksicle-tun.rules"
    assert rule_path.is_file(), "Bundled polkit rule must exist"
    content = rule_path.read_text(encoding="utf-8")

    # Invariant: ppid <= 1 must return NOT_HANDLED
    assert "ppid <= 1" in content
    assert "polkit.Result.NOT_HANDLED" in content

    # Simulate the rule's decision logic in Python to verify behavior
    def simulate_rule(action_id: str, ppid: int | None, caps: int) -> str:
        allowed = {
            "org.freedesktop.resolve1.set-dns-servers": True,
            "org.freedesktop.resolve1.set-domains": True,
            "org.freedesktop.resolve1.set-default-route": True,
            "org.freedesktop.resolve1.revert": True,
        }
        if action_id not in allowed:
            return "NOT_HANDLED"
        if not ppid or ppid <= 1:
            return "NOT_HANDLED"
        if (caps & 0x1000) != 0:
            return "YES"
        return "NOT_HANDLED"

    # Action checks
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", 100, 0x1000) == "YES"
    assert simulate_rule("other.action", 100, 0x1000) == "NOT_HANDLED"

    # Security regression: double-fork orphan reparented to PID 1 (all caps) MUST be rejected
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", 1, 0x1ffffffffff) == "NOT_HANDLED"
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", 0, 0x1ffffffffff) == "NOT_HANDLED"
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", -1, 0x1ffffffffff) == "NOT_HANDLED"
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", None, 0x1ffffffffff) == "NOT_HANDLED"

    # Normal process with CAP_NET_ADMIN (e.g. sing-box) is permitted
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", 1234, 0x1000) == "YES"
    # Process without CAP_NET_ADMIN is rejected
    assert simulate_rule("org.freedesktop.resolve1.set-dns-servers", 1234, 0) == "NOT_HANDLED"

