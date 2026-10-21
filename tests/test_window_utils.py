"""Tests for tiling window manager detection, resizing, and phone-shaped window configuration."""
from unittest import mock
import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget

from utils.window_utils import (
    configure_main_window,
    cursor_for_edge,
    get_phone_window_size,
    get_resize_edge,
    is_tiling_wm,
)


def test_override_env():
    with mock.patch.dict(os.environ, {"SOCKSICLE_TILING": "1"}):
        assert is_tiling_wm() is True
    with mock.patch.dict(os.environ, {"SOCKSICLE_TILING": "0"}):
        assert is_tiling_wm() is False


def test_floating_desktops():
    for de in ("KDE", "GNOME", "xfce", "cinnamon", "ubuntu:GNOME"):
        with mock.patch("sys.platform", "linux"), \
                mock.patch("utils.window_utils.platform_name", return_value="x11"), \
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_CURRENT_DESKTOP": de,
                        "XDG_SESSION_DESKTOP": de,
                        "HYPRLAND_INSTANCE_SIGNATURE": "",
                        "SWAYSOCK": "",
                        "I3SOCK": "",
                    },
                    clear=True,
                ):
            assert is_tiling_wm() is False, f"Failed for {de}"


def test_tiling_desktops():
    for wm in ("Hyprland", "sway", "i3", "bspwm", "river"):
        with mock.patch("sys.platform", "linux"), \
                mock.patch("utils.window_utils.platform_name", return_value="x11"), \
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_CURRENT_DESKTOP": wm,
                        "XDG_SESSION_DESKTOP": wm,
                    },
                    clear=True,
                ):
            assert is_tiling_wm() is True, f"Failed for {wm}"


def test_configure_main_window_tiling(qapp):
    w = QWidget()
    with mock.patch("utils.window_utils.is_tiling_wm", return_value=True), \
            mock.patch("utils.window_utils.get_phone_window_size",
                       return_value=(440, 800)):
        configure_main_window(w, 440, 800)
        assert w.minimumWidth() == 360
        assert w.minimumHeight() == 500
        assert w.width() == 440
        assert w.height() == 800
        # Process pending timers to test unlocking
        qapp.processEvents()


def test_configure_main_window_floating(qapp):
    w = QWidget()
    with mock.patch("utils.window_utils.is_tiling_wm", return_value=False):
        configure_main_window(w, 440, 800)
        assert w.minimumWidth() == 360
        assert w.maximumWidth() > 440


def test_get_resize_edge():
    # Top-left corner
    assert get_resize_edge(QPoint(2, 2), 440, 800) == (Qt.TopEdge | Qt.LeftEdge)
    # Bottom-right corner
    assert get_resize_edge(QPoint(435, 795), 440, 800) == (Qt.RightEdge | Qt.BottomEdge)
    # Right border
    assert get_resize_edge(QPoint(435, 400), 440, 800) == Qt.RightEdge
    # Interior
    assert get_resize_edge(QPoint(200, 200), 440, 800) is None


def test_cursor_for_edge():
    assert cursor_for_edge(Qt.LeftEdge) == Qt.SizeHorCursor
    assert cursor_for_edge(Qt.TopEdge) == Qt.SizeVerCursor
    assert cursor_for_edge(Qt.TopEdge | Qt.LeftEdge) == Qt.SizeFDiagCursor
    assert cursor_for_edge(Qt.TopEdge | Qt.RightEdge) == Qt.SizeBDiagCursor


def test_apply_hyprland_window_rules():
    from utils.window_utils import apply_hyprland_window_rules

    # When not in Hyprland
    with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "", "HYPRLAND_CMD": ""}, clear=True):
        with mock.patch("subprocess.run") as mock_run:
            apply_hyprland_window_rules()
            mock_run.assert_not_called()

    # When in Hyprland with Lua eval support
    with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "sig123"}):
        with mock.patch("shutil.which", return_value="/usr/bin/hyprctl"), \
             mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=b"ok")
            apply_hyprland_window_rules()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "hyprctl"
            assert args[1] == "eval"
            assert "rounding = 0" in args[2]
            assert "no_shadow = true" in args[2]
            assert "no_blur = true" in args[2]

