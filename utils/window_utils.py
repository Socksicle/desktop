"""Cross-platform window-attribute helpers for frameless Socksicle windows.

Every top-level window in Socksicle is frameless and relies on rounded
corners plus a translucent background.  Windows (DWM) and Wayland always
composite, so an ARGB backing store is safe there.  A bare X11 session
without a compositor does not composite, which used to render unpainted
regions as transparent holes (e.g. the QComboBox in the settings dialog).
This module centralizes the translucent decision so dialogs do not have
to repeat it.
"""
import logging
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


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


def is_tiling_wm() -> bool:
    """Detect whether the application is running inside a tiling window manager.

    Returns False on Windows, macOS, and traditional floating desktop environments
    (KDE Plasma, GNOME, XFCE, Cinnamon, MATE, LXQt, etc.), as well as headless/test
    environments (offscreen QPA).

    Returns True when running under tiling compositors and window managers like
    Hyprland, Sway, i3, River, bspwm, dwm, awesome, qtile, xmonad, Herbstluftwm, Niri.
    """
    # Explicit manual override
    override = os.environ.get("SOCKSICLE_TILING")
    if override and override.strip():
        return override.strip().lower() in ("1", "true", "yes", "on")

    # Tests / headless
    if platform_name() == "offscreen":
        return False

    # Windows and macOS use floating window management by default
    if sys.platform in ("win32", "darwin"):
        return False

    session_names = []
    for var in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
        val = os.environ.get(var)
        if val:
            session_names.extend(val.lower().replace(":", " ").replace("-", " ").split())

    floating_desktops = {
        "kde", "plasma", "gnome", "xfce", "cinnamon", "mate",
        "lxqt", "lxde", "unity", "pantheon", "deepin", "budgie",
    }
    tiling_desktops = {
        "hyprland", "sway", "i3", "bspwm", "dwm", "river",
        "awesome", "qtile", "xmonad", "herbstluftwm", "niri",
        "spectrwm", "leftwm",
    }

    # Floating desktop environments take precedence
    if any(name in floating_desktops for name in session_names):
        return False

    # Explicit tiling desktop declared
    if any(name in tiling_desktops for name in session_names):
        return True

    # Check compositor sockets / instance signatures
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("HYPRLAND_CMD"):
        return True
    if os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK"):
        return True
    if (
        os.environ.get("RIVER_RUNTIME_DIR")
        or os.environ.get("BSPWM_SOCKET")
        or os.environ.get("NIRI_SOCKET")
        or os.environ.get("QTILE_SOCKET")
    ):
        return True

    return False


def get_phone_window_size(screen=None) -> tuple[int, int]:
    """Calculate ideal phone-like dimensions (width, height) fitting the current screen."""
    default_w, default_h = 440, 800
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen:
        avail = screen.availableGeometry()
        if avail.height() > 0:
            default_h = min(default_h, max(500, avail.height() - 40))
        if avail.width() > 0:
            default_w = min(default_w, max(360, avail.width() - 40))
    return default_w, default_h


def apply_hyprland_window_rules() -> None:
    """If running inside Hyprland, configure window rules to prevent compositor decoration artifacts.

    Hyprland renders compositor corner rounding, background blur, and drop shadows around the
    top-level window boundary. Since Socksicle is a frameless window with its own 32px rounded
    container and translucent corners, Hyprland's decorations would otherwise cause a translucent
    halo or mismatched corner curve around the transparent margins.
    """
    if not (os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("HYPRLAND_CMD")):
        return
    import shutil
    import subprocess
    if not shutil.which("hyprctl"):
        return
    try:
        # Hyprland 0.56+ (Lua configuration)
        lua = (
            'pcall(function() '
            'hl.window_rule({match = {class = "^([Ss]ocksicle.*)$"}, '
            'float = true, rounding = 0, no_blur = true, no_shadow = true, border_size = 0}) '
            'end)'
        )
        res = subprocess.run(["hyprctl", "eval", lua], capture_output=True, timeout=1)
        # If legacy config parser is active, fallback to keyword
        if res.returncode != 0 or b"keyword" in res.stdout:
            for rule in ("float", "rounding 0", "noblur", "noshadow", "noborder"):
                subprocess.run(
                    ["hyprctl", "keyword", "windowrulev2", f"{rule}, class:^(Socksicle.*)$"],
                    capture_output=True,
                    timeout=1,
                )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("Failed to apply Hyprland window rules: %s", e)


