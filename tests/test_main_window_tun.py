"""Tests for MainWindow TUN mode elevation and Linux capabilities handling."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QMessageBox

from ui.main_window import RoundedWindow
from utils.engines.base import EngineType
from utils.server_model import Server, ProxyProtocol
import utils.twinsock as tw




def test_tun_linux_grant_capabilities_success_and_failure(main_win, monkeypatch):
    """On Linux, missing privileges trigger grant_tun_capabilities; abort on failure without GUI restart."""
    main_win.settings["tun_mode"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "linux")
    monkeypatch.setattr("sys.platform", "linux")

    # 1. Success path
    elevate_called = []
    grant_called = []
    with mock.patch.object(main_win.connection_manager.engine, "find_binary", return_value=Path("/fake/sing-box")), \
         mock.patch("ui.main_window.RoundedWindow._ensure_backend", lambda self: True), \
         mock.patch("utils.platform_utils.elevate_restart", lambda: elevate_called.append(True)), \
         mock.patch("utils.platform_utils.grant_tun_capabilities", lambda p, parent_window=None: grant_called.append(p) or True), \
         mock.patch.object(main_win.connection_manager, "toggle", lambda srv, connect: True):
        main_win.toggle_connection(True)

    assert len(elevate_called) == 0
    assert len(grant_called) == 1

    # 2. Failure path (declining polkit prompt shows warning and aborts)
    elevate_called2 = []
    grant_called2 = []
    warnings = []
    with mock.patch.object(main_win.connection_manager.engine, "find_binary", return_value=Path("/fake/sing-box")), \
         mock.patch("ui.main_window.RoundedWindow._ensure_backend", lambda self: True), \
         mock.patch("utils.platform_utils.elevate_restart", lambda: elevate_called2.append(True)), \
         mock.patch("utils.platform_utils.grant_tun_capabilities", lambda p, parent_window=None: grant_called2.append(p) or False), \
         mock.patch.object(QMessageBox, "warning", lambda parent, title, text: warnings.append((title, text))):
        main_win.toggle_connection(True)

    assert len(elevate_called2) == 0
    assert len(grant_called2) == 1
    assert len(warnings) == 1
    from utils.i18n import tr
    assert tr("dlg.tun_title") in warnings[0][0]


def test_platform_elevation_and_killswitch_rules(main_win, monkeypatch):
    from utils.i18n import tr
    monkeypatch.setattr("ui.main_window.RoundedWindow._ensure_backend", lambda self: True)

    # 1. Windows TUN asks for UAC elevation if not admin
    main_win.settings["tun_mode"] = True
    main_win.settings["killswitch"] = False
    monkeypatch.setattr("utils.platform_utils.sys.platform", "win32")
    monkeypatch.setattr("sys.platform", "win32")
    elevate_win = []
    questions_win = []
    monkeypatch.setattr("utils.platform_utils.is_admin", lambda: False)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_win.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda p, t, txt, b, d: questions_win.append((t, txt)) or QMessageBox.Yes)

    main_win.toggle_connection(True)
    assert len(questions_win) == 1
    assert tr("dlg.admin_title") in questions_win[0][0]
    assert len(elevate_win) == 1

    # 2. Windows Kill Switch asks for UAC elevation
    main_win.settings["tun_mode"] = False
    main_win.settings["killswitch"] = True
    questions_ks = []
    elevate_ks = []
    toggled = []
    monkeypatch.setattr(QMessageBox, "question", lambda p, t, txt, b, d: questions_ks.append((t, txt)) or QMessageBox.Yes)
    monkeypatch.setattr("utils.platform_utils.elevate_restart", lambda: elevate_ks.append(True))
    monkeypatch.setattr(main_win.connection_manager, "toggle", lambda srv, connect: toggled.append((srv, connect)))

    main_win.toggle_connection(True)
    assert len(questions_ks) == 1
    assert tr("dlg.admin_ks_text") in questions_ks[0][1]
    assert len(elevate_ks) == 1
    assert toggled == []

    # 3. Windows refuses Killswitch + TUN combo
    main_win.settings["tun_mode"] = True
    main_win.settings["killswitch"] = True
    warnings_combo = []
    monkeypatch.setattr(QMessageBox, "warning", lambda p, t, txt: warnings_combo.append((t, txt)))

    main_win.toggle_connection(True)
    assert len(warnings_combo) == 1
    assert tr("dlg.ks_tun_text") in warnings_combo[0][1]
    assert toggled == []

    # 4. Linux Kill Switch requires root
    main_win.settings["tun_mode"] = False
    main_win.settings["killswitch"] = True
    monkeypatch.setattr("utils.platform_utils.sys.platform", "linux")
    monkeypatch.setattr("sys.platform", "linux")
    warnings_linux = []
    monkeypatch.setattr(QMessageBox, "warning", lambda p, t, txt: warnings_linux.append((t, txt)))

    main_win.toggle_connection(True)
    assert len(warnings_linux) == 1
    assert tr("dlg.admin_ks_linux_text") in warnings_linux[0][1]
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


def test_connection_dispatch_and_threading_contract(main_win, monkeypatch):
    import ui.main_window as mw
    from ui.main_window import _ToggleJob

    # Module threading guard
    assert not hasattr(mw, "threading")

    # _ToggleJob runs manager toggle
    calls = []
    job = _ToggleJob(SimpleNamespace(toggle=lambda s, c: calls.append((s, c))), "srv", True)
    job.run()
    assert calls == [("srv", True)]

    # Dispatches via QThreadPool
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


def test_engine_setting_selection_and_transitions(main_win, monkeypatch):
    switched = []
    monkeypatch.setattr(main_win.connection_manager, "switch_engine",
                        lambda engine: switched.append(engine))

    # Setting engine 'auto' switches to sing-box
    main_win._on_setting_value_changed("engine", "auto")
    assert len(switched) == 1
    assert switched[0].engine_type == EngineType.SINGBOX

    # Setting engine 'xray' switches to xray
    switched.clear()
    main_win._on_setting_value_changed("engine", "xray")
    assert len(switched) >= 1
    assert all(e.engine_type == EngineType.XRAY for e in switched)

    # Disabling TUN with auto engine does not crash
    main_win.settings["engine"] = "auto"
    switched.clear()
    main_win._on_setting_value_changed("tun_mode", False)
    assert len(switched) == 1
    assert switched[0].engine_type == EngineType.SINGBOX


def _click_event(widget):
    """Synthetic left-button press at the widget center for click handlers."""
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    return QMouseEvent(QEvent.MouseButtonPress, pos, pos,
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_header_tun_badge_lifecycle_and_sync(main_win, monkeypatch, tmp_path):
    switched = []
    real_switch = main_win.connection_manager.switch_engine
    def spy_switch(engine):
        switched.append(engine)
        return real_switch(engine)
    monkeypatch.setattr(main_win.connection_manager, "switch_engine", spy_switch)
    bar = main_win.header_bar.tun_bar

    # 1. Tab visibility
    main_win._on_taskbar_tab_changed(0)
    assert not bar.isHidden()
    main_win._on_taskbar_tab_changed(1)
    assert bar.isHidden()
    main_win._on_taskbar_tab_changed(2)
    assert bar.isHidden()
    main_win._on_taskbar_tab_changed(0)
    assert not bar.isHidden()

    # 2. Click toggles setting and switches engine
    main_win.header_bar.set_tun_mode(False, animate=False)
    bar.mousePressEvent(_click_event(bar))
    assert main_win.settings["tun_mode"] is True
    assert len(switched) == 1
    assert bar.is_checked() is True
    main_win.screen_settings._ensure_section("connection")
    assert getattr(main_win.screen_settings, "sw_tun", None) is None
    assert switched[0].engine_type == EngineType.SINGBOX

    # 3. Setting change updates header badge
    main_win.settings["tun_mode"] = False
    main_win._on_setting_value_changed("tun_mode", True)
    assert main_win.header_bar.tun_bar.is_checked() is True
    main_win._on_setting_value_changed("tun_mode", False)
    assert main_win.header_bar.tun_bar.is_checked() is False

    # 4. _on_tun_toggled switches engine
    main_win.settings["tun_mode"] = False
    main_win.settings["engine"] = "xray"
    main_win.header_bar.set_tun_mode(False, animate=False)
    main_win._on_tun_toggled(True)
    assert main_win.connection_manager.engine.engine_type == EngineType.SINGBOX
    main_win._on_tun_toggled(False)
    assert main_win.connection_manager.engine.engine_type == EngineType.XRAY

    # 5. Saved state on start
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    (tmp_path / "settings.json").write_text('{"tun_mode": true}', encoding="utf-8")
    from ui.main_window import RoundedWindow
    win = RoundedWindow()
    assert win.header_bar.tun_bar.is_checked() is True
