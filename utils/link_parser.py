"""Parse vless://, vmess://, hysteria2:// and hy2:// links into Server objects."""
import base64
import json
import logging
import re
from urllib.parse import parse_qs, unquote

from .server_model import is_private_host
from .i18n import tr

log = logging.getLogger(__name__)


def _default_name(name):
    """Explicit default name, or the translated fallback for untagged links."""
    return name or tr("server.default_name")


def _parse_port(val, default=None) -> int | None:
    if val is None or val == "":
        return default
    try:
        p = int(val)
        return p if (1 <= p <= 65535 and not isinstance(val, bool)) else None
    except (TypeError, ValueError):
        return None


def parse_link(raw_link, default_name=None):
    """Parse a vless://, vmess://, hysteria2://, hy2://, trojan://, or tuic:// link into a Server, or None."""
    if not raw_link:
        return None
    default_name = _default_name(default_name)
    if raw_link.startswith('vless://'):
        return _parse_vless(raw_link, default_name)
    if raw_link.startswith('vmess://'):
        return _parse_vmess(raw_link, default_name)
    if raw_link.startswith(('hysteria2://', 'hy2://')):
        return _parse_hysteria2(raw_link, default_name)
    if raw_link.startswith(('trojan://', 'trojan-go://')):
        return _parse_trojan(raw_link, default_name)
    if raw_link.startswith('tuic://'):
        return _parse_tuic(raw_link, default_name)
    return None


def _b64_decode_padded(s):
    s = s.replace('-', '+').replace('_', '/')
    padded = s + '=' * (-len(s) % 4)
    return base64.b64decode(padded)


def _b64_decode_json(s):
    return json.loads(_b64_decode_padded(s))





_MBPS_RE = re.compile(r'(?i)(mbps|mb/s|m|kbps|k).*')


def _parse_mbps(val: str) -> int:
    """Parse bandwidth string into integer Mbps."""
    if not val:
        return 0
    cleaned = _MBPS_RE.sub('', val).strip()
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return 0


def _parse_hysteria2(raw_link, default_name=None):
    """Parse hysteria2://[auth@]host[:port]?params#Name or hy2://..."""
    default_name = _default_name(default_name)
    try:
        if raw_link.startswith('hysteria2://'):
            payload = raw_link[12:]
        elif raw_link.startswith('hy2://'):
            payload = raw_link[6:]
        else:
            return None

        fragment = ''
        if '#' in payload:
            payload, fragment = payload.rsplit('#', 1)
            fragment = unquote(fragment)
        name = fragment or default_name

        if '@' in payload:
            auth_str, rest = payload.split('@', 1)
            password = unquote(auth_str)
        else:
            password = ""
            rest = payload

        if '?' in rest:
            hostport, query_str = rest.split('?', 1)
        else:
            hostport, query_str = rest, ''

        if not hostport:
            return None

        # Parse host and port (support IPv6 [::1]:443 and [::1])
        if hostport.startswith('[') and ']' in hostport:
            host_part, _, port_part = hostport.partition(']')
            host = host_part[1:]
            port = _parse_port(port_part[1:]) if port_part.startswith(':') else 443
        elif ':' in hostport:
            host, port_str = hostport.rsplit(':', 1)
            port = _parse_port(port_str)
        else:
            host = hostport
            port = 443

        if not host or port is None:
            return None

        params = parse_qs(query_str, keep_blank_values=True)
        def _param(key): return params.get(key, [''])[0]

        if not password:
            password = _param('auth') or _param('password')
        sni = _param('sni') or _param('peer') or _param('serverName') or _param('servername')
        insecure_param = (
            _param('insecure') or _param('allowInsecure') or _param('allow_insecure') or
            _param('skip-cert-verify') or _param('skip_cert_verify') or _param('tls_insecure')
        )
        if insecure_param:
            insecure = insecure_param.lower() not in ('0', 'false', 'no', 'off')
        else:
            insecure = False
        obfs = _param('obfs')
        obfs_password = _param('obfs-password') or _param('obfs_password')
        ports = _param('mport') or _param('ports') or _param('port')
        up_str = _param('up') or _param('upmbps') or _param('up_mbps')
        down_str = _param('down') or _param('downmbps') or _param('down_mbps')
        pcs = _param('pcs') or _param('pinSHA256') or _param('pinnedPeerCertSha256') or _param('pinned_peer_cert_sha256')
        pcn = _param('pcn') or _param('vcn') or _param('verifyPeerCertByName') or _param('verify_peer_cert_by_name')

        is_priv = is_private_host(host)
        if is_priv:
            log.warning("Hysteria2 link points to private/reserved IP: %s", host)

        from .server_model import Server, ProxyProtocol
        return Server(
            key=raw_link,
            name=name,
            host=host,
            port=port,
            protocol=ProxyProtocol.HYSTERIA2,
            password=password,
            server_name=sni,
            insecure=insecure,
            obfs=obfs,
            obfs_password=obfs_password,
            ports=ports,
            up_mbps=_parse_mbps(up_str),
            down_mbps=_parse_mbps(down_str),
            is_private=is_priv,
            pinned_peer_cert_sha256=pcs,
            verify_peer_cert_by_name=pcn,
        )
    except (ValueError, IndexError) as e:
        log.debug("Hysteria2 link parse failed: %s", e)
        return None


