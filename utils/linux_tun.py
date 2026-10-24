"""Linux TUN layer for sing-box 1.14+, per the sdelay.txt architecture.

- One-time capability grant for sing-box (``pkexec`` + ``setcap``), so TUN
  starts without a password prompt on every connect.
- Config builders using sing-box 1.14 pieces: ``dns_mode: "hijack"``,
  ``auto_redirect`` (nftables fast path) and kernel-level ``bypass``
  pre-matching for games/torrents/local traffic.
- Hot node switching, delay probing and the live process list via the
  built-in Clash API — without restarting the TUN interface.
- ``netns`` sandbox mode: sing-box inside a user-owned network namespace
  where TUN needs no root privileges at all.
"""
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# Capabilities sdelay.txt requires for TUN + auto_redirect + low ports.
REQUIRED_CAPS = ("cap_net_admin", "cap_net_raw", "cap_net_bind_service")
_CAPS_MASK = ",".join(REQUIRED_CAPS) + "+eip"
# The inheritable (+i) bit keeps the privileges alive in helper processes
# sing-box forks (e.g. resolvectl configuring TUN link DNS).

TUN_INTERFACE_NAME = "sb_tun0"
TUN_MTU = 9000
SELECTOR_TAG = "proxy-selector"
AUTO_TAG = "auto-best"
DIRECT_TAG = "direct"
PING_URL = "https://cp.cloudflare.com/generate_204"
PING_TIMEOUT_MS = 3000
DEFAULT_GEO_PREFIXES = ("geosite-ru", "geoip-ru")


class LinuxTunError(RuntimeError):
    """Raised when TUN preparation, config validation or the daemon fails."""


def _checked_port(port) -> int:
    if isinstance(port, bool) or not isinstance(port, int):
        raise LinuxTunError(f"invalid port: {port!r}")
    if not 1 <= port <= 65535:
        raise LinuxTunError(f"invalid port: {port}")
    return port


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/sbin/{name}", f"/usr/sbin/{name}", f"/usr/bin/{name}"):
        if Path(candidate).is_file():
            return candidate
    return None


