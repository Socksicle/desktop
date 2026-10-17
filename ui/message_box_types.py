"""Enums, constants, styles, and badge rendering for Material 3 MessageBox."""

from enum import IntEnum
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from utils.i18n import tr


class StandardButton(IntEnum):
    NoButton = 0x00000000
    Ok = 0x00000400
    Save = 0x00000800
    SaveAll = 0x00001000
    Open = 0x00002000
    Yes = 0x00004000
    YesToAll = 0x00008000
    No = 0x00010000
    NoToAll = 0x00020000
    Abort = 0x00040000
    Retry = 0x00080000
    Ignore = 0x00100000
    Close = 0x00200000
    Cancel = 0x00400000
    Discard = 0x00800000
    Help = 0x01000000
    Apply = 0x02000000
    Reset = 0x04000000
    RestoreDefaults = 0x08000000


class ButtonRole(IntEnum):
    InvalidRole = -1
    AcceptRole = 0
    RejectRole = 1
    DestructiveRole = 2
    ActionRole = 3
    HelpRole = 4
    YesRole = 5
    NoRole = 6
    ApplyRole = 7
    ResetRole = 8


class Icon(IntEnum):
    NoIcon = 0
    Information = 1
    Warning = 2
    Critical = 3
    Question = 4
    Success = 5


_BUTTON_ORDER = [
    StandardButton.Help, StandardButton.Reset, StandardButton.RestoreDefaults,
    StandardButton.Discard, StandardButton.Abort, StandardButton.Ignore,
    StandardButton.Retry, StandardButton.No, StandardButton.NoToAll,
    StandardButton.Cancel, StandardButton.Close, StandardButton.Apply,
    StandardButton.Save, StandardButton.SaveAll, StandardButton.Open,
    StandardButton.Yes, StandardButton.YesToAll, StandardButton.Ok,
]

_STANDARD_BUTTON_DEFS = {
    StandardButton.Ok: ("dlg.ok", "OK", ButtonRole.AcceptRole),
    StandardButton.Cancel: ("dlg.cancel", "Cancel", ButtonRole.RejectRole),
    StandardButton.Yes: ("dlg.yes", "Yes", ButtonRole.YesRole),
    StandardButton.No: ("dlg.no", "No", ButtonRole.NoRole),
    StandardButton.Close: ("dlg.close", "Close", ButtonRole.RejectRole),
    StandardButton.Apply: ("dlg.apply", "Apply", ButtonRole.ApplyRole),
    StandardButton.Discard: ("dlg.discard", "Discard", ButtonRole.DestructiveRole),
    StandardButton.Save: ("dlg.save", "Save", ButtonRole.AcceptRole),
    StandardButton.Open: ("dlg.open", "Open", ButtonRole.AcceptRole),
}


def resolve_theme(parent=None, theme=None):
    """Obtain active Material 3 theme from parameter, parent, or active window."""
    if theme is not None:
        return theme
    if parent is not None and hasattr(parent, "theme") and parent.theme is not None:
        return parent.theme
    app = QApplication.instance()
    if app is not None:
        active = app.activeWindow()
        if active is not None and hasattr(active, "theme") and active.theme is not None:
            return active.theme
        for top in app.topLevelWidgets():
            if hasattr(top, "theme") and top.theme is not None:
                return top.theme
    try:
        from utils.theme import M3Theme
        return M3Theme()
    except Exception:
        return None


def get_card_stylesheet(theme) -> str:
    """Return styling for the card container and its content labels."""
    surf = getattr(theme, "surface_container", getattr(theme, "surface", "#1A1624"))
    high = getattr(theme, "surface_container_high", "#241F32")
    lowest = getattr(theme, "surface_container_lowest", "#121016")
    txt = getattr(theme, "on_surface", "#E6E0E9")
    muted = getattr(theme, "on_surface_variant", "#CAC4D0")
    outline = getattr(theme, "outline_variant", "#49454F")
    primary = getattr(theme, "primary", "#D0BCFF")
    on_primary = getattr(theme, "on_primary", "#381E72")

    return f"""
        QFrame#msgBoxCard {{
            background-color: {surf};
            border-radius: 28px;
            border: 1px solid rgba(255, 255, 255, 0.10);
        }}
        QLabel#msgBoxTitle {{
            font-size: 17px; font-weight: bold; color: {txt};
            border: none; background: transparent;
        }}
        QLabel#msgBoxText {{
            font-size: 13.5px; color: {muted};
            border: none; background: transparent;
        }}
        QTextEdit#msgBoxInformative {{
            background-color: {lowest}; color: {txt};
            border: 1px solid {outline}; border-radius: 14px; padding: 10px;
            font-family: 'Segoe UI', 'Consolas', sans-serif;
            font-size: 12.5px; outline: none;
            selection-background-color: {primary};
            selection-color: {on_primary};
        }}
        QPushButton#msgBoxCloseBtn {{
            color: {muted}; background: transparent;
            border-radius: 14px; border: none; font-size: 14px; font-weight: bold; outline: none;
        }}
        QPushButton#msgBoxCloseBtn:hover {{
            background-color: {high}; color: {txt};
        }}
    """


