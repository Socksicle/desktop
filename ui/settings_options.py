"""Static content for the Settings screen: option tables and help copy.

Pure data, no Qt: editing copy or adding a provider touches this module,
not the widget code in :mod:`ui.screen_settings`.

Label fields hold i18n keys (translated with :func:`utils.i18n.tr` at build
time), except USER_AGENT_OPTIONS whose labels are sent verbatim as the
HTTP User-Agent header and must never be translated.
"""

from utils.engines.singbox_config import MUX_PROTOCOLS
from utils.platform_utils import get_socksicle_user_agent

THEME_SWATCHES: list[tuple[str, str, str, bool]] = [
    ("dynamic", "opt.theme.dynamic", "#E2703C", True),
    ("lavender", "opt.theme.lavender", "#D0BCFF", False),
    ("ocean", "opt.theme.ocean", "#4BA3E3", False),
    ("emerald", "opt.theme.emerald", "#4CAF50", False),
    ("coral", "opt.theme.coral", "#E2703C", False),
    ("custom", "opt.theme.custom", "#B388FF", False),
]

THEME_SPEC_OPTIONS: list[tuple[str, str]] = [
    ("2021", "opt.theme_spec.2021"),
    ("2025", "opt.theme_spec.2025"),
]

ENGINE_IDS = ("auto", "sing-box", "xray")
ENGINE_FALLBACK = "auto"
ENGINE_OPTIONS: list[tuple[str, str]] = [
    ("auto", "opt.engine.auto"),
    ("sing-box", "opt.engine.singbox"),
    ("xray", "opt.engine.xray"),
]

MUX_OPTIONS: list[tuple[str, str]] = [
    (protocol, f"opt.mux.{protocol}") for protocol in MUX_PROTOCOLS
]

DNS_OPTIONS: list[tuple[str, str]] = [
    ("system", "opt.dns.system"),
    ("cloudflare", "opt.dns.cloudflare"),
    ("quad9", "opt.dns.quad9"),
    ("adguard", "opt.dns.adguard"),
    ("google", "opt.dns.google"),
    ("custom", "opt.dns.custom"),
]

DNS_PRESET_URLS: dict[str, str] = {
    "system": "",
    "cloudflare": "https://1.1.1.1/dns-query",
    "quad9": "https://9.9.9.9/dns-query",
    "adguard": "https://94.140.14.14/dns-query",
    "google": "https://8.8.8.8/dns-query",
}

USER_AGENT_OPTIONS: list[tuple[str, str]] = [
    ("socksicle", get_socksicle_user_agent()),
    ("sing-box", "sing-box/1.14.3"),
    ("mihomo", "Mihomo/1.19.32"),
    ("clash_verge", "ClashVerge/2.5.8"),
    ("hiddify", "Hiddify/4.1.1"),
    ("v2rayn", "v2rayN/7.25.6"),
    ("flclash", "FlClash/0.9.1"),
    ("karing", "karing/1.2.26"),
    ("nekobox", "NekoBox/4.0.1"),
    ("nekoray", "NekoRay/4.0.1"),
    ("mihomo_party", "MihomoParty/1.5.0"),
    ("v2rayng", "v2rayNG/2.3.11"),
    ("happ", "Happ/4.6.0"),
    ("clash", "Clash.Meta"),
    ("shadowrocket", "Shadowrocket/2.2.0"),
]

PING_METHOD_OPTIONS: list[tuple[str, str, str]] = [
    ("thp", "opt.ping.thp", "opt.ping.thp_sub"),
    ("tcp_connect", "opt.ping.tcp", "opt.ping.tcp_sub"),
    ("http_head", "opt.ping.head", "opt.ping.head_sub"),
    ("http_get", "opt.ping.get", "opt.ping.get_sub"),
]

PING_DISPLAY_OPTIONS: list[tuple[str, str]] = [
    ("opt.display.num", "opt.display.num_sub"),
    ("opt.display.bars", "opt.display.bars_sub"),
    ("opt.display.both", "opt.display.both_sub"),
]

PING_TIMEOUT_DEFAULT = 3
PING_TIMEOUT_MIN = 1
PING_TIMEOUT_MAX = 10

AUTO_UPDATE_INTERVAL_OPTIONS: list[tuple[float, str]] = [
    (0.5, "opt.interval.30m"),
    (1.0, "opt.interval.1h"),
    (2.0, "opt.interval.2h"),
    (4.0, "opt.interval.4h"),
    (6.0, "opt.interval.6h"),
    (12.0, "opt.interval.12h"),
    (24.0, "opt.interval.24h"),
]

LANGUAGE_OPTIONS: list[tuple[str, str]] = [
    ("ru", "Русский"),
    ("en", "English"),
    ("zh", "中文"),
]
