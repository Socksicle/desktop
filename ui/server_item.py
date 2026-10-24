import re
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel,
    QFrame, QSizePolicy, QDialog, QVBoxLayout
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap

from utils.icons import set_button_icon as _set_btn_icon
from utils.window_utils import configure_window, compositing_available, find_overlay_target
from utils.i18n import tr
from .signal_bars_view import SignalBarsView
from .config_tag_badge import ConfigTagBadge, build_badge_texts
from .animated_radio_button import AnimatedRadioButton
from .qr_overlay_dialog import show_server_qr_dialog

_FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")


class ServerItem(QFrame):
    def __init__(self, text, server=None, theme=None, parent=None, ping_display_mode: int = 0,
                 show_flags: bool = False):
        super().__init__(parent)
        self.server = server
        self.theme = theme
        self._ping_display_mode = int(ping_display_mode)
        self._last_ping_ms = None
        self._delete_handler = None
        self._ping_anim: Optional[QTimer] = None
        self._ping_anim_frame = 0
        self.setStyleSheet("border: none; background: transparent;")
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.radio = AnimatedRadioButton(theme, self)

        self.main_layout.addWidget(self.radio)

        self.actions_container = QWidget(self.radio)
        self.actions_layout = QHBoxLayout(self.actions_container)
        self.actions_layout.setContentsMargins(0, 0, 10, 0)
        self.actions_layout.setSpacing(6)
        self.actions_layout.setAlignment(Qt.AlignVCenter | Qt.AlignRight)

        # Signal bars & Ping latency
        self.ping_bars = SignalBarsView(theme=self.theme)
        self.ping_bars.hide()

        self.ping_label = QLabel("")
        self.ping_label.setAlignment(Qt.AlignCenter)
        self.ping_label.setFixedHeight(24)
        self.ping_label.hide()

        # Protocol badge (text is (re)set by update_server())
        self.protocol_badge = ConfigTagBadge(theme=self.theme, parent=self)
        self.protocol_badge.widthChanged.connect(self._on_badge_width_changed)

        self.expired_badge = QLabel(tr("item.expired_badge"))
        self.expired_badge.setAlignment(Qt.AlignCenter)
        self.expired_badge.setFixedHeight(22)
        self.expired_badge.hide()

        self.expire_label = QLabel("")
        self.expire_label.setAlignment(Qt.AlignCenter)
        self.expire_label.setFixedHeight(22)
        self.expire_label.hide()

        self.share_button = QPushButton()
        self.qr_btn = self.share_button
        self.share_button.setFocusPolicy(Qt.NoFocus)
        self.share_button.setFixedSize(26, 26)
        self.share_button.setCursor(Qt.PointingHandCursor)
        self.share_button.clicked.connect(self.show_qr_code)

        self.delete_button = QPushButton()
        self.delete_button.setFocusPolicy(Qt.NoFocus)
        self.delete_button.setFixedSize(26, 26)
        self.delete_button.setCursor(Qt.PointingHandCursor)

        self.actions_layout.addStretch()
        self.actions_layout.addWidget(self.ping_bars, 0, Qt.AlignVCenter)
        self.actions_layout.addWidget(self.ping_label, 0, Qt.AlignVCenter)
        self.actions_layout.addWidget(self.expire_label, 0, Qt.AlignVCenter)
        self.actions_layout.addWidget(self.expired_badge, 0, Qt.AlignVCenter)
        self.actions_layout.addWidget(self.protocol_badge, 0, Qt.AlignVCenter)
        self.actions_layout.addWidget(self.share_button, 0, Qt.AlignVCenter)
        self.actions_layout.addWidget(self.delete_button, 0, Qt.AlignVCenter)

        # Row actions live in the context menu now; the buttons stay around
        # as the API surface (and for tests) but are never painted.
        self.share_button.hide()
        self.delete_button.hide()
        self._style_actions()
        self._raw_name = text or ""
        self._clean_name = text or ""
        self.update_server(text, server, show_flags=show_flags)
        self.setFixedHeight(48)

    def _on_badge_width_changed(self, _width: int):
        self._recompute_actions_width()

    def update_server(self, text, server=None, show_flags: bool | None = None):
        """Rebind the row to (possibly new) server data without recreating widgets.

        Lets ServerListPanel.refresh() reuse rows across rebuilds instead of
        destroying and recreating hundreds of widgets (and transiently holding
        old + new at once). Styling/icons are intentionally untouched: theme
        changes go through apply_theme().
        """
        self.server = server
        if show_flags is not None:
            self.radio._show_flags = bool(show_flags)

        self._raw_name = text or ""
        flag = getattr(server, "flag_emoji", None) if server else None
        if not flag or flag == "🌐":
            from utils.geo_utils import detect_flag, extract_name_and_emoji
            host = getattr(server, "host", "") if server else ""
            detected_flag, _ = detect_flag(text, host)
            clean_text, flag = extract_name_and_emoji(text, detected_flag)
        else:
            from utils.geo_utils import extract_name_and_emoji
            clean_text, _ = extract_name_and_emoji(text, flag)

        self._clean_name = clean_text
        self.radio.flag_emoji = flag
        self._apply_display_name()

        if server:
            host = getattr(server, "host", "") or getattr(server, "server", "")
            port = getattr(server, "port", "") or getattr(server, "server_port", "")
            if host and port:
                self.radio.host_text = f"{host}:{port}"
            elif host:
                self.radio.host_text = str(host)
            else:
                self.radio.host_text = ""
        else:
            self.radio.host_text = ""

        if server:
            compact_text, expanded_text = build_badge_texts(server)
            self.protocol_badge.set_texts(compact_text, expanded_text)
            if getattr(server, "plugin", ""):
                self.protocol_badge.setToolTip(tr("item.plugin").format(plugin=server.plugin))
            else:
                self.protocol_badge.setToolTip("")
        else:
            self.protocol_badge.set_texts("", "")
            self.protocol_badge.setToolTip("")

        if server and getattr(server, "is_expired", False):
            self.expired_badge.show()
        else:
            self.expired_badge.hide()

        import time as _time
        if server and getattr(server, "expires_at", None) and server.expires_at > 0 and not getattr(server, "is_expired", False):
            rem = server.expires_at - _time.time()
            if rem > 0:
                hours = int(rem // 3600)
                if hours < 1:
                    mins = max(1, int(rem // 60))
                    text = f"⏳ {mins}m"
                elif hours < 48:
                    text = f"⏳ {hours}h"
                else:
                    days = hours // 24
                    rem_h = hours % 24
                    text = f"⏳ {days}d {rem_h}h" if rem_h else f"⏳ {days}d"
                self.expire_label.setText(text)
                self.expire_label.show()
            else:
                self.expire_label.hide()
        else:
            self.expire_label.hide()

        # Ping display is re-applied by the caller (ServerListPanel keeps
        # per-server results); never show a stale value on a rebound row.
        self.stop_ping_animation()
        self._last_ping_ms = None
        self.ping_label.setText("")
        self.ping_label.hide()
        self.ping_bars.hide()

        self.radio.update()
        self._recompute_actions_width()

    def _style_actions(self):
        """(Re)apply theme-dependent QSS to the row badges and actions."""
        theme = self.theme
        self.ping_label.setStyleSheet(
            f"color: {theme.on_surface_variant}; font-size: 11px; font-weight: 600; "
            f"border: none; background: transparent; outline: none;"
        )
        self.protocol_badge.setStyleSheet(
            f"color: {theme.on_surface_variant}; background: rgba(255, 255, 255, 0.06); "
            f"border-radius: 6px; padding: 2px 6px; font-size: 9px; font-weight: 600; border: none; outline: none;"
        )
        self.expired_badge.setStyleSheet(
            f"color: {theme.error}; background: rgba(255, 100, 100, 0.12); border-radius: 6px; "
            f"padding: 2px 6px; font-size: 10px; font-weight: bold; border: none; outline: none;"
        )
        self.expire_label.setStyleSheet(
            f"color: {getattr(theme, 'tertiary', '#EFB8C8')}; background: rgba(255, 255, 255, 0.05); "
            f"border-radius: 6px; padding: 2px 6px; font-size: 10px; font-weight: 600; border: none; outline: none;"
        )
        self.share_button.setStyleSheet(f"""
            QPushButton {{
                color: {theme.on_surface_variant};
                background: transparent;
                border-radius: 13px;
                border: none;
                outline: none;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
                color: {theme.primary};
            }}
        """)
        self.delete_button.setStyleSheet(f"""
            QPushButton {{
                color: {theme.on_surface_variant};
                background: transparent;
                border-radius: 13px;
                border: none;
                outline: none;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: rgba(255, 100, 100, 0.18);
                color: {theme.error};
            }}
        """)
        _set_btn_icon(self.share_button, "share",
                      theme.on_surface_variant, 15, "🔗")
        _set_btn_icon(self.delete_button, "close",
                      theme.on_surface_variant, 13, "✕")

    def apply_theme(self, theme):
        self.theme = theme
        self.radio.theme = theme
        if self.radio._bg_anim is not None:
            self.radio._bg_anim.stop()
        self.radio.setBgColor(self.radio._compute_bg(self.radio.isChecked(), False))
        self.ping_bars.apply_theme(theme)
        self.protocol_badge.apply_theme(theme)
        self._style_actions()
        if self._last_ping_ms is not None:
            self.set_ping(self._last_ping_ms)
        self.radio.update()

    def contextMenuEvent(self, event):
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        accent_hex = getattr(self.theme, 'primary', '#D0BCFF')
        c = QColor(accent_hex)
        sel_bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, 0.25)"
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {getattr(self.theme, 'surface_container_high', '#241F32')};
                color: {getattr(self.theme, 'on_surface', '#E6E0E9')};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
                padding: 6px;
            }}
            QMenu::item {{ padding: 8px 16px; border-radius: 8px; }}
            QMenu::item:selected {{
                background-color: {sel_bg};
                color: #FFFFFF;
            }}
        """)
        act_qr = QAction(tr("item.menu_qr"), self)
        act_qr.setEnabled(not (self.server and getattr(self.server, "lock_export", False)))
        act_qr.triggered.connect(self.show_qr_code)
        menu.addAction(act_qr)

        act_delete = QAction(tr("item.menu_delete"), self)
        act_delete.triggered.connect(lambda: self.delete_button.click())
        menu.addAction(act_delete)

        menu.exec(event.globalPos())


    def show_qr_code(self):
        show_server_qr_dialog(self.server, self.theme, self)

    def set_ping(self, ms):
        self.stop_ping_animation()
        self._last_ping_ms = ms
        ok_color = getattr(self.theme, "primary", "#D0BCFF")
        warn_color = getattr(self.theme, "warning", "#FFC107")
        err_color = getattr(self.theme, "error", "#F44336")

        if ms is not None and ms >= 0:
            if ms < 150:
                color = ok_color
            elif ms < 300:
                color = warn_color
            else:
                color = err_color
            self.ping_label.setText(f"{ms:.0f}ms")
            self.ping_label.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: bold; "
                f"border: none; background: transparent; outline: none;"
            )
            self.ping_bars.set_latency(ms)
            self._apply_ping_visibility()
        elif ms is not None and ms < 0:
            self.ping_label.setText(tr("item.unreachable"))
            self.ping_label.setStyleSheet(
                f"color: {err_color}; font-size: 11px; font-weight: bold; "
                f"border: none; background: transparent; outline: none;"
            )
            self.ping_bars.set_latency(-1)
            self._apply_ping_visibility()
        else:
            self.ping_label.setText("")
            self.ping_label.hide()
            self.ping_bars.hide()
        self._recompute_actions_width()

    def is_ping_animating(self) -> bool:
        return self._ping_anim is not None and self._ping_anim.isActive()

    def start_ping_animation(self):
        """Show a lightweight busy indicator until set_ping() delivers a result.

        Numbers mode cycles running dots (".", "..", "..."), bars mode
        cycles the fill level 1-4; both mode animates the two together.
        """
        show_numbers = (self._ping_display_mode != 1)  # 0: numbers, 2: both
        show_bars = (self._ping_display_mode != 0)     # 1: bars, 2: both
        self._ping_anim_frame = 0
        if show_numbers:
            muted = getattr(self.theme, "outline", "#938F99")
            self.ping_label.setStyleSheet(
                f"color: {muted}; font-size: 11px; font-weight: bold; "
                f"border: none; background: transparent; outline: none;")
            self.ping_label.setText(".")
            self.ping_label.show()
        else:
            self.ping_label.hide()
        if show_bars:
            self.ping_bars.set_active_color(getattr(self.theme, "primary", "#D0BCFF"))
            self.ping_bars.set_level(1)
            self.ping_bars.show()
        else:
            self.ping_bars.hide()
        if self._ping_anim is None:
            self._ping_anim = QTimer(self)
            self._ping_anim.setInterval(300)
            self._ping_anim.timeout.connect(self._on_ping_anim_tick)
        if not self._ping_anim.isActive():
            self._ping_anim.start()
        self._recompute_actions_width()

    def stop_ping_animation(self):
        if self._ping_anim is not None and self._ping_anim.isActive():
            self._ping_anim.stop()

    def _on_ping_anim_tick(self):
        self._ping_anim_frame += 1
        if self._ping_display_mode != 1 and self.ping_label.isVisible():
            self.ping_label.setText("." * ((self._ping_anim_frame % 3) + 1))
        if self._ping_display_mode != 0 and self.ping_bars.isVisible():
            self.ping_bars.set_level((self._ping_anim_frame % 4) + 1)

    def _apply_ping_visibility(self):
        if self._last_ping_ms is not None:
            show_numbers = (self._ping_display_mode != 1)  # 0: numbers, 2: both
            show_bars = (self._ping_display_mode != 0)     # 1: bars, 2: both
            self.ping_label.setVisible(show_numbers)
            self.ping_bars.setVisible(show_bars)
        else:
            self.ping_label.setVisible(False)
            self.ping_bars.setVisible(False)

    def set_ping_display_mode(self, mode: int):
        self._ping_display_mode = int(mode)
        if self.is_ping_animating():
            self.ping_label.setVisible(self._ping_display_mode != 1)
            self.ping_bars.setVisible(self._ping_display_mode != 0)
        else:
            self._apply_ping_visibility()
        self._recompute_actions_width()

    def _apply_display_name(self):
        """Use clean name when the flag tile is visible; keep full name with emoji otherwise."""
        display_name = self._clean_name if self.radio._show_flags else self._raw_name
        self.radio.setText(display_name)

    def set_show_flags(self, show: bool):
        """Show or hide the country flag tile (mobile showFlags parity)."""
        self.radio._show_flags = bool(show)
        self._apply_display_name()
        self.radio.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recompute_actions_width()

    def showEvent(self, event):
        super().showEvent(event)
        self._recompute_actions_width()

    def _recompute_actions_width(self):
        w = 0
        if not self.ping_bars.isHidden():
            w += self.ping_bars.width() + 6
        if not self.ping_label.isHidden() and self.ping_label.text():
            w += self.ping_label.fontMetrics().horizontalAdvance(self.ping_label.text()) + 8
        if hasattr(self, "expire_label") and not self.expire_label.isHidden() and self.expire_label.text():
            w += self.expire_label.fontMetrics().horizontalAdvance(self.expire_label.text()) + 12
        if not self.protocol_badge.isHidden() and self.protocol_badge.text():
            w += self.protocol_badge.width() + 8
        if not self.share_button.isHidden():
            w += self.share_button.width() + 4
        w += self.delete_button.width() + 14
        self._actions_width = max(110, w)
        self.radio._actions_width = self._actions_width
        self.actions_container.setGeometry(
            max(0, self.radio.width() - self._actions_width),
            0,
            self._actions_width,
            self.radio.height()
        )
        self.radio.update()
