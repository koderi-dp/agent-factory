"""Session 0.3: stdlib-only Job Object prototype for reuse in plan section 3.2.

Assign the wrapper BEFORE spawning children. Keep its non-inheritable handle
alive until wrapper exit. Closing the last handle kills the wrapper too.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os


if os.name != "nt":
    raise OSError("winjob_proto requires Windows")

SIZE_T = ctypes.c_size_t
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_TERMINATE = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258


class BasicLimitInformation(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", SIZE_T),
                ("MaximumWorkingSetSize", SIZE_T),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", SIZE_T),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", SIZE_T),
                ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T),
                ("PeakJobMemoryUsed", SIZE_T)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
for name, restype, argtypes in (
    ("CreateJobObjectW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
    ("OpenJobObjectW", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]),
    ("SetInformationJobObject", wintypes.BOOL,
     [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
    ("QueryInformationJobObject", wintypes.BOOL,
     [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]),
    ("AssignProcessToJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
    ("GetCurrentProcess", wintypes.HANDLE, []),
    ("TerminateJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
    ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
    ("OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
    ("WaitForSingleObject", wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD]),
    ("QueryFullProcessImageNameW", wintypes.BOOL,
     [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]),
    ("GetHandleInformation", wintypes.BOOL,
     [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]),
):
    function = getattr(kernel32, name)
    function.restype = restype
    function.argtypes = argtypes


def checked(value: int) -> int:
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


class Job:
    def __init__(self, handle: int) -> None:
        self.handle = handle

    @classmethod
    def create(cls, name: str | None = None) -> Job:
        # NULL security attributes => the handle cannot be inherited by children.
        job = cls(checked(kernel32.CreateJobObjectW(None, name)))
        try:
            limits = ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(kernel32.SetInformationJobObject(
                job.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            job.close()
            raise
        return job

    @classmethod
    def open(cls, name: str) -> Job:
        return cls(checked(kernel32.OpenJobObjectW(
            JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE, False, name)))

    def assign_current_process(self) -> None:
        checked(kernel32.AssignProcessToJobObject(self.handle, kernel32.GetCurrentProcess()))

    def process_ids(self) -> list[int]:
        capacity = 16
        while True:
            class ProcessIdList(ctypes.Structure):
                _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                            ("ids", SIZE_T * capacity)]

            data = ProcessIdList()
            if kernel32.QueryInformationJobObject(
                    self.handle, 3, ctypes.byref(data), ctypes.sizeof(data), None):
                return list(data.ids[:data.count])
            if ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(ctypes.get_last_error())
            capacity = max(capacity * 2, data.assigned)

    def inheritable(self) -> bool:
        flags = wintypes.DWORD()
        checked(kernel32.GetHandleInformation(self.handle, ctypes.byref(flags)))
        return bool(flags.value & 1)  # HANDLE_FLAG_INHERIT

    def terminate(self, exit_code: int = 73) -> None:
        checked(kernel32.TerminateJobObject(self.handle, exit_code))

    def close(self) -> None:
        if self.handle:
            checked(kernel32.CloseHandle(self.handle))
            self.handle = 0

    def __enter__(self) -> Job:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class Process:
    """Hold a process handle across termination so PID reuse cannot fool the probe."""

    def __init__(self, pid: int) -> None:
        self.handle = checked(kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid))
        self.pid = pid

    def image(self) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        checked(kernel32.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)))
        return buffer.value

    def exited(self, timeout_ms: int = 0) -> bool:
        result = kernel32.WaitForSingleObject(self.handle, timeout_ms)
        if result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            raise ctypes.WinError(ctypes.get_last_error())
        return result == WAIT_OBJECT_0

    def close(self) -> None:
        if self.handle:
            checked(kernel32.CloseHandle(self.handle))
            self.handle = 0
