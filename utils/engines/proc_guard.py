import ctypes
from ctypes import wintypes
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from utils.platform_utils import get_config_dir, is_windows

log = logging.getLogger("engine.proc_guard")

STATE_DIR_NAME = "engine_state"
PORT_ATTEMPTS = 50
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
CREATE_NO_WINDOW = 0x08000000
_CMD_TIMEOUT_S = 10.0
_START_TIME_TOLERANCE_S = 5.0
_FALLBACK_MARKER_TOLERANCE_S = 300.0
_MARKER_FUTURE_SLACK_S = 10.0


def _kernel32():
    """kernel32 with correct prototypes (ctypes defaults truncate handles)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD
    return kernel32


def _win_open_process(pid: int):
    """Raw OpenProcess handle or None when the pid cannot be opened."""
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    return handle or None


def _win_process_exit_code(handle) -> int | None:
    """Exit code of an open process handle, None when unreadable."""
    try:
        code = wintypes.DWORD()
        if not _kernel32().GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value
    except (AttributeError, OSError, ValueError):
        return None


def _win_last_error() -> int:
    try:
        return _kernel32().GetLastError()
    except (AttributeError, OSError):
        return 0


def _win_close_handle(handle) -> None:
    try:
        _kernel32().CloseHandle(handle)
    except (AttributeError, OSError):
        pass


def _win_process_alive(pid: int) -> bool:
    """Native liveness check: OpenProcess + GetExitCodeProcess, no child processes."""
    try:
        handle = _win_open_process(pid)
    except (AttributeError, OSError, ValueError, OverflowError):
        return False
    if handle is None:
        # No such process — unless it exists but denies access (e.g. elevated).
        return _win_last_error() == ERROR_ACCESS_DENIED
    try:
        code = _win_process_exit_code(handle)
    except (AttributeError, OSError, ValueError):
        return False
    finally:
        _win_close_handle(handle)
    return code == STILL_ACTIVE


def port_available(host: str, port: int) -> bool:
    """Return True when a TCP listener could be bound at host:port now."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if sys.platform != "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, int(port)))
        return True
    except (OSError, ValueError, OverflowError):
        return False


def wait_for_port_available(host: str, port: int, timeout: float = 2.0) -> bool:
    """Wait up to timeout seconds for a port to become available."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if port_available(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def pick_free_port(preferred: int, host: str = "127.0.0.1",
                   attempts: int = PORT_ATTEMPTS) -> int:
    """Pick a free TCP port, preferring ``preferred`` and scanning upward.

    The preferred try-then-increment scheme keeps the canonical port
    (10085 / 9090) whenever possible and only moves on a conflict; the
    bind/close check is inherently racy, which is acceptable for short
    lived local API listeners.
    """
    try:
        preferred = int(preferred)
    except (TypeError, ValueError):
        preferred = 10085
    for port in range(preferred, min(preferred + attempts, 65535) + 1):
        if port_available(host, port):
            return port
    raise RuntimeError(f"No free TCP port found near {preferred} on {host}")


def _state_dir() -> Path:
    d = get_config_dir() / STATE_DIR_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.debug("Cannot create engine state dir %s: %s", d, e)
    return d


def marker_path(engine_name: str) -> Path:
    return _state_dir() / f"{engine_name}.pid"


def write_pid_marker(engine_name: str, pid: int, binary: str) -> Path:
    """Atomically record the pid and binary of a started engine process."""
    path = marker_path(engine_name)
    try:
        start_time = process_start_time(int(pid))
    except (ValueError, OverflowError):
        start_time = None
    payload = json.dumps({"pid": int(pid), "binary": str(binary),
                          "engine": engine_name, "start_time": start_time,
                          "created": time.time()})
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Cannot write pid marker %s: %s", path, e)
    return path


def read_pid_marker(engine_name: str) -> dict | None:
    """Read a pid marker, or None when missing or unusable."""
    path = marker_path(engine_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return data


def remove_pid_marker(engine_name: str) -> None:
    try:
        marker_path(engine_name).unlink(missing_ok=True)
    except OSError as e:
        log.debug("Cannot remove pid marker for %s: %s", engine_name, e)


def _is_zombie(pid: int) -> bool:
    """Best-effort zombie check via /proc (POSIX only)."""
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="ascii") as f:
            data = f.read()
    except OSError:
        return False
    rpar = data.rfind(")")
    if rpar == -1:
        return False
    rest = data[rpar + 1:].split()
    return bool(rest) and rest[0] == "Z"


def process_alive(pid: int) -> bool:
    """Return True when the process with this pid still exists."""
    if is_windows():
        return _win_process_alive(pid)
    if _is_zombie(pid):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def process_executable_path(pid: int) -> str | None:
    """Best-effort absolute path of the pid's executable, None on failure."""
    if is_windows():
        try:
            kernel32 = _kernel32()
            handle = kernel32.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = ctypes.c_ulong(len(buf))
                if not kernel32.QueryFullProcessImageNameW(
                        handle, 0, buf, ctypes.byref(size)):
                    return None
                return buf.value
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        exe = os.readlink(f"/proc/{int(pid)}/exe")
    except (OSError, ValueError):
        return None
    if exe.endswith(" (deleted)"):
        exe = exe[:-len(" (deleted)")]
    return exe