def _parse_vless(raw_link, default_name=None):
    """Parse vless://UUID@host:port?params#Name"""
    default_name = _default_name(default_name)
    try:
        payload = raw_link[8:]
        fragment = ''
        if '#' in payload:
            payload, fragment = payload.rsplit('#', 1)
            fragment = unquote(fragment)
        name = fragment or default_name

        if '@' not in payload:
            return None
        uuid, rest = payload.split('@', 1)

        if '?' in rest:
            hostport, query_str = rest.split('?', 1)
        else:
            hostport, query_str = rest, ''

        # Parse host and port (support IPv6 [::1]:443 and hostname:port)
        if hostport.startswith('[') and ']' in hostport:
            host_part, _, port_part = hostport.partition(']')
            host = host_part[1:]
            port = _parse_port(port_part[1:]) if port_part.startswith(':') else None
        elif ':' in hostport:
            host, port_str = hostport.rsplit(':', 1)
            port = _parse_port(port_str)
        else:
            return None

        if not host or port is None:
            return None

        params = parse_qs(query_str, keep_blank_values=True)
        def _param(key): return params.get(key, [''])[0]

        security = _param('security') or 'none'
        transport = _param('type') or _param('transport') or _param('net') or 'tcp'
        flow = _param('flow')
        sni = _param('sni') or _param('peer') or _param('serverName') or _param('servername')
        fp = _param('fp') or _param('fingerprint') or 'chrome'
        pbk = _param('pbk') or _param('publicKey') or _param('pk')
        sid = _param('sid') or _param('shortId')
        enc = _param('encryption') or 'none'
        host_header = _param('host') or _param('host_header') or _param('headerType')
        path = _param('path') or _param('serviceName') or _param('servicename')
        pcs = _param('pcs') or _param('pinSHA256') or _param('pinnedPeerCertSha256') or _param('pinned_peer_cert_sha256')
        pcn = _param('pcn') or _param('vcn') or _param('verifyPeerCertByName') or _param('verify_peer_cert_by_name')
        xhttp_mode = _param('mode') or _param('xhttp_mode') or 'auto'
        alpn_str = _param('alpn')
        alpn = [p.strip() for p in alpn_str.split(',') if p.strip()] if alpn_str else []

        is_priv = is_private_host(host)
        if is_priv:
            log.warning("VLESS link points to private/reserved IP: %s", host)

        from .server_model import Server, ProxyProtocol
        return Server(
            key=raw_link,
            name=name,
            host=host,
            port=port,
            protocol=ProxyProtocol.VLESS,
            uuid=uuid,
            security=security,
            transport=transport,
            flow=flow,
            encryption=enc,
            server_name=sni,
            fingerprint=fp,
            public_key=pbk,
            short_id=sid,
            path=path,
            host_header=host_header,
            pinned_peer_cert_sha256=pcs,
            verify_peer_cert_by_name=pcn,
            is_private=is_priv,
            xhttp_mode=xhttp_mode,
            alpn=alpn,
        )
    except (ValueError, IndexError) as e:
        log.debug("VLESS link parse failed: %s", e)
        return None


