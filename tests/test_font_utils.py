"""Tests for font_utils and Windows-specific Noto Color Emoji font integration."""
import sys
from utils.font_utils import init_app_fonts
import utils.font_utils as fu


def test_init_app_fonts_on_windows(qapp, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    fu._EMOJI_FONT_FAMILY = None
    family = init_app_fonts()
    assert family == "Noto Color Emoji"


def test_init_app_fonts_on_linux_noops(qapp, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fu._EMOJI_FONT_FAMILY = None
    family = init_app_fonts()
    assert family is None
