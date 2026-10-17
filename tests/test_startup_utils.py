"""Integration tests: startup backend check (utils.startup_utils) and the
connection flow it feeds into.  GUI bits run on the offscreen Qt platform;
all network and subprocess activity is mocked.
"""
import io
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from utils import startup_utils
from utils.engines.base import (
    EngineType, CheckResult, )
from utils.engines import base as engine_base
from utils.engines.singbox_engine import SingBoxEngine


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class FindUsableBackendTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        mgr_p = mock.patch("utils.server_manager.ServerManager")
        self._mock_mgr_cls = mgr_p.start()
        self.addCleanup(mgr_p.stop)
        self._mock_mgr = self._mock_mgr_cls.return_value
        self._mock_mgr.settings = {"engine": "sing-box"}

    def test_usable_backend_found(self):
        existing = self.root / "existing-bin" / "sing-box"
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = existing
        engine_inst.check_usable.return_value = CheckResult(True, "")
        with mock.patch("utils.startup_utils.get_engine",
                        return_value=engine_inst):
            result = startup_utils.find_usable_backend()
        self.assertEqual(result, existing)

    def test_explicit_engine_type_parameter(self):
        existing = self.root / "existing-bin" / "xray"
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = existing
        engine_inst.check_usable.return_value = CheckResult(True, "")
        with mock.patch("utils.startup_utils.get_engine",
                        return_value=engine_inst) as get_eng:
            result = startup_utils.find_usable_backend(EngineType.XRAY)
            self.assertEqual(result, existing)
            get_eng.assert_called_with(EngineType.XRAY)

    def test_missing_backend_returns_none(self):
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = None
        engine_inst.engine_type = EngineType.SINGBOX
        with mock.patch("utils.startup_utils.get_engine",
                        return_value=engine_inst):
            result = startup_utils.find_usable_backend()
        self.assertIsNone(result)

    def test_unusable_backend_returns_none(self):
        existing = self.root / "existing-bin" / "sing-box"
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = existing
        engine_inst.check_usable.return_value = CheckResult(False, "bad")
        engine_inst.engine_type = EngineType.SINGBOX
        with mock.patch("utils.startup_utils.get_engine",
                        return_value=engine_inst):
            result = startup_utils.find_usable_backend()
        self.assertIsNone(result)

    def test_engine_type_from_settings(self):
        self._mock_mgr.settings = {"engine": "xray"}
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = None
        engine_inst.engine_type = EngineType.XRAY
        with mock.patch("utils.startup_utils.get_engine",
                        return_value=engine_inst) as get_eng:
            result = startup_utils.find_usable_backend()
        self.assertIsNone(result)
        get_eng.assert_called_once()

    def test_unknown_engine_setting_falls_back_to_singbox(self):
        self._mock_mgr.settings = {"engine": "nope"}
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = None
        engine_inst.engine_type = EngineType.SINGBOX
        with mock.patch("utils.startup_utils.get_engine",
                        return_value=engine_inst):
            result = startup_utils.find_usable_backend()
        self.assertIsNone(result)


class FailureDialogTest(unittest.TestCase):

    def test_missing_dialog_offers_manual_fallback(self):
        box = mock.MagicMock()
        box.return_value.exec.return_value = 0
        with mock.patch.object(startup_utils, "QMessageBox", box), \
             mock.patch.object(startup_utils, "manual_install_instructions",
                               return_value="MANUAL_STEPS"):
            startup_utils.show_backend_missing()
        text = box.return_value.setText.call_args.args[0]
        informative = box.return_value.setInformativeText.call_args.args[0]
        from utils.i18n import tr
        self.assertIn(tr("backend.missing_text"), text)
        self.assertIn("MANUAL_STEPS", informative)
        self.assertEqual(box.return_value.exec.call_count, 1)

    def test_missing_dialog_manual_instructions(self):
        box = mock.MagicMock()
        box.return_value.exec.return_value = 0
        with mock.patch.object(startup_utils, "QMessageBox", box):
            startup_utils.show_backend_missing()
        informative = box.return_value.setInformativeText.call_args.args[0]
        self.assertIn("sing-box", informative)
        self.assertIn("SagerNet/sing-box/releases", informative)


class ConnectionFlowTest(unittest.TestCase):

    def test_connect_starts_found_backend_and_disconnect_terminates(self):
        fake_proc = _FakeProc()
        backend = Path("C:/bin/sing-box.exe")
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw",
                                 protocol="shadowsocks")
        with mock.patch.object(SingBoxEngine, "find_binary",
                               return_value=backend), \
             mock.patch.object(engine_base.subprocess, "Popen",
                               return_value=fake_proc), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
              mock.patch("utils.engines.base.wait_for_port_available",
                         return_value=True), \
              mock.patch("utils.engines.base._reserve_port",
                         return_value=mock.MagicMock()), \
              mock.patch("utils.engines.base._secure_config_file"):
            proc = SingBoxEngine()
            self.assertTrue(proc.start(server))
            self.assertTrue(proc.is_running())
            proc.disconnect_from_server()
        self.assertFalse(proc.is_running())
        self.assertTrue(fake_proc.stop)

    def test_connect_reports_error_when_backend_missing(self):
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw",
                                 protocol="shadowsocks")
        with mock.patch.object(SingBoxEngine, "find_binary",
                               return_value=None):
            proc = SingBoxEngine()
            emitted = []
            proc.statusChanged.connect(
                lambda msg, err: emitted.append((msg, err)))
            self.assertFalse(proc.start(server))
        self.assertTrue(emitted)
        from utils.i18n import tr
        self.assertIn(tr("engine.binary_not_found").format(name="sing-box"), emitted[0][0])
        self.assertTrue(emitted[0][1])


class _FakeProc:
    def __init__(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.stop = False
        self.pid = 12345

    def poll(self):
        return 0 if self.stop else None

    def terminate(self):
        self.stop = True

    def kill(self):
        self.stop = True

    def wait(self, timeout=None):
        while not self.stop:
            time.sleep(0.01)
        return 0


if __name__ == "__main__":
    unittest.main()