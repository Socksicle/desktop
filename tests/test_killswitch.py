import pytest
from utils.killswitch import KillSwitchManager, _resolve_ip


def test_resolve_ip():
    assert _resolve_ip("1.1.1.1") == "1.1.1.1"
    assert _resolve_ip("127.0.0.1") == "127.0.0.1"
    assert _resolve_ip("") is None


def test_killswitch_singleton():
    ks1 = KillSwitchManager.get_instance()
    ks2 = KillSwitchManager.get_instance()
    assert ks1 is ks2


def test_killswitch_non_admin(monkeypatch):
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: False)
    ks = KillSwitchManager()
    assert ks.enable("1.2.3.4", 443) is False
    assert ks._active is False
    assert ks.last_error


def test_killswitch_no_block_when_server_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: False)
    monkeypatch.setattr("utils.killswitch._resolve_ips", lambda host: [])

    executed = []

    class DummyProc:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(
        KillSwitchManager, "_run_netsh",
        lambda self, args: (executed.append(args), DummyProc())[1])

    ks = KillSwitchManager()
    assert ks.enable("unresolvable.invalid", 443) is False
    assert not any("BlockOut" in " ".join(cmd) for cmd in executed)
    assert "unresolvable.invalid" in ks.last_error


def test_killswitch_enable_disable_windows(monkeypatch):
    executed_cmds = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(monkeypatch, lambda args: 0, executed_cmds, policy)

    ok = ks.enable("1.2.3.4", 443)
    assert ok is True
    assert ks._active is True
    assert policy == {"Domain": "Block", "Private": "Block", "Public": "Block"}

    # Allow rules are added; the old explicit BlockOut is never created because
    # Windows block rules would override these allow rules.
    assert any("Socksicle_KS_AllowLoopback" in " ".join(cmd) for cmd in executed_cmds)
    assert any("Socksicle_KS_AllowLAN" in " ".join(cmd) for cmd in executed_cmds)
    assert any("Socksicle_KS_AllowServer" in " ".join(cmd) for cmd in executed_cmds)
    assert not any(
        "BlockOut" in " ".join(cmd) and "add" in cmd for cmd in executed_cmds)

    assert ks.disable() is True
    assert ks._active is False
    assert policy == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert not ks._state_path().exists()


def test_killswitch_hot_switch_keeps_rules(monkeypatch, tmp_path):
    """Switching servers must not tear down the firewall when the engine is allowed."""
    engine = tmp_path / "sing-box.exe"
    engine.write_bytes(b"binary")
    executed = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(monkeypatch, lambda args: 0, executed, policy)

    assert ks.enable("1.2.3.4", 443, engine_path=str(engine)) is True
    calls_after_enable = list(executed)

    assert ks.enable("5.6.7.8", 443, engine_path=str(engine)) is True
    assert executed == calls_after_enable
    assert policy["Domain"] == "Block"


def test_killswitch_restores_policy_after_restart(monkeypatch):
    """A crash leaves the state file; the next start must restore the policy."""
    executed_cmds = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(monkeypatch, lambda args: 0, executed_cmds, policy)
    assert ks.enable("1.2.3.4", 443) is True
    assert policy["Domain"] == "Block"

    fresh = KillSwitchManager()
    assert fresh.clean_stale_rules() is True
    assert policy == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert not fresh._state_path().exists()


def test_read_windows_policy_parses_profiles(monkeypatch):
    ks = KillSwitchManager()

    class Result:
        returncode = 0
        stderr = ""
        stdout = "Domain=Allow\nPrivate=Block\nPublic=NotConfigured\n"

    monkeypatch.setattr(ks, "_run_powershell", lambda script: Result())
    assert ks._read_windows_policy() == {
        "Domain": "Allow",
        "Private": "Block",
        "Public": "NotConfigured",
    }


def test_read_windows_policy_rejects_partial_output(monkeypatch):
    ks = KillSwitchManager()

    class Result:
        returncode = 0
        stderr = ""
        stdout = "Domain=Allow\n"

    monkeypatch.setattr(ks, "_run_powershell", lambda script: Result())
    assert ks._read_windows_policy() is None