def process_start_time(pid: int) -> float | None:
    """Unix-epoch start time of pid, None when it cannot be determined."""
    if is_windows():
        try:
            kernel32 = _kernel32()
            handle = kernel32.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                creation, exit_t, kernel_t, user_t = (wintypes.FILETIME() for _ in range(4))
                if not kernel32.GetProcessTimes(
                        handle, ctypes.byref(creation), ctypes.byref(exit_t),
                        ctypes.byref(kernel_t), ctypes.byref(user_t)):
                    return None
                ft = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                if ft == 0:
                    return None
                return ft / 10_000_000 - 11644473600.0
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError, OverflowError):
            return None
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="ascii") as f:
            data = f.read()
        rpar = data.rfind(")")
        if rpar == -1:
            return None
        with open("/proc/stat", encoding="ascii") as f:
            btime = next(int(l.split()[1]) for l in f if l.startswith("btime "))
        start_ticks = int(data[rpar + 1:].split()[19])
        try:
            clk = os.sysconf("SC_CLK_TCK")
        except (AttributeError, ValueError, OSError):
            clk = 100
        return btime + start_ticks / float(clk)
    except (OSError, ValueError, StopIteration, IndexError):
        return None


def _paths_match(a: str, b: str) -> bool:
    if not isinstance(a, (str, os.PathLike)) or not isinstance(b, (str, os.PathLike)):
        return False
    try:
        pa, pb = Path(a), Path(b)
    except (TypeError, ValueError):
        return False
    if pa.exists() and pb.exists():
        try:
            return pa.samefile(pb)
        except OSError:
            pass
    return os.path.normcase(str(pa)) == os.path.normcase(str(pb))


def _kill_process(pid: int) -> bool:
    """Force-kill the pid; returns True when the process is gone.

    Private: only call for a pid whose identity was verified (own marker
    with matching executable and start time) or an owned child handle.
    """
    try:
        if is_windows():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=_CMD_TIMEOUT_S,
                creationflags=CREATE_NO_WINDOW)
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        return False
    for _ in range(50):
        if not process_alive(pid):
            return True
        time.sleep(0.05)
    return False


