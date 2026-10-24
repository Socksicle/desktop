import re
from pathlib import Path
from PySide6.QtGui import QGuiApplication, QImage, QWheelEvent
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from utils.theme import M3Theme
from ui.screen_about import (
    ScreenAbout, CONTRIBUTORS,
    get_real_app_version,
    get_real_core_engine, get_real_os_version, get_real_hwid
)
from utils.platform_utils import get_real_device_name
from ui.blob_avatar_view import BlobAvatarView
from ui.legal_dialog import LegalDialog, load_legal_text
from ui.m3_scroll_area import M3ScrollArea, get_m3_scrollbar_stylesheet
from ui.screen_settings import SettingsScreen
from ui.message_box import MessageBox
from utils.icons import pixmap
from utils.i18n import set_language


def test_system_info_and_device_name_retrieval(monkeypatch):
    # Device name uses hostname only
    monkeypatch.setattr("utils.platform_utils.is_windows", lambda: False)
    monkeypatch.setattr("utils.platform_utils.socket.gethostname", lambda: "test-host")
    assert get_real_device_name() == "test-host"

    # Real data retrieval
    app_ver = get_real_app_version()
    toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if toml_path.is_file():
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', toml_path.read_text(encoding="utf-8"))
        if m:
            assert app_ver == m.group(1)
    assert bool(re.match(r"^\d+\.\d+(\.\d+)?", app_ver))
    assert "commit" not in app_ver
    assert len(get_real_device_name()) > 0
    assert "sing-box" in get_real_core_engine("sing-box")
    assert len(get_real_os_version()) > 0
    assert len(get_real_hwid()) > 0


def test_screen_about_full_flow(qapp):
    theme = M3Theme(preset_key="lavender")
    settings = {"engine": "sing-box", "hwid_value": "ABCDEF1234567890"}
    screen = ScreenAbout(theme, settings=settings)

    # 1. Structure check
    assert isinstance(screen.avatar, BlobAvatarView)
    assert screen.title_label.text() == "Socksicle"
    assert screen.version_label.text() == get_real_app_version()
    assert screen.val_device_name.text() == get_real_device_name()
    assert "sing-box" in screen.val_core_engine.text()
    assert "twscore" in screen.val_twscore.text()
    assert screen.edit_hwid.isReadOnly() and screen.edit_hwid.text() == "ABCDEF1234567890"
    assert screen.card_links is not None and screen.card_legal is not None
    assert len(screen._contrib_avatars) == len(CONTRIBUTORS)

    # 2. Copy HWID
    screen._copy_hwid()
    assert QGuiApplication.clipboard().text() == screen.hwid_value
    assert "Скопировано" in screen.btn_copy_hwid.text()

    # 3. Blur toggle on click & restore on hide
    screen.show()
    assert screen.edit_hwid.is_blurred()
    QTest.mouseClick(screen.edit_hwid, Qt.LeftButton)
    assert not screen.edit_hwid.is_blurred()
    QTest.mouseClick(screen.edit_hwid, Qt.LeftButton)
    assert screen.edit_hwid.is_blurred()
    QTest.mouseClick(screen.edit_hwid, Qt.LeftButton)
    assert not screen.edit_hwid.is_blurred()
    screen.hide()
    assert screen.edit_hwid.is_blurred()

    # 4. Dev unlock (5 clicks on card)
    unlocked = False
    screen.devUnlocked.connect(lambda: nonlocal_set())
    def nonlocal_set():
        nonlocal unlocked
        unlocked = True

    for _ in range(5):
        screen._on_hwid_card_clicked(None)
    assert unlocked is True


def test_blob_avatar_features(qapp):
    theme_a = M3Theme(preset_key="emerald")
    blob = BlobAvatarView(theme=theme_a)
    initial_shape = blob._shape
    new_shape = blob.shuffle()
    assert new_shape != initial_shape and 0 <= new_shape < BlobAvatarView.SHAPE_COUNT

    # Tinted glyph & rendering
    blob.resize(112, 112)
    pm = pixmap("launcher_foreground", blob._color_on_accent.name(), 67)
    assert pm is not None and not pm.isNull()

    img = QImage(112, 112, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    blob.render(img)
    assert not img.isNull()

    # Theme update
    theme_b = M3Theme(preset_key="coral")
    blob.apply_theme(theme_b)
    assert blob._color_on_accent.name().lower() == theme_b.on_primary.lower()
    assert blob._color_accent.name().lower() == theme_b.primary.lower()


def test_legal_dialog_and_scroll_direction(qapp):
    # 1. Legal dialogs
    set_language("ru")
    theme = M3Theme(preset_key="lavender")
    for doc in ("licenses", "privacy", "terms"):
        text = load_legal_text(doc, lang="ru")
        assert len(text) > 0 and text != "Документ не найден."
        dlg = LegalDialog(title=f"Тест {doc}", doc_name=doc, theme=theme)
        assert isinstance(dlg, MessageBox) and dlg.txt_informative.toPlainText() == text
        dlg.close()

    # 2. M3 scroll area stylesheet and scroll directions
    qss = get_m3_scrollbar_stylesheet()
    assert "width: 5px" in qss and "border-radius: 2px" in qss

    scroll = M3ScrollArea()
    assert scroll._invert_scroll is False
    content = QWidget()
    content.setFixedSize(200, 1000)
    scroll.setWidget(content)
    scroll.resize(200, 400)
    scroll.show()

    bar = scroll.verticalScrollBar()
    # Normal direction: wheel down (delta -120) scrolls down (> 0)
    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    scroll.wheelEvent(ev)
    QTest.qWait(300)
    assert bar.value() > 0

    # Inverted direction: wheel up (delta +120) scrolls down (> 0)
    bar.setValue(0)
    scroll.set_invert_scroll(True)
    ev_up = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    scroll.wheelEvent(ev_up)
    QTest.qWait(300)
    assert bar.value() > 0

    # 3. Settings screen invert scroll toggle
    screen = SettingsScreen({"theme_preset": "lavender"}, theme)
    screen.open_section("appearance", "Тема и интерфейс")
    assert screen.sw_invert_scroll.isChecked() is False
    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    screen.sw_invert_scroll.toggled.emit(True)
    assert ("invert_scroll", True) in emitted and screen.scroll_area._invert_scroll is True
