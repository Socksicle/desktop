"""Tests for the portable TwinSock v3 vault (utils/twinsock.py) and its integration."""
import base64
import json

import pytest

import utils.twinsock as tw


@pytest.fixture(autouse=True)
def vault_env():
    tw._reset()
    yield
    tw._reset()


def _ss_link(host):
    import base64 as b64
    userinfo = b64.urlsafe_b64encode(b"aes-256-gcm:password").decode().rstrip("=")
    return f"ss://{userinfo}@{host}:8388#ServerOne"


def test_roundtrip_all_secret_fields(vault_env):
    values = ["simple", "unicode key ✓", "x" * 300,
              "with spaces  and\ttabs", "1234567890!@#$%^&*()"]
    for purpose in ("manual", "subscriptions"):
        fields = tw.SECRET_FIELDS + (("url",) if purpose == "subscriptions" else ())
        for field in fields:
            for val in values:
                tok = tw.encrypt_field(purpose, val)
                assert tok.startswith("tws3.")
                assert tok != val
                assert tw.decrypt_field(purpose, tok) == val


def test_v3_token_version_and_layout(vault_env):
    tok = tw.encrypt_field("manual", "test-secret")
    assert tok.startswith("tws3.")
    raw = tw._b64url_decode(tok[5:])
    assert raw[0] == 0x03  # v3 version byte
    # 1 byte ver + 12 byte nonce + 16 byte AES-GCM tag + len("test-secret")
    assert len(raw) == 1 + 12 + 16 + len("test-secret")


def test_vault_is_portable_across_machines(vault_env):
    """The config belongs to the user: tokens decrypt without any machine
    binding, so copying servers.json to a second PC keeps working."""
    tok = tw.encrypt_field("manual", "keep-me-safe")
    tw._reset()
    assert tw.decrypt_field("manual", tok) == "keep-me-safe"


def test_no_drawer_file_created(vault_env, tmp_path):
    """Sealing must never create a drawer.json second sock next to the config."""
    tw.seal_dict("manual", {"password": "pw", "host": "h"}, tw.SECRET_FIELDS)
    assert not (tmp_path / "drawer.json").exists()
    assert list(tmp_path.iterdir()) == []


def test_empty_string_not_tokenized(vault_env):
    assert tw.encrypt_field("manual", "") == ""
    assert tw.decrypt_field("manual", "") == ""
    assert tw.tokenize(b"k" * 32, "") == ""
    assert tw.detokenize(b"k" * 32, "") == ""


def test_flip_byte_in_token_raises(vault_env):
    k = b"K" * 32
    tok = tw.tokenize(k, "some secret data")
    payload = tok[5:]
    raw = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    for flip_at in (1, 10, len(raw) - 1):
        raw2 = bytearray(raw)
        raw2[flip_at] ^= 0x01
        broken = "tws3." + base64.urlsafe_b64encode(bytes(raw2)).rstrip(b"=").decode("ascii")
        with pytest.raises(tw.VaultError):
            tw.detokenize(k, broken)


def test_same_plaintext_yields_different_tokens(vault_env):
    t1 = tw.encrypt_field("manual", "same-value")
    t2 = tw.encrypt_field("manual", "same-value")
    assert t1 != t2
    assert tw.decrypt_field("manual", t1) == "same-value"
    assert tw.decrypt_field("manual", t2) == "same-value"


def test_corrupt_token_empties_field_not_config(vault_env):
    """A tampered token blanks only its own field; the server entry survives."""
    out = tw.unseal_dict("manual", {"host": "h", "password": "tws3.bogus!!!"}, tw.SECRET_FIELDS)
    assert out["host"] == "h"
    assert out["password"] == ""


def test_legacy_tokens_raise_vault_error(vault_env):
    with pytest.raises(tw.VaultError, match="legacy_format"):
        tw.decrypt_field("manual", "tws2.some_old_token")
    with pytest.raises(tw.VaultError, match="legacy_format"):
        tw.decrypt_field("manual", "__obfuscated__legacy_value")
    with pytest.raises(tw.VaultError, match="legacy_format"):
        tw.decrypt_field("manual", "__broken")


