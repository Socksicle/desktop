"""Stale wintun adapter detection, wait helpers, and the single auto-retry."""
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from utils.engines import proc_guard as pg
from utils.engines.singbox_engine import SingBoxEngine
from utils.connection_manager import ConnectionManager, CONNECTING, DISCONNECTED


USER_FATAL = ("FATAL[0015] start service: start inbound/tun[tun-in]: configure tun "
              "interface: (create adapter: Cannot create a file when that file already "
              "exists. | open existing adapter: Element not found.)")


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=["netsh"], returncode=returncode,
                                       stdout=stdout, stderr="")


def test_tun_conflict_hint_and_parsing(monkeypatch):
    # 1. Engine conflict hints
    eng = SingBoxEngine()
    hint = eng._extra_error_hint(USER_FATAL)
    assert hint is not None and "socksicle-tun" in hint
    assert eng.pop_tun_adapter_conflict() is True
    assert eng.pop_tun_adapter_conflict() is False  # Consumed once

    assert eng._extra_error_hint("WARN[0001] something else happened") is None
    assert eng._extra_error_hint("FATAL failed to listen on 127.0.0.1:1080") is None
    assert eng._extra_error_hint("") is None
    assert eng.pop_tun_adapter_conflict() is False

    # 2. proc_guard adapter present parsing (Windows)
    monkeypatch.setattr(pg, "is_windows", lambda: True)
    out = ("Admin State    State          Type             Interface Name\n"
           "Enabled        Connected      Dedicated        socksicle-tun\n"
           "Enabled        Disconnected   Dedicated        Wi-Fi\n")
    with mock.patch.object(pg.subprocess, "run", return_value=_completed(out)):
        assert pg.tun_adapter_present("socksicle-tun") is True
        assert pg.tun_adapter_present("SOCKsicle-TUN") is True
        assert pg.tun_adapter_present("other-tun") is False

    # Error handling returns None
    with mock.patch.object(pg.subprocess, "run", return_value=_completed("", returncode=1)):
        assert pg.tun_adapter_present("socksicle-tun") is None
    with mock.patch.object(pg.subprocess, "run", side_effect=OSError("no netsh")):
        assert pg.tun_adapter_present("socksicle-tun") is None

    # Non-windows returns False without running subprocess
    monkeypatch.setattr(pg, "is_windows", lambda: False)
    with mock.patch.object(pg.subprocess, "run") as run_mock:
        assert pg.tun_adapter_present("socksicle-tun") is False
        run_mock.assert_not_called()


def test_wait_for_tun_adapter_gone_scenarios(monkeypatch):
    # 1. Adapter gone after loop
    seen = iter([True, True, False])
    monkeypatch.setattr(pg, "tun_adapter_present", lambda name: next(seen))
    with mock.patch("time.sleep", return_value=None):
        assert pg.wait_for_tun_adapter_gone("socksicle-tun", timeout=5.0) is True

    # 2. Timeout returns False
    monkeypatch.setattr(pg, "tun_adapter_present", lambda name: True)
    with mock.patch("time.sleep", return_value=None):
        assert pg.wait_for_tun_adapter_gone("socksicle-tun", timeout=0) is False

    # 3. Unknown (None) proceeds immediately
    monkeypatch.setattr(pg, "tun_adapter_present", lambda name: None)
    assert pg.wait_for_tun_adapter_gone("socksicle-tun", timeout=5.0) is True


class _StubEngine:
    def __init__(self):
        self._conflict = False
        self.torn_down = 0

    def pop_tun_adapter_conflict(self):
        hit, self._conflict = self._conflict, False
        return hit

    def teardown(self):
        self.torn_down += 1


def _conflict_manager(monkeypatch):
    mgr = ConnectionManager({"tun_mode": False})
    stub = _StubEngine()
    mgr._engine = stub
    calls = []
    monkeypatch.setattr(mgr, "toggle", lambda s, c: calls.append((s, c)))
    monkeypatch.setattr("utils.connection_manager.QTimer.singleShot", lambda ms, fn: fn())
    return mgr, stub, calls


def test_tun_conflict_retry_lifecycle(monkeypatch):
    # 1. Schedules single retry, tears down, decrements budget
    mgr, stub, calls = _conflict_manager(monkeypatch)
    server = SimpleNamespace(name="S1")
    mgr.state = CONNECTING
    mgr._last_connected_server = server
    mgr._tun_retries = 1
    stub._conflict = True

    mgr._on_status_changed("Connection failed: TUN adapter stuck", True)

    assert mgr.state == DISCONNECTED
    assert stub.torn_down == 1
    assert calls == [(server, True)]
    assert mgr._tun_retries == 0

    # 2. Second failure does not schedule another retry
    mgr.state = CONNECTING
    stub._conflict = True
    mgr._on_status_changed("fail", True)
    assert len(calls) == 1

    # 3. No retry without last_connected_server
    mgr2, stub2, calls2 = _conflict_manager(monkeypatch)
    mgr2.state = CONNECTING
    mgr2._last_connected_server = None
    mgr2._tun_retries = 1
    stub2._conflict = True
    mgr2._on_status_changed("fail", True)
    assert calls2 == []

    # 4. No retry without conflict flag
    mgr3, stub3, calls3 = _conflict_manager(monkeypatch)
    mgr3.state = CONNECTING
    mgr3._last_connected_server = server
    mgr3._tun_retries = 1
    stub3._conflict = False
    mgr3._on_status_changed("fail", True)
    assert calls3 == []
    assert mgr3._tun_retries == 1
