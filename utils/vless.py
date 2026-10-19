"""Comprehensive VLESS protocol support for sing-box 1.14+ and Xray-core 26+.

Provides typed data structures, link parsing (vless://), URI export,
and outbound configuration generators for both sing-box and Xray cores.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, unquote

if TYPE_CHECKING:
    from .server_model import Server

log = logging.getLogger(__name__)

_UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SINGBOX_TRANSPORTS = ("tcp", "ws", "http", "quic", "grpc", "httpupgrade")


def is_private_ip(host: str) -> bool:
    """Return True if host is a valid IP address and is private/loopback/reserved."""
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
    except ValueError:
        return False


@dataclass
class VlessNode:
    """Represents a fully configured VLESS server node."""
    uuid: str
    host: str
    port: int = 443
    name: str = "VLESS Server"
    security: str = "none"         # "none", "tls", "reality"
    flow: str = ""                 # "xtls-rprx-vision"
    transport: str = "tcp"         # "tcp", "ws", "httpupgrade", "xhttp", "grpc", "http", "quic", "kcp"
    server_name: str = ""          # SNI
    fingerprint: str = "chrome"    # uTLS fingerprint
    public_key: str = ""           # REALITY public key (pbk)
    short_id: str = ""             # REALITY short id (sid)
    spider_x: str = ""             # REALITY spiderX (spx)
    path: str = ""                 # WS / xHTTP / HTTP path
    host_header: str = ""          # Host header
    service_name: str = ""         # gRPC serviceName
    xhttp_mode: str = "auto"       # "auto", "stream-up", "stream-one", "packet-up"
    header_type: str = ""          # TCP / mKCP / QUIC header camouflage
    seed: str = ""                 # mKCP seed
    quic_security: str = ""        # QUIC security
    quic_key: str = ""             # QUIC key
    alpn: list[str] = field(default_factory=list)
    insecure: bool = False
    packet_encoding: str = "xudp"  # "xudp", "packetaddr", "none"
    is_private: bool = False
    raw_link: str = ""

    def __post_init__(self) -> None:
        self.security = (self.security or "none").strip().lower()
        self.transport = (self.transport or "tcp").strip().lower()
        if self.transport == "splithttp":
            self.transport = "xhttp"
        elif self.transport == "h2":
            self.transport = "http"
        if not self.fingerprint:
            self.fingerprint = "chrome"
        if self.flow and (self.transport != "tcp" or self.security not in ("tls", "reality")):
            log.debug("Clearing incompatible flow '%s' for transport '%s'", self.flow, self.transport)
            self.flow = ""
        if not self.is_private and self.host:
            self.is_private = is_private_ip(self.host)


def parse_vless_url(raw_url: str, default_name: str = "VLESS Server") -> VlessNode:
    """Parse a vless:// URI into a validated VlessNode."""
    if not raw_url or not raw_url.startswith("vless://"):
        raise ValueError("Invalid VLESS link: missing 'vless://' scheme")

    payload = raw_url[8:]
    name = default_name
    if "#" in payload:
        payload, fragment = payload.rsplit("#", 1)
        decoded = unquote(fragment).strip()
        if decoded:
            name = decoded

    if "@" not in payload:
        raise ValueError("Invalid VLESS link: missing '@' delimiter")

    uuid_part, rest = payload.split("@", 1)
    uuid_str = unquote(uuid_part).strip()
    if not _UUID_REGEX.match(uuid_str):
        raise ValueError(f"Invalid UUID in VLESS link: {uuid_str}")

    hostport, _, query_str = rest.partition("?")
    if hostport.startswith("[") and "]" in hostport:
        host_part, _, port_part = hostport.partition("]")
        host = host_part[1:].strip()
        port = int(port_part[1:]) if port_part.startswith(":") else 443
    elif ":" in hostport:
        host, port_str = hostport.rsplit(":", 1)
        port = int(port_str)
    else:
        host, port = hostport.strip(), 443

    if not host:
        raise ValueError("Invalid VLESS link: missing host")
    if not (1 <= port <= 65535):
        raise ValueError(f"Invalid port in VLESS link: {port}")

    params = parse_qs(query_str, keep_blank_values=True)

    def _get(keys: tuple[str, ...], default: str = "") -> str:
        for k in keys:
            vals = params.get(k)
            if vals and vals[0]:
                return vals[0].strip()
        return default

    alpn_raw = _get(("alpn",))
    alpn = [x.strip() for x in alpn_raw.split(",") if x.strip()] if alpn_raw else []
    insecure = _get(("insecure", "allowInsecure", "skip-cert-verify")).lower() in ("1", "true", "yes", "on")

    return VlessNode(
        uuid=uuid_str, host=host, port=port, name=name,
        security=_get(("security",), "none"),
        transport=_get(("type", "transport", "net"), "tcp"),
        flow=_get(("flow",)),
        server_name=_get(("sni", "peer", "serverName", "servername")),
        fingerprint=_get(("fp", "fingerprint"), "chrome"),
        public_key=_get(("pbk", "publicKey", "pk")),
        short_id=_get(("sid", "shortId")),
        spider_x=_get(("spx", "spiderX")),
        path=unquote(_get(("path",))),
        host_header=_get(("host", "host_header")),
        service_name=_get(("serviceName", "servicename")),
        xhttp_mode=_get(("mode",), "auto"),
        header_type=_get(("headerType", "headertype")),
        seed=_get(("seed",)),
        quic_security=_get(("quicSecurity", "quic_security")),
        quic_key=_get(("key", "quicKey", "quic_key")),
        alpn=alpn, insecure=insecure,
        packet_encoding=_get(("packetEncoding", "packet_encoding"), "xudp"),
        raw_link=raw_url,
    )