def test_legacy_tokens_unseal_logs_warning_and_empties_field(vault_env, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        out = tw.unseal_dict(
            "manual",
            {
                "host": "h",
                "password": "tws2.legacy_pass",
                "key": "__obfuscated__legacy_key",
                "uuid": "normal_plaintext_uuid",
            },
            tw.SECRET_FIELDS,
        )

    assert out["host"] == "h"
    assert out["password"] == ""
    assert out["key"] == ""
    assert out["uuid"] == "normal_plaintext_uuid"

    warning_texts = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("field password: legacy_format (set to empty)" in text for text in warning_texts)
    assert any("field key: legacy_format (set to empty)" in text for text in warning_texts)
    assert not any("uuid" in text for text in warning_texts)


def test_subscription_seal_unseal(vault_env, tmp_path):
    from utils import subscription_store as sbm
    import unittest.mock as mock
    with mock.patch.object(sbm, "get_config_dir", return_value=tmp_path):
        subs = [{
            "name": "Sub A",
            "url": "https://example.com/sub1",
            "servers": [
                {"key": "ss://raw1", "password": "pw-1", "uuid": "u1", "public_key": "pk1",
                 "name": "s1", "host": "h1", "port": 8388, "method": "aes-256-gcm"},
                {"key": "", "password": "", "uuid": "", "public_key": "",
                 "name": "s2", "host": "h2", "port": 8389,
                 "method": "chacha20-ietf-poly1305"},
            ],
            "traffic": {"used": 1, "total": 100, "expire": 0},
        }]
        sbm.save_subscriptions(subs)
        raw = json.loads((tmp_path / "subscriptions.json").read_text(encoding="utf-8"))
        assert raw[0]["url"].startswith("tws3.")
        assert raw[0]["servers"][0]["password"].startswith("tws3.")
        assert raw[0]["servers"][0]["key"].startswith("tws3.")
        assert raw[0]["servers"][1]["password"] == ""
        assert raw[0]["name"] == "Sub A"
        assert raw[0]["servers"][0]["host"] == "h1"
        loaded = sbm.load_subscriptions()
        assert loaded[0]["url"] == "https://example.com/sub1"
        assert loaded[0]["servers"][0]["password"] == "pw-1"
        assert loaded[0]["servers"][0]["uuid"] == "u1"
        assert loaded[0]["servers"][0]["public_key"] == "pk1"
        assert loaded[0]["servers"][0]["key"] == "ss://raw1"
        assert loaded[0]["name"] == "Sub A"


def test_export_import_roundtrip(vault_env, monkeypatch, tmp_path):
    from utils import server_manager as sm
    from utils import subscription_store as sbm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(sbm, "get_config_dir", lambda: tmp_path)
    mgr = sm.ServerManager()
    srv = mgr.add_from_link(_ss_link("example.com"))
    assert srv is not None
    assert mgr.manual_servers[0].password == "password"
    subs = [{
        "name": "Sub",
        "url": "https://example.com/s",
        "servers": [{"key": "k1", "password": "pw-sub", "uuid": "", "public_key": "",
                     "name": "s1", "host": "h", "port": 1, "method": "m"}],
        "traffic": None,
    }]
    payload = tw.export_payload(mgr.manual_servers, subs)
    assert payload["schema"] == "socksicle-export"
    assert "note" in payload
    assert payload["manual_servers"][0]["password"] == "password"
    assert payload["subscriptions"][0]["servers"][0]["password"] == "pw-sub"
    manuals, subs2 = tw.import_payload(payload)
    assert len(manuals) == 1
    assert manuals[0].password == "password"
    assert manuals[0].host == "example.com"
    assert subs2[0]["url"] == "https://example.com/s"
    assert subs2[0]["servers"][0].password == "pw-sub"



def test_server_manager_never_renames_user_files(vault_env, monkeypatch, tmp_path):
    """save/load must never retire the user config to *.foreign-* and must
    never create drawer.json — the config belongs to the user."""
    from utils import server_manager as sm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)
    mgr = sm.ServerManager()
    mgr.add_from_link(_ss_link("one.example"))
    assert len(mgr.manual_servers) == 1
    mgr.save_manual_servers()
    mgr2 = sm.ServerManager()
    assert len(mgr2.manual_servers) == 1
    assert mgr2.manual_servers[0].password == "password"
    assert mgr2.manual_servers[0].host == "one.example"
    names = [p.name for p in tmp_path.iterdir()]
    assert not any(".foreign-" in n for n in names)
    assert "drawer.json" not in names


def test_empty_secret_fields_not_tokenized(vault_env, monkeypatch, tmp_path):
    from utils import server_manager as sm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)
    mgr = sm.ServerManager()
    srv = mgr.add_from_link(_ss_link("example.com"))
    srv.password = ""
    mgr.save_manual_servers()
    raw = json.loads((tmp_path / "servers.json").read_text(encoding="utf-8"))
    assert raw[0]["password"] == ""
    assert raw[0]["key"].startswith("tws3.")
    tw._reset()
    mgr2 = sm.ServerManager()
    assert mgr2.manual_servers[0].password == ""
    assert mgr2.manual_servers[0].key.startswith("ss://")



