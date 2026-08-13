"""Windows kill-on-close Job Object containment for owned native process trees."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import TracebackType
from typing import Any

import psutil

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
THREAD_SUSPEND_RESUME = 0x0002
RESUME_THREAD_FAILED = 0xFFFFFFFF
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operations", ctypes.c_uint64),
        ("write_operations", ctypes.c_uint64),
        ("other_operations", ctypes.c_uint64),
        ("read_transfer", ctypes.c_uint64),
        ("write_transfer", ctypes.c_uint64),
        ("other_transfer", ctypes.c_uint64),
    ]


class BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_int64),
        ("per_job_user_time_limit", ctypes.c_int64),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", BasicLimitInformation),
        ("io_info", IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class WindowsKillOnCloseJob:
    """Own one Windows native process tree and kill it when this handle closes."""

    def __init__(self, kernel32: Any | None = None) -> None:
        if kernel32 is None:
            loader = getattr(ctypes, "WinDLL", None)
            if loader is None:
                raise OSError("Windows Job Objects are unavailable on this platform")
            kernel32 = loader("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        self._configure_api()
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            self._raise_last_error("CreateJobObjectW")
        limits = ExtendedLimitInformation()
        limits.basic_limit_information.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            self.close()
            self._raise_last_error("SetInformationJobObject")

    def _configure_api(self) -> None:
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenThread.restype = wintypes.HANDLE
        self._kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self._kernel32.ResumeThread.restype = wintypes.DWORD
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def assign_and_resume(self, pid: int) -> None:
        """Assign a suspended process before allowing its first thread to execute."""
        process_handle = self._kernel32.OpenProcess(
            PROCESS_TERMINATE | PROCESS_SET_QUOTA,
            False,
            pid,
        )
        if not process_handle:
            self._raise_last_error("OpenProcess")
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
                self._raise_last_error("AssignProcessToJobObject")
        finally:
            self._kernel32.CloseHandle(process_handle)

        try:
            threads = psutil.Process(pid).threads()
        except psutil.Error as error:
            raise OSError(f"Unable to identify suspended Windows process thread {pid}: {error}") from error
        if not threads:
            raise OSError(f"Suspended Windows process {pid} has no resumable thread")
        thread_handle = self._kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, threads[0].id)
        if not thread_handle:
            self._raise_last_error("OpenThread")
        try:
            if self._kernel32.ResumeThread(thread_handle) == RESUME_THREAD_FAILED:
                self._raise_last_error("ResumeThread")
        finally:
            self._kernel32.CloseHandle(thread_handle)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> WindowsKillOnCloseJob:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
        code = int(get_last_error())
        raise OSError(code, f"{operation} failed")
