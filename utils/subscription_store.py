import base64
import binascii
import ipaddress
import json
import logging
import os
import re
import secrets
import tempfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

from .platform_utils import get_config_dir
from .i18n import tr
from . import twinsock

log = logging.getLogger(__name__)


USER_AGENT_PRESETS = {
    "socksicle": "Socksicle/1.0",
    "happ": "Happ/4.0.5",
    "hiddify": "HiddifyNext/2.5.7",
    "incy": "InCY/1.0.0",
    "karing": "Karing/1.0.8",
    "clash": "ClashForWindows/0.20.39",
    "curl": "curl/7.88.0",
    "nekoray": "NekoBox/PC/2.1.0 (Prefer ClashMeta Format)",
    "v2rayng": "v2rayNG/1.8.12",
    "mihomo": "clash-meta",
    "shadowsocks": "ShadowsocksWindows/4.4.1.0",
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
}


def get_installation_id(settings):
    """Return stable per-installation ID for the X-hwid header."""
    install_id = str(settings.get("installation_id") or "").strip()
    if not install_id:
        install_id = secrets.token_hex(16)
        settings["installation_id"] = install_id
    return install_id


def _subscriptions_path():
    return get_config_dir() / "subscriptions.json"


def _decode_maybe_base64(value: str) -> str:
    """Return UTF-8 text for 'base64:<...>' header values (de-facto panel convention);
    any other value passes through unchanged."""
    if not value.startswith("base64:"):
        return value
    body = value[len("base64:"):]
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return value


def _extract_metadata(response):
    """Extract all supported subscription headers into a metadata dict."""
    meta = {}

    info_header = response.headers.get('Subscription-Userinfo')
    if info_header:
        parts = dict(
            item.split('=', 1)
            for item in info_header.replace(' ', '').split(';')
            if '=' in item
        )
        try:
            meta['traffic'] = {
                'used': int(parts.get('upload', 0)) + int(parts.get('download', 0)),
                'total': int(parts.get('total', 0)),
                'expire': int(parts.get('expire', 0)),
            }
        except (ValueError, TypeError):
            meta['traffic'] = {'used': 0, 'total': 0, 'expire': 0}

    for header_name, key, converter in [
        ('Profile-Update-Interval', 'profile_update_interval', int),
        ('Profile-Title', 'profile_title', str),
        ('Profile-Description', 'description', str),
        ('Support-URL', 'support_url', str),
        ('Profile-Web-Page-URL', 'profile_web_page_url', str),
        ('Announce', 'announce', str),
        ('Content-Disposition', 'content_disposition', str),
    ]:
        raw = response.headers.get(header_name)
        if raw:
            try:
                meta[key] = converter(raw)
            except (ValueError, TypeError):
                meta[key] = raw

    # Panels base64-encode UTF-8 header values with a "base64:" prefix
    for key in ('profile_title', 'announce', 'content_disposition', 'description'):
        if key in meta:
            meta[key] = _decode_maybe_base64(meta[key])

    return meta


def _extract_description(headers, decoded_lines, announce, meta):
    """Derive a subscription description from headers or body preamble."""
    desc = meta.get('description')
    if desc and not desc.startswith("base64:"):
        return desc
    if announce and not announce.startswith("base64:"):
        if announce.strip() and not re.fullmatch(r'https?://\S*', announce):
            return announce
    preamble = []
    for line in decoded_lines:
        line = line.strip()
        if line.startswith(('ss://', 'vless://', 'vmess://', 'hysteria2://', 'hy2://')):
            break
        if not line or line.startswith("base64:"):
            continue
        preamble.append(line)
        if len(preamble) >= 10:
            break
    return " ".join(preamble)


