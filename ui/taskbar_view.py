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
from PySide6.QtCore import Qt, Signal

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

        self.setFixedHeight(64)
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

        if theme:
            self.apply_theme(theme)

    @property
    def _selected_tab(self) -> int:
        return self._current_tab

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
        self.search_btn.set_expanded(index == 0, animated=animated)
        self.fab.set_expanded(index == 0, animated=animated)
        self.tabChanged.emit(index)

    def set_search_open(self, open_: bool, animated: bool = True):
        if self.capsule._search_open == open_:
            return
        if open_ and self._current_tab != 0:
            self.select_tab(0, animated=animated)
        if open_:
            # Hand the search button over to the capsule morph: pin it at full
            # size first so the progress signal drives it from 56px to 0.
            self.search_btn.set_expanded(True, animated=False)
        self.capsule.set_search_mode(open_, animated=animated)
        self.fab.set_close_mode(open_)
        self.searchOpenChanged.emit(open_)
        if not open_:
            self.searchTextChanged.emit("")
        else:
            self.searchClicked.emit()

    def _on_search_progress(self, progress: float):
        """Morph the search button and the toolbar gap alongside the capsule.

        The capsule grows by (search button width + one layout gap) while the
        button collapses and the two gaps shrink into one, so the centered
        toolbar keeps a constant total width instead of jumping sideways.
        """
        self.search_btn.set_anim_width(self._SEARCH_BTN_WIDTH * (1.0 - progress))
        self.search_btn.set_anim_opacity(1.0 - progress)
        self.lay.setSpacing(int(round(self._BASE_SPACING * (1.0 - 0.5 * progress))))

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
