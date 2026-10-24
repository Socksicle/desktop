import pytest
from utils.killswitch import KillSwitchManager, _resolve_ip


def test_killswitch_basics_and_admin_check(monkeypatch):
    # Resolve IP
    assert _resolve_ip("1.1.1.1") == "1.1.1.1"
    assert _resolve_ip("127.0.0.1") == "127.0.0.1"
    assert _resolve_ip("") is None

    # Singleton
    ks1 = KillSwitchManager.get_instance()
    ks2 = KillSwitchManager.get_instance()
    assert ks1 is ks2

    # Non-admin rejected
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: False)
    ks = KillSwitchManager()
    assert ks.enable("1.2.3.4", 443) is False
    assert ks._active is False
    assert ks.last_error


def test_clean_stale_linux_without_root_warns(monkeypatch, caplog):
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: False)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: False)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: True)
    executed = []
    monkeypatch.setattr("utils.killswitch.subprocess.run",
                        lambda cmd, **kwargs: executed.append(cmd))

    ks = KillSwitchManager()
    with caplog.at_level("WARNING", logger="killswitch"):
        assert ks.clean_stale_rules() is False
    assert not executed
    assert "without root" in caplog.text


def test_read_windows_policy(monkeypatch):
    ks = KillSwitchManager()

    class Result:
        returncode = 0
        stderr = ""
        stdout = "Domain=Allow\nPrivate=Block\nPublic=NotConfigured\n"

    # Valid profiles
    monkeypatch.setattr(ks, "_run_powershell", lambda script: Result())
    assert ks._read_windows_policy() == {
        "Domain": "Allow",
        "Private": "Block",
        "Public": "NotConfigured",
    }

    # Partial output rejected
    class PartialResult:
        returncode = 0
        stderr = ""
        stdout = "Domain=Allow\n"

    monkeypatch.setattr(ks, "_run_powershell", lambda script: PartialResult())
    assert ks._read_windows_policy() is None


def test_killswitch_cleanup_and_encoding_safety(monkeypatch):
    import subprocess
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    deleted_rules = []

    def fake_run_netsh(args):
        if "delete" in args:
            deleted_rules.append(args)
        class DummyProc:
            returncode = 0
            stderr = ""
        return DummyProc()

    ks = KillSwitchManager()
    monkeypatch.setattr(ks, "_run_netsh", fake_run_netsh)
    ks.clean_stale_rules()
    assert len(deleted_rules) >= 5

    # Netsh encoding safety with replace
    def fake_subprocess_run(cmd, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"
        return subprocess.CompletedProcess(cmd, 0, stdout="Удалено правил: 1", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    ks2 = KillSwitchManager()
    res = ks2._run_netsh(["advfirewall", "firewall", "show", "rule", "name=all"])
    assert res.returncode == 0
    assert "Удалено" in res.stdout


class _NetshResult:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stderr = "" if returncode == 0 else "netsh failed"
        self.stdout = ""


def _windows_killswitch(monkeypatch, rule_result, executed, policy):
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: False)
    monkeypatch.setattr("utils.killswitch._resolve_ips", lambda host: ["1.2.3.4"])
    monkeypatch.setattr(
        KillSwitchManager, "_read_windows_policy", lambda self: dict(policy))
    monkeypatch.setattr(
        KillSwitchManager, "_windows_firewall_enabled", lambda self: True)

    def set_policy(self, profiles):
        policy.update(profiles)
        return True

    monkeypatch.setattr(KillSwitchManager, "_set_windows_policy", set_policy)

    def fake_run(self, args):
        executed.append(args)
        return _NetshResult(rule_result(args))

    monkeypatch.setattr(KillSwitchManager, "_run_netsh", fake_run)
    return KillSwitchManager()


def test_killswitch_windows_lifecycle_and_multinode(monkeypatch, tmp_path):
    executed_cmds = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(monkeypatch, lambda args: 0, executed_cmds, policy)

    # Enable
    assert ks.enable("1.2.3.4", 443) is True
    assert ks._active is True
    assert policy == {"Domain": "Block", "Private": "Block", "Public": "Block"}
    assert any("Socksicle_KS_AllowLoopback" in " ".join(cmd) for cmd in executed_cmds)
    assert any("Socksicle_KS_AllowLAN" in " ".join(cmd) for cmd in executed_cmds)
    assert any("Socksicle_KS_AllowServer" in " ".join(cmd) for cmd in executed_cmds)
    assert not any("BlockOut" in " ".join(cmd) and "add" in cmd for cmd in executed_cmds)

    # Disable restores policy and removes state
    assert ks.disable() is True
    assert ks._active is False
    assert policy == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert not ks._state_path().exists()

    # Hot switch keeps firewall rules when engine is allowed
    engine = tmp_path / "sing-box.exe"
    engine.write_bytes(b"binary")
    executed_hot = []
    policy_hot = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks_hot = _windows_killswitch(monkeypatch, lambda args: 0, executed_hot, policy_hot)
    assert ks_hot.enable("1.2.3.4", 443, engine_path=str(engine)) is True
    calls_after_enable = list(executed_hot)
    assert ks_hot.enable("5.6.7.8", 443, engine_path=str(engine)) is True
    assert executed_hot == calls_after_enable
    assert policy_hot["Domain"] == "Block"

    # Restores policy after crash/restart
    fresh = KillSwitchManager()
    assert fresh.clean_stale_rules() is True
    assert policy_hot == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert not fresh._state_path().exists()

    # Allows every resolved address (IPv4 and IPv6)
    executed_multi = []
    policy_multi = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks_multi = _windows_killswitch(monkeypatch, lambda args: 0, executed_multi, policy_multi)
    monkeypatch.setattr("utils.killswitch._resolve_ips", lambda host: ["1.2.3.4", "2001:db8::1"])
    assert ks_multi.enable("example.com", 443) is True
    server_cmd = next(cmd for cmd in executed_multi if "AllowServer" in " ".join(cmd) and "remoteip" in " ".join(cmd))
    assert "1.2.3.4" in " ".join(server_cmd)
    assert "2001:db8::1" in " ".join(server_cmd)


def test_killswitch_windows_failure_safeties(monkeypatch):
    from utils.i18n import tr

    # 1. Unresolvable server host does not block
    executed_unres = []
    class DummyProc:
        returncode = 0
        stderr = ""
        stdout = ""
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: False)
    monkeypatch.setattr("utils.killswitch._resolve_ips", lambda host: [])
    monkeypatch.setattr(KillSwitchManager, "_run_netsh", lambda self, args: (executed_unres.append(args), DummyProc())[1])
    ks1 = KillSwitchManager()
    assert ks1.enable("unresolvable.invalid", 443) is False
    assert not any("BlockOut" in " ".join(cmd) for cmd in executed_unres)
    assert "unresolvable.invalid" in ks1.last_error

    # 2. Allow rule failure never blocks traffic
    executed_fail = []
    policy_fail = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks2 = _windows_killswitch(monkeypatch, lambda args: 1 if "AllowServer" in " ".join(args) else 0, executed_fail, policy_fail)
    assert ks2.enable("example.com", 443) is False
    assert policy_fail == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert "AllowServer" in ks2.last_error

    # 3. Policy change failure does not block
    executed_pol = []
    policy_pol = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks3 = _windows_killswitch(monkeypatch, lambda args: 0, executed_pol, policy_pol)
    monkeypatch.setattr(KillSwitchManager, "_set_windows_policy", lambda self, p: False)
    assert ks3.enable("example.com", 443) is False
    assert policy_pol == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert ks3.last_error == tr("killswitch.reason_policy")
    assert not any("BlockOut" in " ".join(cmd) and "add" in cmd for cmd in executed_pol)

    # 4. Aborts when firewall is disabled
    policy_dis = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks4 = _windows_killswitch(monkeypatch, lambda args: 0, [], policy_dis)
    monkeypatch.setattr(KillSwitchManager, "_windows_firewall_enabled", lambda self: False)
    assert ks4.enable("example.com", 443) is False
    assert ks4.last_error == tr("killswitch.reason_firewall")
    assert policy_dis == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}


