"""Tests for utils.engines.engine_manager (registry and selection)."""
import unittest
from pathlib import Path
from unittest import mock

import pytest

from utils.engines.base import EngineType, CheckResult
from utils.engines.xray_engine import XrayEngine
from utils.engines.singbox_engine import SingBoxEngine
from utils.engines import engine_manager as em


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class RegistrationTest(unittest.TestCase):
    """Test engine registration and lookup."""


    def test_all_engines_registered(self):
        classes = em.get_engine_classes()
        self.assertIn(EngineType.XRAY, classes)
        self.assertIn(EngineType.SINGBOX, classes)
        self.assertEqual(classes[EngineType.XRAY], XrayEngine)
        self.assertEqual(classes[EngineType.SINGBOX], SingBoxEngine)

    def test_get_all_engine_types(self):
        types = em.get_all_engine_types()
        self.assertEqual(len(types), 2)
        self.assertIn(EngineType.XRAY, types)
        self.assertIn(EngineType.SINGBOX, types)

    def test_get_engine_returns_singleton(self):
        e1 = em.get_engine(EngineType.SINGBOX)
        e2 = em.get_engine(EngineType.SINGBOX)
        self.assertIs(e1, e2)

    def test_get_engine_by_class(self):
        e = em.get_engine(SingBoxEngine)
        self.assertIsInstance(e, SingBoxEngine)
        self.assertEqual(e.engine_type, EngineType.SINGBOX)

    def test_get_engine_different_types(self):
        xr = em.get_engine(EngineType.XRAY)
        sb = em.get_engine(EngineType.SINGBOX)
        self.assertIsNot(xr, sb)


class GetCurrentEngineTest(unittest.TestCase):
    """Test get_current_engine with settings dict."""


    def test_default_engine_is_singbox(self):
        engine = em.get_current_engine({})
        self.assertEqual(engine.engine_type, EngineType.SINGBOX)

    def test_xray_setting(self):
        engine = em.get_current_engine({"engine": "xray"})
        self.assertEqual(engine.engine_type, EngineType.XRAY)

    def test_singbox_setting(self):
        engine = em.get_current_engine({"engine": "sing-box"})
        self.assertEqual(engine.engine_type, EngineType.SINGBOX)

    def test_unknown_engine_falls_back_to_singbox(self):
        engine = em.get_current_engine({"engine": "unknown"})
        self.assertEqual(engine.engine_type, EngineType.SINGBOX)


class CheckEngineTest(unittest.TestCase):
    """Test check_engine function."""


    def test_check_engine_returns_check_result(self):
        # Mock find_binary to return None (no binary found)
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=None):
            result = em.check_engine(EngineType.SINGBOX)
            self.assertIsInstance(result, CheckResult)
            self.assertFalse(result.usable)
            self.assertIn("sing-box", result.reason)

    def test_check_engine_with_usable_binary(self):
        fake_path = Path("C:/fake/sing-box")
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=fake_path), \
             mock.patch.object(SingBoxEngine, "check_usable",
                               return_value=CheckResult(True, "")):
            result = em.check_engine(EngineType.SINGBOX)
            self.assertTrue(result.usable)


class SwitchEngineTest(unittest.TestCase):
    """Test switch_engine function."""


    def test_switch_engine_updates_settings(self):
        settings = {"engine": "sing-box"}
        conn_mgr = mock.MagicMock()
        conn_mgr.is_connected = False
        new_engine = em.switch_engine(settings, EngineType.XRAY, conn_mgr)
        self.assertEqual(settings["engine"], "xray")
        self.assertEqual(new_engine.engine_type, EngineType.XRAY)

    def test_switch_engine_disconnects_if_connected(self):
        settings = {"engine": "sing-box"}
        conn_mgr = mock.MagicMock()
        conn_mgr.is_connected = True
        em.switch_engine(settings, EngineType.XRAY, conn_mgr)
        conn_mgr.disconnect.assert_called_once()

    def test_switch_engine_without_connection_manager(self):
        settings = {"engine": "xray"}
        new_engine = em.switch_engine(settings, EngineType.SINGBOX)
        self.assertEqual(settings["engine"], "sing-box")
        self.assertEqual(new_engine.engine_type, EngineType.SINGBOX)

    def test_get_engine_unregistered_type_falls_back_to_singbox(self):
        engine = em.get_engine("unknown")
        self.assertEqual(engine.engine_type, EngineType.SINGBOX)

    def test_switch_engine_normalizes_unknown_type(self):
        settings = {"engine": "unknown"}
        new_engine = em.switch_engine(settings, "unknown")
        self.assertEqual(settings["engine"], "sing-box")
        self.assertEqual(new_engine.engine_type, EngineType.SINGBOX)


class ResolveEngineForServerTest(unittest.TestCase):
    """Test resolve_engine_for_server logic."""

    def test_exclusive_singbox_protocols_always_route_to_singbox(self):
        from utils.server_model import Server, ProxyProtocol
        for proto in (ProxyProtocol.HYSTERIA2, ProxyProtocol.TUIC, ProxyProtocol.TROJAN):
            srv = Server(protocol=proto)
            # Even if settings explicitly asked for xray or auto
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto"}), EngineType.SINGBOX)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "xray"}), EngineType.SINGBOX)

    def test_exclusive_xray_transports_route_to_xray(self):
        from utils.server_model import Server, ProxyProtocol
        for tr in ("xhttp", "splithttp", "kcp"):
            srv = Server(protocol=ProxyProtocol.VLESS, transport=tr)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto"}), EngineType.XRAY)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "sing-box"}), EngineType.XRAY)

    def test_tun_mode_prefers_singbox(self):
        from utils.server_model import Server, ProxyProtocol
        srv = Server(protocol=ProxyProtocol.VLESS, transport="tcp")
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto", "tun_mode": True}), EngineType.SINGBOX)

    def test_tun_mode_overrides_explicit_engine_preference(self):
        # Only sing-box implements TUN (Wintun/sing-tun); xray would start
        # silently in proxy mode instead of the selected TUN routing.
        from utils.server_model import Server, ProxyProtocol
        srv = Server(protocol=ProxyProtocol.VLESS, transport="tcp")
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "xray", "tun_mode": True}), EngineType.SINGBOX)
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "sing-box", "tun_mode": True}), EngineType.SINGBOX)

    def test_explicit_selection_respected_for_compatible_server(self):
        from utils.server_model import Server, ProxyProtocol
        srv = Server(protocol=ProxyProtocol.VLESS, transport="tcp")
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "xray"}), EngineType.XRAY)
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "sing-box"}), EngineType.SINGBOX)

    def test_auto_fallback_to_xray_when_singbox_unusable(self):
        from utils.server_model import Server, ProxyProtocol
        srv = Server(protocol=ProxyProtocol.VLESS, transport="tcp")
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=None), \
             mock.patch.object(XrayEngine, "find_binary", return_value=Path("C:/fake/xray")), \
             mock.patch.object(XrayEngine, "check_usable", return_value=CheckResult(True, "")):
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto"}), EngineType.XRAY)


if __name__ == "__main__":
    unittest.main()

