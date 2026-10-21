"""sing-box 1.14 config generation: DNS, route rules, inbounds and outbounds.

Route rules use explicit actions (``route``/``reject``/``sniff``/``hijack-dns``);
bare ``outbound`` rules and the ``block`` outbound are deprecated or removed
in the 1.11-1.14 line.
"""
import ipaddress
import logging
import sys
from urllib.parse import urlparse

from .. import linux_tun
from ..server_model import ProxyProtocol, server_identity
from ..i18n import tr
from .singbox_outbounds import (
    _SINGBOX_OUTBOUND_BUILDERS,
    _build_singbox_hysteria2_outbound,
)

log = logging.getLogger("engine.singbox")

_debug_log_enabled = False


def set_debug_logs(enabled: bool) -> None:
    """Set the sing-box log level to full debug output (for --debug runs)."""
    global _debug_log_enabled
    _debug_log_enabled = bool(enabled)

# The wintun adapter the TUN inbound creates. The engine waits for and
# cleans up this adapter by name across restarts.
TUN_ADAPTER_NAME = "socksicle-tun"
TUN_ADDRESS = "172.19.0.1/30"

_DEFAULT_DNS_SERVER = "1.1.1.1"
_DNS_QUERY_PATH = "/dns-query"


def _checked_dns_port(port, target: str) -> int:
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError(f"invalid DNS port in: {target}")
    return port


