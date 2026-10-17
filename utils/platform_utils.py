"""Cross-platform helpers for Socksicle.

Centralizes config paths, binary discovery, logging, and Windows-specific
utilities so the UI layer stays clean and Windows-safe.
"""
import os
import sys
import subprocess
import logging
import platform
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .i18n import tr

log = logging.getLogger(__name__)


def get_app_dir() -> Path:
    """Application directory (where engine binaries are bundled when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def get_config_dir() -> Path:
    """OS-correct user config directory that needs no admin rights."""
    if is_windows():
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    d = base / "socksicle"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_log_dir() -> Path:
    """OS-correct writable log directory."""
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = get_config_dir()
    d = base / "socksicle" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_admin() -> bool:
    """Best-effort elevated-privilege check.

    Windows: checks the UAC admin token. Linux: checks EUID 0 (root).
    Returns False when the check is unavailable or the process is not elevated.
    """
    if is_windows():
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (AttributeError, OSError) as e:
            log.debug("Admin check failed: %s", e)
            return False
    if is_linux():
        geteuid = getattr(os, "geteuid", None)
        if geteuid is None:
            return False
        return geteuid() == 0
    return False


def elevate_restart() -> bool:
    """Restart the current application with elevated privileges.

    Windows: relaunches via UAC (ShellExecuteW "runas").
    Linux: relaunches as root via pkexec, falling back to sudo.
    Returns False when elevation is not available or fails.
    """
    if is_windows():
        try:
            import ctypes
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{sys.argv[0]}" {params}'.strip(), None, 1
            )
            if int(ret) > 32:
                sys.exit(0)
            return False
        except Exception as e:
            log.warning("Elevation restart failed: %s", e)
            return False

    if is_linux():
        import shutil
        launcher_args = [sys.executable, sys.argv[0], *sys.argv[1:]]
        for launcher in ("pkexec", "sudo"):
            if shutil.which(launcher) is None:
                continue
            try:
                subprocess.Popen([launcher, *launcher_args])
                log.info("Restarting with elevated privileges via %s", launcher)
                sys.exit(0)
            except (OSError, subprocess.SubprocessError) as e:
                log.warning("Elevation restart failed with %s: %s", launcher, e)
        return False

    return False


def _find_getcap() -> str | None:
    """Locate getcap utility across PATH and standard system directories."""
    import shutil
    found = shutil.which("getcap")
    if found:
        return found
    for candidate in ("/sbin/getcap", "/usr/sbin/getcap", "/usr/bin/getcap"):
        if Path(candidate).is_file():
            return candidate
    return None


def check_tun_capabilities(binary_path: str | Path | None) -> bool:
    """Check if binary has Linux capabilities (cap_net_admin) for TUN mode.

    Returns True if cap_net_admin is present on the binary file.
    Returns False on non-Linux, missing binary, missing getcap, or missing capabilities.
    """
    if not is_linux() or binary_path is None:
        return False
    path = Path(binary_path)
    if not path.is_file():
        return False

    getcap_bin = _find_getcap()
    if not getcap_bin:
        log.debug("check_tun_capabilities: getcap utility not found")
        return False

    try:
        proc = subprocess.run(
            [getcap_bin, str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return False
        output = (proc.stdout or "").lower()
        return "cap_net_admin" in output
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("check_tun_capabilities failed: %s", e)
        return False


def grant_tun_capabilities(binary_path: str | Path | None, parent_window=None) -> bool:
    """Grant cap_net_admin,cap_net_bind_service+ep to binary via pkexec setcap.

    Returns True if capability was successfully granted, False otherwise.
    """
    if not is_linux() or binary_path is None:
        return False
    path = Path(binary_path)
    if not path.is_file():
        log.warning("grant_tun_capabilities: binary not found: %s", path)
        return False

    import shutil
    pkexec_bin = shutil.which("pkexec")
    if not pkexec_bin:
        for candidate in ("/usr/bin/pkexec", "/bin/pkexec"):
            if Path(candidate).is_file():
                pkexec_bin = candidate
                break
    if not pkexec_bin:
        log.warning("grant_tun_capabilities: pkexec not found")
        return False

    setcap_bin = shutil.which("setcap")
    if not setcap_bin:
        for candidate in ("/sbin/setcap", "/usr/sbin/setcap", "/usr/bin/setcap"):
            if Path(candidate).is_file():
                setcap_bin = candidate
                break
    if not setcap_bin:
        setcap_bin = "setcap"

    cmd = [pkexec_bin, setcap_bin, "cap_net_admin,cap_net_bind_service+ep", str(path)]
    log.info("Requesting TUN capabilities via Polkit: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0:
            log.info("Successfully executed pkexec setcap for %s", path)
            if check_tun_capabilities(path):
                return True
            if not _find_getcap():
                return True
            return False
        log.warning("pkexec setcap failed with code %d: %s", proc.returncode, (proc.stderr or "").strip())
        return False
    except subprocess.TimeoutExpired:
        log.warning("pkexec setcap timed out")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("grant_tun_capabilities failed: %s", e)
        return False





def setup_logging(level: int = logging.INFO) -> Path:
    """Rotating file + stderr logging. Returns the active log path."""
    log_path = get_log_dir() / "socksicle.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handlers = [handler]
    if sys.stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger().info(f"Socksicle starting (pid={os.getpid()}, platform={platform.platform()})")
    return log_path


# MSVC / loader error codes commonly seen when the engine fails on Windows.
_WIN_ERROR_TR_KEYS = {
    0xC0000135: "humanize.vc_missing",
    0xC0000139: "humanize.vc_outdated",
    5: "humanize.access",
    0x80070005: "humanize.access_win32",
    0x80070002: "humanize.not_found",
}


def humanize_error(e: BaseException, engine_name: str = "sing-box") -> str:
    """Map common Windows exceptions to friendly user messages."""
    if isinstance(e, FileNotFoundError):
        return tr("humanize.component_missing").format(engine=engine_name)
    if isinstance(e, PermissionError):
        return tr("humanize.access_std")
    if isinstance(e, subprocess.SubprocessError):
        return tr("humanize.process_failed").format(e=e)

    winerror = getattr(e, "winerror", None)
    if winerror is None and isinstance(e, OSError):
        winerror = e.errno
    if winerror and (winerror in _WIN_ERROR_TR_KEYS or winerror & 0xFFFFFFFF in _WIN_ERROR_TR_KEYS):
        key = winerror if winerror in _WIN_ERROR_TR_KEYS else winerror & 0xFFFFFFFF
        return tr(_WIN_ERROR_TR_KEYS[key])

    return str(e)