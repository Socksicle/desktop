"""Unit tests for utils.startup_utils's platform-specific pieces.

Complements the flow-level coverage in test_entry_points.py: native message
parsing of the tray/power filter, Windows-only environment setup, high-DPI
rounding policy application, native handler wiring, and the unhandled-
exception hook behavior.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils import startup_utils  # noqa: E402
from utils.startup_utils import TrayAndPowerFilter  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

_HIGH_DPI_KEYS = ("QT_ENABLE_HIGHDPI_SCALING", "QT_AUTO_SCREEN_SCALE_FACTOR",
                  "QT_SCALE_FACTOR_ROUNDING_POLICY")


class ImportTimeEnvironmentTest(unittest.TestCase):
    """The Windows high-DPI environment is applied at import time only."""

    def test_high_dpi_env_applied_before_qt_initialises(self):
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import os, utils.startup_utils; "
                "print({k: os.environ.get(k) for k in ("
                "'QT_ENABLE_HIGHDPI_SCALING', 'QT_AUTO_SCREEN_SCALE_FACTOR', "
                "'QT_SCALE_FACTOR_ROUNDING_POLICY')})")
        out = subprocess.check_output(
            [sys.executable, "-c", code],
            cwd=pkg,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        values = eval(out.strip())
        if sys.platform == "win32":
            self.assertEqual(values["QT_ENABLE_HIGHDPI_SCALING"], "1")
            self.assertEqual(values["QT_AUTO_SCREEN_SCALE_FACTOR"], "1")
            self.assertEqual(values["QT_SCALE_FACTOR_ROUNDING_POLICY"],
                             "PassThrough")
        else:
            for key in _HIGH_DPI_KEYS:
                self.assertIsNone(values[key])


class TrayAndPowerFilterTest(unittest.TestCase):
    """Native message parsing: taskbar-create and power-resume events."""

    def setUp(self):
        self.taskbar = mock.Mock()
        self.resume = mock.Mock()
        self.filter_ = TrayAndPowerFilter(
            on_taskbar_created=self.taskbar, on_resume=self.resume)

    def _make_msg(self, msg=0, wparam=0):
        """Create a fake Windows MSG struct as an int-addressable object."""
        import ctypes
        import struct
        buf = ctypes.create_string_buffer(32)
        struct.pack_into("<QI4xQq", buf, 0, 0, msg, wparam, 0)

        class _MsgAddr(int):
            _kept = []
            def __new__(cls, address, _keep=buf):
                cls._kept.append(_keep)
                return super().__new__(cls, address)

        return _MsgAddr(ctypes.addressof(buf))

    def test_taskbar_created_event_reregisters_tray(self):
        buf = self._make_msg(msg=startup_utils.WM_TASKBARCREATED)
        self.filter_.nativeEventFilter(b"windows_generic_MSG", buf)
        self.taskbar.assert_called_once_with()
        self.resume.assert_not_called()

    def test_power_resume_event_reconnects(self):
        buf = self._make_msg(
            msg=startup_utils.WM_POWERBROADCAST,
            wparam=startup_utils.PBT_APMRESUMESUSPEND)
        self.filter_.nativeEventFilter(b"windows_generic_MSG", buf)
        self.resume.assert_called_once_with()
        self.taskbar.assert_not_called()

    def test_power_event_with_other_wparam_is_ignored(self):
        buf = self._make_msg(msg=startup_utils.WM_POWERBROADCAST, wparam=0)
        self.filter_.nativeEventFilter(b"windows_generic_MSG", buf)
        self.resume.assert_not_called()
        self.taskbar.assert_not_called()

    def test_non_windows_event_type_is_ignored(self):
        buf = self._make_msg(msg=startup_utils.WM_TASKBARCREATED)
        self.filter_.nativeEventFilter(b"generic", buf)
        self.taskbar.assert_not_called()
        self.resume.assert_not_called()

    def test_malformed_message_does_not_crash(self):
        for message in (b"\x00" * 4, 12345):
            result = self.filter_.nativeEventFilter(
                b"windows_generic_MSG", message)
            self.assertEqual(result, (False, 0))
        self.taskbar.assert_not_called()
        self.resume.assert_not_called()

    def test_returns_false_to_forward_event_to_qt(self):
        buf = self._make_msg(msg=startup_utils.WM_TASKBARCREATED)
        result = self.filter_.nativeEventFilter(
            b"windows_generic_MSG", buf)
        self.assertEqual(result, (False, 0))

    def test_keyboard_interrupt_calls_interrupt_handler_cleanly(self):
        interrupt_mock = mock.Mock()
        taskbar_mock = mock.Mock(side_effect=KeyboardInterrupt)
        filter_ = TrayAndPowerFilter(
            on_taskbar_created=taskbar_mock,
            on_resume=self.resume,
            on_interrupt=interrupt_mock,
        )
        buf = self._make_msg(msg=startup_utils.WM_TASKBARCREATED)
        result = filter_.nativeEventFilter(b"windows_generic_MSG", buf)
        self.assertEqual(result, (True, 0))
        interrupt_mock.assert_called_once_with()


class ApplyHighDpiPolicyTest(unittest.TestCase):

    def test_windows_applies_passthrough_rounding_policy(self):
        app = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=True):
            result = startup_utils.apply_high_dpi_policy(app)
        self.assertIsNone(result)
        app.setHighDpiScaleFactorRoundingPolicy.assert_called_once_with(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    def test_linux_skips_rounding_policy(self):
        app = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=False):
            startup_utils.apply_high_dpi_policy(app)
        app.setHighDpiScaleFactorRoundingPolicy.assert_not_called()


class InstallNativeHandlersTest(unittest.TestCase):
    """Tray/power native handlers on Windows, D-Bus handlers on Linux."""

    def test_windows_installs_tray_and_power_filter(self):
        app = mock.Mock()
        window = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=True), \
             mock.patch.object(startup_utils, "is_linux",
                               return_value=False):
            filter_ = startup_utils.install_native_handlers(app, window)
        self.assertIsInstance(filter_, TrayAndPowerFilter)
        app.installNativeEventFilter.assert_called_once_with(filter_)
        self.assertIs(app.native_filter, filter_)
        filter_.on_taskbar_created()
        window.tray_icon.show.assert_called_once_with()
        filter_.on_resume()
        window.reconnect_after_resume.assert_called_once_with()

    def test_linux_installs_dbus_power_network_filter(self):
        app = mock.Mock()
        window = mock.Mock()
        fake_dbus = mock.Mock()
        fake_dbus.isConnected.return_value = True
        fake_dbus.connect.return_value = True

        with mock.patch.object(startup_utils, "is_windows",
                               return_value=False), \
             mock.patch.object(startup_utils, "is_linux",
                               return_value=True), \
             mock.patch("PySide6.QtDBus.QDBusConnection.systemBus",
                        return_value=fake_dbus, create=True):
            filter_ = startup_utils.install_native_handlers(app, window)

        self.assertIsInstance(filter_, startup_utils.LinuxDBusPowerNetworkFilter)
        self.assertIs(app.native_filter, filter_)
        # Test signal invocation through filter
        filter_.handle_prepare_for_sleep(False)
        window.reconnect_after_resume.assert_called_once_with()

    def test_linux_handles_dbus_init_exception_gracefully(self):
        app = mock.Mock()
        window = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=False), \
             mock.patch.object(startup_utils, "is_linux",
                               return_value=True), \
             mock.patch.object(startup_utils,
                               "LinuxDBusPowerNetworkFilter",
                               side_effect=RuntimeError("D-Bus init failed")):
            filter_ = startup_utils.install_native_handlers(app, window)
        self.assertIsNone(filter_)

    def test_other_platform_skips_native_handlers(self):
        app = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=False), \
             mock.patch.object(startup_utils, "is_linux",
                               return_value=False):
            filter_ = startup_utils.install_native_handlers(
                app, mock.Mock())
        self.assertIsNone(filter_)
        app.installNativeEventFilter.assert_not_called()
        self.assertNotIn("native_filter", app._mock_children)


class LinuxDBusFilterTest(unittest.TestCase):
    """Linux D-Bus signal subscription, sleep/wake, and network change handling."""

    def setUp(self):
        self.resume = mock.Mock()
        self.net_change = mock.Mock()
        self.fake_bus = mock.Mock()
        self.fake_bus.isConnected.return_value = True
        self.fake_bus.connect.return_value = True

    def test_successful_dbus_registration(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            on_network_change=self.net_change,
            bus=self.fake_bus,
        )
        self.assertTrue(filter_.is_connected)
        self.assertTrue(filter_._login1_connected)
        self.assertTrue(filter_._nm_connected)
        self.assertEqual(self.fake_bus.connect.call_count, 2)

    def test_prepare_for_sleep_true_sets_sleeping_flag(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            on_network_change=self.net_change,
            bus=self.fake_bus,
        )
        filter_.handle_prepare_for_sleep(True)
        self.assertTrue(filter_.is_sleeping)
        self.resume.assert_not_called()

    def test_prepare_for_sleep_false_wakes_and_calls_resume(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            on_network_change=self.net_change,
            bus=self.fake_bus,
        )
        filter_.handle_prepare_for_sleep(True)
        filter_.handle_prepare_for_sleep(False)
        self.assertFalse(filter_.is_sleeping)
        self.resume.assert_called_once_with()

    def test_prepare_for_sleep_handles_callback_exception_gracefully(self):
        self.resume.side_effect = RuntimeError("resume callback error")
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            bus=self.fake_bus,
        )
        # Should not raise exception
        filter_.handle_prepare_for_sleep(False)
        self.resume.assert_called_once_with()

    def test_nm_state_changed_connected_global_triggers_reconnect(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            on_network_change=self.net_change,
            bus=self.fake_bus,
        )
        filter_.handle_nm_state_changed(startup_utils.NM_STATE_CONNECTED_GLOBAL)
        self.net_change.assert_called_once_with()
        self.assertEqual(filter_._last_nm_state, startup_utils.NM_STATE_CONNECTED_GLOBAL)

    def test_nm_state_changed_other_states_do_not_trigger_reconnect(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            on_network_change=self.net_change,
            bus=self.fake_bus,
        )
        for state in (startup_utils.NM_STATE_DISCONNECTED,
                      startup_utils.NM_STATE_CONNECTING,
                      startup_utils.NM_STATE_ASLEEP,
                      startup_utils.NM_STATE_CONNECTED_LOCAL):
            filter_.handle_nm_state_changed(state)
        self.net_change.assert_not_called()
        self.assertEqual(filter_._last_nm_state, startup_utils.NM_STATE_CONNECTED_LOCAL)

    def test_nm_state_changed_handles_invalid_and_callback_errors(self):
        self.net_change.side_effect = RuntimeError("network callback error")
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            on_network_change=self.net_change,
            bus=self.fake_bus,
        )
        filter_.handle_nm_state_changed("invalid_state")
        self.net_change.assert_not_called()
        # Should not raise exception when callback fails
        filter_.handle_nm_state_changed(startup_utils.NM_STATE_CONNECTED_GLOBAL)
        self.net_change.assert_called_once_with()

    def test_dbus_not_connected_fallback(self):
        self.fake_bus.isConnected.return_value = False
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            bus=self.fake_bus,
        )
        self.assertFalse(filter_.is_connected)
        self.fake_bus.connect.assert_not_called()

    def test_qtdbus_import_error_fallback(self):
        with mock.patch("builtins.__import__", side_effect=ImportError("No QtDBus")):
            filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
                on_resume=self.resume,
                bus=None,
            )
        self.assertFalse(filter_.is_connected)

    def test_disconnect_signals(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume,
            bus=self.fake_bus,
        )
        self.assertTrue(filter_.is_connected)
        filter_.disconnect_signals()
        self.assertFalse(filter_.is_connected)
        self.assertEqual(self.fake_bus.disconnect.call_count, 2)


class ExcepthookTest(unittest.TestCase):
    """The unhandled-exception hook runs on every platform and logs safely."""

    def setUp(self):
        self._original_hook = sys.excepthook
        self.addCleanup(self._restore_hook)

    def _restore_hook(self):
        sys.excepthook = self._original_hook

    def test_windows_installs_hook_that_logs_and_prints(self):
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=True):
            startup_utils.install_excepthook()
        self.assertIsNot(sys.excepthook, self._original_hook)

        logger = mock.Mock()
        traceback_print = mock.Mock()
        with mock.patch.object(startup_utils.logging, "getLogger",
                               return_value=logger), \
             mock.patch.object(startup_utils.traceback,
                               "print_exception",
                               side_effect=traceback_print):
            sys.excepthook(ValueError, ValueError("boom"), None)
        logger.critical.assert_called_once()
        self.assertIn("exc_info", logger.critical.call_args.kwargs)
        traceback_print.assert_called_once()

    def test_linux_installs_hook_that_logs_and_prints(self):
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=False):
            startup_utils.install_excepthook()
        self.assertIsNot(sys.excepthook, self._original_hook)

        logger = mock.Mock()
        traceback_print = mock.Mock()
        with mock.patch.object(startup_utils.logging, "getLogger",
                               return_value=logger), \
             mock.patch.object(startup_utils.traceback,
                               "print_exception",
                               side_effect=traceback_print):
            sys.excepthook(ValueError, ValueError("boom"), None)
        logger.critical.assert_called_once()
        self.assertIn("exc_info", logger.critical.call_args.kwargs)
        traceback_print.assert_called_once()

    def test_hook_tolerates_stderr_failure(self):
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=True):
            startup_utils.install_excepthook()
        with mock.patch.object(startup_utils.logging, "getLogger"), \
             mock.patch.object(startup_utils.traceback,
                               "print_exception",
                               side_effect=RuntimeError("stderr gone")):
            sys.excepthook(ValueError, ValueError("boom"), None)  # no raise

    def test_excepthook_handles_keyboard_interrupt_gracefully(self):
        window = mock.Mock()
        startup_utils.install_excepthook(window)
        logger = mock.Mock()
        traceback_print = mock.Mock()
        with mock.patch.object(startup_utils.logging, "getLogger",
                               return_value=logger), \
             mock.patch.object(startup_utils.traceback,
                               "print_exception",
                               side_effect=traceback_print):
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        logger.critical.assert_not_called()
        traceback_print.assert_not_called()
        window.quit_app.assert_called_once_with()

    def test_setup_sigint_handler_quits_on_sigint(self):
        import signal
        app = mock.Mock()
        window = mock.Mock()
        with mock.patch("signal.signal") as mock_signal:
            startup_utils.setup_sigint_handler(app, window)
            mock_signal.assert_called_once()
            self.assertEqual(mock_signal.call_args[0][0], signal.SIGINT)
            handler = mock_signal.call_args[0][1]
            handler(signal.SIGINT, None)
            window.quit_app.assert_called_once_with()

    def test_console_ctrl_handler_ref_is_kept(self):
        """The WINFUNCTYPE wrapper must be anchored on the app, or Windows
        may GC it and the console handler dies with it."""
        import signal
        from types import SimpleNamespace

        class _App:
            pass

        app = _App()
        window = mock.Mock()
        calls = []

        def _winfunctype(*types):
            def _deco(fn):
                return fn
            return _deco

        fake_ctypes = SimpleNamespace(
            windll=SimpleNamespace(
                kernel32=SimpleNamespace(
                    SetConsoleCtrlHandler=lambda fn, add: calls.append((fn, add)))),
            WINFUNCTYPE=_winfunctype,
        )
        with mock.patch("signal.signal"), \
                mock.patch.object(startup_utils, "ctypes", fake_ctypes):
            startup_utils.setup_sigint_handler(app, window)

        self.assertEqual(len(calls), 1)
        handler, add = calls[0]
        self.assertTrue(add)
        self.assertIs(app._console_ctrl_handler, handler)


class DesktopFileNameTest(unittest.TestCase):

    def test_windows_uses_app_id_name(self):
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=True):
            self.assertEqual(startup_utils.desktop_file_name(),
                             "Socksicle")

    def test_linux_uses_desktop_entry_name(self):
        with mock.patch.object(startup_utils, "is_windows",
                               return_value=False):
            self.assertEqual(startup_utils.desktop_file_name(),
                             "Socksicle.desktop")


class XDGAutostartLinuxTest(unittest.TestCase):
    """Unit tests for Linux XDG Autostart (.desktop file management)."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.config_dir = Path(self.tmp_dir.name)
        self.desktop_file = self.config_dir / "autostart" / "socksicle.desktop"

    def test_get_autostart_desktop_path_default(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(startup_utils.Path, "home",
                               return_value=startup_utils.Path("/home/testuser")):
            path = startup_utils.get_autostart_desktop_path()
            self.assertEqual(
                str(path).replace("\\", "/"),
                "/home/testuser/.config/autostart/socksicle.desktop"
            )

    def test_get_autostart_desktop_path_custom_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/config"}):
            path = startup_utils.get_autostart_desktop_path()
            self.assertEqual(
                str(path).replace("\\", "/"),
                "/custom/config/autostart/socksicle.desktop"
            )

    def test_set_autostart_linux_enable(self):
        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path",
                               return_value=self.desktop_file):
            ok = startup_utils.set_autostart(True)
            self.assertTrue(ok)
            self.assertTrue(self.desktop_file.is_file())
            content = self.desktop_file.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", content)
            self.assertIn("Type=Application", content)
            self.assertIn("Name=Socksicle", content)
            self.assertIn("Icon=socksicle", content)
            self.assertIn("--minimized", content)
            self.assertIn("X-GNOME-Autostart-enabled=true", content)

    def test_set_autostart_linux_custom_app_path(self):
        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path",
                               return_value=self.desktop_file):
            ok = startup_utils.set_autostart(True, app_path="/usr/bin/socksicle")
            self.assertTrue(ok)
            content = self.desktop_file.read_text(encoding="utf-8")
            self.assertIn("Exec=/usr/bin/socksicle --minimized", content)

    def test_is_autostart_enabled_linux_true(self):
        self.desktop_file.parent.mkdir(parents=True, exist_ok=True)
        self.desktop_file.write_text(
            "[Desktop Entry]\nType=Application\nName=Socksicle\nExec=socksicle --minimized\n",
            encoding="utf-8"
        )
        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path",
                               return_value=self.desktop_file):
            self.assertTrue(startup_utils.is_autostart_enabled())

    def test_is_autostart_enabled_linux_false_when_missing(self):
        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path",
                               return_value=self.desktop_file):
            self.assertFalse(startup_utils.is_autostart_enabled())

    def test_is_autostart_enabled_linux_false_when_hidden(self):
        self.desktop_file.parent.mkdir(parents=True, exist_ok=True)
        self.desktop_file.write_text(
            "[Desktop Entry]\nType=Application\nName=Socksicle\nHidden=true\n",
            encoding="utf-8"
        )
        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path",
                               return_value=self.desktop_file):
            self.assertFalse(startup_utils.is_autostart_enabled())

    def test_set_autostart_linux_disable(self):
        self.desktop_file.parent.mkdir(parents=True, exist_ok=True)
        self.desktop_file.write_text("[Desktop Entry]\nName=Socksicle\n", encoding="utf-8")
        self.assertTrue(self.desktop_file.exists())

        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path",
                               return_value=self.desktop_file):
            ok = startup_utils.set_autostart(False)
            self.assertTrue(ok)
            self.assertFalse(self.desktop_file.exists())


