"""Startup orchestration and platform initialization (unified module).

Runs the shared startup flow across Windows and Linux:
    application start
      -> platform initialization (env, logging, user model id, excepthook)
      -> high-DPI scaling policy
      -> local backend check (a usable engine binary must be installed)
      -> main window creation
      -> install native event / power / network / signal handlers
      -> start Qt event loop

Also provides autostart configuration for Windows (Registry) and Linux (XDG .desktop).
"""
import os
import sys

# Must be applied before Qt/PySide6 is imported on Windows; plain
# assignment so the env vars take effect before Qt initialises.
if sys.platform == "win32":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

import ctypes
import logging
from pathlib import Path
import signal
import socket
import struct
import traceback

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QEvent,
    QObject,
    QSocketNotifier,
    QTimer,
    SLOT,
    Slot,
    Qt,
)
from ui.message_box import MessageBox
QMessageBox = MessageBox

from utils.platform_utils import is_frozen, is_linux, is_windows, setup_logging
from utils.i18n import set_language, tr
from utils.engines.engine_manager import (
    EngineType, get_engine,
)

log = logging.getLogger(__name__)

WM_POWERBROADCAST = 0x0218
PBT_APMRESUMESUSPEND = 0x0012

_MIN_VALID_MSG_ADDRESS = 0x10000