def apply_button_style(btn: QPushButton, role: ButtonRole, is_default: bool, theme):
    """Apply role-appropriate Material 3 styling to a dialog action button."""
    if not theme:
        return
    if role == ButtonRole.DestructiveRole:
        err = getattr(theme, "error", "#F2B8B5")
        on_err = getattr(theme, "on_error", "#601410")
        err_box = getattr(theme, "error_container", "#8C1D18")
        on_err_box = getattr(theme, "on_error_container", "#F9DEDC")
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {err_box}; color: {on_err_box};
                border-radius: 18px; padding: 0px 18px; height: 36px;
                font-size: 13px; font-weight: 600;
                border: 1px solid rgba(255, 255, 255, 0.08); outline: none;
            }}
            QPushButton:hover {{ background-color: {err}; color: {on_err}; }}
        """)
    elif is_default or role in (ButtonRole.AcceptRole, ButtonRole.YesRole):
        btn.setStyleSheet(theme.get_button_style("filled"))
    elif role in (ButtonRole.RejectRole, ButtonRole.NoRole):
        btn.setStyleSheet(theme.get_button_style("text"))
    else:
        btn.setStyleSheet(theme.get_button_style("tonal"))


def get_icon_style(icon_type: Icon, theme) -> tuple[str, str, str, str]:
    """Return (bg_color, fg_color, svg_icon_name, fallback_symbol) for an icon type."""
    t = theme
    # Consistent subtle "light black" container across all badges
    badge_bg = getattr(t, "secondary_container", "#4A4458")
    icon_map = {
        Icon.Information: (
            badge_bg,
            getattr(t, "primary", "#D0BCFF"),
            "msg_info",
            "ℹ",
        ),
        Icon.Warning: (
            badge_bg,
            "#FBBF24",
            "msg_warning",
            "⚠",
        ),
        Icon.Critical: (
            badge_bg,
            getattr(t, "error", "#F2B8B5"),
            "msg_error",
            "✕",
        ),
        Icon.Question: (
            badge_bg,
            getattr(t, "on_primary_container", "#EADDFF"),
            "msg_help",
            "?",
        ),
        Icon.Success: (
            badge_bg,
            getattr(t, "success", "#81C784"),
            "msg_success",
            "✓",
        ),
    }
    return icon_map.get(icon_type, ("transparent", "#FFFFFF", "", ""))


def _parse_badge_color(val: str | QColor, fallback: str = "#4A4458") -> QColor:
    if isinstance(val, QColor) and val.isValid():
        return val
    if isinstance(val, str):
        val = val.strip()
        c = QColor(val)
        if c.isValid():
            return c
        if val.startswith("rgba(") and val.endswith(")"):
            try:
                parts = [p.strip() for p in val[5:-1].split(",")]
                r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                a = float(parts[3])
                return QColor(r, g, b, int(a * 255 if a <= 1.0 else a))
            except Exception:
                pass
    return QColor(fallback)


class BadgeWidget(QWidget):
    """Antialiased rounded badge displaying an SVG vector icon or fallback glyph."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self._bg_color = QColor("#4A4458")
        self._fg_color = QColor(255, 255, 255)
        self._symbol = ""
        self._icon_name = ""
        self._pixmap = None

    def set_badge(self, bg_color: str | QColor, fg_color: str | QColor,
                  symbol: str = "", icon_name: str = "", pixmap: QPixmap | None = None):
        self._bg_color = _parse_badge_color(bg_color, "#4A4458")
        self._fg_color = _parse_badge_color(fg_color, "#FFFFFF")
        self._symbol = symbol
        self._icon_name = icon_name
        if pixmap is not None:
            self._pixmap = pixmap
        elif icon_name:
            from utils.icons import pixmap as get_icon_pixmap
            color_str = self._fg_color.name() if isinstance(self._fg_color, QColor) else str(fg_color)
            self._pixmap = get_icon_pixmap(icon_name, color_str, 24)
        else:
            self._pixmap = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        badge_rect = QRectF(0.5, 0.5, rect.width() - 1.0, rect.height() - 1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        painter.setBrush(QBrush(self._bg_color))
        painter.drawRoundedRect(badge_rect, 13.0, 13.0)

        if self._pixmap and not self._pixmap.isNull():
            w = 24
            h = 24
            pm_rect = QRectF((rect.width() - w) / 2, (rect.height() - h) / 2, w, h)
            painter.drawPixmap(pm_rect.toRect(), self._pixmap)
        elif self._symbol:
            painter.setPen(self._fg_color)
            font = painter.font()
            font.setPointSize(15)
            font.setBold(True)
            font.setFamily("Segoe UI, sans-serif")
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, self._symbol)

