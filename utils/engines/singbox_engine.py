"""sing-box engine implementation for sing-box 1.14.

Binary discovery, version probing, process lifecycle and Clash API
hot-switching live here; config generation lives in ``singbox_config`` and
outbound builders in ``singbox_outbounds``.
"""
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

from ..server_model import server_identity
from ..i18n import tr
from .base import ProxyEngine, EngineType
from . import common
from .singbox_config import TUN_ADAPTER_NAME, _generate_config

log = logging.getLogger("engine.singbox")


def _find_free_port() -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
    except OSError:
        return 9095

# Substrings (lowercased) identifying a stale-TUN-adapter startup failure.
_TUN_CONFLICT_MARKERS = (
    "cannot create a file when that file already exists",
    "the object already exists",
)

# Substrings (lowercased) identifying a TLS certificate verification
# failure (e.g. self-signed Hysteria2 server certificate).
_TLS_CERT_MARKERS = (
    "failed to verify certificate",
    "certificate signed by unknown authority",
    "x509",
)

# Oldest sing-box release the app still supports. Older binaries only
# trigger a warning for now (see startup_utils.show_singbox_outdated).
MIN_SINGBOX_VERSION = (1, 14, 0)
MIN_SINGBOX_VERSION_STR = "1.14"
SINGBOX_RELEASES_URL = "https://github.com/SagerNet/sing-box/releases"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

# Bound for `sing-box version` probes used only for the support check.
VERSION_PROBE_TIMEOUT_S = 5.0

_TARGET_MAP = {
    ("windows", "amd64"): "windows-amd64",
    ("windows", "x86_64"): "windows-amd64",
    ("windows", "arm64"): "windows-arm64",
    ("windows", "aarch64"): "windows-arm64",
    ("linux", "amd64"): "linux-amd64",
    ("linux", "x86_64"): "linux-amd64",
    ("linux", "arm64"): "linux-arm64",
    ("linux", "aarch64"): "linux-arm64",
    ("darwin", "amd64"): "darwin-amd64",
    ("darwin", "x86_64"): "darwin-amd64",
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "aarch64"): "darwin-arm64",
}


def _detect_target():
    return common._detect_target(_TARGET_MAP)


def _binary_name():
    return common._binary_name("sing-box")


def _find_binary() -> Path | None:
    return common._find_binary("sing-box")


def _check_usable(path) -> "common.CheckResult":
    return common._check_usable(path, "sing-box")


def parse_singbox_version(text: str | None) -> tuple[int, int, int] | None:
    """Extract (major, minor, patch) from `sing-box version` output."""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    except (ValueError, TypeError):
        return None