def _register_taskbar_created_message() -> int:
    """Return the TaskbarCreated broadcast id for the current Windows session.

    TaskbarCreated is registered at runtime with RegisterWindowMessageW; its
    numeric value is session-specific and must not be hard-coded.
    """
    if not is_windows():
        return 0
    try:
        return int(ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated"))
    except (AttributeError, OSError) as e:
        log.warning("Could not register TaskbarCreated message: %s", e)
        return 0


NM_STATE_UNKNOWN = 0
NM_STATE_ASLEEP = 10
NM_STATE_DISCONNECTED = 20
NM_STATE_DISCONNECTING = 30
NM_STATE_CONNECTING = 40
NM_STATE_CONNECTED_LOCAL = 50
NM_STATE_CONNECTED_SITE = 60
NM_STATE_CONNECTED_GLOBAL = 70

LOGIN1_SERVICE = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_INTERFACE = "org.freedesktop.login1.Manager"
LOGIN1_SIGNAL_PREPARE_FOR_SLEEP = "PrepareForSleep"

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_INTERFACE = "org.freedesktop.NetworkManager"
NM_SIGNAL_STATE_CHANGED = "StateChanged"


def _subscribe_dbus_signal(bus, service: str, path: str, interface: str, name: str,
                           receiver: QObject, slot_name: str, arg_type: str) -> bool:
    """Connect a D-Bus signal to a QObject slot.

    PySide6's QtDBus only accepts the receiver + SLOT("name(types)") form;
    plain strings and raw callables are rejected or silently fail.
    """
    slot = SLOT(f"{slot_name}({arg_type})")
    return bool(bus.connect(service, path, interface, name, receiver, slot))


class LinuxDBusPowerNetworkFilter(QObject):
    """Monitors system sleep/resume (systemd-logind) and network state (NetworkManager) on Linux via D-Bus."""

    def __init__(self, on_resume, on_network_change=None, parent=None, bus=None):
        super().__init__(parent)
        self.on_resume = on_resume
        self.on_network_change = on_network_change or on_resume
        self.is_sleeping = False
        self._last_nm_state = None
        self._bus = bus
        self._login1_connected = False
        self._nm_connected = False
        self.setup()

    @property
    def is_connected(self) -> bool:
        """True if at least one D-Bus signal subscription succeeded."""
        return self._login1_connected or self._nm_connected

    def setup(self) -> bool:
        """Connect to system bus and subscribe to systemd-logind and NetworkManager signals."""
        try:
            if self._bus is None:
                try:
                    from PySide6 import QtDBus
                    self._bus = QtDBus.QDBusConnection.systemBus()
                except (ImportError, AttributeError) as e:
                    log.warning("PySide6.QtDBus is not available: %s; Linux power/network monitoring disabled.", e)
                    return False

            if not hasattr(self._bus, "isConnected") or not self._bus.isConnected():
                log.warning("D-Bus system bus is not connected; Linux power/network monitoring disabled.")
                return False

            # Subscribe to systemd-logind PrepareForSleep(bool)
            try:
                self._login1_connected = _subscribe_dbus_signal(
                    self._bus,
                    LOGIN1_SERVICE,
                    LOGIN1_PATH,
                    LOGIN1_INTERFACE,
                    LOGIN1_SIGNAL_PREPARE_FOR_SLEEP,
                    self,
                    "handle_prepare_for_sleep",
                    "bool",
                )
                if self._login1_connected:
                    log.info("Subscribed to systemd-logind PrepareForSleep D-Bus signal.")
                else:
                    log.warning("Failed to subscribe to systemd-logind PrepareForSleep D-Bus signal.")
            except Exception as e:
                log.warning("Error subscribing to systemd-logind PrepareForSleep: %s", e)

            # Subscribe to NetworkManager StateChanged(uint32)
            try:
                self._nm_connected = _subscribe_dbus_signal(
                    self._bus,
                    NM_SERVICE,
                    NM_PATH,
                    NM_INTERFACE,
                    NM_SIGNAL_STATE_CHANGED,
                    self,
                    "handle_nm_state_changed",
                    "int",
                )
                if self._nm_connected:
                    log.info("Subscribed to NetworkManager StateChanged D-Bus signal.")
                else:
                    log.warning("Failed to subscribe to NetworkManager StateChanged D-Bus signal.")
            except Exception as e:
                log.warning("Error subscribing to NetworkManager StateChanged: %s", e)

            return self.is_connected

        except Exception as e:
            log.warning("Unexpected error initializing Linux D-Bus listener: %s", e)
            return False

    @Slot(bool)
    def handle_prepare_for_sleep(self, sleeping: bool):
        """Handle systemd-logind PrepareForSleep(bool) signal."""
        sleeping_bool = bool(sleeping)
        self.is_sleeping = sleeping_bool
        if sleeping_bool:
            log.info("System preparing for sleep...")
        else:
            log.info("System resumed from sleep, triggering proxy reconnect...")
            if callable(self.on_resume):
                try:
                    self.on_resume()
                except Exception as e:
                    log.error("Error in on_resume callback: %s", e)

    @Slot(int)
    def handle_nm_state_changed(self, state: int):
        """Handle NetworkManager StateChanged(uint32) signal."""
        try:
            state_int = int(state)
        except (ValueError, TypeError):
            log.debug("Ignoring invalid NetworkManager state value: %r", state)
            return

        prev_state = self._last_nm_state
        self._last_nm_state = state_int

        if state_int == NM_STATE_CONNECTED_GLOBAL:
            log.info("Network connectivity globally restored (NM_STATE_CONNECTED_GLOBAL=%d), triggering reconnect...", state_int)
            if callable(self.on_network_change):
                try:
                    self.on_network_change()
                except Exception as e:
                    log.error("Error in on_network_change callback: %s", e)
        elif state_int == NM_STATE_DISCONNECTED:
            log.info("Network disconnected (NM_STATE_DISCONNECTED=%d)", state_int)
        elif state_int == NM_STATE_CONNECTING:
            log.debug("Network connecting (NM_STATE_CONNECTING=%d)...", state_int)
        elif state_int == NM_STATE_ASLEEP:
            log.debug("NetworkManager asleep (NM_STATE_ASLEEP=%d)", state_int)
        else:
            log.debug("NetworkManager state changed: %d (previous: %s)", state_int, prev_state)

    def disconnect_signals(self):
        """Disconnect D-Bus signal listeners if connected."""
        if self._bus is not None and hasattr(self._bus, "disconnect"):
            if self._login1_connected:
                try:
                    self._bus.disconnect(
                        LOGIN1_SERVICE,
                        LOGIN1_PATH,
                        LOGIN1_INTERFACE,
                        LOGIN1_SIGNAL_PREPARE_FOR_SLEEP,
                        self,
                        SLOT("handle_prepare_for_sleep(bool)"),
                    )
                except (AttributeError, RuntimeError, TypeError):
                    pass
                self._login1_connected = False
            if self._nm_connected:
                try:
                    self._bus.disconnect(
                        NM_SERVICE,
                        NM_PATH,
                        NM_INTERFACE,
                        NM_SIGNAL_STATE_CHANGED,
                        self,
                        SLOT("handle_nm_state_changed(int)"),
                    )
                except (AttributeError, RuntimeError, TypeError):
                    pass
                self._nm_connected = False





class TrayAndPowerFilter(QAbstractNativeEventFilter):
    """Keeps the tray icon alive after Explorer restarts and auto-reconnects
    the proxy after the machine wakes from sleep."""

    def __init__(self, on_taskbar_created, on_resume, on_interrupt=None):
        super().__init__()
        self.on_taskbar_created = on_taskbar_created
        self.on_resume = on_resume
        self.on_interrupt = on_interrupt
        self.taskbar_created_msg = _register_taskbar_created_message()

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType == b"windows_generic_MSG":
                addr = int(message)
                if addr < _MIN_VALID_MSG_ADDRESS:
                    return False, 0
                data = (ctypes.c_char * 32).from_address(addr)
                _, msg, wparam, _ = struct.unpack_from("<QI4xQq", bytes(data))
                if self.taskbar_created_msg and msg == self.taskbar_created_msg:
                    self.on_taskbar_created()
                elif msg == WM_POWERBROADCAST and wparam == PBT_APMRESUMESUSPEND:
                    self.on_resume()
        except KeyboardInterrupt:
            log.info("Received KeyboardInterrupt in native event filter, shutting down...")
            if self.on_interrupt and callable(self.on_interrupt):
                self.on_interrupt()
            else:
                QCoreApplication.quit()
            return True, 0
        except (ValueError, OSError, AttributeError, TypeError,
                struct.error) as e:
            log.debug("Ignoring malformed native message: %s", e)
        return False, 0


def initialize():
    """Per-platform startup initialization.

    Logging and the unhandled-exception hook run on every platform; the
    AppUserModelID is set on Windows only.
    """
    setup_logging()
    logging.getLogger("startup").info(
        "Launching Socksicle on %s",
        "Windows" if is_windows() else "Linux",
    )
    set_app_user_model_id()
    install_excepthook()
    # Shrink the default ~10 MB QPixmapCache: the app only shows a handful
    # of small icons, so 1 MB is plenty. Best-effort here (pre-QApplication);
    # main.py re-applies it once the QApplication exists.
    try:
        configure_pixmap_cache()
    except Exception as e:
        log.debug("Early pixmap cache tuning skipped: %s", e)
    # Emergency crash recovery: clean any stale Kill Switch firewall rules from previous taskkill crashes
    try:
        from utils.killswitch import KillSwitchManager
        KillSwitchManager.get_instance().clean_stale_rules()
    except Exception as e:
        log.debug("Startup stale rule cleanup skipped: %s", e)


def set_app_user_model_id():
    """Required for correct taskbar grouping and toast notifications."""
    if not is_windows():
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "socksicle.desktop")
    except (AttributeError, OSError) as e:
        log.warning("Failed to set AppUserModelID: %s", e)


