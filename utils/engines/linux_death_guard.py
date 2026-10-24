"""Linux process death guard for bounding engine process lifecycles.

On Linux, PR_SET_PDEATHSIG set before execve is automatically cleared by the
Linux kernel when executing binaries with file capabilities (such as sing-box
with cap_net_admin+eip). This module provides a pipe-backed companion watchdog
process that monitors the parent UI process and ensures child engine processes
are terminated if the parent crashes or exits unexpectedly.
"""
import logging
import os
import signal
import subprocess
import sys
from typing import Any

log = logging.getLogger("engine.linux_death_guard")

_GUARD_SCRIPT = """
import os, select, signal, sys, time

try:
    target_pid = int(sys.argv[1])
except Exception:
    sys.exit(0)

if target_pid <= 1:
    sys.exit(0)

def _kill_target():
    try:
        os.kill(target_pid, signal.SIGTERM)
        time.sleep(0.5)
        os.kill(target_pid, signal.SIGKILL)
    except OSError:
        pass

while True:
    try:
        os.kill(target_pid, 0)
    except OSError:
        break

    try:
        r, _, _ = select.select([0], [], [], 1.0)
        if 0 in r:
            data = os.read(0, 1)
            if not data:
                _kill_target()
                break
    except Exception:
        break

sys.exit(0)
"""


class LinuxDeathGuard:
    """Manages the lifetime of a Linux death guard subprocess and its pipe."""

    def __init__(self, proc: subprocess.Popen, write_fd: int, target_pid: int):
        self.proc = proc
        self.write_fd: int | None = write_fd
        self.target_pid = target_pid

    def stop(self) -> None:
        """Close the parent write end of the pipe and terminate the guard."""
        if self.write_fd is not None:
            try:
                os.close(self.write_fd)
            except OSError:
                pass
            self.write_fd = None

        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
            except (OSError, subprocess.SubprocessError):
                pass
            self.proc = None


def start_linux_death_guard(proc: Any) -> LinuxDeathGuard | None:
    """Start a death guard for proc on Linux.

    Returns a LinuxDeathGuard instance or None if not on Linux or if launch failed.
    """
    if not sys.platform.startswith("linux"):
        return None

    pid = getattr(proc, "pid", proc)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None

    try:
        r_fd, w_fd = os.pipe()
        os.set_inheritable(r_fd, True)
        os.set_inheritable(w_fd, False)

        cmd = [
            sys.executable, "-u", "-c",
            _GUARD_SCRIPT,
            str(pid),
        ]
        guard_proc = subprocess.Popen(
            cmd,
            stdin=r_fd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        os.close(r_fd)
        return LinuxDeathGuard(proc=guard_proc, write_fd=w_fd, target_pid=pid)
    except Exception as e:
        log.warning("Failed to start Linux death guard for pid %s: %s", pid, e)
        return None


def stop_linux_death_guard(guard: LinuxDeathGuard | None) -> None:
    """Clean up a running Linux death guard."""
    if guard is not None:
        guard.stop()
