"""Xray-core engine implementation.

Manages xray binary discovery, config generation, and process lifecycle.
"""
import logging
from pathlib import Path

from ..server_model import ProxyProtocol
from ..i18n import tr
from .base import ProxyEngine, EngineType
from . import common

log = logging.getLogger("engine.xray")

_TARGET_MAP = {
    ("windows", "amd64"): "windows-64",
    ("windows", "x86_64"): "windows-64",
    ("windows", "arm64"): "windows-arm64-v8a",
    ("windows", "aarch64"): "windows-arm64-v8a",
    ("linux", "amd64"): "linux-64",
    ("linux", "x86_64"): "linux-64",
    ("linux", "arm64"): "linux-arm64-v8a",
    ("linux", "aarch64"): "linux-arm64-v8a",
    ("darwin", "amd64"): "macos-64",
    ("darwin", "x86_64"): "macos-64",
    ("darwin", "arm64"): "macos-arm64-v8a",
    ("darwin", "aarch64"): "macos-arm64-v8a",
}

def _detect_target():
    return common._detect_target(_TARGET_MAP)


def _binary_name():
    return common._binary_name("xray")


def _find_binary() -> Path | None:
    return common._find_binary("xray")


def _check_usable(path) -> "CheckResult":
    return common._check_usable(path, "xray")


def _build_xray_stream_settings(server) -> dict:
    """Build streamSettings for VLESS/VMess outbounds."""
    transport = (getattr(server, 'transport', 'tcp') or 'tcp').lower()
    if transport in ('http', 'h2', 'h3', 'quic'):
        raise ValueError(
            f"Xray 26+ does not support '{transport}' transport (removed in Xray 26; use XHTTP instead)"
        )

    stream = {"network": transport}

    security = (getattr(server, 'security', 'none') or 'none').lower()
    fp = getattr(server, 'fingerprint', 'chrome')
    sni = getattr(server, 'server_name', '') or getattr(server, 'host', '')
    host_header = getattr(server, 'host_header', '')
    path = getattr(server, 'path', '')

    if security == 'tls':
        stream["security"] = "tls"
        tls_settings = {}
        if sni:
            tls_settings["serverName"] = sni
        if fp:
            tls_settings["fingerprint"] = fp
        alpn = getattr(server, 'alpn', None)
        if alpn:
            tls_settings["alpn"] = alpn if isinstance(alpn, list) else [alpn]
        pcs = getattr(server, 'pinned_peer_cert_sha256', '') or getattr(server, 'pcs', '')
        if pcs:
            tls_settings["pinnedPeerCertSha256"] = pcs
        pcn = (
            getattr(server, 'verify_peer_cert_by_name', '')
            or getattr(server, 'verifyPeerCertByName', '')
            or getattr(server, 'pcn', '')
            or getattr(server, 'vcn', '')
        )
        if pcn:
            tls_settings["verifyPeerCertByName"] = pcn
        stream["tlsSettings"] = tls_settings
    elif security == 'reality':
        stream["security"] = "reality"
        reality_settings = {}
        if sni:
            reality_settings["serverName"] = sni
        if fp:
            reality_settings["fingerprint"] = fp
        pbk = getattr(server, 'public_key', '') or getattr(server, 'password', '')
        sid = getattr(server, 'short_id', '')
        spx = getattr(server, 'spider_x', '')
        if pbk:
            reality_settings["publicKey"] = pbk
        if sid:
            reality_settings["shortId"] = sid
        if spx:
            reality_settings["spiderX"] = spx
        stream["realitySettings"] = reality_settings

    if transport == 'ws':
        ws_settings = {}
        if path:
            ws_settings["path"] = path
        if host_header:
            ws_settings["headers"] = {"Host": host_header}
        stream["wsSettings"] = ws_settings
    elif transport == 'grpc':
        grpc_settings = {}
        if path:
            grpc_settings["serviceName"] = path
        stream["grpcSettings"] = grpc_settings
    elif transport == 'xhttp':
        mode = getattr(server, 'xhttp_mode', 'auto') or 'auto'
        if mode not in ("auto", "packet-up", "stream-up", "stream-one"):
            raise ValueError(f"Invalid XHTTP mode: {mode}")
        xhttp_settings = {"mode": mode}
        if path:
            xhttp_settings["path"] = path
        if host_header:
            xhttp_settings["host"] = host_header
        stream["xhttpSettings"] = xhttp_settings
    elif transport == 'kcp':
        kcp_settings = {}
        header_type = getattr(server, 'header_type', '')
        seed = getattr(server, 'seed', '')
        if header_type:
            kcp_settings["header"] = {"type": header_type}
        if seed:
            kcp_settings["seed"] = seed
        if kcp_settings:
            stream["kcpSettings"] = kcp_settings
    elif transport == 'httpupgrade':
        hu_settings = {}
        if path:
            hu_settings["path"] = path
        if host_header:
            hu_settings["host"] = host_header
        stream["httpupgradeSettings"] = hu_settings
    elif transport == 'tcp':
        header_type = getattr(server, 'header_type', '')
        if header_type:
            stream["tcpSettings"] = {"header": {"type": header_type}}

    return stream


