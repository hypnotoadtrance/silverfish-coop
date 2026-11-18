"""Windows job ownership for the staged game process."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess


class BasicLimits(ctypes.Structure):
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


class IoCounters(ctypes.Structure):
    _fields_ = [(field, ctypes.c_uint64) for field in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimits),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel_call(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


kernel = ctypes.WinDLL("kernel32", use_last_error=True)
create_job = _kernel_call(kernel, "CreateJobObjectW", wintypes.HANDLE,
                          wintypes.LPVOID, wintypes.LPCWSTR)
set_information = _kernel_call(kernel, "SetInformationJobObject", wintypes.BOOL,
                               wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
open_process = _kernel_call(kernel, "OpenProcess", wintypes.HANDLE,
                            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
assign_process = _kernel_call(kernel, "AssignProcessToJobObject", wintypes.BOOL,
                              wintypes.HANDLE, wintypes.HANDLE)
close_handle = _kernel_call(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_TERMINATE_AND_SET_QUOTA = 0x0001 | 0x0100


class GameProcess(subprocess.Popen):
    """Terminate the staged game and its children if this launcher exits."""

    def __init__(self, *args, **kwargs):
        self._job = create_job(None, None)
        if not self._job:
            raise ctypes.WinError(ctypes.get_last_error())
        started = False
        try:
            settings = ExtendedLimits()
            settings.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not set_information(self._job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                   ctypes.byref(settings), ctypes.sizeof(settings)):
                raise ctypes.WinError(ctypes.get_last_error())
            super().__init__(*args, **kwargs)
            started = True
            process_handle = open_process(PROCESS_TERMINATE_AND_SET_QUOTA, False, self.pid)
            if not process_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not assign_process(self._job, process_handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                close_handle(process_handle)
        except BaseException:
            self.close_job()
            if started and self.poll() is None:
                self.terminate()
                self.wait(timeout=10)
            raise

    def close_job(self) -> None:
        if self._job:
            close_handle(self._job)
            self._job = None
