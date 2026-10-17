"""Header bar composition: search + add + close only (no overflow menu,
no minimize button) and tray-conditional close behavior."""
import pytest
from PySide6.QtGui import QCloseEvent

from ui.header_bar import HeaderBar
from ui.main_window import RoundedWindow
from utils.theme import M3Theme
import utils.twinsock as tw


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


@pytest.fixture
def main_win(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    return RoundedWindow()


def test_header_has_title_and_close_only():
    bar = HeaderBar(M3Theme(preset_key="lavender"))
    bar.show()

    assert bar.close_btn.isVisible() is True
    assert bar.title_label.text() == "Socksicle"

    # Search and Add have moved down to the floating navigation toolbar
    assert hasattr(bar, "btn_search") is False
    assert hasattr(bar, "btn_add") is False
    assert hasattr(bar, "btn_more") is False
    assert hasattr(bar, "min_btn") is False


def test_header_buttons_emit(qapp):
    bar = HeaderBar(M3Theme(preset_key="lavender"))
    bar.show()
    qapp.processEvents()

    fired = []
    bar.closeRequested.connect(lambda: fired.append("close"))

    bar.close_btn.click()
    assert fired == ["close"]


def test_header_title_updates():
    bar = HeaderBar(M3Theme(preset_key="lavender"))
    bar.show()

    bar.set_title("Settings")
    assert bar.title_label.text() == "Settings"
    assert bar.close_btn.isVisible() is True


def test_close_hides_to_tray_when_enabled(main_win, monkeypatch):
    win = main_win
    win.settings["minimize_to_tray"] = True
    monkeypatch.setattr(win.tray_manager, "tray_available", True)
    hidden = []
    notified = []
    monkeypatch.setattr(win, "hide", lambda: hidden.append(True))
    monkeypatch.setattr(win.tray_manager, "notify",
                        lambda *a: notified.append(a))

    ev = QCloseEvent()
    win.closeEvent(ev)

    assert ev.isAccepted() is False
    assert hidden == [True]
    assert len(notified) == 1


def test_close_quits_when_tray_disabled(main_win, monkeypatch):
    win = main_win
    win.settings["minimize_to_tray"] = False
    quit_called = []
    monkeypatch.setattr(win, "quit_app", lambda: quit_called.append(True))

    ev = QCloseEvent()
    win.closeEvent(ev)

    assert quit_called == [True]
