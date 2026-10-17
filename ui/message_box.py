"""Material Design 3 (Material You) Custom MessageBox for Socksicle."""

import logging
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QMessageBox,
)

from ui.message_box_types import (
    BadgeWidget,
    ButtonRole,
    Icon,
    StandardButton,
    _BUTTON_ORDER,
    _STANDARD_BUTTON_DEFS,
    apply_button_style,
    get_card_stylesheet,
    get_icon_style,
    resolve_theme,
)
from utils.i18n import tr
from utils.window_utils import configure_window

log = logging.getLogger(__name__)

_ORIG_QMB = {
    Icon.Warning: QMessageBox.warning,
    Icon.Question: QMessageBox.question,
    Icon.Information: QMessageBox.information,
    Icon.Critical: QMessageBox.critical,
}


class MessageBox(QDialog):
    """Material Design 3 dialog for alerts, confirmations, and prompts."""

    StandardButton = StandardButton
    ButtonRole = ButtonRole
    Icon = Icon

    def __init__(self, parent=None, icon: Icon = Icon.NoIcon, title: str = "", text: str = "",
                 buttons=StandardButton.NoButton, theme=None):
        super().__init__(parent)
        self.theme = resolve_theme(parent, theme)
        self._icon_type = icon
        self._custom_pixmap = None
        self._title = title
        self._buttons: list[QPushButton] = []
        self._button_to_standard: dict[QPushButton, StandardButton] = {}
        self._standard_to_button: dict[StandardButton, QPushButton] = {}
        self._button_roles: dict[QPushButton, ButtonRole] = {}
        self._default_button: QPushButton | None = None
        self._clicked_button: QPushButton | None = None
        self._result_button = StandardButton.NoButton
        self._dragging = False
        self._drag_pos = None

        self.setWindowFlags(Qt.Dialog)
        configure_window(self)
        self.setModal(True)
        self.setMinimumWidth(400)

        self._setup_ui()
        if title:
            self.setWindowTitle(title)
        if text:
            self.setText(text)
        if icon != Icon.NoIcon:
            self.setIcon(icon)
        if buttons != StandardButton.NoButton:
            self.setStandardButtons(buttons)
        if self.theme:
            self.apply_theme(self.theme)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)

        self.card = QFrame(self)
        self.card.setObjectName("msgBoxCard")
        root_layout.addWidget(self.card)

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(22, 20, 22, 20)
        self.card_layout.setSpacing(14)

        self.hdr_layout = QHBoxLayout()
        self.hdr_layout.setContentsMargins(0, 0, 0, 0)
        self.hdr_layout.setSpacing(12)

        self.badge = BadgeWidget(self.card)
        self.badge.setVisible(False)
        self.hdr_layout.addWidget(self.badge)

        self.lbl_title = QLabel(self.card)
        self.lbl_title.setObjectName("msgBoxTitle")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.hdr_layout.addWidget(self.lbl_title, 1)

        self.btn_close_x = QPushButton("✕", self.card)
        self.btn_close_x.setObjectName("msgBoxCloseBtn")
        self.btn_close_x.setFixedSize(28, 28)
        self.btn_close_x.setCursor(Qt.PointingHandCursor)
        self.btn_close_x.clicked.connect(self._on_close_clicked)
        self.hdr_layout.addWidget(self.btn_close_x)
        self.card_layout.addLayout(self.hdr_layout)

        self.lbl_text = QLabel(self.card)
        self.lbl_text.setObjectName("msgBoxText")
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_text.setOpenExternalLinks(True)
        self.lbl_text.setVisible(False)
        self.card_layout.addWidget(self.lbl_text)

        self.txt_informative = QTextEdit(self.card)
        self.txt_informative.setObjectName("msgBoxInformative")
        self.txt_informative.setReadOnly(True)
        self.txt_informative.setMaximumHeight(140)
        self.txt_informative.setVisible(False)
        self.card_layout.addWidget(self.txt_informative)

        self.btn_layout = QHBoxLayout()
        self.btn_layout.setContentsMargins(0, 4, 0, 0)
        self.btn_layout.setSpacing(8)
        self.btn_layout.addStretch(1)
        self.card_layout.addLayout(self.btn_layout)

    def _update_geometry(self):
        """Adapt dialog width and height to fit title, text, and action buttons without clipping."""
        target_w = 420
        title_text = self._title or self.lbl_title.text()
        if title_text:
            fm = self.lbl_title.fontMetrics()
            title_advance = fm.horizontalAdvance(title_text)
            badge_w = (40 + 12) if (hasattr(self, "badge") and self.badge.isVisible()) else 0
            overhead = badge_w + 28 + 12 + 44 + 24 + 16
            needed_w = title_advance + overhead
            target_w = max(target_w, min(needed_w, 540))

        if self._buttons:
            btn_w = sum(b.sizeHint().width() for b in self._buttons) + (len(self._buttons) - 1) * 8 + 44 + 24 + 12
            target_w = max(target_w, min(btn_w, 560))

        self.setFixedWidth(target_w)
        self.adjustSize()

    def setWindowTitle(self, title: str):
        super().setWindowTitle(title)
        self._title = title
        self.lbl_title.setText(title)
        self._update_geometry()

    setTitle = setWindowTitle

    def setText(self, text: str):
        self.lbl_text.setText(text)
        self.lbl_text.setVisible(bool(text))
        self._update_geometry()

    def setInformativeText(self, text: str):
        self.txt_informative.setPlainText(text)
        self.txt_informative.setVisible(bool(text))
        self._update_geometry()

    def setIcon(self, icon: Icon | int):
        self._icon_type = Icon(int(icon))
        self._update_icon()
        self._update_geometry()

    def setIconPixmap(self, pixmap: QPixmap):
        self._custom_pixmap = pixmap
        self.badge.setVisible(True)
        self.badge.set_badge("transparent", "transparent", pixmap=pixmap)
        self._update_geometry()

    def _update_icon(self):
        if self._custom_pixmap and not self._custom_pixmap.isNull():
            self.badge.setVisible(True)
            self.badge.set_badge("transparent", "transparent", pixmap=self._custom_pixmap)
            return
        if self._icon_type == Icon.NoIcon:
            self.badge.setVisible(False)
            return

        self.badge.setVisible(True)
        bg, fg, ic_name, sym = get_icon_style(self._icon_type, self.theme)
        self.badge.set_badge(bg, fg, symbol=sym, icon_name=ic_name)


    def setStandardButtons(self, buttons):
        for btn in list(self._buttons):
            self.removeButton(btn)

        mask = int(buttons) if isinstance(buttons, (int, StandardButton)) else 0
        btn_list = buttons if isinstance(buttons, (list, tuple, set)) else []

        for btn_enum in _BUTTON_ORDER:
            should_add = (btn_enum in btn_list or int(btn_enum) in btn_list) if btn_list else bool(mask & int(btn_enum))
            if should_add:
                self._add_standard_button(btn_enum)

        if not self._default_button:
            for pref in (StandardButton.Ok, StandardButton.Yes, StandardButton.Save, StandardButton.Close):
                if pref in self._standard_to_button:
                    self.setDefaultButton(pref)
                    break

    def _add_standard_button(self, standard_button: StandardButton) -> QPushButton:
        btn_def = _STANDARD_BUTTON_DEFS.get(standard_button)
        if btn_def:
            i18n_key, fallback_text, role = btn_def
            text = tr(i18n_key) if i18n_key else fallback_text
            if text == i18n_key:
                text = fallback_text
        else:
            text = standard_button.name
            role = ButtonRole.ActionRole

        btn = QPushButton(text, self.card)
        self._add_button_internal(btn, role, standard_button=standard_button)
        return btn

    def addButton(self, button_or_text, role: ButtonRole = ButtonRole.ActionRole) -> QPushButton:
        btn = QPushButton(button_or_text, self.card) if isinstance(button_or_text, str) else button_or_text
        self._add_button_internal(btn, role)
        return btn

    def _add_button_internal(self, btn: QPushButton, role: ButtonRole, standard_button: StandardButton | None = None):
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._on_button_clicked(btn))
        self._buttons.append(btn)
        self._button_roles[btn] = role
        if standard_button is not None:
            self._button_to_standard[btn] = standard_button
            self._standard_to_button[standard_button] = btn
        self.btn_layout.addWidget(btn)
        if self.theme:
            apply_button_style(btn, role, self._default_button == btn, self.theme)
        self._update_geometry()

    def removeButton(self, btn: QPushButton):
        if btn in self._buttons:
            self._buttons.remove(btn)
            self.btn_layout.removeWidget(btn)
            std = self._button_to_standard.pop(btn, None)
            if std:
                self._standard_to_button.pop(std, None)
            self._button_roles.pop(btn, None)
            if self._default_button == btn:
                self._default_button = None
            btn.deleteLater()
            self._update_geometry()

    def button(self, which: StandardButton | int) -> QPushButton | None:
        try:
            return self._standard_to_button.get(StandardButton(int(which)))
        except (ValueError, TypeError):
            return None

    def standardButton(self, button: QPushButton) -> StandardButton:
        return self._button_to_standard.get(button, StandardButton.NoButton)

    def buttonRole(self, button: QPushButton) -> ButtonRole:
        return self._button_roles.get(button, ButtonRole.InvalidRole)

    def clickedButton(self) -> QPushButton | None:
        return self._clicked_button

    def setDefaultButton(self, button):
        btn = self.button(button) if isinstance(button, (int, StandardButton)) else button
        self._default_button = btn
        if btn is not None:
            btn.setDefault(True)
            btn.setFocus()
        if self.theme:
            self._update_button_styles()

    def _on_button_clicked(self, btn: QPushButton):
        self._clicked_button = btn
        std = self._button_to_standard.get(btn, StandardButton.NoButton)
        self._result_button = std
        role = self._button_roles.get(btn, ButtonRole.ActionRole)
        if role in (ButtonRole.RejectRole, ButtonRole.DestructiveRole, ButtonRole.NoRole) or std in (StandardButton.Cancel, StandardButton.No):
            self.reject()
        else:
            self.accept()

    def _on_close_clicked(self):
        for candidate in (StandardButton.Cancel, StandardButton.Close, StandardButton.No):
            btn = self._standard_to_button.get(candidate)
            if btn:
                self._on_button_clicked(btn)
                return
        self.reject()

    def exec(self) -> int:
        super().exec()
        if self._result_button != StandardButton.NoButton:
            return int(self._result_button)
        prefs = ((StandardButton.Ok, StandardButton.Yes, StandardButton.Save)
                 if self.result() == QDialog.Accepted else
                 (StandardButton.Cancel, StandardButton.Close, StandardButton.No))
        for pref in prefs:
            if pref in self._standard_to_button:
                return int(pref)
        return int(StandardButton.Ok if self.result() == QDialog.Accepted else StandardButton.NoButton)

    exec_ = exec

    def apply_theme(self, theme):
        self.theme = theme
        self.card.setStyleSheet(get_card_stylesheet(theme))
        self._update_icon()
        self._update_button_styles()

    def _update_button_styles(self):
        for btn in self._buttons:
            role = self._button_roles.get(btn, ButtonRole.ActionRole)
            apply_button_style(btn, role, self._default_button == btn, self.theme)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not isinstance(self.childAt(event.position().toPoint()), (QPushButton, QTextEdit)):
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._on_close_clicked()
            event.accept()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._default_button and self._default_button.isEnabled():
            self._default_button.click()
            event.accept()
        else:
            super().keyPressEvent(event)

    @classmethod
    def _show_box(cls, icon: Icon, default_title_key: str, parent=None,
                  title: str = "", text: str = "", buttons=StandardButton.Ok,
                  default_button=StandardButton.NoButton,
                  informative_text: str = "", theme=None) -> StandardButton:
        curr_fn = {Icon.Warning: QMessageBox.warning, Icon.Question: QMessageBox.question,
                   Icon.Information: QMessageBox.information, Icon.Critical: QMessageBox.critical}.get(icon)
        if curr_fn and curr_fn is not _ORIG_QMB.get(icon):
            try:
                return curr_fn(parent, title, text, buttons, default_button)
            except TypeError:
                return curr_fn(parent, title, text)

        dlg = cls(parent=parent, icon=icon, title=title or tr(default_title_key),
                  text=text, buttons=buttons, theme=theme)
        if informative_text:
            dlg.setInformativeText(informative_text)
        if default_button != StandardButton.NoButton:
            dlg.setDefaultButton(default_button)
        res = dlg.exec()
        try:
            return StandardButton(res)
        except ValueError:
            return StandardButton(dlg._result_button)

    @classmethod
    def information(cls, parent=None, title: str = "", text: str = "",
                    buttons=StandardButton.Ok, default_button=StandardButton.NoButton,
                    informative_text: str = "", theme=None) -> StandardButton:
        return cls._show_box(Icon.Information, "dlg.info", parent, title, text, buttons, default_button, informative_text, theme)

    @classmethod
    def warning(cls, parent=None, title: str = "", text: str = "",
                buttons=StandardButton.Ok, default_button=StandardButton.NoButton,
                informative_text: str = "", theme=None) -> StandardButton:
        return cls._show_box(Icon.Warning, "dlg.warning", parent, title, text, buttons, default_button, informative_text, theme)

    @classmethod
    def critical(cls, parent=None, title: str = "", text: str = "",
                 buttons=StandardButton.Ok, default_button=StandardButton.NoButton,
                 informative_text: str = "", theme=None) -> StandardButton:
        return cls._show_box(Icon.Critical, "dlg.error", parent, title, text, buttons, default_button, informative_text, theme)

    error = critical

    @classmethod
    def question(cls, parent=None, title: str = "", text: str = "",
                 buttons=StandardButton.Yes | StandardButton.No, default_button=StandardButton.NoButton,
                 informative_text: str = "", theme=None) -> StandardButton:
        return cls._show_box(Icon.Question, "dlg.confirm", parent, title, text, buttons, default_button, informative_text, theme)

    @classmethod
    def success(cls, parent=None, title: str = "", text: str = "",
                buttons=StandardButton.Ok, default_button=StandardButton.NoButton,
                informative_text: str = "", theme=None) -> StandardButton:
        return cls._show_box(Icon.Success, "dlg.success", parent, title, text, buttons, default_button, informative_text, theme)


# Expose enum members as direct attributes on MessageBox for drop-in compatibility
for _enum_cls in (StandardButton, ButtonRole, Icon):
    for _name, _val in _enum_cls.__members__.items():
        setattr(MessageBox, _name, _val)
