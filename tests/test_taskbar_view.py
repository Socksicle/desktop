"""Tests for the Material 3 Expressive 2026 Floating Navigation Toolbar, Search, and FAB."""

import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent, QKeyEvent

from ui.floating_button import FloatingButton, FloatingAddButton, FloatingSearchButton
from ui.taskbar_capsule import TaskbarCapsule
from ui.taskbar_view import TaskbarView
from utils.theme import M3Theme


@pytest.fixture
def theme():
    return M3Theme(preset_key="dynamic")


def test_fab_initial_state_and_click(qapp, theme):
    fab = FloatingAddButton(theme=theme)
    assert fab.width() == 64
    assert fab.height() == 64
    assert fab.get_anim_width() == 64.0
    assert fab.get_anim_opacity() == 1.0

    clicked = []
    fab.clicked.connect(lambda: clicked.append(True))

    pos = QPointF(32, 32)
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    ev_release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, pos, pos, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    fab.mousePressEvent(ev_press)
    fab.mouseReleaseEvent(ev_release)

    assert len(clicked) == 1


def test_search_btn_initial_state(qapp, theme):
    sbtn = FloatingSearchButton(theme=theme)
    assert sbtn.width() == 56
    assert sbtn.height() == 64
    assert sbtn.icon_name == "search"
    assert sbtn.get_anim_width() == 56.0
    assert sbtn.get_anim_opacity() == 1.0


def test_fab_collapse_and_expand_instant(qapp, theme):
    fab = FloatingAddButton(theme=theme)
    fab.set_expanded(False, animated=False)
    assert fab.get_anim_width() == 0.0
    assert fab.get_anim_opacity() == 0.0
    assert not fab.isVisible()

    fab.set_expanded(True, animated=False)
    assert fab.get_anim_width() == 64.0
    assert fab.get_anim_opacity() == 1.0
    assert fab.isVisible()


def test_capsule_3_slots_and_navigation(qapp, theme):
    capsule = TaskbarCapsule(theme=theme)
    assert capsule.height() == 64
    # 8*2 + 3*56 = 184
    assert capsule.width() == 184

    init_x = capsule._calc_indicator_x_for_screen(0)
    assert capsule.get_indicator_x() == init_x

    capsule.select_screen(1, animated=False)
    assert capsule._selected_screen == 1
    assert capsule.get_indicator_x() == capsule._calc_indicator_x_for_screen(1)

    capsule.select_screen(2, animated=False)
    assert capsule._selected_screen == 2
    assert capsule.get_indicator_x() == capsule._calc_indicator_x_for_screen(2)


