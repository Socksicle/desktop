"""Unit tests for Material You (M3) Theme Engine."""
import unittest
from unittest.mock import patch, MagicMock
from PySide6.QtGui import QColor

from utils.theme import M3Theme, _rgb_to_lch


class M3ThemeTest(unittest.TestCase):

    def test_theme_tokens_presets_and_seed(self):
        theme = M3Theme(preset_key="lavender")
        tokens = [
            theme.primary, theme.on_primary, theme.primary_container, theme.on_primary_container,
            theme.secondary, theme.secondary_container, theme.tertiary, theme.surface,
            theme.surface_container, theme.surface_container_high, theme.outline, theme.error, theme.success,
        ]
        for tok in tokens:
            self.assertTrue(tok.startswith("#"))

        # Presets distinct palettes
        for preset_name in ("lavender", "ocean", "emerald", "coral", "amoled"):
            t = M3Theme(preset_key=preset_name)
            self.assertEqual(t.preset_key, preset_name)
            self.assertTrue(t.primary.startswith("#"))
            if preset_name == "amoled":
                self.assertEqual(t.surface, "#000000")

        # Custom seed generation
        theme_custom = M3Theme()
        theme_custom.generate_palette(QColor(255, 100, 50))
        self.assertTrue(theme_custom.primary.startswith("#"))
        self.assertTrue(theme_custom.on_primary.startswith("#"))

    def test_theme_specs_and_palettes(self):
        self.assertEqual(M3Theme(preset_key="ocean").spec, "2021")

        classic = M3Theme(preset_key="ocean")
        expressive = M3Theme(preset_key="ocean", spec="2025")
        self.assertGreater(_rgb_to_lch(QColor(classic.primary))[1], _rgb_to_lch(QColor(expressive.primary))[1])
        self.assertNotEqual(classic.tertiary, expressive.tertiary)
        self.assertNotEqual(classic.surface_container, expressive.surface_container)

        # Fallback to 2021
        theme_fallback = M3Theme(preset_key="ocean", spec="1999")
        self.assertEqual(theme_fallback.spec, "2021")
        self.assertEqual(theme_fallback.primary, classic.primary)

        # Warning hue tracks seed but stays amber (60 to 110 deg)
        hues = {key: _rgb_to_lch(QColor(M3Theme(preset_key=key).warning))[2] for key in ("emerald", "lavender", "ocean", "coral")}
        for hue in hues.values():
            self.assertGreaterEqual(hue, 60.0)
            self.assertLessEqual(hue, 110.0)
        self.assertGreater(hues["emerald"], hues["lavender"])

        # Spec 2021 vs 2025 wallpaper accents
        with patch("sys.platform", "linux"), \
             patch("utils.theme._get_preferred_scheme_type", return_value=None), \
             patch("utils.theme.M3Theme._get_linux_wallpaper_accent_color", return_value=(QColor("#5E798B"), "neutral", None)):
            t21 = M3Theme(preset_key="dynamic", spec="2021")
            self.assertGreater(_rgb_to_lch(QColor(t21.primary))[1], 20.0)

            t25 = M3Theme(preset_key="dynamic", spec="2025")
            self.assertLess(_rgb_to_lch(QColor(t25.primary))[1], 16.0)

    def test_theme_styles(self):
        theme = M3Theme(preset_key="ocean")
        self.assertIn(theme.primary, theme.get_button_style("filled"))
        self.assertIn(theme.secondary_container, theme.get_button_style("tonal"))
        self.assertIn("transparent", theme.get_button_style("text"))
        self.assertIn("border", theme.get_button_style("outlined"))

        tt = M3Theme(preset_key="lavender").get_tooltip_style()
        self.assertIn("QToolTip", tt)
        self.assertIn(M3Theme(preset_key="lavender").on_surface, tt)

    @patch("utils.theme.M3Theme._get_windows_accent_color")
    def test_dynamic_windows_accent(self, mock_accent):
        mock_accent.return_value = QColor(0, 120, 215)
        theme = M3Theme(preset_key="dynamic")
        self.assertTrue(theme.primary.startswith("#"))


