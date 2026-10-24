"""Tests for the portable TwinSock v3 vault (utils/twinsock.py) and its integration."""
import base64
import json
import logging
from unittest import mock
import pytest

import utils.twinsock as tw


@pytest.fixture(autouse=True)
def vault_env():
    tw._reset()
    yield
    tw._reset()


def _ss_link(host):
    userinfo = base64.urlsafe_b64encode(b"aes-256-gcm:password").decode().rstrip("=")
    return f"ss://{userinfo}@{host}:8388#ServerOne"


def test_vault_sealing_properties(vault_env, tmp_path):
    # Roundtrip secret fields for manual & subscriptions
    values = ["simple", "unicode key ✓", "x" * 300, "with spaces  and\ttabs", "1234567890!@#$%^&*()"]
    for purpose in ("manual", "subscriptions"):
        fields = tw.SECRET_FIELDS + (("url",) if purpose == "subscriptions" else ())
        for field in fields:
            for val in values:
                tok = tw.encrypt_field(purpose, val)
                assert tok.startswith("tws3.")
                assert tok != val
                assert tw.decrypt_field(purpose, tok) == val

    # Token layout & v3 version byte
    tok = tw.encrypt_field("manual", "test-secret")
    raw = tw._b64url_decode(tok[5:])
    assert raw[0] == 0x03
    assert len(raw) == 1 + 12 + 16 + len("test-secret")

    # Vault portability across machines (no machine binding)
    tok_port = tw.encrypt_field("manual", "keep-me-safe")
    tw._reset()
    assert tw.decrypt_field("manual", tok_port) == "keep-me-safe"

    # No drawer file created
    tw.seal_dict("manual", {"password": "pw", "host": "h"}, tw.SECRET_FIELDS)
    assert not (tmp_path / "drawer.json").exists()
    assert list(tmp_path.iterdir()) == []

    # Empty string handling
    assert tw.encrypt_field("manual", "") == ""
    assert tw.decrypt_field("manual", "") == ""
    assert tw.tokenize(b"k" * 32, "") == ""
    assert tw.detokenize(b"k" * 32, "") == ""

    # Random IV yields different tokens for identical plaintext
    t1 = tw.encrypt_field("manual", "same-value")
    t2 = tw.encrypt_field("manual", "same-value")
    assert t1 != t2
    assert tw.decrypt_field("manual", t1) == "same-value"
    assert tw.decrypt_field("manual", t2) == "same-value"


def test_vault_tamper_and_legacy_handling(vault_env, caplog):
    # Flipped byte raises VaultError
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

    # Corrupt token empties field without corrupting surrounding entry
    out = tw.unseal_dict("manual", {"host": "h", "password": "tws3.bogus!!!"}, tw.SECRET_FIELDS)
    assert out["host"] == "h"
    assert out["password"] == ""

    # Legacy tokens raise VaultError on decrypt_field
    for leg in ("tws2.some_old_token", "__obfuscated__legacy_value", "__broken"):
        with pytest.raises(tw.VaultError, match="legacy_format"):
            tw.decrypt_field("manual", leg)

    # Legacy tokens log warning and empty only the affected field in unseal_dict
    with caplog.at_level(logging.WARNING):
        out_leg = tw.unseal_dict(
            "manual",
            {
                "host": "h",
                "password": "tws2.legacy_pass",
                "key": "__obfuscated__legacy_key",
                "uuid": "normal_plaintext_uuid",
            },
            tw.SECRET_FIELDS,
        )
    assert out_leg["host"] == "h"
    assert out_leg["password"] == ""
    assert out_leg["key"] == ""
    assert out_leg["uuid"] == "normal_plaintext_uuid"
    warning_texts = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("field password: legacy_format (set to empty)" in text for text in warning_texts)
    assert any("field key: legacy_format (set to empty)" in text for text in warning_texts)


