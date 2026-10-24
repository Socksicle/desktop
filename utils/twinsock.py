"""TwinSock v3 — local obfuscation layer for secret fields in JSON configs."""
import base64
import binascii
import hashlib
import json
import logging
import os
import secrets
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger(__name__)

TOKEN_VERSION_CURRENT = 0x03
VERSION = "twscore-v0.7-t1"

TOKEN_PREFIX_CURRENT = "tws3."
TOKEN_PREFIXES = (TOKEN_PREFIX_CURRENT,)

SHARE_SCHEME_CURRENT = "tws3://"
SHARE_SCHEMES = (SHARE_SCHEME_CURRENT,)

SECRET_FIELDS = ("key", "password", "uuid", "public_key", "obfs_password")
_EAR_FIELDS = SECRET_FIELDS + ("url",)

_VAULT_APP_SECRET = b"socksicle::tws::vault::portable-v1"

SALT_VAULT = hashlib.sha256(b"socksicle::tws::v3::salt::vault").digest()
SALT_SHARE = hashlib.sha256(b"socksicle::tws::v3::salt::share").digest()

_lock = threading.Lock()


class VaultError(Exception):
    pass


def _derive_key_v3(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 key derivation (RFC 5869)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
        backend=None,
    ).derive(ikm)


def _field_key(purpose: str) -> bytes:
    return _derive_key_v3(
        _VAULT_APP_SECRET,
        salt=SALT_VAULT,
        info=b"twinsock-v3-vault::" + purpose.encode("utf-8"),
    )


def _share_derived_key(key: str) -> bytes:
    try:
        d = _b64url_decode(key)
    except (binascii.Error, ValueError):
        d = key.encode("utf-8")
    return _derive_key_v3(d, salt=SALT_SHARE, info=b"twinsock-v3-share")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _strip_token_prefix(token: str) -> str:
    for prefix in TOKEN_PREFIXES:
        if token.startswith(prefix):
            return token[len(prefix):]
    return token


def tokenize(k: bytes, plain: str, nonce: bytes | None = None) -> str:
    """Encrypt plaintext using AES-256-GCM (NIST SP 800-38D, 12B nonce + 16B tag)."""
    if not plain:
        return ""
    nonce = nonce or secrets.token_bytes(12)
    pt = plain.encode("utf-8")
    aesgcm = AESGCM(k)
    ct_with_tag = aesgcm.encrypt(nonce, pt, None)
    return TOKEN_PREFIX_CURRENT + _b64url(bytes([TOKEN_VERSION_CURRENT]) + nonce + ct_with_tag)


def detokenize(k: bytes, token: str) -> str:
    """Decrypt token using AES-256-GCM."""
    if not token:
        return token
    if not any(token.startswith(p) for p in TOKEN_PREFIXES):
        return token
    payload = _strip_token_prefix(token)
    try:
        raw = _b64url_decode(payload)
    except (binascii.Error, ValueError):
        raise VaultError("integrity")
    if not raw:
        raise VaultError("integrity")
    ver = raw[0]
    if ver == TOKEN_VERSION_CURRENT:
        if len(raw) < 1 + 12 + 16:  # 1 byte ver + 12 byte nonce + 16 byte tag
            raise VaultError("integrity")
        nonce = raw[1:13]
        ct_with_tag = raw[13:]
        try:
            pt = AESGCM(k).decrypt(nonce, ct_with_tag, None)
            return pt.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            raise VaultError("integrity")
    else:
        raise VaultError(f"unsupported_version:{ver}")


DEFAULT_SHARE_KEY = "abcdfg"


def _encrypt_share_bytes(k: bytes, plain: str) -> str:
    nonce = secrets.token_bytes(12)
    pt = plain.encode("utf-8")
    aesgcm = AESGCM(k)
    ct_with_tag = aesgcm.encrypt(nonce, pt, None)
    return SHARE_SCHEME_CURRENT + _b64url(bytes([TOKEN_VERSION_CURRENT]) + nonce + ct_with_tag)


def encrypt_share(key: str = "", plaintext: str | list = "", lock_export: bool = False, expires_at: int | None = None) -> str:
    """Encrypt a share link or list of links into a clean tws3://<base64url> token.

    Supports bundling multiple servers, permissions (lock_export), and expiration timestamp (expires_at).
    If key is empty or not specified, uses DEFAULT_SHARE_KEY.
    """
    if not plaintext:
        return ""
    if not key:
        key = DEFAULT_SHARE_KEY

    if isinstance(plaintext, list):
        payload_dict = {
            "targets": [str(x) for x in plaintext if str(x).strip()],
            "lock_export": bool(lock_export),
            "expires_at": expires_at,
        }
        pt = json.dumps(payload_dict, ensure_ascii=False)
    elif lock_export or expires_at is not None:
        payload_dict = {
            "target": plaintext,
            "lock_export": bool(lock_export),
            "expires_at": expires_at,
        }
        pt = json.dumps(payload_dict, ensure_ascii=False)
    else:
        pt = str(plaintext)

    return _encrypt_share_bytes(_share_derived_key(key), pt)


def _extract_share_payload_bytes(token: str) -> tuple[int, bytes, bytes, bytes] | None:
    """Parse share token into (version, raw_bytes, nonce, ct_with_tag) or None."""
    if not token:
        return None
    t = token.strip()
    for scheme in SHARE_SCHEMES:
        if t.startswith(scheme):
            t = t[len(scheme):]
            break
    else:
        for prefix in TOKEN_PREFIXES:
            if t.startswith(prefix):
                t = t[len(prefix):]
                break
    try:
        raw = _b64url_decode(t)
    except (binascii.Error, ValueError):
        return None
    if not raw or raw[0] != TOKEN_VERSION_CURRENT or len(raw) < 1 + 12 + 16:
        return None
    return raw[0], raw, raw[1:13], raw[13:]


