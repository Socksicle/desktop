"""sing-box outbound builders for the protocols this app supports.

Builders return plain dicts without a final tag; the config generator assigns
node tags. Data imported from links is constrained here: values sing-box
cannot express (unknown transport, malformed port range, unknown obfs type)
raise ValueError instead of producing a config that only fails at engine start.
"""
import ipaddress
import logging

from ..server_model import ProxyProtocol

log = logging.getLogger("engine.singbox")

# V2Ray transports implemented by sing-box 1.14.
_V2RAY_TRANSPORTS = ("http", "ws", "quic", "grpc", "httpupgrade")

# Obfs types accepted by the hysteria2 outbound.
_HYSTERIA2_OBFS_TYPES = ("salamander", "gecko")


def _transport_type(server) -> str:
    return (getattr(server, "transport", "tcp") or "tcp").strip().lower()


def _host_header(server) -> str:
    return (getattr(server, "host_header", "") or "").strip() or \
        (getattr(server, "server_name", "") or "").strip()


def _server_name(server) -> str:
    """SNI: the explicit server_name, else the host when it is not an IP."""
    sni = (getattr(server, "server_name", "") or "").strip()
    if sni:
        return sni
    host = (getattr(server, "host", "") or "").strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return ""


def _tls_insecure(server) -> bool:
    """Explicit per-server TLS verification opt-out (imported link flag or pinned cert on older core)."""
    return bool(
        getattr(server, "allow_insecure", False)
        or getattr(server, "insecure", False)
        or getattr(server, "pinned_peer_cert_sha256", False)
        or getattr(server, "pcs", False)
    )


def _tls_alpn(transport_type: str) -> list[str] | None:
    if transport_type in ("http", "ws", "httpupgrade"):
        return ["http/1.1"]
    if transport_type == "grpc":
        return ["h2"]
    return None


def _v2ray_tls(server) -> dict:
    """TLS block for VLESS/VMess/Trojan: uTLS fingerprint and transport ALPN."""
    tls: dict = {"enabled": True}
    sni = _server_name(server)
    if sni:
        tls["server_name"] = sni
    tls["utls"] = {
        "enabled": True,
        "fingerprint": getattr(server, "fingerprint", "") or "chrome",
    }
    if _tls_insecure(server):
        tls["insecure"] = True
    alpn = _tls_alpn(_transport_type(server))
    if alpn:
        tls["alpn"] = alpn
    return tls


def _build_singbox_transport(server) -> dict | None:
    """Translate the link transport into a sing-box transport block.

    Returns None for plain TCP. Raises ValueError for transports sing-box
    does not implement (xhttp, h2, kcp, mKCP, ...): mapping them onto a
    different sing-box transport would connect with the wrong framing.
    """
    transport_type = _transport_type(server)
    if transport_type in ("", "tcp", "none"):
        return None
    if transport_type not in _V2RAY_TRANSPORTS:
        raise ValueError(f"sing-box does not support transport: {transport_type}")

    path = (getattr(server, "path", "") or "").strip()
    host_header = _host_header(server)

    if transport_type == "ws":
        transport: dict = {"type": "ws"}
        if path:
            transport["path"] = path
        if host_header:
            transport["headers"] = {"Host": host_header}
        return transport
    if transport_type == "http":
        transport = {"type": "http"}
        if host_header:
            transport["host"] = [host_header]
        if path:
            transport["path"] = path
        return transport
    if transport_type == "httpupgrade":
        transport = {"type": "httpupgrade"}
        if path:
            transport["path"] = path
        if host_header:
            transport["host"] = host_header
        return transport
    if transport_type == "grpc":
        transport = {"type": "grpc"}
        if path:
            transport["service_name"] = path
        return transport
    return {"type": "quic"}


def _build_singbox_vless_outbound(server) -> dict:
    outbound: dict = {
        "type": "vless",
        "tag": "proxy",
        "server": server.host,
        "server_port": int(server.port),
        "uuid": getattr(server, "uuid", ""),
    }

    flow = getattr(server, "flow", "") or ""
    if flow and _transport_type(server) == "tcp":
        outbound["flow"] = flow

    security = getattr(server, "security", "none") or "none"
    if security != "none":
        tls = _v2ray_tls(server)
        if security == "reality":
            tls["reality"] = {
                "enabled": True,
                "public_key": getattr(server, "public_key", "") or "",
                "short_id": getattr(server, "short_id", "") or "",
            }
        outbound["tls"] = tls

    transport = _build_singbox_transport(server)
    if transport:
        outbound["transport"] = transport
    return outbound


def _build_singbox_vmess_outbound(server) -> dict:
    outbound: dict = {
        "type": "vmess",
        "tag": "proxy",
        "server": server.host,
        "server_port": int(server.port),
        "uuid": getattr(server, "uuid", ""),
        "alter_id": int(getattr(server, "alter_id", 0) or 0),
        "security": getattr(server, "vmess_security", "auto") or "auto",
    }

    if (getattr(server, "security", "none") or "none") != "none":
        outbound["tls"] = _v2ray_tls(server)

    transport = _build_singbox_transport(server)
    if transport:
        outbound["transport"] = transport
    return outbound


