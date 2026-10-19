"""Unit tests for utils/linux_tun.py (sing-box 1.14 Linux TUN layer)."""
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from utils import linux_tun


def _make_getcap_output(caps):
    def fake_run(cmd, *a, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"{cmd[-1]}: {caps}\n", stderr="")
    return fake_run


class TestCapabilities:
    def test_all_required_caps_pass(self, monkeypatch):
        monkeypatch.setattr(linux_tun.subprocess, "run",
                            _make_getcap_output(
                                "cap_net_admin=ep cap_net_raw=ep "
                                "cap_net_bind_service=ep"))
        assert linux_tun.has_tun_capabilities("/usr/bin/sing-box") is True

    def test_missing_cap_fails(self, monkeypatch):
        monkeypatch.setattr(linux_tun.subprocess, "run",
                            _make_getcap_output(
                                "cap_net_admin=ep cap_net_bind_service=ep"))
        assert linux_tun.has_tun_capabilities("/usr/bin/sing-box") is False

    def test_missing_binary_rejected(self):
        assert linux_tun.has_tun_capabilities("/no/such/path") is False
        assert not linux_tun.grant_tun_capabilities("/no/such/path")

    def test_grant_calls_pkexec_setcap(self, monkeypatch):
        calls = []

        def fake_run(cmd, *a, **kwargs):
            calls.append(cmd)
            name = os.path.basename(cmd[0])
            if name == "getcap":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(linux_tun.subprocess, "run", fake_run)
        monkeypatch.setattr(linux_tun, "_which",
                            lambda name: f"/usr/bin/{name}")
        assert linux_tun.grant_tun_capabilities("/usr/bin/sing-box") is True
        grant = [c for c in calls if os.path.basename(c[0]) == "pkexec"][0]
        assert os.path.basename(grant[1]) == "setcap"
        assert grant[2] == "cap_net_admin,cap_net_raw,cap_net_bind_service+eip"

    def test_grant_rejects_when_pkexec_fails(self, monkeypatch):
        def fake_run(cmd, *a, **kwargs):
            code = 1 if os.path.basename(cmd[0]) == "getcap" else 2
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")
        monkeypatch.setattr(linux_tun.subprocess, "run", fake_run)
        monkeypatch.setattr(linux_tun, "_which",
                            lambda name: f"/usr/bin/{name}")
        assert linux_tun.grant_tun_capabilities("/usr/bin/sing-box") is False


class TestPreMatchRules:
    def test_pre_match_rules_support_scoped_hijack(self):
        rules = linux_tun.build_pre_match_rules(dns_hijack_inbound="tun-in")
        assert rules[-1] == {"inbound": ["tun-in"],
                             "protocol": "dns", "action": "hijack-dns"}
        assert linux_tun.build_pre_match_rules()[-1] == {
            "protocol": "dns", "action": "hijack-dns"}


class TestBuildConfig:
    def _nodes(self):
        nodes = [
            {"type": "vless", "tag": "node-a", "server": "1.2.3.4",
             "server_port": 443},
            {"type": "vless", "tag": "node-b", "server": "5.6.7.8",
             "server_port": 443},
        ]
        return nodes, "node-b"

    def test_tun_fields_and_rule_order(self, tmp_path):
        nodes, active = self._nodes()
        config = linux_tun.build_config(nodes, active_tag=active,
                                        work_dir=tmp_path)
        inbound = config["inbounds"][0]
        assert inbound["type"] == "tun"
        assert inbound["dns_mode"] == "hijack"
        assert inbound["auto_redirect"] is True
        assert inbound["interface_name"] == linux_tun.TUN_INTERFACE_NAME

        actions = [r.get("action") for r in config["route"]["rules"]]
        assert actions.index("bypass") < actions.index("sniff")
        assert config["route"]["rules"][0] == {
            "ip_is_private": True, "action": "bypass"}

    def test_bypass_processes_rule_before_sniff(self, tmp_path):
        nodes, active = self._nodes()
        config = linux_tun.build_config(
            nodes, active_tag=active, work_dir=tmp_path,
            bypass_processes=["cs2", "steam"])
        rules = config["route"]["rules"]
        names = next(r for r in rules if "process_name" in r)
        assert names["process_name"] == ["cs2", "steam"]
        assert names["action"] == "bypass"
        assert rules.index(names) < [r.get("action") for r in rules].index("sniff")

    def test_selector_defaults_to_active_node(self, tmp_path):
        nodes, active = self._nodes()
        config = linux_tun.build_config(nodes, active_tag=active,
                                        work_dir=tmp_path)
        selector = config["outbounds"][0]
        assert selector["type"] == "selector"
        assert selector["default"] == active
        assert selector["interrupt_exist_connections"] is True

    def test_local_srs_sets_used_when_present(self, tmp_path):
        (tmp_path / "geosite-ru.srs").write_bytes(b"\x01\x02")
        nodes, active = self._nodes()
        config = linux_tun.build_config(nodes, active_tag=active,
                                        work_dir=tmp_path)
        sets = config["route"]["rule_set"]
        assert sets[0]["type"] == "local"
        assert sets[0]["format"] == "binary"
        assert all(Path(s["path"]).is_file() for s in sets)
        assert config["route"]["rules"][-1] == {
            "rule_set": ["geosite-ru"], "action": "route",
            "outbound": "direct"}

    def test_clash_api_block_when_credentials_given(self, tmp_path):
        nodes, active = self._nodes()
        config = linux_tun.build_config(nodes, active_tag=active,
                                        work_dir=tmp_path,
                                        api_port=19090, api_secret="tok")
        api = config["experimental"]["clash_api"]
        assert api["external_controller"] == "127.0.0.1:19090"
        assert api["secret"] == "tok"
        assert "experimental" not in linux_tun.build_config(
            nodes, active_tag=active, work_dir=tmp_path)

    def test_invalid_inputs_rejected(self, tmp_path):
        nodes, active = self._nodes()
        with pytest.raises(linux_tun.LinuxTunError):
            linux_tun.build_config([], active_tag=active, work_dir=tmp_path)
        with pytest.raises(linux_tun.LinuxTunError):
            linux_tun.build_config(nodes, active_tag="nope",
                                   work_dir=tmp_path)
        with pytest.raises(linux_tun.LinuxTunError):
            linux_tun.build_config(
                [{"type": "vless"}], active_tag="x", work_dir=tmp_path)


