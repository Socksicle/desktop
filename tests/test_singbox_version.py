"""Tests for the sing-box minimum version check (warning only)."""
import subprocess
from unittest import mock

from utils.engines import singbox_engine as sb


def test_parse_singbox_version_supported_formats():
    assert sb.parse_singbox_version("sing-box version 1.14.0") == (1, 14, 0)
    assert sb.parse_singbox_version("sing-box version 1.14.0 (go1.24)") == (1, 14, 0)
    assert sb.parse_singbox_version("v1.14.1") == (1, 14, 1)
    assert sb.parse_singbox_version("1.15") == (1, 15, 0)


def test_parse_singbox_version_invalid():
    assert sb.parse_singbox_version(None) is None
    assert sb.parse_singbox_version("") is None
    assert sb.parse_singbox_version("no version here") is None


def test_is_singbox_version_supported():
    assert sb.is_singbox_version_supported((1, 14, 0)) is True
    assert sb.is_singbox_version_supported((1, 14, 1)) is True
    assert sb.is_singbox_version_supported((1, 15, 0)) is True
    assert sb.is_singbox_version_supported((2, 0, 0)) is True
    assert sb.is_singbox_version_supported((1, 13, 9)) is False
    assert sb.is_singbox_version_supported((1, 10, 0)) is False
    assert sb.is_singbox_version_supported(None) is True


def test_get_singbox_version_parses_stdout(tmp_path):
    binary = tmp_path / "sing-box"
    binary.write_bytes(b"x" * 10)
    proc = mock.Mock(stdout="sing-box version 1.12.0\n", stderr="", returncode=0)
    with mock.patch.object(sb.subprocess, "run", return_value=proc):
        assert sb.get_singbox_version(binary) == ("1.12.0", (1, 12, 0))


def test_get_singbox_version_unknown_on_failure(tmp_path):
    binary = tmp_path / "sing-box"
    binary.write_bytes(b"x" * 10)
    with mock.patch.object(
        sb.subprocess, "run",
        side_effect=subprocess.SubprocessError("boom"),
    ):
        assert sb.get_singbox_version(binary) == (None, None)


def test_get_singbox_version_no_binary():
    with mock.patch.object(sb, "_find_binary", return_value=None):
        assert sb.get_singbox_version(None) == (None, None)


def test_min_version_constant():
    assert sb.MIN_SINGBOX_VERSION == (1, 14, 0)
    assert "SagerNet/sing-box" in sb.SINGBOX_RELEASES_URL