def _build_singbox_ss_outbound(server) -> dict:
    return {
        "type": "shadowsocks",
        "tag": "proxy",
        "server": server.host,
        "server_port": int(server.port),
        "method": server.method,
        "password": server.password,
        "multiplex": {"enabled": False},
    }


def _hysteria2_server_ports(ports: str) -> list[str]:
    """Convert a hysteria2 ``mport`` list into sing-box ``server_ports``.

    Links carry "443,10000-20000"; sing-box requires ":" ranges
    (hysteria.ParsePorts) and rejects a bare port, so every item is
    normalised to "start:end".
    """
    result = []
    for item in ports.split(","):
        item = item.strip()
        if not item:
            continue
        separator = "-" if "-" in item else ":"
        if separator in item:
            start_text, _, end_text = item.partition(separator)
            start = _checked_port(start_text, ports)
            end = _checked_port(end_text, ports)
            if start > end:
                raise ValueError(f"invalid hysteria2 port range: {item}")
        else:
            start = end = _checked_port(item, ports)
        result.append(f"{start}:{end}")
    return result


def _checked_port(text: str, context: str) -> int:
    try:
        port = int(text)
    except ValueError:
        raise ValueError(f"invalid hysteria2 port range: {context}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"invalid hysteria2 port range: {context}")
    return port


def _hysteria2_obfs(server) -> dict | None:
    """Obfs block; a password without an explicit type means salamander."""
    obfs_type = (getattr(server, "obfs", "") or "").strip().lower()
    password = getattr(server, "obfs_password", "") or ""
    if not obfs_type and not password:
        return None
    if not obfs_type:
        obfs_type = "salamander"
    if obfs_type not in _HYSTERIA2_OBFS_TYPES:
        raise ValueError(f"unknown hysteria2 obfs type: {obfs_type}")
    obfs = {"type": obfs_type}
    if password:
        obfs["password"] = password
    return obfs


def _build_singbox_hysteria2_outbound(server, allow_insecure: bool = False) -> dict:
    """Hysteria2 outbound.

    ``allow_insecure`` is the user's explicit global opt-in for self-signed
    hy2 servers; per-server ``insecure``/``allow_insecure`` flags (manual
    links) keep working regardless of it.
    """
    outbound: dict = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": server.host,
        "server_port": int(server.port),
    }

    password = getattr(server, "password", "") or ""
    if password:
        outbound["password"] = password

    ports = _hysteria2_server_ports(getattr(server, "ports", "") or "")
    if ports:
        outbound["server_ports"] = ports

    for key in ("up_mbps", "down_mbps"):
        value = getattr(server, key, 0) or 0
        if value:
            outbound[key] = int(value)

    obfs = _hysteria2_obfs(server)
    if obfs:
        outbound["obfs"] = obfs

    tls: dict = {"enabled": True}
    sni = _server_name(server)
    if sni:
        tls["server_name"] = sni
    if _tls_insecure(server) or allow_insecure:
        tls["insecure"] = True
    outbound["tls"] = tls

    return outbound


def _build_singbox_trojan_outbound(server) -> dict:
    outbound: dict = {
        "type": "trojan",
        "tag": "proxy",
        "server": server.host,
        "server_port": int(server.port),
        "password": getattr(server, "password", "") or "",
        "multiplex": {"enabled": False},
    }
    sec = (getattr(server, "security", "tls") or "tls").lower()
    if sec != "none":
        outbound["tls"] = _v2ray_tls(server)

    transport = _build_singbox_transport(server)
    if transport:
        outbound["transport"] = transport
    return outbound


def _build_singbox_tuic_outbound(server) -> dict:
    outbound: dict = {
        "type": "tuic",
        "tag": "proxy",
        "server": server.host,
        "server_port": int(server.port),
        "uuid": getattr(server, "uuid", ""),
        "password": getattr(server, "password", "") or "",
        "congestion_control": getattr(server, "congestion_control", "bbr") or "bbr",
        "udp_relay_mode": getattr(server, "udp_relay_mode", "native") or "native",
        "zero_rtt_handshake": False,
    }
    tls: dict = {"enabled": True, "alpn": ["h3"]}
    sni = _server_name(server)
    if sni:
        tls["server_name"] = sni
    if _tls_insecure(server):
        tls["insecure"] = True
    outbound["tls"] = tls
    return outbound


# Hysteria2 is built separately because of its allow_insecure opt-in.
_SINGBOX_OUTBOUND_BUILDERS = {
    ProxyProtocol.SHADOWSOCKS: _build_singbox_ss_outbound,
    ProxyProtocol.VLESS: _build_singbox_vless_outbound,
    ProxyProtocol.VMESS: _build_singbox_vmess_outbound,
    ProxyProtocol.TROJAN: _build_singbox_trojan_outbound,
    ProxyProtocol.TUIC: _build_singbox_tuic_outbound,
}