def effective_sni(node: VlessNode) -> str:
    """Return explicit SNI or fallback to host if host is a domain name."""
    if node.server_name:
        return node.server_name
    try:
        ipaddress.ip_address(node.host)
        return ""
    except ValueError:
        return node.host


def _default_alpn(transport: str) -> list[str] | None:
    if transport in ("ws", "http", "httpupgrade"):
        return ["http/1.1"]
    if transport == "grpc":
        return ["h2"]
    return None


def generate_singbox_vless_outbound(node: VlessNode, tag: str = "proxy") -> dict:
    """Generate a sing-box 1.14+ outbound configuration for VLESS."""
    if node.transport not in _SINGBOX_TRANSPORTS:
        raise ValueError(
            f"sing-box 1.14+ does not support transport '{node.transport}'. "
            f"Supported transports are: {', '.join(_SINGBOX_TRANSPORTS)}"
        )

    outbound: dict = {
        "type": "vless", "tag": tag, "server": node.host,
        "server_port": int(node.port), "uuid": node.uuid,
    }
    if node.flow and node.transport == "tcp":
        outbound["flow"] = node.flow

    if node.security in ("tls", "reality"):
        tls_block: dict = {"enabled": True}
        sni = effective_sni(node)
        if sni:
            tls_block["server_name"] = sni
        if node.fingerprint:
            tls_block["utls"] = {"enabled": True, "fingerprint": node.fingerprint}
        if node.insecure:
            tls_block["insecure"] = True
        alpn = node.alpn or _default_alpn(node.transport)
        if alpn:
            tls_block["alpn"] = alpn
        if node.security == "reality":
            tls_block["reality"] = {
                "enabled": True,
                "public_key": node.public_key,
                "short_id": node.short_id,
            }
        outbound["tls"] = tls_block

    if node.transport in ("ws", "httpupgrade", "grpc", "http", "quic"):
        transport_block: dict = {"type": node.transport}
        host = node.host_header or effective_sni(node)
        if node.transport == "ws":
            if node.path: transport_block["path"] = node.path
            if host: transport_block["headers"] = {"Host": host}
        elif node.transport == "httpupgrade":
            if node.path: transport_block["path"] = node.path
            if host: transport_block["host"] = host
        elif node.transport == "grpc":
            grpc_service = node.service_name or node.path
            if grpc_service: transport_block["service_name"] = grpc_service
        elif node.transport == "http":
            if node.path: transport_block["path"] = node.path
            if host: transport_block["host"] = [host]
        outbound["transport"] = transport_block

    if node.packet_encoding:
        outbound["packet_encoding"] = node.packet_encoding
    return outbound


