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
    def test_capabilities_check_and_missing(self, monkeypatch):
        monkeypatch.setattr(linux_tun, "_which", lambda name: f"/usr/bin/{name}")

        # All required caps
        monkeypatch.setattr(linux_tun.subprocess, "run",
                            _make_getcap_output(
                                "cap_net_admin=ep cap_net_raw=ep "
                                "cap_net_bind_service=ep"))
        assert linux_tun.has_tun_capabilities("/usr/bin/sing-box") is True

        # Missing cap fails
        monkeypatch.setattr(linux_tun.subprocess, "run",
                            _make_getcap_output(
                                "cap_net_admin=ep cap_net_bind_service=ep"))
        assert linux_tun.has_tun_capabilities("/usr/bin/sing-box") is False

        # Missing binary rejected
        assert linux_tun.has_tun_capabilities("/no/such/path") is False
        assert not linux_tun.grant_tun_capabilities("/no/such/path")

    def test_capabilities_grant(self, monkeypatch, tmp_path):
        binary = tmp_path / "sing-box"
        binary.write_text("")
        monkeypatch.setattr(linux_tun, "_which", lambda name: f"/usr/bin/{name}")

        # Success: calls pkexec setcap
        calls = []
        def fake_run(cmd, *a, **kwargs):
            calls.append(cmd)
            name = os.path.basename(cmd[0])
            if name == "getcap":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(linux_tun.subprocess, "run", fake_run)
        assert linux_tun.grant_tun_capabilities(binary) is True
        grant = [c for c in calls if os.path.basename(c[0]) == "pkexec"][0]
        assert os.path.basename(grant[1]) == "setcap"
        assert grant[2] == "cap_net_admin,cap_net_raw,cap_net_bind_service+eip"

        # Failure: pkexec fails
        def fake_run_fail(cmd, *a, **kwargs):
            code = 1 if os.path.basename(cmd[0]) == "getcap" else 2
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")
        monkeypatch.setattr(linux_tun.subprocess, "run", fake_run_fail)
        assert linux_tun.grant_tun_capabilities(binary) is False


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

    def test_build_config_rules_selectors_and_options(self, tmp_path):
        nodes, active = self._nodes()

        # 1. Base tun inbound and rule ordering
        config = linux_tun.build_config(nodes, active_tag=active, work_dir=tmp_path)
        inbound = config["inbounds"][0]
        assert inbound["type"] == "tun"
        assert inbound["dns_mode"] == "hijack"
        assert inbound["auto_redirect"] is True
        assert inbound["interface_name"] == linux_tun.TUN_INTERFACE_NAME

        actions = [r.get("action") for r in config["route"]["rules"]]
        assert actions.index("bypass") < actions.index("sniff")
        assert config["route"]["rules"][0] == {"ip_is_private": True, "action": "bypass"}

        selector = config["outbounds"][0]
        assert selector["type"] == "selector"
        assert selector["default"] == active
        assert selector["interrupt_exist_connections"] is True
        assert "experimental" not in config

        # 2. Bypass processes before sniff
        cfg_proc = linux_tun.build_config(
            nodes, active_tag=active, work_dir=tmp_path,
            bypass_processes=["cs2", "steam"])
        rules = cfg_proc["route"]["rules"]
        names = next(r for r in rules if "process_name" in r)
        assert names["process_name"] == ["cs2", "steam"]
        assert names["action"] == "bypass"
        assert rules.index(names) < [r.get("action") for r in rules].index("sniff")

        # 3. Local SRS rule sets
        (tmp_path / "geosite-ru.srs").write_bytes(b"\x01\x02")
        cfg_srs = linux_tun.build_config(nodes, active_tag=active, work_dir=tmp_path)
        sets = cfg_srs["route"]["rule_set"]
        assert sets[0]["type"] == "local"
        assert sets[0]["format"] == "binary"
        assert all(Path(s["path"]).is_file() for s in sets)
        assert cfg_srs["route"]["rules"][-1] == {
            "rule_set": ["geosite-ru"], "action": "route", "outbound": "direct"}

        # 4. Clash API credentials
        cfg_api = linux_tun.build_config(
            nodes, active_tag=active, work_dir=tmp_path,
            api_port=19090, api_secret="tok")
        api = cfg_api["experimental"]["clash_api"]
        assert api["external_controller"] == "127.0.0.1:19090"
        assert api["secret"] == "tok"

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
        for bad_port in ("1080", 1080.5, True, 0, 65536):
            with pytest.raises(linux_tun.LinuxTunError):
                linux_tun.build_config(nodes, active_tag=active,
                                       work_dir=tmp_path, local_port=bad_port)


