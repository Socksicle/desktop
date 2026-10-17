"""Material You (Material Design 3 / SPEC_2025) Theme Engine for Socksicle.

Provides dynamic tonal color generation, native system accent color
extraction (Windows DWM, Linux XDG Desktop Portal), and curated
Material 3 preset palettes.

The tonal engine follows the M3 SPEC_2025 recipe with tonal-spot palettes:
seed -> palettes faithful to the seed hue -> dark-scheme tone mapping.
No playful hue rotations: every role stays in the seed's own hue family.
Tones are CIELAB L* (which is exactly what an M3 "tone" is); hue/chroma
live in CIELAB LCH as a close, dependency-free stand-in for CAM16-UCS.
"""
import logging
import sys
from PySide6.QtGui import QColor

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


def _sanitize_degrees(degrees: float) -> float:
    return degrees % 360.0


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


class M3Theme:
    """Material 3 (Material You) Theme Manager."""

    def __init__(self, preset_key: str = "dynamic", seed=None):
        self.preset_key = preset_key
        self.is_amoled = preset_key == "amoled"
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

    def _get_linux_portal_accent_color(self, bus=None) -> QColor | None:
        """Read native Linux Accent Color from XDG Desktop Portal."""
        return get_portal_accent_color(bus=bus)

    def _get_linux_portal_color_scheme(self, bus=None) -> int | None:
        """Read native Linux Color Scheme preference from XDG Desktop Portal."""
        return get_portal_color_scheme(bus=bus)

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
            accent = self._get_linux_portal_accent_color()
            if accent:
                log.info("Applying dynamic theme from Linux XDG portal accent color: %s", accent.name())
                self.generate_palette(accent)
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

    def generate_palette(self, seed: QColor):
        """Generate a Material 3 (SPEC_2025, tonal-spot) dark palette.

        Every palette keeps the seed hue, so the whole theme stays in one
        hue family with rich, non-washed-out chroma.
        """
        _, _, seed_hue = _rgb_to_lch(seed)
        primary = (seed_hue, _TONAL_SPOT_PRIMARY_CHROMA)
        secondary = (seed_hue, _TONAL_SPOT_SECONDARY_CHROMA)
        tertiary = (
            _sanitize_degrees(seed_hue + _TONAL_SPOT_TERTIARY_HUE_SHIFT),
            _TONAL_SPOT_TERTIARY_CHROMA,
        )
        neutral = (seed_hue, _TONAL_SPOT_NEUTRAL_CHROMA)
        neutral_variant = (seed_hue, _TONAL_SPOT_NEUTRAL_VARIANT_CHROMA)
        error = (_TONAL_SPOT_ERROR_HUE, _TONAL_SPOT_ERROR_CHROMA)
        success = (150.0, 60.0)
        warning = (85.0, 65.0)

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
            self.surface = tone(neutral, 6)
            self.surface_dim = tone(neutral, 6)
            self.surface_bright = tone(neutral, 24)
            self.surface_container_lowest = tone(neutral, 4)
            self.surface_container_low = tone(neutral, 10)
            self.surface_container = tone(neutral, 12)
            self.surface_container_high = tone(neutral, 17)
            self.surface_container_highest = tone(neutral, 22)
            self.surface_variant = tone(neutral_variant, 30)

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

