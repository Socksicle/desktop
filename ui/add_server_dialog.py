from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QFrame,
    QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QEventLoop, QEvent, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor

from utils.i18n import tr


_PROTOCOL_HINTS = {
    "ss://": "Shadowsocks",
    "vless://": "VLESS",
    "vmess://": "VMess",
    "trojan://": "Trojan",
    "trojan-go://": "Trojan",
    "tuic://": "TUIC",
    "hysteria2://": "Hysteria 2",
    "hy2://": "Hysteria 2",
    "tws3://": "TwinSock Share",
    "https://": "Subscription URL",
    "http://": "Subscription URL",
}


class AddServerDialog(QWidget):
    """In-app modal dialog overlay for adding server links.

    Renders directly inside the application container instead of creating a
    separate top-level OS window, ensuring seamless integration on tiling WMs
    and Wayland compositors.
    """
    Accepted = 1
    Rejected = 0

    def __init__(self, parent=None, theme=None):
        target_parent = parent.container if parent and hasattr(parent, "container") else parent
        super().__init__(target_parent)
        self._target_parent = target_parent

        if theme is None:
            from utils.theme import M3Theme
            theme = getattr(parent, "theme", None) or M3Theme()
        self.theme = theme

        self._result = self.Rejected
        self._loop = None
        self._scrim_opacity = 0.0
        self._dialog_progress = 0.0

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if self._target_parent:
            self.setGeometry(self._target_parent.rect())
            self._target_parent.installEventFilter(self)

        self._anim = QPropertyAnimation(self, b"dialog_progress")
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignCenter)
        self.main_layout.setContentsMargins(20, 20, 20, 20)

        self.container = QFrame(self)
        self.card = self.container
        self.container.setObjectName("addServerCard")
        self.card_opacity_effect = QGraphicsOpacityEffect(self.container)
        self.container.setGraphicsEffect(self.card_opacity_effect)
        self._update_card_geometry()

        surface_hex = getattr(self.theme, "surface_container_high", getattr(self.theme, "surface", "#1e1a22"))

        self.container.setStyleSheet(f"""
            QFrame#addServerCard {{
                background-color: {surface_hex};
                border-radius: 28px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            QLabel {{ color: {self.theme.on_surface}; font-size: 13.5px; border: none; }}
            QLineEdit {{
                background-color: {self.theme.surface_variant};
                color: {self.theme.on_surface};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 10px 12px;
                font-size: 13.5px;
            }}
            QLineEdit:focus {{
                border: 1px solid {self.theme.primary};
                background-color: rgba(255, 255, 255, 0.05);
            }}
        """)
        self.main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        title = QLabel(tr("add.title"))
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; margin-bottom: 4px; color: {self.theme.on_surface};")
        layout.addWidget(title)

        hint = QLabel(tr("add.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {getattr(self.theme, 'on_surface_variant', self.theme.on_surface)}; font-size: 13px; line-height: 1.3;")
        layout.addWidget(hint)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(tr("add.placeholder"))
        self.input_field.textChanged.connect(self._on_text_changed)
        self.input_field.returnPressed.connect(self.accept)
        layout.addWidget(self.input_field)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet(
            f"color: {self.theme.on_surface_variant}; font-size: 11px; margin-top: 2px; "
            f"border: none; background: transparent; min-height: 16px;"
        )
        layout.addWidget(self.hint_label)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 10, 0, 0)
        button_layout.setSpacing(10)

        self.cancel_button = QPushButton(tr("dlg.cancel"))
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.setStyleSheet(f"""
            QPushButton {{
                color: {self.theme.primary}; background: transparent; padding: 8px 16px; font-weight: 600; border: none; border-radius: 18px;
            }}
            QPushButton:hover {{ background: rgba(255, 255, 255, 0.08); }}
        """)
        self.cancel_button.clicked.connect(self.reject)

        self.add_button = QPushButton(tr("dlg.add"))
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setStyleSheet(self.theme.get_button_style("filled"))
        self.add_button.clicked.connect(self.accept)

        button_layout.addStretch()
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.add_button)
        layout.addLayout(button_layout)

        self.hide()

    def _update_card_geometry(self):
        w = self._target_parent.width() if self._target_parent else 360
        card_w = min(360, max(260, w - 36)) if w > 0 else 360
        self.container.setFixedWidth(card_w)

    def get_dialog_progress(self) -> float:
        return self._dialog_progress

    def set_dialog_progress(self, val: float):
        self._dialog_progress = max(0.0, min(1.0, float(val)))
        self._scrim_opacity = self._dialog_progress
        if hasattr(self, "card_opacity_effect"):
            self.card_opacity_effect.setOpacity(self._dialog_progress)
        offset = round(14 * (1.0 - self._dialog_progress))
        self.main_layout.setContentsMargins(20, 20 + offset, 20, 20 - offset)
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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.container.geometry().contains(event.position().toPoint()):
                self.reject()
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        alpha = int(140 * self._scrim_opacity)
        if alpha > 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect(), 32, 32)

    def _on_text_changed(self, text):
        text = text.strip().lower()
        detected = None
        for prefix, label in _PROTOCOL_HINTS.items():
            if text.startswith(prefix):
                detected = label
                break
        if detected:
            self.hint_label.setText(tr("add.detected").format(detected=detected))
            self.hint_label.setStyleSheet(
                f"color: {self.theme.primary}; font-size: 11px; font-weight: bold; "
                f"margin-top: 2px; border: none; background: transparent; min-height: 16px;"
            )
        else:
            self.hint_label.setText("")
            self.hint_label.setStyleSheet(
                f"color: {self.theme.on_surface_variant}; font-size: 11px; "
                f"margin-top: 2px; border: none; background: transparent; min-height: 16px;"
            )

    def get_server_key(self) -> str:
        return self.input_field.text().strip()

    def accept(self):
        self._result = self.Accepted
        self._finish()

    def reject(self):
        self._result = self.Rejected
        self._finish()

    def _finish(self, animate: bool = True):
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
        if self._target_parent:
            self.setGeometry(self._target_parent.rect())
            self._update_card_geometry()
        self.set_dialog_progress(0.0)
        self.show()
        self.raise_()
        self.input_field.setFocus()
        self.input_field.selectAll()

        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

        self._loop = QEventLoop()
        self._loop.exec()
        return self._result

    def exec_(self) -> int:
        return self.exec()

    def result(self) -> int:
        return self._result

    def closeEvent(self, event):
        if self._loop and self._loop.isRunning():
            self._loop.quit()
        super().closeEvent(event)

    def hideEvent(self, event):
        if self._loop and self._loop.isRunning():
            self._loop.quit()
        super().hideEvent(event)
