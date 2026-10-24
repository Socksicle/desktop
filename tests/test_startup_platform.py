"""Unit tests for utils.startup_utils platform-specific pieces."""
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest

from utils import startup_utils
from utils.startup_utils import TrayAndPowerFilter
from PySide6.QtCore import QObject, Qt, Signal

_HIGH_DPI_KEYS = ("QT_ENABLE_HIGHDPI_SCALING", "QT_AUTO_SCREEN_SCALE_FACTOR",
                  "QT_SCALE_FACTOR_ROUNDING_POLICY")


def test_platform_env_and_handlers():
    # 1. High DPI keys & metadata
    if sys.platform != "win32":
        for key in _HIGH_DPI_KEYS:
            assert os.environ.get(key) is None

    with mock.patch.object(startup_utils, "is_windows", return_value=True):
        assert startup_utils.desktop_file_name() == "Socksicle"
    with mock.patch.object(startup_utils, "is_windows", return_value=False):
        assert startup_utils.desktop_file_name() == "Socksicle.desktop"

    from utils.platform_utils import is_frozen, get_app_dir
    with mock.patch.object(sys, "executable", "/usr/bin/python3"):
        assert is_frozen() is False
    fake_exe = Path.cwd() / "fake_dir" / "Socksicle.exe"
    with mock.patch.object(sys, "executable", str(fake_exe)), \
         mock.patch("utils.platform_utils.is_frozen", return_value=True):
        assert get_app_dir() == fake_exe.resolve().parent

    # 2. Taskbar created registration
    user32 = mock.Mock()
    user32.RegisterWindowMessageW.return_value = 0xC123
    fake_ctypes = SimpleNamespace(windll=SimpleNamespace(user32=user32))

    with mock.patch.object(startup_utils, "is_windows", return_value=True), \
         mock.patch.object(startup_utils, "ctypes", fake_ctypes):
        assert startup_utils._register_taskbar_created_message() == 0xC123

    user32.RegisterWindowMessageW.side_effect = OSError("user32 unavailable")
    with mock.patch.object(startup_utils, "is_windows", return_value=True), \
         mock.patch.object(startup_utils, "ctypes", fake_ctypes), \
         mock.patch.object(startup_utils.log, "warning"):
        assert startup_utils._register_taskbar_created_message() == 0

    with mock.patch.object(startup_utils, "is_windows", return_value=False):
        assert startup_utils._register_taskbar_created_message() == 0

    # 3. High DPI policy platform handling
    app = mock.Mock()
    with mock.patch.object(startup_utils, "is_windows", return_value=True):
        startup_utils.apply_high_dpi_policy(app)
        app.setHighDpiScaleFactorRoundingPolicy.assert_called_once_with(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app_linux = mock.Mock()
    with mock.patch.object(startup_utils, "is_windows", return_value=False):
        startup_utils.apply_high_dpi_policy(app_linux)
        app_linux.setHighDpiScaleFactorRoundingPolicy.assert_not_called()

    # 4. Install native handlers (Windows & Linux)
    window = mock.NonCallableMock(spec=["tray_manager", "reconnect_after_resume", "quit_app"])
    window.tray_manager = mock.Mock()

    with mock.patch.object(startup_utils, "is_windows", return_value=True), \
         mock.patch.object(startup_utils, "is_linux", return_value=False), \
         mock.patch.object(startup_utils, "_register_taskbar_created_message", return_value=0xC123):
        filter_ = startup_utils.install_native_handlers(app, window)
    assert isinstance(filter_, TrayAndPowerFilter)
    app.installNativeEventFilter.assert_called_once_with(filter_)
    filter_.on_taskbar_created()
    window.tray_manager.restore.assert_called_once_with()
    filter_.on_resume()
    window.reconnect_after_resume.assert_called_once_with()

    fake_dbus = mock.Mock(isConnected=mock.Mock(return_value=True), connect=mock.Mock(return_value=True))
    with mock.patch.object(startup_utils, "is_windows", return_value=False), \
         mock.patch.object(startup_utils, "is_linux", return_value=True), \
         mock.patch("PySide6.QtDBus.QDBusConnection.systemBus", return_value=fake_dbus, create=True):
        filter_linux = startup_utils.install_native_handlers(app_linux, window)
    assert isinstance(filter_linux, startup_utils.LinuxDBusPowerNetworkFilter)

    with mock.patch.object(startup_utils, "is_windows", return_value=False), \
         mock.patch.object(startup_utils, "is_linux", return_value=True), \
         mock.patch.object(startup_utils, "LinuxDBusPowerNetworkFilter", side_effect=RuntimeError("fail")):
        assert startup_utils.install_native_handlers(app_linux, window) is None


def _make_msg(msg=0, wparam=0):
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


def test_tray_and_power_native_filters():
    taskbar_msg = 0xC123
    with mock.patch.object(startup_utils, "_register_taskbar_created_message", return_value=taskbar_msg):
        taskbar = mock.Mock()
        resume = mock.Mock()
        filter_ = TrayAndPowerFilter(on_taskbar_created=taskbar, on_resume=resume)

        # Taskbar created
        buf = _make_msg(msg=taskbar_msg)
        assert filter_.nativeEventFilter(b"windows_generic_MSG", buf) == (False, 0)
        taskbar.assert_called_once_with()
        resume.assert_not_called()

        # Power resume
        taskbar.reset_mock()
        buf_pwr = _make_msg(msg=startup_utils.WM_POWERBROADCAST, wparam=startup_utils.PBT_APMRESUMESUSPEND)
        filter_.nativeEventFilter(b"windows_generic_MSG", buf_pwr)
        resume.assert_called_once_with()
        taskbar.assert_not_called()

        # Ignored / malformed buffers
        resume.reset_mock()
        buf_ign = _make_msg(msg=startup_utils.WM_POWERBROADCAST, wparam=0)
        filter_.nativeEventFilter(b"windows_generic_MSG", buf_ign)
        filter_.nativeEventFilter(b"generic", buf)
        resume.assert_not_called()
        taskbar.assert_not_called()

        for message in (b"\x00" * 4, 12345):
            assert filter_.nativeEventFilter(b"windows_generic_MSG", message) == (False, 0)

        # KeyboardInterrupt
        interrupt_mock = mock.Mock()
        filter_intr = TrayAndPowerFilter(
            on_taskbar_created=mock.Mock(side_effect=KeyboardInterrupt),
            on_resume=resume,
            on_interrupt=interrupt_mock
        )
        assert filter_intr.nativeEventFilter(b"windows_generic_MSG", buf) == (True, 0)
        interrupt_mock.assert_called_once_with()

    # Linux DBus Power and Network Filter
    resume_dbus = mock.Mock()
    net_change = mock.Mock()
    fake_bus = mock.Mock(isConnected=mock.Mock(return_value=True),
                         connect=mock.Mock(return_value=True),
                         disconnect=mock.Mock(return_value=True))

    dbus_filter = startup_utils.LinuxDBusPowerNetworkFilter(
        on_resume=resume_dbus, on_network_change=net_change, bus=fake_bus)
    assert dbus_filter.is_connected is True
    assert fake_bus.connect.call_count == 2

    # Prepare for sleep
    dbus_filter.handle_prepare_for_sleep(True)
    assert dbus_filter.is_sleeping is True
    resume_dbus.assert_not_called()
    dbus_filter.handle_prepare_for_sleep(False)
    assert dbus_filter.is_sleeping is False
    resume_dbus.assert_called_once_with()

    # NetworkManager state changes
    for st in (startup_utils.NM_STATE_DISCONNECTED, startup_utils.NM_STATE_CONNECTING, "invalid"):
        dbus_filter.handle_nm_state_changed(st)
    net_change.assert_not_called()
    dbus_filter.handle_nm_state_changed(startup_utils.NM_STATE_CONNECTED_GLOBAL)
    net_change.assert_called_once_with()

    # Disconnect signals
    dbus_filter.disconnect_signals()
    assert dbus_filter.is_connected is False
    assert fake_bus.disconnect.call_count == 2


def test_excepthook_and_sigint_handlers():
    original_hook = sys.excepthook
    try:
        # Excepthook handling
        startup_utils.install_excepthook()
        assert sys.excepthook is not original_hook
        logger = mock.Mock()
        with mock.patch.object(startup_utils.logging, "getLogger", return_value=logger), \
             mock.patch.object(startup_utils.traceback, "print_exception"):
            sys.excepthook(ValueError, ValueError("boom"), None)
        logger.critical.assert_called_once()

        window = mock.Mock()
        startup_utils.install_excepthook(window)
        with mock.patch.object(startup_utils.logging, "getLogger"):
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        window.quit_app.assert_called_once_with()
    finally:
        sys.excepthook = original_hook

    # SIGINT handler POSIX and Windows
    class FakeApp(QObject):
        aboutToQuit = Signal()

    app = FakeApp()
    win_mock = mock.Mock()

    # POSIX: installs socket notifier
    with mock.patch("signal.signal"), \
         mock.patch.object(startup_utils, "ctypes", SimpleNamespace()), \
         mock.patch.object(startup_utils, "is_windows", return_value=False), \
         mock.patch.object(startup_utils.signal, "set_wakeup_fd", return_value=-1) as mock_wakeup:
        startup_utils.setup_sigint_handler(app, win_mock)
        assert hasattr(app, "_sigint_notifier")
        rsock, wsock = app._sigint_sockets
        mock_wakeup.assert_called_with(wsock.fileno(), warn_on_full_buffer=False)
        app.aboutToQuit.emit()
        assert rsock._closed and wsock._closed

    # Windows: no polling timer or socket notifier
    app_win = QObject()
    with mock.patch("signal.signal") as mock_sig, \
         mock.patch.object(startup_utils, "is_windows", return_value=True):
        startup_utils.setup_sigint_handler(app_win, win_mock)
        assert not hasattr(app_win, "_sigint_timer")
        assert not hasattr(app_win, "_sigint_notifier")

        # Verify both SIGINT and SIGTERM were registered
        registered_signals = [call.args[0] for call in mock_sig.call_args_list]
        assert signal.SIGINT in registered_signals
        assert signal.SIGTERM in registered_signals

        # Verify invoking SIGTERM handler triggers clean quit_app()
        term_handler = [call.args[1] for call in mock_sig.call_args_list if call.args[0] == signal.SIGTERM][0]
        term_handler(signal.SIGTERM, None)
        win_mock.quit_app.assert_called_once()


def test_autostart_lifecycle_cross_platform(tmp_path):
    desktop_file = tmp_path / "autostart" / "socksicle.desktop"

    # 1. XDG Linux autostart
    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch.object(startup_utils.Path, "home", return_value=startup_utils.Path("/home/u")):
        assert str(startup_utils.get_autostart_desktop_path()).replace("\\", "/") == "/home/u/.config/autostart/socksicle.desktop"
    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/cfg"}):
        assert str(startup_utils.get_autostart_desktop_path()).replace("\\", "/") == "/custom/cfg/autostart/socksicle.desktop"

    with mock.patch.object(startup_utils, "is_linux", return_value=True), \
         mock.patch.object(startup_utils, "is_windows", return_value=False), \
         mock.patch.object(startup_utils, "get_autostart_desktop_path", return_value=desktop_file):
        assert startup_utils.is_autostart_enabled() is False
        assert startup_utils.set_autostart(True) is True
        assert startup_utils.is_autostart_enabled() is True
        content = desktop_file.read_text(encoding="utf-8")
        assert "[Desktop Entry]" in content and "--minimized" in content
        assert startup_utils.set_autostart(False) is True
        assert startup_utils.is_autostart_enabled() is False

    # 2. Windows registry autostart lifecycle
    mock_winreg = mock.MagicMock()
    mock_key = mock.MagicMock()
    mock_winreg.OpenKey.return_value = mock_key
    mock_winreg.QueryValueEx.return_value = ("C:\\Socksicle.exe --minimized", 1)

    with mock.patch.object(startup_utils, "is_windows", return_value=True), \
         mock.patch.dict("sys.modules", {"winreg": mock_winreg}):
        assert startup_utils.is_autostart_enabled() is True
        assert startup_utils.set_autostart(True, app_path="C:\\app.exe") is True
        mock_winreg.SetValueEx.assert_called_once()
        assert startup_utils.set_autostart(False) is True
        mock_winreg.DeleteValue.assert_called_once_with(mock_key, "Socksicle")
