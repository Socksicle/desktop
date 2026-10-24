"""Tests for single-instance guard, local IPC, and window restoration."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QWidget

from ui.main_window import RoundedWindow
from utils import single_instance
from utils.single_instance import (
    acquire_single_instance_lock,
    get_single_instance_name,
    notify_running_instance,
    setup_single_instance_server,
    SingleInstanceServer,
)
import main


class SingleInstanceLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_get_single_instance_name(self):
        name1 = get_single_instance_name(self.root)
        name2 = get_single_instance_name(self.root)
        self.assertEqual(name1, name2)
        self.assertTrue(name1.startswith("socksicle_instance_"))

        other_root = self.root / "other"
        name_other = get_single_instance_name(other_root)
        self.assertNotEqual(name1, name_other)

    def test_single_instance_lock_lifecycle(self):
        lock = acquire_single_instance_lock(self.root)
        self.assertIsNotNone(lock)
        self.assertTrue((self.root / "single_instance.lock").exists())

        # Second acquisition fails
        second_lock = acquire_single_instance_lock(self.root)
        self.assertIsNone(second_lock)

        # Release and reacquire
        lock.unlock()
        reacquired = acquire_single_instance_lock(self.root)
        self.assertIsNotNone(reacquired)
        reacquired.unlock()

    def test_single_instance_removes_stale_lock_after_crash(self):
        # Simulate a stale lock file left behind by a dead process
        lock_path = self.root / "single_instance.lock"
        lock_path.write_text("9999999\nfake_app\nfake_host\n", encoding="utf-8")

        lock = acquire_single_instance_lock(self.root)
        self.assertIsNotNone(lock)
        self.assertTrue(lock_path.exists())
        lock.unlock()


def test_server_and_client_notification(qapp, tmp_path):
    server_name = f"test_socksicle_ipc_{tmp_path.name}"
    server = SingleInstanceServer()
    assert server.start(server_name)

    activated_calls = []
    server.activated.connect(lambda: activated_calls.append(True))

    assert notify_running_instance(name=server_name, timeout_ms=500, attempts=1)

    # Process events to allow Qt to dispatch server signal
    qapp.processEvents()

    assert len(activated_calls) == 1
    assert server.activation_requested is True

    server.close()


def test_notify_nonexistent_server_fails_cleanly(qapp):
    success = notify_running_instance(
        name="test_socksicle_definitely_nonexistent_9999",
        timeout_ms=50,
        attempts=1,
    )
    assert success is False


def test_setup_single_instance_server_registers_about_to_quit(qapp, tmp_path):
    server_name = f"test_socksicle_setup_{tmp_path.name}"
    server = setup_single_instance_server(qapp, name=server_name)
    assert server is not None
    assert server.server is not None
    assert server.server.isListening()

    server.close()


def test_setup_single_instance_server_without_qapp():
    with mock.patch("PySide6.QtCore.QCoreApplication.instance", return_value=None):
        srv = setup_single_instance_server()
        assert srv is None


def test_window_show_and_activate(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)

    win = RoundedWindow()
    try:
        # 1. Hidden to tray -> show_and_activate makes it visible and active
        win.hide()
        assert not win.isVisible()
        win.show_and_activate()
        assert win.isVisible()

        # 2. Minimized -> show_and_activate restores normal state
        win.showMinimized()
        win.show_and_activate()
        assert not win.isMinimized()

        # 3. Active modal widget gets raised too
        modal = QWidget(win)
        modal.setWindowModality(win.windowModality())
        modal.show()
        with mock.patch.object(QApplication, "activeModalWidget", return_value=modal):
            with mock.patch.object(modal, "raise_") as mock_raise, \
                 mock.patch.object(modal, "activateWindow") as mock_activate:
                win.show_and_activate()
                mock_raise.assert_called_once()
                mock_activate.assert_called_once()
        modal.close()
    finally:
        win.close()
        win.deleteLater()


class MainSingleInstanceFlowTest(unittest.TestCase):
    def test_secondary_instance_normal_notifies_and_exits_0(self):
        with mock.patch.object(main, "acquire_single_instance_lock", return_value=None), \
             mock.patch.object(main, "notify_running_instance", return_value=True) as mock_notify, \
             mock.patch.object(main, "show_already_running") as mock_dialog, \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "QApplication", return_value=mock.Mock()), \
             mock.patch.object(main, "desktop_file_name", return_value="Socksicle.desktop"), \
             mock.patch.object(sys, "exit", side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main([])
            exit_mock.assert_called_once_with(0)
            mock_notify.assert_called_once()
            mock_dialog.assert_not_called()

    def test_secondary_instance_minimized_flag_exits_0_without_notify(self):
        with mock.patch.object(main, "acquire_single_instance_lock", return_value=None), \
             mock.patch.object(main, "notify_running_instance") as mock_notify, \
             mock.patch.object(main, "show_already_running") as mock_dialog, \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "QApplication", return_value=mock.Mock()), \
             mock.patch.object(main, "desktop_file_name", return_value="Socksicle.desktop"), \
             mock.patch.object(sys, "exit", side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main(["--minimized"])
            exit_mock.assert_called_once_with(0)
            mock_notify.assert_not_called()
            mock_dialog.assert_not_called()

    def test_secondary_instance_fallback_to_dialog_when_notify_fails(self):
        with mock.patch.object(main, "acquire_single_instance_lock", return_value=None), \
             mock.patch.object(main, "notify_running_instance", return_value=False) as mock_notify, \
             mock.patch.object(main, "show_already_running") as mock_dialog, \
             mock.patch.object(main, "apply_language_from_settings") as mock_lang, \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "QApplication", return_value=mock.Mock()), \
             mock.patch.object(main, "desktop_file_name", return_value="Socksicle.desktop"), \
             mock.patch.object(sys, "exit", side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                main.main([])
            exit_mock.assert_called_once_with(0)
            mock_notify.assert_called_once()
            mock_lang.assert_called_once()
            mock_dialog.assert_called_once()

    def test_primary_instance_connects_server_and_activates_if_pending(self):
        server_mock = mock.Mock()
        server_mock.activation_requested = True
        window_mock = mock.Mock()
        app_mock = mock.Mock()
        app_mock.exec.return_value = 0

        with mock.patch.object(main, "acquire_single_instance_lock", return_value=mock.Mock()), \
             mock.patch.object(main, "setup_single_instance_server", return_value=server_mock), \
             mock.patch.object(main, "RoundedWindow", return_value=window_mock), \
             mock.patch.object(main, "QIcon"), \
             mock.patch.object(main, "get_app_dir", return_value=Path.cwd() / "icon.svg"), \
             mock.patch.object(main, "find_usable_backend", return_value=Path("/bin/sing-box")), \
             mock.patch.object(main, "initialize"), \
             mock.patch.object(main, "apply_high_dpi_policy"), \
             mock.patch.object(main, "desktop_file_name", return_value="Socksicle.desktop"), \
             mock.patch.object(main, "install_native_handlers"), \
             mock.patch.object(main, "QApplication", return_value=app_mock), \
             mock.patch.object(sys, "exit", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                main.main([])

            server_mock.activated.connect.assert_called_once_with(window_mock.show_and_activate)
            window_mock.show_and_activate.assert_called_once()