def _parse_vmess(raw_link, default_name=None):
    """Parse vmess://base64(JSON)"""
    default_name = _default_name(default_name)
    try:
        payload = raw_link[8:]
        data = _b64_decode_json(payload)
        if not isinstance(data, dict):
            return None

        name = data.get('ps', '') or default_name
        host = data.get('add', '')
        port = _parse_port(data.get('port'), default=443)
        if port is None:
            return None
        uuid = data.get('id', '')
        alter_id = int(data.get('aid') or 0)
        vmess_sec = data.get('scy', 'auto')
        net = data.get('net', 'tcp')
        tls = data.get('tls', '')
        sni = data.get('sni', '')
        fp = data.get('fp', 'chrome')
        host_header = data.get('host', '')
        path = data.get('path', '')

        security = 'tls' if tls == 'tls' else 'none'

        is_priv = is_private_host(host)
        if is_priv:
            log.warning("VMess link points to private/reserved IP: %s", host)

        alpn_raw = data.get('alpn', [])
        if isinstance(alpn_raw, str):
            alpn = [p.strip() for p in alpn_raw.split(',') if p.strip()]
        elif isinstance(alpn_raw, list):
            alpn = [str(p).strip() for p in alpn_raw if str(p).strip()]
        else:
            alpn = []

        from .server_model import Server, ProxyProtocol
        return Server(
            key=raw_link,
            name=name,
            host=host,
            port=port,
            protocol=ProxyProtocol.VMESS,
            uuid=uuid,
            security=security,
            transport=net,
            encryption='auto',
            server_name=sni,
            fingerprint=fp,
            alter_id=alter_id,
            vmess_security=vmess_sec,
            path=path,
            host_header=host_header,
            is_private=is_priv,
            alpn=alpn,
        )
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        log.debug("VMess link parse failed: %s", e)
        return None


def _parse_trojan(raw_link, default_name=None):
    """Parse trojan://password@host:port?params#Name or trojan-go://..."""
    default_name = _default_name(default_name)
    try:
        if raw_link.startswith('trojan://'):
            payload = raw_link[9:]
        elif raw_link.startswith('trojan-go://'):
            payload = raw_link[12:]
        else:
            return None

        fragment = ''
        if '#' in payload:
            payload, fragment = payload.rsplit('#', 1)
            fragment = unquote(fragment)
        name = fragment or default_name

        if '@' not in payload:
            return None
        auth_str, rest = payload.split('@', 1)
        password = unquote(auth_str)

        if '?' in rest:
            hostport, query_str = rest.split('?', 1)
        else:
            hostport, query_str = rest, ''

        if hostport.startswith('[') and ']' in hostport:
            host_part, _, port_part = hostport.partition(']')
            host = host_part[1:]
            port = _parse_port(port_part[1:]) if port_part.startswith(':') else 443
        elif ':' in hostport:
            host, port_str = hostport.rsplit(':', 1)
            port = _parse_port(port_str)
        else:
            host = hostport
            port = 443

        if not host or port is None:
            return None

        params = parse_qs(query_str, keep_blank_values=True)
        def _param(key): return params.get(key, [''])[0]

        sni = _param('sni') or _param('peer') or _param('serverName') or _param('servername')
        fp = _param('fp') or _param('fingerprint') or 'chrome'
        transport = _param('type') or _param('transport') or _param('net') or 'tcp'
        if transport == 'original':
            transport = 'tcp'
        host_header = _param('host') or _param('host_header') or _param('headerType')
        path = _param('path')
        pcs = _param('pcs') or _param('pinSHA256') or _param('pinnedPeerCertSha256') or _param('pinned_peer_cert_sha256')
        pcn = _param('pcn') or _param('vcn') or _param('verifyPeerCertByName') or _param('verify_peer_cert_by_name')
        alpn_str = _param('alpn')
        alpn = [p.strip() for p in alpn_str.split(',') if p.strip()] if alpn_str else []
        sec_param = _param('security').lower()
        security = sec_param if sec_param else "tls"
        insecure_param = (
            _param('insecure') or _param('allowInsecure') or _param('allow_insecure') or
            _param('skip-cert-verify') or _param('skip_cert_verify') or _param('tls_insecure')
        )
        insecure = insecure_param.lower() not in ('0', 'false', 'no', 'off') if insecure_param else False

        is_priv = is_private_host(host)
        if is_priv:
            log.warning("Trojan link points to private/reserved IP: %s", host)

        from .server_model import Server, ProxyProtocol
        return Server(
            key=raw_link,
            name=name,
            host=host,
            port=port,
            protocol=ProxyProtocol.TROJAN,
            password=password,
            security=security,
            transport=transport,
            server_name=sni,
            fingerprint=fp,
            path=path,
            host_header=host_header,
            insecure=insecure,
            pinned_peer_cert_sha256=pcs,
            verify_peer_cert_by_name=pcn,
            is_private=is_priv,
            alpn=alpn,
        )
    except (ValueError, IndexError) as e:
        log.debug("Trojan link parse failed: %s", e)
        return None


