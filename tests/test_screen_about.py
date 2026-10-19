from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import Qt

from utils.theme import M3Theme
from ui.screen_about import (
    ScreenAbout, CONTRIBUTORS,
    get_real_app_version, get_real_device_name,
    get_real_core_engine, get_real_os_version, get_real_hwid
)
from ui.blob_avatar_view import BlobAvatarView
from ui.legal_dialog import LegalDialog, load_legal_text


def test_real_data_retrieval_functions():
    app_ver = get_real_app_version()
    assert app_ver == "0.1-rc.1"
    assert "commit" not in app_ver

    dev_name = get_real_device_name()
    assert len(dev_name) > 0

    core_eng = get_real_core_engine("sing-box")
    assert "sing-box" in core_eng

    os_ver = get_real_os_version()
    assert len(os_ver) > 0

    hwid = get_real_hwid()
    assert len(hwid) > 0


def test_screen_about_components(qapp):
    theme = M3Theme(preset_key="lavender")
    settings = {"engine": "sing-box", "hwid_value": "ABCDEF1234567890"}
    screen = ScreenAbout(theme, settings=settings)

    # Hero section
    assert screen.avatar is not None
    assert isinstance(screen.avatar, BlobAvatarView)
    assert screen.title_label.text() == "Socksicle"
    assert screen.version_label.text() == get_real_app_version()

    # Card 1: System info (real detected values)
    assert screen.card_info is not None
    assert screen.val_device_name.text() == get_real_device_name()
    assert "sing-box" in screen.val_core_engine.text()
    assert "twscore" in screen.val_twscore.text()
    assert len(screen.val_os_ver.text()) > 0

    # Card 2: HWID & dev unlock
    assert screen.card_hwid is not None
    assert screen.edit_hwid.isReadOnly()
    assert screen.edit_hwid.text() == "ABCDEF1234567890"
    assert screen.btn_copy_hwid.text() == "Копировать"

    # Card 3: Community
    assert screen.card_links is not None
    assert screen.btn_gh is not None
    assert screen.btn_tg is not None
    assert screen.btn_dc is not None

    # Card 4: Legal documents
    assert screen.card_legal is not None
    assert screen.btn_licenses is not None
    assert screen.btn_privacy is not None
    assert screen.btn_terms is not None

    # Card 5: Contributors
    assert screen.card_contrib is not None
    assert len(screen._contrib_avatars) == len(CONTRIBUTORS)
    assert len(screen._contrib_git_buttons) == len(CONTRIBUTORS)


def test_screen_about_copy_hwid(qapp):
    theme = M3Theme(preset_key="ocean")
    screen = ScreenAbout(theme)
    screen._copy_hwid()
    assert QGuiApplication.clipboard().text() == screen.hwid_value
    assert "Скопировано" in screen.btn_copy_hwid.text()


def test_screen_about_dev_unlock(qapp):
    theme = M3Theme(preset_key="coral")
    screen = ScreenAbout(theme)
    unlocked = False

    def on_unlocked():
        nonlocal unlocked
        unlocked = True

    screen.devUnlocked.connect(on_unlocked)
    for _ in range(5):
        screen._on_hwid_card_clicked(None)

    assert unlocked is True
    assert "DEV UNLOCKED" in screen.edit_hwid.text()


def test_blob_avatar_shuffle(qapp):
    theme = M3Theme(preset_key="emerald")
    blob = BlobAvatarView(theme=theme)
    initial_shape = blob._shape
    new_shape = blob.shuffle()
    assert new_shape != initial_shape
    assert 0 <= new_shape < BlobAvatarView.SHAPE_COUNT


def test_blob_avatar_renders_tinted_glyph(qapp):
    from PySide6.QtGui import QImage
    from utils.icons import pixmap

    theme_a = M3Theme(preset_key="lavender")
    blob = BlobAvatarView(theme=theme_a)
    blob.resize(112, 112)

    # Verify glyph pixmap loads with the theme color
    pm = pixmap("launcher_foreground", blob._color_on_accent.name(), 67)
    assert pm is not None and not pm.isNull()

    # Verify paint event executes cleanly
    img = QImage(112, 112, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    blob.render(img)
    assert not img.isNull()

    # Verify dynamic color update on theme change
    theme_b = M3Theme(preset_key="coral")
    blob.apply_theme(theme_b)
    assert blob._color_on_accent.name().lower() == theme_b.on_primary.lower()
    assert blob._color_accent.name().lower() == theme_b.primary.lower()
    pm_b = pixmap("launcher_foreground", blob._color_on_accent.name(), 67)
    assert pm_b is not None and not pm_b.isNull()


def test_legal_dialog_and_text(qapp):
    from ui.message_box import MessageBox
    from utils.i18n import set_language
    set_language("ru")
    theme = M3Theme(preset_key="lavender")
    for doc in ("licenses", "privacy", "terms"):
        text = load_legal_text(doc, lang="ru")
        assert len(text) > 0
        assert text != "Документ не найден."

        dlg = LegalDialog(title=f"Тест {doc}", doc_name=doc, theme=theme)
        assert isinstance(dlg, MessageBox)
        assert dlg.txt_informative.toPlainText() == text
        assert not dlg.badge.isHidden()
        assert dlg.btn_close_x is not None
        dlg.close()


def test_m3_scroll_area_styling_and_direction(qapp):
    from ui.m3_scroll_area import M3ScrollArea, get_m3_scrollbar_stylesheet
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget

    qss = get_m3_scrollbar_stylesheet()
    assert "width: 5px" in qss
    assert "border-radius: 2px" in qss

    scroll = M3ScrollArea()
    assert scroll._invert_scroll is False
    content = QWidget()
    content.setFixedSize(200, 1000)
    scroll.setWidget(content)
    scroll.resize(200, 400)
    scroll.show()

    bar = scroll.verticalScrollBar()
    initial_val = bar.value()
    assert initial_val == 0

    # By default (invert_scroll=False), wheel down (delta -120) increases value (scrolls down)
    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    scroll.wheelEvent(ev)
    QTest.qWait(300)  # wheel scrolling is animated (140 ms)
    val_normal = bar.value()
    assert val_normal > 0

    # With invert_scroll=True, wheel up (delta +120) increases value (inverted direction)
    bar.setValue(0)
    scroll.set_invert_scroll(True)
    ev_up = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    scroll.wheelEvent(ev_up)
    QTest.qWait(300)
    assert bar.value() > 0


def test_settings_screen_invert_scroll(qapp):
    from ui.screen_settings import SettingsScreen

    theme = M3Theme(preset_key="lavender")
    settings = {"theme_preset": "lavender"}
    screen = SettingsScreen(settings, theme)
    screen.open_section("appearance", "Тема и интерфейс")
    assert hasattr(screen, "sw_invert_scroll")
    assert screen.sw_invert_scroll.isChecked() is False
    assert screen.scroll_area._invert_scroll is False

    emitted = []
    screen.settingChanged.connect(lambda k, v: emitted.append((k, v)))
    screen.sw_invert_scroll.toggled.emit(True)
    assert ("invert_scroll", True) in emitted
    assert screen.scroll_area._invert_scroll is True