def _build_xray_vless_outbound(server) -> dict:
    """Generate outbound dict for a VLESS server."""
    transport = (getattr(server, 'transport', 'tcp') or 'tcp').lower()
    security = (getattr(server, 'security', 'none') or 'none').lower()
    flow = getattr(server, 'flow', '') or ''
    user = {
        "id": getattr(server, 'uuid', ''),
        "encryption": getattr(server, 'encryption', 'none') or 'none',
    }
    if flow and transport in ('tcp', 'raw') and security in ('tls', 'reality'):
        user["flow"] = flow

    settings = {
        "vnext": [{
            "address": server.host,
            "port": int(server.port),
            "users": [user]
        }]
    }
    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": settings,
        "streamSettings": _build_xray_stream_settings(server),
    }


def _build_xray_vmess_outbound(server) -> dict:
    """Generate outbound dict for a VMess server."""
    settings = {
        "vnext": [{
            "address": server.host,
            "port": int(server.port),
            "users": [{
                "id": getattr(server, 'uuid', ''),
                "alterId": int(getattr(server, 'alter_id', 0) or 0),
                "security": getattr(server, 'vmess_security', 'auto') or 'auto',
            }]
        }]
    }
    return {
        "tag": "proxy",
        "protocol": "vmess",
        "settings": settings,
        "streamSettings": _build_xray_stream_settings(server),
    }


def _build_xray_ss_outbound(server) -> dict:
    """Generate outbound dict for a Shadowsocks server."""
    return {
        "tag": "proxy",
        "protocol": "shadowsocks",
        "settings": {
            "servers": [{
                "address": server.host,
                "port": int(server.port),
                "method": server.method,
                "password": server.password,
            }]
        },
        "streamSettings": {"network": "tcp"},
    }


def _build_xray_trojan_outbound(server) -> dict:
    """Generate outbound dict for a Trojan server."""
    return {
        "tag": "proxy",
        "protocol": "trojan",
        "settings": {
            "servers": [{
                "address": server.host,
                "port": int(server.port),
                "password": getattr(server, "password", "") or "",
            }]
        },
        "streamSettings": _build_xray_stream_settings(server),
    }


_XRAY_OUTBOUND_BUILDERS = {
    ProxyProtocol.SHADOWSOCKS: _build_xray_ss_outbound,
    ProxyProtocol.VLESS: _build_xray_vless_outbound,
    ProxyProtocol.VMESS: _build_xray_vmess_outbound,
    ProxyProtocol.TROJAN: _build_xray_trojan_outbound,
}


def _generate_config(server, local_port, custom_dns=None, block_udp: bool = False) -> dict:
    """Generate xray JSON config for a single proxy server.

    ``block_udp`` drops internet UDP traffic via a blackhole outbound
    (applies on reconnect).
    """
    protocol = getattr(server, 'protocol', ProxyProtocol.SHADOWSOCKS)
    builder = _XRAY_OUTBOUND_BUILDERS.get(protocol)
    if builder is None:
        raise ValueError(tr("engine.unsupported").format(engine="Xray", proto=protocol))
    outbound = builder(server)

    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": int(local_port),
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
            }
        ],
        "outbounds": [
            outbound,
            {
                "tag": "direct",
                "protocol": "freedom"
            },
            *([{"tag": "blocked", "protocol": "blackhole"}] if block_udp else []),
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                *([{"type": "field", "network": "udp", "outboundTag": "blocked"}] if block_udp else []),
            ],
        }
    }
    if custom_dns and custom_dns.strip() and custom_dns.strip().lower() != "default":
        target = custom_dns.strip()
        if target.startswith("https://"):
            dns_server = target
        elif target.startswith("tls://"):
            # Xray has no DoT nameserver; passing tls:// through yields a broken
            # UDP lookup, and rewriting it to tcp:// silently drops the
            # encryption the user selected.
            raise ValueError(f"Xray does not support DNS-over-TLS: {target}")
        elif target.startswith("udp://"):
            dns_server = target.replace("udp://", "")
        else:
            dns_server = target
        cfg["dns"] = {
            "servers": [dns_server, "localhost"]
        }
    return cfg


class XrayEngine(ProxyEngine):
    engine_type = EngineType.XRAY

    def __init__(self):
        super().__init__()
        self.custom_dns = None
        self.block_udp = False

    def find_binary(self):
        return _find_binary()

    def check_usable(self, path):
        return _check_usable(path)

    def build_config(self, server):
        return _generate_config(server, self.local_port, custom_dns=self.custom_dns,
                                block_udp=self.block_udp)

    def build_args(self, server):
        return super().build_args(server, ["run", "-c"], "xray-")

    def version_args(self):
        return ["version"]

    def process_name(self):
        return "xray"


