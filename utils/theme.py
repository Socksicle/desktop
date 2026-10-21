"""Material You (Material Design 3) Theme Engine for Socksicle.

Provides dynamic tonal color generation, native system accent color
extraction (Windows DWM, Linux XDG Desktop Portal), and curated
Material 3 preset palettes.

Supports the classic M3 SPEC_2021 and the M3 Expressive SPEC_2025
tonal-spot recipes. Tones are CIELAB L* (which is exactly what an M3
"tone" is); hue/chroma live in CIELAB LCH as a close, dependency-free
stand-in for CAM16-UCS.
"""
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

log = logging.getLogger("theme")



PRESET_SEEDS = {
    "lavender": QColor("#D0BCFF"),
    "ocean": QColor("#4BA3E3"),
    "emerald": QColor("#4CAF50"),
    "coral": QColor("#FF7043"),
    "amoled": QColor("#9E9E9E"),
}


def _unwrap_dbus_variant(raw):
    """Unwrap nested QDBusVariant / QVariant wrappers to raw Python types."""
    while hasattr(raw, "variant"):
        raw = raw.variant()
    return raw


def _parse_portal_accent_color(raw) -> QColor | None:
    """Parse raw portal accent-color setting into a valid QColor."""
    if raw is None:
        return None
    raw = _unwrap_dbus_variant(raw)
    if isinstance(raw, QColor) and raw.isValid():
        return raw

    if isinstance(raw, (tuple, list)) and len(raw) >= 3:
        try:
            r, g, b = float(raw[0]), float(raw[1]), float(raw[2])
            if 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0:
                return QColor.fromRgbF(r, g, b)
            elif 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255:
                return QColor(int(r), int(g), int(b))
        except (ValueError, TypeError):
            pass

    if isinstance(raw, str):
        s = raw.strip()
        c = QColor(s)
        if c.isValid():
            return c

    return None


def read_portal_setting(namespace: str, key: str, bus=None):
    """Read a setting from org.freedesktop.portal.Settings via D-Bus session bus."""
    try:
        if bus is None:
            try:
                from PySide6.QtDBus import QDBusConnection
                bus = QDBusConnection.sessionBus()
            except (ImportError, AttributeError) as e:
                log.debug("PySide6.QtDBus unavailable for portal settings: %s", e)
                return None
        if not hasattr(bus, "isConnected") or not bus.isConnected():
            return None

        from PySide6.QtDBus import QDBusInterface
        iface = QDBusInterface(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            bus,
        )
        if not iface.isValid():
            return None

        reply = iface.call("Read", namespace, key)
        if not reply.isValid() or not reply.arguments():
            return None

        return _unwrap_dbus_variant(reply.arguments()[0])
    except Exception as e:
        log.debug("Failed to read portal setting %s/%s: %s", namespace, key, e)
        return None


def get_portal_accent_color(bus=None) -> QColor | None:
    """Read accent color from XDG Desktop Portal Settings."""
    try:
        raw = read_portal_setting("org.freedesktop.appearance", "accent-color", bus=bus)
        return _parse_portal_accent_color(raw)
    except Exception as e:
        log.debug("Failed to read portal accent color: %s", e)
        return None


def get_portal_color_scheme(bus=None) -> int | None:
    """Read color scheme from XDG Desktop Portal Settings.

    Returns:
        0: No preference
        1: Prefer dark
        2: Prefer light
        None: Unavailable / error
    """
    try:
        raw = read_portal_setting("org.freedesktop.appearance", "color-scheme", bus=bus)
        if raw is not None:
            return int(raw)
    except Exception as e:
        log.debug("Failed to read portal color scheme: %s", e)
    return None


_TONAL_SPOT_PRIMARY_CHROMA = 36.0
_TONAL_SPOT_SECONDARY_CHROMA = 16.0
_TONAL_SPOT_TERTIARY_HUE_SHIFT = 60.0
_TONAL_SPOT_TERTIARY_CHROMA = 24.0
_TONAL_SPOT_NEUTRAL_CHROMA = 6.0
_TONAL_SPOT_NEUTRAL_VARIANT_CHROMA = 8.0
_TONAL_SPOT_ERROR_HUE = 25.0
_TONAL_SPOT_ERROR_CHROMA = 84.0

_STATUS_WARNING_HUE = 85.0
_STATUS_HUE_HARMONIZE_DEG = 15.0


def _sanitize_degrees(degrees: float) -> float:
    return degrees % 360.0


def _harmonize_hue(hue: float, target_hue: float, max_shift: float) -> float:
    """Rotate ``hue`` toward ``target_hue`` by at most ``max_shift`` degrees."""
    delta = _sanitize_degrees(target_hue - hue + 180.0) - 180.0
    return _sanitize_degrees(hue + max(-max_shift, min(max_shift, delta)))


