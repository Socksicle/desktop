"""Entry-point tests for the single Socksicle entry point (main.py).

Complements test_startup_platform.py: verifies exit codes, the shared flow
order with a single backend check, and -- with real platform functions --
that Windows-specific startup only runs on Windows and Linux keeps its
exact previous behavior.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main  # noqa: E402
from utils import startup_utils as su  # noqa: E402


def _run_main(backend, exit_behavior=SystemExit):
    """Run main.main() with everything concrete mocked; sys.exit raises."""
    app_inst = mock.Mock()
    app_inst.exec.side_effect = exit_behavior or (lambda: 0)
    app_cls = mock.Mock(return_value=app_inst)
    window_inst = mock.Mock()
    window_cls = mock.Mock(return_value=window_inst)
    with mock.patch.object(main, "QApplication", app_cls), \
         mock.patch.object(main, "RoundedWindow", window_cls), \
         mock.patch.object(main, "QIcon"), \
         mock.patch.object(main, "get_app_dir",
                           return_value=Path.cwd() / "icon.svg"), \
         mock.patch.object(main, "find_usable_backend",
                           return_value=backend), \
         mock.patch.object(sys, "exit",
                           side_effect=SystemExit) as exit_mock:
        try:
            main.main()
        except SystemExit:
            pass
    return app_inst, window_inst, app_cls, window_cls, exit_mock


class ExitCodeTest(unittest.TestCase):
    """main.main() exits with the Qt event loop code or 1 on backend loss."""

    def test_success_uses_app_exec_code(self):
        app_inst = mock.Mock()
        app_inst.exec.return_value = 42
        with mock.patch.object(main, "QApplication",
                               return_value=app_inst), \
             mock.patch.object(main, "RoundedWindow",
                               return_value=mock.Mock()), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir",
                               return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend",
                               return_value=Path("/bin/sing-box")), \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "desktop_file_name",
                               return_value="Socksicle.desktop"), \
             mock.patch.object(main, "install_native_handlers"), \
             mock.patch.object(sys, "exit",
                               side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main()
        exit_mock.assert_called_once_with(42)
        app_inst.exec.assert_called_once_with()

    def test_missing_backend_shows_dialog_and_exits_1(self):
        with mock.patch.object(main, "QApplication",
                               return_value=mock.Mock()), \
             mock.patch.object(main, "RoundedWindow") as window_cls, \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "find_usable_backend",
                               return_value=None), \
             mock.patch.object(main, "show_backend_missing") as show, \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "desktop_file_name"), \
             mock.patch.object(sys, "exit",
                               side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main()
            exit_mock.assert_called_once_with(1)
            show.assert_called_once()
            window_cls.assert_not_called()


class SharedFlowOrderTest(unittest.TestCase):
    """The shared flow runs once, in order, with one backend check."""

    def test_flow_order_and_single_backend_check(self):
        calls = []
        app_inst = mock.Mock()
        app_inst.exec.side_effect = lambda: calls.append("exec") or 0
        window_inst = mock.Mock()
        window_inst.show.side_effect = lambda: calls.append("show")
        with mock.patch.object(main, "QApplication",
                               return_value=app_inst), \
             mock.patch.object(main, "RoundedWindow",
                               side_effect=lambda: calls.append("create")
                               or window_inst), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir",
                               return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend",
                               side_effect=lambda: calls.append("backend")
                               or Path("/bin/sing-box")) as check, \
             mock.patch.object(main, "initialize",
                               side_effect=lambda: calls.append("init")), \
             mock.patch.object(main, "apply_high_dpi_policy",
                               side_effect=lambda *a: calls.append("high-dpi")), \
             mock.patch.object(main, "desktop_file_name",
                               side_effect=lambda: calls.append("desktop-file")
                               or "Socksicle.desktop"), \
             mock.patch.object(main, "install_native_handlers",
                               side_effect=lambda a, w: calls.append(
                                   "handlers")), \
             mock.patch.object(sys, "exit",
                               side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main()
        self.assertEqual(calls, [
            "init", "high-dpi", "desktop-file", "backend",
            "create", "handlers", "show", "exec",
        ])
        check.assert_called_once_with()
        exit_mock.assert_called_once_with(0)


@mock.patch.object(su, "is_windows", return_value=False)
class LinuxPathTest(unittest.TestCase):
    """Real platform functions: Linux skips every Windows-only step."""

    def test_linux_flow_keeps_previous_behavior(self, _is_win):
        app_inst = mock.Mock(spec=[
            "setApplicationName", "setDesktopFileName", "setWindowIcon",
            "setHighDpiScaleFactorRoundingPolicy", "installNativeEventFilter",
            "setStyleSheet", "setStyle", "setPalette", "exec",
        ])
        app_inst.exec.return_value = 0
        with mock.patch.object(main, "QApplication",
                               return_value=app_inst), \
             mock.patch.object(main, "RoundedWindow",
                               return_value=mock.Mock()), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir",
                               return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend",
                               return_value=Path("/bin/sing-box")), \
             mock.patch.object(sys, "exit",
                               side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main()
        exit_mock.assert_called_once_with(0)
        app_inst.setApplicationName.assert_called_once_with("Socksicle")
        app_inst.setDesktopFileName.assert_called_once_with(
            "Socksicle.desktop")
        app_inst.setHighDpiScaleFactorRoundingPolicy.assert_not_called()
        app_inst.installNativeEventFilter.assert_not_called()
        if sys.platform == "win32":
            app_inst.setStyle.assert_not_called()
            app_inst.setPalette.assert_not_called()
        else:
            app_inst.setStyle.assert_called_once_with("Fusion")
            app_inst.setPalette.assert_called_once()
        self.assertNotIn("native_filter", app_inst._mock_children)


class WindowsPathTest(unittest.TestCase):
    """Real platform functions: Windows applies all platform pieces."""

    @mock.patch.object(su, "is_windows", return_value=True)
    def test_windows_flow_applies_windows_pieces(self, _is_win):
        app_inst = mock.Mock()
        app_inst.exec.return_value = 0
        window_inst = mock.Mock()
        with mock.patch.object(su, "setup_logging"), \
             mock.patch.object(su.logging, "getLogger"), \
             mock.patch.object(su, "set_app_user_model_id"), \
             mock.patch.object(su, "install_excepthook"), \
             mock.patch.object(su, "_register_taskbar_created_message",
                               return_value=0xC123), \
             mock.patch.object(main, "QApplication",
                               return_value=app_inst), \
             mock.patch.object(main, "RoundedWindow",
                               return_value=window_inst), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir",
                               return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend",
                               return_value=Path("C:/bin/sing-box.exe")), \
             mock.patch.object(sys, "exit",
                               side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main()
        exit_mock.assert_called_once_with(0)
        app_inst.setDesktopFileName.assert_called_once_with("Socksicle")
        app_inst.installNativeEventFilter.assert_called_once()
        self.assertIsInstance(app_inst.native_filter,
                              su.TrayAndPowerFilter)
        app_inst.native_filter.on_taskbar_created()
        window_inst.tray_manager.restore.assert_called_once_with()
        app_inst.native_filter.on_resume()
        window_inst.reconnect_after_resume.assert_called_once_with()


class CLIMinimizedTest(unittest.TestCase):
    """Tests for --minimized / -m command line argument behavior in main.py."""

    def test_parse_args_flags(self):
        args_default = main.parse_args([])
        self.assertFalse(args_default.minimized)

        args_long = main.parse_args(["--minimized"])
        self.assertTrue(args_long.minimized)

        args_short = main.parse_args(["-m"])
        self.assertTrue(args_short.minimized)

    def test_minimized_with_tray_available_skips_window_show(self):
        app_inst = mock.Mock()
        app_inst.exec.return_value = 0
        window_inst = mock.Mock()

        with mock.patch.object(main, "QApplication", return_value=app_inst), \
             mock.patch.object(main, "RoundedWindow", return_value=window_inst), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir", return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend", return_value=Path("/bin/sing-box")), \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "desktop_file_name", return_value="Socksicle.desktop"), \
             mock.patch.object(main, "install_native_handlers"), \
             mock.patch.object(main.QSystemTrayIcon, "isSystemTrayAvailable", return_value=True), \
             mock.patch.object(sys, "exit", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                main.main(["--minimized"])

        window_inst.show.assert_not_called()
        app_inst.exec.assert_called_once()

    def test_minimized_without_tray_available_shows_window(self):
        app_inst = mock.Mock()
        app_inst.exec.return_value = 0
        window_inst = mock.Mock()

        with mock.patch.object(main, "QApplication", return_value=app_inst), \
             mock.patch.object(main, "RoundedWindow", return_value=window_inst), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir", return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend", return_value=Path("/bin/sing-box")), \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "desktop_file_name", return_value="Socksicle.desktop"), \
             mock.patch.object(main, "install_native_handlers"), \
             mock.patch.object(main.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False), \
             mock.patch.object(sys, "exit", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                main.main(["-m"])

        window_inst.show.assert_called_once()
        app_inst.exec.assert_called_once()


def test_app_icon_svg_loads(qapp):
    """The bundled vector app icon must rasterize (window + tray use it)."""
    from PySide6.QtGui import QIcon

    icon_file = Path(__file__).resolve().parent.parent / "icon.svg"
    assert icon_file.is_file()
    icon = QIcon(str(icon_file))
    assert not icon.isNull()
    for size in (16, 32, 64):
        pm = icon.pixmap(size, size)
        assert not pm.isNull()
        assert pm.width() == size


if __name__ == "__main__":
    unittest.main()
