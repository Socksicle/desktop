"""Windows Job Object management for bounding engine process lifecycles.

On Windows, child processes outlive a crashing or task-killed parent unless
assigned to a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
"""
import ctypes
from ctypes import wintypes
import logging
import sys

log = logging.getLogger("engine.win_job")

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryLimit", ctypes.c_size_t),
        ("PeakJobMemoryLimit", ctypes.c_size_t),
    ]


_job_object_handle = None


def _get_kernel32():
    kernel32 = ctypes.windll.kernel32
    if hasattr(kernel32, "CreateJobObjectW"):
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    if hasattr(kernel32, "SetInformationJobObject"):
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
    if hasattr(kernel32, "AssignProcessToJobObject"):
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    if hasattr(kernel32, "OpenProcess"):
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
    if hasattr(kernel32, "CloseHandle"):
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    if hasattr(kernel32, "GetLastError"):
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
    return kernel32


def get_engine_job_object():
    """Return a singleton Win32 Job Object configured to kill children on close.

    On non-Windows, returns None.
    On Windows, the job handle is held by the Python process; when Python
    terminates (normally, crash, OOM, or killed via Task Manager), the kernel
    closes the handle, terminating all child processes assigned to the job.
    """
    global _job_object_handle
    if sys.platform != "win32":
        return None
    if _job_object_handle is not None:
        return _job_object_handle
    try:
        k32 = _get_kernel32()
        h_job = k32.CreateJobObjectW(None, None)
        if not h_job:
            log.warning("Failed to create Win32 Job Object: error %s", k32.GetLastError())
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            h_job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            log.warning("Failed to set Job Object limits: error %s", k32.GetLastError())
            k32.CloseHandle(h_job)
            return None
        _job_object_handle = h_job
        return _job_object_handle
    except Exception as e:
        log.warning("Exception initializing Windows Job Object: %s", e)
        return None


def assign_process_to_job(proc) -> bool:
    """Assign a subprocess.Popen instance to the killer Job Object on Windows."""
    if sys.platform != "win32" or proc is None:
        return False
    h_job = get_engine_job_object()
    if not h_job:
        return False
    try:
        k32 = _get_kernel32()
        h_proc = getattr(proc, "_handle", None)
        opened = False
        if not h_proc:
            h_proc = k32.OpenProcess(0x0100 | 0x0001, False, int(proc.pid))
            opened = True
        if not h_proc:
            return False
        try:
            ok = k32.AssignProcessToJobObject(h_job, wintypes.HANDLE(int(h_proc)))
            if not ok:
                log.debug("AssignProcessToJobObject failed for pid %s: %s",
                          getattr(proc, "pid", None), k32.GetLastError())
            return bool(ok)
        finally:
            if opened:
                k32.CloseHandle(h_proc)
    except Exception as e:
        log.debug("Error assigning process to job object: %s", e)
        return False
