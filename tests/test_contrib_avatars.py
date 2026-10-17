"""Contributor avatar loading: GUI-thread decode, disk cache, failure tolerance."""
import base64

import pytest

from ui.screen_about import CircularAvatarView


# Valid 1x1 PNG (opaque red pixel).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def test_avatar_slot_decodes_caches_and_shows(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    av = CircularAvatarView("I", "https://example.com/a.png", "avatar_unit.png")
    assert not av.has_image()

    # The download worker delivers raw bytes; decode happens on the GUI thread.
    av._on_avatar_loaded(bytes(_PNG_1X1))

    assert av.has_image()
    assert (tmp_path / "avatars" / "avatar_unit.png").is_file()


def test_avatar_slot_rejects_garbage(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    av = CircularAvatarView("P", "https://example.com/b.png", "avatar_garbage.png")
    av._on_avatar_loaded(b"definitely not an image")
    assert not av.has_image()
    assert not (tmp_path / "avatars" / "avatar_garbage.png").exists()


def test_avatar_slot_rejects_empty(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    av = CircularAvatarView("P", "https://example.com/b.png", "avatar_empty.png")
    av._on_avatar_loaded(b"")
    assert not av.has_image()


def test_avatar_loads_from_disk_cache(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    cache_dir = tmp_path / "avatars"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "avatar_cached.png").write_bytes(_PNG_1X1)

    av = CircularAvatarView("C", "https://example.com/c.png", "avatar_cached.png")
    assert av.has_image()