def decrypt_share_payload(key: str, token: str) -> tuple[str | list, dict]:
    """Decrypt a tws3:// share token back to (target_link_or_list, metadata_dict).

    Supports single links, multi-server arrays, clean format ('tws3://<b64url>'),
    and bare tokens.
    """
    if not token:
        return token, {"lock_export": False, "expires_at": None}

    parsed = _extract_share_payload_bytes(token)
    if parsed is None:
        if any(token.startswith(s) for s in SHARE_SCHEMES) or any(token.startswith(p) for p in TOKEN_PREFIXES):
            raise VaultError("TwinSock share verification failed (tampered token)")
        return token, {"lock_export": False, "expires_at": None}

    ver, raw, nonce, ct_with_tag = parsed

    keys_to_try = []
    if key and key.strip():
        keys_to_try.append(key.strip())
    if DEFAULT_SHARE_KEY not in keys_to_try:
        keys_to_try.append(DEFAULT_SHARE_KEY)

    last_error = None
    decrypted_text = None

    for candidate_key in keys_to_try:
        k = _share_derived_key(candidate_key)
        try:
            pt = AESGCM(k).decrypt(nonce, ct_with_tag, None)
            decrypted_text = pt.decode("utf-8")
            break
        except (InvalidTag, UnicodeDecodeError, ValueError) as e:
            last_error = e
            continue

    if decrypted_text is None:
        raise VaultError("TwinSock share verification failed (wrong key or tampered token)")

    target_link = decrypted_text
    metadata = {"lock_export": False, "expires_at": None}
    try:
        data = json.loads(decrypted_text)
        if isinstance(data, dict):
            if "targets" in data and isinstance(data["targets"], list):
                target_link = data["targets"]
            elif "servers" in data and isinstance(data["servers"], list):
                target_link = data["servers"]
            elif "target" in data:
                target_link = data["target"]
            metadata["lock_export"] = bool(data.get("lock_export", False))
            expires_at = data.get("expires_at")
            # Untrusted share payload: a non-numeric expiry would crash the
            # import slot when compared with the current time.
            if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
                expires_at = None
            metadata["expires_at"] = expires_at
            for k, v in data.items():
                if k not in metadata and k not in ("target", "targets", "servers"):
                    metadata[k] = v
        elif isinstance(data, list):
            target_link = data
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return target_link, metadata


def decrypt_share(key: str, token: str) -> str:
    """Decrypt a tws3:// share token back to the original link.

    Accepts clean format ('tws3://<b64url>') and bare tokens.
    Returns target link string for backward compatibility.
    """
    target, _ = decrypt_share_payload(key, token)
    return target


def encrypt_field(purpose: str, plaintext: str) -> str:
    with _lock:
        return tokenize(_field_key(purpose), plaintext)


def decrypt_field(purpose: str, token: str) -> str:
    with _lock:
        if not token:
            return ""
        if any(token.startswith(p) for p in TOKEN_PREFIXES):
            return detokenize(_field_key(purpose), token)
        if token.startswith(("tws2.", "__")):
            raise VaultError("legacy_format")
        return token


def seal_dict(purpose: str, d: dict, fields: tuple | list) -> dict:
    out = dict(d)
    for field in fields:
        if out.get(field):
            out[field] = encrypt_field(purpose, str(out[field]))
    return out


def unseal_dict(purpose: str, d: dict, fields: tuple | list) -> dict:
    out = dict(d)
    for field in fields:
        value = out.get(field)
        if isinstance(value, str) and value:
            try:
                out[field] = decrypt_field(purpose, value)
            except VaultError as e:
                log.warning("vault: field %s: %s (set to empty)", field, e)
                out[field] = ""
    return out


def export_payload(manual_servers, subscriptions):
    def is_locked(item):
        if hasattr(item, "lock_export"):
            return bool(item.lock_export)
        if isinstance(item, dict):
            return bool(item.get("lock_export", False))
        return False

    def server_dict(s):
        return s.to_dict() if hasattr(s, "to_dict") else dict(s)

    exported_manuals = [
        server_dict(s) for s in manual_servers
        if not is_locked(s)
    ]
    exported_subs = []
    for sub in subscriptions:
        if is_locked(sub):
            continue
        sub_dict = dict(sub)
        sub_servers = [
            server_dict(s) for s in sub_dict.get("servers", [])
            if not is_locked(s)
        ]
        exported_subs.append({**sub_dict, "servers": sub_servers})

    return {
        "schema": "socksicle-export",
        "note": "Secret fields (passwords, links) in this file are stored in plain text — "
                "do not share or store it unnecessarily.",
        "manual_servers": exported_manuals,
        "subscriptions": exported_subs,
    }


def import_payload(data):
    from .server_model import Server
    data = dict(data or {})
    manuals = []
    for raw in data.get("manual_servers", []):
        if not isinstance(raw, dict):
            continue
        try:
            srv = Server.from_dict(raw)
        except (TypeError, ValueError, VaultError):
            continue
        if srv:
            manuals.append(srv)
    subs = []
    for raw in data.get("subscriptions", []):
        if not isinstance(raw, dict):
            continue
        servers = []
        for s in raw.get("servers", []):
            if isinstance(s, dict):
                try:
                    srv = Server.from_dict(s)
                except (TypeError, ValueError, VaultError):
                    continue
                if srv:
                    servers.append(srv)
        raw["servers"] = servers
        subs.append(raw)
    return manuals, subs


def _reset():
    pass