class TestWriteConfig:
    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_owner_only_permissions(self, tmp_path):
        path = linux_tun.write_config({"a": 1}, tmp_path)
        assert path.name == "socksicle-linux-tun.json"
        assert json.loads(path.read_text()) == {"a": 1}
        assert (path.stat().st_mode & 0o077) == 0


class TestControlAPI:
    def _api(self):
        return linux_tun.SingBoxControlAPI(port=19090, secret="tok")

    def test_control_api_methods_and_errors(self):
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

        # Failure returns False
        opener_fail = mock.Mock()
        opener_fail.open.side_effect = OSError("refused")
        with mock.patch.object(urllib.request, "build_opener",
                               return_value=opener_fail):
            assert api.switch_node("proxy-selector", "node-a") is False

        # Delay error returns -1
        opener_down = mock.Mock()
        opener_down.open.side_effect = OSError("down")
        with mock.patch.object(urllib.request, "build_opener",
                               return_value=opener_down):
            assert api.get_node_delay("node-a") == -1


class TestDaemon:
    def _daemon(self, tmp_path):
        binary = tmp_path / "sing-box"
        binary.write_text("")
        config = linux_tun.write_config({"log": {}}, tmp_path)
        return linux_tun.SingBoxDaemon(binary, config, tmp_path)

    def test_daemon_start_modes_and_failures(self, tmp_path):
        daemon = self._daemon(tmp_path)
        check = subprocess.CompletedProcess([], 0, "", "")
        popen = mock.Mock(return_value=mock.Mock(pid=123))

        # Rootless start uses unshare
        with mock.patch.object(linux_tun.subprocess, "run",
                               return_value=check) as run, \
                mock.patch.object(linux_tun.subprocess, "Popen", popen), \
                mock.patch.object(linux_tun, "_which",
                                  side_effect=lambda name: f"/usr/bin/{name}"), \
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

        # Blocked when caps not granted
        with mock.patch.object(linux_tun, "grant_tun_capabilities",
                               return_value=False), \
                mock.patch.object(linux_tun.subprocess, "Popen") as popen_blocked, \
                pytest.raises(PermissionError):
            daemon.start()
        popen_blocked.assert_not_called()

        # Check failure raises with stderr
        fail = subprocess.CompletedProcess([], 1, stdout="",
                                           stderr="bad field")
        with mock.patch.object(linux_tun, "grant_tun_capabilities",
                               return_value=True), \
                mock.patch.object(linux_tun.subprocess, "run",
                                  return_value=fail), \
                pytest.raises(linux_tun.LinuxTunError, match="bad field"):
            daemon.start()

    def test_daemon_stop_and_namespace_args(self, tmp_path):
        daemon = self._daemon(tmp_path)

        # Stop kills after terminate timeout
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("t", 3), None]
        daemon.process = proc
        daemon.stop()
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

        # Stop is no-op when not running
        daemon2 = self._daemon(tmp_path)
        daemon2.stop()
        assert daemon2.process is None

        # Namespace entry args uses pid
        daemon3 = self._daemon(tmp_path)
        with pytest.raises(linux_tun.LinuxTunError):
            daemon3.namespace_entry_args()
        daemon3.process = mock.Mock(pid=42)
        assert daemon3.namespace_entry_args() == [
            "nsenter", "-t", "42", "-U", "--preserve-credentials", "-n"]
