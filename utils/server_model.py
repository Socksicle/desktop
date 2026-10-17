"""Server model shared across the app."""
import functools
import ipaddress
import logging
import time
from dataclasses import dataclass
from enum import Enum

from .i18n import tr
from .shadowsocks_parser import decode_ss_link

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1024)
def is_private_host(host: str) -> bool:
    """Return True if *host* resolves to a private/loopback/reserved IP."""
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
    except ValueError:
        return False


class ProxyProtocol(str, Enum):
    SHADOWSOCKS = "shadowsocks"
    VLESS = "vless"
    VMESS = "vmess"
    HYSTERIA2 = "hysteria2"
    TROJAN = "trojan"
    TUIC = "tuic"


@dataclass
class Server:
    key: str = ""
    name: str = "Server"
    host: str = ""
    port: int = 443
    method: str = "aes-256-gcm"
    password: str = ""
    plugin: str = ""
    plugin_opts: str = ""
    protocol: ProxyProtocol = ProxyProtocol.SHADOWSOCKS
    uuid: str = ""
    security: str = "none"
    transport: str = "tcp"
    flow: str = ""
    encryption: str = "none"
    server_name: str = ""
    fingerprint: str = "chrome"
    public_key: str = ""
    short_id: str = ""
    alter_id: int = 0
    vmess_security: str = "auto"
    path: str = ""
    host_header: str = ""
    is_private: bool = False
    insecure: bool = False
    obfs: str = ""
    obfs_password: str = ""
    ports: str = ""
    up_mbps: int = 0
    down_mbps: int = 0
    congestion_control: str = "bbr"
    udp_relay_mode: str = "native"
    lock_export: bool = False
    expires_at: int | None = None
    flag_emoji: str = "🌐"
    country_code: str = "UN"

    def __post_init__(self):
        if self.flag_emoji == "🌐" or not self.flag_emoji:
            try:
                from .geo_utils import detect_flag, extract_name_and_emoji
                detected_flag, code = detect_flag(self.name, self.host)
                _, emoji = extract_name_and_emoji(self.name, detected_flag)
                self.flag_emoji = emoji
                self.country_code = code
            except (ImportError, AttributeError, ValueError) as e:
                log.debug("Flag detection skipped for %s: %s", self.host, e)

    @property
    def is_expired(self) -> bool:
        """Return True if expires_at is set and current time is past it."""
        if self.expires_at is None or self.expires_at <= 0:
            return False
        return time.time() >= self.expires_at

    @classmethod
    def from_link(cls, raw_link, default_name=None):
        """Parse an ss://, vless://, vmess://, hysteria2://, or hy2:// link into a Server, or None."""
        if not raw_link:
            return None
        if default_name is None:
            default_name = tr("server.default_name")
        if raw_link.startswith(('vless://', 'vmess://', 'hysteria2://', 'hy2://', 'trojan://', 'trojan-go://', 'tuic://')):
            from .link_parser import parse_link
            return parse_link(raw_link, default_name=default_name)
        data = decode_ss_link(raw_link)
        if not data:
            return None
        host = data.get('server', '')
        is_priv = is_private_host(host)
        if is_priv:
            log.warning("SS link points to private/reserved IP: %s", host)
        return cls(
            key=raw_link,
            name=data.get('tag', default_name) or default_name,
            host=host,
            port=int(data.get('port', 443)),
            method=data.get('method', 'aes-256-gcm'),
            password=data.get('password', ''),
            plugin=data.get('plugin', ''),
            plugin_opts=data.get('plugin_opts', ''),
            is_private=is_priv,
        )

    @classmethod
    def from_dict(cls, data):
        """Build a Server from a stored dict (servers.json / subscription)."""
        if not isinstance(data, dict):
            data = {}
        try:
            port = int(data.get('port', 443))
        except (TypeError, ValueError):
            port = 443
        try:
            protocol = ProxyProtocol(data.get('protocol', 'shadowsocks'))
        except ValueError:
            protocol = ProxyProtocol.SHADOWSOCKS
        try:
            alter_id = int(data.get('alter_id', 0))
        except (TypeError, ValueError):
            alter_id = 0
        try:
            up_mbps = int(data.get('up_mbps', 0))
        except (TypeError, ValueError):
            up_mbps = 0
        try:
            down_mbps = int(data.get('down_mbps', 0))
        except (TypeError, ValueError):
            down_mbps = 0
        expires_at_raw = data.get('expires_at')
        expires_at = None
        if expires_at_raw is not None:
            try:
                expires_at = int(expires_at_raw)
            except (ValueError, TypeError):
                expires_at = None
        lock_export = bool(data.get('lock_export', False))
        return cls(
            key=data.get('key', ''),
            name=data.get('name') or tr("server.default_name"),
            host=data.get('host', ''),
            port=port,
            method=data.get('method', 'aes-256-gcm'),
            password=data.get('password', ''),
            plugin=data.get('plugin', ''),
            plugin_opts=data.get('plugin_opts', ''),
            protocol=protocol,
            uuid=data.get('uuid', ''),
            security=data.get('security', 'none'),
            transport=data.get('transport', 'tcp'),
            flow=data.get('flow', ''),
            encryption=data.get('encryption', 'none'),
            server_name=data.get('server_name', ''),
            fingerprint=data.get('fingerprint', 'chrome'),
            public_key=data.get('public_key', ''),
            short_id=data.get('short_id', ''),
            alter_id=alter_id,
            vmess_security=data.get('vmess_security', 'auto'),
            path=data.get('path', ''),
            host_header=data.get('host_header', ''),
            is_private=data.get('is_private', False),
            insecure=bool(data.get('insecure', False)),
            obfs=data.get('obfs', ''),
            obfs_password=data.get('obfs_password', ''),
            ports=data.get('ports', ''),
            up_mbps=up_mbps,
            down_mbps=down_mbps,
            congestion_control=data.get('congestion_control', 'bbr') or 'bbr',
            udp_relay_mode=data.get('udp_relay_mode', 'native') or 'native',
            lock_export=lock_export,
            expires_at=expires_at,
            flag_emoji=data.get('flag_emoji', '🌐'),
            country_code=data.get('country_code', 'UN'),
        )

    def to_dict(self):
        """Serialize to storage format."""
        d = {
            "key": self.key,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "method": self.method,
            "password": self.password,
        }
        if self.plugin:
            d["plugin"] = self.plugin
        if self.plugin_opts:
            d["plugin_opts"] = self.plugin_opts
        if self.protocol != ProxyProtocol.SHADOWSOCKS:
            d["protocol"] = self.protocol.value
        if self.uuid:
            d["uuid"] = self.uuid
        if self.security != "none":
            d["security"] = self.security
        if self.transport != "tcp":
            d["transport"] = self.transport
        if self.flow:
            d["flow"] = self.flow
        if self.encryption != "none":
            d["encryption"] = self.encryption
        if self.server_name:
            d["server_name"] = self.server_name
        if self.fingerprint and self.fingerprint != "chrome":
            d["fingerprint"] = self.fingerprint
        if self.public_key:
            d["public_key"] = self.public_key
        if self.short_id:
            d["short_id"] = self.short_id
        if self.alter_id:
            d["alter_id"] = self.alter_id
        if self.vmess_security and self.vmess_security != "auto":
            d["vmess_security"] = self.vmess_security
        if self.path:
            d["path"] = self.path
        if self.host_header:
            d["host_header"] = self.host_header
        if self.is_private:
            d["is_private"] = True
        if self.insecure:
            d["insecure"] = True
        if self.obfs:
            d["obfs"] = self.obfs
        if self.obfs_password:
            d["obfs_password"] = self.obfs_password
        if self.ports:
            d["ports"] = self.ports
        if self.up_mbps:
            d["up_mbps"] = self.up_mbps
        if self.down_mbps:
            d["down_mbps"] = self.down_mbps
        if self.congestion_control and self.congestion_control != "bbr":
            d["congestion_control"] = self.congestion_control
        if self.udp_relay_mode and self.udp_relay_mode != "native":
            d["udp_relay_mode"] = self.udp_relay_mode
        if self.lock_export:
            d["lock_export"] = True
        if self.expires_at is not None:
            d["expires_at"] = self.expires_at
        if self.flag_emoji and self.flag_emoji != "🌐":
            d["flag_emoji"] = self.flag_emoji
        if self.country_code and self.country_code != "UN":
            d["country_code"] = self.country_code
        return d

    @property
    def unique_key(self):
        if self.protocol == ProxyProtocol.SHADOWSOCKS:
            return f"{self.method}:{self.password}@{self.host}:{self.port}"
        if self.protocol == ProxyProtocol.VMESS:
            return f"vmess:{self.uuid}@{self.host}:{self.port}"
        if self.protocol == ProxyProtocol.HYSTERIA2:
            return f"hysteria2:{self.password}@{self.host}:{self.port}"
        if self.protocol == ProxyProtocol.TROJAN:
            return f"trojan:{self.password}@{self.host}:{self.port}"
        if self.protocol == ProxyProtocol.TUIC:
            return f"tuic:{self.uuid}@{self.host}:{self.port}"
        return f"vless:{self.uuid}@{self.host}:{self.port}"

    @property
    def display_protocol(self):
        return self.protocol.value.upper()


def server_identity(srv) -> str:
    """Stable identity for row reuse and selection restore.

    Prefers the persistent ``key``, then the content-derived ``unique_key``,
    then host:port. Objects with no stable id fall back to their address, so
    only the very same object reuses a row.
    """
    if srv is None:
        return "addr:none"
    key = (getattr(srv, "key", "") or "").strip()
    if key:
        return key
    unique_key = getattr(srv, "unique_key", "") or ""
    if unique_key:
        return unique_key
    host = getattr(srv, "host", "") or ""
    port = getattr(srv, "port", "") or ""
    if host or port:
        return f"hp:{host}:{port}"
    return f"addr:{id(srv)}"