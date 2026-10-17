"""Unit tests for Material You (M3) Theme Engine."""
import unittest
from unittest.mock import patch
from PySide6.QtGui import QColor

from utils.theme import M3Theme


class M3ThemeTest(unittest.TestCase):
    def test_default_tokens_exist(self):
        theme = M3Theme(preset_key="lavender")
        self.assertTrue(theme.primary.startswith("#"))
        self.assertTrue(theme.on_primary.startswith("#"))
        self.assertTrue(theme.primary_container.startswith("#"))
        self.assertTrue(theme.on_primary_container.startswith("#"))
        self.assertTrue(theme.secondary.startswith("#"))
        self.assertTrue(theme.secondary_container.startswith("#"))
        self.assertTrue(theme.tertiary.startswith("#"))
        self.assertTrue(theme.surface.startswith("#"))
        self.assertTrue(theme.surface_container.startswith("#"))
        self.assertTrue(theme.surface_container_high.startswith("#"))
        self.assertTrue(theme.outline.startswith("#"))
        self.assertTrue(theme.error.startswith("#"))
        self.assertTrue(theme.success.startswith("#"))

    def test_presets_generate_distinct_palettes(self):
        for preset_name in ("lavender", "ocean", "emerald", "coral", "amoled"):
            theme = M3Theme(preset_key=preset_name)
            self.assertEqual(theme.preset_key, preset_name)
            self.assertTrue(theme.primary.startswith("#"))
            if preset_name == "amoled":
                self.assertEqual(theme.surface, "#000000")

    def test_custom_seed_generation(self):
        theme = M3Theme()
        custom_seed = QColor(255, 100, 50)
        theme.generate_palette(custom_seed)
        self.assertTrue(theme.primary.startswith("#"))
        self.assertTrue(theme.on_primary.startswith("#"))
        self.assertTrue(theme.primary_container.startswith("#"))

    def test_button_styles(self):
        theme = M3Theme(preset_key="ocean")
        filled = theme.get_button_style("filled")
        tonal = theme.get_button_style("tonal")
        text = theme.get_button_style("text")
        outlined = theme.get_button_style("outlined")

        self.assertIn(theme.primary, filled)
        self.assertIn(theme.secondary_container, tonal)
        self.assertIn("transparent", text)
        self.assertIn("border", outlined)

    def test_tooltip_style(self):
        theme = M3Theme(preset_key="lavender")
        tt = theme.get_tooltip_style()
        self.assertIn("QToolTip", tt)
        self.assertIn(theme.on_surface, tt)

    @patch("utils.theme.M3Theme._get_windows_accent_color")
    def test_dynamic_windows_accent(self, mock_accent):
        mock_accent.return_value = QColor(0, 120, 215)
        theme = M3Theme(preset_key="dynamic")
        self.assertTrue(theme.primary.startswith("#"))


