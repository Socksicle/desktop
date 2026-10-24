"""Tests for the Material 3 Expressive 2026 Floating Navigation Toolbar, Search, and FAB."""

import pytest
from PySide6.QtCore import Qt, QPointF, QRect
from PySide6.QtGui import QMouseEvent, QKeyEvent, QPaintEvent

from ui.floating_button import FloatingAddButton, FloatingSearchButton
from ui.taskbar_capsule import TaskbarCapsule
from ui.taskbar_view import TaskbarView
from ui.anim_utils import fluid_easing
from utils.theme import M3Theme


@pytest.fixture
def theme():
    return M3Theme(preset_key="dynamic")


def test_action_buttons_lifecycle(qapp, theme):
    # FAB initial state & click
    fab = FloatingAddButton(theme=theme)
    assert fab.width() == 64
    assert fab.height() == 64
    assert fab.get_anim_width() == 64.0
    assert fab.get_anim_opacity() == 1.0

    clicked = []
    fab.clicked.connect(lambda: clicked.append(True))
    pos = QPointF(32, 32)
    fab.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    fab.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, pos, pos, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert len(clicked) == 1

    # Search button initial state
    sbtn = FloatingSearchButton(theme=theme)
    assert sbtn.width() == 56
    assert sbtn.height() == 64
    assert sbtn.icon_name == "search"
    assert sbtn.get_anim_width() == 56.0
    assert sbtn.get_anim_opacity() == 1.0

    # Collapse and expand instant
    fab.set_expanded(False, animated=False)
    assert fab.get_anim_width() == 0.0
    assert fab.get_anim_opacity() == 0.0
    assert not fab.isVisible()
    fab.set_expanded(True, animated=False)
    assert fab.get_anim_width() == 64.0
    assert fab.get_anim_opacity() == 1.0
    assert fab.isVisible()


def test_capsule_slots_navigation_and_rendering(qapp, theme):
    capsule = TaskbarCapsule(theme=theme)
    assert capsule.height() == 64
    assert capsule.width() == 184

    # Navigation slots
    assert capsule.get_indicator_x() == capsule._calc_indicator_x_for_screen(0)
    capsule.select_screen(1, animated=False)
    assert capsule._selected_screen == 1
    assert capsule.get_indicator_x() == capsule._calc_indicator_x_for_screen(1)
    capsule.select_screen(2, animated=False)
    assert capsule._selected_screen == 2
    assert capsule.get_indicator_x() == capsule._calc_indicator_x_for_screen(2)

    # Click interactions
    selected_tabs = []
    capsule.tabSelected.connect(selected_tabs.append)
    pos_settings = QPointF(90, 32)
    capsule.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos_settings, pos_settings, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    pos_about = QPointF(140, 32)
    capsule.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos_about, pos_about, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    pos_home = QPointF(30, 32)
    capsule.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos_home, pos_home, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert selected_tabs == [1, 2, 0]

    # Paint event without error
    capsule.paintEvent(QPaintEvent(QRect(0, 0, capsule.width(), capsule.height())))


def test_capsule_search_interactions(qapp, theme, monkeypatch):
    capsule = TaskbarCapsule(theme=theme)
    assert not capsule._search_open
    assert not capsule.search_widget.isVisible()
    assert capsule._search_anim.duration() == 240
    assert capsule._search_anim.easingCurve() == fluid_easing()

    focused = False
    monkeypatch.setattr(capsule.search_input, "setFocus", lambda: None)

    capsule.set_search_mode(True)
    assert capsule._search_open
    assert not capsule.search_widget.isHidden()
    assert capsule.width() == 250

    typed = []
    capsule.searchTextChanged.connect(typed.append)
    capsule.search_input.setText("wireguard")
    assert typed[-1] == "wireguard"

    closed = []
    capsule.searchClosed.connect(lambda: closed.append(True))
    capsule.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert len(closed) == 1

    capsule.set_search_mode(False)
    assert not capsule._search_open
    assert not capsule.search_widget.isVisible()
    assert capsule.width() == 184
    assert capsule.search_input.text() == ""


