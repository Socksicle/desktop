import json
import logging
import socks

log = logging.getLogger("geo_utils")

MAX_RESPONSE_BYTES = 64 * 1024
MAX_HEADER_BYTES = 16 * 1024
_RESET_WINERRNOS = {10048, 10052, 10053, 10054, 10058}


import re

EMOJI_AND_FLAG_REGEX = re.compile(
    r"(?:[\U0001F1E6-\U0001F1FF]{2}"
    r"|[\U0001F300-\U0001FAFF](?:\uFE0F)?(?:\u200D[\U0001F300-\U0001FAFF](?:\uFE0F)?)*"
    r"|[\u2600-\u27BF\u2300-\u23FF\u2B50](?:\uFE0F)?)"
)

COUNTRY_MAPPINGS = [
    ({"germany", "frankfurt", "berlin", "de", "германия"}, ("🇩🇪", "DE")),
    ({"netherlands", "amsterdam", "nl", "нидерланды"}, ("🇳🇱", "NL")),
    ({"finland", "helsinki", "fi", "финляндия"}, ("🇫🇮", "FI")),
    ({"united states", "usa", "us", "new york", "california", "miami", "los angeles", "сша"}, ("🇺🇸", "US")),
    ({"united kingdom", "london", "uk", "gb", "великобритания", "лондон"}, ("🇬🇧", "GB")),
    ({"france", "paris", "fr", "франция", "париж"}, ("🇫🇷", "FR")),
    ({"sweden", "stockholm", "se", "швеция"}, ("🇸🇪", "SE")),
    ({"poland", "warsaw", "pl", "польша", "варшава"}, ("🇵🇱", "PL")),
    ({"czech", "prague", "cz", "чехия", "прага"}, ("🇨🇿", "CZ")),
    ({"austria", "vienna", "at", "австрия", "вена"}, ("🇦🇹", "AT")),
    ({"switzerland", "zurich", "ch", "швейцария"}, ("🇨🇭", "CH")),
    ({"italy", "milan", "rome", "it", "италия", "рим", "милан"}, ("🇮🇹", "IT")),
    ({"spain", "madrid", "es", "испания", "мадрид"}, ("🇪🇸", "ES")),
    ({"japan", "tokyo", "osaka", "jp", "япония", "токио"}, ("🇯🇵", "JP")),
    ({"singapore", "sg", "сингапур"}, ("🇸🇬", "SG")),
    ({"turkey", "istanbul", "tr", "турция", "стамбул"}, ("🇹🇷", "TR")),
    ({"kazakhstan", "almaty", "astana", "kz", "казахстан", "алматы", "астана"}, ("🇰🇿", "KZ")),
    ({"russia", "moscow", "ru", "россия", "москва"}, ("🇷🇺", "RU")),
    ({"ukraine", "kyiv", "ua", "украина", "киев"}, ("🇺🇦", "UA")),
    ({"canada", "toronto", "ca", "канада"}, ("🇨🇦", "CA")),
    ({"hong kong", "hk", "гонконг"}, ("🇭🇰", "HK")),
    ({"korea", "seoul", "kr", "корея", "сеул"}, ("🇰🇷", "KR")),
    ({"india", "mumbai", "in", "индия"}, ("🇮🇳", "IN")),
    ({"brazil", "sao paulo", "br", "бразилия"}, ("🇧🇷", "BR")),
    ({"uae", "dubai", "ae", "оаэ", "дубай"}, ("🇦🇪", "AE")),
]


def detect_flag(name: str, host: str = "") -> tuple[str, str]:
    """Detect country flag emoji and 2-letter ISO code from server name or host (Android 1:1)."""
    hay = f"{name or ''} {host or ''}".lower()
    tokens = set(re.findall(r"[\w]+", hay))
    for keywords, (emoji, code) in COUNTRY_MAPPINGS:
        for kw in keywords:
            if len(kw) == 2 and " " not in kw:
                if kw in tokens:
                    return emoji, code
            else:
                if kw in hay:
                    return emoji, code
    return "🌐", "UN"


def extract_name_and_emoji(raw_name: str, fallback_flag: str = "🌐") -> tuple[str, str]:
    """Extract embedded emoji from server name or return fallback flag (Android 1:1)."""
    name = (raw_name or "").strip()
    emoji = fallback_flag
    m = EMOJI_AND_FLAG_REGEX.search(name)
    if m:
        emoji = m.group(0).strip()
        name = name.replace(m.group(0), "").strip()
    name = name.strip(" -|:·—_/\\•~><")
    if not name:
        name = "Server"
    return name, emoji


def clean_server_name_and_emoji(raw_name: str, fallback_flag: str = "🌐") -> tuple[str, str]:
    """Clean server name and extract emoji, capping display name at 14 characters."""
    name, emoji = extract_name_and_emoji(raw_name, fallback_flag)
    if len(name) > 14:
        name = name[:14].rstrip()
    return name, emoji


def get_flag_emoji(country_code):
    if not country_code or len(country_code) != 2:
        return "🌐"
    return chr(ord(country_code[0].upper()) + 127397) + chr(ord(country_code[1].upper()) + 127397)


def _recv_until(sock, delimiter, limit):
    data = b""
    while delimiter not in data:
        remaining = limit - len(data)
        if remaining <= 0:
            return data
        chunk = sock.recv(min(4096, remaining))
        if not chunk:
            return None
        data += chunk
    return data


def _recv_line(sock, buffered, limit):
    buf = buffered
    while b"\r\n" not in buf:
        if len(buf) >= limit:
            return None, buf
        chunk = sock.recv(4096)
        if not chunk:
            return None, buf
        buf += chunk
    line, buf = buf.split(b"\r\n", 1)
    return line, buf


