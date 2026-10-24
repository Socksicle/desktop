"""Unit tests for ui.message_box (Material Design 3 MessageBox)."""

from unittest import mock
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QMouseEvent, QKeyEvent, QPixmap
from PySide6.QtWidgets import QPushButton

from ui.message_box import MessageBox, StandardButton, ButtonRole, Icon
from utils.theme import M3Theme
from utils.i18n import set_language


@pytest.fixture
def theme():
    return M3Theme(preset_key="lavender")


def test_message_box_content_and_icons(qapp, theme):
    dlg = MessageBox(title="Test Title", text="Hello world",
                     icon=MessageBox.Information,
                     buttons=MessageBox.Ok | MessageBox.Cancel,
                     theme=theme)
    assert dlg.windowTitle() == "Test Title"
    assert dlg.lbl_title.text() == "Test Title"
    assert dlg.lbl_text.text() == "Hello world"
    assert not dlg.badge.isHidden()
    assert dlg.button(MessageBox.Ok) is not None
    assert dlg.button(MessageBox.Cancel) is not None

    # Informative text
    dlg.setInformativeText("Details: Connection refused")
    assert not dlg.txt_informative.isHidden()
    assert "Connection refused" in dlg.txt_informative.toPlainText()
    dlg.setInformativeText("")
    assert dlg.txt_informative.isHidden()

    # Icon cycling
    for icon in (MessageBox.Information, MessageBox.Warning, MessageBox.Critical,
                 MessageBox.Question, MessageBox.Success):
        dlg.setIcon(icon)
        assert not dlg.badge.isHidden()
        assert dlg.badge._pixmap is not None and not dlg.badge._pixmap.isNull()

    dlg.setIcon(MessageBox.NoIcon)
    assert dlg.badge.isHidden()


def test_message_box_buttons(qapp, theme):
    dlg = MessageBox(theme=theme)
    dlg.setStandardButtons(MessageBox.Yes | MessageBox.No)
    btn_yes = dlg.button(MessageBox.Yes)
    btn_no = dlg.button(MessageBox.No)
    assert dlg.standardButton(btn_yes) == MessageBox.Yes
    assert dlg.buttonRole(btn_yes) == ButtonRole.YesRole

    # Custom button
    custom_btn = dlg.addButton("Custom Action", ButtonRole.ActionRole)
    assert custom_btn in dlg._buttons
    assert dlg.buttonRole(custom_btn) == ButtonRole.ActionRole
    dlg.removeButton(custom_btn)
    assert custom_btn not in dlg._buttons

    # Default button
    dlg.setDefaultButton(MessageBox.Yes)
    assert dlg._default_button == btn_yes


def test_message_box_interactions(qapp, theme, monkeypatch):
    # Click and exec
    dlg = MessageBox(buttons=MessageBox.Yes | MessageBox.No, theme=theme)
    yes_btn = dlg.button(MessageBox.Yes)
    monkeypatch.setattr(dlg, "exec", lambda: dlg._on_button_clicked(yes_btn) or int(MessageBox.Yes))
    assert dlg.exec() == int(MessageBox.Yes)
    assert dlg.clickedButton() == yes_btn

    # Close X triggers cancel
    dlg2 = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    dlg2.btn_close_x.click()
    assert dlg2._result_button == MessageBox.Cancel

    # Escape triggers cancel
    dlg3 = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    dlg3.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert dlg3._result_button == MessageBox.Cancel

    # Enter clicks default
    dlg4 = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    dlg4.setDefaultButton(MessageBox.Ok)
    dlg4.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert dlg4._result_button == MessageBox.Ok


def test_message_box_static_methods(qapp, theme, monkeypatch):
    monkeypatch.setattr(MessageBox, "exec", lambda self: int(MessageBox.Ok))
    assert MessageBox.information(None, "Info", "Info text", theme=theme) == MessageBox.Ok
    assert MessageBox.warning(None, "Warning", "Warn text", theme=theme) == MessageBox.Ok
    assert MessageBox.critical(None, "Critical", "Crit text", theme=theme) == MessageBox.Ok
    assert MessageBox.error(None, "Error", "Err text", theme=theme) == MessageBox.Ok
    assert MessageBox.success(None, "Success", "Yay", theme=theme) == MessageBox.Ok

    monkeypatch.setattr(MessageBox, "exec", lambda self: int(MessageBox.Yes))
    assert MessageBox.question(None, "Question", "Proceed?", theme=theme) == MessageBox.Yes


def test_message_box_i18n_buttons(qapp, theme):
    set_language("en")
    dlg_en = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    assert dlg_en.button(MessageBox.Ok).text() == "OK"
    assert dlg_en.button(MessageBox.Cancel).text() == "Cancel"

    set_language("ru")
    dlg_ru = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    assert dlg_ru.button(MessageBox.Ok).text() == "ОК"
    assert dlg_ru.button(MessageBox.Cancel).text() == "Отмена"


def test_message_box_apply_theme_presets(qapp):
    theme_lavender = M3Theme(preset_key="lavender")
    theme_amoled = M3Theme(preset_key="amoled")

    dlg = MessageBox(title="Themed", text="Content", buttons=MessageBox.Discard, theme=theme_lavender)
    discard_btn = dlg.button(MessageBox.Discard)
    assert dlg.theme == theme_lavender

    # Switching to amoled updates styles without throwing
    dlg.apply_theme(theme_amoled)
    assert dlg.theme == theme_amoled
    assert discard_btn.styleSheet() != ""


def test_message_box_dynamic_width_enlargement(qapp):
    """Verify that a long title enlarges the dialog width and wraps without clipping."""
    short_dlg = MessageBox(None, title="Short", text="Hello")
    short_w = short_dlg.width()
    assert short_w >= 400

    long_title = "Administrator Privileges Required For Advanced System Network Setup"
    long_dlg = MessageBox(None, icon=MessageBox.Icon.Question, title=long_title, text="Some description")
    assert long_dlg.lbl_title.wordWrap() is True
    assert long_dlg.width() > short_w
    assert long_dlg.width() >= 460


def test_message_box_overlay(qapp, theme):
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QTimer, QPointF
    from PySide6.QtGui import QMouseEvent

    parent = QWidget()
    parent.resize(440, 800)
    parent.container = QWidget(parent)
    parent.container.setGeometry(0, 0, 440, 800)

    # 1. Overlay mode geometry and exec
    dlg = MessageBox(parent=parent, title="Overlay Alert", text="Testing overlay mode",
                     buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    assert dlg._is_overlay is True
    assert dlg.parent() == parent.container
    assert dlg.geometry() == parent.container.rect()

    ok_btn = dlg.button(MessageBox.Ok)
    QTimer.singleShot(20, lambda: ok_btn.click())
    assert dlg.exec() == int(MessageBox.Ok)

    # 2. Outside click on backdrop rejects dialog
    dlg2 = MessageBox(parent=parent, title="Overlay Outside", text="Click backdrop",
                      buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    parent.show()
    dlg2.show()
    qapp.processEvents()

    press = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(10, 10),
                        QPointF(10, 10), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    dlg2.mousePressEvent(press)
    assert dlg2._result_button == MessageBox.Cancel

