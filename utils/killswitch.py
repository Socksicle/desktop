"""Kill Switch implementation for Socksicle.

Provides OS firewall-level traffic blocking (Windows firewall policy /
Linux nftables) to prevent real IP leaks if the VPN proxy tunnel drops
unexpectedly.
"""
import atexit
import ipaddress
import json
import logging
import os
import socket
import subprocess
import threading
from .i18n import tr
from .linux_tun import TUN_INTERFACE_NAME
from .platform_utils import is_windows, is_linux, is_admin, elevate_restart

log = logging.getLogger("killswitch")

# Firewall rule names for Windows. BlockOut is only deleted here: older
# versions created it as an explicit rule, and explicit block rules override
# allow rules on Windows, so the current implementation switches the profile
# default outbound action instead.
_WIN_RULE_PREFIX = "Socksicle_KS_"
_WIN_RULES = [
    f"{_WIN_RULE_PREFIX}BlockOut",
    f"{_WIN_RULE_PREFIX}AllowLoopback",
    f"{_WIN_RULE_PREFIX}AllowLAN",
    f"{_WIN_RULE_PREFIX}AllowServer",
    f"{_WIN_RULE_PREFIX}AllowApp",
]

_WIN_PROFILES = ("Domain", "Private", "Public")
_WIN_ACTIONS = ("Allow", "Block", "NotConfigured")

_LAN_RANGES_V4 = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]
_LAN_RANGES_V6 = ["fe80::/10", "fc00::/7"]

_LINUX_TABLE = "socksicle_ks"
_LINUX_CHAIN = "output"

_STATE_FILE = "killswitch_state.json"

_PS_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _resolve_ips(host: str) -> list[str]:
    """Resolve a host to every address the proxy may connect to.

    Returning all answers matters for CDN-hosted endpoints: allowing only
    the first address would block the engine whenever it dials another one.
    """
    if not host:
        return []
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return [host]
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        log.warning("Cannot resolve kill switch target %r: %s", host, e)
        return []
    addresses: list[str] = []
    for info in infos:
        text = info[4][0]
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        text = str(address)
        if text not in addresses:
            addresses.append(text)
    return addresses


def _resolve_ip(host: str) -> str | None:
    """Resolve a domain host to an IPv4/IPv6 address string."""
    addresses = _resolve_ips(host)
    return addresses[0] if addresses else None


