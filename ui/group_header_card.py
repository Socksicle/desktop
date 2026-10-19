from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget
from PySide6.QtCore import Qt, QPointF, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap

from utils.icons import pixmap, set_button_icon

from .chevron_view import ChevronView
from .elided_label import ElidedLabel
from utils.i18n import tr


class GroupHeaderCard(QFrame):
    """Subscription group header card (item_subscription_group.xml).

    Layout inside the card: chevron 22px -> icon tile 36px -> name 14.5px bold
    + subtitle 11.5px muted -> right actions 32x32 (ping / refresh / edit).

    The outer margins (0, 6, 0, 12) reproduce the Android item margins: header
    bottom margin 12dp, node top margin 6dp (list spacing supplies the rest).
    """

    collapseToggled = Signal(str)
    pingGroupRequested = Signal(str)
    refreshRequested = Signal(str)
    editRequested = Signal(str)

    def __init__(self, key: str, title: str, icon: str, subtitle: str = "",
                 is_direct: bool = False, theme=None, parent=None):
        super().__init__(parent)
        self.key = key
        self.is_direct = is_direct
        self.theme = theme
        self._expanded = True
        self._refreshing = False
        self._refresh_base_subtitle = ""
        self._refresh_pixmap: QPixmap | None = None
        self._spin_angle = 0.0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
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

        self.icon_tile = QFrame()
        self.icon_tile.setFixedSize(36, 36)
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
        self.btn_edit = self._action_button("pencil", tr("group.edit_tip"), fallback="✏")

        layout.addWidget(self.btn_ping, 0, Qt.AlignVCenter)
        layout.addWidget(self.btn_refresh, 0, Qt.AlignVCenter)
        layout.addWidget(self.btn_edit, 0, Qt.AlignVCenter)

        if is_direct:
            self.btn_refresh.hide()
            self.btn_edit.hide()

        self.btn_ping.clicked.connect(lambda: self.pingGroupRequested.emit(self.key))
        self.btn_refresh.clicked.connect(lambda: self.refreshRequested.emit(self.key))
        self.btn_edit.clicked.connect(lambda: self.editRequested.emit(self.key))

        if theme:
            self.apply_theme(theme)

    def _action_button(self, icon_name: str, tooltip: str, fallback: str = "") -> QPushButton:
        btn = QPushButton(fallback)
        btn.setFixedSize(32, 32)
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
            if not self._spin_timer.isActive():
                self._spin_angle = 0.0
                self._spin_timer.start()
            self.set_subtitle(tr("group.refreshing"))
        elif self._refreshing:
            self._refreshing = False
            self._spin_timer.stop()
            self._spin_angle = 0.0
            if self._refresh_pixmap is not None and not self._refresh_pixmap.isNull():
                self.btn_refresh.setIcon(QIcon(self._refresh_pixmap))
            self.set_subtitle(self._refresh_base_subtitle)

    def _spin_tick(self):
        icon = self._rotated_refresh_icon(self._spin_angle)
        self._spin_angle = (self._spin_angle + 4.0) % 360.0
        if icon is not None:
            self.btn_refresh.setIcon(icon)

    def _rotated_refresh_icon(self, angle: float) -> QIcon | None:
        """Render the refresh glyph rotated in place (no bounding-box growth)."""
        base = self._refresh_pixmap
        if base is None or base.isNull():
            return None
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
        return QIcon(pm)

    def set_expanded(self, expanded: bool):
        """Chevron points down when expanded, right when collapsed."""
        self._expanded = expanded
        self.chevron.set_angle(0 if expanded else -90)

    def set_subtitle(self, text: str):
        self.sub_label.setText(text)
        self.sub_label.setVisible(bool(text))

    def update_group(self, title: str, icon: str, subtitle: str = "",
                     is_direct: bool = False):
        """Rebind the card to (possibly new) group data without recreating widgets.

        Lets ServerListPanel.refresh() reuse cards across rebuilds. Signal
        connections use ``self.key`` dynamically, so callers only need to
        assign the new key alongside this call.
        """
        self.is_direct = is_direct
        self.name_label.setText(title or "")
        self.icon_label.setText(icon or "🌐")
        self.set_subtitle(subtitle or "")
        self.btn_refresh.setVisible(not is_direct)
        self.btn_edit.setVisible(not is_direct)

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
        self.icon_tile.setStyleSheet(f"""
            background-color: {surface_high};
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.15);
        """)
        self.name_label.setStyleSheet(
            f"color: {text_hex}; font-size: 14.5px; font-weight: bold; background: transparent;")
        self.sub_label.setStyleSheet(
            f"color: {muted_hex}; font-size: 11.5px; background: transparent;")

        btn_style = f"""
            QPushButton {{
                color: {muted_hex};
                background: transparent;
                border: none;
                border-radius: 16px;
                font-size: 14px;
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
        for btn in (self.btn_ping, self.btn_refresh, self.btn_edit):
            btn.setStyleSheet(btn_style)
            set_button_icon(btn, btn.property("iconName"),
                            muted_hex, 18, btn.property("iconFallback") or "")
        self._refresh_pixmap = pixmap("refresh", muted_hex, 18)
