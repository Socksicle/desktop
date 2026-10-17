"""Subscription state: load/save, add/update/delete, traffic summary, auto-update."""
import logging
import threading
import time
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal, QTimer

from .i18n import tr
from .server_model import Server
from .subscription_store import (
    get_last_fetch_error, load_subscriptions, parse_subscription,
    save_subscriptions,
)

log = logging.getLogger(__name__)

AUTO_UPDATE_INTERVAL_MS = 5 * 60 * 1000


def _drop_untrusted_insecure(servers) -> int:
    """Clear TLS-verification opt-outs from untrusted subscription nodes.

    A subscription-provided ``insecure`` flag is not the user's explicit
    choice; the secure default is restored. Manual adds keep their flag.
    """
    dropped = 0
    for s in servers:
        if getattr(s, "insecure", False):
            s.insecure = False
            dropped += 1
    return dropped


class SubscriptionManager(QObject):
    updated = Signal(bool, int)  # success, new node count
    updateFailed = Signal(str, str, str)  # sub_name, sub_url, error_message

    def __init__(self, settings=None):
        super().__init__()
        self._lock = threading.Lock()
        self._settings = settings or {}
        self.subscriptions = load_subscriptions()
        for sub in self.subscriptions:
            sub['servers'] = [Server.from_dict(s) for s in sub.get('servers', [])]

        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._check_auto_update)
        self._auto_timer.start(AUTO_UPDATE_INTERVAL_MS)

    def _get_sub_settings(self):
        """Extract subscription-related settings for parse_subscription."""
        return {
            "user_agent_key": self._settings.get("user_agent_key", "socksicle"),
            "fake_hwid": self._settings.get("fake_hwid", False),
            "hwid_value": self._settings.get("hwid_value", ""),
        }

    def _serialize_unlocked(self):
        return [
            {
                **sub,
                "servers": [s.to_dict() for s in sub.get("servers", [])],
            }
            for sub in self.subscriptions
        ]

    def _serialized(self):
        with self._lock:
            return self._serialize_unlocked()

    def get(self, name):
        with self._lock:
            return next((s for s in self.subscriptions if s['name'] == name), None)

    def add(self, name, url, lock_export: bool = False, expires_at: int | None = None,
            use_remote_title: bool = False):
        """Fetch and store a new subscription. Returns True on success.

        When ``use_remote_title`` is set (app add-flow where ``name`` is just
        the URL hostname default), the provider ``Profile-Title`` is used as
        the group name when present - matching the Android client.
        """
        links, meta = parse_subscription(url, self._get_sub_settings())
        if not links:
            return False
        servers = []
        seen_keys = set()
        for link in links:
            s = Server.from_link(link)
            if s:
                if lock_export:
                    s.lock_export = True
                if expires_at is not None:
                    s.expires_at = expires_at
                dedup_key = s.key.strip() if s.key else s.unique_key
                if dedup_key not in seen_keys:
                    servers.append(s)
                    seen_keys.add(dedup_key)
        remote_title = (meta.get('profile_title') or '').strip()
        display_name = remote_title if (use_remote_title and remote_title) else name
        dropped_insecure = _drop_untrusted_insecure(servers)
        if dropped_insecure:
            log.info("Dropped insecure TLS flag from %d node(s) of subscription %s",
                     dropped_insecure, display_name)
        with self._lock:
            sub_dict = {
                "name": display_name,
                "url": url,
                "servers": servers,
                "traffic": meta.get('traffic'),
                "profile_title": meta.get('profile_title', ''),
                "support_url": meta.get('support_url', ''),
                "profile_web_page_url": meta.get('profile_web_page_url', ''),
                "announce": meta.get('announce', ''),
                "description": meta.get('description', ''),
                "profile_update_interval": meta.get('profile_update_interval', 0),
                "last_updated": time.time(),
            }
            if lock_export:
                sub_dict["lock_export"] = True
            if expires_at is not None:
                sub_dict["expires_at"] = expires_at
            existing_idx = next(
                (i for i, s in enumerate(self.subscriptions) if s.get("url") == url), -1)
            if existing_idx < 0:
                existing_idx = next(
                    (i for i, s in enumerate(self.subscriptions) if s.get("name") == display_name), -1)
            if existing_idx >= 0:
                old_sub = self.subscriptions[existing_idx]
                if "icon_emoji" in old_sub:
                    sub_dict["icon_emoji"] = old_sub["icon_emoji"]
                if "is_pinned" in old_sub:
                    sub_dict["is_pinned"] = old_sub["is_pinned"]
                if old_sub.get("is_customized"):
                    sub_dict["is_customized"] = True
                    sub_dict["name"] = old_sub.get("name", display_name)
                self.subscriptions[existing_idx] = sub_dict
            else:
                self.subscriptions.append(sub_dict)
            save_subscriptions(self._serialize_unlocked())
        return True

    def update(self, sub):
        """Refresh a subscription off the GUI thread; emits `updated` when done."""
        threading.Thread(target=self._update_worker, args=(sub,), daemon=True).start()

    def _update_worker(self, sub, emit_signal=True):
        """Refresh a subscription; emits `updated` only when emit_signal is True."""
        links, meta = parse_subscription(sub['url'], self._get_sub_settings())
        if not links:
            err_msg = meta.get('error') or get_last_fetch_error(sub.get('url')) or tr("sub.fetch_failed")
            self.updateFailed.emit(sub.get('name', 'Subscription'), sub.get('url', ''), err_msg)
            if emit_signal:
                self.updated.emit(False, 0)
            return

        old_keys = {s.key.strip() if s.key else s.unique_key for s in sub.get('servers', [])}
        new_servers = []
        seen_keys = set()
        new_count = 0

        sub_lock_export = bool(sub.get('lock_export', False))
        sub_expires_at = sub.get('expires_at')

        for link in links:
            s = Server.from_link(link)
            if s:
                if sub_lock_export:
                    s.lock_export = True
                if sub_expires_at is not None:
                    s.expires_at = sub_expires_at
                dedup_key = s.key.strip() if s.key else s.unique_key
                if dedup_key not in seen_keys:
                    new_servers.append(s)
                    seen_keys.add(dedup_key)
                    if dedup_key not in old_keys:
                        new_count += 1

        dropped_insecure = _drop_untrusted_insecure(new_servers)
        if dropped_insecure:
            log.info("Dropped insecure TLS flag from %d node(s) of subscription %s",
                     dropped_insecure, sub.get('name', 'Subscription'))

        with self._lock:
            sub['servers'] = new_servers

            # Android parity: latch is_customized for pre-flag manual renames.
            remote_title = (meta.get('profile_title') or '').strip()
            if remote_title and not sub.get('is_customized'):
                current = (sub.get('name') or '').strip()
                host = urlparse(sub.get('url') or '').hostname or ''
                prev_remote = (sub.get('profile_title') or '').strip()
                if current in ('', host) or current == prev_remote or current == remote_title:
                    sub['name'] = remote_title
                else:
                    sub['is_customized'] = True

            if meta.get('traffic'):
                sub['traffic'] = meta['traffic']
            for key in ('profile_title', 'support_url', 'profile_web_page_url',
                         'announce', 'description', 'profile_update_interval'):
                if key in meta:
                    sub[key] = meta[key]
            sub['last_updated'] = time.time()

            save_subscriptions(self._serialize_unlocked())
        if emit_signal:
            self.updated.emit(True, new_count)

    def delete(self, name):
        with self._lock:
            self.subscriptions = [s for s in self.subscriptions if s['name'] != name]
            save_subscriptions(self._serialize_unlocked())

    def rename(self, old_name: str, new_name: str, icon_emoji: str = "", url: str = ""):
        """Rename subscription, update its emoji and optional URL, and persist.

        Any manual save latches ``is_customized`` so later refreshes no longer
        overwrite the name with the provider title (Android parity).
        """
        with self._lock:
            sub = next((s for s in self.subscriptions if s['name'] == old_name), None)
            if sub is None:
                return False
            sub['name'] = new_name
            if icon_emoji:
                sub['icon_emoji'] = icon_emoji
            if url:
                sub['url'] = url
            sub['is_customized'] = True
            save_subscriptions(self._serialize_unlocked())
        return True

    def set_pinned(self, name: str, is_pinned: bool):
        """Toggle pinned status for subscription and persist."""
        with self._lock:
            sub = next((s for s in self.subscriptions if s['name'] == name), None)
            if sub is None:
                return False
            sub['is_pinned'] = bool(is_pinned)
            save_subscriptions(self._serialize_unlocked())
        return True



    def _check_auto_update(self):
        """Periodically check if any subscription needs updating."""
        now = time.time()
        due = []
        with self._lock:
            for sub in self.subscriptions:
                interval_hours = sub.get('profile_update_interval', 0)
                if interval_hours <= 0:
                    continue
                last = sub.get('last_updated', 0)
                elapsed_hours = (now - last) / 3600
                if elapsed_hours >= interval_hours:
                    due.append(sub)
        for sub in due:
            threading.Thread(
                target=self._update_worker, args=(sub,), daemon=True,
                kwargs={"emit_signal": False}
            ).start()

    def update_all(self):
        """Update all subscriptions (for app startup)."""
        with self._lock:
            subs = list(self.subscriptions)
        for sub in subs:
            threading.Thread(
                target=self._update_worker, args=(sub,), daemon=True,
                kwargs={"emit_signal": False}
            ).start()
