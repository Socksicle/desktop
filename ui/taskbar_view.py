"""Material 3 Expressive 2026 Floating Navigation Toolbar with FAB and Search.

Features:
- Left: 64px capsule (radius 32px) in surface_container_high holding 3 icon-only
  tabs (Home, Settings, About) with a sliding 48x48px indicator (radius 24px).
- Right: Search button (56x64) next to vibrant FAB (64x64).
  On Settings and About tabs, both Search and FAB smoothly animate to 0 width/opacity (220ms, OutCubic),
  allowing the capsule to smoothly center.
- In Search mode: the capsule morphs into a search input bar with search icon,
  input field, and clear button, while the FAB turns into a close-search button ('✕').
- The capsule grows and the search button collapses with the same fluid_easing()
  morph (240ms) used by the config badges, keeping the toolbar width constant.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout
from PySide6.QtCore import Qt, Signal, Property, QPropertyAnimation, QEasingCurve

from utils.i18n import tr
from .floating_button import FloatingButton, FloatingAddButton, FloatingSearchButton
from .taskbar_capsule import TaskbarCapsule


class TaskbarView(QWidget):
    """Horizontal floating toolbar with navigation capsule, search button, and squircle FAB."""
    tabChanged = Signal(int)
    addClicked = Signal()
    searchClicked = Signal()
    searchTextChanged = Signal(str)
    searchOpenChanged = Signal(bool)

    _SEARCH_BTN_WIDTH = 56.0
    _BASE_SPACING = 10.0

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self._current_tab = 0
        self.tabs = [
            (tr("nav.main"), "tab_main"),
            (tr("nav.settings"), "tab_settings"),
            (tr("nav.about"), "tab_about"),
        ]

        self.setFixedSize(324, 64)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")

        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.lay.setAlignment(Qt.AlignCenter)

        self.capsule = TaskbarCapsule(theme=theme, parent=self)
        self.capsule.tabSelected.connect(self._on_capsule_tab_selected)
        self.capsule.searchTextChanged.connect(self.searchTextChanged.emit)
        self.capsule.searchClosed.connect(lambda: self.set_search_open(False))
        self.capsule.searchProgressChanged.connect(self._on_search_progress)
        self.lay.addWidget(self.capsule, 0, Qt.AlignVCenter)

        self.search_btn = FloatingSearchButton(theme=theme, parent=self)
        self.search_btn.clicked.connect(self.toggle_search)
        self.lay.addWidget(self.search_btn, 0, Qt.AlignVCenter)

        self.fab = FloatingAddButton(theme=theme, parent=self)
        self.fab.clicked.connect(self._on_fab_clicked)
        self.lay.addWidget(self.fab, 0, Qt.AlignVCenter)

        self._buttons_progress = 1.0
        self._buttons_anim = QPropertyAnimation(self, b"buttons_progress")
        self._buttons_anim.setDuration(220)
        self._buttons_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._buttons_anim.finished.connect(self._on_buttons_anim_finished)

        if theme:
            self.apply_theme(theme)

    @property
    def _selected_tab(self) -> int:
        return self._current_tab

    def get_buttons_progress(self) -> float:
        return self._buttons_progress

    def set_buttons_progress(self, val: float):
        p = max(0.0, min(1.0, float(val)))
        self._buttons_progress = p
        self.search_btn.set_anim_width(self._SEARCH_BTN_WIDTH * p)
        self.search_btn.set_anim_opacity(p)
        self.fab.set_anim_width(self.fab._default_width * p)
        self.fab.set_anim_opacity(p)
        self.lay.setSpacing(int(round(self._BASE_SPACING * p)))
        self.setFixedWidth(int(round(self.capsule._NORMAL_WIDTH + (324 - self.capsule._NORMAL_WIDTH) * p)))
        if p <= 0.001:
            self.search_btn.setVisible(False)
            self.fab.setVisible(False)
        else:
            self.search_btn.setVisible(True)
            self.fab.setVisible(True)

    buttons_progress = Property(float, get_buttons_progress, set_buttons_progress)

    def _on_buttons_anim_finished(self):
        if self._buttons_progress <= 0.001:
            self.search_btn.setVisible(False)
            self.fab.setVisible(False)
            self.lay.setSpacing(0)
            self.setFixedWidth(self.capsule._NORMAL_WIDTH)
        elif self._buttons_progress >= 0.999:
            self.search_btn.setVisible(True)
            self.fab.setVisible(True)
            self.lay.setSpacing(int(self._BASE_SPACING))
            self.setFixedWidth(324)

    def _on_capsule_tab_selected(self, screen_idx: int):
        self.select_tab(screen_idx, animated=True)

    def _on_fab_clicked(self):
        if self.is_search_open():
            self.set_search_open(False)
        else:
            self.addClicked.emit()

    def select_tab(self, index: int, animated: bool = True):
        index = max(0, min(2, int(index)))
        if self._current_tab == index and not self.is_search_open():
            return
        self._current_tab = index
        if self.is_search_open():
            self.set_search_open(False, animated=False)
        self.capsule.select_screen(index, animated=animated)

        target_p = 1.0 if index == 0 else 0.0
        if not animated:
            self._buttons_anim.stop()
            self.set_buttons_progress(target_p)
        else:
            if target_p == 1.0 and self._buttons_progress <= 0.001:
                self.lay.setSpacing(0)
                self.search_btn.set_anim_width(0.0)
                self.fab.set_anim_width(0.0)
                self.search_btn.set_anim_opacity(0.0)
                self.fab.set_anim_opacity(0.0)
                self.search_btn.setVisible(True)
                self.fab.setVisible(True)
            if abs(self._buttons_progress - target_p) > 0.001:
                self._buttons_anim.stop()
                self._buttons_anim.setStartValue(self._buttons_progress)
                self._buttons_anim.setEndValue(target_p)
                self._buttons_anim.start()

        self.tabChanged.emit(index)

    def set_search_open(self, open_: bool, animated: bool = True):
        if self.capsule._search_open == open_:
            return
        if open_ and self._current_tab != 0:
            self.select_tab(0, animated=animated)
        if open_:
            # Hand the search button over to the capsule morph: pin it at full
            # size first so the progress signal drives it from 56px to 0.
            self._buttons_anim.stop()
            self._buttons_progress = 1.0
            self.setFixedWidth(324)
            self.search_btn.setVisible(True)
            self.search_btn.set_anim_width(self._SEARCH_BTN_WIDTH)
            self.search_btn.set_anim_opacity(1.0)
        else:
            self.setFixedWidth(324)
            self.search_btn.setVisible(True)
        self.capsule.set_search_mode(open_, animated=animated)
        self.fab.set_close_mode(open_)
        self.searchOpenChanged.emit(open_)
        if not open_:
            self.searchTextChanged.emit("")
        else:
            self.searchClicked.emit()

    def _on_search_progress(self, progress: float):
        """Morph the search button alongside the capsule."""
        self.search_btn.set_anim_width(self._SEARCH_BTN_WIDTH * (1.0 - progress))
        self.search_btn.set_anim_opacity(1.0 - progress)
        if progress >= 0.999:
            self.search_btn.setVisible(False)
        else:
            self.search_btn.setVisible(True)

    def is_search_open(self) -> bool:
        return self.capsule._search_open

    def toggle_search(self, animated: bool = True):
        self.set_search_open(not self.is_search_open(), animated=animated)

    def get_search_text(self) -> str:
        return self.capsule.search_input.text()

    def apply_theme(self, theme):
        self.theme = theme
        self.capsule.apply_theme(theme)
        self.search_btn.theme = theme
        self.search_btn.update()
        self.fab.theme = theme
        self.fab.update()


__all__ = ["FloatingButton", "FloatingAddButton", "FloatingSearchButton", "TaskbarCapsule", "TaskbarView"]