class XDGPortalThemeTest(unittest.TestCase):
    """Unit tests for FreeDesktop XDG Desktop Portal theme integration."""

    @patch("utils.theme.read_portal_setting")
    def test_portal_color_scheme_dark(self, mock_read):
        mock_read.return_value = 1
        from utils.theme import get_portal_color_scheme
        self.assertEqual(get_portal_color_scheme(), 1)

    @patch("utils.theme.read_portal_setting")
    def test_portal_color_scheme_light(self, mock_read):
        mock_read.return_value = 2
        from utils.theme import get_portal_color_scheme
        self.assertEqual(get_portal_color_scheme(), 2)

    @patch("utils.theme.read_portal_setting")
    def test_portal_color_scheme_no_preference(self, mock_read):
        mock_read.return_value = 0
        from utils.theme import get_portal_color_scheme
        self.assertEqual(get_portal_color_scheme(), 0)

    @patch("utils.theme.read_portal_setting")
    def test_portal_color_scheme_error(self, mock_read):
        mock_read.return_value = None
        from utils.theme import get_portal_color_scheme
        self.assertIsNone(get_portal_color_scheme())

    @patch("utils.theme.read_portal_setting")
    def test_portal_accent_color_float_tuple(self, mock_read):
        mock_read.return_value = (0.2, 0.6, 1.0)
        from utils.theme import get_portal_accent_color
        color = get_portal_accent_color()
        self.assertIsNotNone(color)
        self.assertTrue(color.isValid())
        self.assertAlmostEqual(color.redF(), 0.2, places=2)
        self.assertAlmostEqual(color.greenF(), 0.6, places=2)
        self.assertAlmostEqual(color.blueF(), 1.0, places=2)

    @patch("utils.theme.read_portal_setting")
    def test_portal_accent_color_int_tuple(self, mock_read):
        mock_read.return_value = (50, 150, 250)
        from utils.theme import get_portal_accent_color
        color = get_portal_accent_color()
        self.assertIsNotNone(color)
        self.assertTrue(color.isValid())
        self.assertEqual(color.red(), 50)
        self.assertEqual(color.green(), 150)
        self.assertEqual(color.blue(), 250)

    @patch("utils.theme.read_portal_setting")
    def test_portal_accent_color_hex_string(self, mock_read):
        mock_read.return_value = "#FF5722"
        from utils.theme import get_portal_accent_color
        color = get_portal_accent_color()
        self.assertIsNotNone(color)
        self.assertEqual(color.name().upper(), "#FF5722")

    @patch("utils.theme.read_portal_setting")
    def test_portal_accent_color_invalid(self, mock_read):
        mock_read.return_value = "invalid_color"
        from utils.theme import get_portal_accent_color
        color = get_portal_accent_color()
        self.assertIsNone(color)

    def test_read_portal_setting_with_mock_bus(self):
        from utils.theme import read_portal_setting
        from unittest.mock import MagicMock

        fake_bus = MagicMock()
        fake_bus.isConnected.return_value = True

        with patch("PySide6.QtDBus.QDBusInterface") as mock_iface_cls:
            mock_iface = MagicMock()
            mock_iface.isValid.return_value = True
            mock_reply = MagicMock()
            mock_reply.isValid.return_value = True
            mock_reply.arguments.return_value = [1]
            mock_iface.call.return_value = mock_reply
            mock_iface_cls.return_value = mock_iface

            val = read_portal_setting("org.freedesktop.appearance", "color-scheme", bus=fake_bus)
            self.assertEqual(val, 1)
            mock_iface.call.assert_called_once_with("Read", "org.freedesktop.appearance", "color-scheme")

    def test_read_portal_setting_bus_disconnected(self):
        from utils.theme import read_portal_setting
        from unittest.mock import MagicMock

        fake_bus = MagicMock()
        fake_bus.isConnected.return_value = False
        val = read_portal_setting("org.freedesktop.appearance", "color-scheme", bus=fake_bus)
        self.assertIsNone(val)

    @patch("sys.platform", "linux")
    @patch("utils.theme.M3Theme._get_linux_portal_accent_color")
    def test_linux_dynamic_applies_portal_accent(self, mock_portal_accent):
        mock_portal_accent.return_value = QColor("#E91E63")
        theme = M3Theme(preset_key="dynamic")
        self.assertEqual(theme.preset_key, "dynamic")
        self.assertTrue(theme.primary.startswith("#"))
        mock_portal_accent.assert_called()

    @patch("sys.platform", "linux")
    @patch("utils.theme.M3Theme._get_linux_portal_accent_color", return_value=None)
    def test_linux_dynamic_fallback_to_lavender(self, mock_portal):
        theme = M3Theme(preset_key="dynamic")
        self.assertEqual(theme.preset_key, "dynamic")
        self.assertEqual(theme.primary, M3Theme(preset_key="lavender").primary)




if __name__ == "__main__":
    unittest.main()