def generate_xray_vless_outbound(node: VlessNode, tag: str = "proxy") -> dict:
    """Generate an Xray-core 26+ outbound configuration for VLESS."""
    user: dict = {"id": node.uuid, "encryption": "none"}
    if node.flow and node.transport == "tcp":
        user["flow"] = node.flow

    stream_settings: dict = {"network": node.transport}
    sni = effective_sni(node)

    if node.security == "tls":
        stream_settings["security"] = "tls"
        tls_settings: dict = {}
        if sni:
            tls_settings["serverName"] = sni
        if node.fingerprint:
            tls_settings["fingerprint"] = node.fingerprint
        if node.insecure:
            tls_settings["allowInsecure"] = True
        alpn = node.alpn or _default_alpn(node.transport)
        if alpn:
            tls_settings["alpn"] = alpn
        stream_settings["tlsSettings"] = tls_settings

    elif node.security == "reality":
        stream_settings["security"] = "reality"
        reality_settings: dict = {
            "publicKey": node.public_key,
            "shortId": node.short_id,
        }
        if sni:
            reality_settings["serverName"] = sni
        if node.fingerprint:
            reality_settings["fingerprint"] = node.fingerprint
        if node.spider_x:
            reality_settings["spiderX"] = node.spider_x
        stream_settings["realitySettings"] = reality_settings

    host = node.host_header or sni
    if node.transport == "tcp" and node.header_type:
        stream_settings["tcpSettings"] = {"header": {"type": node.header_type}}
    elif node.transport == "kcp":
        kcp_settings: dict = {"header": {"type": node.header_type or "none"}}
        if node.seed:
            kcp_settings["seed"] = node.seed
        stream_settings["kcpSettings"] = kcp_settings
    elif node.transport == "quic":
        stream_settings["quicSettings"] = {
            "security": node.quic_security or "none",
            "key": node.quic_key or "",
            "header": {"type": node.header_type or "none"},
        }
    elif node.transport == "ws":
        ws: dict = {}
        if node.path:
            ws["path"] = node.path
        if host:
            ws["headers"] = {"Host": host}
        stream_settings["wsSettings"] = ws
    elif node.transport == "httpupgrade":
        hu: dict = {}
        if node.path:
            hu["path"] = node.path
        if host:
            hu["host"] = host
        stream_settings["httpupgradeSettings"] = hu
    elif node.transport == "xhttp":
        xh: dict = {"mode": node.xhttp_mode or "auto"}
        if node.path:
            xh["path"] = node.path
        if host:
            xh["host"] = host
        stream_settings["xhttpSettings"] = xh
    elif node.transport == "grpc":
        grpc_service = node.service_name or node.path
        grpc: dict = {"multiMode": True}
        if grpc_service:
            grpc["serviceName"] = grpc_service
        stream_settings["grpcSettings"] = grpc
    elif node.transport == "http":
        h_s: dict = {}
        if node.path:
            h_s["path"] = node.path
        if host:
            h_s["host"] = [host]
        stream_settings["httpSettings"] = h_s

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": node.host,
                "port": int(node.port),
                "users": [user],
            }]
        },
        "streamSettings": stream_settings,
    }


def export_vless_url(node: VlessNode) -> str:
    """Serialize a VlessNode into a canonical vless:// share URL."""
    fields = [
        ("type", node.transport), ("security", node.security), ("encryption", "none"),
        ("flow", node.flow), ("sni", node.server_name), ("fp", node.fingerprint),
        ("pbk", node.public_key), ("sid", node.short_id), ("spx", node.spider_x),
        ("path", node.path), ("host", node.host_header), ("serviceName", node.service_name),
        ("mode", node.xhttp_mode if node.transport == "xhttp" else ""),
        ("headerType", node.header_type), ("seed", node.seed),
        ("quicSecurity", node.quic_security), ("key", node.quic_key),
        ("alpn", ",".join(node.alpn) if node.alpn else ""),
        ("allowInsecure", "1" if node.insecure else ""),
    ]
    query = "&".join(f"{k}={quote(v)}" for k, v in fields if v)
    fragment = quote(node.name)
    host_str = f"[{node.host}]" if ":" in node.host else node.host
    return f"vless://{node.uuid}@{host_str}:{node.port}?{query}#{fragment}"


def to_server(node: VlessNode) -> Server:
    """Convert a VlessNode into the Socksicle Server dataclass."""
    from .server_model import ProxyProtocol, Server
    return Server(
        key=node.raw_link or export_vless_url(node),
        name=node.name,
        host=node.host,
        port=node.port,
        protocol=ProxyProtocol.VLESS,
        uuid=node.uuid,
        security=node.security,
        transport=node.transport,
        flow=node.flow,
        encryption="none",
        server_name=node.server_name,
        fingerprint=node.fingerprint,
        public_key=node.public_key,
        short_id=node.short_id,
        path=node.path or node.service_name,
        host_header=node.host_header,
        is_private=node.is_private,
        insecure=node.insecure,
    )


def from_server(server: Server) -> VlessNode:
    """Convert a Socksicle Server dataclass into a VlessNode."""
    return VlessNode(
        uuid=getattr(server, "uuid", ""),
        host=server.host,
        port=int(server.port),
        name=server.name,
        security=getattr(server, "security", "none") or "none",
        flow=getattr(server, "flow", "") or "",
        transport=getattr(server, "transport", "tcp") or "tcp",
        server_name=getattr(server, "server_name", "") or "",
        fingerprint=getattr(server, "fingerprint", "chrome") or "chrome",
        public_key=getattr(server, "public_key", "") or "",
        short_id=getattr(server, "short_id", "") or "",
        path=getattr(server, "path", "") or "",
        host_header=getattr(server, "host_header", "") or "",
        insecure=bool(getattr(server, "insecure", False)),
        is_private=bool(getattr(server, "is_private", False)),
        raw_link=getattr(server, "key", "") or "",
    )