class TestWriteConfig:
    def test_owner_only_permissions(self, tmp_path):
        path = linux_tun.write_config({"a": 1}, tmp_path)
        assert path.name == "socksicle-linux-tun.json"
        assert json.loads(path.read_text()) == {"a": 1}
        assert (path.stat().st_mode & 0o077) == 0


class TestControlAPI:
    def _api(self):
        return linux_tun.SingBoxControlAPI(port=19090, secret="tok")

    def test_switch_node_sends_put(self):
        api = self._api()
        requests = []

        def fake_open(request, timeout):
            requests.append((request.full_url, request.get_method(),
                             request.get_header("Authorization"),
                             request.data))
            response = mock.Mock()
            response.status = 204
            response.__enter__ = mock.Mock(return_value=response)
            response.__exit__ = mock.Mock(return_value=False)
            return response

        opener = mock.Mock()
        opener.open.side_effect = fake_open
        with mock.patch.object(urllib.request, "build_opener",
                               return_value=opener) as build:
            assert api.switch_node("proxy-selector", "node-a") is True
        # The localhost controller must not be routed through env proxies.
        proxy_handler = build.call_args[0][0]
        assert proxy_handler.proxies == {}
        url, method, auth, payload = requests[0]
        assert method == "PUT"
        assert auth == "Bearer tok"
        assert json.loads(payload) == {"name": "node-a"}
        assert url == "http://127.0.0.1:19090/proxies/proxy-selector"

    def test_switch_node_failure_returns_false(self):
        api = self._api()
        opener = mock.Mock()
        opener.open.side_effect = OSError("refused")
        with mock.patch.object(urllib.request, "build_opener",
                               return_value=opener):
            assert api.switch_node("proxy-selector", "node-a") is False

    def test_delay_returns_minus_one_on_error(self):
        api = self._api()
        opener = mock.Mock()
        opener.open.side_effect = OSError("down")
        with mock.patch.object(urllib.request, "build_opener",
                               return_value=opener):
            assert api.get_node_delay("node-a") == -1


class TestDaemon:
    def _daemon(self, tmp_path):
        (tmp_path / "sing-box").write_text("")
        config = linux_tun.write_config({"log": {}}, tmp_path)
        return linux_tun.SingBoxDaemon("/usr/bin/sing-box", config, tmp_path)

    def test_rootless_start_uses_unshare(self, tmp_path):
        daemon = self._daemon(tmp_path)
        check = subprocess.CompletedProcess([], 0, "", "")
        popen = mock.Mock(return_value=mock.Mock(pid=123))
        with mock.patch.object(linux_tun.subprocess, "run",
                               return_value=check) as run, \
                mock.patch.object(linux_tun.subprocess, "Popen", popen), \
                mock.patch.object(linux_tun, "grant_tun_capabilities",
                                  side_effect=AssertionError(
                                      "caps must not be needed in rootless")):
            daemon.start(rootless=True)
        assert not any("setcap" in c.args[0]
                       for c in run.call_args_list if c.args)
        args = popen.call_args[0][0]
        assert os.path.basename(args[0]) == "unshare"
        assert args[1:5] == ["--user", "--map-root-user", "--net", "--"]
        assert args[5] == str(daemon.binary_path)

    def test_start_blocked_when_caps_not_granted(self, tmp_path):
        daemon = self._daemon(tmp_path)
        with mock.patch.object(linux_tun, "grant_tun_capabilities",
                               return_value=False), \
                mock.patch.object(linux_tun.subprocess, "Popen") as popen, \
                pytest.raises(PermissionError):
            daemon.start()
        popen.assert_not_called()

    def test_check_failure_raises_with_stderr(self, tmp_path):
        daemon = self._daemon(tmp_path)
        fail = subprocess.CompletedProcess([], 1, stdout="",
                                           stderr="bad field")
        with mock.patch.object(linux_tun, "grant_tun_capabilities",
                               return_value=True), \
                mock.patch.object(linux_tun.subprocess, "run",
                                  return_value=fail), \
                pytest.raises(linux_tun.LinuxTunError, match="bad field"):
            daemon.start()

    def test_stop_kills_after_terminate_timeout(self, tmp_path):
        daemon = self._daemon(tmp_path)
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("t", 3), None]
        daemon.process = proc
        daemon.stop()
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_stop_is_noop_when_not_running(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon.stop()
        assert daemon.process is None

    def test_namespace_entry_args_uses_pid(self, tmp_path):
        daemon = self._daemon(tmp_path)
        with pytest.raises(linux_tun.LinuxTunError):
            daemon.namespace_entry_args()
        daemon.process = mock.Mock(pid=42)
        assert daemon.namespace_entry_args() == [
            "nsenter", "-t", "42", "-U", "--preserve-credentials", "-n"]