class WindowsAutostartTest(unittest.TestCase):
    """Unit tests for Windows registry autostart management."""

    def test_is_autostart_enabled_windows_true(self):
        mock_winreg = mock.MagicMock()
        mock_winreg.QueryValueEx.return_value = ("C:\\Socksicle.exe --minimized", 1)
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.dict("sys.modules", {"winreg": mock_winreg}):
            self.assertTrue(startup_utils.is_autostart_enabled())

    def test_is_autostart_enabled_windows_false(self):
        mock_winreg = mock.MagicMock()
        mock_winreg.OpenKey.side_effect = OSError("Key not found")
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.dict("sys.modules", {"winreg": mock_winreg}):
            self.assertFalse(startup_utils.is_autostart_enabled())

    def test_set_autostart_windows_enable(self):
        mock_winreg = mock.MagicMock()
        mock_key = mock.MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.dict("sys.modules", {"winreg": mock_winreg}):
            ok = startup_utils.set_autostart(True, app_path="C:\\app.exe")
            self.assertTrue(ok)
            mock_winreg.SetValueEx.assert_called_once()
            args = mock_winreg.SetValueEx.call_args[0]
            self.assertEqual(args[1], "Socksicle")
            self.assertIn("--minimized", args[4])

    def test_set_autostart_windows_disable(self):
        mock_winreg = mock.MagicMock()
        mock_key = mock.MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.dict("sys.modules", {"winreg": mock_winreg}):
            ok = startup_utils.set_autostart(False)
            self.assertTrue(ok)
            mock_winreg.DeleteValue.assert_called_once_with(mock_key, "Socksicle")


if __name__ == "__main__":
    unittest.main()