def find_overlay_target(parent=None):
    """Find the in-app container widget to attach modal overlays to.

    Returns the main window's container widget if available, or the window itself,
    preventing external window creation on tiling WMs and Wayland compositors.
    """
    from PySide6.QtWidgets import QWidget
    if parent is not None:
        if hasattr(parent, "container") and isinstance(parent.container, QWidget):
            return parent.container
        if hasattr(parent, "window"):
            win = parent.window()
            if hasattr(win, "container") and isinstance(win.container, QWidget):
                return win.container
            if win is not None and win.isVisible():
                return win
        if isinstance(parent, QWidget) and parent.isVisible():
            return parent
    return None


def configure_main_window(window, width: int = 440, height: int = 800) -> None:
    """Configure the main application window with phone proportions and frameless styling.

    On tiling window managers (Hyprland, Sway, i3, etc.), hints the compositor
    to spawn the window in floating mode at phone dimensions, then unlocks the
    maximum size constraint so the user can freely resize the window.

    On non-tiling / floating environments (Windows, KDE, GNOME, etc.), configures
    standard resizable geometry with phone initial size.
    """
    configure_window(window, frameless=True)

    target_w, target_h = get_phone_window_size()
    target_w = min(target_w, width)
    target_h = min(target_h, height)

    window.resize(target_w, target_h)
    window.setMinimumSize(360, 500)

    if is_tiling_wm():
        apply_hyprland_window_rules()
        # Hint tiling compositors to float by temporarily setting max size on spawn
        window.setMaximumSize(target_w, target_h)
        from PySide6.QtCore import QTimer
        # Release the maximum constraint shortly after mapping so user can freely resize
        QTimer.singleShot(250, lambda: window.setMaximumSize(16777215, 16777215))
    else:
        window.setMaximumSize(16777215, 16777215)

    screen = QApplication.primaryScreen()
    if screen:
        geom = screen.availableGeometry()
        x = geom.x() + max(0, (geom.width() - target_w) // 2)
        y = geom.y() + max(0, (geom.height() - target_h) // 2)
        window.move(x, y)


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


def start_window_move(window) -> bool:
    """Ask the window manager to start moving the top-level window.

    Manual ``QWidget.move()`` dragging does nothing on Wayland, where only
    the compositor may reposition windows.  ``startSystemMove()`` covers
    Windows, X11 and Wayland.  Returns False on platforms without
    client-initiated system moves (e.g. the offscreen QPA used in tests),
    so callers can fall back to their own drag logic.
    """
    handle = window.windowHandle()
    return handle is not None and handle.startSystemMove()


def start_window_resize(window, edge: Qt.Edge) -> bool:
    """Ask the window manager to start interactively resizing the top-level window."""
    handle = window.windowHandle()
    return handle is not None and hasattr(handle, "startSystemResize") and handle.startSystemResize(edge)


def get_resize_edge(pos, width: int, height: int, margin: int = 8) -> Qt.Edge | None:
    """Determine which window edge the point lies within, or None if in interior."""
    x, y = pos.x(), pos.y()
    edge = Qt.Edge(0)
    if x < margin:
        edge |= Qt.LeftEdge
    elif x > width - margin:
        edge |= Qt.RightEdge
    if y < margin:
        edge |= Qt.TopEdge
    elif y > height - margin:
        edge |= Qt.BottomEdge
    return edge if edge != Qt.Edge(0) else None


def cursor_for_edge(edge: Qt.Edge) -> Qt.CursorShape:
    """Return the appropriate Qt resize cursor for a given window edge combination."""
    if edge in (Qt.TopEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge):
        return Qt.SizeFDiagCursor
    if edge in (Qt.TopEdge | Qt.RightEdge, Qt.BottomEdge | Qt.LeftEdge):
        return Qt.SizeBDiagCursor
    if edge in (Qt.LeftEdge, Qt.RightEdge):
        return Qt.SizeHorCursor
    if edge in (Qt.TopEdge, Qt.BottomEdge):
        return Qt.SizeVerCursor
    return Qt.ArrowCursor