def test_tws3_share_key_auto_generated_for_new_user(vault_env, monkeypatch, tmp_path):
    from utils import server_manager as sm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)
    mgr = sm.ServerManager()
    key = mgr.settings.get("tws3_share_key", "")
    assert key
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw.get("tws3_share_key") == key


def test_save_settings_preserves_share_key(vault_env, monkeypatch, tmp_path):
    from utils import server_manager as sm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)
    mgr = sm.ServerManager()
    key = mgr.settings["tws3_share_key"]
    mgr.save_settings()
    assert mgr.settings.get("tws3_share_key") == key
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw.get("tws3_share_key") == key


def test_share_roundtrip_server_link(vault_env):
    key = "test-share-key"
    link = _ss_link("share.example")
    tok = tw.encrypt_share(key, link)
    assert tok.startswith("tws3://")
    assert not tok.startswith("tws3://tws3.")
    assert "ss://" not in tok
    raw = tw._b64url_decode(tok[len("tws3://"):])
    assert raw[0] == 0x03  # v3
    assert tw.decrypt_share(key, tok) == link
    assert tw.decrypt_share(key, tok[len("tws3://"):]) == link


def test_share_bare_token_and_reject_double_prefix(vault_env):
    key = "test-share-key"
    link = _ss_link("legacy.example")
    clean_tok = tw.encrypt_share(key, link)
    b64_part = clean_tok[len("tws3://"):]

    # Bare token tws3.<b64url>
    bare_tok = "tws3." + b64_part
    assert tw.decrypt_share(key, bare_tok) == link

    # Transitional double prefix tws3://tws3.<b64url> is rejected
    legacy_tok = "tws3://tws3." + b64_part
    with pytest.raises(tw.VaultError):
        tw.decrypt_share(key, legacy_tok)


def test_share_default_key_and_fallback(vault_env):
    link = _ss_link("public.example")

    # 1. Encrypt with default key (empty key argument)
    tok = tw.encrypt_share("", link)
    assert tok.startswith("tws3://")

    # Decrypt with empty key -> uses DEFAULT_SHARE_KEY
    assert tw.decrypt_share("", tok) == link

    # Decrypt with mismatched personal key -> automatic fallback to DEFAULT_SHARE_KEY
    assert tw.decrypt_share("my-personal-share-key", tok) == link

    # 2. Encrypt with specific personal key
    personal_tok = tw.encrypt_share("personal-key-123", link)
    assert tw.decrypt_share("personal-key-123", personal_tok) == link

    # Decrypting personal link with completely wrong key fails
    with pytest.raises(tw.VaultError):
        tw.decrypt_share("completely-wrong-key-456", personal_tok)


def test_share_metadata_and_payload_roundtrip(vault_env):
    key = "meta-key"
    link = _ss_link("meta.example")
    expires = 1893456000  # Year 2030

    tok = tw.encrypt_share(key, link, lock_export=True, expires_at=expires)
    assert tok.startswith("tws3://")

    # Standard decrypt_share returns pure target link
    assert tw.decrypt_share(key, tok) == link

    # decrypt_share_payload returns target link and metadata dict
    target, meta = tw.decrypt_share_payload(key, tok)
    assert target == link
    assert meta["lock_export"] is True
    assert meta["expires_at"] == expires

    # Test link without metadata returns defaults
    tok_plain = tw.encrypt_share(key, link)
    target_plain, meta_plain = tw.decrypt_share_payload(key, tok_plain)
    assert target_plain == link
    assert meta_plain["lock_export"] is False
    assert meta_plain["expires_at"] is None


def test_server_model_lock_export_and_expiration():
    import time
    from utils.server_model import Server

    # Active server
    s_active = Server(name="Active", expires_at=int(time.time()) + 3600, lock_export=True)
    assert s_active.is_expired is False
    assert s_active.lock_export is True

    # Expired server
    s_expired = Server(name="Expired", expires_at=int(time.time()) - 10, lock_export=False)
    assert s_expired.is_expired is True

    # No expiration
    s_lifetime = Server(name="Lifetime", expires_at=None)
    assert s_lifetime.is_expired is False

    # Serialization roundtrip
    d = s_active.to_dict()
    assert d["lock_export"] is True
    assert d["expires_at"] == s_active.expires_at

    s_restored = Server.from_dict(d)
    assert s_restored.name == "Active"
    assert s_restored.lock_export is True
    assert s_restored.expires_at == s_active.expires_at
    assert s_restored.is_expired is False