def install_excepthook(window=None):
    """Route unhandled exceptions through logging and stderr on all platforms.
    Gracefully handles KeyboardInterrupt (Ctrl+C) without logging an unhandled crash."""

    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            log.info("KeyboardInterrupt received, exiting cleanly...")
            if window and hasattr(window, "quit_app") and callable(window.quit_app):
                window.quit_app()
            else:
                QCoreApplication.quit()
            return

        logging.getLogger("unhandled").critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        try:
            if sys.stderr:
                traceback.print_exception(exc_type, exc_value, exc_tb)
        except Exception as e:
            log.debug("Failed to write exception to stderr: %s", e)

    sys.excepthook = excepthook


def setup_sigint_handler(app, window=None):
    """Ensure Ctrl+C (SIGINT) and console events cleanly quit the application."""

    def _sigint_handler(signum, frame):
        log.info("Received SIGINT (Ctrl+C), shutting down...")
        if window and hasattr(window, "quit_app") and callable(window.quit_app):
            window.quit_app()
        elif hasattr(app, "quit") and callable(app.quit):
            app.quit()
        else:
            QCoreApplication.quit()

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except (ValueError, OSError, AttributeError) as e:
        log.debug("Could not install SIGINT handler: %s", e)

    # In Qt, C++ event loop blocks Python signal evaluation when idling.
    # On POSIX (Linux/macOS), signal.set_wakeup_fd + QSocketNotifier wakes
    # the Qt event loop via the self-pipe trick with zero latency and zero
    # periodic CPU wakeups during idle, preserving CPU sleep states (C-states).
    # On Windows, SetConsoleCtrlHandler handles console events natively,
    # making wake timers and wakeup sockets unnecessary.
    if not is_windows() and isinstance(app, QObject):
        try:
            rsock, wsock = socket.socketpair()
            rsock.setblocking(False)
            wsock.setblocking(False)
            old_fd = signal.set_wakeup_fd(wsock.fileno(), warn_on_full_buffer=False)

            def _on_wakeup():
                try:
                    rsock.recv(4096)
                except BlockingIOError:
                    pass

            notifier = QSocketNotifier(rsock.fileno(), QSocketNotifier.Type.Read, app)
            notifier.activated.connect(_on_wakeup)
            app._sigint_notifier = notifier
            app._sigint_sockets = (rsock, wsock)

            def _cleanup():
                try:
                    signal.set_wakeup_fd(old_fd)
                except Exception:
                    pass
                try:
                    notifier.setEnabled(False)
                except Exception:
                    pass
                for s in (rsock, wsock):
                    try:
                        s.close()
                    except Exception:
                        pass

            if hasattr(app, "aboutToQuit") and hasattr(app.aboutToQuit, "connect"):
                app.aboutToQuit.connect(_cleanup)
        except Exception as e:
            log.debug("Could not install signal wakeup notifier: %s", e)

    # On Windows console, also listen for console control events (Ctrl+C, Ctrl+Break, Close)
    try:
        if hasattr(ctypes, "windll"):
            from ctypes import wintypes

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
            def _console_ctrl_handler(ctrl_type):
                if ctrl_type in (0, 1, 2):  # CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT
                    log.info("Console control event (%s) received, shutting down...", ctrl_type)
                    if window and hasattr(window, "quit_app") and callable(window.quit_app):
                        QTimer.singleShot(0, window.quit_app)
                    else:
                        QTimer.singleShot(0, QCoreApplication.quit)
                    return True
                return False

            ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ctrl_handler, True)
            app._console_ctrl_handler = _console_ctrl_handler
    except Exception as e:
        log.debug("Could not install Windows console control handler: %s", e)


