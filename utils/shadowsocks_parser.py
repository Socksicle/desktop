import base64
import binascii
import logging
from urllib.parse import urlparse, unquote, parse_qs

log = logging.getLogger(__name__)

_AEAD_2022_PREFIXES = ("2022-blake3-",)


def _is_aead_2022_method(method):
    return any(method.startswith(p) for p in _AEAD_2022_PREFIXES)


def _unescape_plugin_string(s):
    r"""Unescape Shadowsocks plugin escape sequences: \; -> ;, \= -> =, \\ -> \\."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in (';', '=', '\\'):
                result.append(nxt)
                i += 2
                continue
        result.append(s[i])
        i += 1
    return ''.join(result)


def _parse_plugin(raw_plugin):
    """Parse plugin string like 'obfs-local;obfs=http;obfs-host=example.com'
    into (plugin_name, plugin_opts_string)."""
    raw_plugin = _unescape_plugin_string(raw_plugin)
    parts = raw_plugin.split(';', 1)
    plugin_name = parts[0].strip()
    plugin_opts = parts[1].strip() if len(parts) > 1 else ""
    return plugin_name, plugin_opts


def decode_ss_link(ss_link):
    """Parse a ss:// link into a dict with keys:
    server, port, method, password, tag, plugin, plugin_opts.

    Supports:
    - Legacy whole-URI base64: ss://BASE64(method:password)@host:port#tag
    - SIP002 base64 userinfo: ss://BASE64(method:password)@host:port/?plugin=...#tag
    - AEAD-2022 plain userinfo: ss://method:password@host:port#tag
    """
    if not ss_link or not ss_link.startswith('ss://'):
        return None

    payload = ss_link[5:]

    tag = ''
    if '#' in payload:
        payload, raw_tag = payload.split('#', 1)
        tag = unquote(raw_tag)

    plugin = ''
    plugin_opts = ''

    # Extract query string (for plugin)
    query_string = ''
    if '?' in payload:
        payload, query_string = payload.split('?', 1)
        qs = parse_qs(query_string, keep_blank_values=True)
        raw_plugin = qs.get('plugin', [''])[0]
        if raw_plugin:
            decoded_plugin = unquote(raw_plugin)
            plugin, plugin_opts = _parse_plugin(decoded_plugin)

    # Legacy base64 payload first, then SIP002 URL-style.
    try:
        padded = payload + '=' * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode('utf-8')
        if ':' in decoded and '@' in decoded:
            method, rest = decoded.split(':', 1)
            password, server_port = rest.rsplit('@', 1)
            server, port = server_port.rsplit(':', 1)
            return {
                'server': server,
                'port': int(port),
                'method': method,
                'password': password,
                'tag': tag,
                'plugin': plugin,
                'plugin_opts': plugin_opts,
            }
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        log.debug("Legacy base64 decode failed: %s", e)

    try:
        parsed = urlparse('ss://' + payload)
        server = parsed.hostname
        port = parsed.port

        if not server or not port:
            return None

        # urlparse splits method:password into username:password when ':' is present
        method = parsed.username
        password = parsed.password

        if method is None:
            # ss://host:port carries no userinfo, so no method/password at all
            return None

        if password is not None:
            # urlparse found a ':' in the raw userinfo
            # Could be SIP002 (base64-encoded) or AEAD-2022 (plain)
            # Try base64 decoding the full userinfo first (SIP002)
            try:
                full_userinfo = f"{method}:{password}"
                padded_ui = full_userinfo + '=' * (-len(full_userinfo) % 4)
                decoded_ui = base64.urlsafe_b64decode(padded_ui).decode('utf-8')
                if ':' in decoded_ui:
                    method, password = decoded_ui.split(':', 1)
                    return {
                        'server': server,
                        'port': int(port),
                        'method': method,
                        'password': password,
                        'tag': tag,
                        'plugin': plugin,
                        'plugin_opts': plugin_opts,
                    }
            except (binascii.Error, ValueError, UnicodeDecodeError) as e:
                log.debug("SIP002 userinfo base64 decode failed: %s", e)

            # Base64 failed -> AEAD-2022 plain userinfo
            # password from urlparse is NOT percent-decoded, so unquote it
            password = unquote(password)
        else:
            # No ':' in raw userinfo -> must be base64-encoded (SIP002)
            # method is actually the entire base64 userinfo
            userinfo = method
            if not userinfo:
                return None
            try:
                padded_ui = userinfo + '=' * (-len(userinfo) % 4)
                decoded_ui = base64.urlsafe_b64decode(padded_ui).decode('utf-8')
                if ':' in decoded_ui:
                    method, password = decoded_ui.split(':', 1)
                else:
                    return None
            except (binascii.Error, ValueError, UnicodeDecodeError) as e:
                log.debug("No-password base64 decode failed: %s", e)
                return None

        if not method or not password:
            return None

        return {
            'server': server,
            'port': int(port),
            'method': method,
            'password': password,
            'tag': tag,
            'plugin': plugin,
            'plugin_opts': plugin_opts,
        }
    except (ValueError, AttributeError, IndexError, TypeError) as e:
        log.debug("SS link parse failed: %s", e)
        return None
