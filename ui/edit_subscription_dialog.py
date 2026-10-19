"""Edit subscription dialog (name, icon, URL, expiry, traffic)."""

import random
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QGridLayout, QApplication
)
from PySide6.QtCore import Qt, QSize
from ui.message_box import MessageBox
from ui.wavy_progress_bar import WavyLinearProgressBar
from utils.window_utils import configure_window
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
        super().__init__(parent)
        self.sub = sub_dict
        self.manager = sub_manager
        self.theme = theme
        self.selected_icon = sub_dict.get("icon_emoji", "🌐")
        self.deleted = False
        self.setWindowFlags(Qt.Dialog)
        configure_window(self)
        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(420)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

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
            btn_share = QPushButton(tr("sub.copy_link"))
            btn_share.setFixedHeight(38)
            btn_share.setCursor(Qt.PointingHandCursor)
            ic_copy = icon("copy", text_hex, 16)
            if ic_copy:
                btn_share.setIcon(ic_copy)
                btn_share.setIconSize(QSize(16, 16))
            btn_share.setStyleSheet(f"""
                QPushButton {{
                    background-color: {surface_high};
                    color: {text_hex};
                    border-radius: 12px;
                    font-weight: 600;
                    font-size: 12.5px;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.12);
                }}
            """)
            btn_share.clicked.connect(self._share_sub)
            lay.addWidget(btn_share)

        is_pinned = self.sub.get("is_pinned", False)
        self.btn_pin = QPushButton(tr("sub.unpin") if is_pinned else tr("sub.pin"))
        self.btn_pin.setFixedHeight(38)
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        ic_pin = icon("pin", text_hex, 16)
        if ic_pin:
            self.btn_pin.setIcon(ic_pin)
            self.btn_pin.setIconSize(QSize(16, 16))
        self.btn_pin.setStyleSheet(f"""
            QPushButton {{
                background-color: {surface_high};
                color: {text_hex};
                border-radius: 12px;
                font-weight: 600;
                font-size: 12.5px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.12);
            }}
        """)
        self.btn_pin.clicked.connect(self._toggle_pin)
        lay.addWidget(self.btn_pin)

        if self.sub.get("url"):
            btn_del = QPushButton(tr("sub.delete"))
            btn_del.setFixedHeight(38)
            btn_del.setCursor(Qt.PointingHandCursor)
            ic_del = icon("delete", "#FF6B6B", 16)
            if ic_del:
                btn_del.setIcon(ic_del)
                btn_del.setIconSize(QSize(16, 16))
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 51, 51, 0.12);
                    color: #FF6B6B;
                    border-radius: 12px;
                    font-weight: 600;
                    font-size: 12.5px;
                    border: 1px solid rgba(255, 82, 82, 0.25);
                }
                QPushButton:hover {
                    background-color: rgba(255, 51, 51, 0.22);
                }
            """)
            btn_del.clicked.connect(self._delete_sub)
            lay.addWidget(btn_del)

        act_row = QHBoxLayout()
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

    def _open_icon_picker(self):
        dlg = QDialog(self)
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
        o_lay.setContentsMargins(10, 10, 10, 10)
        o_lay.addWidget(box)
        dlg.exec()

    def _select_icon(self, emoji: str, dlg: QDialog):
        self.selected_icon = emoji
        self.btn_icon.setText(emoji)
        dlg.accept()

    def _share_sub(self):
        url = self.sub.get("url", "")
        if url:
            QApplication.clipboard().setText(url)
            MessageBox.success(self, tr("sub.shared_title"), tr("sub.shared_done"), theme=self.theme)

    def _toggle_pin(self):
        current = not self.sub.get("is_pinned", False)
        self.sub["is_pinned"] = current
        self.btn_pin.setText(tr("sub.unpin") if current else tr("sub.pin"))
        ic_pin = icon("pin", getattr(self.theme, "on_surface", "#E6E0E9"), 16)
        if ic_pin:
            self.btn_pin.setIcon(ic_pin)
            self.btn_pin.setIconSize(QSize(16, 16))
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

        self.manager.rename(old_name, new_name, self.selected_icon, new_url)
        self.accept()
