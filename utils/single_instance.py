"""Single-instance application guard and IPC activation.

Ensures that only one instance of Socksicle runs per user configuration,
and provides local IPC to bring the existing window to the foreground when a
secondary instance is launched (e.g. by clicking a desktop shortcut while
the application is minimized to the system tray).
"""
import hashlib
import logging
from pathlib import Path
import sys
from typing import Any

from PySide6.QtCore import QCoreApplication, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from utils.platform_utils import is_windows

log = logging.getLogger(__name__)


def get_single_instance_name(config_dir: Path | None = None) -> str:
    """Return a deterministic, per-user single-instance socket name."""
    import utils.platform_utils as platform_utils

    if config_dir is None:
        config_dir = platform_utils.get_config_dir()
    h = hashlib.sha256(str(Path(config_dir).resolve()).encode("utf-8")).hexdigest()[:12]
    return f"socksicle_instance_{h}"


def acquire_single_instance_lock(
    config_dir: Path | None = None,
    timeout_ms: int = 0,
) -> QLockFile | None:
    """Acquire the app-wide single-instance lock (QLockFile in the user config dir).

    Returns the QLockFile that the caller must keep referenced for the
    process lifetime, or None when another Socksicle instance already holds
    it. QLockFile automatically detects and removes lock files left by crashed instances.
    """
    import utils.platform_utils as platform_utils

    if config_dir is None:
        config_dir = platform_utils.get_config_dir()
    lock_path = Path(config_dir) / "single_instance.lock"
    lock = QLockFile(str(lock_path))
    if lock.tryLock(timeout_ms):
        log.info("Single-instance lock acquired: %s", lock.fileName())
        return lock

    # If tryLock failed, check if the lock is stale (e.g. from an abnormal termination/crash).
    # removeStaleLockFile() checks whether the process holding the lock is still running;
    # if dead, it removes the stale lock file and returns True.
    if lock.removeStaleLockFile():
        log.info("Removed stale single-instance lock: %s", lock.fileName())
        if lock.tryLock(timeout_ms):
            log.info("Single-instance lock acquired after removing stale lock: %s", lock.fileName())
            return lock

    log.warning("Second Socksicle instance refused: lock %s is already held", lock.fileName())
    return None


def notify_running_instance(
    name: str | None = None,
    timeout_ms: int = 1000,
    attempts: int = 3,
) -> bool:
    """Notify the already-running instance to show and activate its window.

    Returns True if the primary instance was reached and acknowledged the request,
    or False if the connection could not be established.
    """
    if is_windows():
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception as e:
            log.debug("AllowSetForegroundWindow failed: %s", e)

    server_name = name or get_single_instance_name()
    for attempt in range(attempts):
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(timeout_ms):
            socket.write(b"ACTIVATE\n")
            socket.flush()
            socket.waitForBytesWritten(timeout_ms)
            socket.disconnectFromServer()
            return True
        if attempt < attempts - 1:
            import time
            time.sleep(0.1)
    log.warning("Could not connect to single-instance server %s", server_name)
    return False


class SingleInstanceServer(QObject):
    """Local IPC server listening for activation requests from secondary instances."""

    activated = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent if isinstance(parent, QObject) else None)
        self.server: QLocalServer | None = None
        self.server_name: str = ""
        self.activation_requested: bool = False

    def start(self, name: str | None = None) -> bool:
        """Start listening on the local server socket."""
        self.server_name = name or get_single_instance_name()
        self.server = QLocalServer(self)

        # Clear stale socket leftover from a previous crash; safe because lock is held
        QLocalServer.removeServer(self.server_name)

        if not self.server.listen(self.server_name):
            log.warning(
                "Failed to start single-instance server %s: %s",
                self.server_name,
                self.server.errorString(),
            )
            return False

        self.server.newConnection.connect(self._on_new_connection)
        log.info("Single-instance server listening on %s", self.server_name)
        return True

    def _on_new_connection(self):
        if not self.server:
            return
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            if not client:
                continue
            client.disconnected.connect(client.deleteLater)
            client.readyRead.connect(lambda c=client: c.readAll())
            self.activation_requested = True
            log.info("Single-instance activation requested by another process")
            self.activated.emit()

    def close(self):
        """Close the server and remove the socket."""
        if self.server is not None:
            try:
                self.server.close()
            except Exception as e:
                log.debug("Error closing single-instance server: %s", e)
            try:
                QLocalServer.removeServer(self.server_name)
            except Exception as e:
                log.debug("Error removing single-instance server: %s", e)
            self.server = None


def setup_single_instance_server(
    app: Any = None,
    name: str | None = None,
) -> SingleInstanceServer | None:
    """Initialize and start the single-instance listener on the primary instance.

    Returns None if running in a headless test environment without a real QCoreApplication.
    """
    if QCoreApplication.instance() is None:
        return None
    server = SingleInstanceServer(app if isinstance(app, QObject) else None)
    if server.start(name):
        if app is not None and hasattr(app, "aboutToQuit") and hasattr(app.aboutToQuit, "connect"):
            try:
                app.aboutToQuit.connect(server.close)
            except Exception as e:
                log.debug("Could not connect single-instance server close to aboutToQuit: %s", e)
        return server
    return None
