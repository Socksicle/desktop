"""Top window chrome: application title and window close button only."""
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton
)
from PySide6.QtCore import Qt, Signal
from utils.i18n import tr
from utils.icons import set_button_icon

log = logging.getLogger(__name__)


class HeaderBar(QWidget):
    """Top window chrome: application title and window close button only."""
    closeRequested = Signal()

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._icon_widgets: list = []
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._setup_ui()
        self.apply_theme(theme)

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.row = QFrame()
        self.row.setObjectName("headerRow")
        outer.addWidget(self.row)

        header = QHBoxLayout(self.row)
        header.setContentsMargins(20, 12, 20, 12)
        header.setSpacing(6)

        self.title_label = QLabel("Socksicle")
        self.title_label.setFocusPolicy(Qt.NoFocus)
        header.addWidget(self.title_label, 1)

        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(38, 38)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip(tr("header.close_tooltip"))
        self._icon_widgets.append((self.close_btn, "close", 18, "text", "✕"))
        self.close_btn.clicked.connect(self.closeRequested.emit)
        header.addWidget(self.close_btn)

    def _refresh_icons(self):
        """Re-tint close icon for active theme."""
        colors = {
            "text": getattr(self.theme, "on_surface", "#E6E0E9"),
            "muted": getattr(self.theme, "outline", "#938F99"),
            "accent": getattr(self.theme, "primary", "#D0BCFF"),
        }
        for widget, name, px, role, fallback in self._icon_widgets:
            color = colors.get(role, colors["text"])
            set_button_icon(widget, name, color, px, fallback)

    def set_title(self, title: str):
        self.title_label.setText(title)

    def set_actions_visible(self, visible: bool):
        pass

    def apply_theme(self, theme):
        self.theme = theme
        text_hex = getattr(theme, "on_surface", "#E6E0E9")
        surface_hex = getattr(theme, "surface", "#141218")
        surface_high = getattr(theme, "surface_container_high", "#241F32")
        surface_el = getattr(theme, "surface_container", surface_high)

        self.row.setStyleSheet(f"""
            QFrame#headerRow {{
                background-color: {surface_hex};
                border-top-left-radius: 32px;
                border-top-right-radius: 32px;
                border: none;
            }}
        """)
        self.title_label.setStyleSheet(
            f"color: {text_hex}; font-size: 24px; font-weight: bold; "
            f"background: transparent; border: none; outline: none;")
        self._refresh_icons()

        close_style = f"""
            QPushButton {{
                color: {text_hex};
                background: {surface_el};
                border-radius: 14px;
                border: none;
                font-size: 13px;
                font-weight: bold;
                outline: none;
            }}
            QPushButton:hover {{
                background: rgba(255, 80, 80, 0.25);
                color: #FF8A80;
                border: none;
            }}
            QToolTip {{
                background-color: {surface_el};
                color: {text_hex};
                border: none;
                border-radius: 8px;
                padding: 5px 9px;
                font-size: 11.5px;
            }}
        """
        self.close_btn.setStyleSheet(close_style)
