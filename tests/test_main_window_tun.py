"""Tests for MainWindow TUN mode elevation and Linux capabilities handling."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest
from PySide6.QtWidgets import QMessageBox

from ui.main_window import RoundedWindow
from utils.server_model import Server, ProxyProtocol
import utils.twinsock as tw




def test_tun_linux_no_gui_elevation_when_caps_present(main_win, monkeypatch):
    """On Linux, TUN mode should NOT elevate GUI via elevate_restart if caps are present."""
    main_win.settings["tun_mode"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "linux")
    monkeypatch.setattr("sys.platform", "linux")

    elevate_called = []
    grant_called = []

    monkeypatch.setattr(main_win.connection_manager.engine, "find_binary", lambda: Path("/fake/sing-box"))
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_called.append(True))
    monkeypatch.setattr("utils.platform_utils.check_tun_capabilities", lambda p: True)
    monkeypatch.setattr("utils.platform_utils.grant_tun_capabilities", lambda p, parent_window=None: grant_called.append(p))
    monkeypatch.setattr(main_win.connection_manager, "toggle", lambda srv, connect: True)

    main_win.toggle_connection(True)

    assert len(elevate_called) == 0
    assert len(grant_called) == 0


def test_tun_linux_grants_caps_without_gui_restart(main_win, monkeypatch):
    """On Linux, missing capabilities trigger grant_tun_capabilities without restarting GUI."""
    main_win.settings["tun_mode"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "linux")
    monkeypatch.setattr("sys.platform", "linux")

    elevate_called = []
    grant_called = []

    checks = [False, True]
    monkeypatch.setattr(main_win.connection_manager.engine, "find_binary", lambda: Path("/fake/sing-box"))
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_called.append(True))
    monkeypatch.setattr("utils.platform_utils.check_tun_capabilities", lambda p: checks.pop(0) if checks else True)
    monkeypatch.setattr("utils.platform_utils.grant_tun_capabilities", lambda p, parent_window=None: grant_called.append(p) or True)
    monkeypatch.setattr(main_win.connection_manager, "toggle", lambda srv, connect: True)

    main_win.toggle_connection(True)

    assert len(elevate_called) == 0
    assert len(grant_called) == 1


def test_tun_linux_warns_and_aborts_when_grant_fails(main_win, monkeypatch):
    """On Linux, declining polkit prompt shows warning and aborts without restarting GUI."""
    main_win.settings["tun_mode"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "linux")
    monkeypatch.setattr("sys.platform", "linux")

    elevate_called = []
    grant_called = []
    warnings = []

    monkeypatch.setattr(main_win.connection_manager.engine, "find_binary", lambda: Path("/fake/sing-box"))
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_called.append(True))
    monkeypatch.setattr("utils.platform_utils.check_tun_capabilities", lambda p: False)
    monkeypatch.setattr("utils.platform_utils.grant_tun_capabilities", lambda p, parent_window=None: grant_called.append(p) or False)
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append((title, text)))

    main_win.toggle_connection(True)

    assert len(elevate_called) == 0
    assert len(grant_called) == 1
    assert len(warnings) == 1
    from utils.i18n import tr
    assert tr("dlg.tun_title") in warnings[0][0]


def test_tun_windows_asks_for_uac_elevation_when_not_admin(main_win, monkeypatch):
    """On Windows, TUN mode prompts for UAC restart if not admin."""
    main_win.settings["tun_mode"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "win32")
    monkeypatch.setattr("sys.platform", "win32")

    elevate_called = []
    questions = []

    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr("utils.platform_utils.is_admin", lambda: False)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_called.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, text, buttons, default: questions.append((title, text)) or QMessageBox.Yes)

    main_win.toggle_connection(True)

    assert len(questions) == 1
    from utils.i18n import tr
    assert tr("dlg.admin_title") in questions[0][0]
    assert len(elevate_called) == 1


def test_killswitch_windows_asks_for_uac_elevation(main_win, monkeypatch):
    """Kill Switch needs Administrator rights on Windows; ask to restart."""
    main_win.settings["tun_mode"] = False
    main_win.settings["killswitch"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "win32")
    monkeypatch.setattr("sys.platform", "win32")

    elevate_called = []
    questions = []
    toggled = []

    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr("utils.platform_utils.is_admin", lambda: False)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_called.append(True))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda parent, title, text, buttons, default: questions.append((title, text)) or QMessageBox.Yes)
    monkeypatch.setattr(
        main_win.connection_manager, "toggle",
        lambda srv, connect: toggled.append((srv, connect)))

    main_win.toggle_connection(True)

    assert len(questions) == 1
    from utils.i18n import tr
    assert tr("dlg.admin_ks_text") in questions[0][1]
    assert len(elevate_called) == 1
    assert toggled == []


def test_killswitch_and_tun_are_refused_on_windows(main_win, monkeypatch):
    """Firewall policy would block the traffic TUN routes; refuse the combo."""
    main_win.settings["tun_mode"] = True
    main_win.settings["killswitch"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "win32")
    monkeypatch.setattr("sys.platform", "win32")

    warnings = []
    toggled = []

    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text: warnings.append((title, text)))
    monkeypatch.setattr(
        main_win.connection_manager, "toggle",
        lambda srv, connect: toggled.append((srv, connect)))

    main_win.toggle_connection(True)

    assert len(warnings) == 1
    from utils.i18n import tr
    assert tr("dlg.ks_tun_text") in warnings[0][1]
    assert toggled == []


def test_killswitch_linux_requires_root(main_win, monkeypatch):
    """nftables needs root; do not connect while claiming protection is on."""
    main_win.settings["tun_mode"] = False
    main_win.settings["killswitch"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "linux")
    monkeypatch.setattr("sys.platform", "linux")

    warnings = []
    toggled = []

    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    monkeypatch.setattr("utils.platform_utils.is_admin", lambda: False)
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text: warnings.append((title, text)))
    monkeypatch.setattr(
        main_win.connection_manager, "toggle",
        lambda srv, connect: toggled.append((srv, connect)))

    main_win.toggle_connection(True)

    assert len(warnings) == 1
    from utils.i18n import tr
    assert tr("dlg.admin_ks_linux_text") in warnings[0][1]
    assert toggled == []


def test_connecting_animation_and_status(main_win):
    """Hero power button should spin during connecting and settle once connected."""
    screen = main_win.screen_main
    btn = screen.hero_btn

    assert btn.is_connecting() is False
    assert btn.is_connected() is False

    # 1. Connecting state
    screen.set_status("Подключение...", False, is_connecting=True)
    assert btn.is_connecting() is True
    assert btn.is_connected() is False
    assert btn._spin_timer.isActive() is True

    # 2. Connected state
    screen.set_status("Подключено", True, is_connecting=False)
    assert btn.is_connecting() is False
    assert btn.is_connected() is True
    assert btn._spin_timer.isActive() is False

    # 3. Disconnected state
    screen.set_status("Отключено", False)
    assert btn.is_connecting() is False
    assert btn.is_connected() is False


def test_opening_search_keeps_main_page_scroll(main_win, qapp):
    """Opening search must not jump the main page down to the server list."""
    main_win.resize(480, 360)
    main_win.show()
    qapp.processEvents()
    screen = main_win.screen_main
    bar = screen.scroll.verticalScrollBar()
    assert bar.maximum() > 0  # content overflows, so a jump would be observable

    bar.setValue(0)
    main_win.taskbar.set_search_open(True, animated=False)
    qapp.processEvents()
    assert bar.value() == 0


def test_show_add_dialog_adds_server(main_win, monkeypatch):
    """Adding server via dialog imports and adds the server to server list."""
    from PySide6.QtWidgets import QDialog
    from ui.add_server_dialog import AddServerDialog

    class DummyDialog:
        def __init__(self, *args, **kwargs):
            pass
        def exec(self):
            return QDialog.Accepted
        def get_server_key(self):
            return "ss://YWVzLTI1Ni1nY206cGFzc3dvcmRAMS4yLjMuNDo4Mzg4#ImportedTest"

    monkeypatch.setattr("ui.main_window.AddServerDialog", DummyDialog)
    main_win.show_add_dialog()
    # Verified no AttributeError: 'RoundedWindow' object has no attribute 'tab_bar'
    srvs = main_win._current_servers()
    assert any(s.name == "ImportedTest" for s in srvs)


def test_toggle_job_runs_manager_toggle():
    """_ToggleJob forwards (server, connect) to the manager, swallowing errors."""
    from ui.main_window import _ToggleJob
    calls = []
    job = _ToggleJob(SimpleNamespace(toggle=lambda s, c: calls.append((s, c))), "srv", True)
    job.run()
    assert calls == [("srv", True)]


def test_toggle_connection_dispatches_via_threadpool(main_win, monkeypatch):
    """No naked threading.Thread from the button: dispatch goes to QThreadPool."""
    import ui.main_window as mw
    from ui.main_window import _ToggleJob
    started = []

    class FakePool:
        def start(self, job):
            started.append(job)
            job.run()

    monkeypatch.setattr(mw.QThreadPool, "globalInstance", lambda: FakePool())
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)
    toggled = []
    monkeypatch.setattr(main_win.connection_manager, "toggle",
                        lambda srv, connect: toggled.append((srv, connect)))
    main_win.toggle_connection(True)
    assert len(started) == 1
    assert isinstance(started[0], _ToggleJob)
    assert len(toggled) == 1
    assert toggled[0][1] is True
    main_win.toggle_connection(False)
    assert len(toggled) == 2
    assert toggled[1] == (None, False)


def test_main_window_module_uses_no_threading():
    """Regression guard: threading.Thread must not creep back into UI slots."""
    import ui.main_window as mw
    assert not hasattr(mw, "threading")
