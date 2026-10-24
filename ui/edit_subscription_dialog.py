"""Edit subscription dialog (name, icon, URL, expiry, traffic)."""

import random
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QGridLayout, QApplication, QGraphicsOpacityEffect,
    QWidget
)
from PySide6.QtCore import (
    Qt, QSize, QEvent, QEventLoop, Property, QPropertyAnimation, QEasingCurve,
    QUrl
)
from PySide6.QtGui import QPainter, QColor, QDesktopServices
from ui.message_box import MessageBox
from ui.wavy_progress_bar import WavyLinearProgressBar
from ui.toggle_switch import AnimatedToggleSwitch
from utils.window_utils import configure_window, find_overlay_target
from utils.locale import format_days_remaining
from utils.icons import icon, set_label_icon
from utils.i18n import tr

ALL_EMOJI_POOL = [
    "🌐", "🚀", "🛡", "⚡", "☁", "🖥", "🔒", "⭐", "🧭", "❄",
    "🎯", "💎", "🔥", "🪐", "🛰", "🦊", "🐱", "🐲", "👾", "🤖",
    "🔮", "🔑", "👑", "🗡", "⚔", "🎲", "🛸", "🌍", "🌎", "🌏",
    "🗽", "🗼", "🏰", "🌋", "🏖", "🏝", "🌊", "🌪", "🌈", "☀",
    "🌙", "🌟", "💫", "✨", "💥", "🕶", "🎩", "🦄", "🦅", "🦉",
    "🐺", "🦁", "🐯", "🐼", "🐸", "🐙", "🦑", "🐬", "🐳", "🦈"
]