SPEC_2021 = "2021"
SPEC_2025 = "2025"
_THEME_SPECS = (SPEC_2021, SPEC_2025)


def _normalize_spec(spec) -> str:
    """Return a supported Material color spec, defaulting to SPEC_2021."""
    return spec if spec in _THEME_SPECS else SPEC_2021


def _piecewise_value(value: float, breakpoints: list[float], values: list[float]) -> float:
    """Return the entry whose breakpoint interval contains ``value``."""
    for lo, hi, mapped in zip(breakpoints, breakpoints[1:], values):
        if lo <= value < hi:
            return mapped
    return value


def _rotated_hue(value: float, breakpoints: list[float], rotations: list[float]) -> float:
    """Apply a Material piecewise hue rotation to ``value``."""
    return _sanitize_degrees(value + _piecewise_value(value, breakpoints, rotations))


def _coerce_seed(seed) -> QColor | None:
    """Accept a QColor or hex string seed; return None when unusable."""
    if isinstance(seed, QColor) and seed.isValid():
        return seed
    if isinstance(seed, str):
        cand = QColor(seed.strip())
        if cand.isValid():
            return cand
    return None


def _srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _rgb_to_lch(color: "QColor") -> tuple[float, float, float]:
    """sRGB -> CIELAB L* (the M3 tone) + LCH hue/chroma. Returns (L, C, H)."""
    r = _srgb_to_linear(color.redF())
    g = _srgb_to_linear(color.greenF())
    b = _srgb_to_linear(color.blueF())
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.0
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def _f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = _f(x), _f(y), _f(z)
    lab_l = 116.0 * fy - 16.0
    lab_a = 500.0 * (fx - fy)
    lab_b = 200.0 * (fy - fz)
    import math as _math

    chroma = _math.hypot(lab_a, lab_b)
    hue = _math.degrees(_math.atan2(lab_b, lab_a)) % 360.0
    return lab_l, chroma, hue


def _lch_to_rgbTone(lightness: float, chroma: float, hue: float) -> tuple[int, int, int] | None:
    """LCH -> sRGB bytes. Returns None when the color is out of gamut."""
    import math as _math

    hue_rad = _math.radians(hue)
    lab_a = chroma * _math.cos(hue_rad)
    lab_b = chroma * _math.sin(hue_rad)
    fy = (lightness + 16.0) / 116.0
    fx = fy + lab_a / 500.0
    fz = fy - lab_b / 200.0

    def _finv(t: float) -> float:
        return t ** 3 if t > 0.206893 else (t - 16.0 / 116.0) / 7.787

    x = _finv(fx) * 0.95047
    y = _finv(fy) * 1.0
    z = _finv(fz) * 1.08883
    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    b = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    if min(r, g, b) < -0.001 or max(r, g, b) > 1.001:
        return None
    return (
        int(round(_linear_to_srgb(r) * 255)),
        int(round(_linear_to_srgb(g) * 255)),
        int(round(_linear_to_srgb(b) * 255)),
    )


def _palette_tone(hue: float, chroma: float, tone: float) -> "QColor":
    """Color of a tonal palette at the given M3 tone (0-100).

    Keeps the requested chroma when the sRGB gamut allows it, otherwise
    bisects chroma down (same idea as TonalPalette gamut mapping).
    """
    from PySide6.QtGui import QColor as _QColor

    tone = max(0.0, min(100.0, tone))
    if tone <= 0.5:
        return _QColor(0, 0, 0)
    if tone >= 99.5:
        return _QColor(255, 255, 255)
    result = _lch_to_rgbTone(tone, chroma, _sanitize_degrees(hue))
    if result is not None:
        return _QColor(*result)
    lo, hi, best = 0.0, chroma, None
    for _ in range(12):
        mid = (lo + hi) / 2.0
        trial = _lch_to_rgbTone(tone, mid, _sanitize_degrees(hue))
        if trial is not None:
            best = trial
            lo = mid
        else:
            hi = mid
    if best is not None:
        return _QColor(*best)
    gray = _lch_to_rgbTone(tone, 0.0, 0.0) or (0, 0, 0)
    return _QColor(*gray)