def _try_parse_sip008_json(content):
    """Try to parse content as SIP008 JSON. Returns (ss_links, meta) or None."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict) or data.get('version') != 1:
        return None

    servers = data.get('servers', [])
    if not isinstance(servers, list):
        return None

    ss_links = []
    for srv in servers:
        if not isinstance(srv, dict):
            continue
        raw_host = srv.get('server')
        if not isinstance(raw_host, str):
            continue
        host = raw_host.strip()
        if not host or any(c in host for c in ' \t\r\n/#?@'):
            continue
        try:
            ip = ipaddress.ip_address(host.strip('[]'))
            if ip.version == 6:
                host = f"[{ip}]"
            else:
                host = str(ip)
        except ValueError:
            if ':' in host:
                continue

        raw_port = srv.get('server_port', 443)
        if isinstance(raw_port, bool):
            continue
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            continue
        if not 1 <= port <= 65535:
            continue

        method = srv.get('method', '')
        if not isinstance(method, str) or not method.strip():
            continue
        method = method.strip()

        password = srv.get('password', '')
        if not isinstance(password, str):
            password = str(password) if password is not None else ''

        userinfo_b64 = base64.urlsafe_b64encode(
            f"{method}:{password}".encode('utf-8')
        ).decode('ascii').rstrip('=')

        link = f"ss://{userinfo_b64}@{host}:{port}"

        plugin = srv.get('plugin', '')
        plugin_opts = srv.get('plugin_opts', '')
        if isinstance(plugin, str) and plugin.strip():
            raw_plugin = plugin.strip()
            if isinstance(plugin_opts, str) and plugin_opts.strip():
                raw_plugin += ";" + plugin_opts.strip()
            escaped = raw_plugin.replace('\\', '\\\\').replace(';', '\\;').replace('=', '\\=')
            link += "/?plugin=" + quote(escaped, safe='')

        remarks = srv.get('remarks', '')
        if isinstance(remarks, str) and remarks.strip():
            link += "#" + quote(remarks.strip(), safe='')
        elif remarks and not isinstance(remarks, (list, dict, set, tuple)):
            try:
                rem_str = str(remarks).strip()
                if rem_str:
                    link += "#" + quote(rem_str, safe='')
            except Exception:
                pass

        ss_links.append(link)

    meta = {}
    if 'bytes_used' in data or 'bytes_remaining' in data:
        try:
            used = int(data.get('bytes_used', 0))
            remaining = int(data.get('bytes_remaining', 0))
            meta['traffic'] = {
                'used': used,
                'total': used + remaining,
                'expire': 0,
            }
        except (ValueError, TypeError):
            meta['traffic'] = {'used': 0, 'total': 0, 'expire': 0}

    return ss_links, meta


_last_fetch_errors: dict[str, str] = {}


def get_last_fetch_error(url: str) -> str | None:
    """Return the last recorded fetch error for a URL."""
    return _last_fetch_errors.get(url)


def format_subscription_error(e: Exception) -> str:
    """Format subscription network/HTTP exception into human-readable error explanation."""
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        reason = getattr(e, "reason", "") or ""
        if code in (404, 410):
            return tr("sub.http_404")
        elif code in (401, 403):
            return tr("sub.http_denied").format(code=code)
        elif code >= 500:
            return tr("sub.http_server").format(code=code, reason=reason or "Server Error")
        return tr("sub.http_generic").format(code=code, reason=reason or "HTTP error")

    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", None)
        if isinstance(reason, TimeoutError) or (reason and "timed out" in str(reason).lower()):
            return tr("sub.net_timeout")
        if isinstance(reason, OSError):
            winerror = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
            if winerror in (10061, 111):
                return tr("sub.net_refused")
            elif winerror in (10060, 110):
                return tr("sub.net_timeout")
            elif winerror in (11001, -2, -3) or (reason and ("name resolution" in str(reason).lower() or "getaddrinfo" in str(reason).lower())):
                return tr("sub.net_dns")
            return tr("sub.net_error").format(reason=reason)
        if reason:
            reason_str = str(reason)
            if "certificate" in reason_str.lower() or "ssl" in reason_str.lower():
                return tr("sub.net_ssl").format(reason=reason_str)
            return tr("sub.net_error").format(reason=reason_str)

    if isinstance(e, TimeoutError):
        return tr("sub.net_timeout")

    return str(e)


from .subscription_fetch import (
    MAX_SUBSCRIPTION_NODES,
    _BlockedSubscription,
    _open_subscription_request,
    _read_limited_body,
    _require_public_url,
)


def _safe_url_host(url: str) -> str:
    """Hostname for logs; the full URL may carry a secret token."""
    try:
        return urlparse(url).hostname or "invalid-url"
    except ValueError:
        return "invalid-url"


def _refuse(url, message):
    log.warning("Blocked subscription fetch for host %s: %s", _safe_url_host(url), message)
    if len(_last_fetch_errors) >= 50:
        _last_fetch_errors.pop(next(iter(_last_fetch_errors)), None)
    _last_fetch_errors[url] = message
    return [], {"error": message}


def parse_subscription(url, settings=None):
    """Fetch and parse a shadowsocks subscription.

    Args:
        url: Subscription URL.
        settings: Optional dict with keys:
            - user_agent_key: str key from USER_AGENT_PRESETS
            - fake_hwid: bool whether to send X-hwid header
            - hwid_value: str custom HWID value (if empty, stable installation_id)

    Returns (ss_links, metadata_dict).

    Supports:
    - Base64-encoded link lists (standard)
    - SIP008 JSON format
    - Extended HTTP headers for metadata
    """
    settings = settings or {}
    parsed = urlparse(url)
    if parsed.scheme.lower() == "http":
        return _refuse(url, tr("sub.http_only"))
    if parsed.scheme.lower() != "https":
        log.warning("Blocked subscription URL with scheme: %s", parsed.scheme)
        return [], {}
    try:
        _require_public_url(url)
        ua_key = settings.get("user_agent_key", "socksicle")
        user_agent = USER_AGENT_PRESETS.get(ua_key, USER_AGENT_PRESETS["socksicle"])

        headers = {'User-Agent': user_agent}

        if settings.get("fake_hwid", False):
            hwid_val = settings.get("hwid_value", "").strip()
            if not hwid_val:
                hwid_val = get_installation_id(settings)
            headers['X-hwid'] = hwid_val

        req = urllib.request.Request(url, headers=headers)
        with _open_subscription_request(req, timeout=10) as response:
            raw_content = _read_limited_body(response).decode('utf-8').strip()
            content_type = response.headers.get('Content-Type', '')

            meta = _extract_metadata(response)

            is_json = (
                'application/json' in content_type
                or (raw_content.startswith('{') and raw_content.endswith('}'))
            )
            if is_json:
                result = _try_parse_sip008_json(raw_content)
                if result is not None:
                    ss_links, json_meta = result
                    if len(ss_links) > MAX_SUBSCRIPTION_NODES:
                        raise _BlockedSubscription(
                            tr("sub.too_many_nodes").format(
                                count=len(ss_links), max=MAX_SUBSCRIPTION_NODES))
                    meta.update(json_meta)
                    return ss_links, meta

            try:
                clean_b64 = re.sub(r'\s+', '', raw_content).replace('-', '+').replace('_', '/')
                padded_b64 = clean_b64 + '=' * (-len(clean_b64) % 4)
                decoded_content = base64.b64decode(padded_b64).decode('utf-8', errors='replace')
            except Exception as e:
                log.warning("Base64 decode failed, using raw content: %s", e)
                decoded_content = raw_content

            decoded_lines = decoded_content.splitlines()
            if 'description' not in meta or not meta['description']:
                meta['description'] = _extract_description(
                    headers, decoded_lines, meta.get('announce', ''), meta)

            supported_prefixes = ('ss://', 'vless://', 'vmess://', 'hysteria2://', 'hy2://', 'tws3://')
            links = [
                line.strip() for line in decoded_lines
                if line.strip().startswith(supported_prefixes)
            ]
            if len(links) > MAX_SUBSCRIPTION_NODES:
                raise _BlockedSubscription(
                    tr("sub.too_many_nodes").format(
                        count=len(links), max=MAX_SUBSCRIPTION_NODES))

            _last_fetch_errors.pop(url, None)
            return links, meta
    except _BlockedSubscription as e:
        return _refuse(url, str(e))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError) as e:
        err_msg = format_subscription_error(e)
        log.error("Subscription fetch/parse error for host %s: %s", _safe_url_host(url), e)
        if len(_last_fetch_errors) >= 50:
            _last_fetch_errors.pop(next(iter(_last_fetch_errors)), None)
        _last_fetch_errors[url] = err_msg
        return [], {"error": err_msg}


def _seal_subscription(sub):
    d = dict(sub)
    servers = []
    for s in d.get("servers", []):
        s_dict = s.to_dict() if hasattr(s, "to_dict") else dict(s)
        servers.append(twinsock.seal_dict("subscriptions", s_dict, twinsock.SECRET_FIELDS))
    d["servers"] = servers
    d["url"] = twinsock.encrypt_field("subscriptions", d.get("url", ""))
    return d


def save_subscriptions(subs):
    path = _subscriptions_path()
    sealed = [_seal_subscription(sub) for sub in subs]
    with tempfile.NamedTemporaryFile(
            mode='w', dir=str(path.parent), delete=False,
            encoding="utf-8", suffix='.tmp') as f:
        json.dump(sealed, f)
        tmp_name = f.name
    try:
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def load_subscriptions():
    path = _subscriptions_path()
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding="utf-8", errors="replace") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.error("Failed to load subscriptions: %s", e)
        return []
    if not isinstance(raw, list):
        return []
    subs = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        d = twinsock.unseal_dict("subscriptions", d, ("url",))
        servers = []
        for s in d.get("servers", []):
            if isinstance(s, dict):
                s = twinsock.unseal_dict("subscriptions", s, twinsock.SECRET_FIELDS)
            servers.append(s)
        d["servers"] = servers
        subs.append(d)
    return subs