class EditSubscriptionDialog(QDialog):
    """Material 3 subscription editor."""

    def __init__(self, sub_dict: dict, sub_manager, theme, parent=None):
        target = find_overlay_target(parent)
        self._target_parent = target
        self._is_overlay = target is not None
        super().__init__(target if self._is_overlay else parent)
        self.sub = sub_dict
        self.manager = sub_manager
        self.theme = theme
        self.selected_icon = sub_dict.get("icon_emoji", "🌐")
        self.deleted = False
        self._dialog_progress = 0.0
        self._scrim_opacity = 0.0
        self._is_finishing = False
        self._loop = None

        if self._is_overlay:
            self.setWindowFlags(Qt.Widget)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setGeometry(self._target_parent.rect())
            self._target_parent.installEventFilter(self)
        else:
            self.setWindowFlags(Qt.Dialog)
            configure_window(self)

        self._init_ui()

        if self._is_overlay:
            self.card_opacity_effect = QGraphicsOpacityEffect(self.card)
            self.card.setGraphicsEffect(self.card_opacity_effect)
            self._anim = QPropertyAnimation(self, b"dialog_progress")
            self._anim.setDuration(180)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._update_card_geometry()

    def _init_ui(self):
        self.outer_layout = QVBoxLayout(self)
        if self._is_overlay:
            self.outer_layout.setAlignment(Qt.AlignCenter)
            self.outer_layout.setContentsMargins(16, 16, 16, 16)
        else:
            self.setFixedWidth(420)
            self.outer_layout.setContentsMargins(14, 14, 14, 14)
        outer = self.outer_layout

        surface_hex = getattr(self.theme, "surface_container", getattr(self.theme, "surface", "#1A1624"))
        surface_high = getattr(self.theme, "surface_container_high", "#241F32")
        accent_hex = getattr(self.theme, "primary", "#D0BCFF")
        text_hex = getattr(self.theme, "on_surface", "#E6E0E9")
        muted_hex = getattr(self.theme, "outline", "#938F99")

        self.card = QFrame(self)
        self.card.setStyleSheet(f"""
            QFrame {{
                background-color: {surface_hex};
                border-radius: 24px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
        """)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        title_text = tr("sub.edit_title") if self.sub.get("url") else tr("sub.list_title")
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {text_hex}; border: none; background: transparent;")
        lay.addWidget(title_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_icon = QPushButton(self.selected_icon)
        self.btn_icon.setFixedSize(48, 48)
        self.btn_icon.setCursor(Qt.PointingHandCursor)
        self.btn_icon.setToolTip(tr("sub.icon_tooltip"))
        self.btn_icon.setStyleSheet(f"""
            QPushButton {{
                background-color: {surface_high};
                border-radius: 14px;
                font-size: 22px;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.12);
            }}
        """)
        self.btn_icon.clicked.connect(self._open_icon_picker)
        row.addWidget(self.btn_icon)

        self.edit_name = QLineEdit(self.sub.get("name", ""))
        self.edit_name.setFixedHeight(48)
        self.edit_name.setPlaceholderText(tr("sub.name_placeholder"))
        self.edit_name.setStyleSheet(f"""
            QLineEdit {{
                background-color: {surface_high};
                color: {text_hex};
                border-radius: 14px;
                padding: 0 14px;
                font-size: 14px;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }}
        """)
        row.addWidget(self.edit_name, 1)
        lay.addLayout(row)

        insecure_row = QWidget()
        insecure_row.setStyleSheet("border: none; background: transparent;")
        insecure_lay = QHBoxLayout(insecure_row)
        insecure_lay.setContentsMargins(4, 4, 4, 4)
        insecure_lay.setSpacing(12)

        insecure_lbl_box = QVBoxLayout()
        insecure_lbl_box.setSpacing(2)

        lbl_insecure_title = QLabel(tr("sub.allow_insecure"))
        lbl_insecure_title.setStyleSheet(f"font-size: 13.5px; font-weight: bold; color: {text_hex}; background: transparent; border: none;")
        lbl_insecure_title.setWordWrap(True)
        insecure_lbl_box.addWidget(lbl_insecure_title)

        lbl_insecure_sub = QLabel(tr("sub.allow_insecure_sub"))
        lbl_insecure_sub.setStyleSheet(f"font-size: 11px; color: {muted_hex}; background: transparent; border: none;")
        lbl_insecure_sub.setWordWrap(True)
        insecure_lbl_box.addWidget(lbl_insecure_sub)

        insecure_lay.addLayout(insecure_lbl_box, 1)

        self.sw_insecure = AnimatedToggleSwitch(theme=self.theme)
        self.sw_insecure.setChecked(bool(self.sub.get("allow_insecure", False)))
        self.sw_insecure.setFixedWidth(52)
        insecure_lay.addWidget(self.sw_insecure, 0, Qt.AlignVCenter)
        lay.addWidget(insecure_row)

        traffic_meta = self.sub.get("traffic")
        expire_ts = self.sub.get("expires_at") or (traffic_meta.get("expire") if traffic_meta else 0)
        if expire_ts and expire_ts > 0:
            exp_card = QFrame()
            exp_card.setStyleSheet(f"background-color: {surface_high}; border-radius: 12px; border: none;")
            exp_lay = QHBoxLayout(exp_card)
            exp_lay.setContentsMargins(12, 6, 12, 6)
            exp_lay.setSpacing(8)

            exp_icon = QLabel()
            exp_icon.setFixedSize(16, 16)
            exp_icon.setAlignment(Qt.AlignCenter)
            exp_icon.setStyleSheet("border: none; background: transparent;")
            set_label_icon(exp_icon, "hourglass", accent_hex, 16)
            exp_lay.addWidget(exp_icon)

            lbl_exp_title = QLabel(tr("sub.expire_title"))
            lbl_exp_title.setStyleSheet(f"font-size: 10px; font-weight: bold; color: {muted_hex}; border: none; background: transparent;")
            exp_lay.addWidget(lbl_exp_title)
            exp_lay.addStretch()

            lbl_exp_val = QLabel(format_days_remaining(expire_ts))
            lbl_exp_val.setStyleSheet(f"font-size: 12px; color: {accent_hex}; font-weight: bold; border: none; background: transparent;")
            exp_lay.addWidget(lbl_exp_val)
            lay.addWidget(exp_card)

        desc = (self.sub.get("description") or self.sub.get("announce") or "").strip()
        if desc:
            spoiler_card = QFrame()
            spoiler_card.setStyleSheet(f"background-color: {surface_high}; border-radius: 12px; border: none;")
            spoiler_lay = QVBoxLayout(spoiler_card)
            spoiler_lay.setContentsMargins(12, 8, 12, 8)
            spoiler_lay.setSpacing(6)

            header = QWidget()
            header.setCursor(Qt.PointingHandCursor)
            header_lay = QHBoxLayout(header)
            header_lay.setContentsMargins(0, 0, 0, 0)
            header_lay.setSpacing(8)

            desc_icon = QLabel()
            desc_icon.setFixedSize(16, 16)
            desc_icon.setAlignment(Qt.AlignCenter)
            desc_icon.setStyleSheet("border: none; background: transparent;")
            set_label_icon(desc_icon, "description", accent_hex, 16)
            header_lay.addWidget(desc_icon)

            lbl_desc_title = QLabel(tr("sub.description_title"))
            lbl_desc_title.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {muted_hex}; border: none; background: transparent;")
            header_lay.addWidget(lbl_desc_title)
            header_lay.addStretch()

            lbl_arrow = QLabel()
            lbl_arrow.setFixedSize(16, 16)
            lbl_arrow.setAlignment(Qt.AlignCenter)
            lbl_arrow.setStyleSheet("border: none; background: transparent;")
            set_label_icon(lbl_arrow, "arrow_drop_down", muted_hex, 16)
            header_lay.addWidget(lbl_arrow)
            spoiler_lay.addWidget(header)

            desc_content = QLabel(desc)
            desc_content.setWordWrap(True)
            desc_content.setTextInteractionFlags(Qt.TextSelectableByMouse)
            desc_content.setStyleSheet(f"font-size: 12px; color: {text_hex}; border: none; background: transparent; padding: 2px 0 4px 0;")
            desc_content.setVisible(False)
            spoiler_lay.addWidget(desc_content)

            def _toggle_desc(event=None):
                is_open = desc_content.isHidden()
                desc_content.setVisible(is_open)
                set_label_icon(lbl_arrow, "arrow_drop_up" if is_open else "arrow_drop_down", muted_hex, 16)
                if self._target_parent:
                    self._update_card_geometry()

            header.mousePressEvent = _toggle_desc
            self.spoiler_desc = desc_content
            self.spoiler_toggle = _toggle_desc
            lay.addWidget(spoiler_card)

        if traffic_meta and traffic_meta.get("total", 0) > 0:
            tr_card = QFrame()
            tr_card.setStyleSheet(f"background-color: {surface_high}; border-radius: 12px; border: none;")
            tr_lay = QVBoxLayout(tr_card)
            tr_lay.setContentsMargins(12, 6, 12, 8)
            tr_lay.setSpacing(6)

            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)

            tr_icon = QLabel()
            tr_icon.setFixedSize(16, 16)
            tr_icon.setAlignment(Qt.AlignCenter)
            tr_icon.setStyleSheet("border: none; background: transparent;")
            set_label_icon(tr_icon, "data_usage", accent_hex, 16)
            head.addWidget(tr_icon)

            lbl_tr_title = QLabel(tr("sub.traffic_title"))
            lbl_tr_title.setStyleSheet(f"font-size: 10px; font-weight: bold; color: {muted_hex}; border: none; background: transparent;")
            head.addWidget(lbl_tr_title)
            head.addStretch()

            used_bytes = traffic_meta.get("used", 0)
            total_bytes = traffic_meta.get("total", 0)
            used_gb = used_bytes / (1024 ** 3)
            total_gb = total_bytes / (1024 ** 3)
            used_str = f"{used_gb:.2f}".rstrip('0').rstrip('.') if used_gb % 1 else f"{int(used_gb)}"
            total_str = f"{total_gb:.2f}".rstrip('0').rstrip('.') if total_gb % 1 else f"{int(total_gb)}"

            self.lbl_tr_val = QLabel(f"{used_str} GB / {total_str} GB")
            self.lbl_tr_val.setStyleSheet(f"font-size: 12px; color: {accent_hex}; font-weight: bold; border: none; background: transparent;")
            head.addWidget(self.lbl_tr_val)
            tr_lay.addLayout(head)

            pct = min(100.0, max(0.0, (used_bytes / total_bytes) * 100.0)) if total_bytes > 0 else 0.0
            self.bar_traffic = WavyLinearProgressBar(self.theme)
            self.bar_traffic.set_value(pct / 100.0)
            tr_lay.addWidget(self.bar_traffic)
            lay.addWidget(tr_card)

        if self.sub.get("url"):
            lbl_url = QLabel(tr("sub.url_label"))
            lbl_url.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {muted_hex}; border: none; background: transparent;")
            lay.addWidget(lbl_url)

            self.edit_url = QLineEdit(self.sub.get("url", ""))
            self.edit_url.setFixedHeight(40)
            self.edit_url.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {surface_high};
                    color: {text_hex};
                    border-radius: 12px;
                    padding: 0 12px;
                    font-size: 12.5px;
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            lay.addWidget(self.edit_url)
        else:
            self.edit_url = None

        act_row = QHBoxLayout()
        act_row.setContentsMargins(0, 4, 0, 0)
        act_row.setSpacing(8)

        if self.sub.get("url"):
            support_url = (self.sub.get("support_url") or "").strip()
            web_url = (self.sub.get("profile_web_page_url") or "").strip()

            self.btn_tg = QPushButton()
            self.btn_tg.setFixedSize(38, 38)
            self.btn_tg.setCursor(Qt.PointingHandCursor if support_url else Qt.ArrowCursor)
            self.btn_tg.setEnabled(bool(support_url))
            self.btn_tg.setToolTip(f"{tr('sub.support_tg')}: {support_url}" if support_url else tr("sub.no_support_url"))
            ic_tg = icon("telegram", text_hex if support_url else muted_hex, 20)
            if ic_tg:
                self.btn_tg.setIcon(ic_tg)
                self.btn_tg.setIconSize(QSize(20, 20))
            self.btn_tg.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.08);
                }
                QPushButton:pressed {
                    background-color: rgba(255, 255, 255, 0.14);
                }
                QPushButton:disabled {
                    background: transparent;
                    border: none;
                }
            """)
            if support_url:
                self.btn_tg.clicked.connect(lambda: self._open_external_url(support_url))
            act_row.addWidget(self.btn_tg)

            self.btn_web = QPushButton()
            self.btn_web.setFixedSize(38, 38)
            self.btn_web.setCursor(Qt.PointingHandCursor if web_url else Qt.ArrowCursor)
            self.btn_web.setEnabled(bool(web_url))
            self.btn_web.setToolTip(f"{tr('sub.website')}: {web_url}" if web_url else tr("sub.no_website_url"))
            ic_web = icon("web", text_hex if web_url else muted_hex, 20)
            if ic_web:
                self.btn_web.setIcon(ic_web)
                self.btn_web.setIconSize(QSize(20, 20))
            self.btn_web.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.08);
                }
                QPushButton:pressed {
                    background-color: rgba(255, 255, 255, 0.14);
                }
                QPushButton:disabled {
                    background: transparent;
                    border: none;
                }
            """)
            if web_url:
                self.btn_web.clicked.connect(lambda: self._open_external_url(web_url))
            act_row.addWidget(self.btn_web)

        act_row.addStretch()

        btn_cancel = QPushButton(tr("dlg.cancel"))
        btn_cancel.setFixedHeight(38)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(f"border: none; color: {muted_hex}; font-size: 13px; font-weight: 600; padding: 0 16px;")
        btn_cancel.clicked.connect(self.reject)
        act_row.addWidget(btn_cancel)

        btn_save = QPushButton(tr("dlg.save"))
        btn_save.setFixedHeight(38)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent_hex};
                color: #000000;
                border-radius: 12px;
                font-weight: bold;
                font-size: 13px;
                padding: 0 22px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.85);
            }}
        """)
        btn_save.clicked.connect(self._save)
        act_row.addWidget(btn_save)

        lay.addLayout(act_row)
        outer.addWidget(self.card)

    def _update_card_geometry(self):
        if not self._is_overlay:
            return
        parent_w = self._target_parent.width() if self._target_parent else 420
        card_w = min(420, max(280, parent_w - 32)) if parent_w > 0 else 420
        self.card.setFixedWidth(card_w)

    def get_dialog_progress(self) -> float:
        return self._dialog_progress

    def set_dialog_progress(self, val: float):
        self._dialog_progress = max(0.0, min(1.0, float(val)))
        self._scrim_opacity = self._dialog_progress
        if hasattr(self, "card_opacity_effect"):
            self.card_opacity_effect.setOpacity(self._dialog_progress)
        offset = round(14 * (1.0 - self._dialog_progress))
        if hasattr(self, "outer_layout"):
            self.outer_layout.setContentsMargins(16, 16 + offset, 16, 16 - offset)
        self.update()

    dialog_progress = Property(float, get_dialog_progress, set_dialog_progress)

    def get_scrim_opacity(self) -> float:
        return self._scrim_opacity

    def set_scrim_opacity(self, val: float):
        self._scrim_opacity = max(0.0, min(1.0, float(val)))
        self.update()

    scrim_opacity = Property(float, get_scrim_opacity, set_scrim_opacity)

    def eventFilter(self, watched, event):
        target = getattr(self, "_target_parent", None)
        if target is not None and watched == target and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.setGeometry(target.rect())
            self._update_card_geometry()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        if self._is_overlay:
            alpha = int(140 * self._scrim_opacity)
            if alpha > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(self.rect(), 32, 32)
        else:
            super().paintEvent(event)

    def mousePressEvent(self, event):
        if self._is_overlay:
            if event.button() == Qt.LeftButton:
                if not self.card.geometry().contains(event.position().toPoint()):
                    self.reject()
                    event.accept()
                    return
        super().mousePressEvent(event)

    def accept(self):
        super().accept()
        if self._is_overlay:
            self._finish_overlay(animate=True)

    def reject(self):
        super().reject()
        if self._is_overlay:
            self._finish_overlay(animate=True)

    def _finish_overlay(self, animate: bool = True):
        if getattr(self, "_is_finishing", False):
            return
        self._is_finishing = True

        def _cleanup():
            if self._target_parent:
                try:
                    self._target_parent.removeEventFilter(self)
                except Exception:
                    pass
            self.hide()
            if self._loop and self._loop.isRunning():
                self._loop.quit()

        if animate and self.isVisible() and self._dialog_progress > 0.05:
            self._anim.stop()
            self._anim.setDuration(140)
            self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
            self._anim.setStartValue(self._dialog_progress)
            self._anim.setEndValue(0.0)
            self._anim.finished.connect(_cleanup)
            self._anim.start()
        else:
            _cleanup()

    def exec(self) -> int:
        if not self._is_overlay:
            return super().exec()

        if self._target_parent:
            self.setGeometry(self._target_parent.rect())
            self._update_card_geometry()
        self.set_dialog_progress(0.0)
        self.show()
        self.raise_()
        self.edit_name.setFocus()

        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

        self._loop = QEventLoop()
        self._loop.exec()
        return self.result()

    exec_ = exec

    def closeEvent(self, event):
        if self._loop and self._loop.isRunning():
            self._loop.quit()
        super().closeEvent(event)

    def hideEvent(self, event):
        if self._loop and self._loop.isRunning():
            self._loop.quit()
        super().hideEvent(event)

    def _open_icon_picker(self):
        dlg = QDialog(self)
        if self._is_overlay:
            dlg.setWindowFlags(Qt.Widget)
            dlg.setAttribute(Qt.WA_TranslucentBackground, True)
            dlg.setGeometry(self.rect())
        else:
            dlg.setWindowFlags(Qt.Dialog)
            configure_window(dlg)
        surface_high = getattr(self.theme, "surface_container_high", "#241F32")

        box = QFrame(dlg)
        box.setStyleSheet(f"background-color: {surface_high}; border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.15);")
        d_lay = QVBoxLayout(box)
        d_lay.setContentsMargins(14, 14, 14, 14)

        t_lbl = QLabel(tr("sub.icon_prompt"), box)
        t_lbl.setStyleSheet(f"color: {self.theme.on_surface}; font-weight: bold; font-size: 14px; border: none; background: transparent;")
        d_lay.addWidget(t_lbl)

        grid = QGridLayout()
        grid.setSpacing(8)

        emojis = random.sample(ALL_EMOJI_POOL, 15)
        for i, em in enumerate(emojis):
            btn = QPushButton(em)
            btn.setFixedSize(48, 48)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.08);
                    border-radius: 12px;
                    font-size: 24px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.18);
                }
            """)
            btn.clicked.connect(lambda _, e=em: self._select_icon(e, dlg))
            grid.addWidget(btn, i // 5, i % 5)

        d_lay.addLayout(grid)

        o_lay = QVBoxLayout(dlg)
        if self._is_overlay:
            o_lay.setAlignment(Qt.AlignCenter)
            o_lay.setContentsMargins(16, 16, 16, 16)
            dlg.mousePressEvent = lambda e: dlg.reject() if not box.geometry().contains(e.position().toPoint()) else None
        else:
            o_lay.setContentsMargins(10, 10, 10, 10)
        o_lay.addWidget(box)
        dlg.exec()

    def _select_icon(self, emoji: str, dlg: QDialog):
        self.selected_icon = emoji
        self.btn_icon.setText(emoji)
        dlg.accept()

    def _open_external_url(self, raw_url: str):
        if not raw_url:
            return
        url_str = raw_url.strip()
        if url_str.startswith("@"):
            url_str = f"https://t.me/{url_str[1:]}"
        elif not url_str.startswith(("http://", "https://", "tg://")):
            url_str = f"https://{url_str}"
        QDesktopServices.openUrl(QUrl(url_str))

    def _share_sub(self):
        url = self.sub.get("url", "")
        if url:
            QApplication.clipboard().setText(url)
            MessageBox.success(self, tr("sub.shared_title"), tr("sub.shared_done"), theme=self.theme)

    def _toggle_pin(self):
        current = not self.sub.get("is_pinned", False)
        self.sub["is_pinned"] = current
        if getattr(self, "btn_pin", None):
            self.btn_pin.setText(tr("sub.unpin") if current else tr("sub.pin"))
            ic_pin = icon("pin", getattr(self.theme, "on_surface", "#E6E0E9"), 16)
            if ic_pin:
                self.btn_pin.setIcon(ic_pin)
                self.btn_pin.setIconSize(QSize(16, 16))
        if self.manager:
            self.manager.set_pinned(self.sub.get("name", ""), current)

    def _delete_sub(self):
        reply = MessageBox.question(
            self,
            tr("sub.delete_title"),
            tr("sub.delete_confirm").format(name=self.sub.get('name')),
            MessageBox.Yes | MessageBox.No,
            MessageBox.No,
            theme=self.theme,
        )
        if reply == MessageBox.Yes:
            self.manager.delete(self.sub.get("name", ""))
            self.deleted = True
            self.accept()

    def _save(self):
        new_name = self.edit_name.text().strip()
        if not new_name:
            new_name = self.sub.get("name", "")

        new_url = self.edit_url.text().strip() if self.edit_url else ""
        old_name = self.sub.get("name", "")
        allow_insecure = self.sw_insecure.isChecked() if hasattr(self, "sw_insecure") else None

        if self.manager:
            self.manager.rename(old_name, new_name, self.selected_icon, new_url, allow_insecure=allow_insecure)
        if allow_insecure is not None:
            self.sub["allow_insecure"] = allow_insecure
        self.accept()
