"""Integration tests: startup backend check (utils.startup_utils) and the
connection flow it feeds into. GUI bits run on the offscreen Qt platform;
all network and subprocess activity is mocked.
"""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from utils import startup_utils
from utils.i18n import set_language, tr
from utils.engines.base import EngineType, CheckResult
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

    def test_usable_backend_resolution(self):
        # Default settings resolution
        existing = self.root / "existing-bin" / "sing-box"
        engine_inst = mock.MagicMock()
        engine_inst.find_binary.return_value = existing
        engine_inst.check_usable.return_value = CheckResult(True, "")
        with mock.patch("utils.startup_utils.get_engine", return_value=engine_inst):
            self.assertEqual(startup_utils.find_usable_backend(), existing)

        # Explicit engine parameter
        existing_xray = self.root / "existing-bin" / "xray"
        engine_inst.find_binary.return_value = existing_xray
        with mock.patch("utils.startup_utils.get_engine", return_value=engine_inst) as get_eng:
            self.assertEqual(startup_utils.find_usable_backend(EngineType.XRAY), existing_xray)
            get_eng.assert_called_with(EngineType.XRAY)

    def test_unusable_missing_or_invalid_backend(self):
        engine_inst = mock.MagicMock()
        # Missing binary
        engine_inst.find_binary.return_value = None
        engine_inst.engine_type = EngineType.SINGBOX
        with mock.patch("utils.startup_utils.get_engine", return_value=engine_inst):
            self.assertIsNone(startup_utils.find_usable_backend())

        # Unusable binary
        existing = self.root / "existing-bin" / "sing-box"
        engine_inst.find_binary.return_value = existing
        engine_inst.check_usable.return_value = CheckResult(False, "bad")
        with mock.patch("utils.startup_utils.get_engine", return_value=engine_inst):
            self.assertIsNone(startup_utils.find_usable_backend())

        # Engine setting xray
        self._mock_mgr.settings = {"engine": "xray"}
        engine_inst.find_binary.return_value = None
        engine_inst.engine_type = EngineType.XRAY
        with mock.patch("utils.startup_utils.get_engine", return_value=engine_inst) as get_eng:
            self.assertIsNone(startup_utils.find_usable_backend())
            get_eng.assert_called_once()

        # Unknown engine setting falls back to singbox
        self._mock_mgr.settings = {"engine": "nope"}
        engine_inst.engine_type = EngineType.SINGBOX
        with mock.patch("utils.startup_utils.get_engine", return_value=engine_inst):
            self.assertIsNone(startup_utils.find_usable_backend())


class FailureDialogTest(unittest.TestCase):

    def test_missing_dialog_offers_manual_fallback_and_instructions(self):
        box = mock.MagicMock()
        box.return_value.exec.return_value = 0
        with mock.patch.object(startup_utils, "QMessageBox", box), \
             mock.patch.object(startup_utils, "manual_install_instructions", return_value="MANUAL_STEPS"):
            startup_utils.show_backend_missing()
        text = box.return_value.setText.call_args.args[0]
        informative = box.return_value.setInformativeText.call_args.args[0]
        self.assertIn(tr("backend.missing_text"), text)
        self.assertIn("MANUAL_STEPS", informative)
        self.assertEqual(box.return_value.exec.call_count, 1)

        # Without mocked instructions, verifies default instructions
        box.reset_mock()
        with mock.patch.object(startup_utils, "QMessageBox", box):
            startup_utils.show_backend_missing()
        informative = box.return_value.setInformativeText.call_args.args[0]
        self.assertIn("sing-box", informative)
        self.assertIn("SagerNet/sing-box/releases", informative)