def _make_window(monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    import utils.twinsock as tw
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    win = RoundedWindow()
    win.show()
    return win


def test_lerp_color_hex_endpoints_and_midpoint():
    from ui.theme_transition import lerp_color_hex

    assert lerp_color_hex("#000000", "#ffffff", 0.0) == "#000000"
    assert lerp_color_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert lerp_color_hex("#000000", "#ffffff", 0.5) == "#808080"
    assert lerp_color_hex("#000000", "#ffffff", -1.0) == "#000000"
    assert lerp_color_hex("#000000", "#ffffff", 2.0) == "#ffffff"


def test_lerp_color_hex_invalid_falls_back_to_target():
    from ui.theme_transition import lerp_color_hex

    assert lerp_color_hex("not-a-color", "#ffffff", 0.5) == "#ffffff"


def test_lerp_theme_tokens_interpolates_each_key():
    from ui.theme_transition import lerp_theme_tokens

    start = {"primary": "#000000", "surface": "#101010"}
    end = {"primary": "#ffffff", "surface": "#101010"}
    mid = lerp_theme_tokens(start, end, 0.5)
    assert mid["primary"] == "#808080"
    assert mid["surface"] == "#101010"
    assert lerp_theme_tokens(start, end, 0.0) == start
    assert lerp_theme_tokens(start, end, 1.0) == end


def test_theme_switch_lerps_to_target(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from utils.theme import M3Theme

    win = _make_window(monkeypatch, tmp_path)
    QApplication.processEvents()

    win._on_settings_theme_changed("ocean")
    assert win.theme.preset_key == "ocean"
    assert win.settings["theme_preset"] == "ocean"

    expected = M3Theme(preset_key="ocean")
    assert win._theme_target_tokens["primary"] == expected.primary
    win._theme_transition.finish_now()
    QApplication.processEvents()
    assert win.theme.primary == expected.primary
    assert win.theme.surface == expected.surface


def test_theme_rapid_reswitch_targets_latest(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from utils.theme import M3Theme

    win = _make_window(monkeypatch, tmp_path)
    QApplication.processEvents()

    win._on_settings_theme_changed("ocean")
    win._on_settings_theme_changed("emerald")
    QApplication.processEvents()

    expected = M3Theme(preset_key="emerald")
    assert win.theme.preset_key == "emerald"
    assert win._theme_target_tokens["primary"] == expected.primary
    win._theme_transition.finish_now()
    QApplication.processEvents()
    assert win.theme.primary == expected.primary


def test_theme_same_preset_applies_without_animation(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    win = _make_window(monkeypatch, tmp_path)
    QApplication.processEvents()

    current = win.settings.get("theme_preset", "dynamic")
    win._on_settings_theme_changed(current)
    QApplication.processEvents()
    assert win._theme_transition.is_running() is False
    assert win._theme_target_tokens == {}


def test_custom_seed_builds_palette_from_color():
    from PySide6.QtGui import QColor
    from utils.theme import M3Theme

    custom = M3Theme(preset_key="custom", seed=QColor("#4BA3E3"))
    assert custom.preset_key == "custom"
    assert custom.is_amoled is False
    assert custom.primary == M3Theme(preset_key="ocean").primary


def test_custom_seed_accepts_hex_and_falls_back():
    from utils.theme import M3Theme

    assert M3Theme(preset_key="custom", seed="#4BA3E3").primary == M3Theme(preset_key="ocean").primary
    assert M3Theme(preset_key="custom", seed="nope").primary == M3Theme(preset_key="lavender").primary
    assert M3Theme(preset_key="custom").primary == M3Theme(preset_key="lavender").primary


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


def test_theme_switch_to_custom_uses_stored_color(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from utils.theme import M3Theme

    win = _make_window(monkeypatch, tmp_path)
    QApplication.processEvents()

    win.settings["theme_custom_color"] = "#4BA3E3"
    win._on_settings_theme_changed("custom")
    assert win.theme.preset_key == "custom"
    win._theme_transition.finish_now()
    QApplication.processEvents()
    assert win.theme.primary == M3Theme(preset_key="ocean").primary
