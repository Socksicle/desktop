"""Manual server list and app settings persistence, plus profile import/export."""
import base64
import json
import logging
import os
import secrets
import tempfile

from .platform_utils import get_config_dir
from .i18n import tr
from .server_model import Server
from .subscription_store import save_subscriptions
from .engines.base import DEFAULT_LOCAL_PORT
from .ping import DEFAULT_PING_METHOD
from . import twinsock

log = logging.getLogger(__name__)




class ServerManager:
    def __init__(self):
        self.config_dir = str(get_config_dir())
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_file = os.path.join(self.config_dir, "servers.json")
        self.settings_file = os.path.join(self.config_dir, "settings.json")
        self.manual_servers = self.load_manual_servers()
        self.settings = self.load_settings()
        self._ensure_tws3_share_key()
        self._ensure_installation_id()

    def load_manual_servers(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    servers = []
                    for s in data:
                        if isinstance(s, dict):
                            servers.append(
                                twinsock.unseal_dict("manual", s, twinsock.SECRET_FIELDS))
                    return [Server.from_dict(s) for s in servers]
            except (json.JSONDecodeError, OSError, ValueError) as e:
                log.error("Failed to load manual servers: %s", e)
        return []

    def save_manual_servers(self):
        payload = [
            twinsock.seal_dict("manual", s.to_dict(), twinsock.SECRET_FIELDS)
            for s in self.manual_servers
        ]
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", dir=self.config_dir, delete=False,
                    encoding="utf-8", suffix=".tmp") as f:
                tmp_name = f.name
                json.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.config_file)
        except OSError as e:
            log.error("Failed to save manual servers: %s", e)
        finally:
            if tmp_name:
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass

    def load_settings(self):
        settings = None
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding="utf-8", errors="replace") as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, OSError, ValueError) as e:
                log.error("Failed to load settings: %s", e)
        if not isinstance(settings, dict):
            settings = {"local_port": DEFAULT_LOCAL_PORT, "auto_connect": False,
                        "minimize_to_tray": True,
                        "notifications_enabled": True,
                        "notify_connecting": True,
                        "notify_connected": True,
                        "notify_disconnected": True,
                        "notify_connection_error": True,
                        "notify_sub_updated": True,
                        "notify_sub_error": True,
                        "notify_killswitch": True,
                        "notify_tray_minimized": True,
                        "auto_update_subs": True, "auto_update_interval": 2.0,
                        "user_agent_key": "socksicle",
                        "fake_hwid": False, "hwid_value": "",
                        "engine": "auto", "ping_method": DEFAULT_PING_METHOD,
                        "tun_mode": False, "theme_preset": "dynamic", "language": "ru",
                        "bypass_lan": True, "selected_server_key": None,
                        "show_active_server_card": False, "show_ip_card": False,
                        "show_flags": False}

        return settings

    def get_share_key(self) -> str:
        return self.settings.get("tws3_share_key", "")

    def _ensure_tws3_share_key(self):
        if self.settings.get("tws3_share_key"):
            return
        self.settings["tws3_share_key"] = base64.urlsafe_b64encode(
            secrets.token_bytes(32)).rstrip(b"=").decode()
        self.save_settings()
        log.info("generated TwinSock v3 share key")

    def _ensure_installation_id(self):
        if self.settings.get("installation_id"):
            return
        self.settings["installation_id"] = secrets.token_hex(16)
        self.save_settings()
        log.info("generated stable installation id")

    def save_settings(self):
        if not self.settings.get("tws3_share_key"):
            self.settings["tws3_share_key"] = base64.urlsafe_b64encode(
                secrets.token_bytes(32)).rstrip(b"=").decode()
            log.info("generated TwinSock v3 share key")
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", dir=self.config_dir, delete=False,
                    encoding="utf-8", suffix=".tmp") as f:
                tmp_name = f.name
                json.dump(self.settings, f)
            os.replace(tmp_name, self.settings_file)
        except OSError as e:
            log.error("Failed to save settings: %s", e)
        finally:
            if tmp_name:
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass

    def add_from_link(self, raw_link, default_name=None, lock_export: bool = False, expires_at: int | None = None):
        """Parse an ss://, vless://, vmess://, hysteria2://, or hy2:// link and append it to the manual server list."""
        server = Server.from_link(raw_link, default_name=default_name or tr("server.new_name"))
        if not server:
            return None
        if lock_export:
            server.lock_export = True
        if expires_at is not None:
            server.expires_at = expires_at
        self.manual_servers.append(server)
        self.save_manual_servers()
        return server

    def delete_manual(self, index):
        if index < 0 or index >= len(self.manual_servers):
            return
        del self.manual_servers[index]
        self.save_manual_servers()

    def export_profiles(self, path, subscriptions):
        data = twinsock.export_payload(self.manual_servers, subscriptions)
        with open(path, 'w', encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def import_profiles(self, path, subscriptions):
        """Merge profiles from a JSON file. Returns (added_servers, added_subs)."""
        with open(path, 'r', encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return 0, 0
        manuals, subs = twinsock.import_payload(data)
        added_m = 0
        for srv in manuals:
            if srv not in self.manual_servers:
                self.manual_servers.append(srv)
                added_m += 1
        if added_m:
            self.save_manual_servers()
        added_s = 0
        for raw_sub in subs:
            if not any(x['url'] == raw_sub['url'] for x in subscriptions):
                subscriptions.append(raw_sub)
                added_s += 1
        if added_s:
            save_subscriptions(subscriptions)
        return added_m, added_s