def test_killswitch_emergency_cleanup(monkeypatch):
    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    deleted_rules = []

    def fake_run_netsh(self, args):
        if "delete" in args:
            deleted_rules.append(args)
        class DummyProc:
            returncode = 0
            stderr = ""
        return DummyProc()

    monkeypatch.setattr(KillSwitchManager, "_run_netsh", fake_run_netsh)

    ks = KillSwitchManager()
    ks.clean_stale_rules()
    assert len(deleted_rules) >= 5


def test_run_netsh_encoding_safety(monkeypatch):
    import subprocess
    ks = KillSwitchManager()

    # Emulate netsh output with cp866/Russian bytes that caused charmap UnicodeDecodeError
    def fake_subprocess_run(cmd, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"
        return subprocess.CompletedProcess(cmd, 0, stdout="Удалено правил: 1", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    res = ks._run_netsh(["advfirewall", "firewall", "show", "rule", "name=all"])
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


def test_killswitch_allow_rule_failure_never_blocks_traffic(monkeypatch):
    executed = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(
        monkeypatch,
        lambda args: 1 if "AllowServer" in " ".join(args) else 0,
        executed,
        policy)

    assert ks.enable("example.com", 443) is False
    assert policy == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert "AllowServer" in ks.last_error


def test_killswitch_policy_change_failure_does_not_block(monkeypatch):
    from utils.i18n import tr

    executed = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(monkeypatch, lambda args: 0, executed, policy)
    monkeypatch.setattr(KillSwitchManager, "_set_windows_policy", lambda self, p: False)

    assert ks.enable("example.com", 443) is False
    assert policy == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    assert ks.last_error == tr("killswitch.reason_policy")
    assert not any(
        "BlockOut" in " ".join(cmd) and "add" in cmd for cmd in executed)


def test_killswitch_aborts_when_firewall_is_disabled(monkeypatch):
    from utils.i18n import tr

    executed = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}
    ks = _windows_killswitch(monkeypatch, lambda args: 0, executed, policy)
    monkeypatch.setattr(
        KillSwitchManager, "_windows_firewall_enabled", lambda self: False)

    assert ks.enable("example.com", 443) is False
    assert ks.last_error == tr("killswitch.reason_firewall")
    assert policy == {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}


def test_killswitch_allows_every_resolved_address(monkeypatch):
    executed = []
    policy = {"Domain": "Allow", "Private": "Allow", "Public": "Allow"}

    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: False)
    monkeypatch.setattr(
        "utils.killswitch._resolve_ips",
        lambda host: ["1.2.3.4", "2001:db8::1"])
    monkeypatch.setattr(
        KillSwitchManager, "_read_windows_policy", lambda self: dict(policy))
    monkeypatch.setattr(
        KillSwitchManager, "_windows_firewall_enabled", lambda self: True)
    monkeypatch.setattr(
        KillSwitchManager, "_set_windows_policy",
        lambda self, profiles: policy.update(profiles) or True)

    def fake_run(self, args):
        executed.append(args)
        return _NetshResult(0)

    monkeypatch.setattr(KillSwitchManager, "_run_netsh", fake_run)
    ks = KillSwitchManager()

    assert ks.enable("example.com", 443) is True
    server_cmd = next(
        cmd for cmd in executed
        if "AllowServer" in " ".join(cmd) and "remoteip" in " ".join(cmd))
    assert "1.2.3.4" in " ".join(server_cmd)
    assert "2001:db8::1" in " ".join(server_cmd)


def test_killswitch_linux_batch_covers_ipv4_and_ipv6(monkeypatch):
    import subprocess

    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: False)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: True)

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
    assert "ip6 daddr { 2001:db8::1 } accept" in script
    assert "ip daddr { 10.0.0.0/8" in script
    assert "policy drop" in script


def test_killswitch_linux_failure_deletes_table(monkeypatch):
    import subprocess

    monkeypatch.setattr("utils.killswitch.is_admin", lambda: True)
    monkeypatch.setattr("utils.killswitch.is_windows", lambda: False)
    monkeypatch.setattr("utils.killswitch.is_linux", lambda: True)

    deletes = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["nft", "-f"]:
            return subprocess.CompletedProcess(cmd, 1, "", "syntax error")
        if cmd[:2] == ["nft", "delete"]:
            deletes.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ks = KillSwitchManager()

    assert ks.enable("1.2.3.4", 443) is False
    assert ks.last_error
    assert deletes, "partial nftables table was not removed"
