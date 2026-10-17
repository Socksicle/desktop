"""Material 3 Expressive 2026 Navigation Capsule.

64px height capsule with 32px radius holding 3 navigation slots:
- Home (screen 0)
- Settings (screen 1)
- About (screen 2)
In search mode, capsule displays an embedded search input and clear button.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QLabel
)
from PySide6.QtCore import (
    Qt, Signal, Property, QPropertyAnimation, QEasingCurve, QRectF, QPointF
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QIcon, QPen
)

from utils import icons as _icons
from utils.i18n import tr
from .anim_utils import fluid_easing


class TaskbarCapsule(QWidget):
    """Floating navigation capsule (64px height, radius 32px)."""
    tabSelected = Signal(int)  # 0: Home, 1: Settings, 2: About
    searchTextChanged = Signal(str)
    searchClosed = Signal()
    searchProgressChanged = Signal(float)

    _SLOT_WIDTH = 56.0
    _PAD_H = 8.0
    _INDICATOR_SIZE = 48.0
    _NORMAL_WIDTH = 184  # 8*2 + 3*56
    _SEARCH_WIDTH = 250

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._selected_screen = 0  # 0: Home, 1: Settings, 2: About
        self._indicator_x = self._calc_indicator_x_for_screen(0)
        self._search_open = False
        self._search_progress = 0.0

        self.setFixedHeight(64)
        self.setFixedWidth(self._NORMAL_WIDTH)
        self.setCursor(Qt.PointingHandCursor)

        self._indicator_anim = QPropertyAnimation(self, b"indicator_x")
        self._indicator_anim.setDuration(220)
        self._indicator_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Same morph as the config badges: width interpolates with fluid_easing()
        # while the search content slides in and the nav icons fade out.
        self._search_anim = QPropertyAnimation(self, b"search_progress")
        self._search_anim.setDuration(240)
        self._search_anim.setEasingCurve(fluid_easing())

        # Embedded search UI (shown when search is open)
        self.search_widget = QWidget(self)
        self.search_widget.setGeometry(0, 0, self._SEARCH_WIDTH, 64)
        sw_lay = QHBoxLayout(self.search_widget)
        sw_lay.setContentsMargins(16, 10, 14, 10)
        sw_lay.setSpacing(8)

        self.search_icon_lbl = QLabel()
        self.search_icon_lbl.setFixedSize(20, 20)
        self.search_icon_lbl.setAlignment(Qt.AlignCenter)
        sw_lay.addWidget(self.search_icon_lbl, 0, Qt.AlignVCenter)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("header.search_placeholder"))
        self.search_input.setFrame(False)
        self.search_input.setMaxLength(200)
        self.search_input.textChanged.connect(self.searchTextChanged.emit)
        sw_lay.addWidget(self.search_input, 1, Qt.AlignVCenter)

        self.search_clear_btn = QPushButton()
        self.search_clear_btn.setFixedSize(24, 24)
        self.search_clear_btn.setCursor(Qt.PointingHandCursor)
        self.search_clear_btn.hide()
        self.search_clear_btn.clicked.connect(self._on_clear_clicked)
        self.search_input.textChanged.connect(lambda t: self.search_clear_btn.setVisible(bool(t)))
        sw_lay.addWidget(self.search_clear_btn, 0, Qt.AlignVCenter)

        self.search_widget.hide()
        if theme:
            self.apply_theme(theme)

    def get_indicator_x(self) -> float:
        return self._indicator_x

    def set_indicator_x(self, val: float):
        self._indicator_x = float(val)
        self.update()

    indicator_x = Property(float, get_indicator_x, set_indicator_x)

    def get_search_progress(self) -> float:
        return self._search_progress

    def set_search_progress(self, val: float):
        p = max(0.0, min(1.0, float(val)))
        if p == self._search_progress:
            return
        self._search_progress = p
        width = int(round(self._NORMAL_WIDTH + (self._SEARCH_WIDTH - self._NORMAL_WIDTH) * p))
        self.setFixedWidth(width)
        # Keep the content's right edge on the capsule's right edge so it slides
        # out from the search button slot and is clipped while the capsule grows.
        self.search_widget.setGeometry(width - self._SEARCH_WIDTH, 0, self._SEARCH_WIDTH, 64)
        if p > 0.0:
            self.search_widget.show()
        else:
            self.search_widget.hide()
        self.update()
        self.searchProgressChanged.emit(p)

    search_progress = Property(float, get_search_progress, set_search_progress)

    def _calc_indicator_x_for_screen(self, screen_idx: int) -> float:
        slot = max(0, min(2, int(screen_idx)))
        slot_cx = self._PAD_H + (slot * self._SLOT_WIDTH) + (self._SLOT_WIDTH / 2.0)
        return slot_cx - (self._INDICATOR_SIZE / 2.0)

    def select_screen(self, screen_idx: int, animated: bool = True):
        screen_idx = max(0, min(2, int(screen_idx)))
        self._selected_screen = screen_idx
        target_x = self._calc_indicator_x_for_screen(screen_idx)
        if not animated:
            self._indicator_anim.stop()
            self.set_indicator_x(target_x)
        else:
            self._indicator_anim.stop()
            self._indicator_anim.setStartValue(self._indicator_x)
            self._indicator_anim.setEndValue(target_x)
            self._indicator_anim.start()

    def set_search_mode(self, open_: bool, animated: bool = False):
        self._search_open = bool(open_)
        target = 1.0 if self._search_open else 0.0

        if animated:
            self._search_anim.stop()
            self._search_anim.setStartValue(self._search_progress)
            self._search_anim.setEndValue(target)
            self._search_anim.start()
        else:
            self._search_anim.stop()
            self.set_search_progress(target)

        if self._search_open:
            self.search_input.setFocus()
        else:
            self.search_input.clear()

    def _on_clear_clicked(self):
        self.search_input.clear()
        self.search_input.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._search_open:
            self.searchClosed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self._search_open:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            slot = int((x - self._PAD_H) / self._SLOT_WIDTH)
            slot = max(0, min(2, slot))
            self.tabSelected.emit(slot)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())
        radius = h / 2.0  # 32px

        bg_hex = getattr(self.theme, "surface_container_lowest", getattr(self.theme, "surface", "#171005"))
        border_hex = getattr(self.theme, "outline_variant", "#52443d")
        border_color = QColor(border_hex)
        border_color.setAlpha(120)

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(QBrush(QColor(bg_hex)))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius - 0.5, radius - 0.5)

        icons_alpha = 1.0 - self._search_progress
        if icons_alpha <= 0.01:
            return
        if icons_alpha < 0.999:
            painter.setOpacity(icons_alpha)

        # Active indicator circle/pill (48x48px, radius 24px)
        ind_bg = getattr(self.theme, "secondary_container", "#40331e")
        ind_border = getattr(self.theme, "primary", "#f5d47a")
        ind_y = (h - self._INDICATOR_SIZE) / 2.0
        ind_rect = QRectF(self._indicator_x, ind_y, self._INDICATOR_SIZE, self._INDICATOR_SIZE)

        ind_border_c = QColor(ind_border)
        ind_border_c.setAlpha(70)
        painter.setPen(QPen(ind_border_c, 1.0))
        painter.setBrush(QBrush(QColor(ind_bg)))
        painter.drawRoundedRect(ind_rect, 24.0, 24.0)

        icons_info = [
            (0, "tab_main", 24),
            (1, "tab_settings", 24),
            (2, "tab_about", 24),
        ]
        active_fg = getattr(self.theme, "primary", "#F5D47A")
        inactive_fg = getattr(self.theme, "outline", "#938F99")

        for slot_idx, icon_name, icon_size in icons_info:
            is_active = (slot_idx == self._selected_screen)
            color_hex = active_fg if is_active else inactive_fg
            slot_cx = self._PAD_H + (slot_idx * self._SLOT_WIDTH) + (self._SLOT_WIDTH / 2.0)
            slot_cy = h / 2.0

            pm = _icons.pixmap(icon_name, color_hex, icon_size)
            if pm and not pm.isNull():
                logical = pm.width() / max(1.0, pm.devicePixelRatio())
                painter.drawPixmap(QPointF(slot_cx - logical / 2.0, slot_cy - logical / 2.0), pm)

    def apply_theme(self, theme):
        self.theme = theme
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                color: {text_hex};
                background: transparent;
                border: none;
                font-size: 13.5px;
                font-weight: 500;
                padding: 0px;
                selection-background-color: rgba(255, 255, 255, 0.2);
            }}
        """)
        pm = _icons.pixmap("search", muted_hex, 18)
        if pm:
            self.search_icon_lbl.setPixmap(pm)
        pm_close = _icons.pixmap("close", muted_hex, 14)
        if pm_close:
            self.search_clear_btn.setIcon(QIcon(pm_close))
            self.search_clear_btn.setStyleSheet("background: transparent; border: none;")
        self.update()