def test_killswitch_linux_nftables_lifecycle_and_rollback(monkeypatch):
    import subprocess

    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: False)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: True)

    # 1. Successful batch covers IPv4 and IPv6
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input", "")))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ks = KillSwitchManager()
    assert ks.enable("2001:db8::1", 443) is True
    scripts = [text for cmd, text in calls if cmd[:2] == ["nft", "-f"]]
    assert scripts, "nft batch was not executed"
    script = scripts[-1]
    assert 'oifname "sb_tun0" accept' in script
    assert "socksicle-tun" not in script
    assert "ip6 daddr { 2001:db8::1 } accept" in script
    assert "ip daddr { 10.0.0.0/8" in script
    assert "policy drop" in script

    # 2. Failure deletes table
    deletes = []
    def fake_run_fail(cmd, **kwargs):
        if cmd[:2] == ["nft", "-f"]:
            return subprocess.CompletedProcess(cmd, 1, "", "syntax error")
        if cmd[:2] == ["nft", "delete"]:
            deletes.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run_fail)
    ks_fail = KillSwitchManager()
    assert ks_fail.enable("1.2.3.4", 443) is False
    assert ks_fail.last_error
    assert deletes, "partial nftables table was not removed"


def test_clean_stale_windows_without_admin(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: False)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: False)

    state_file = tmp_path / "killswitch_state.json"
    ks = KillSwitchManager()
    monkeypatch.setattr(ks, "_state_path", lambda: state_file)

    elevate_calls = []
    monkeypatch.setattr("utils.killswitch.elevate_restart",
                        lambda: (elevate_calls.append(True), False)[1])

    # 1. No stale state: clean_stale_rules succeeds without elevation
    assert ks.clean_stale_rules() is True
    assert not elevate_calls

    # 2. Stale state exists: elevation is attempted
    state_file.write_text('{"profiles": {"Domain": "Allow"}}', encoding="utf-8")
    with caplog.at_level("WARNING", logger="killswitch"):
        # elevate_restart returns False -> clean_stale_rules must return False, not True!
        assert ks.clean_stale_rules() is False
    assert len(elevate_calls) == 1
    assert "start Socksicle as Administrator" in caplog.text
    assert ks.last_error

    # 3. Successful elevation returns True
    monkeypatch.setattr("utils.killswitch.elevate_restart",
                        lambda: (elevate_calls.append(True), True)[1])
    assert ks.clean_stale_rules() is True
    assert len(elevate_calls) == 2

    # 4. elevate=False avoids calling elevate_restart
    elevate_calls.clear()
    assert ks.clean_stale_rules(elevate=False) is False
    assert not elevate_calls