def has_tun_capabilities(binary_path) -> bool:
    """True when the binary carries every capability in REQUIRED_CAPS."""
    getcap = _which("getcap")
    if getcap is None:
        log.debug("getcap not found; assuming capabilities are missing")
        return False
    try:
        proc = subprocess.run(
            [getcap, str(binary_path)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("getcap probe failed: %s", e)
        return False
    return (proc.returncode == 0
            and all(cap in (proc.stdout or "").lower() for cap in REQUIRED_CAPS))


def grant_tun_capabilities(binary_path) -> bool:
    """Grant file capabilities via a single graphical Polkit prompt.

    Silent no-op when the caps are already in place; returns False only
    when the prompt was cancelled or setcap failed.
    """
    path = Path(binary_path)
    if not path.is_file():
        log.warning("grant_tun_capabilities: binary not found: %s", path)
        return False
    if has_tun_capabilities(path):
        return True
    pkexec, setcap = _which("pkexec"), _which("setcap")
    if pkexec is None or setcap is None:
        log.warning("grant_tun_capabilities: pkexec/setcap not found")
        return False
    try:
        proc = subprocess.run(
            [pkexec, setcap, _CAPS_MASK, str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Polkit capability grant failed: %s", e)
        return False
    if proc.returncode != 0:
        log.warning("setcap failed (code %s): %s",
                    proc.returncode, (proc.stderr or "").strip())
        return False
    log.info("Granted %s to %s", _CAPS_MASK, path)
    return True


def find_free_api_port() -> int:
    """Bind-free port for the local Clash API controller."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
    except OSError:
        return 19090


def write_config(config: dict, directory,
                 name: str = "socksicle-linux-tun.json") -> Path:
    """Persist a TUN config (owner-only, it may carry the API secret)."""
    work_dir = Path(directory)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def build_tun_inbound(
    *,
    netns: str | None = None,
    addresses: list[str] | None = None,
    exclude_cidrs: list[str] | None = None,
) -> dict:
    """Build the sing-box 1.14 TUN inbound (hijack + auto_redirect)."""
    inbound: dict = {
        "type": "tun",
        "tag": "tun-in",
        "interface_name": TUN_INTERFACE_NAME,
        "address": list(addresses or ["172.19.0.1/30"]),
        "mtu": TUN_MTU,
        "auto_route": True,
        # nftables fast path: also resolves the TUN/Docker bridge conflict.
        "auto_redirect": True,
        "strict_route": True,
        # 1.14: sing-box negotiates DNS with systemd-resolved and hijacks
        # port 53 natively instead of editing resolv.conf.
        "dns_mode": "hijack",
        "stack": "mixed",
    }
    if netns is not None:
        # 1.14: TUN + auto_route + auto_redirect inside a user-owned
        # namespace require no root privileges.
        inbound["netns"] = netns
    if exclude_cidrs:
        # Proxy server endpoints stay outside the tunnel.
        inbound["route_exclude_address"] = list(exclude_cidrs)
    return inbound


def build_pre_match_rules(bypass_processes=(), dns_hijack_inbound=None, bypass_lan: bool = True) -> list[dict]:
    """Route rules ordered so kernel bypass fires before sniffing.

    ``bypass`` is honoured only for auto_redirect connections in
    pre-match; light rules first keeps games/torrents out of the
    userspace sniffer path. When ``dns_hijack_inbound`` is set, the
    hijack-dns rule applies only to that inbound.
    """
    rules: list[dict] = []
    if bypass_lan:
        rules.append({"ip_is_private": True, "action": "bypass"})
    processes = [p for p in bypass_processes if p]
    if processes:
        rules.append({"process_name": processes, "action": "bypass"})
    rules.append({"action": "sniff"})
    hijack: dict = {"protocol": "dns", "action": "hijack-dns"}
    if dns_hijack_inbound:
        hijack = {"inbound": [dns_hijack_inbound], **hijack}
    rules.append(hijack)
    return rules


def local_rule_sets(work_dir, prefixes=DEFAULT_GEO_PREFIXES) -> list[dict]:
    """Local ``.srs`` rule sets shipped with the installer, if present."""
    sets: list[dict] = []
    for prefix in prefixes:
        path = Path(work_dir) / f"{prefix}.srs"
        if path.is_file():
            sets.append({"type": "local", "tag": prefix,
                         "format": "binary", "path": str(path)})
    return sets


def build_config(
    nodes: list[dict],
    *,
    active_tag: str,
    work_dir,
    api_port: int | None = None,
    api_secret: str | None = None,
    bypass_processes: list[str] | None = None,
    dns_direct: str = "77.88.8.8",
    local_port: int | None = None,
) -> dict:
    """Build the full sing-box config described by sdelay.txt.

    ``nodes`` are already-tagged outbound dicts (``node_frankfurt`` etc.);
    ``local_port`` optionally adds the mixed SOCKS/HTTP inbound.
    """
    if not nodes:
        raise LinuxTunError("build_config: no node outbounds")
    tags = [n.get("tag") for n in nodes]
    if not all(tags):
        raise LinuxTunError("build_config: every node needs a tag")
    if active_tag not in tags:
        raise LinuxTunError("build_config: active tag is not among nodes")

    outbounds: list[dict] = [
        {"type": "selector", "tag": SELECTOR_TAG,
         "outbounds": [AUTO_TAG, *tags], "default": active_tag,
         "interrupt_exist_connections": True},
        {"type": "urltest", "tag": AUTO_TAG, "outbounds": list(tags),
         "url": PING_URL, "interval": "3m", "tolerance": 50},
        *nodes,
        {"type": "direct", "tag": DIRECT_TAG},
    ]

    inbounds = [build_tun_inbound()]
    if local_port is not None:
        inbounds.append({"type": "mixed", "tag": "mixed-in",
                         "listen": "127.0.0.1",
                         "listen_port": _checked_port(local_port)})

    sets = local_rule_sets(work_dir)
    set_tags = {s["tag"] for s in sets}
    dns_rules = ([{"rule_set": "geosite-ru", "server": "dns-direct"}]
                 if "geosite-ru" in set_tags else [])
    dns: dict = {
        "servers": [
            {"type": "https", "tag": "dns-proxy", "server": "1.1.1.1",
             "detour": SELECTOR_TAG},
            {"type": "udp", "tag": "dns-direct", "server": dns_direct},
        ],
        "rules": dns_rules,
        "final": "dns-proxy",
        "strategy": "ipv4_only",
    }

    rules = build_pre_match_rules(bypass_processes or ())
    if set_tags:
        rules.append({"rule_set": sorted(set_tags),
                      "action": "route", "outbound": DIRECT_TAG})
    route: dict = {
        "auto_detect_interface": True,
        "default_domain_resolver": "dns-direct",
        "rules": rules,
        "final": SELECTOR_TAG,
    }
    if sets:
        route["rule_set"] = sets

    config: dict = {"log": {"level": "info", "timestamp": True},
                    "dns": dns, "inbounds": inbounds,
                    "outbounds": outbounds, "route": route}
    if api_port is not None and api_secret:
        config["experimental"] = {
            "cache_file": {"enabled": True, "path": "cache.db"},
            "clash_api": {
                "external_controller": f"127.0.0.1:{_checked_port(api_port)}",
                "secret": api_secret,
            },
        }
    return config


class SingBoxDaemon:
    """sing-box process owner: validate config, grant caps once, run, stop."""

    def __init__(self, binary_path, config_path, work_dir):
        self.binary_path = Path(binary_path).resolve()
        self.config_path = Path(config_path).resolve()
        self.work_dir = Path(work_dir).resolve()
        for path in (self.binary_path, self.config_path):
            if not path.is_file():
                raise FileNotFoundError(f"missing file: {path}")
        if not self.work_dir.is_dir():
            raise FileNotFoundError(f"missing work dir: {self.work_dir}")
        self.process: subprocess.Popen | None = None

    def start(self, *, rootless: bool = False) -> subprocess.Popen:
        """Start the daemon; raise LinuxTunError/FileNotFoundError on failure.

        ``rootless`` runs sing-box inside a user-thrown network namespace
        (``unshare``). No setcap is needed there: uid 0 inside the
        namespace holds every capability within it.
        """
        if self.process and self.process.poll() is None:
            raise LinuxTunError("sing-box daemon already running")

        if rootless:
            unshare = _which("unshare")
            if unshare is None:
                raise LinuxTunError("rootless mode requires unshare(1)")
            prefix = [unshare, "--user", "--map-root-user", "--net", "--"]
        else:
            if not grant_tun_capabilities(self.binary_path):
                raise PermissionError(
                    f"TUN capabilities were not granted for {self.binary_path}")
            prefix = []

        proc = subprocess.run(
            [str(self.binary_path), "check", "-c", str(self.config_path),
             "-D", str(self.work_dir)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise LinuxTunError(f"config check failed: {detail}")

        args = [*prefix, str(self.binary_path), "run",
                "-c", str(self.config_path), "-D", str(self.work_dir)]
        try:
            self.process = subprocess.Popen(
                args, cwd=str(self.work_dir), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as e:
            raise LinuxTunError(f"failed to start sing-box: {e}") from e
        log.info("sing-box started (pid=%s, rootless=%s)",
                 self.process.pid, rootless)
        return self.process

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 3.0) -> None:
        proc, self.process = self.process, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
            log.warning("sing-box daemon did not exit; killed")

    def namespace_entry_args(self) -> list[str]:
        """Prefix launching apps inside the sandbox netns (rootless mode)."""
        pid = self.process.pid if self.process else None
        if pid is None:
            raise LinuxTunError("daemon is not running")
        return ["nsenter", "-t", str(pid), "-U", "--preserve-credentials", "-n"]


class SingBoxControlAPI:
    """Clash API client for hot control of the running sing-box."""

    def __init__(self, host: str = "127.0.0.1", port: int = 19090,
                 secret: str | None = None):
        self.base_url = f"http://{host}:{_checked_port(port)}"
        self.headers = {"Content-Type": "application/json"}
        if secret:
            self.headers["Authorization"] = f"Bearer {secret}"

    def _open(self, request, timeout: float):
        # Bypass ambient env proxies; the controller is always localhost.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)

    def _get_json(self, path: str, timeout: float):
        with self._send("GET", path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _send(self, method: str, path: str, payload: bytes | None = None,
              timeout: float = 2.0):
        request = urllib.request.Request(self.base_url + path,
                                        data=payload, method=method)
        for key, value in self.headers.items():
            request.add_header(key, value)
        return self._open(request, timeout)

    def switch_node(self, selector_tag: str, target_node: str) -> bool:
        """Hot-switch the selector; the tunnel stays up."""
        try:
            with self._send(
                "PUT",
                f"/proxies/{urllib.parse.quote(selector_tag)}",
                json.dumps({"name": target_node}).encode("utf-8"),
            ) as response:
                return response.status in (200, 204)
        except (OSError, ValueError) as e:
            log.error("switch_node failed: %s", e)
            return False

    def get_node_delay(self, node_tag: str) -> int:
        """Real protocol-level delay in ms, -1 when unreachable."""
        params = urllib.parse.urlencode({"url": PING_URL,
                                         "timeout": PING_TIMEOUT_MS})
        try:
            data = self._get_json(
                f"/proxies/{urllib.parse.quote(node_tag)}/delay?{params}", 4.0)
            return int(data.get("delay", -1))
        except (OSError, ValueError, TypeError):
            return -1

    def get_active_processes(self) -> list[str]:
        """Programs currently sending traffic through the tunnel."""
        try:
            data = self._get_json("/connections", 2.0)
        except (OSError, ValueError) as e:
            log.debug("connections fetch failed: %s", e)
            return []
        processes: set[str] = set()
        for conn in data.get("connections") or []:
            meta = conn.get("metadata", {})
            proc = meta.get("process") or meta.get("processPath")
            if proc:
                processes.add(proc)
        return sorted(processes)


def new_api_secret() -> str:
    """Random secret for the local Clash API (never a fixed token)."""
    return secrets.token_hex(16)
