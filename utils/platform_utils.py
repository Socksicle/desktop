"""Cross-platform helpers for Socksicle.

Centralizes config paths, binary discovery, logging, and Windows-specific
utilities so the UI layer stays clean and Windows-safe.
"""
import functools
import hashlib
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
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


@functools.lru_cache(maxsize=1)
def get_real_app_version() -> str:
    """Retrieve actual application version from pyproject.toml or package metadata."""
    base_ver = "0.1-rc.1"
    try:
        toml_path = get_app_dir() / "pyproject.toml"
        if toml_path.is_file():
            content = toml_path.read_text(encoding="utf-8")
            m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                return m.group(1)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log.debug("App version from pyproject.toml unavailable: %s", e)

    try:
        from importlib.metadata import version
        return version('socksicle')
    except (ImportError, AttributeError, ValueError, Exception) as e:
        log.debug("App version from package metadata unavailable: %s", e)

    return base_ver


def get_socksicle_user_agent() -> str:
    """Return canonical Socksicle user agent: socksicle/{version}/{os}."""
    ver = get_real_app_version()
    os_name = "windows" if is_windows() else ("linux" if is_linux() else sys.platform.lower())
    return f"socksicle/{ver}/{os_name}"


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


_TUN_CAPS_MASK = "cap_net_admin,cap_net_bind_service+eip"
# The inheritable (+i) bit is required: sing-box forks helper processes (e.g.
# the resolvectl calls that configure TUN link DNS) and caps granted without
# `+i` are dropped at exec, so children run unprivileged.
_TUN_RULE_DEST = "/etc/polkit-1/rules.d/20-socksicle-tun.rules"
_TUN_RULE_REL = Path("data") / "polkit" / "20-socksicle-tun.rules"


def _tun_rule_source() -> Path | None:
    candidate = get_app_dir() / _TUN_RULE_REL
    return candidate if candidate.is_file() else None


def _tun_rule_in_sync() -> bool:
    """Whether the installed polkit rule matches the bundled one.

    A bundled source that is missing counts as synced: nothing to install
    (packaged setups may ship the rule via the distribution instead).
    Hardened distros keep /etc/polkit-1/rules.d root-only readable
    (drwxr-x--- root polkitd), so an unreadable destination falls back to
    the marker written right after pkexec confirmed the install.
    """
    src = _tun_rule_source()
    if src is None:
        return True
    try:
        src_bytes = src.read_bytes()
    except OSError as e:
        log.debug("TUN rule source unreadable: %s", e)
        return False
    try:
        return Path(_TUN_RULE_DEST).read_bytes() == src_bytes
    except OSError:
        try:
            marker = _rule_marker_path().read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return marker == hashlib.sha256(src_bytes).hexdigest()


def _rule_marker_path() -> Path:
    return get_config_dir() / "tun_polkit_rule.marker"


def _write_rule_marker(src: Path) -> None:
    try:
        marker = _rule_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(hashlib.sha256(src.read_bytes()).hexdigest(),
                          encoding="utf-8")
    except OSError as e:
        log.debug("TUN rule marker write skipped: %s", e)


def _systemd_resolved_active() -> bool:
    """Whether systemd-resolved is running (its link DNS setup is polkit-gated)."""
    import shutil
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    try:
        proc = subprocess.run(
            [systemctl, "is-active", "--quiet", "systemd-resolved"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("systemd-resolved check failed: %s", e)
        return False


def grant_tun_capabilities(binary_path: str | Path | None, parent_window=None) -> bool:
    """Grant TUN privileges to the sing-box binary via a single Polkit prompt.

    Sets file capabilities ``cap_net_admin,cap_net_bind_service+eip`` (the
    inheritable bit keeps the privileges alive in processes sing-box forks)
    and, when systemd-resolved is running, installs a Polkit rule that lets
    its resolvectl configure the TUN link without a password on every connect.
    Silent no-op (no prompt) when both are already in place.
    Returns True when the binary is ready for TUN mode, False otherwise.
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

    caps_ok = check_tun_capabilities(path)
    rule_needed = _systemd_resolved_active() and not _tun_rule_in_sync()
    if caps_ok and not rule_needed:
        return True

    setcap_bin = shutil.which("setcap")
    if not setcap_bin:
        for candidate in ("/sbin/setcap", "/usr/sbin/setcap", "/usr/bin/setcap"):
            if Path(candidate).is_file():
                setcap_bin = candidate
                break
    if not setcap_bin:
        setcap_bin = "setcap"

    script = f"setcap {shlex.quote(_TUN_CAPS_MASK)} {shlex.quote(str(path))}"
    rule_src = _tun_rule_source() if rule_needed else None
    if rule_needed:
        if rule_src is None:
            # Very unlikely race: the bundled rule existed when the check ran
            # but is gone now. Still set the caps; the password prompts for
            # DNS setup remain until the rule is installed.
            log.warning("TUN polkit rule source vanished; installing caps only")
        else:
            script += (f" && install -m 0644 {shlex.quote(str(rule_src))}"
                       f" {shlex.quote(_TUN_RULE_DEST)}")

    cmd = [pkexec_bin, "/bin/sh", "-c", script]
    log.info("Requesting TUN privileges via Polkit: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            log.warning("pkexec grant failed with code %d: %s",
                        proc.returncode, (proc.stderr or "").strip())
            return False
        log.info("Successfully granted TUN privileges for %s", path)
        if rule_src is not None:
            _write_rule_marker(rule_src)
            if not _tun_rule_in_sync():
                return False
        if not caps_ok and check_tun_capabilities(path):
            return True
        return rule_needed or not _find_getcap()
    except subprocess.TimeoutExpired:
        log.warning("pkexec grant timed out")
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