def desktop_file_name() -> str:
    """Windows uses the app id; Linux uses the .desktop entry name."""
    return "Socksicle" if is_windows() else "Socksicle.desktop"


def apply_high_dpi_policy(app=None):
    """Windows: PassThrough scale-factor rounding for the QApplication."""
    if not is_windows():
        return
    if app is not None and hasattr(app, "setHighDpiScaleFactorRoundingPolicy"):
        app.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    else:
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except (AttributeError, RuntimeError):
            pass


PIXMAP_CACHE_LIMIT_KB = 1024


def configure_pixmap_cache(limit_kb: int = PIXMAP_CACHE_LIMIT_KB) -> bool:
    """Cap the global QPixmapCache (default 1 MB instead of Qt's ~10 MB).

    Safe to call before or after QApplication creation; failures are logged
    and never raise. Returns True when the limit was applied.
    """
    try:
        from PySide6.QtGui import QPixmapCache
        QPixmapCache.setCacheLimit(int(limit_kb))
        log.info("QPixmapCache limit set to %d KB", int(limit_kb))
        return True
    except Exception as e:
        log.debug("Could not set QPixmapCache limit: %s", e)
        return False


def _read_settings_file() -> dict:
    """Read the persisted settings.json from the user config dir.

    Returns {} when the file is missing or not a dict of settings; IO and
    JSON errors propagate to the caller.
    """
    import json

    from utils.platform_utils import get_config_dir

    path = get_config_dir() / "settings.json"
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        value = json.load(f)
    return value if isinstance(value, dict) else {}


def apply_language_from_settings(settings: dict | None = None) -> str:
    """Apply the persisted UI language before the first dialog is shown.

    main_window applies the language from its own settings object; this
    covers dialogs shown before the window exists (e.g. single-instance).
    Does not raise; falls back to the default language.
    """
    if not isinstance(settings, dict) or not settings.get("language"):
        try:
            settings = _read_settings_file()
        except Exception as e:
            log.debug("Could not load UI language from settings: %s", e)
            settings = {}
    return set_language(settings.get("language"))


