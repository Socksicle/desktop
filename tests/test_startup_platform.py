"""Unit tests for utils.startup_utils platform-specific pieces."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils import startup_utils
from utils.startup_utils import TrayAndPowerFilter
from PySide6.QtCore import QObject, Qt, Signal

_HIGH_DPI_KEYS = ("QT_ENABLE_HIGHDPI_SCALING", "QT_AUTO_SCREEN_SCALE_FACTOR",
                  "QT_SCALE_FACTOR_ROUNDING_POLICY")


class ImportTimeEnvironmentTest(unittest.TestCase):

    def test_high_dpi_env_keys(self):
        if sys.platform != "win32":
            for key in _HIGH_DPI_KEYS:
                self.assertIsNone(os.environ.get(key))


class TaskbarCreatedRegistrationTest(unittest.TestCase):

    def test_taskbar_created_registration(self):
        user32 = mock.Mock()
        user32.RegisterWindowMessageW.return_value = 0xC123
        fake_ctypes = SimpleNamespace(windll=SimpleNamespace(user32=user32))

        # Windows success
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.object(startup_utils, "ctypes", fake_ctypes):
            self.assertEqual(startup_utils._register_taskbar_created_message(), 0xC123)

        # Windows failure
        user32.RegisterWindowMessageW.side_effect = OSError("user32 unavailable")
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.object(startup_utils, "ctypes", fake_ctypes), \
             mock.patch.object(startup_utils.log, "warning"):
            self.assertEqual(startup_utils._register_taskbar_created_message(), 0)

        # Non-windows
        with mock.patch.object(startup_utils, "is_windows", return_value=False):
            self.assertEqual(startup_utils._register_taskbar_created_message(), 0)


class TrayAndPowerFilterTest(unittest.TestCase):

    def setUp(self):
        self.taskbar_msg = 0xC123
        self._register_patch = mock.patch.object(
            startup_utils, "_register_taskbar_created_message",
            return_value=self.taskbar_msg)
        self._register_patch.start()
        self.addCleanup(self._register_patch.stop)

        self.taskbar = mock.Mock()
        self.resume = mock.Mock()
        self.filter_ = TrayAndPowerFilter(
            on_taskbar_created=self.taskbar, on_resume=self.resume)

    def _make_msg(self, msg=0, wparam=0):
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

    def test_tray_and_power_events(self):
        # Taskbar created
        buf = self._make_msg(msg=self.taskbar_msg)
        res = self.filter_.nativeEventFilter(b"windows_generic_MSG", buf)
        self.assertEqual(res, (False, 0))
        self.taskbar.assert_called_once_with()
        self.resume.assert_not_called()

        # Power resume
        self.taskbar.reset_mock()
        buf_pwr = self._make_msg(
            msg=startup_utils.WM_POWERBROADCAST,
            wparam=startup_utils.PBT_APMRESUMESUSPEND)
        self.filter_.nativeEventFilter(b"windows_generic_MSG", buf_pwr)
        self.resume.assert_called_once_with()
        self.taskbar.assert_not_called()

        # Ignored other event / non-windows message type
        self.resume.reset_mock()
        buf_ign = self._make_msg(msg=startup_utils.WM_POWERBROADCAST, wparam=0)
        self.filter_.nativeEventFilter(b"windows_generic_MSG", buf_ign)
        self.filter_.nativeEventFilter(b"generic", buf)
        self.resume.assert_not_called()
        self.taskbar.assert_not_called()

    def test_tray_and_power_filter_robustness(self):
        # Malformed buffers return False cleanly
        for message in (b"\x00" * 4, 12345):
            self.assertEqual(self.filter_.nativeEventFilter(b"windows_generic_MSG", message), (False, 0))

        # KeyboardInterrupt
        interrupt_mock = mock.Mock()
        taskbar_mock = mock.Mock(side_effect=KeyboardInterrupt)
        filter_ = TrayAndPowerFilter(
            on_taskbar_created=taskbar_mock, on_resume=self.resume, on_interrupt=interrupt_mock)
        buf = self._make_msg(msg=self.taskbar_msg)
        self.assertEqual(filter_.nativeEventFilter(b"windows_generic_MSG", buf), (True, 0))
        interrupt_mock.assert_called_once_with()


class ApplyHighDpiPolicyTest(unittest.TestCase):

    def test_high_dpi_policy_platform_handling(self):
        app = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows", return_value=True):
            startup_utils.apply_high_dpi_policy(app)
            app.setHighDpiScaleFactorRoundingPolicy.assert_called_once_with(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

        app_linux = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows", return_value=False):
            startup_utils.apply_high_dpi_policy(app_linux)
            app_linux.setHighDpiScaleFactorRoundingPolicy.assert_not_called()


class InstallNativeHandlersTest(unittest.TestCase):

    def test_windows_native_handlers(self):
        app = mock.Mock()
        window = mock.NonCallableMock(spec=["tray_manager", "reconnect_after_resume", "quit_app"])
        window.tray_manager = mock.Mock()
        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.object(startup_utils, "is_linux", return_value=False), \
             mock.patch.object(startup_utils, "_register_taskbar_created_message", return_value=0xC123):
            filter_ = startup_utils.install_native_handlers(app, window)
        self.assertIsInstance(filter_, TrayAndPowerFilter)
        app.installNativeEventFilter.assert_called_once_with(filter_)
        filter_.on_taskbar_created()
        window.tray_manager.restore.assert_called_once_with()
        filter_.on_resume()
        window.reconnect_after_resume.assert_called_once_with()

    def test_linux_and_fallback_native_handlers(self):
        app = mock.Mock()
        window = mock.Mock()
        fake_dbus = mock.Mock(isConnected=mock.Mock(return_value=True), connect=mock.Mock(return_value=True))

        # Linux installs DBus filter
        with mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch("PySide6.QtDBus.QDBusConnection.systemBus", return_value=fake_dbus, create=True):
            filter_ = startup_utils.install_native_handlers(app, window)
        self.assertIsInstance(filter_, startup_utils.LinuxDBusPowerNetworkFilter)

        # Linux DBus init failure returns None safely
        with mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "LinuxDBusPowerNetworkFilter", side_effect=RuntimeError("fail")):
            self.assertIsNone(startup_utils.install_native_handlers(app, window))


class LinuxDBusFilterTest(unittest.TestCase):

    def setUp(self):
        self.resume = mock.Mock()
        self.net_change = mock.Mock()
        self.fake_bus = mock.Mock(isConnected=mock.Mock(return_value=True),
                                  connect=mock.Mock(return_value=True),
                                  disconnect=mock.Mock(return_value=True))

    def test_dbus_registration_and_disconnect(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume, on_network_change=self.net_change, bus=self.fake_bus)
        self.assertTrue(filter_.is_connected)
        self.assertEqual(self.fake_bus.connect.call_count, 2)
        filter_.disconnect_signals()
        self.assertFalse(filter_.is_connected)
        self.assertEqual(self.fake_bus.disconnect.call_count, 2)

    def test_prepare_for_sleep_flow(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume, on_network_change=self.net_change, bus=self.fake_bus)
        filter_.handle_prepare_for_sleep(True)
        self.assertTrue(filter_.is_sleeping)
        self.resume.assert_not_called()

        filter_.handle_prepare_for_sleep(False)
        self.assertFalse(filter_.is_sleeping)
        self.resume.assert_called_once_with()

    def test_network_manager_state_changed_flow(self):
        filter_ = startup_utils.LinuxDBusPowerNetworkFilter(
            on_resume=self.resume, on_network_change=self.net_change, bus=self.fake_bus)
        # Non-global states ignored
        for st in (startup_utils.NM_STATE_DISCONNECTED, startup_utils.NM_STATE_CONNECTING, "invalid"):
            filter_.handle_nm_state_changed(st)
        self.net_change.assert_not_called()

        # Global connected triggers reconnect
        filter_.handle_nm_state_changed(startup_utils.NM_STATE_CONNECTED_GLOBAL)
        self.net_change.assert_called_once_with()


class ExcepthookTest(unittest.TestCase):

    def setUp(self):
        self._original_hook = sys.excepthook
        self.addCleanup(self._restore_hook)

    def _restore_hook(self):
        sys.excepthook = self._original_hook

    def test_excepthook_logs_and_prints(self):
        startup_utils.install_excepthook()
        self.assertIsNot(sys.excepthook, self._original_hook)
        logger = mock.Mock()
        with mock.patch.object(startup_utils.logging, "getLogger", return_value=logger), \
             mock.patch.object(startup_utils.traceback, "print_exception"):
            sys.excepthook(ValueError, ValueError("boom"), None)
        logger.critical.assert_called_once()

    def test_excepthook_handles_keyboard_interrupt(self):
        window = mock.Mock()
        startup_utils.install_excepthook(window)
        with mock.patch.object(startup_utils.logging, "getLogger") as logger_mock:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        window.quit_app.assert_called_once_with()


class SetupSigintHandlerTest(unittest.TestCase):

    def test_sigint_handler_posix_and_windows(self):
        import signal

        class FakeApp(QObject):
            aboutToQuit = Signal()

        app = FakeApp()
        window = mock.Mock()

        # POSIX: installs socket notifier
        with mock.patch("signal.signal"), \
             mock.patch.object(startup_utils, "ctypes", SimpleNamespace()), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils.signal, "set_wakeup_fd", return_value=-1) as mock_wakeup:
            startup_utils.setup_sigint_handler(app, window)
            self.assertTrue(hasattr(app, "_sigint_notifier"))
            rsock, wsock = app._sigint_sockets
            mock_wakeup.assert_called_with(wsock.fileno(), warn_on_full_buffer=False)
            app.aboutToQuit.emit()
            self.assertTrue(rsock._closed)
            self.assertTrue(wsock._closed)

        # Windows: no polling timer or socket notifier
        app_win = QObject()
        with mock.patch("signal.signal"), \
             mock.patch.object(startup_utils, "is_windows", return_value=True):
            startup_utils.setup_sigint_handler(app_win, window)
            self.assertFalse(hasattr(app_win, "_sigint_timer"))
            self.assertFalse(hasattr(app_win, "_sigint_notifier"))


class DesktopFileNameTest(unittest.TestCase):

    def test_desktop_file_name_platforms(self):
        with mock.patch.object(startup_utils, "is_windows", return_value=True):
            self.assertEqual(startup_utils.desktop_file_name(), "Socksicle")
        with mock.patch.object(startup_utils, "is_windows", return_value=False):
            self.assertEqual(startup_utils.desktop_file_name(), "Socksicle.desktop")


class XDGAutostartLinuxTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.config_dir = Path(self.tmp_dir.name)
        self.desktop_file = self.config_dir / "autostart" / "socksicle.desktop"

    def test_autostart_paths(self):
        # CI images export XDG_CONFIG_HOME, which takes precedence over Path.home().
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(startup_utils.Path, "home", return_value=startup_utils.Path("/home/u")):
            self.assertEqual(str(startup_utils.get_autostart_desktop_path()).replace("\\", "/"),
                             "/home/u/.config/autostart/socksicle.desktop")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/cfg"}):
            self.assertEqual(str(startup_utils.get_autostart_desktop_path()).replace("\\", "/"),
                             "/custom/cfg/autostart/socksicle.desktop")

    def test_set_and_is_autostart_linux_lifecycle(self):
        with mock.patch.object(startup_utils, "is_linux", return_value=True), \
             mock.patch.object(startup_utils, "is_windows", return_value=False), \
             mock.patch.object(startup_utils, "get_autostart_desktop_path", return_value=self.desktop_file):
            # Initially disabled
            self.assertFalse(startup_utils.is_autostart_enabled())
            # Enable
            self.assertTrue(startup_utils.set_autostart(True))
            self.assertTrue(startup_utils.is_autostart_enabled())
            content = self.desktop_file.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", content)
            self.assertIn("--minimized", content)
            # Disable
            self.assertTrue(startup_utils.set_autostart(False))
            self.assertFalse(startup_utils.is_autostart_enabled())


class WindowsAutostartTest(unittest.TestCase):

    def test_windows_autostart_lifecycle(self):
        mock_winreg = mock.MagicMock()
        mock_key = mock.MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = ("C:\\Socksicle.exe --minimized", 1)

        with mock.patch.object(startup_utils, "is_windows", return_value=True), \
             mock.patch.dict("sys.modules", {"winreg": mock_winreg}):
            self.assertTrue(startup_utils.is_autostart_enabled())
            self.assertTrue(startup_utils.set_autostart(True, app_path="C:\\app.exe"))
            mock_winreg.SetValueEx.assert_called_once()
            self.assertTrue(startup_utils.set_autostart(False))
            mock_winreg.DeleteValue.assert_called_once_with(mock_key, "Socksicle")


class IsFrozenAndAppDirTest(unittest.TestCase):

    def test_is_frozen_and_app_dir(self):
        from utils.platform_utils import is_frozen, get_app_dir
        with mock.patch.object(sys, "executable", "/usr/bin/python3"):
            self.assertFalse(is_frozen())
        fake_exe = Path.cwd() / "fake_dir" / "Socksicle.exe"
        with mock.patch.object(sys, "executable", str(fake_exe)), \
             mock.patch("utils.platform_utils.is_frozen", return_value=True):
            self.assertEqual(get_app_dir(), fake_exe.resolve().parent)


if __name__ == "__main__":
    unittest.main()