def _set_domain_resolver(entry: dict, host: str) -> None:
    """Resolve a domain-hosted DNS server via local DNS.

    Without this the server host would be resolved with the tunnel's
    default resolver, i.e. through the very DNS server being dialed.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        entry["domain_resolver"] = "local-dns"


def _build_singbox_dns_server(dns_target: str, tag: str = "remote-dns",
                              detour: str = "proxy") -> dict:
    """Build a sing-box 1.12+ DNS server entry from a DoH/DoT/UDP address.

    Raises ValueError for an unsupported scheme or an invalid port so a bad
    custom DNS setting fails config generation instead of engine startup.
    """
    target = (dns_target or "").strip()

    if target.startswith(("https://", "tls://")):
        parsed = urlparse(target)
        host = parsed.hostname or ""
        if not host:
            raise ValueError(f"invalid DNS address: {dns_target}")
        if parsed.port is not None:
            _checked_dns_port(parsed.port, target)
        if target.startswith("https://"):
            entry: dict = {
                "tag": tag,
                "type": "https",
                "server": host,
                "path": parsed.path or _DNS_QUERY_PATH,
                "detour": detour,
            }
            if parsed.port is not None:
                entry["server_port"] = parsed.port
        else:
            entry = {
                "tag": tag,
                "type": "tls",
                "server": host,
                "server_port": parsed.port or 853,
                "detour": detour,
            }
        _set_domain_resolver(entry, host)
        return entry

    address = target.removeprefix("udp://")
    if "://" in address:
        raise ValueError(f"unsupported DNS scheme: {dns_target}")
    if not address:
        raise ValueError(f"invalid DNS address: {dns_target}")

    port = 53
    if address.startswith("["):
        # [2001:db8::1] or [2001:db8::1]:5353
        host, bracket, rest = address[1:].partition("]")
        if bracket and rest:
            if not rest.startswith(":"):
                raise ValueError(f"invalid DNS address: {dns_target}")
            port = _checked_dns_port(rest[1:], target)
    elif address.count(":") == 1:
        host, port_text = address.split(":", 1)
        port = _checked_dns_port(port_text, target)
    else:
        # Bare IPv4/IPv6, or a domain without a port.
        host = address
    if not host:
        raise ValueError(f"invalid DNS address: {dns_target}")

    entry = {
        "tag": tag,
        "type": "udp",
        "server": host,
        "server_port": port,
        "detour": detour,
    }
    _set_domain_resolver(entry, host)
    return entry


def _build_dns_servers(custom_dns=None) -> list[dict]:
    servers = []
    custom = (custom_dns or "").strip()
    if custom and custom.lower() != "default":
        servers.append(_build_singbox_dns_server(custom, "remote-dns", "proxy"))
    else:
        servers.append({
            "tag": "remote-dns",
            "type": "https",
            "server": _DEFAULT_DNS_SERVER,
            "path": _DNS_QUERY_PATH,
            "detour": "proxy",
        })
    servers.append({
        "tag": "local-dns",
        "type": "local",
        "detour": "direct",
    })
    return servers


def _build_node_outbound(server, hy2_insecure: bool) -> dict:
    """Build one node outbound; the caller assigns the node tag."""
    proto = getattr(server, "protocol", ProxyProtocol.SHADOWSOCKS)
    if proto == ProxyProtocol.HYSTERIA2:
        return _build_singbox_hysteria2_outbound(server, allow_insecure=hy2_insecure)
    builder = _SINGBOX_OUTBOUND_BUILDERS.get(proto)
    if builder is None:
        raise ValueError(tr("engine.unsupported").format(engine="sing-box", proto=proto))
    return builder(server)


MUX_PROTOCOLS = ("h2mux", "smux", "yamux")
MUX_DEFAULT_PROTOCOL = "h2mux"
MUX_MAX_CONNECTIONS_DEFAULT = 4
MUX_MAX_CONNECTIONS_LIMIT = 16
_MUX_OUTBOUND_TYPES = ("shadowsocks", "vless", "vmess", "trojan")


def _build_mux_block(enabled: bool, protocol: str, max_connections) -> dict | None:
    """Validate and build the shared ``multiplex`` block, or None when off."""
    if not enabled:
        return None
    name = (protocol or MUX_DEFAULT_PROTOCOL).strip().lower()
    if name not in MUX_PROTOCOLS:
        raise ValueError(f"unsupported mux protocol: {protocol}")
    if isinstance(max_connections, bool):
        raise ValueError(f"invalid mux max connections: {max_connections}")
    try:
        limit = int(max_connections)
    except (TypeError, ValueError):
        raise ValueError(
            f"invalid mux max connections: {max_connections}") from None
    if not 1 <= limit <= MUX_MAX_CONNECTIONS_LIMIT:
        raise ValueError(f"invalid mux max connections: {max_connections}")
    return {
        "enabled": True,
        "protocol": name,
        "max_connections": limit,
        "padding": False,
    }


def _apply_tunnel_options(outbound: dict, tls_fragment: bool,
                          mux: dict | None) -> None:
    """Attach TLS fragmentation and multiplexing to one node outbound."""
    if tls_fragment:
        tls = outbound.get("tls")
        if isinstance(tls, dict):
            # sing-box 1.12+ TLS options: upstream keys only. The size/sleep
            # object used by the Android fork is not part of the stock schema.
            tls["fragment"] = True
            tls["record_fragment"] = True
            tls["fragment_fallback_delay"] = "500ms"
    if mux is None or outbound.get("type") not in _MUX_OUTBOUND_TYPES:
        return
    # XTLS Vision owns the stream layout, so mux cannot be combined with flow.
    if outbound.get("type") == "vless" and outbound.get("flow"):
        return
    outbound["multiplex"] = dict(mux)


def _generate_config(
    server,
    local_port,
    tun_mode=False,
    custom_dns=None,
    hy2_insecure: bool = False,
    block_udp: bool = False,
    available_servers: list | None = None,
    api_port: int | None = None,
    api_secret: str | None = None,
    server_tag_map_out: dict | None = None,
    tls_fragment: bool = False,
    mux_enabled: bool = False,
    mux_protocol: str = MUX_DEFAULT_PROTOCOL,
    mux_max_connections: int = MUX_MAX_CONNECTIONS_DEFAULT,
) -> dict:
    """Generate a sing-box 1.14 JSON config.

    With ``available_servers`` a selector group plus Clash API controller is
    built so the active node can be hot-switched without restarting the
    tunnel or tearing down the TUN adapter.
    """
    mux_block = _build_mux_block(mux_enabled, mux_protocol, mux_max_connections)
    if available_servers:
        candidates = list(available_servers)
        active_id = server_identity(server)
        if not any(server_identity(s) == active_id for s in candidates):
            candidates.insert(0, server)
    else:
        candidates = [server]
        active_id = server_identity(server)

    server_outbounds: list[dict] = []
    node_tags: list[str] = []
    server_ip_cidrs: list[str] = []
    server_domains: list[str] = []
    tag_map: dict[str, str] = {}

    for candidate in candidates:
        identity = server_identity(candidate)
        if identity in tag_map:
            continue
        is_active = identity == active_id
        try:
            outbound = _build_node_outbound(candidate, hy2_insecure)
        except Exception as e:
            if is_active:
                # The selected server must never silently fall back to
                # another node: surface the failure so start() reports it.
                # ValueError keeps base.start()'s error path (a bare
                # non-ValueError would escape it and stall the UI).
                if isinstance(e, ValueError):
                    raise
                raise ValueError(str(e) or type(e).__name__) from e
            log.debug("Skipping server %s in selector outbounds: %s",
                      getattr(candidate, "name", ""), e)
            continue

        _apply_tunnel_options(outbound, tls_fragment, mux_block)
        node_tag = f"node_{len(server_outbounds)}"
        outbound["tag"] = node_tag
        outbound["domain_resolver"] = "local-dns"
        server_outbounds.append(outbound)
        node_tags.append(node_tag)
        tag_map[identity] = node_tag

        host = getattr(candidate, "host", "") or ""
        if not host:
            continue
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if host not in server_domains:
                server_domains.append(host)
        else:
            cidr = f"{host}/32" if ip.version == 4 else f"{host}/128"
            if cidr not in server_ip_cidrs:
                server_ip_cidrs.append(cidr)

    if server_tag_map_out is not None:
        server_tag_map_out.clear()
        server_tag_map_out.update(tag_map)

    if not server_outbounds:
        raise ValueError(tr("engine.unsupported").format(
            engine="sing-box",
            proto=getattr(server, "protocol", ProxyProtocol.SHADOWSOCKS),
        ))

    # Any active build failure raised above, so the active tag is present.
    active_tag = tag_map[active_id]

    direct_outbound = {"type": "direct", "tag": "direct", "domain_resolver": "local-dns"}
    use_selector = len(node_tags) > 1 or (api_port is not None and api_secret is not None)
    if use_selector:
        outbounds = [
            {
                "type": "selector",
                "tag": "proxy",
                "outbounds": node_tags,
                "default": active_tag,
                "interrupt_exist_connections": True,
            },
            *server_outbounds,
            direct_outbound,
        ]
    else:
        server_outbounds[0]["tag"] = "proxy"
        outbounds = [*server_outbounds, direct_outbound]

    rules: list[dict] = []
    if tun_mode:
        if sys.platform.startswith("linux"):
            # sdelay.txt architecture: nftables fast path and kernel-level
            # bypass pre-matching keep local/game traffic out of sing-box.
            rules.extend(linux_tun.build_pre_match_rules(
                dns_hijack_inbound="tun-in"))
        else:
            rules.append({"action": "sniff"})
            rules.append({"inbound": ["tun-in"], "protocol": "dns",
                          "action": "hijack-dns"})
    else:
        # Sniff only DNS ports so DNS traffic through the mixed inbound is
        # routed to the proxy even when block_udp rejects other UDP.
        rules.append({"port": 53, "action": "sniff"})
        rules.append({"protocol": "dns", "action": "route", "outbound": "proxy"})
    rules.append({"port": 123, "action": "route", "outbound": "direct"})
    if server_ip_cidrs:
        rules.append({"ip_cidr": server_ip_cidrs, "action": "route", "outbound": "direct"})
    if server_domains:
        rules.append({"domain": server_domains, "action": "route", "outbound": "direct"})
    rules.append({"ip_is_private": True, "action": "route", "outbound": "direct"})
    if block_udp:
        rules.append({"network": "udp", "action": "reject"})

    inbounds: list[dict] = []
    if tun_mode:
        if sys.platform.startswith("linux"):
            # sing-box 1.14 TUN inbound: dns_mode hijack + auto_redirect.
            tun_inbound = linux_tun.build_tun_inbound(
                exclude_cidrs=list(server_ip_cidrs) if server_ip_cidrs else None)
        else:
            tun_inbound = {
                "type": "tun",
                "tag": "tun-in",
                "interface_name": TUN_ADAPTER_NAME,
                "address": [TUN_ADDRESS],
                "dns_mode": "hijack",
                "auto_route": True,
                "strict_route": False,
                "stack": "mixed",
            }
            if server_ip_cidrs:
                # Keep direct connections to the proxy endpoints outside the tunnel.
                tun_inbound["route_exclude_address"] = list(server_ip_cidrs)
        inbounds.append(tun_inbound)
    inbounds.append({
        "type": "mixed",
        "tag": "mixed-in",
        "listen": "127.0.0.1",
        "listen_port": int(local_port),
    })

    dns: dict = {
        "servers": _build_dns_servers(custom_dns),
        "strategy": "prefer_ipv4",
    }
    if tun_mode:
        dns["reverse_mapping"] = True

    route: dict = {
        "rules": rules,
        "final": "proxy",
        "default_domain_resolver": "remote-dns",
    }
    if tun_mode:
        route["auto_detect_interface"] = True

    config: dict = {
        "log": {"level": "debug" if _debug_log_enabled else "warn",
                "timestamp": True},
        "dns": dns,
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": route,
    }

    if api_port is not None and api_secret is not None:
        config["experimental"] = {
            "clash_api": {
                "external_controller": f"127.0.0.1:{api_port}",
                "secret": api_secret,
                "default_mode": "rule",
            }
        }

    return config