def acquire_single_instance_lock():
    """Acquire the app-wide single-instance lock (QLockFile in the user config dir).

    Returns the QLockFile that the caller must keep referenced for the
    process lifetime, or None when another Socksicle instance already holds
    it. QLockFile detects and removes lock files left by crashed instances.
    """
    from PySide6.QtCore import QLockFile

    from utils.platform_utils import get_config_dir

    lock = QLockFile(str(get_config_dir() / "single_instance.lock"))
    if lock.tryLock(0):
        log.info("Single-instance lock acquired: %s", lock.fileName())
        return lock
    log.warning("Second Socksicle instance refused: lock %s is already held", lock.fileName())
    return None


def install_native_handlers(app, window):
    """Install platform-specific event handlers (Windows native messages or Linux D-Bus)
    and graceful Ctrl+C / SIGINT handlers.

    On Windows: installs TrayAndPowerFilter via QAbstractNativeEventFilter for
    Explorer restarts (WM_TASKBARCREATED) and sleep resume (WM_POWERBROADCAST).
    On Linux: installs LinuxDBusPowerNetworkFilter for systemd-logind sleep/wake
    signals and NetworkManager state changes.

    Keeps a strong reference on the app so the filter lives for the whole session.
    Returns the active filter/listener object, or None if unsupported/unavailable.
    """
    setup_sigint_handler(app, window)
    install_excepthook(window)

    if is_windows():
        on_interrupt = window.quit_app if (window and hasattr(window, "quit_app")) else None

        def _restore_tray():
            if window is not None:
                window.tray_manager.restore()

        filter_ = TrayAndPowerFilter(
            on_taskbar_created=_restore_tray,
            on_resume=window.reconnect_after_resume if (window and hasattr(window, "reconnect_after_resume")) else None,
            on_interrupt=on_interrupt,
        )
        app.installNativeEventFilter(filter_)
        app.native_filter = filter_  # keep a strong reference
        return filter_

    if is_linux():
        try:
            dbus_filter = LinuxDBusPowerNetworkFilter(
                on_resume=window.reconnect_after_resume,
                on_network_change=window.reconnect_after_resume,
            )
            try:
                app.native_filter = dbus_filter  # keep a strong reference
            except AttributeError:
                pass
            return dbus_filter
        except Exception as e:
            log.warning("Failed to initialize Linux D-Bus listener: %s", e)
            return None

    return None