def test_export_payload_excludes_locked_servers_and_subs():
    from utils.server_model import Server

    s_normal = Server(name="Normal Srv", host="normal.example")
    s_locked = Server(name="Locked Srv", host="locked.example", lock_export=True)

    sub_normal = {
        "name": "Normal Sub",
        "url": "https://example.com/sub",
        "servers": [s_normal, s_locked],
        "lock_export": False
    }
    sub_locked = {
        "name": "Locked Sub",
        "url": "https://example.com/sub_locked",
        "servers": [s_normal],
        "lock_export": True
    }

    payload = tw.export_payload([s_normal, s_locked], [sub_normal, sub_locked])

    # Manual servers: only s_normal exported
    assert len(payload["manual_servers"]) == 1
    assert payload["manual_servers"][0]["name"] == "Normal Srv"

    # Subscriptions: only sub_normal exported, and its inner servers exclude s_locked
    assert len(payload["subscriptions"]) == 1
    assert payload["subscriptions"][0]["name"] == "Normal Sub"
    assert len(payload["subscriptions"][0]["servers"]) == 1
    assert payload["subscriptions"][0]["servers"][0]["name"] == "Normal Srv"


def test_share_roundtrip_subscription_url(vault_env):
    key = "sub-share-key-2026"
    url = "https://example.com/sub?token=abc123"
    tok = tw.encrypt_share(key, url)
    assert tok.startswith("tws3://")
    assert tw.decrypt_share(key, tok) == url


def test_share_b64url_key(vault_env):
    key = base64.urlsafe_b64encode(b"D" * 32).rstrip(b"=").decode("ascii")
    tok = tw.encrypt_share(key, "vless://uuid@host:443")
    assert tw.decrypt_share(key, tok) == "vless://uuid@host:443"


def test_share_flip_byte_raises(vault_env):
    key = "flip-key"
    tok = tw.encrypt_share(key, _ss_link("flip.example"))
    payload = tok[len("tws3://"):]
    raw = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    for flip_at in (1, 10, len(raw) - 1):
        raw2 = bytearray(raw)
        raw2[flip_at] ^= 0x01
        broken = "tws3://" + base64.urlsafe_b64encode(bytes(raw2)).rstrip(b"=").decode("ascii")
        with pytest.raises(tw.VaultError):
            tw.decrypt_share(key, broken)


def test_share_bundle_multiple_servers(vault_env):
    key = "bundle-test-key"
    links = [
        "ss://YWVzLTI1Ni1nY206cGFzc3dvcmRAMTI3LjAuMC4xOjgzODg=#Node%201",
        "vless://uuid-123@example.com:443?security=reality&type=grpc#Node%202",
        "hysteria2://user:pass@hy2.example.com:443#Node%203"
    ]
    tok = tw.encrypt_share(key, links, lock_export=True, expires_at=1893456000)
    assert tok.startswith("tws3://")

    target, metadata = tw.decrypt_share_payload(key, tok)
    assert isinstance(target, list)
    assert len(target) == 3
    assert target == links
    assert metadata["lock_export"] is True
    assert metadata["expires_at"] == 1893456000


def test_share_wrong_key_raises(vault_env):
    link = _ss_link("wrong-key.example")
    tok = tw.encrypt_share("key-a", link)
    with pytest.raises(tw.VaultError):
        tw.decrypt_share("key-b", tok)


def test_share_empty_string(vault_env):
    assert tw.encrypt_share("any-key", "") == ""
    assert tw.decrypt_share("any-key", "") == ""


def test_share_passthrough_non_token(vault_env):
    key = "some-key"
    assert tw.decrypt_share(key, "ss://plain@h:8388#x") == "ss://plain@h:8388#x"
    assert tw.decrypt_share(key, "https://example.com/sub") == "https://example.com/sub"
    assert tw.decrypt_share(key, "random-text") == "random-text"


def test_add_server_dialog_hints_tws3(qapp):
    from utils.theme import M3Theme
    from ui.add_server_dialog import AddServerDialog

    dlg = AddServerDialog(theme=M3Theme(preset_key="lavender"))
    dlg.input_field.setText("tws3://some_token")
    assert "TwinSock Share" in dlg.hint_label.text()