class XDGPortalThemeTest(unittest.TestCase):

    @patch("utils.theme.read_portal_setting")
    def test_portal_color_scheme_and_accent_formats(self, mock_read):
        from utils.theme import get_portal_color_scheme, get_portal_accent_color
        for val in (1, 2, 0, None):
            mock_read.return_value = val
            self.assertEqual(get_portal_color_scheme(), val)

        # Float tuple
        mock_read.return_value = (0.2, 0.6, 1.0)
        self.assertAlmostEqual(get_portal_accent_color().redF(), 0.2, places=2)

        # Int tuple
        mock_read.return_value = (50, 150, 250)
        self.assertEqual(get_portal_accent_color().red(), 50)

        # Hex string
        mock_read.return_value = "#FF5722"
        self.assertEqual(get_portal_accent_color().name().upper(), "#FF5722")

        # Invalid
        mock_read.return_value = "invalid_color"
        self.assertIsNone(get_portal_accent_color())

    def test_read_portal_setting_bus_states(self):
        from utils.theme import read_portal_setting

        fake_bus = MagicMock(isConnected=MagicMock(return_value=True))
        with patch("PySide6.QtDBus.QDBusInterface") as mock_iface_cls:
            mock_iface = MagicMock(isValid=MagicMock(return_value=True))
            mock_reply = MagicMock(isValid=MagicMock(return_value=True), arguments=MagicMock(return_value=[1]))
            mock_iface.call.return_value = mock_reply
            mock_iface_cls.return_value = mock_iface

            val = read_portal_setting("org.freedesktop.appearance", "color-scheme", bus=fake_bus)
            self.assertEqual(val, 1)

        # Disconnected bus returns None
        fake_bus.isConnected.return_value = False
        self.assertIsNone(read_portal_setting("org.freedesktop.appearance", "color-scheme", bus=fake_bus))

    @patch("sys.platform", "linux")
    def test_linux_dynamic_accent_priority_and_fallback(self):
        # 1. Portal accent takes priority
        with patch("utils.theme.M3Theme._get_linux_portal_accent_color", return_value=QColor("#E91E63")), \
             patch("utils.theme.M3Theme._get_linux_wallpaper_accent_color", return_value=None):
            t_portal = M3Theme(preset_key="dynamic")
            self.assertEqual(t_portal.preset_key, "dynamic")
            self.assertTrue(t_portal.primary.startswith("#"))

        # 2. Wallpaper accent fallback
        with patch("utils.theme.M3Theme._get_linux_portal_accent_color", return_value=None), \
             patch("utils.theme.M3Theme._get_linux_wallpaper_accent_color", return_value=QColor("#C4BB8A")):
            t_wp = M3Theme(preset_key="dynamic")
            self.assertEqual(t_wp.preset_key, "dynamic")
            self.assertTrue(t_wp.primary.startswith("#"))

        # 3. Fallback to lavender when both missing
        with patch("utils.theme.M3Theme._get_linux_portal_accent_color", return_value=None), \
             patch("utils.theme.M3Theme._get_linux_wallpaper_accent_color", return_value=None):
            t_fb = M3Theme(preset_key="dynamic")
            self.assertEqual(t_fb.preset_key, "dynamic")
            self.assertEqual(t_fb.primary, M3Theme(preset_key="lavender").primary)


# ─── Standalone transition & seed tests ─────────────────────────────────────

def test_lerp_color_and_tokens():
    from ui.theme_transition import lerp_color_hex, lerp_theme_tokens

    assert lerp_color_hex("#000000", "#ffffff", 0.0) == "#000000"
    assert lerp_color_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert lerp_color_hex("#000000", "#ffffff", 0.5) == "#808080"
    assert lerp_color_hex("#000000", "#ffffff", -1.0) == "#000000"
    assert lerp_color_hex("#000000", "#ffffff", 2.0) == "#ffffff"
    assert lerp_color_hex("not-a-color", "#ffffff", 0.5) == "#ffffff"

    start = {"primary": "#000000", "surface": "#101010"}
    end = {"primary": "#ffffff", "surface": "#101010"}
    mid = lerp_theme_tokens(start, end, 0.5)
    assert mid["primary"] == "#808080"
    assert mid["surface"] == "#101010"
    assert lerp_theme_tokens(start, end, 0.0) == start
    assert lerp_theme_tokens(start, end, 1.0) == end