def test_taskbar_view_state_and_theme(qapp, theme):
    tb = TaskbarView(theme=theme)
    tab_changes = []
    tb.tabChanged.connect(tab_changes.append)

    # Tab selection collapsing
    tb.select_tab(1, animated=False)
    assert tab_changes == [1]
    assert tb._selected_tab == 1
    assert tb.search_btn.isHidden()
    assert tb.fab.isHidden()

    tb.select_tab(0, animated=False)
    assert tab_changes == [1, 0]
    assert not tb.search_btn.isHidden()
    assert not tb.fab.isHidden()

    # Search toggle
    tb.toggle_search(animated=False)
    assert tb.is_search_open()
    assert tb.fab._is_close_mode
    assert tb.search_btn.get_anim_width() == 0.0

    tb.set_search_open(False, animated=False)
    assert not tb.is_search_open()
    assert not tb.fab._is_close_mode
    assert not tb.search_btn.isHidden()
    assert not tb.fab.isHidden()

    # Theme application
    dark_theme = M3Theme(preset_key="dark")
    tb.apply_theme(dark_theme)
    assert tb.theme == dark_theme
    assert tb.capsule.theme == dark_theme
    assert tb.search_btn.theme == dark_theme
    assert tb.fab.theme == dark_theme


def test_taskbar_layout_and_progress_invariants(qapp, theme):
    tb = TaskbarView(theme=theme)
    width_before = tb.layout().sizeHint().width()

    # Opening search keeps toolbar width
    tb.set_search_open(True, animated=True)
    assert tb.layout().sizeHint().width() == width_before
    tb.set_search_open(False, animated=True)
    assert tb.layout().sizeHint().width() == width_before

    # Non-home tab switches keep width
    tb.select_tab(1, animated=False)
    width_settings = tb.layout().sizeHint().width()
    tb.select_tab(2, animated=True)
    assert tb.layout().sizeHint().width() == width_settings

    # Buttons progress sync
    tb.select_tab(0, animated=False)
    assert tb.get_buttons_progress() == 1.0
    assert tb.lay.spacing() == 10
    tb.set_buttons_progress(0.5)
    assert abs(tb.search_btn.get_anim_width() - 28.0) < 0.1
    assert abs(tb.fab.get_anim_width() - 32.0) < 0.1
    assert tb.lay.spacing() == 5
    tb.set_buttons_progress(0.0)
    assert tb.search_btn.isHidden()
    assert tb.fab.isHidden()
    assert tb.lay.spacing() == 0

    # Spacing and width stability across transitions
    tb_trans = TaskbarView(theme=theme)
    assert tb_trans.width() == 324
    for step in range(11):
        p = step / 10.0
        tb_trans.capsule.set_search_progress(p)
        tb_trans._on_search_progress(p)
        tb_trans.lay.activate()
        assert tb_trans.width() == 324


def test_capsule_search_animation_invariants(qapp, theme):
    capsule = TaskbarCapsule(theme=theme)

    # Initial state
    capsule.set_search_progress(0.0)
    assert capsule.width() == 184
    assert not capsule.search_widget.isVisible()

    # Mid progress
    capsule.set_search_progress(0.5)
    expected_w = int(round(184 + (250 - 184) * 0.5))
    assert capsule.width() == expected_w
    assert capsule.search_widget.x() == 0
    assert not capsule.search_widget.isHidden()

    # Full progress
    capsule.set_search_progress(1.0)
    assert capsule.width() == 250
    assert capsule.search_opacity_effect.opacity() == 1.0

    # Non-overlap guarantee
    for step in range(21):
        p = step / 20.0
        capsule.set_search_progress(p)
        search_visible = not capsule.search_widget.isHidden() and capsule.search_opacity_effect.opacity() > 0.001
        icons_visible = p < capsule._FADE_SPLIT and (1.0 - (p / capsule._FADE_SPLIT)) > 0.001
        assert not (search_visible and icons_visible), f"Overlap at p={p}"
