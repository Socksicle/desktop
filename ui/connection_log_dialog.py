import collections
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout,
    QFrame, QLabel
)
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt

from utils.icons import set_button_icon as _set_btn_icon
from utils.window_utils import configure_window, start_window_move
from utils.i18n import tr

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
MAX_LOG_BLOCKS = 1000


class ConnectionLogDialog(QDialog):
    """Standalone Material 3 Connection Log Viewer."""

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self.theme = theme
        self._dragging = False
        self._drag_pos = None
        self._log_buffer = collections.deque(maxlen=MAX_LOG_BLOCKS)
        self._dirty = False

        # Standalone frameless window with independent taskbar presence
        self.setWindowFlags(Qt.Window)
        configure_window(self)
        self.resize(560, 520)
        self.setMinimumSize(440, 360)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 12, 12, 12)

        self.container = QFrame()
        self.main_layout.addWidget(self.container)

        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(20, 16, 20, 20)
        self.container_layout.setSpacing(12)

        self.header_layout = QHBoxLayout()
        self.title_label = QLabel(tr("log.title"))
        self.title_label.setFocusPolicy(Qt.NoFocus)
        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch()

        self.close_hdr_btn = QPushButton("✕")
        self.close_hdr_btn.setFocusPolicy(Qt.NoFocus)
        self.close_hdr_btn.setFixedSize(32, 32)
        self.close_hdr_btn.clicked.connect(self.hide)
        self.header_layout.addWidget(self.close_hdr_btn)
        self.container_layout.addLayout(self.header_layout)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.container_layout.addWidget(self.log_text)

        self.button_layout = QHBoxLayout()
        self.button_layout.setContentsMargins(0, 4, 0, 0)

        self.clear_button = QPushButton(tr("dlg.clear"))
        self.clear_button.setFocusPolicy(Qt.NoFocus)
        self.clear_button.clicked.connect(self.clear_log)
        self.button_layout.addWidget(self.clear_button)

        self.button_layout.addStretch()

        self.close_button = QPushButton(tr("dlg.close"))
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.hide)
        self.button_layout.addWidget(self.close_button)

        self.container_layout.addLayout(self.button_layout)

        if theme:
            self.set_theme(theme)

    def set_theme(self, theme):
        """Update window and widget styling when theme changes."""
        self.theme = theme
        self.container.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.surface};
                border-radius: 28px;
                border: none;
            }}
        """)
        self.title_label.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.on_surface}; border: none; outline: none;"
        )
        self.close_hdr_btn.setStyleSheet(f"""
            QPushButton {{
                color: {theme.on_surface_variant};
                background: transparent;
                border-radius: 16px;
                border: none;
                font-size: 14px;
                font-weight: bold;
                outline: none;
            }}
            QPushButton:hover {{
                background: {getattr(theme, 'surface_container_highest', theme.surface_variant)};
                color: {theme.on_surface};
            }}
        """)
        self.log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {getattr(theme, 'surface_container_lowest', theme.surface_variant)};
                color: {theme.on_surface};
                border: 1px solid {getattr(theme, 'outline_variant', '#44424B')};
                border-radius: 14px;
                padding: 12px;
                font-family: 'Consolas', 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 12px;
                line-height: 1.4;
                outline: none;
            }}
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {getattr(theme, 'surface_container_highest', theme.surface_variant)};
                min-height: 24px;
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self.clear_button.setStyleSheet(theme.get_button_style("tonal"))
        self.close_button.setStyleSheet(theme.get_button_style("filled"))
        _set_btn_icon(self.close_hdr_btn, "close",
                      theme.on_surface_variant, 15, "✕")

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self.log_text.setPlainText("\n".join(self._log_buffer))
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_text.setTextCursor(cursor)
            self._dirty = False

    def add_log(self, message):
        clean_msg = _ANSI_RE.sub('', message)
        self._log_buffer.append(clean_msg)
        if self.isVisible():
            self.log_text.append(clean_msg)
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_text.setTextCursor(cursor)
        else:
            self._dirty = True

    def clear_log(self):
        self._log_buffer.clear()
        self.log_text.clear()
        self._dirty = False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 60:
            if start_window_move(self):
                return
            self._dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._dragging and self._drag_pos is not None:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._dragging = False