def test_main_window_rejects_unsupported_link(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    from PySide6.QtWidgets import QDialog, QMessageBox
    import utils.subscription_store as sub_store

    sub_store._last_fetch_errors["https://foreign.example.com/sub"] = "Foreign 502"

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warned.append(text))
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    win = RoundedWindow()
    win.settings["tws3_share_key"] = "pure-v3-share-key"

    # Attempt to paste invalid link
    monkeypatch.setattr("ui.main_window.AddServerDialog.exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr("ui.main_window.AddServerDialog.get_server_key", lambda self: "invalid://link")

    win.show_add_dialog()
    from utils.i18n import tr
    assert any(tr("dlg.bad_link_text") in w for w in warned)
    assert not any("Foreign 502" in w for w in warned)
    assert len(win.server_manager.manual_servers) == 0


def test_main_window_add_failed_subscription_url(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    from PySide6.QtWidgets import QDialog, QMessageBox
    import utils.subscription_store as sub_store
    from utils.i18n import tr

    target_url = "https://failing-subscription.org/sub"
    sub_store._last_fetch_errors[target_url] = "Connection reset by peer"

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warned.append((title, text)))
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    win = RoundedWindow()
    monkeypatch.setattr("ui.main_window.AddServerDialog.exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr("ui.main_window.AddServerDialog.get_server_key", lambda self: target_url)
    monkeypatch.setattr(win.subscription_manager, "add", lambda *args, **kwargs: False)

    win.show_add_dialog()
    assert len(warned) == 1
    title, text = warned[0]
    assert title == tr("dlg.load_failed_title")
    assert "Connection reset by peer" in text


def test_main_window_add_tws3_server_link(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    from PySide6.QtWidgets import QDialog

    share_key = "test-share-key"
    ss = _ss_link("imported-server.example")
    tws3_link = tw.encrypt_share(share_key, ss)

    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    win = RoundedWindow()
    win.settings["tws3_share_key"] = share_key

    monkeypatch.setattr("ui.main_window.AddServerDialog.exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr("ui.main_window.AddServerDialog.get_server_key", lambda self: tws3_link)

    win.show_add_dialog()
    assert any(s.host == "imported-server.example" for s in win.server_manager.manual_servers)


def test_main_window_tws3_with_metadata(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    from PySide6.QtWidgets import QDialog, QMessageBox

    share_key = "test-share-key"
    ss = _ss_link("metadata-server.example")
    expires = 1999999999
    tws3_link = tw.encrypt_share(share_key, ss, lock_export=True, expires_at=expires)

    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    win = RoundedWindow()
    win.settings["tws3_share_key"] = share_key

    monkeypatch.setattr("ui.main_window.AddServerDialog.exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr("ui.main_window.AddServerDialog.get_server_key", lambda self: tws3_link)

    win.show_add_dialog()
    srv = next(s for s in win.server_manager.manual_servers if s.host == "metadata-server.example")
    assert srv.lock_export is True
    assert srv.expires_at == expires
    assert srv.is_expired is False

    # Check UI server items have QR button hidden
    assert len(win.server_panel._server_items) == 1
    item = win.server_panel._server_items[0]
    assert item.share_button.isHidden() is True
    assert item.expired_badge.isHidden() is True


def test_main_window_expired_server_blocks_connection(qapp, monkeypatch, tmp_path):
    from ui.main_window import RoundedWindow
    from PySide6.QtWidgets import QDialog, QMessageBox

    warned = []
    informed = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warned.append((title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: informed.append((title, text)))
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)

    # Expired link (timestamp in past)
    expired_ts = 1000000000
    tws3_expired_link = tw.encrypt_share("", _ss_link("expired.example"), lock_export=False, expires_at=expired_ts)

    win = RoundedWindow()
    monkeypatch.setattr(win, "_ensure_backend", lambda: True)
    monkeypatch.setattr(win.server_panel, "get_selected_index", lambda: 0)
    monkeypatch.setattr("ui.main_window.AddServerDialog.exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr("ui.main_window.AddServerDialog.get_server_key", lambda self: tws3_expired_link)

    win.show_add_dialog()
    from utils.i18n import tr
    assert any(tr("dlg.expired_text") in text for title, text in informed)
    assert len(win.server_manager.manual_servers) == 1
    srv = win.server_manager.manual_servers[0]
    assert srv.is_expired is True

    # UI item shows [EXPIRED] badge
    assert len(win.server_panel._server_items) == 1
    item = win.server_panel._server_items[0]
    assert item.expired_badge.isHidden() is False

    # Try connecting to expired server
    item.radio.setChecked(True)
    win.toggle_connection(True)

    assert any(tr("dlg.server_expired_title") in title for title, text in warned)
    assert win.connection_manager.is_connected is False
    assert win.screen_main.hero_btn.is_connected() is False