def test_custom_seed_resolution():
    from PySide6.QtGui import QColor

    custom = M3Theme(preset_key="custom", seed=QColor("#4BA3E3"))
    assert custom.preset_key == "custom"
    assert custom.is_amoled is False
    assert custom.primary == M3Theme(preset_key="ocean").primary
    assert M3Theme(preset_key="custom", seed="#4BA3E3").primary == M3Theme(preset_key="ocean").primary
    assert M3Theme(preset_key="custom", seed="nope").primary == M3Theme(preset_key="lavender").primary
    assert M3Theme(preset_key="custom").primary == M3Theme(preset_key="lavender").primary


def test_dominant_color_extraction(tmp_path):
    from PySide6.QtGui import QImage, QColor
    from utils.theme import _extract_dominant_color_from_image

    img_path = str(tmp_path / "test_wp.png")
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("#2E7D32"))
    img.setPixelColor(0, 0, QColor("#FF0000"))
    img.setPixelColor(1, 1, QColor("#0000FF"))
    img.save(img_path)

    dom, _ = _extract_dominant_color_from_image(img_path)
    assert dom is not None
    assert dom.isValid()
    assert 100 <= dom.hsvHue() <= 140

    # Monochrome black returns None
    img_black = str(tmp_path / "black_wp.png")
    img_b = QImage(32, 32, QImage.Format_RGB32)
    img_b.fill(QColor("#000000"))
    img_b.save(img_black)
    assert _extract_dominant_color_from_image(img_black)[0] is None


def test_transition_decimates_intermediate_frames(qapp):
    from ui.theme_transition import ThemeColorTransition

    anim = ThemeColorTransition()
    seen = []
    anim.frame.connect(seen.append)
    anim.start({"primary": "#000000"}, {"primary": "#ffffff"})
    anim.stop()
    before = len(seen)
    for v in (0.1, 0.2, 0.3, 0.4):
        anim._set_blend(v)
    assert len(seen) - before == 2
    assert seen[-1] == {"primary": "#666666"}
    anim.finish_now()
    assert seen[-1] == {"primary": "#ffffff"}


def test_window_theme_switching(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from ui.main_window import RoundedWindow
    import utils.twinsock as tw

    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    win = RoundedWindow()
    try:
        QApplication.processEvents()

        # 1. Switch lerps to target
        win._on_settings_theme_changed("ocean")
        assert win.theme.preset_key == "ocean"
        assert win.settings["theme_preset"] == "ocean"
        expected = M3Theme(preset_key="ocean")
        assert win._theme_target_tokens["primary"] == expected.primary
        win._theme_transition.finish_now()
        QApplication.processEvents()
        assert win.theme.primary == expected.primary
        assert win.theme.surface == expected.surface

        # 2. Rapid reswitch targets latest
        win._on_settings_theme_changed("ocean")
        win._on_settings_theme_changed("emerald")
        QApplication.processEvents()
        expected_em = M3Theme(preset_key="emerald")
        assert win.theme.preset_key == "emerald"
        assert win._theme_target_tokens["primary"] == expected_em.primary
        win._theme_transition.finish_now()
        QApplication.processEvents()
        assert win.theme.primary == expected_em.primary

        # 3. Same preset applies without animation
        current = win.settings.get("theme_preset", "emerald")
        win._on_settings_theme_changed(current)
        QApplication.processEvents()
        assert win._theme_transition.is_running() is False
        assert win._theme_target_tokens == {}

        # 4. Custom color uses stored color
        win.settings["theme_custom_color"] = "#4BA3E3"
        win._on_settings_theme_changed("custom")
        assert win.theme.preset_key == "custom"
        win._theme_transition.finish_now()
        QApplication.processEvents()
        assert win.theme.primary == M3Theme(preset_key="ocean").primary

        # 5. Selected spec (2025)
        win.settings["theme_spec"] = "2025"
        win._on_settings_theme_changed("ocean")
        expected_2025 = M3Theme(preset_key="ocean", spec="2025")
        win._theme_transition.finish_now()
        QApplication.processEvents()
        assert win.theme.primary == expected_2025.primary
    finally:
        win.close()


if __name__ == "__main__":
    unittest.main()
