"""Tests for utils.engines.engine_manager (registry and selection)."""
import unittest
from pathlib import Path
from unittest import mock
import pytest

from utils.engines.base import EngineType, CheckResult
from utils.engines.xray_engine import XrayEngine
from utils.engines.singbox_engine import SingBoxEngine
from utils.engines import engine_manager as em
from utils.server_model import Server, ProxyProtocol


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class EngineRegistryAndLookupTest(unittest.TestCase):

    def test_engine_registration_and_singleton_lookup(self):
        classes = em.get_engine_classes()
        self.assertEqual(classes[EngineType.XRAY], XrayEngine)
        self.assertEqual(classes[EngineType.SINGBOX], SingBoxEngine)

        types = em.get_all_engine_types()
        self.assertEqual(set(types), {EngineType.XRAY, EngineType.SINGBOX})

        e1 = em.get_engine(EngineType.SINGBOX)
        e2 = em.get_engine(EngineType.SINGBOX)
        self.assertIs(e1, e2)

        by_class = em.get_engine(SingBoxEngine)
        self.assertIsInstance(by_class, SingBoxEngine)
        self.assertEqual(by_class.engine_type, EngineType.SINGBOX)

        xr = em.get_engine(EngineType.XRAY)
        self.assertIsNot(xr, e1)

    def test_current_engine_and_check(self):
        # Settings resolution
        self.assertEqual(em.get_current_engine({}).engine_type, EngineType.SINGBOX)
        self.assertEqual(em.get_current_engine({"engine": "xray"}).engine_type, EngineType.XRAY)
        self.assertEqual(em.get_current_engine({"engine": "sing-box"}).engine_type, EngineType.SINGBOX)
        self.assertEqual(em.get_current_engine({"engine": "unknown"}).engine_type, EngineType.SINGBOX)

        # Check engine unusable
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=None):
            result = em.check_engine(EngineType.SINGBOX)
            self.assertIsInstance(result, CheckResult)
            self.assertFalse(result.usable)
            self.assertIn("sing-box", result.reason)

        # Check engine usable
        fake_path = Path("C:/fake/sing-box")
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=fake_path), \
             mock.patch.object(SingBoxEngine, "check_usable", return_value=CheckResult(True, "")):
            self.assertTrue(em.check_engine(EngineType.SINGBOX).usable)

    def test_switch_engine_lifecycle(self):
        # Updates settings
        settings = {"engine": "sing-box"}
        conn_mgr = mock.MagicMock(is_connected=False)
        new_engine = em.switch_engine(settings, EngineType.XRAY, conn_mgr)
        self.assertEqual(settings["engine"], "xray")
        self.assertEqual(new_engine.engine_type, EngineType.XRAY)

        # Disconnects if connected
        conn_mgr.is_connected = True
        em.switch_engine(settings, EngineType.XRAY, conn_mgr)
        conn_mgr.disconnect.assert_called_once()

        # Without conn mgr & normalization
        s2 = {"engine": "xray"}
        self.assertEqual(em.switch_engine(s2, EngineType.SINGBOX).engine_type, EngineType.SINGBOX)
        self.assertEqual(s2["engine"], "sing-box")

        self.assertEqual(em.get_engine("unknown").engine_type, EngineType.SINGBOX)
        s3 = {"engine": "unknown"}
        self.assertEqual(em.switch_engine(s3, "unknown").engine_type, EngineType.SINGBOX)
        self.assertEqual(s3["engine"], "sing-box")


class ResolveEngineRoutingTest(unittest.TestCase):

    def test_exclusive_protocols_and_transports(self):
        # Singbox exclusive protocols
        for proto in (ProxyProtocol.HYSTERIA2, ProxyProtocol.TUIC, ProxyProtocol.TROJAN):
            srv = Server(protocol=proto)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto"}), EngineType.SINGBOX)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "xray"}), EngineType.SINGBOX)

        # Xray exclusive transports
        for tr in ("xhttp", "splithttp", "kcp"):
            srv = Server(protocol=ProxyProtocol.VLESS, transport=tr)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto"}), EngineType.XRAY)
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "sing-box"}), EngineType.XRAY)

    def test_tun_mode_and_preferences(self):
        srv = Server(protocol=ProxyProtocol.VLESS, transport="tcp")
        # TUN mode forces sing-box even when xray is selected
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto", "tun_mode": True}), EngineType.SINGBOX)
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "xray", "tun_mode": True}), EngineType.SINGBOX)
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "sing-box", "tun_mode": True}), EngineType.SINGBOX)

        srv_xh = Server(protocol=ProxyProtocol.VLESS, transport="xhttp")
        self.assertEqual(em.resolve_engine_for_server(srv_xh, {"engine": "auto", "tun_mode": True}), EngineType.SINGBOX)

        # Explicit selection respected without TUN
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "xray"}), EngineType.XRAY)
        self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "sing-box"}), EngineType.SINGBOX)

        # Fallback to xray when singbox missing
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=None), \
             mock.patch.object(XrayEngine, "find_binary", return_value=Path("C:/fake/xray")), \
             mock.patch.object(XrayEngine, "check_usable", return_value=CheckResult(True, "")):
            self.assertEqual(em.resolve_engine_for_server(srv, {"engine": "auto"}), EngineType.XRAY)


if __name__ == "__main__":
    unittest.main()