def cleanup_stale_engines(engine_names: list[str]) -> list[str]:
    """Kill leftover engine processes from previous sessions.

    Only processes recorded in our own pid markers are ever touched; a
    live pid whose executable or start time does not match the marker
    is treated as recycled and left alone, as is any pid that cannot
    be verified. Returns human readable actions (empty when idle).
    """
    actions = []
    for name in engine_names:
        try:
            marker = read_pid_marker(name)
        except Exception as e:
            log.warning("Cannot inspect pid marker for %s: %s", name, e)
            continue
        if marker is None:
            continue
        pid = marker["pid"]
        recorded_binary = marker.get("binary")
        if not process_alive(pid):
            remove_pid_marker(name)
            actions.append(f"removed stale pid marker {name} (pid {pid})")
            continue
        live_path = process_executable_path(pid)
        live_start = process_start_time(pid)
        if live_path is None or live_start is None:
            log.warning(
                "Not killing pid %s recorded for %s: executable path or "
                "start time cannot be verified",
                pid, name)
            remove_pid_marker(name)
            actions.append(f"dropped unverified pid marker {name} (pid {pid})")
            continue
        if recorded_binary and not _paths_match(recorded_binary, live_path):
            log.warning(
                "Pid %s recorded for %s now belongs to %s, not %s - "
                "leaving it alone and dropping the marker",
                pid, name, live_path, recorded_binary)
            remove_pid_marker(name)
            actions.append(f"dropped recycled pid marker {name} (pid {pid})")
            continue
        stored_start = marker.get("start_time")
        if isinstance(stored_start, (int, float)) and not isinstance(stored_start, bool):
            if abs(live_start - float(stored_start)) > _START_TIME_TOLERANCE_S:
                log.warning(
                    "Pid %s recorded for %s has a mismatching start time - "
                    "leaving it alone and dropping the marker",
                    pid, name)
                remove_pid_marker(name)
                actions.append(f"dropped recycled pid marker {name} (pid {pid})")
                continue
        else:
            try:
                mtime = marker_path(name).stat().st_mtime
            except OSError:
                mtime = None
            if (mtime is None
                    or live_start > mtime + _MARKER_FUTURE_SLACK_S
                    or mtime - live_start > _FALLBACK_MARKER_TOLERANCE_S):
                log.warning(
                    "Not killing pid %s recorded for %s: marker "
                    "start time cannot be verified",
                    pid, name)
                remove_pid_marker(name)
                actions.append(f"dropped unverified pid marker {name} (pid {pid})")
                continue
        log.info("Killing stale %s process (pid=%s, exe=%s)",
                 name, pid, live_path)
        if _kill_process(pid):
            remove_pid_marker(name)
            actions.append(f"killed stale {name} process (pid {pid})")
        else:
            log.error("Failed to kill stale %s process (pid=%s)",
                      name, pid)
    return actions


def tun_adapter_present(adapter_name: str) -> bool | None:
    """Check whether a Windows TUN network adapter still exists.

    sing-box reuses ``interface_name`` via wintun ``CreateAdapter``: when a
    previous session crashed (or was force-killed) the ``socksicle-tun``
    device can linger in a half-removed state and the next start dies with
    ``configure tun interface: (create adapter: Cannot create a file when
    that file already exists. | open existing adapter: Element not found.)``
    (sing-box#716, v2rayN#4541). Polling this before (re)starting lets us
    wait for the driver to release the name instead of racing it.

    Returns True/False, or None when the presence cannot be determined
    (non-Windows, missing ``netsh``, timeout) - callers must proceed anyway.
    """
    if not is_windows():
        return False
    try:
        proc = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_CMD_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    needle = (adapter_name or "").lower()
    if not needle:
        return None
    for line in (proc.stdout or "").splitlines():
        if needle in line.lower():
            return True
    return False


def wait_for_tun_adapter_gone(adapter_name: str, timeout: float = 8.0) -> bool:
    """Wait until the TUN adapter disappears; True when free or unknown."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        present = tun_adapter_present(adapter_name)
        if not present:
            # Absent (False) or undeterminable (None): nothing more to wait for.
            if present is False:
                log.info("TUN adapter '%s' is free", adapter_name)
            return True
        if time.monotonic() >= deadline:
            log.warning("TUN adapter '%s' is still present after %.0fs",
                        adapter_name, timeout)
            return False
        time.sleep(0.25)