class KillSwitchManager:
    """Manages OS firewall rules for Kill Switch protection."""

    _instance = None
    _instance_lock = threading.Lock()
    _lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "KillSwitchManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._active = False
        self._engine_allowed = False
        self.last_error = ""
        self._registered_atexit = True
        atexit.register(self.cleanup)

    @property
    def is_active(self) -> bool:
        return self._active

    # ---- public lifecycle -------------------------------------------------

    def clean_stale_rules(self, elevate: bool = True) -> bool:
        """Restore the firewall policy and remove leftover rules.

        Returns False when the previous policy could not be restored; in that
        case the allow rules are intentionally left in place so the machine is
        not left without any path back to its proxy engine.
        """
        if is_windows():
            if not is_admin():
                if self._state_path().exists():
                    if elevate and elevate_restart():
                        return True
                    self.last_error = tr("killswitch.reason_admin")
                    log.warning(
                        "A previous Kill Switch session left the firewall policy "
                        "blocked; start Socksicle as Administrator to restore it")
                    return False
                return True
            log.info("Checking and cleaning stale Socksicle Kill Switch rules...")
            if not self._restore_windows_policy():
                return False
            self._delete_windows_rules()
            self._engine_allowed = False
            return True
        if is_linux():
            if not is_admin():
                # nftables requires root even to list the table, so a stale
                # drop policy cannot be detected or removed from here.
                log.warning(
                    "Cannot verify or remove a previous Kill Switch table "
                    "without root; if network access is blocked, start "
                    "Socksicle as root to clean it")
                return False
            self._delete_linux_table()
            self._engine_allowed = False
            return True
        return True

    def enable(self, server_host: str, server_port: int | None = None,
               engine_path: str | None = None) -> bool:
        """Enable Kill Switch protection for the given server and engine.

        Every allow rule (and, on Windows, the profile policy change) is
        verified before the machine is cut off, and a partial setup is rolled
        back. The method never leaves the machine unable to reach its proxy.
        """
        with self._lock:
            if not is_admin():
                self.last_error = tr("killswitch.reason_admin")
                log.warning("Cannot enable Kill Switch: %s", self.last_error)
                return False

            # The engine executable is allowed as a whole, so switching the
            # server does not require touching the firewall again.
            if self._active and self._engine_allowed:
                log.info("Kill Switch already covers the engine; rules unchanged")
                return True

            self._active = False
            self.last_error = ""
            self._engine_allowed = False
            server_ips = _resolve_ips(server_host)
            if not server_ips:
                self.last_error = tr("killswitch.reason_resolve").format(host=server_host)
                log.warning("Cannot enable Kill Switch: %s", self.last_error)
                return False

            if not self.clean_stale_rules():
                self.last_error = tr("killswitch.reason_restore")
                log.error("Cannot enable Kill Switch: previous firewall policy was not restored")
                return False

            if is_windows():
                error = self._enable_windows(server_ips, engine_path)
            elif is_linux():
                error = self._enable_linux(server_ips)
            else:
                error = tr("killswitch.reason_platform")

            if error:
                self.clean_stale_rules()
                self.last_error = error
                log.warning("Kill Switch not enabled: %s", error)
                return False

            self._active = True
            log.info("Kill Switch ENABLED (Target server: %s, port: %s)",
                     ", ".join(server_ips), server_port)
            return True

    def disable(self) -> bool:
        """Disable Kill Switch protection and restore the firewall policy.

        On Windows the previous outbound policy is restored before the allow
        rules are deleted, so a failed restore cannot lock the machine out.
        """
        with self._lock:
            if is_windows():
                if not self._restore_windows_policy():
                    self.last_error = tr("killswitch.reason_restore")
                    log.error("Kill Switch stays active: %s", self.last_error)
                    return False
                self._delete_windows_rules()
            elif is_linux():
                self._delete_linux_table()
            self._active = False
            self._engine_allowed = False
            self.last_error = ""
            log.info("Kill Switch DISABLED")
            return True

    def cleanup(self) -> None:
        """Teardown handler on application shutdown."""
        with self._lock:
            if self._active or is_admin():
                if self.clean_stale_rules(elevate=False):
                    self._active = False

    # ---- Windows ----------------------------------------------------------

    def _state_path(self):
        from .platform_utils import get_config_dir
        return get_config_dir() / _STATE_FILE

    def _read_state(self) -> dict[str, str] | None:
        path = self._state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            return None
        result = {}
        for name in _WIN_PROFILES:
            action = profiles.get(name)
            if action not in _WIN_ACTIONS:
                return None
            result[name] = action
        return result

    def _write_state(self, profiles: dict[str, str]) -> bool:
        path = self._state_path()
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError as e:
            log.error("Cannot persist kill switch firewall state: %s", e)
            return False

    def _remove_state(self) -> None:
        try:
            self._state_path().unlink(missing_ok=True)
        except OSError as e:
            log.debug("Cannot remove kill switch firewall state: %s", e)

    def _run_powershell(self, script: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-Command", script,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_PS_CREATE_NO_WINDOW,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.error("PowerShell firewall command failed: %s", e)
            return subprocess.CompletedProcess(["powershell"], 1, "", str(e))

    def _read_windows_policy(self) -> dict[str, str] | None:
        script = (
            "Get-NetFirewallProfile | ForEach-Object "
            '{ "$($_.Name)=$($_.DefaultOutboundAction)" }'
        )
        res = self._run_powershell(script)
        if res.returncode != 0:
            log.error("Cannot read firewall profiles: %s",
                      (res.stderr or res.stdout or "").strip())
            return None
        profiles: dict[str, str] = {}
        for line in (res.stdout or "").splitlines():
            name, sep, action = line.strip().partition("=")
            if not sep or name not in _WIN_PROFILES:
                continue
            action = action.strip()
            if action not in _WIN_ACTIONS:
                log.error("Unexpected firewall action %r for profile %s", action, name)
                return None
            profiles[name] = action
        if set(profiles) != set(_WIN_PROFILES):
            log.error("Firewall profile list is incomplete: %s", profiles)
            return None
        return profiles

    def _set_windows_policy(self, profiles: dict[str, str]) -> bool:
        statements = ["$ErrorActionPreference='Stop'"]
        for name in _WIN_PROFILES:
            action = profiles.get(name)
            if action not in _WIN_ACTIONS:
                return False
            statements.append(
                f"Set-NetFirewallProfile -Profile {name} "
                f"-DefaultOutboundAction {action}")
        res = self._run_powershell("; ".join(statements))
        if res.returncode != 0:
            log.error("Cannot set firewall outbound policy: %s",
                      (res.stderr or res.stdout or "").strip())
            return False
        return True

    def _windows_firewall_enabled(self) -> bool:
        res = self._run_powershell(
            "@(Get-NetFirewallProfile | Where-Object { -not $_.Enabled }).Count")
        if res.returncode != 0:
            return False
        return (res.stdout or "").strip() == "0"

    def _restore_windows_policy(self) -> bool:
        path = self._state_path()
        if not path.exists():
            return True
        previous = self._read_state()
        if previous is None:
            log.error("Kill switch firewall state is corrupt; policy not restored")
            return False
        if not self._set_windows_policy(previous):
            return False
        self._remove_state()
        return True

    def _add_win_rule(self, suffix: str, extra: list[str]) -> bool:
        res = self._run_netsh([
            "advfirewall", "firewall", "add", "rule",
            f"name={_WIN_RULE_PREFIX}{suffix}",
            "dir=out",
            "action=allow",
            "profile=any",
            *extra,
        ])
        if res.returncode != 0:
            detail = (res.stderr or res.stdout or "").strip()
            log.error("Failed to add kill switch rule %s: %s", suffix, detail)
            return False
        return True

    def _delete_windows_rules(self) -> None:
        for rule in _WIN_RULES:
            self._run_netsh(["advfirewall", "firewall", "delete", "rule", f"name={rule}"])

    def _enable_windows(self, server_ips: list[str], engine_path: str | None) -> str:
        """Set up a fail-closed Windows firewall policy with allow rules.

        An explicit "block all outbound" rule would override the allow rules
        (Windows gives block rules precedence), so the profile default action
        is switched to Block instead. The previous actions are persisted and
        restored on disconnect or on the next start after a crash.
        """
        previous = self._read_windows_policy()
        if previous is None:
            return tr("killswitch.reason_policy")
        if not self._windows_firewall_enabled():
            return tr("killswitch.reason_firewall")
        if not self._write_state(previous):
            return tr("killswitch.reason_state")

        # Allow rules are added while outbound traffic is still permitted;
        # switching the default action first would cut the running proxy
        # before its own exception exists.
        lan_ips = ",".join(_LAN_RANGES_V4 + _LAN_RANGES_V6)
        rules = [
            ("AllowLoopback", ["remoteip=127.0.0.1,::1"]),
            ("AllowLAN", [f"remoteip={lan_ips}"]),
        ]
        if engine_path and os.path.exists(engine_path):
            rules.append(("AllowApp", [f"program={engine_path}"]))
            self._engine_allowed = True
        else:
            # Without a verified engine path only the endpoint is allowed;
            # DNS resolution may then be limited to the LAN resolver.
            rules.append(("AllowServer", [f"remoteip={','.join(server_ips)}"]))

        for suffix, extra in rules:
            if not self._add_win_rule(suffix, extra):
                self._delete_windows_rules()
                self._remove_state()
                return tr("killswitch.reason_rule").format(
                    name=f"{_WIN_RULE_PREFIX}{suffix}")

        if not self._set_windows_policy({name: "Block" for name in _WIN_PROFILES}):
            if self._restore_windows_policy():
                self._delete_windows_rules()
            return tr("killswitch.reason_policy")

        current = self._read_windows_policy()
        if current is None or any(action != "Block" for action in current.values()):
            if self._restore_windows_policy():
                self._delete_windows_rules()
            return tr("killswitch.reason_policy")
        return ""

    # ---- Linux ------------------------------------------------------------

    def _run_netsh(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run netsh command hidden without opening console window."""
        creationflags = 0
        if is_windows():
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            return subprocess.run(
                ["netsh"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                check=False
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.error("netsh failed: %s", e)
            return subprocess.CompletedProcess(["netsh"] + args, 1, "", str(e))

    def _delete_linux_table(self) -> None:
        try:
            subprocess.run(
                ["nft", "delete", "table", "inet", _LINUX_TABLE],
                capture_output=True, check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("Could not delete nftables table %s: %s", _LINUX_TABLE, e)

    def _enable_linux(self, server_ips: list[str]) -> str:
        """Set up an nftables output chain with fail-closed policy.

        The whole rule set is applied as one nft transaction so a failure cannot
        leave a half-configured drop policy behind.
        """
        ipv4 = [ip for ip in server_ips if ":" not in ip]
        ipv6 = [ip for ip in server_ips if ":" in ip]
        lines = [
            f"add table inet {_LINUX_TABLE}",
            (f"add chain inet {_LINUX_TABLE} {_LINUX_CHAIN} "
             "{ type filter hook output priority 0; policy drop; }"),
            f'add rule inet {_LINUX_TABLE} {_LINUX_CHAIN} oifname "lo" accept',
            (f"add rule inet {_LINUX_TABLE} {_LINUX_CHAIN} "
             f'oifname "{TUN_INTERFACE_NAME}" accept'),
            (f"add rule inet {_LINUX_TABLE} {_LINUX_CHAIN} "
             f"ip daddr {{ {', '.join(_LAN_RANGES_V4)} }} accept"),
            (f"add rule inet {_LINUX_TABLE} {_LINUX_CHAIN} "
             f"ip6 daddr {{ {', '.join(_LAN_RANGES_V6)} }} accept"),
        ]
        if ipv4:
            lines.append(
                f"add rule inet {_LINUX_TABLE} {_LINUX_CHAIN} "
                f"ip daddr {{ {', '.join(ipv4)} }} accept")
        if ipv6:
            lines.append(
                f"add rule inet {_LINUX_TABLE} {_LINUX_CHAIN} "
                f"ip6 daddr {{ {', '.join(ipv6)} }} accept")
        script = "\n".join(lines) + "\n"
        try:
            res = subprocess.run(
                ["nft", "-f", "-"],
                input=script,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.error("Failed to enable Linux Kill Switch: %s", e)
            return tr("killswitch.reason_nft")
        if res.returncode != 0:
            log.error("nftables rejected kill switch rules: %s",
                      (res.stderr or res.stdout or "").strip())
            return tr("killswitch.reason_nft")
        return ""
