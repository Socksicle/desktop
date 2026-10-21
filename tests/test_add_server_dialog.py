"""Unit tests for AddServerDialog in-app modal overlay."""

import pytest
from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QWidget, QFrame

from ui.add_server_dialog import AddServerDialog
from utils.theme import M3Theme


@pytest.fixture
def theme():
    return M3Theme(preset_key="dynamic")


def test_add_server_dialog_in_app_parenting(qapp, theme):
    class MockMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.resize(400, 700)
            self.container = QFrame(self)
            self.container.setGeometry(0, 0, 400, 700)

    win = MockMainWindow()
    win.show()

    dlg = AddServerDialog(win, theme)
    # Must be parented to container, NOT an OS-level top-level window
    assert dlg.parent() == win.container
    assert not (dlg.windowFlags() & Qt.Window)
    assert not (dlg.windowFlags() & Qt.Dialog)
    assert dlg.geometry().width() == 400
    assert dlg.geometry().height() == 700


def test_add_server_dialog_protocol_hints(qapp, theme):
    dlg = AddServerDialog(theme=theme)
    assert dlg.hint_label.text() == ""

    dlg.input_field.setText("vless://user@1.1.1.1:443")
    assert "VLESS" in dlg.hint_label.text()

    dlg.input_field.setText("hysteria2://pw@1.1.1.1:443")
    assert "Hysteria 2" in dlg.hint_label.text()

    dlg.input_field.setText("https://sub.link/vpn")
    assert "Subscription" in dlg.hint_label.text()

    dlg.input_field.setText("unknown://custom")
    assert dlg.hint_label.text() == ""


def test_add_server_dialog_exec_accept_and_key(qapp, theme):
    parent = QWidget()
    parent.resize(400, 600)
    dlg = AddServerDialog(parent, theme)
    dlg.input_field.setText("ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpwYXNzd29yZA@1.2.3.4:8388")

    QTimer.singleShot(20, lambda: dlg.accept())
    res = dlg.exec()
    assert res == AddServerDialog.Accepted
    assert dlg.get_server_key().startswith("ss://")


def test_add_server_dialog_exec_reject_escape(qapp, theme):
    parent = QWidget()
    parent.resize(400, 600)
    dlg = AddServerDialog(parent, theme)

    def press_esc():
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        dlg.keyPressEvent(ev)

    QTimer.singleShot(20, press_esc)
    res = dlg.exec()
    assert res == AddServerDialog.Rejected


def test_add_server_dialog_click_outside_rejects(qapp, theme):
    parent = QWidget()
    parent.resize(400, 600)
    dlg = AddServerDialog(parent, theme)
    dlg.setGeometry(0, 0, 400, 600)

    # Position clearly outside the centered card (e.g. top-left corner at 10, 10)
    def click_scrim():
        pos = QPointF(10, 10)
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        dlg.mousePressEvent(ev)

    QTimer.singleShot(20, click_scrim)
    res = dlg.exec()
    assert res == AddServerDialog.Rejected


def test_add_server_dialog_parent_resize_synchronization(qapp, theme):
    parent = QWidget()
    parent.resize(400, 600)
    parent.show()
    dlg = AddServerDialog(parent, theme)

    parent.resize(480, 800)
    qapp.processEvents()
    assert dlg.geometry().width() == 480
    assert dlg.geometry().height() == 800


def test_add_server_dialog_entrance_animation(qapp, theme):
    parent = QWidget()
    parent.resize(400, 600)
    dlg = AddServerDialog(parent, theme)

    dlg.set_dialog_progress(0.0)
    assert dlg.get_dialog_progress() == 0.0
    assert dlg.card_opacity_effect.opacity() == 0.0

    dlg.set_dialog_progress(0.5)
    assert dlg.get_dialog_progress() == 0.5
    assert dlg.card_opacity_effect.opacity() == 0.5

    dlg.set_dialog_progress(1.0)
    assert dlg.get_dialog_progress() == 1.0
    assert dlg.card_opacity_effect.opacity() == 1.0
    assert dlg.main_layout.contentsMargins().top() == 20