def test_capsule_paints_without_missing_symbols(qapp, theme):
    """Regression: capsule paintEvent must run to completion.

    It referenced QPen without importing it, which raised NameError and left
    the whole navigation bar blank (and aborted the app on a real repaint).
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPaintEvent

    capsule = TaskbarCapsule(theme=theme)
    capsule.paintEvent(QPaintEvent(QRect(0, 0, capsule.width(), capsule.height())))


def test_capsule_search_mode_and_escape(qapp, theme):
    capsule = TaskbarCapsule(theme=theme)
    assert not capsule._search_open
    assert not capsule.search_widget.isVisible()

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
    ev_esc = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    capsule.keyPressEvent(ev_esc)
    assert len(closed) == 1

    capsule.set_search_mode(False)
    assert not capsule._search_open
    assert not capsule.search_widget.isVisible()
    assert capsule.width() == 184
    assert capsule.search_input.text() == ""


def test_capsule_mouse_clicks_slots(qapp, theme):
    capsule = TaskbarCapsule(theme=theme)
    selected_tabs = []
    capsule.tabSelected.connect(selected_tabs.append)

    # Click slot 1 (Settings): x in [8 + 56, 8 + 112] => e.g. x = 90
    pos_settings = QPointF(90, 32)
    ev_settings = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos_settings, pos_settings, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    capsule.mousePressEvent(ev_settings)
    assert selected_tabs == [1]

    # Click slot 2 (About): x in [8 + 112, 8 + 168] => e.g. x = 140
    pos_about = QPointF(140, 32)
    ev_about = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos_about, pos_about, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    capsule.mousePressEvent(ev_about)
    assert selected_tabs == [1, 2]

    # Click slot 0 (Home): x in [8, 8 + 56] => e.g. x = 30
    pos_home = QPointF(30, 32)
    ev_home = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos_home, pos_home, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    capsule.mousePressEvent(ev_home)
    assert selected_tabs == [1, 2, 0]


def test_taskbar_view_tab_selection_collapses_both_actions(qapp, theme):
    tb = TaskbarView(theme=theme)
    tab_changes = []
    tb.tabChanged.connect(tab_changes.append)

    # Initial state: screen 0 (Home), both Search and FAB are expanded
    assert tb._selected_tab == 0
    assert tb.search_btn.get_anim_width() == 56.0
    assert tb.fab.get_anim_width() == 64.0

    # Select Settings (1): both Search and FAB collapse to 0 width
    tb.select_tab(1, animated=False)
    assert tb._selected_tab == 1
    assert tb.search_btn.get_anim_width() == 0.0
    assert tb.fab.get_anim_width() == 0.0
    assert not tb.search_btn.isVisible()
    assert not tb.fab.isVisible()
    assert tab_changes == [1]

    # Select About (2): both stay collapsed
    tb.select_tab(2, animated=False)
    assert tb._selected_tab == 2
    assert tb.search_btn.get_anim_width() == 0.0
    assert tb.fab.get_anim_width() == 0.0
    assert tab_changes == [1, 2]

    # Select Home (0): both expand back
    tb.select_tab(0, animated=False)
    assert tb._selected_tab == 0
    assert tb.search_btn.get_anim_width() == 56.0
    assert tb.fab.get_anim_width() == 64.0
    assert not tb.search_btn.isHidden()
    assert not tb.fab.isHidden()
    assert tab_changes == [1, 2, 0]


def test_taskbar_view_search_button_toggle_and_fab_close(qapp, theme):
    tb = TaskbarView(theme=theme)
    add_clicks = []
    search_open_states = []
    tb.addClicked.connect(lambda: add_clicks.append(True))
    tb.searchOpenChanged.connect(search_open_states.append)

    # Click search button (with animated=False for instant test assertion)
    tb.toggle_search(animated=False)
    assert tb.is_search_open()
    assert search_open_states == [True]
    assert tb.fab._is_close_mode
    assert tb.search_btn.get_anim_width() == 0.0

    # In search mode, clicking FAB acts as close button
    tb.set_search_open(False, animated=False)
    assert not tb.is_search_open()
    assert search_open_states == [True, False]
    assert not tb.fab._is_close_mode
    assert tb.search_btn.get_anim_width() == 56.0
    # addClicked should NOT have fired
    assert len(add_clicks) == 0

    # In normal mode, clicking FAB emits addClicked
    tb._on_fab_clicked()
    assert len(add_clicks) == 1


def test_open_search_keeps_toolbar_width(qapp, theme):
    """Regression: opening search must not resize the centered toolbar.

    The capsule grew +66px while the search button animated away over 220ms,
    so the whole bar jumped sideways and slid back - felt as lag.
    """
    tb = TaskbarView(theme=theme)
    width_before = tb.layout().sizeHint().width()

    tb.set_search_open(True, animated=True)
    assert tb.layout().sizeHint().width() == width_before

    tb.set_search_open(False, animated=True)
    assert tb.layout().sizeHint().width() == width_before


def test_switch_between_non_home_tabs_keeps_width(qapp, theme):
    """Regression: Settings -> About re-collapsed already-collapsed buttons.

    Re-showing them reserved layout spacing for one animation cycle, so the
    toolbar jerked in and out while the tabs changed.
    """
    tb = TaskbarView(theme=theme)
    tb.select_tab(1, animated=False)
    width_settings = tb.layout().sizeHint().width()

    tb.select_tab(2, animated=True)
    assert tb.layout().sizeHint().width() == width_settings


def test_taskbar_view_theme_switch(qapp, theme):
    tb = TaskbarView(theme=theme)
    dark_theme = M3Theme(preset_key="dark")
    tb.apply_theme(dark_theme)
    assert tb.theme == dark_theme
    assert tb.capsule.theme == dark_theme
    assert tb.search_btn.theme == dark_theme
    assert tb.fab.theme == dark_theme
