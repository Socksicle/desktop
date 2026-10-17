"""Shared binary discovery for proxy engine binaries.

xray and sing-box follow the same lookup: detect a release target for
this machine, locate an existing binary, and validate it by probing it
with ``--version``. Engines are never downloaded: the user places the
binary manually (see ``manual_install_instructions``).
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .base import CheckResult, CREATE_NO_WINDOW

MIN_PLAUSIBLE_SIZE = 1_000_000
VERSION_ARG_TIMEOUT_S = 10.0

# (platform class, architecture) -> release asset target
TargetMap = dict[tuple[str, str], str]


def _detect_target(target_map: TargetMap) -> str:
    """Map (sys.platform, platform.machine()) to a release target string."""
    arch = platform.machine().lower()
    sys_plat = sys.platform
    if sys_plat == "win32":
        key = ("windows", arch)
    elif sys_plat.startswith("linux"):
        key = ("linux", arch)
    elif sys_plat == "darwin":
        key = ("darwin", arch)
    else:
        raise ValueError(f"Unsupported platform: {sys_plat}")
    try:
        return target_map[key]
    except KeyError:
        raise ValueError(
            f"Unsupported platform/arch: {key[0]}/{arch}") from None


def _binary_name(engine_name: str) -> str:
    return f"{engine_name}.exe" if sys.platform == "win32" else engine_name


def _local_bin_subdir(engine_name: str) -> str:
    """Subdirectory under bin/ where a manually-placed binary is expected."""
    return engine_name.replace("-", "")


def _find_binary(engine_name: str,
                 min_size: int = MIN_PLAUSIBLE_SIZE) -> Path | None:
    """Locate an engine binary across app, config and PATH locations."""
    from utils.platform_utils import get_config_dir, get_app_dir
    name = _binary_name(engine_name)
    subdir = _local_bin_subdir(engine_name)
    for candidate in (get_app_dir() / "bin" / name,
                      get_config_dir() / "bin" / name,
                      get_app_dir() / "bin" / subdir / name,
                      get_config_dir() / "bin" / subdir / name):
        if candidate.is_file() and candidate.stat().st_size > min_size:
            return candidate
    found = shutil.which(name)
    if found:
        p = Path(found)
        if p.is_file() and p.stat().st_size > min_size:
            return p
    return None


def _check_usable(path, display_name, version_arg=("version",),
                  min_size: int = MIN_PLAUSIBLE_SIZE) -> CheckResult:
    """Validate that a binary exists, looks real, and answers --version."""
    if path is None:
        return CheckResult(False, f"No {display_name} path given.")
    p = Path(path)
    if not p.is_file():
        return CheckResult(False, f"Not a file: {p}")
    try:
        if p.stat().st_size <= min_size:
            return CheckResult(False, f"Rejected {p}: too small")
    except OSError as e:
        return CheckResult(False, f"Cannot stat {p}: {e}")
    try:
        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [str(p), *version_arg],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=VERSION_ARG_TIMEOUT_S,
            creationflags=flags,
        )
        if proc.returncode == 0:
            return CheckResult(True, "")
        return CheckResult(
            False,
            f"{display_name} version exited with code {proc.returncode}")
    except (OSError, subprocess.SubprocessError) as e:
        return CheckResult(False, f"Could not run {p.name}: {e}")