def test_subscription_and_secret_fields_sealing(vault_env, tmp_path):
    from utils import subscription_store as sbm
    from utils import server_manager as sm
    import unittest.mock as mock

    # Subscription store seal / unseal roundtrip
    with mock.patch.object(sbm, "get_config_dir", return_value=tmp_path):
        subs = [{
            "name": "Sub A",
            "url": "https://example.com/sub1",
            "servers": [
                {"key": "ss://raw1", "password": "pw-1", "uuid": "u1", "public_key": "pk1",
                 "name": "s1", "host": "h1", "port": 8388, "method": "aes-256-gcm"},
                {"key": "", "password": "", "uuid": "", "public_key": "",
                 "name": "s2", "host": "h2", "port": 8389, "method": "chacha20-ietf-poly1305"},
            ],
            "traffic": {"used": 1, "total": 100, "expire": 0},
        }]
        sbm.save_subscriptions(subs)
        raw = json.loads((tmp_path / "subscriptions.json").read_text(encoding="utf-8"))
        assert raw[0]["url"].startswith("tws3.")
        assert raw[0]["servers"][0]["password"].startswith("tws3.")
        assert raw[0]["servers"][0]["key"].startswith("tws3.")
        assert raw[0]["servers"][1]["password"] == ""

        loaded = sbm.load_subscriptions()
        assert loaded[0]["url"] == "https://example.com/sub1"
        assert loaded[0]["servers"][0]["password"] == "pw-1"
        assert loaded[0]["servers"][0]["uuid"] == "u1"
        assert loaded[0]["servers"][0]["public_key"] == "pk1"
        assert loaded[0]["servers"][0]["key"] == "ss://raw1"

    # Empty secret fields are not tokenized in server_manager
    with mock.patch.object(sm, "get_config_dir", return_value=tmp_path):
        mgr = sm.ServerManager()
        srv = mgr.add_from_link(_ss_link("example.com"))
        srv.password = ""
        mgr.save_manual_servers()
        raw_srv = json.loads((tmp_path / "servers.json").read_text(encoding="utf-8"))
        assert raw_srv[0]["password"] == ""
        assert raw_srv[0]["key"].startswith("tws3.")

        tw._reset()
        mgr2 = sm.ServerManager()
        assert mgr2.manual_servers[0].password == ""
        assert mgr2.manual_servers[0].key.startswith("ss://")


def test_export_import_and_file_safety(vault_env, monkeypatch, tmp_path):
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

    # Server manager never renames user files or creates drawer.json
    mgr.save_manual_servers()
    mgr2 = sm.ServerManager()
    assert len(mgr2.manual_servers) == 1
    assert mgr2.manual_servers[0].password == "password"
    names = [p.name for p in tmp_path.iterdir()]
    assert not any(".foreign-" in n for n in names)
    assert "drawer.json" not in names


def test_share_key_generation_and_persistence(vault_env, monkeypatch, tmp_path):
    from utils import server_manager as sm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)

    mgr = sm.ServerManager()
    key = mgr.settings.get("tws3_share_key", "")
    assert key
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw.get("tws3_share_key") == key

    # Preserves share key across saves
    mgr.save_settings()
    assert mgr.settings.get("tws3_share_key") == key
    raw2 = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw2.get("tws3_share_key") == key


def test_save_settings_is_atomic_and_handles_oserror(vault_env, monkeypatch, tmp_path):
    from utils import server_manager as sm
    monkeypatch.setattr(sm, "get_config_dir", lambda: tmp_path)

    mgr = sm.ServerManager()
    path = tmp_path / "settings.json"
    original = path.read_text(encoding="utf-8")

    # A failed replace must keep the previous file intact and leave no temp.
    mgr.settings["local_port"] = 2080
    with mock.patch.object(sm.os, "replace", side_effect=OSError("disk full")):
        mgr.save_settings()
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []

    # A failed temp-file creation must not escape as an exception.
    with mock.patch.object(sm.tempfile, "NamedTemporaryFile",
                           side_effect=OSError("no space")):
        mgr.save_settings()
    assert path.read_text(encoding="utf-8") == original

    # The next successful save still replaces the file and persists settings.
    mgr.save_settings()
    assert json.loads(path.read_text(encoding="utf-8"))["local_port"] == 2080
    assert list(tmp_path.glob("*.tmp")) == []


def test_share_token_roundtrips_and_bundles(vault_env):
    key = "test-share-key"
    link = _ss_link("share.example")

    # Server link
    tok = tw.encrypt_share(key, link)
    assert tok.startswith("tws3://")
    assert not tok.startswith("tws3://tws3.")
    assert "ss://" not in tok
    raw = tw._b64url_decode(tok[len("tws3://"):])
    assert raw[0] == 0x03
    assert tw.decrypt_share(key, tok) == link
    assert tw.decrypt_share(key, tok[len("tws3://"):]) == link

    # Subscription URL
    url = "https://example.com/sub?token=abc123"
    tok_sub = tw.encrypt_share("sub-share-key-2026", url)
    assert tok_sub.startswith("tws3://")
    assert tw.decrypt_share("sub-share-key-2026", tok_sub) == url

    # Base64url key
    b64_key = base64.urlsafe_b64encode(b"D" * 32).rstrip(b"=").decode("ascii")
    tok_b64 = tw.encrypt_share(b64_key, "vless://uuid@host:443")
    assert tw.decrypt_share(b64_key, tok_b64) == "vless://uuid@host:443"

    # Default share key and fallback
    pub_link = _ss_link("public.example")
    tok_pub = tw.encrypt_share("", pub_link)
    assert tok_pub.startswith("tws3://")
    assert tw.decrypt_share("", tok_pub) == pub_link
    assert tw.decrypt_share("my-personal-share-key", tok_pub) == pub_link

    # Metadata and payload roundtrip
    expires = 1893456000
    tok_meta = tw.encrypt_share(key, link, lock_export=True, expires_at=expires)
    assert tw.decrypt_share(key, tok_meta) == link
    target, meta = tw.decrypt_share_payload(key, tok_meta)
    assert target == link
    assert meta["lock_export"] is True
    assert meta["expires_at"] == expires

    # Non-numeric expiry in an untrusted payload is dropped, not compared
    for bad_expiry in ("soon", {"at": 1}, True):
        token_bad = tw.encrypt_share(key, link, lock_export=True, expires_at=bad_expiry)
        _, meta_bad = tw.decrypt_share_payload(key, token_bad)
        assert meta_bad["expires_at"] is None

    # Plain without metadata returns defaults
    t_plain, m_plain = tw.decrypt_share_payload(key, tok)
    assert t_plain == link
    assert m_plain["lock_export"] is False
    assert m_plain["expires_at"] is None

    # Multi-server bundle
    links = [
        "ss://YWVzLTI1Ni1nY206cGFzc3dvcmRAMTI3LjAuMC4xOjgzODg=#Node%201",
        "vless://uuid-123@example.com:443?security=reality&type=grpc#Node%202",
        "hysteria2://user:pass@hy2.example.com:443#Node%203",
    ]
    tok_bundle = tw.encrypt_share(key, links, lock_export=True, expires_at=1893456000)
    bundle_target, bundle_meta = tw.decrypt_share_payload(key, tok_bundle)
    assert isinstance(bundle_target, list)
    assert len(bundle_target) == 3
    assert bundle_target == links
    assert bundle_meta["lock_export"] is True
    assert bundle_meta["expires_at"] == 1893456000


