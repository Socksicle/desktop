from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget
from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap, QColor, QBrush, QPen

from utils.icons import pixmap, set_button_icon

from .chevron_view import ChevronView
from .elided_label import ElidedLabel
from utils.i18n import tr

_SPIN_FRAME_COUNT = 36
_SPIN_INTERVAL_MS = 33


class _SmoothIconTile(QFrame):
    """Antialiased rounded squircle container for icon glyphs.

    Replaces QSS border-radius to eliminate aliasing and jagged edges on curved borders.
    """

    def __init__(self, radius: float = 14.0, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self._radius = radius
        self._bg_color = QColor(255, 255, 255, 12)
        self._border_color = QColor(255, 255, 255, 38)
        self.setStyleSheet("background: transparent; border: none;")

    def set_colors(self, bg: str | QColor, border: str | QColor):
        self._bg_color = QColor(bg) if isinstance(bg, str) else bg
        self._border_color = QColor(border) if isinstance(border, str) else border
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.setPen(QPen(self._border_color, 1.0))
        p.setBrush(QBrush(self._bg_color))
        p.drawRoundedRect(rect, self._radius, self._radius)


class GroupHeaderCard(QFrame):
    """Subscription group header card (item_subscription_group.xml).

    Layout inside the card: chevron 22px -> icon tile 36px -> name 14.5px bold
    + subtitle 11.5px muted -> right actions 36x36 (ping / refresh / edit).

    The outer margins (0, 6, 0, 12) reproduce the Android item margins: header
    bottom margin 12dp, node top margin 6dp (list spacing supplies the rest).
    """

    collapseToggled = Signal(str)
    pingGroupRequested = Signal(str)
    refreshRequested = Signal(str)
    editRequested = Signal(str)
    pinToggled = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, key: str, title: str, icon: str, subtitle: str = "",
                 is_direct: bool = False, is_pinned: bool = False, theme=None, parent=None):
        super().__init__(parent)
        self.key = key
        self.is_direct = is_direct
        self.is_pinned = is_pinned
        self.theme = theme
        self._expanded = True
        self._refreshing = False
        self._refresh_base_subtitle = ""
        self._refresh_pixmap: QPixmap | None = None
        self._spin_frames: list[QIcon] = []
        self._spin_frame_index = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(_SPIN_INTERVAL_MS)
        self._spin_timer.timeout.connect(self._spin_tick)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 12)

        self.card = QFrame()
        self.card.setObjectName("groupCard")
        outer.addWidget(self.card)

        layout = QHBoxLayout(self.card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(2)

        self.chevron = ChevronView(22)
        layout.addWidget(self.chevron, 0, Qt.AlignVCenter)

        chevron_gap = QWidget()
        chevron_gap.setFixedWidth(6)
        chevron_gap.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(chevron_gap)

        self.icon_tile = _SmoothIconTile(radius=14.0)
        tile_layout = QVBoxLayout(self.icon_tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        self.icon_label = QLabel(icon or "🌐")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 17px; background: transparent;")
        tile_layout.addWidget(self.icon_label)
        layout.addWidget(self.icon_tile, 0, Qt.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(8, 0, 6, 0)
        text_box.setSpacing(1)

        self.name_label = ElidedLabel(title)
        self.name_label.setStyleSheet("font-size: 14.5px; font-weight: bold; background: transparent;")
        text_box.addWidget(self.name_label)

        self.sub_label = ElidedLabel(subtitle)
        self.sub_label.setStyleSheet("font-size: 11.5px; background: transparent;")
        self.sub_label.setVisible(bool(subtitle))
        text_box.addWidget(self.sub_label)

        layout.addLayout(text_box, 1)

        self.btn_ping = self._action_button("speedometer", tr("group.ping_tip"), fallback="⚡")
        self.btn_refresh = self._action_button("refresh", tr("group.refresh_tip"), fallback="🔄")
        self.btn_more = self._action_button("more_vert", tr("group.more_tip"), fallback="⋮")
        self.btn_edit = self.btn_more

        layout.addWidget(self.btn_ping, 0, Qt.AlignVCenter)
        layout.addWidget(self.btn_refresh, 0, Qt.AlignVCenter)
        layout.addWidget(self.btn_more, 0, Qt.AlignVCenter)

        if is_direct:
            self.btn_refresh.hide()
            self.btn_more.hide()

        self.btn_ping.clicked.connect(lambda: self.pingGroupRequested.emit(self.key))
        self.btn_refresh.clicked.connect(lambda: self.refreshRequested.emit(self.key))
        self.btn_more.clicked.connect(self._show_more_menu)

        if theme:
            self.apply_theme(theme)

    def _action_button(self, icon_name: str, tooltip: str, fallback: str = "") -> QPushButton:
        btn = QPushButton(fallback)
        btn.setFixedSize(36, 36)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setProperty("iconName", icon_name)
        btn.setProperty("iconFallback", fallback)
        return btn

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.collapseToggled.emit(self.key)
            event.accept()
        else:
            super().mousePressEvent(event)

    def set_pinging(self, busy: bool):
        """Disable the ping action while this group's batch runs off-thread."""
        self.btn_ping.setEnabled(not busy)

    def set_refreshing(self, refreshing: bool):
        """Disable the refresh action, spin its icon, and mark the card busy."""
        self.btn_refresh.setEnabled(not refreshing)
        if refreshing:
            if not self._refreshing:
                self._refresh_base_subtitle = self.sub_label.text()
            self._refreshing = True
            self._update_spin_timer()
            self.set_subtitle(tr("group.refreshing"))
        elif self._refreshing:
            self._refreshing = False
            self._spin_timer.stop()
            self._spin_frame_index = 0
            if self._refresh_pixmap is not None and not self._refresh_pixmap.isNull():
                self.btn_refresh.setIcon(QIcon(self._refresh_pixmap))
            self.set_subtitle(self._refresh_base_subtitle)

    def showEvent(self, event):
        super().showEvent(event)
        self._update_spin_timer()

    def hideEvent(self, event):
        self._spin_timer.stop()
        super().hideEvent(event)

    def _update_spin_timer(self):
        if self._refreshing and self.isVisible() and self._ensure_spin_frames():
            if not self._spin_timer.isActive():
                self._spin_timer.start()
        else:
            self._spin_timer.stop()

    def _ensure_spin_frames(self) -> bool:
        if self._spin_frames:
            return True
        base = self._refresh_pixmap
        if base is None or base.isNull():
            return False
        step = 360.0 / _SPIN_FRAME_COUNT
        self._spin_frames = [
            QIcon(self._rotated_refresh_pixmap(base, i * step))
            for i in range(_SPIN_FRAME_COUNT)
        ]
        return True

    def _spin_tick(self):
        if not self.isVisible() or not self._ensure_spin_frames():
            self._spin_timer.stop()
            return
        self._spin_frame_index = (self._spin_frame_index + 1) % len(self._spin_frames)
        self.btn_refresh.setIcon(self._spin_frames[self._spin_frame_index])

    def _rotated_refresh_pixmap(self, base: QPixmap, angle: float) -> QPixmap:
        """Render the refresh glyph rotated in place (no bounding-box growth)."""
        pm = QPixmap(base.size())
        pm.setDevicePixelRatio(base.devicePixelRatio())
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        try:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            dpr = base.devicePixelRatio()
            w, h = base.width() / dpr, base.height() / dpr
            painter.translate(w / 2.0, h / 2.0)
            painter.rotate(angle)
            painter.drawPixmap(QPointF(-w / 2.0, -h / 2.0), base)
        finally:
            painter.end()
        return pm

    def _show_more_menu(self):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        from utils.icons import icon

        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)

        surface_high = getattr(self.theme, "surface_container_high", "#241F32") if self.theme else "#241F32"
        text_hex = getattr(self.theme, "on_surface", "#E6E0E9") if self.theme else "#E6E0E9"
        accent_hex = getattr(self.theme, "primary", "#D0BCFF") if self.theme else "#D0BCFF"

        c = QColor(accent_hex)
        sel_bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.25)"

        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 5px;
            }}
            QMenu::item {{
                padding: 6px 14px 6px 30px;
                border-radius: 7px;
                font-size: 13px;
                color: {text_hex};
            }}
            QMenu::icon {{
                left: 8px;
            }}
            QMenu::item:selected {{
                background-color: {sel_bg};
                color: #FFFFFF;
            }}
        """)

        # 1. Редактировать (с иконкой карандаша)
        act_edit = QAction(tr("menu.edit"), self)
        ic_edit = icon("pencil", text_hex, 16)
        if ic_edit:
            act_edit.setIcon(ic_edit)
        act_edit.triggered.connect(lambda: self.editRequested.emit(self.key))
        menu.addAction(act_edit)

        # 2. Закрепить / Открепить (с иконкой пина)
        pin_text = tr("menu.unpin") if self.is_pinned else tr("menu.pin")
        act_pin = QAction(pin_text, self)
        ic_pin = icon("pin", text_hex, 16)
        if ic_pin:
            act_pin.setIcon(ic_pin)
        act_pin.triggered.connect(lambda: self.pinToggled.emit(self.key))
        menu.addAction(act_pin)

        # 3. Удалить (с иконкой удаления)
        act_del = QAction(tr("menu.delete"), self)
        ic_del = icon("delete", "#FF6B6B", 16)
        if ic_del:
            act_del.setIcon(ic_del)
        act_del.triggered.connect(lambda: self.deleteRequested.emit(self.key))
        menu.addAction(act_del)

        btn_pos = self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft())
        menu.exec(btn_pos)

    def set_expanded(self, expanded: bool):
        """Chevron points down when expanded, right when collapsed."""
        self._expanded = expanded
        self.chevron.set_angle(0 if expanded else -90)

    def set_subtitle(self, text: str):
        self.sub_label.setText(text)
        self.sub_label.setVisible(bool(text))

    def update_group(self, title: str, icon: str, subtitle: str = "",
                     is_direct: bool = False, is_pinned: bool = False):
        """Rebind the card to (possibly new) group data without recreating widgets.

        Lets ServerListPanel.refresh() reuse cards across rebuilds. Signal
        connections use ``self.key`` dynamically, so callers only need to
        assign the new key alongside this call.
        """
        self.is_direct = is_direct
        self.is_pinned = is_pinned
        self.name_label.setText(title or "")
        self.icon_label.setText(icon or "🌐")
        self.set_subtitle(subtitle or "")
        self.btn_refresh.setVisible(not is_direct)
        self.btn_more.setVisible(not is_direct)

    def apply_theme(self, theme):
        self.theme = theme
        accent_hex = getattr(theme, "primary", "#D0BCFF")
        surface_hex = getattr(theme, "surface_container", getattr(theme, "surface", "#1A1624"))
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(theme, "outline", "#938F99")

        self.setStyleSheet("background: transparent; border: none;")
        self.card.setStyleSheet(f"""
            QFrame#groupCard {{
                background-color: {surface_hex};
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.07);
            }}
        """)
        self.chevron.set_color(muted_hex)
        self.icon_tile.set_colors(surface_high, QColor(255, 255, 255, 38))
        self.name_label.setStyleSheet(
            f"color: {text_hex}; font-size: 14.5px; font-weight: bold; background: transparent;")
        self.sub_label.setStyleSheet(
            f"color: {muted_hex}; font-size: 11.5px; background: transparent;")

        btn_style = f"""
            QPushButton {{
                color: {muted_hex};
                background: transparent;
                border: none;
                border-radius: 18px;
                font-size: 15px;
                outline: none;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
                color: {accent_hex};
            }}
            QToolTip {{
                background-color: {surface_high};
                color: {text_hex};
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 5px 9px;
                font-size: 11.5px;
            }}
        """
        for btn in (self.btn_ping, self.btn_refresh, self.btn_more):
            btn.setStyleSheet(btn_style)
            set_button_icon(btn, btn.property("iconName"),
                            muted_hex, 18, btn.property("iconFallback") or "")
        self._refresh_pixmap = pixmap("refresh", muted_hex, 18)
        self._spin_frames = []
        self._spin_frame_index = 0
        if self._refreshing:
            self._update_spin_timer()