def _recv_exact(sock, buffered, count):
    buf = buffered
    while len(buf) < count:
        chunk = sock.recv(4096)
        if not chunk:
            return None, buf
        buf += chunk
    return buf[:count], buf[count:]


def _drain_trailers(sock, buffered):
    buf = buffered
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEADER_BYTES:
            return buf
        chunk = sock.recv(4096)
        if not chunk:
            return buf
        buf += chunk
    return buf


def _read_chunked_body(sock, buffered):
    body = b""
    buf = buffered
    while True:
        size_line, buf = _recv_line(sock, buf, 4096)
        if size_line is None:
            log.warning("HTTP response: truncated chunked body (missing terminal 0-chunk)")
            return None
        try:
            size = int(size_line.split(b";", 1)[0].strip(), 16)
        except ValueError:
            log.warning("HTTP response: malformed chunk size line %r",
                        size_line.decode("latin-1", "replace"))
            return None
        if size < 0 or len(body) + size > MAX_RESPONSE_BYTES:
            log.warning("HTTP response: chunked body exceeds limit (%d bytes)",
                        MAX_RESPONSE_BYTES)
            return None
        chunk, buf = _recv_exact(sock, buf, size)
        if chunk is None:
            log.warning("HTTP response: truncated chunked body (chunk of %d bytes not fully received)",
                        size)
            return None
        body += chunk
        if size == 0:
            _drain_trailers(sock, buf)
            return body
        crlf, buf = _recv_exact(sock, buf, 2)
        if crlf != b"\r\n":
            log.warning("HTTP response: malformed chunk terminator")
            return None


def _read_http_response(sock, timeout=10):
    """Read an HTTP response body following RFC framing.

    Supports Content-Length, Transfer-Encoding: chunked, and
    close-delimited bodies. Returns the raw body bytes on success,
    or None with a logged concrete reason when the response is
    malformed, carries a non-200 status, or has an empty/oversized body.
    """
    sock.settimeout(timeout)
    data = _recv_until(sock, b"\r\n\r\n", MAX_HEADER_BYTES)
    if data is None:
        log.warning("HTTP response: connection closed before end of headers")
        return None
    if b"\r\n\r\n" not in data:
        log.warning("HTTP response: headers exceed %d bytes without terminator",
                    MAX_HEADER_BYTES)
        return None
    header_block, rest = data.split(b"\r\n\r\n", 1)
    lines = header_block.split(b"\r\n")
    status_parts = lines[0].split(b" ", 2)
    if len(status_parts) < 2 or status_parts[1] != b"200":
        status = (status_parts[1].decode("latin-1", "replace")
                  if len(status_parts) >= 2 else "<malformed>")
        log.warning("HTTP response: unexpected status %s", status)
        return None
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(b":")
        if sep:
            headers[name.strip().lower()] = value.strip()

    transfer_encoding = headers.get(b"transfer-encoding", b"").lower()
    if b"chunked" in transfer_encoding:
        body = _read_chunked_body(sock, rest)
        return body if body is not None else None

    content_length = headers.get(b"content-length")
    if content_length is not None:
        try:
            clen = int(content_length)
        except ValueError:
            log.warning("HTTP response: invalid Content-Length %r",
                        content_length.decode("latin-1", "replace"))
            return None
        if clen <= 0:
            log.warning("HTTP response: empty body (Content-Length: %d)", clen)
            return None
        if clen > MAX_RESPONSE_BYTES:
            log.warning("HTTP response: Content-Length %d exceeds limit (%d bytes)",
                        clen, MAX_RESPONSE_BYTES)
            return None
        body = rest
        while len(body) < clen:
            chunk = sock.recv(4096)
            if not chunk:
                log.warning("HTTP response: short body (got %d of %d bytes)",
                            len(body), clen)
                return None
            body += chunk
        return body[:clen]

    body = rest
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk
        if len(body) > MAX_RESPONSE_BYTES:
            log.warning("HTTP response: body exceeds limit (%d bytes) without framing",
                        MAX_RESPONSE_BYTES)
            return None
    if not body:
        log.warning("HTTP response: empty body without framing")
        return None
    return body


def fetch_ip_info_via_proxy(proxy_port, timeout=10):
    """Fetch public IP info through SOCKS5 proxy using socksocket directly."""
    url_host = "ip-api.com"
    url_path = "/json/?fields=status,countryCode,query"
    log.info("Fetching IP via proxy on port %s...", proxy_port)

    import socket
    s = socks.socksocket()
    try:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        s.set_proxy(socks.SOCKS5, "127.0.0.1", int(proxy_port))
        s.settimeout(timeout)
        s.connect((url_host, 80))

        request = (
            f"GET {url_path} HTTP/1.1\r\n"
            f"Host: {url_host}\r\n"
            f"User-Agent: Socksicle/1.1 (geo)\r\n"
            "Connection: close\r\n\r\n"
        )
        s.sendall(request.encode())

        body = _read_http_response(s, timeout)
        if body is None:
            return None

        data = json.loads(body.decode("utf-8"))
        if data.get("status") == "success":
            log.info("Success: %s", data.get('query'))
            return {
                "ip": data.get("query"),
                "flag": get_flag_emoji(data.get("countryCode"))
            }
        log.debug("Geo lookup returned status %r", data.get("status"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        if isinstance(e, OSError) and getattr(e, "winerror", None) in _RESET_WINERRNOS:
            log.debug("Failed (tunnel torn down): %s", e)
        else:
            log.warning("Failed: %s", e)
    finally:
        s.close()
    return None