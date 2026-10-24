"""Unit tests for Linux process death guard."""
import os
import signal
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import pytest

from utils.engines.linux_death_guard import (
    LinuxDeathGuard,
    start_linux_death_guard,
    stop_linux_death_guard,
)


@pytest.fixture(autouse=True)
def _qapp_available(qapp):
    return qapp


class LinuxDeathGuardTest(unittest.TestCase):

    def test_noop_on_non_linux(self):
        with mock.patch("sys.platform", "win32"):
            self.assertIsNone(start_linux_death_guard(1234))
        with mock.patch("sys.platform", "darwin"):
            self.assertIsNone(start_linux_death_guard(1234))

    def test_invalid_target_pid(self):
        with mock.patch("sys.platform", "linux"):
            self.assertIsNone(start_linux_death_guard(None))
            self.assertIsNone(start_linux_death_guard(True))
            self.assertIsNone(start_linux_death_guard(False))
            self.assertIsNone(start_linux_death_guard(0))
            self.assertIsNone(start_linux_death_guard(1))
            self.assertIsNone(start_linux_death_guard(-5))
            self.assertIsNone(start_linux_death_guard("not-a-pid"))

    def test_start_and_stop_guard_lifecycle(self):
        fake_guard_proc = mock.MagicMock()
        with mock.patch("sys.platform", "linux"), \
             mock.patch("os.pipe", return_value=(10, 11)), \
             mock.patch("os.set_inheritable"), \
             mock.patch("os.close") as mock_close, \
             mock.patch("subprocess.Popen", return_value=fake_guard_proc) as mock_popen:
            guard = start_linux_death_guard(54321)
            self.assertIsNotNone(guard)
            self.assertEqual(guard.target_pid, 54321)
            self.assertEqual(guard.write_fd, 11)
            mock_close.assert_called_once_with(10)  # r_fd closed
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            self.assertIn("54321", args[0])
            self.assertEqual(kwargs["stdin"], 10)
            self.assertTrue(kwargs["close_fds"])

            # Clean stop
            guard.stop()
            self.assertIsNone(guard.write_fd)
            self.assertIsNone(guard.proc)
            fake_guard_proc.terminate.assert_called_once()
            fake_guard_proc.wait.assert_called_once()

    def test_stop_handles_none_or_errors(self):
        stop_linux_death_guard(None)
        guard = LinuxDeathGuard(proc=None, write_fd=None, target_pid=123)
        guard.stop()
        self.assertIsNone(guard.proc)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux")
    def test_live_guard_terminates_target_on_pipe_close(self):
        target = subprocess.Popen(["sleep", "60"])
        guard = start_linux_death_guard(target)
        self.assertIsNotNone(guard)
        try:
            self.assertIsNone(target.poll())
            self.assertIsNone(guard.proc.poll())

            # Close write end to simulate parent death
            os.close(guard.write_fd)
            guard.write_fd = None

            # Target process should receive signal and terminate
            target.wait(timeout=3)
            self.assertIsNotNone(target.poll())
        finally:
            if target.poll() is None:
                target.kill()
            guard.stop()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux")
    def test_live_guard_exits_when_target_dies_normally(self):
        target = subprocess.Popen(["sleep", "0.1"])
        guard = start_linux_death_guard(target)
        self.assertIsNotNone(guard)
        try:
            target.wait(timeout=2)
            # Guard should detect target is gone and exit cleanly
            guard.proc.wait(timeout=3)
            self.assertEqual(guard.proc.poll(), 0)
        finally:
            guard.stop()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux")
    def test_guard_survives_launcher_thread_exit(self):
        import threading
        target = subprocess.Popen(["sleep", "60"])
        guard = None

        def launcher():
            nonlocal guard
            guard = start_linux_death_guard(target)

        t = threading.Thread(target=launcher)
        t.start()
        t.join()

        self.assertIsNotNone(guard)
        try:
            # Let worker thread finish and confirm neither target nor guard died
            time.sleep(0.3)
            self.assertIsNone(target.poll())
            self.assertIsNone(guard.proc.poll())

            # Now close write end and verify target is still terminated on pipe close
            os.close(guard.write_fd)
            guard.write_fd = None
            target.wait(timeout=3)
            self.assertIsNotNone(target.poll())
        finally:
            if target.poll() is None:
                target.kill()
            guard.stop()
