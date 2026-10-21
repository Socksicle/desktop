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


def test_message_box_init(qapp, theme):
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
    assert dlg.button(MessageBox.No) is None


def test_message_box_informative_text(qapp, theme):
    dlg = MessageBox(title="Error", text="Failed to connect", theme=theme)
    assert dlg.txt_informative.isHidden()
    dlg.setInformativeText("Error code: 500\nDetails: Connection refused")
    assert not dlg.txt_informative.isHidden()
    assert "Connection refused" in dlg.txt_informative.toPlainText()
    dlg.setInformativeText("")
    assert dlg.txt_informative.isHidden()


def test_message_box_icons(qapp, theme):
    dlg = MessageBox(theme=theme)
    assert dlg.badge.isHidden()

    for icon in (MessageBox.Information, MessageBox.Warning, MessageBox.Critical,
                 MessageBox.Question, MessageBox.Success):
        dlg.setIcon(icon)
        assert not dlg.badge.isHidden()
        assert dlg.badge._symbol != ""
        assert dlg.badge._pixmap is not None
        assert not dlg.badge._pixmap.isNull()

    # Verify uniform non-black badge background (warning matches info)
    dlg.setIcon(MessageBox.Information)
    info_bg = dlg.badge._bg_color.name()
    dlg.setIcon(MessageBox.Warning)
    warn_bg = dlg.badge._bg_color.name()
    assert info_bg == warn_bg
    assert warn_bg != "#000000"

    dlg.setIcon(MessageBox.NoIcon)
    assert dlg.badge.isHidden()

    pm = QPixmap(24, 24)
    dlg.setIconPixmap(pm)
    assert not dlg.badge.isHidden()
    assert dlg.badge._pixmap is not None


def test_message_box_standard_buttons(qapp, theme):
    dlg = MessageBox(theme=theme)
    dlg.setStandardButtons(MessageBox.Yes | MessageBox.No)
    btn_yes = dlg.button(MessageBox.Yes)
    btn_no = dlg.button(MessageBox.No)
    assert btn_yes is not None
    assert btn_no is not None
    assert dlg.standardButton(btn_yes) == MessageBox.Yes
    assert dlg.standardButton(btn_no) == MessageBox.No
    assert dlg.buttonRole(btn_yes) == ButtonRole.YesRole
    assert dlg.buttonRole(btn_no) == ButtonRole.NoRole


def test_message_box_custom_buttons(qapp, theme):
    dlg = MessageBox(theme=theme)
    custom_btn = dlg.addButton("Custom Action", ButtonRole.ActionRole)
    assert custom_btn in dlg._buttons
    assert dlg.buttonRole(custom_btn) == ButtonRole.ActionRole
    assert dlg.standardButton(custom_btn) == MessageBox.NoButton

    dlg.removeButton(custom_btn)
    assert custom_btn not in dlg._buttons
    assert custom_btn.parent() is None


def test_message_box_default_button(qapp, theme):
    dlg = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    ok_btn = dlg.button(MessageBox.Ok)
    dlg.setDefaultButton(MessageBox.Ok)
    assert dlg._default_button == ok_btn

    cancel_btn = dlg.button(MessageBox.Cancel)
    dlg.setDefaultButton(cancel_btn)
    assert dlg._default_button == cancel_btn


def test_message_box_click_and_exec(qapp, theme, monkeypatch):
    dlg = MessageBox(buttons=MessageBox.Yes | MessageBox.No, theme=theme)
    yes_btn = dlg.button(MessageBox.Yes)

    monkeypatch.setattr(dlg, "exec", lambda: dlg._on_button_clicked(yes_btn) or int(MessageBox.Yes))
    res = dlg.exec()
    assert res == int(MessageBox.Yes)
    assert dlg.clickedButton() == yes_btn


def test_message_box_close_x_triggers_cancel(qapp, theme):
    dlg = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    dlg.btn_close_x.click()
    assert dlg.clickedButton() == dlg.button(MessageBox.Cancel)
    assert dlg._result_button == MessageBox.Cancel


def test_message_box_escape_key(qapp, theme):
    dlg = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    esc_event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    dlg.keyPressEvent(esc_event)
    assert dlg._result_button == MessageBox.Cancel


def test_message_box_enter_key_clicks_default(qapp, theme):
    dlg = MessageBox(buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    dlg.setDefaultButton(MessageBox.Ok)
    enter_event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    dlg.keyPressEvent(enter_event)
    assert dlg.clickedButton() == dlg.button(MessageBox.Ok)
    assert dlg._result_button == MessageBox.Ok


def test_message_box_mouse_dragging(qapp, theme):
    dlg = MessageBox(theme=theme)
    dlg.resize(420, 200)
    dlg.show()

    press = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(50, 20),
                        QPointF(50, 20), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    dlg.mousePressEvent(press)
    assert dlg._dragging is True
    assert dlg._drag_pos is not None

    move = QMouseEvent(QMouseEvent.MouseMove, QPointF(60, 30),
                       QPointF(60, 30), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    dlg.mouseMoveEvent(move)

    release = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(60, 30),
                          QPointF(60, 30), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    dlg.mouseReleaseEvent(release)
    assert dlg._dragging is False


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


def test_message_box_overlay_mode(qapp, theme):
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QTimer

    parent = QWidget()
    parent.resize(440, 800)
    parent.container = QWidget(parent)
    parent.container.setGeometry(0, 0, 440, 800)

    dlg = MessageBox(parent=parent, title="Overlay Alert", text="Testing overlay mode",
                     buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    assert dlg._is_overlay is True
    assert dlg.parent() == parent.container
    assert dlg.geometry() == parent.container.rect()

    ok_btn = dlg.button(MessageBox.Ok)
    QTimer.singleShot(20, lambda: ok_btn.click())
    res = dlg.exec()
    assert res == int(MessageBox.Ok)


def test_message_box_overlay_click_outside_rejects(qapp, theme):
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    parent = QWidget()
    parent.resize(440, 800)
    parent.container = QWidget(parent)
    parent.container.setGeometry(0, 0, 440, 800)

    dlg = MessageBox(parent=parent, title="Overlay Outside", text="Click backdrop",
                     buttons=MessageBox.Ok | MessageBox.Cancel, theme=theme)
    assert dlg._is_overlay is True
    parent.show()
    dlg.show()
    qapp.processEvents()

    # Card is centered vertically, (10, 10) is on the top backdrop
    press = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(10, 10),
                        QPointF(10, 10), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    dlg.mousePressEvent(press)
    assert dlg._result_button == MessageBox.Cancel