def test_share_token_edge_cases_and_tampering(vault_env):
    key = "test-share-key"
    link = _ss_link("legacy.example")
    clean_tok = tw.encrypt_share(key, link)
    b64_part = clean_tok[len("tws3://"):]

    # Bare token tws3.<b64url> accepts
    bare_tok = "tws3." + b64_part
    assert tw.decrypt_share(key, bare_tok) == link

    # Double prefix tws3://tws3.<b64url> is rejected
    with pytest.raises(tw.VaultError):
        tw.decrypt_share(key, "tws3://tws3." + b64_part)

    # Flip byte raises VaultError
    raw = bytearray(base64.urlsafe_b64decode(b64_part + "=" * (-len(b64_part) % 4)))
    for flip_at in (1, 10, len(raw) - 1):
        raw2 = bytearray(raw)
        raw2[flip_at] ^= 0x01
        broken = "tws3://" + base64.urlsafe_b64encode(bytes(raw2)).rstrip(b"=").decode("ascii")
        with pytest.raises(tw.VaultError):
            tw.decrypt_share(key, broken)

    # Wrong key raises
    with pytest.raises(tw.VaultError):
        tw.decrypt_share("wrong-key", clean_tok)

    # Empty string
    assert tw.encrypt_share("any-key", "") == ""
    assert tw.decrypt_share("any-key", "") == ""

    # Passthrough non-token
    assert tw.decrypt_share(key, "ss://plain@h:8388#x") == "ss://plain@h:8388#x"
    assert tw.decrypt_share(key, "https://example.com/sub") == "https://example.com/sub"
    assert tw.decrypt_share(key, "random-text") == "random-text"


def test_server_lock_export_rules():
    import time
    from utils.server_model import Server

    # Model lock_export and expiration properties
    s_active = Server(name="Active", expires_at=int(time.time()) + 3600, lock_export=True)
    assert s_active.is_expired is False
    assert s_active.lock_export is True

    s_expired = Server(name="Expired", expires_at=int(time.time()) - 10, lock_export=False)
    assert s_expired.is_expired is True

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

    # Export payload excludes locked servers and locked subs
    s_normal = Server(name="Normal Srv", host="normal.example")
    s_locked = Server(name="Locked Srv", host="locked.example", lock_export=True)

    sub_normal = {"name": "Normal Sub", "url": "https://example.com/sub", "servers": [s_normal, s_locked], "lock_export": False}
    sub_locked = {"name": "Locked Sub", "url": "https://example.com/sub_locked", "servers": [s_normal], "lock_export": True}

    payload = tw.export_payload([s_normal, s_locked], [sub_normal, sub_locked])
    assert len(payload["manual_servers"]) == 1
    assert payload["manual_servers"][0]["name"] == "Normal Srv"
    assert len(payload["subscriptions"]) == 1
    assert payload["subscriptions"][0]["name"] == "Normal Sub"
    assert len(payload["subscriptions"][0]["servers"]) == 1
    assert payload["subscriptions"][0]["servers"][0]["name"] == "Normal Srv"


def test_add_server_dialog_hints_tws3(qapp):
    from utils.theme import M3Theme
    from ui.add_server_dialog import AddServerDialog

    dlg = AddServerDialog(theme=M3Theme(preset_key="lavender"))
    dlg.input_field.setText("tws3://some_token")
    assert "TwinSock Share" in dlg.hint_label.text()