def get_singbox_version(binary=None) -> tuple[str | None, tuple[int, int, int] | None]:
    """Probe `sing-box version`; returns (display, tuple), (None, None) when unknown."""
    path = Path(binary) if binary is not None else _find_binary()
    if path is None:
        return None, None
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [str(path), "version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=VERSION_PROBE_TIMEOUT_S,
            creationflags=flags,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    version = parse_singbox_version(output)
    if version is None:
        return None, None
    return f"{version[0]}.{version[1]}.{version[2]}", version


def is_singbox_version_supported(version: tuple[int, int, int] | None) -> bool:
    """True when the version meets the minimum or could not be detected."""
    if version is None:
        return True
    return tuple(version) >= MIN_SINGBOX_VERSION


def _tun_device_check() -> tuple[bool, str]:
    """Check whether the Linux TUN device is present and usable.

    Returns (ok, message); on non-Linux platforms always ok.
    """
    if sys.platform == "win32":
        return True, ""
    if not os.path.exists("/dev/net/tun"):
        return False, tr("tun.no_device")
    return True, ""


class SingBoxEngine(ProxyEngine):
    engine_type = EngineType.SINGBOX

    def __init__(self):
        super().__init__()
        self.tun_mode = False
        self.custom_dns = None
        self.hy2_insecure = False
        self.block_udp = False
        self._tun_conflict = False
        self.available_servers: list = []
        self._api_port: int | None = None
        self._api_secret: str | None = None
        self._server_tag_map: dict[str, str] = {}

    def set_available_servers(self, servers: list) -> None:
        with self._lock:
            self.available_servers = list(servers) if servers else []

    def can_switch_server(self, server) -> bool:
        with self._lock:
            if not self.is_running() or not self._api_port or not self._api_secret:
                return False
            return server_identity(server) in self._server_tag_map

    def switch_server(self, server) -> bool:
        """Select another node in the running selector via the Clash API."""
        with self._lock:
            if not self.can_switch_server(server):
                return False
            tag = self._server_tag_map[server_identity(server)]
            port = self._api_port
            secret = self._api_secret

        try:
            url = f"http://127.0.0.1:{port}/proxies/proxy"
            payload = json.dumps({"name": tag}).encode("utf-8")
            request = urllib.request.Request(url, data=payload, method="PUT")
            request.add_header("Authorization", f"Bearer {secret}")
            request.add_header("Content-Type", "application/json")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=2.0) as response:
                if response.status not in (200, 204):
                    log.warning("sing-box switch server failed with status %s",
                                response.status)
                    return False
        except (OSError, ValueError) as e:
            log.error("Failed to hot-switch sing-box server: %s", e)
            return False

        with self._lock:
            self.current_server = server
        log.info("Hot-switched sing-box server to %s (%s)",
                 getattr(server, "name", ""), tag)
        return True

    def _wait_tun_release(self, timeout: float):
        """Wait for a stale wintun device to be released (Windows only).

        A previous sing-box may have been force-killed (or the driver may
        lag behind process exit); starting the next instance immediately
        races the driver and fails the TUN inbound. Returns instantly when
        the adapter name is already free.
        """
        if sys.platform != "win32":
            return
        try:
            from .proc_guard import wait_for_tun_adapter_gone
            wait_for_tun_adapter_gone(TUN_ADAPTER_NAME, timeout=timeout)
        except (ImportError, OSError) as e:
            log.debug("TUN adapter wait skipped: %s", e)

    def _extra_error_hint(self, line: str) -> str | None:
        low = (line or "").lower()
        if "tun" in low and any(m in low for m in _TUN_CONFLICT_MARKERS):
            with self._lock:
                self._tun_conflict = True
            return tr("tun.stuck").format(name=TUN_ADAPTER_NAME)
        if any(m in low for m in _TLS_CERT_MARKERS):
            return tr("engine.tls_verify")
        return None

    def pop_tun_adapter_conflict(self) -> bool:
        with self._lock:
            hit, self._tun_conflict = self._tun_conflict, False
        return hit

    def _clean_stale_tun_adapter(self):
        """Clean any stale Wintun/socksicle network adapter on Windows."""
        if sys.platform != "win32":
            return
        try:
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            subprocess.run(
                ["netsh", "interface", "delete", "interface", f"name={TUN_ADAPTER_NAME}"],
                capture_output=True,
                timeout=1.5,
                creationflags=flags,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("TUN adapter cleanup skipped: %s", e)

    def start(self, server):
        with self._lock:
            if self.available_servers:
                self._api_port = _find_free_port()
                self._api_secret = secrets.token_hex(16)
            else:
                self._api_port = None
                self._api_secret = None

        if self.tun_mode:
            if sys.platform == "win32":
                self._clean_stale_tun_adapter()
                self._wait_tun_release(timeout=3.0)
            ok, reason = _tun_device_check()
            if not ok:
                log.error("TUN start blocked: %s", reason)
                self.statusChanged.emit(tr("tun.failed_prefix").format(reason=reason), True)
                return False
            if sys.platform.startswith("linux"):
                # Grant is a silent no-op when capabilities and the Polkit
                # rule are already in place; otherwise it prompts once.
                from ..platform_utils import grant_tun_capabilities
                binary = self.find_binary()
                if binary and not grant_tun_capabilities(binary):
                    reason = tr("tun.no_caps")
                    log.error("TUN start blocked: %s", reason)
                    self.statusChanged.emit(tr("tun.failed_prefix").format(reason=reason), True)
                    return False
        return super().start(server)

    def teardown(self):
        super().teardown()
        with self._lock:
            self._server_tag_map.clear()
            self._api_port = None
            self._api_secret = None
        if self.tun_mode:
            if sys.platform == "win32":
                self._clean_stale_tun_adapter()
                self._wait_tun_release(timeout=3.0)

    def find_binary(self):
        return _find_binary()

    def check_usable(self, path):
        return _check_usable(path)

    def build_config(self, server):
        return _generate_config(
            server,
            self.local_port,
            tun_mode=self.tun_mode,
            custom_dns=self.custom_dns,
            hy2_insecure=self.hy2_insecure,
            block_udp=self.block_udp,
            available_servers=self.available_servers,
            api_port=self._api_port,
            api_secret=self._api_secret,
            server_tag_map_out=self._server_tag_map,
        )

    def build_args(self, server):
        return super().build_args(server, ["run", "-c"], "singbox-")

    def version_args(self):
        return ["version"]

    def process_name(self):
        return "sing-box (TUN)" if self.tun_mode else "sing-box"
