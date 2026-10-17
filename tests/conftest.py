"""Shared pytest fixtures for the Socksicle test suite."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """Session-wide QApplication singleton for Qt-based tests."""
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    yield inst


@pytest.fixture(autouse=True)
def _isolate_app_config_dir(tmp_path_factory, monkeypatch):
    """Ensure all tests run with an isolated config directory in tmp_path."""
    temp_dir = tmp_path_factory.mktemp("socksicle_test_config")
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: temp_dir)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: temp_dir)
    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: temp_dir)
    monkeypatch.setattr("utils.engines.proc_guard.get_config_dir", lambda: temp_dir)
    import utils.subscription_store
    utils.subscription_store._last_fetch_errors.clear()


@pytest.fixture(autouse=True)
def _mock_init_app_fonts(monkeypatch):
    """Prevent font file access violations in parallel xdist workers."""
    import utils.font_utils
    monkeypatch.setattr(utils.font_utils, "init_app_fonts", lambda: None)


@pytest.fixture(autouse=True)
def _disable_sub_auto_update(monkeypatch):
    """Ensure no unmocked subscription update threads are spawned during tests."""
    from utils.subscription_manager import SubscriptionManager
    monkeypatch.setattr(SubscriptionManager, "update_all", lambda self: None)


@pytest.fixture(scope="session", autouse=True)
def _drain_qt_threadpool():
    """Let queued QRunnable work finish before the QApplication is destroyed."""
    yield
    try:
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(5000)
    except Exception:
        pass


@pytest.fixture
def main_win(qapp, monkeypatch, tmp_path):
    """Fixture providing an isolated RoundedWindow with test servers populated."""
    import utils.twinsock as tw
    from ui.main_window import RoundedWindow
    from utils.server_model import Server, ProxyProtocol

    monkeypatch.setattr("utils.server_manager.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.subscription_store.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("utils.platform_utils.get_config_dir", lambda: tmp_path)
    tw._reset()

    win = RoundedWindow()
    manual = Server(key="manual-1", name="Manual One", host="10.0.0.1", port=8388,
                    password="pw1", protocol=ProxyProtocol.SHADOWSOCKS)
    win.server_manager.manual_servers = [manual]
    win.manual_servers = [manual]
    win.subscription_manager.subscriptions = [{
        "name": "TestSub",
        "url": "https://example.com/sub",
        "icon_emoji": "🚀",
        "servers": [
            Server(key="sub-a", name="Sub A", host="10.0.0.2", port=8388,
                   password="pw2", protocol=ProxyProtocol.SHADOWSOCKS),
            Server(key="sub-b", name="Sub B", host="10.0.0.3", port=8388,
                   password="pw3", protocol=ProxyProtocol.SHADOWSOCKS),
        ],
    }]
    win._refresh_server_list()
    if win.server_panel._server_items:
        win.server_panel._server_items[0].radio.setChecked(True)
    yield win
    try:
        win.close()
        win.deleteLater()
    except Exception:
        pass

