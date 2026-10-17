"""Cross-platform window-attribute helpers for frameless Socksicle windows.

Every top-level window in Socksicle is frameless and relies on rounded
corners plus a translucent background.  Windows (DWM) and Wayland always
composite, so an ARGB backing store is safe there.  A bare X11 session
without a compositor does not composite, which used to render unpainted
regions as transparent holes (e.g. the QComboBox in the settings dialog).
This module centralizes the translucent decision so dialogs do not have
to repeat it.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication


def platform_name() -> str:
    """Lower-cased QPA platform name (e.g. 'windows', 'xcb', 'wayland')."""
    return QGuiApplication.platformName().lower()


def compositing_available() -> bool:
    """Whether the current desktop session can composite a translucent window.

    Windows and Wayland always composite.  On X11 a compositor is the norm
    on modern desktops, but users on a bare window manager can force opaque
    rendering with ``SOCKSICLE_TRANSLUCENT=0``.
    """
    name = platform_name()
    if name in ("windows", "wayland", "offscreen"):
        return True
    if name == "xcb":
        return os.environ.get("SOCKSICLE_TRANSLUCENT", "1") != "0"
    return False


def configure_window(window, frameless: bool = True) -> None:
    """Apply the frameless + translucent attributes for the current session.

    On platforms without compositing the window stays fully opaque so every
    child widget always paints over an opaque backing store.
    """
    flags = window.windowFlags()
    if frameless:
        flags |= Qt.FramelessWindowHint
    window.setWindowFlags(flags)
    window.setAttribute(Qt.WA_TranslucentBackground, compositing_available())