def _get_preferred_scheme_type() -> str | None:
    """Check if desktop configuration specifies a preferred Material 3 scheme variant."""
    for conf_path in [
        os.path.expanduser("~/.config/illogical-impulse/config.json"),
    ]:
        if os.path.isfile(conf_path):
            try:
                with open(conf_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                st = d.get("appearance", {}).get("palette", {}).get("type")
                if st and isinstance(st, str):
                    return st
            except Exception:
                pass
    return None


def _calculate_image_colorfulness(small: QImage) -> float:
    """Calculate Hasler & Süsstrunk colorfulness metric on a scaled image."""
    n = small.width() * small.height()
    if n == 0:
        return 0.0
    rg_list, yb_list = [], []
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            r, g, b = c.red(), c.green(), c.blue()
            rg_list.append(abs(r - g))
            yb_list.append(abs(0.5 * (r + g) - b))
    mean_rg = sum(rg_list) / n
    mean_yb = sum(yb_list) / n
    std_rg = math.sqrt(sum((v - mean_rg) ** 2 for v in rg_list) / n)
    std_yb = math.sqrt(sum((v - mean_yb) ** 2 for v in yb_list) / n)
    return math.sqrt(std_rg ** 2 + std_yb ** 2) + (0.3 * math.sqrt(mean_rg ** 2 + mean_yb ** 2))


def _extract_dominant_color_from_image(image_path: str) -> tuple[QColor | None, str]:
    """Extract dominant seed color and scheme type using 5-bit quantization and M3 scoring."""
    try:
        img = QImage(image_path)
        if img.isNull():
            return None, "tonal-spot"
        small = img.scaled(64, 64, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        colorfulness = _calculate_image_colorfulness(small)

        # 5-bit RGB quantization (clusters colors in 3D color space)
        bins = {}
        for y in range(small.height()):
            for x in range(small.width()):
                c = QColor(small.pixel(x, y))
                r, g, b = c.red(), c.green(), c.blue()
                k = (r >> 3, g >> 3, b >> 3)
                if k not in bins:
                    bins[k] = [0, 0, 0, 0]
                entry = bins[k]
                entry[0] += 1
                entry[1] += r
                entry[2] += g
                entry[3] += b

        candidates, total_count = [], 0
        for cnt, r_sum, g_sum, b_sum in bins.values():
            qc = QColor(r_sum // cnt, g_sum // cnt, b_sum // cnt)
            tone, chroma, hue = _rgb_to_lch(qc)
            # Filter noise: too dark, too light, or pure gray
            if tone < 20 or tone > 92 or chroma < 8.0:
                continue
            # Google Dislike filter: muddy yellow-green olive
            if 88.0 <= hue <= 115.0 and chroma > 16.0 and tone < 65.0:
                continue
            candidates.append((qc, tone, chroma, hue, cnt))
            total_count += cnt

        if not candidates:
            return None, "tonal-spot"

        scored = []
        for qc, tone, chroma, hue, cnt in candidates:
            prop = cnt / total_count if total_count else 0
            prop_score = prop * 70.0
            chroma_weight = 0.1 if chroma < 48.0 else 0.3
            chroma_score = (chroma - 48.0) * chroma_weight
            score = prop_score + chroma_score
            scored.append((score, qc, hue))

        scored.sort(key=lambda item: item[0], reverse=True)
        best_color = scored[0][1]

        # Scheme type selection matching Material You / Quickshell scheme_for_image
        preferred_scheme = _get_preferred_scheme_type()
        if preferred_scheme == "scheme-neutral" or colorfulness < 40.0:
            scheme_type = "neutral"
        else:
            scheme_type = "tonal-spot"

        return best_color, scheme_type
    except Exception as e:
        log.debug("Image dominant color extraction failed: %s", e)
        return None, "tonal-spot"


def _detect_linux_wallpaper_path() -> str | None:
    """Detect active desktop wallpaper image path across Hyprland, Sway, tiling WMs and DEs."""
    # 1. Hyprpaper CLI
    if shutil.which("hyprctl"):
        try:
            res = subprocess.run(["hyprctl", "hyprpaper", "listactive"], capture_output=True, text=True, timeout=0.3)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "=" in line:
                        p = line.split("=", 1)[1].strip()
                        if p and os.path.isfile(p):
                            return p
        except Exception:
            pass

    # 2. SWWW CLI
    if shutil.which("swww"):
        try:
            res = subprocess.run(["swww", "query"], capture_output=True, text=True, timeout=0.3)
            if res.returncode == 0:
                m = re.search(r"image:\s*([^\r\n]+)", res.stdout)
                if m:
                    p = m.group(1).strip()
                    if p and os.path.isfile(p):
                        return p
        except Exception:
            pass

    # 3. GNOME / Cinnamon / MATE / XFCE desktop settings
    if shutil.which("gsettings"):
        for schema, key in [
            ("org.gnome.desktop.background", "picture-uri-dark"),
            ("org.gnome.desktop.background", "picture-uri"),
            ("org.cinnamon.desktop.background", "picture-uri"),
            ("org.mate.background", "picture-filename"),
        ]:
            try:
                res = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=0.3)
                if res.returncode == 0 and res.stdout.strip():
                    raw = res.stdout.strip().strip("'\"")
                    if raw.startswith("file://"):
                        raw = raw[7:]
                    if raw and os.path.isfile(raw):
                        return raw
            except Exception:
                pass

    if shutil.which("xfconf-query"):
        try:
            res = subprocess.run(
                ["xfconf-query", "-c", "xfce4-desktop", "-p", "/backdrop/screen0/monitor0/workspace0/last-image"],
                capture_output=True, text=True, timeout=0.3
            )
            if res.returncode == 0 and res.stdout.strip():
                p = res.stdout.strip()
                if p and os.path.isfile(p):
                    return p
        except Exception:
            pass

    # 4. Standard tiling and desktop configs (Hyprland, Waypaper, Feh, Pywal, Nitrogen, Sway, KDE)
    config_candidates = [
        os.path.expanduser("~/.config/hypr/hyprlock/colors.conf"),
        os.path.expanduser("~/.config/hypr/hyprlock.conf"),
        os.path.expanduser("~/.config/hypr/hyprpaper.conf"),
        os.path.expanduser("~/.config/illogical-impulse/config.json"),
        os.path.expanduser("~/.config/waypaper/config.ini"),
        os.path.expanduser("~/.cache/wal/wal"),
        os.path.expanduser("~/.fehbg"),
        os.path.expanduser("~/.config/nitrogen/bg-saved.cfg"),
        os.path.expanduser("~/.config/sway/config"),
        os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc"),
    ]
    for conf in config_candidates:
        if not os.path.isfile(conf):
            continue
        try:
            with open(conf, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if conf.endswith(".json"):
                try:
                    jd = json.loads(content)
                    wp = jd.get("wallpaper") or jd.get("wallpaper_path") or jd.get("background")
                    if wp and isinstance(wp, str) and os.path.isfile(os.path.expanduser(wp)):
                        return os.path.expanduser(wp)
                except Exception:
                    pass

            # Check single path file (e.g. ~/.cache/wal/wal)
            first_line = content.splitlines()[0].strip() if content.splitlines() else ""
            if first_line and not first_line.startswith(("#", "[", "$")) and os.path.isfile(os.path.expanduser(first_line)):
                return os.path.expanduser(first_line)

            # Feh execution script
            if conf.endswith(".fehbg"):
                m_feh = re.search(r"'(.*?)'|\"(.*?)\"", content)
                if m_feh:
                    p = os.path.expanduser((m_feh.group(1) or m_feh.group(2) or "").strip())
                    if p and os.path.isfile(p):
                        return p

            # Key-value match (supports spaces in file paths!)
            m = re.search(
                r'(?:\$background_image|path|wallpaper|file|Image)\s*=\s*(?:[a-zA-Z0-9_\-]+,\s*)?([^\r\n"\'#;]+)',
                content
            )
            if m:
                p = os.path.expanduser(m.group(1).strip())
                if p.startswith("file://"):
                    p = p[7:]
                if p and os.path.isfile(p):
                    return p
        except Exception:
            pass

    return None


_WALLPAPER_ACCENT_CACHE: dict[str, object] = {
    "path": None,
    "mtime": 0.0,
    "spec": None,
    "result": None,
}


def get_linux_wallpaper_accent_color(spec: str = SPEC_2021) -> tuple[QColor | None, str, dict | None]:
    """Extract vibrant accent color and scheme variant from active desktop wallpaper on Linux.

    Returns:
        tuple (accent_color, scheme_type, precalculated_tokens_or_None)
    """
    wp_path = _detect_linux_wallpaper_path()
    if not wp_path:
        return None, "tonal-spot", None

    try:
        mtime = os.path.getmtime(wp_path)
    except OSError:
        mtime = 0.0

    if (
        _WALLPAPER_ACCENT_CACHE["path"] == wp_path
        and _WALLPAPER_ACCENT_CACHE["mtime"] == mtime
        and _WALLPAPER_ACCENT_CACHE["spec"] == spec
        and _WALLPAPER_ACCENT_CACHE["result"] is not None
    ):
        return _WALLPAPER_ACCENT_CACHE["result"]  # type: ignore[return-value]

    result: tuple[QColor | None, str, dict | None] = (None, "tonal-spot", None)

    # Check if matugen CLI is available for native system palette alignment.
    # matugen 4.x cannot emit the 2025 spec, so 2025 uses the in-app engine.
    matugen_bin = shutil.which("matugen") or ("/usr/sbin/matugen" if os.path.isfile("/usr/sbin/matugen") else None)
    if matugen_bin and spec != SPEC_2025:
        try:
            preferred = _get_preferred_scheme_type()
            cmd = [matugen_bin, "image", wp_path, "--dry-run", "-j", "hex", "--source-color-index", "0", "--mode", "dark"]
            if preferred:
                cmd.extend(["--type", preferred])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
            if res.returncode == 0:
                d = json.loads(res.stdout)
                c_dict = d.get("colors", {})
                tokens = {}
                for k in [
                    "primary", "on_primary", "primary_container", "on_primary_container",
                    "secondary", "on_secondary", "secondary_container", "on_secondary_container",
                    "tertiary", "on_tertiary", "tertiary_container", "on_tertiary_container",
                    "surface", "surface_dim", "surface_bright", "surface_container_lowest",
                    "surface_container_low", "surface_container", "surface_container_high",
                    "surface_container_highest", "surface_variant", "on_surface", "on_surface_variant",
                    "outline", "outline_variant", "error", "on_error", "error_container", "on_error_container"
                ]:
                    role_info = c_dict.get(k, {})
                    if isinstance(role_info, dict):
                        hex_val = role_info.get("dark", {}).get("color") or role_info.get("default", {}).get("color")
                    elif isinstance(role_info, str):
                        hex_val = role_info
                    else:
                        hex_val = None
                    if hex_val and isinstance(hex_val, str) and hex_val.startswith("#"):
                        tokens[k] = hex_val
                if "primary" in tokens and "surface" in tokens:
                    result = (QColor(tokens["primary"]), preferred or "neutral", tokens)
        except Exception as e:
            log.debug("Matugen dry-run extraction failed: %s", e)

    if result[0] is None:
        # Pure in-app Python extraction (independent of external tools)
        dom, scheme = _extract_dominant_color_from_image(wp_path)
        if dom and dom.isValid():
            result = (dom, scheme, None)

    _WALLPAPER_ACCENT_CACHE["path"] = wp_path
    _WALLPAPER_ACCENT_CACHE["mtime"] = mtime
    _WALLPAPER_ACCENT_CACHE["spec"] = spec
    _WALLPAPER_ACCENT_CACHE["result"] = result
    return result


class M3Theme:
    """Material 3 (Material You) Theme Manager."""

    def __init__(self, preset_key: str = "dynamic", seed=None, spec: str = SPEC_2021):
        self.preset_key = preset_key
        self.is_amoled = preset_key == "amoled"
        self.spec = _normalize_spec(spec)
        self._init_defaults()
        self.apply_theme(preset_key, seed=seed)

    def _init_defaults(self):
        """Default Material 3 dark tokens (Lavender fallback)."""
        self.primary = "#D0BCFF"
        self.on_primary = "#381E72"
        self.primary_container = "#4F378B"
        self.on_primary_container = "#EADDFF"

        self.secondary = "#CCC2DC"
        self.on_secondary = "#332D41"
        self.secondary_container = "#4A4458"
        self.on_secondary_container = "#E8DEF8"

        self.tertiary = "#EFB8C8"
        self.on_tertiary = "#492532"
        self.tertiary_container = "#633B48"
        self.on_tertiary_container = "#FFD8E4"

        self.surface = "#141218"
        self.surface_dim = "#141218"
        self.surface_bright = "#3B383E"
        self.surface_container_lowest = "#0F0D13"
        self.surface_container_low = "#1D1B20"
        self.surface_container = "#211F26"
        self.surface_container_high = "#2B2930"
        self.surface_container_highest = "#36343B"

        self.on_surface = "#E6E0E9"
        self.on_surface_variant = "#CAC4D0"
        self.outline = "#938F99"
        self.outline_variant = "#49454F"

        self.error = "#F2B8B5"
        self.on_error = "#601410"
        self.error_container = "#8C1D18"
        self.on_error_container = "#F9DEDC"

        self.success = "#81C784"
        self.on_success = "#1B5E20"
        self.success_container = "#2E7D32"

        self.warning = "#F1C047"
        self.on_warning = "#422C00"
        self.warning_container = "#5F4100"

    def get_swatch_quadrant_colors(self) -> list[str]:
        """Return [top_right, top_left, bottom_left, bottom_right] colors for dynamic theme swatch circle."""
        c_pri = getattr(self, "primary", "#D0BCFF")
        c_sec = getattr(self, "secondary", "#CCC2DC")
        c_ter = getattr(self, "tertiary", "#EFB8C8")
        c_con = getattr(self, "primary_container", getattr(self, "secondary_container", "#4A4458"))

        def _rgb_dist(c1: str, c2: str) -> float:
            q1, q2 = QColor(c1), QColor(c2)
            if not q1.isValid() or not q2.isValid():
                return 999.0
            return math.hypot(q1.red() - q2.red(), q1.green() - q2.green(), q1.blue() - q2.blue())

        # If primary, secondary, and tertiary are nearly identical (e.g. scheme-neutral / monochrome),
        # distribute across distinct tonal roles so all 4 quadrants are clearly visually distinguishable.
        if _rgb_dist(c_pri, c_sec) < 20.0 and _rgb_dist(c_pri, c_ter) < 20.0:
            c_sec = getattr(self, "outline", getattr(self, "on_surface_variant", "#93908c"))
            c_ter = getattr(self, "surface_container_highest", getattr(self, "surface_bright", "#363532"))

        return [c_sec, c_pri, c_ter, c_con]

    def _get_linux_portal_accent_color(self, bus=None) -> QColor | None:
        """Read native Linux Accent Color from XDG Desktop Portal."""
        return get_portal_accent_color(bus=bus)

    def _get_linux_portal_color_scheme(self, bus=None) -> int | None:
        """Read native Linux Color Scheme preference from XDG Desktop Portal."""
        return get_portal_color_scheme(bus=bus)

    def _get_linux_wallpaper_accent_color(self) -> tuple[QColor | None, str, dict | None]:
        """Read native Linux wallpaper accent color and variant from the desktop."""
        return get_linux_wallpaper_accent_color(spec=self.spec)

    def apply_theme(self, preset_key: str = "dynamic", seed=None):
        """Apply a preset or derive a seed from the system accent color."""
        self.preset_key = preset_key
        self.is_amoled = preset_key == "amoled"

        if preset_key == "custom":
            self.is_amoled = False
            self.generate_palette(_coerce_seed(seed) or PRESET_SEEDS["lavender"])
            return

        if preset_key != "dynamic" and preset_key in PRESET_SEEDS:
            seed = PRESET_SEEDS[preset_key]
            self.generate_palette(seed)
            return

        if sys.platform == "win32":
            accent = self._get_windows_accent_color()
            if accent:
                log.info("Applying dynamic theme from Windows accent color: %s", accent.name())
                self.generate_palette(accent)
                return

        if sys.platform.startswith("linux"):
            accent_res = self._get_linux_wallpaper_accent_color()
            if accent_res:
                tokens = None
                scheme_type = "tonal-spot"
                if isinstance(accent_res, tuple):
                    if len(accent_res) == 3:
                        accent, scheme_type, tokens = accent_res
                    elif len(accent_res) == 2:
                        accent, scheme_type = accent_res
                    else:
                        accent = accent_res[0]
                else:
                    accent = accent_res

                if tokens:
                    log.info("Applying dynamic theme from Linux desktop tokens")
                    for k, v in tokens.items():
                        setattr(self, k, v)
                    return

                if accent and hasattr(accent, "isValid") and accent.isValid():
                    # The low-colorfulness neutral fallback is a 2025 behavior;
                    # SPEC_2021 keeps the seed hue vivid unless the desktop
                    # explicitly asks for the neutral scheme.
                    if (self.spec == SPEC_2021 and scheme_type == "neutral"
                            and _get_preferred_scheme_type() != "scheme-neutral"):
                        scheme_type = "tonal-spot"
                    log.info("Applying dynamic theme from Linux wallpaper: %s (scheme: %s)", accent.name(), scheme_type)
                    self.generate_palette(accent, scheme_type=scheme_type)
                    return

            portal_accent = self._get_linux_portal_accent_color()
            if portal_accent:
                log.info("Applying dynamic theme from Linux XDG portal accent color: %s", portal_accent.name())
                self.generate_palette(portal_accent)
                return

        self.generate_palette(PRESET_SEEDS["lavender"])

    def _get_windows_accent_color(self) -> QColor | None:
        """Read native Windows Accent Color from DWM Registry."""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\DWM"
            )
            val, _ = winreg.QueryValueEx(key, "AccentColor")
            # AccentColor is ABGR format uint32
            r = val & 0xFF
            g = (val >> 8) & 0xFF
            b = (val >> 16) & 0xFF
            return QColor(r, g, b)
        except (OSError, AttributeError, ValueError) as e:
            log.debug("Windows AccentColor read failed: %s", e)
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\DWM"
                )
                val, _ = winreg.QueryValueEx(key, "ColorizationColor")
                # ColorizationColor is ARGB
                a = (val >> 24) & 0xFF
                r = (val >> 16) & 0xFF
                g = (val >> 8) & 0xFF
                b = val & 0xFF
                return QColor(r, g, b)
            except (OSError, AttributeError, ValueError) as e2:
                log.debug("Windows ColorizationColor read failed: %s", e2)
        return None

    def generate_palette(self, seed: QColor, scheme_type: str = "tonal-spot"):
        """Generate a dark Material 3 palette for the selected spec.

        SPEC_2021 keeps the seed hue more saturated; SPEC_2025 uses the
        M3 Expressive palette recipe with rotated tertiary/error hues.
        """
        _, seed_chroma, seed_hue = _rgb_to_lch(seed)
        is_2025 = self.spec == SPEC_2025
        is_blue = 250.0 <= seed_hue < 270.0

        if scheme_type == "neutral":
            if is_2025:
                primary_chroma = 12.0 if is_blue else 8.0
                secondary_chroma = 6.0 if is_blue else 4.0
                tertiary_hue = _rotated_hue(
                    seed_hue,
                    [0.0, 38.0, 105.0, 161.0, 204.0, 278.0, 333.0, 360.0],
                    [-32.0, 26.0, 10.0, -39.0, 24.0, -15.0, -32.0],
                )
                tertiary_chroma = 20.0
                neutral_chroma = 1.4
                neutral_variant_chroma = 3.1
                error_chroma = 50.0
            else:
                primary_chroma = 12.0
                secondary_chroma = 8.0
                tertiary_hue = _sanitize_degrees(seed_hue + _TONAL_SPOT_TERTIARY_HUE_SHIFT)
                tertiary_chroma = 16.0
                neutral_chroma = 2.0
                neutral_variant_chroma = 2.0
                error_chroma = _TONAL_SPOT_ERROR_CHROMA
        else:
            if is_2025:
                primary_chroma = 26.0
                secondary_chroma = 16.0
                tertiary_hue = _rotated_hue(
                    seed_hue,
                    [0.0, 20.0, 71.0, 161.0, 333.0, 360.0],
                    [-40.0, 48.0, -32.0, 40.0, -32.0],
                )
                tertiary_chroma = 28.0
                neutral_chroma = 5.0
                neutral_variant_chroma = 8.5
                error_chroma = 60.0
            else:
                primary_chroma = _TONAL_SPOT_PRIMARY_CHROMA
                secondary_chroma = _TONAL_SPOT_SECONDARY_CHROMA
                tertiary_hue = _sanitize_degrees(seed_hue + _TONAL_SPOT_TERTIARY_HUE_SHIFT)
                tertiary_chroma = _TONAL_SPOT_TERTIARY_CHROMA
                neutral_chroma = _TONAL_SPOT_NEUTRAL_CHROMA
                neutral_variant_chroma = _TONAL_SPOT_NEUTRAL_VARIANT_CHROMA
                error_chroma = _TONAL_SPOT_ERROR_CHROMA

        error_hue = (
            _piecewise_value(
                seed_hue,
                [0.0, 3.0, 13.0, 23.0, 33.0, 43.0, 153.0, 273.0, 360.0],
                [12.0, 22.0, 32.0, 12.0, 22.0, 32.0, 22.0, 12.0],
            )
            if is_2025
            else _TONAL_SPOT_ERROR_HUE
        )

        # For yellow/olive/green hues (40-140 deg in CIELAB), dark neutral tones
        # with chroma > 3 look like murky swamp mud. Clamp neutral chroma so
        # dark surfaces remain clean, elegant charcoal with a gentle warm tint.
        if 40.0 <= seed_hue <= 140.0:
            neutral_chroma = min(neutral_chroma, 2.0)
            neutral_variant_chroma = min(neutral_variant_chroma, 3.5)

        # Google Material Dislike rule: yellow-green / olive hues (88 to 115 deg)
        # look sickly when oversaturated, so clamp primary chroma to avoid mustard:
        if 88.0 <= seed_hue <= 115.0:
            primary_chroma = min(primary_chroma, 16.0)

        primary = (seed_hue, primary_chroma)
        secondary = (seed_hue, secondary_chroma)
        tertiary = (tertiary_hue, tertiary_chroma)
        neutral = (seed_hue, neutral_chroma)
        neutral_variant = (seed_hue, neutral_variant_chroma)
        error = (error_hue, error_chroma)
        success = (150.0, 60.0)
        # Keep warning readable as a warm signal color, but pull its hue
        # toward the seed so it belongs to the active palette (M3 custom
        # color harmonization, bounded to preserve the warning meaning).
        warning_hue = (
            _harmonize_hue(_STATUS_WARNING_HUE, seed_hue, _STATUS_HUE_HARMONIZE_DEG)
            if seed_chroma >= 8.0
            else _STATUS_WARNING_HUE
        )
        warning = (warning_hue, 65.0)

        def tone(palette: tuple[float, float], level: float) -> str:
            return _palette_tone(palette[0], palette[1], level).name()

        self.primary = tone(primary, 80)
        self.on_primary = tone(primary, 20)
        self.primary_container = tone(primary, 30)
        self.on_primary_container = tone(primary, 90)

        self.secondary = tone(secondary, 80)
        self.on_secondary = tone(secondary, 20)
        self.secondary_container = tone(secondary, 30)
        self.on_secondary_container = tone(secondary, 90)

        self.tertiary = tone(tertiary, 80)
        self.on_tertiary = tone(tertiary, 20)
        self.tertiary_container = tone(tertiary, 30)
        self.on_tertiary_container = tone(tertiary, 90)

        if self.is_amoled:
            self.surface = "#000000"
            self.surface_dim = "#000000"
            self.surface_bright = "#1A1A1A"
            self.surface_container_lowest = "#000000"
            self.surface_container_low = "#0A0A0A"
            self.surface_container = "#121212"
            self.surface_container_high = "#1C1C1C"
            self.surface_container_highest = "#262626"
            self.surface_variant = "#1E1E1E"
        else:
            if is_2025:
                surface_tones = {
                    "surface": 4,
                    "surface_dim": 4,
                    "surface_bright": 18,
                    "surface_container_lowest": 0,
                    "surface_container_low": 6,
                    "surface_container": 9,
                    "surface_container_high": 12,
                    "surface_container_highest": 15,
                }
            else:
                surface_tones = {
                    "surface": 6,
                    "surface_dim": 6,
                    "surface_bright": 24,
                    "surface_container_lowest": 4,
                    "surface_container_low": 10,
                    "surface_container": 12,
                    "surface_container_high": 17,
                    "surface_container_highest": 22,
                }
            for attr, level in surface_tones.items():
                setattr(self, attr, tone(neutral, level))
            self.surface_variant = tone(neutral_variant, 15 if is_2025 else 30)

        self.on_surface = tone(neutral, 90)
        self.on_surface_variant = tone(neutral_variant, 80)
        self.outline = tone(neutral_variant, 60)
        self.outline_variant = tone(neutral_variant, 30)

        self.error = tone(error, 80)
        self.on_error = tone(error, 20)
        self.error_container = tone(error, 30)
        self.on_error_container = tone(error, 90)

        self.success = tone(success, 80)
        self.on_success = tone(success, 20)
        self.success_container = tone(success, 30)

        self.warning = tone(warning, 80)
        self.on_warning = tone(warning, 20)
        self.warning_container = tone(warning, 30)

        if self.is_amoled:
            # Gray seed has no hue; tonal engine would tint accent greenish.
            self.primary = "#FFFFFF"
            self.on_primary = "#000000"
            self.primary_container = "#2E2E2E"
            self.on_primary_container = "#FFFFFF"

            self.secondary = "#C7C7C7"
            self.on_secondary = "#1B1B1B"
            self.secondary_container = "#2E2E2E"
            self.on_secondary_container = "#F5F5F5"

            self.on_surface = "#F2F2F2"
            self.on_surface_variant = "#B8B8B8"
            self.outline = "#8E8E8E"
            self.outline_variant = "#383838"

    def get_button_style(self, variant="filled") -> str:
        """Return Material 3 button stylesheet for various roles."""
        if variant == "filled":
            return f"""
                QPushButton {{
                    background-color: {self.primary};
                    color: {self.on_primary};
                    border-radius: 20px;
                    padding: 0px 24px;
                    height: 40px;
                    font-size: 14px;
                    font-weight: 600;
                    border: none;
                    outline: none;
                }}
                QPushButton:hover {{
                    background-color: {self.on_surface_variant};
                    color: {self.surface};
                }}
                QPushButton:pressed {{
                    background-color: {self.outline};
                }}
                QPushButton:disabled {{
                    background-color: rgba(255, 255, 255, 0.12);
                    color: rgba(255, 255, 255, 0.38);
                }}
            """
        elif variant == "tonal":
            return f"""
                QPushButton {{
                    background-color: {self.secondary_container};
                    color: {self.on_secondary_container};
                    border-radius: 14px;
                    padding: 0px 16px;
                    height: 36px;
                    font-size: 13px;
                    font-weight: 600;
                    border: none;
                    outline: none;
                }}
                QPushButton:hover {{
                    background-color: {self.surface_container_highest};
                }}
                QPushButton:pressed {{
                    background-color: {self.primary_container};
                    color: {self.on_primary_container};
                }}
            """
        elif variant == "text":
            return f"""
                QPushButton {{
                    color: {self.primary};
                    background: transparent;
                    border-radius: 18px;
                    padding: 0px 14px;
                    height: 36px;
                    font-size: 13px;
                    font-weight: 600;
                    border: none;
                    outline: none;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 0.14);
                }}
            """
        elif variant == "outlined":
            return f"""
                QPushButton {{
                    background: transparent;
                    color: {self.primary};
                    border: 1px solid {self.outline_variant};
                    border-radius: 18px;
                    padding: 0px 16px;
                    height: 36px;
                    font-size: 13px;
                    font-weight: 600;
                    outline: none;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border-color: {self.primary};
                }}
            """
        return ""

    def get_tooltip_style(self) -> str:
        """Return Material 3 styling for application tooltips (QToolTip)."""
        bg = getattr(self, "surface_container_highest", getattr(self, "surface_container_high", "#2B2930"))
        fg = getattr(self, "on_surface", "#E6E0E9")
        border = getattr(self, "outline_variant", "rgba(255, 255, 255, 0.12)")
        return f"""
            QToolTip {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 5px 9px;
                font-size: 11.5px;
            }}
        """

    def apply_tooltip_palette(self):
        """Set QToolTip palette on PySide6 level for Windows compatibility."""
        try:
            from PySide6.QtWidgets import QToolTip
            from PySide6.QtGui import QPalette, QColor
            bg = getattr(self, "surface_container_highest", getattr(self, "surface_container_high", "#2B2930"))
            fg = getattr(self, "on_surface", "#E6E0E9")
            p = QToolTip.palette()
            p.setColor(QPalette.ToolTipBase, QColor(bg))
            p.setColor(QPalette.ToolTipText, QColor(fg))
            QToolTip.setPalette(p)
        except Exception:
            pass

