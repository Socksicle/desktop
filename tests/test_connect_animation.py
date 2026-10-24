"""Hero-button animation must survive (re)connect flows without GUI stalls."""
import threading
import time
from types import SimpleNamespace

import pytest

from ui.main_window import RoundedWindow
from utils.connection_manager import ConnectionManager, CONNECTED, CONNECTING, DISCONNECTED
import utils.twinsock as tw


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


@pytest.fixture
def main_win(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()
    return RoundedWindow()


class _StartOkEngine:
    def __init__(self):
        self.started = 0

    def start(self, server):
        self.started += 1
        return True

    def teardown(self):
        pass

    def disconnect_from_server(self):
        pass

    def is_running(self):
        return False


def test_connect_disconnect_ui_and_animation(main_win, qapp):
    from utils.i18n import tr
    from ui.hero_power_button import HeroPowerButton
    from utils.theme import M3Theme

    win = main_win

    # 1. Stale disconnect keeps connecting UI
    win.screen_main.set_status(tr("status.connecting"), False, is_connecting=True)
    assert win.screen_main.hero_btn.is_connecting() is True
    win.connection_manager.is_connecting = True
    win.on_status_changed(tr("engine.disconnected"), False)
    assert win.screen_main.hero_btn.is_connecting() is True
    assert tr("status.connecting").rstrip(".") in win.screen_main.status_label.text()

    # 2. User disconnect shows offline
    win.connection_manager.is_connecting = False
    win.on_status_changed(tr("engine.disconnected"), False)
    assert win.screen_main.hero_btn.is_connecting() is False
    assert win.screen_main.status_label.text() == tr("status.disconnected")

    # 3. Toggle rearms connecting status
    mgr = ConnectionManager({"tun_mode": False})
    mgr._engine = _StartOkEngine()
    server = SimpleNamespace(protocol=SimpleNamespace(value="shadowsocks"))
    msgs = []
    mgr.statusChanged.connect(lambda m, e: msgs.append((m, e)))
    try:
        assert mgr.toggle(server, True) is True
        assert any((not err) and ("Подключение" in m) for m, err in msgs)
    finally:
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()

    # 4. Hero button clockwise rotations
    btn = HeroPowerButton(theme=M3Theme())
    btn.set_connecting(True)
    try:
        before = btn._spin_angle
        btn._on_spin_tick()
        assert btn._spin_angle == pytest.approx((before - 4.0) % 360.0)
    finally:
        btn.set_connecting(False)

    rots = [btn._ring_frame(p)[0] for p in (0.4, 0.6, 0.8, 1.0)]
    assert rots == sorted(rots, reverse=True)
    assert btn._ring_frame(1.0)[1] == pytest.approx(180.0)
    assert btn._ring_frame(0.4)[1] == pytest.approx(115.0)

    # 5. Disconnect ring frames rotate clockwise and contract
    disc_rots = [btn._disconnect_ring_frame(p)[0] for p in (1.0, 0.7, 0.3, 0.0)]
    assert disc_rots == sorted(disc_rots, reverse=True)
    assert btn._disconnect_ring_frame(1.0)[1] == pytest.approx(180.0)
    assert btn._disconnect_ring_frame(0.0)[1] == pytest.approx(80.0)

    # 6. Paint rendering in all states without exceptions
    btn.set_connected(True, animated=False)
    btn.repaint()
    btn.set_progress(0.5)
    btn._is_connected = False
    btn.repaint()
    btn.set_connected(False, animated=False)
    btn.repaint()


def test_killswitch_async_execution_and_reporting(qapp, monkeypatch):
    import utils.killswitch

    main_tid = threading.get_ident()
    done = threading.Event()
    seen = {}

    class FakeKS:
        last_error = "netsh exploded"

        @staticmethod
        def get_instance():
            return FakeKS()

        def enable(self, host, port, engine_path):
            seen.update(host=host, port=port, path=engine_path, tid=threading.get_ident())
            done.set()
            return True

        def disable(self):
            pass

    monkeypatch.setattr(utils.killswitch, "KillSwitchManager", FakeKS)
    monkeypatch.setattr("utils.connection_manager.fetch_ip_info_via_proxy", lambda port: None)

    mgr = ConnectionManager({"tun_mode": False})
    stub = _StartOkEngine()
    stub.confirm_connected = lambda: None
    stub.get_current_server = lambda: SimpleNamespace(host="9.9.9.9", port=443)
    stub.find_binary = lambda: None
    mgr._engine = stub
    monkeypatch.setattr(mgr, "_update_ping", lambda: None)
    monkeypatch.setattr(mgr, "_fetch_geo", lambda: None)
    mgr.kill_switch_enabled = True
    mgr.state = CONNECTING
    mgr._generation = 7

    try:
        # Off GUI thread execution
        mgr._on_proxy_ready()
        assert done.wait(3.0), "kill-switch enable was never called"
        assert seen["host"] == "9.9.9.9" and seen["port"] == 443 and seen["tid"] != main_tid

        # Aborted on stale generation
        calls = []
        FakeKS.enable = lambda self, *args: calls.append(args) or True
        mgr.state = DISCONNECTED
        mgr._generation = 9
        mgr._enable_killswitch_async(gen=8, host="9.9.9.9", port=443)
        time.sleep(0.1)
        assert calls == []

        # Failure reported
        reasons = []
        FakeKS.enable = lambda self, *args: False
        mgr.state = CONNECTED
        mgr._generation = 11
        mgr.killSwitchFailed.connect(reasons.append)
        mgr._enable_killswitch_async(gen=11, host="9.9.9.9", port=443)
        assert reasons == ["netsh exploded"]
    finally:
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()


def test_killswitch_async_race_conditions(qapp, monkeypatch):
    import utils.killswitch

    # 1. Rollback skips already active session
    entered = threading.Event()
    release = threading.Event()
    disabled = []

    class FakeKS:
        last_error = ""
        is_active = True

        @staticmethod
        def get_instance():
            return FakeKS()

        def enable(self, *args):
            entered.set()
            release.wait(3.0)
            return True

        def disable(self):
            disabled.append(True)

    monkeypatch.setattr(utils.killswitch, "KillSwitchManager", FakeKS)
    mgr = ConnectionManager({"tun_mode": False})
    try:
        mgr.state = CONNECTED
        mgr._generation = 20
        worker = threading.Thread(target=mgr._enable_killswitch_async, args=(20, "9.9.9.9", 443))
        worker.start()
        assert entered.wait(3.0)
        mgr.state = DISCONNECTED
        mgr._generation = 21
        release.set()
        worker.join(3.0)
        assert not worker.is_alive() and disabled == []
    finally:
        release.set()
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()

    # 2. Rules rolled back when disconnect wins
    entered2 = threading.Event()
    release2 = threading.Event()
    disabled2 = []
    FakeKS.is_active = False
    FakeKS.enable = lambda self, *args: entered2.set() or release2.wait(3.0) or True
    FakeKS.disable = lambda self: disabled2.append(True)

    mgr2 = ConnectionManager({"tun_mode": False})
    try:
        mgr2.state = CONNECTED
        mgr2._generation = 12
        worker2 = threading.Thread(target=mgr2._enable_killswitch_async, args=(12, "9.9.9.9", 443))
        worker2.start()
        assert entered2.wait(3.0)
        mgr2.state = DISCONNECTED
        mgr2._generation = 13
        release2.set()
        worker2.join(3.0)
        assert not worker2.is_alive() and disabled2 == [True]
    finally:
        release2.set()
        mgr2.probe_timer.stop()
        mgr2.ping_timer.stop()