def get_autostart_desktop_path() -> Path:
    """Path to the user's XDG autostart desktop entry for Socksicle on Linux."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else (Path.home() / ".config")
    return base / "autostart" / "socksicle.desktop"


def is_autostart_enabled() -> bool:
    """Check if Socksicle autostart is enabled on the current platform.

    On Windows: checks HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run for 'Socksicle'.
    On Linux: checks ~/.config/autostart/socksicle.desktop exists and is not disabled.
    """
    if is_windows():
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            try:
                val, _ = winreg.QueryValueEx(key, "Socksicle")
                return bool(val)
            finally:
                winreg.CloseKey(key)
        except OSError:
            return False

    elif is_linux():
        path = get_autostart_desktop_path()
        if not path.is_file():
            return False
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                clean = line.strip().lower()
                if clean in ("hidden=true", "x-gnome-autostart-enabled=false"):
                    return False
            return True
        except Exception as e:
            log.debug("Failed to read autostart desktop file: %s", e)
            return False

    return False


def set_autostart(enable: bool, app_path: str | None = None) -> bool:
    """Enable or disable autostart on system boot for the current platform.

    Args:
        enable: True to enable autostart (with --minimized), False to disable.
        app_path: Optional custom executable path or command line.

    Returns:
        True if configuration succeeded, False on error.
    """
    if is_windows():
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_READ,
            )
            try:
                if enable:
                    if app_path:
                        exec_cmd = app_path if "--minimized" in app_path else f'"{app_path}" --minimized'
                    elif is_frozen():
                        exec_cmd = f'"{sys.executable}" --minimized'
                    else:
                        main_script = Path(sys.argv[0]).resolve()
                        exec_cmd = f'"{sys.executable}" "{main_script}" --minimized'
                    winreg.SetValueEx(key, "Socksicle", 0, winreg.REG_SZ, exec_cmd)
                    log.info("Enabled Windows autostart: %s", exec_cmd)
                    return True
                else:
                    try:
                        winreg.DeleteValue(key, "Socksicle")
                        log.info("Disabled Windows autostart.")
                    except FileNotFoundError:
                        pass
                    return True
            finally:
                winreg.CloseKey(key)
        except Exception as e:
            log.warning("Failed to configure Windows autostart: %s", e)
            return False

    elif is_linux():
        path = get_autostart_desktop_path()
        try:
            if enable:
                if app_path:
                    exec_cmd = app_path if "--minimized" in app_path else f"{app_path} --minimized"
                elif is_frozen():
                    exec_cmd = f"{sys.executable} --minimized"
                else:
                    main_script = Path(sys.argv[0]).resolve()
                    exec_cmd = f"{sys.executable} {main_script} --minimized"

                path.parent.mkdir(parents=True, exist_ok=True)
                desktop_entry = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Socksicle\n"
                    "Comment=Cross-platform Shadowsocks/TwinSock proxy client\n"
                    f"Exec={exec_cmd}\n"
                    "Icon=socksicle\n"
                    "Terminal=false\n"
                    "Categories=Network;Proxy;\n"
                    "StartupNotify=false\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
                path.write_text(desktop_entry, encoding="utf-8")
                log.info("Created Linux XDG autostart desktop entry at %s (Exec=%s)", path, exec_cmd)
                return True
            else:
                if path.exists():
                    path.unlink(missing_ok=True)
                    log.info("Removed Linux XDG autostart desktop entry at %s", path)
                return True
        except Exception as e:
            log.warning("Failed to configure Linux XDG autostart: %s", e)
            return False

    return False


def find_usable_backend(engine_type=None) -> Path | None:
    """Return a usable engine binary path, or None when missing/unusable.

    Engines are never downloaded: the binary must be placed manually
    (see :func:`manual_install_instructions`). If engine_type is None,
    the currently selected engine from settings is used.
    """
    from utils.server_manager import ServerManager as _SM
    settings = _SM().settings

    if engine_type is None:
        try:
            engine_type = EngineType(settings.get("engine", "sing-box"))
        except ValueError:
            engine_type = EngineType.SINGBOX

    engine = get_engine(engine_type)
    binary = engine.find_binary()
    if binary is None:
        log.warning("No usable %s binary found across candidate paths", engine_type)
        return None
    check = engine.check_usable(binary)
    if not check.usable:
        log.warning("Candidate binary %s rejected: %s", binary, check.reason)
        return None
    return binary


def manual_install_instructions() -> str:
    """User-facing manual installation instructions for the current OS."""
    if is_windows():
        return tr("backend.instructions_win")
    return tr("backend.instructions_linux")


def show_backend_missing(parent=None) -> None:
    """Explain that no usable proxy backend was found and how to install one."""
    msg = QMessageBox(parent) if parent else QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setWindowTitle(tr("backend.missing_title"))
    msg.setText(tr("backend.missing_text"))
    msg.setInformativeText(tr("backend.howto")
                           + manual_install_instructions())
    msg.setStandardButtons(QMessageBox.Ok)
    msg.exec()


def show_already_running(parent=None) -> None:
    """Explain that a second Socksicle instance cannot start."""
    msg = QMessageBox(parent) if parent else QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setWindowTitle(tr("app.already_running_title"))
    msg.setText(tr("app.already_running_text"))
    msg.setStandardButtons(QMessageBox.Ok)
    msg.exec()


def show_singbox_outdated(parent=None, version_str: str | None = None) -> None:
    """Warn that the installed sing-box is older than the minimum supported.

    Warning only: the caller continues with the connect/startup flow.
    """
    from utils.engines.singbox_engine import (
        MIN_SINGBOX_VERSION_STR,
        SINGBOX_RELEASES_URL,
    )
    msg = QMessageBox(parent) if parent else QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setWindowTitle(tr("engine.singbox_outdated_title"))
    msg.setText(tr("engine.singbox_outdated_text").format(
        version=version_str or "?",
        minimum=MIN_SINGBOX_VERSION_STR,
        url=SINGBOX_RELEASES_URL,
    ))
    msg.setStandardButtons(QMessageBox.Ok)
    msg.exec()