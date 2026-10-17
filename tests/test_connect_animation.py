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


def test_stale_disconnect_keeps_connecting_ui(main_win):
    """Hot-reconnect's internal 'Disconnected' must not stop the spin."""
    from utils.i18n import tr
    win = main_win
    win.screen_main.set_status(tr("status.connecting"), False, is_connecting=True)
    assert win.screen_main.hero_btn.is_connecting() is True

    win.connection_manager.is_connecting = True
    win.on_status_changed(tr("engine.disconnected"), False)

    assert win.screen_main.hero_btn.is_connecting() is True
    assert tr("status.connecting").rstrip(".") in win.screen_main.status_label.text()


def test_user_disconnect_shows_offline(main_win):
    from utils.i18n import tr
    win = main_win
    win.screen_main.set_status(tr("status.connecting"), False, is_connecting=True)
    win.connection_manager.is_connecting = False
    win.on_status_changed(tr("engine.disconnected"), False)

    assert win.screen_main.hero_btn.is_connecting() is False
    assert win.screen_main.status_label.text() == tr("status.disconnected")


class _StartOkEngine:
    """Minimal engine stub whose start() always succeeds."""

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


def test_toggle_rearms_connecting_status(qapp):
    """After a successful start the manager re-emits 'connecting' status."""
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


def test_killswitch_enable_runs_off_gui_thread(qapp, monkeypatch):
    """netsh/nft setup must not execute on the GUI thread."""
    import utils.killswitch

    main_tid = threading.get_ident()
    done = threading.Event()
    seen = {}

    class FakeKS:
        @staticmethod
        def get_instance():
            return FakeKS()

        def enable(self, host, port, engine_path):
            seen.update(host=host, port=port, path=engine_path,
                        tid=threading.get_ident())
            done.set()
            return True

    monkeypatch.setattr(utils.killswitch, "KillSwitchManager", FakeKS)
    # Keep the test hermetic: no real ping/probe/geo work.
    monkeypatch.setattr("utils.connection_manager.fetch_ip_info_via_proxy",
                        lambda port: None)

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
        mgr._on_proxy_ready()
        assert done.wait(3.0), "kill-switch enable was never called"
        assert seen["host"] == "9.9.9.9"
        assert seen["port"] == 443
        assert seen["tid"] != main_tid
    finally:
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()


def test_connecting_spin_runs_clockwise(qapp):
    """Connecting arcs must rotate right (decreasing Qt angle)."""
    from ui.hero_power_button import HeroPowerButton
    from utils.theme import M3Theme

    btn = HeroPowerButton(theme=M3Theme())
    btn.set_connecting(True)
    try:
        before = btn._spin_angle
        btn._on_spin_tick()
        assert btn._spin_angle == pytest.approx((before - 4.0) % 360.0)
    finally:
        btn.set_connecting(False)


def test_ring_finish_spins_clockwise(qapp):
    """Settling ring must keep rotating right as progress grows."""
    from ui.hero_power_button import HeroPowerButton
    from utils.theme import M3Theme

    btn = HeroPowerButton(theme=M3Theme())
    rots = [btn._ring_frame(p)[0] for p in (0.4, 0.6, 0.8, 1.0)]
    assert rots == sorted(rots, reverse=True)
    assert btn._ring_frame(1.0)[1] == pytest.approx(180.0)
    assert btn._ring_frame(0.4)[1] == pytest.approx(115.0)


def test_killswitch_enable_aborted_on_stale_generation(qapp, monkeypatch):
    import utils.killswitch

    calls = []

    class FakeKS:
        @staticmethod
        def get_instance():
            return FakeKS()

        def enable(self, *args):
            calls.append(args)
            return True

    monkeypatch.setattr(utils.killswitch, "KillSwitchManager", FakeKS)
    mgr = ConnectionManager({"tun_mode": False})
    try:
        mgr.state = DISCONNECTED
        mgr._generation = 9
        # Generation moved on and we are no longer connected: must not touch
        # the firewall after a user disconnect.
        mgr._enable_killswitch_async(gen=8, host="9.9.9.9", port=443)
        time.sleep(0.2)
        assert calls == []
    finally:
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()


def test_killswitch_failure_is_reported(qapp, monkeypatch):
    import utils.killswitch

    reasons = []

    class FakeKS:
        last_error = "netsh exploded"

        @staticmethod
        def get_instance():
            return FakeKS()

        def enable(self, *args):
            return False

        def disable(self):
            pass

    monkeypatch.setattr(utils.killswitch, "KillSwitchManager", FakeKS)
    mgr = ConnectionManager({"tun_mode": False})
    try:
        mgr.state = CONNECTED
        mgr._generation = 11
        mgr.killSwitchFailed.connect(reasons.append)
        mgr._enable_killswitch_async(gen=11, host="9.9.9.9", port=443)
        assert reasons == ["netsh exploded"]
    finally:
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()


def test_killswitch_rollback_skips_already_active_session(qapp, monkeypatch):
    """A stale hot-switch must not disable rules owned by a newer session."""
    import utils.killswitch

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
        worker = threading.Thread(
            target=mgr._enable_killswitch_async,
            args=(20, "9.9.9.9", 443),
        )
        worker.start()
        assert entered.wait(3.0)
        mgr.state = DISCONNECTED
        mgr._generation = 21
        release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert disabled == []
    finally:
        release.set()
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()


def test_killswitch_rules_rolled_back_when_disconnect_wins(qapp, monkeypatch):
    import utils.killswitch

    entered = threading.Event()
    release = threading.Event()
    disabled = []

    class FakeKS:
        last_error = ""

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
        mgr._generation = 12
        worker = threading.Thread(
            target=mgr._enable_killswitch_async,
            args=(12, "9.9.9.9", 443),
        )
        worker.start()
        assert entered.wait(3.0)
        # The user disconnects while netsh/nft is still running.
        mgr.state = DISCONNECTED
        mgr._generation = 13
        release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert disabled == [True]
    finally:
        release.set()
        mgr.probe_timer.stop()
        mgr.ping_timer.stop()

