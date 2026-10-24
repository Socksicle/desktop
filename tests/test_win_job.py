"""Unit tests for Windows Job Object assignment and process bounding."""
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from utils.engines import win_job


class WinJobTest(unittest.TestCase):

    def setUp(self):
        # Reset cached singleton handle
        win_job._job_object_handle = None

    def tearDown(self):
        win_job._job_object_handle = None

    def test_noop_on_non_windows(self):
        with mock.patch.object(win_job.sys, "platform", "linux"):
            self.assertIsNone(win_job.get_engine_job_object())
            fake_proc = SimpleNamespace(pid=1234, _handle=5678)
            self.assertFalse(win_job.assign_process_to_job(fake_proc))
            self.assertFalse(win_job.assign_process_to_job(None))

    def test_job_object_creation_and_limits_on_windows(self):
        k32 = mock.MagicMock()
        k32.CreateJobObjectW.return_value = 0xAAAA
        k32.SetInformationJobObject.return_value = 1
        k32.AssignProcessToJobObject.return_value = 1

        with mock.patch.object(win_job.sys, "platform", "win32"), \
             mock.patch.object(win_job, "_get_kernel32", return_value=k32):
            h_job = win_job.get_engine_job_object()
            self.assertEqual(h_job, 0xAAAA)
            k32.CreateJobObjectW.assert_called_once_with(None, None)
            k32.SetInformationJobObject.assert_called_once()
            args = k32.SetInformationJobObject.call_args[0]
            self.assertEqual(args[0], 0xAAAA)
            self.assertEqual(args[1], win_job.JobObjectExtendedLimitInformation)

            # Assign process with existing _handle
            fake_proc = SimpleNamespace(pid=1234, _handle=0xBBBB)
            ok = win_job.assign_process_to_job(fake_proc)
            self.assertTrue(ok)
            k32.AssignProcessToJobObject.assert_called_once()
            assign_args = k32.AssignProcessToJobObject.call_args[0]
            self.assertEqual(assign_args[0], 0xAAAA)
            self.assertEqual(assign_args[1].value, 0xBBBB)

    def test_job_object_creation_failure_handled(self):
        k32 = mock.MagicMock()
        k32.CreateJobObjectW.return_value = None
        k32.GetLastError.return_value = 5

        with mock.patch.object(win_job.sys, "platform", "win32"), \
             mock.patch.object(win_job, "_get_kernel32", return_value=k32):
            self.assertIsNone(win_job.get_engine_job_object())
            fake_proc = SimpleNamespace(pid=1234, _handle=0xBBBB)
            self.assertFalse(win_job.assign_process_to_job(fake_proc))

    def test_job_object_set_information_failure_closes_handle(self):
        k32 = mock.MagicMock()
        k32.CreateJobObjectW.return_value = 0xAAAA
        k32.SetInformationJobObject.return_value = 0
        k32.GetLastError.return_value = 87

        with mock.patch.object(win_job.sys, "platform", "win32"), \
             mock.patch.object(win_job, "_get_kernel32", return_value=k32):
            self.assertIsNone(win_job.get_engine_job_object())
            k32.CloseHandle.assert_called_once_with(0xAAAA)

    def test_assign_process_without_handle_uses_open_process(self):
        k32 = mock.MagicMock()
        k32.CreateJobObjectW.return_value = 0xAAAA
        k32.SetInformationJobObject.return_value = 1
        k32.OpenProcess.return_value = 0xCCCC
        k32.AssignProcessToJobObject.return_value = 1

        with mock.patch.object(win_job.sys, "platform", "win32"), \
             mock.patch.object(win_job, "_get_kernel32", return_value=k32):
            fake_proc = SimpleNamespace(pid=1234)  # No _handle attribute
            ok = win_job.assign_process_to_job(fake_proc)
            self.assertTrue(ok)
            k32.OpenProcess.assert_called_once_with(0x0100 | 0x0001, False, 1234)
            k32.CloseHandle.assert_called_once_with(0xCCCC)