def _parse_tuic(raw_link, default_name=None):
    """Parse tuic://[uuid:]password@host:port?params#Name"""
    default_name = _default_name(default_name)
    try:
        if not raw_link.startswith('tuic://'):
            return None
        payload = raw_link[7:]

        fragment = ''
        if '#' in payload:
            payload, fragment = payload.rsplit('#', 1)
            fragment = unquote(fragment)
        name = fragment or default_name

        if '@' not in payload:
            return None
        auth_str, rest = payload.split('@', 1)
        auth = unquote(auth_str)

        if ':' in auth:
            uuid, password = auth.split(':', 1)
        else:
            uuid = ""
            password = auth

        if '?' in rest:
            hostport, query_str = rest.split('?', 1)
        else:
            hostport, query_str = rest, ''

        if hostport.startswith('[') and ']' in hostport:
            host_part, _, port_part = hostport.partition(']')
            host = host_part[1:]
            port = _parse_port(port_part[1:]) if port_part.startswith(':') else 8443
        elif ':' in hostport:
            host, port_str = hostport.rsplit(':', 1)
            port = _parse_port(port_str)
        else:
            host = hostport
            port = 8443

        if not host or port is None:
            return None

        params = parse_qs(query_str, keep_blank_values=True)
        def _param(key): return params.get(key, [''])[0]

        sni = _param('sni') or _param('peer') or _param('serverName') or _param('servername')
        congestion = _param('congestion_control') or _param('congestion') or 'bbr'
        udp_relay_mode = _param('udp_relay_mode') or 'native'
        insecure_param = (
            _param('insecure') or _param('allowInsecure') or _param('allow_insecure') or
            _param('skip-cert-verify') or _param('skip_cert_verify') or _param('tls_insecure')
        )
        insecure = insecure_param.lower() not in ('0', 'false', 'no', 'off') if insecure_param else False

        is_priv = is_private_host(host)
        if is_priv:
            log.warning("TUIC link points to private/reserved IP: %s", host)

        from .server_model import Server, ProxyProtocol
        return Server(
            key=raw_link,
            name=name,
            host=host,
            port=port,
            protocol=ProxyProtocol.TUIC,
            uuid=uuid,
            password=password,
            security="tls",
            server_name=sni,
            congestion_control=congestion,
            udp_relay_mode=udp_relay_mode,
            insecure=insecure,
            is_private=is_priv,
        )
    except (ValueError, IndexError) as e:
        log.debug("TUIC link parse failed: %s", e)
        return None