class ConnectionFlowTest(unittest.TestCase):

    def test_connection_flow_lifecycle_and_missing_binary(self):
        fake_proc = _FakeProc()
        backend = Path("C:/bin/sing-box.exe")
        server = SimpleNamespace(host="1.2.3.4", port=8388,
                                 method="aes-256-gcm", password="pw",
                                 protocol="shadowsocks")
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=backend), \
             mock.patch.object(engine_base.subprocess, "Popen", return_value=fake_proc), \
             mock.patch("utils.engines.base.write_pid_marker"), \
             mock.patch("utils.engines.base.cleanup_stale_engines"), \
             mock.patch("utils.engines.base.wait_for_port_available", return_value=True), \
             mock.patch("utils.engines.base._reserve_port", return_value=mock.MagicMock()), \
             mock.patch("utils.engines.base._secure_config_file"):
            proc = SingBoxEngine()
            self.assertTrue(proc.start(server))
            self.assertTrue(proc.is_running())
            proc.disconnect_from_server()
        self.assertFalse(proc.is_running())
        self.assertTrue(fake_proc.stop)

        # Missing binary
        with mock.patch.object(SingBoxEngine, "find_binary", return_value=None):
            proc = SingBoxEngine()
            emitted = []
            proc.statusChanged.connect(lambda msg, err: emitted.append((msg, err)))
            self.assertFalse(proc.start(server))
        self.assertTrue(emitted)
        self.assertIn(tr("engine.binary_not_found").format(name="sing-box"), emitted[0][0])
        self.assertTrue(emitted[0][1])


class SingleInstanceLockTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_single_instance_lock_lifecycle(self):
        from PySide6.QtCore import QLockFile
        with mock.patch("utils.platform_utils.get_config_dir", return_value=self.root):
            lock = startup_utils.acquire_single_instance_lock()
            self.assertIsNotNone(lock)
            self.assertTrue((self.root / "single_instance.lock").exists())
            self.assertIsInstance(lock, QLockFile)
            # Second attempt fails
            self.assertIsNone(startup_utils.acquire_single_instance_lock())
            lock.unlock()
            # Can reacquire after release
            again = startup_utils.acquire_single_instance_lock()
            self.assertIsNotNone(again)
            again.unlock()

            # Stale lock file from crashed process is removed and acquired
            stale_file = self.root / "single_instance.lock"
            stale_file.write_text("9999999\nfake_app\nfake_host\n", encoding="utf-8")
            from_stale = startup_utils.acquire_single_instance_lock()
            self.assertIsNotNone(from_stale)
            self.addCleanup(from_stale.unlock)

    def test_already_running_dialog_and_language_settings(self):
        box = mock.MagicMock()
        box.return_value.exec.return_value = 0
        with mock.patch.object(startup_utils, "QMessageBox", box):
            startup_utils.show_already_running()
        self.assertEqual(tr("app.already_running_title"), box.return_value.setWindowTitle.call_args.args[0])
        self.assertEqual(tr("app.already_running_text"), box.return_value.setText.call_args.args[0])
        self.assertEqual(box.return_value.exec.call_count, 1)

        # Language settings test
        settings_path = self.root / "settings.json"
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump({"language": "en"}, f)
        with mock.patch("utils.platform_utils.get_config_dir", return_value=self.root):
            self.assertEqual(startup_utils.apply_language_from_settings(), "en")
        self.assertEqual(set_language(None), "ru")


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


class StaleKillSwitchWarningTest(unittest.TestCase):

    def test_initialize_warns_when_stale_rules_are_not_cleaned(self):
        ks = mock.Mock()
        ks.clean_stale_rules.return_value = False
        with mock.patch("utils.killswitch.KillSwitchManager.get_instance",
                        return_value=ks), \
             mock.patch.object(startup_utils, "setup_logging"), \
             mock.patch.object(startup_utils, "set_app_user_model_id"), \
             mock.patch.object(startup_utils, "install_excepthook"), \
             mock.patch.object(startup_utils, "configure_pixmap_cache"):
            with self.assertLogs("utils.startup_utils", level="WARNING") as cm:
                startup_utils.initialize()
        self.assertTrue(any("Kill Switch" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
