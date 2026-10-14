"""Native Windows process inspection for Ubersight, using only ``ctypes``.

Ubersight needs three facts about Windows processes, which it reads from
``/proc`` on Linux:

- whether a background-job writer's PID is still running;
- that process's creation time, which guards against PID reuse; and
- the parent links that form a ``--process-root`` subtree.

This module gets them from ``kernel32``: Toolhelp snapshots,
``OpenProcess``, ``GetProcessTimes``, ``WaitForSingleObject``, and
``GetExitCodeProcess``. It imports cleanly on every platform, so tests can
exercise the pure helpers anywhere. The Win32 calls themselves run only on
Windows and report ``unsupported`` elsewhere.

If this inspection proves fragile or too limited, for example when full
command lines are needed, the approved fallback is a Windows-only ``psutil``
dependency behind these same functions.
"""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

# Win32 constants used by the calls below.
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
MAX_PATH = 260

# FILETIME counts 100 ns intervals from 1601-01-01 UTC; the Unix epoch is
# this many seconds later.
FILETIME_EPOCH_OFFSET_SECONDS = 11_644_473_600
FILETIME_TICKS_PER_SECOND = 10_000_000

PROCESS_RUNNING = "running"
PROCESS_EXITED = "exited"
PROCESS_MISSING = "missing"
PROCESS_INACCESSIBLE = "inaccessible"
PROCESS_UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ProcessProbe:
    """Result of probing one Windows process ID.

    ``state`` is one of ``running``, ``exited``, ``missing``,
    ``inaccessible``, or ``unsupported``. ``creation_filetime`` holds the
    creation time as FILETIME ticks when Windows reported it.
    """

    state: str
    creation_filetime: int | None = None


@dataclass(frozen=True)
class ProcessEntry:
    """One row from a Toolhelp process snapshot."""

    pid: int
    ppid: int
    exe_name: str


class _FileTime(ctypes.Structure):
    """Win32 ``FILETIME``: a 64-bit tick count split into two DWORDs."""

    _fields_ = [
        ("low", ctypes.c_uint32),
        ("high", ctypes.c_uint32),
    ]

    def ticks(self) -> int:
        """Return the combined 64-bit FILETIME tick count."""

        return (self.high << 32) | self.low


class _ProcessEntry32W(ctypes.Structure):
    """Win32 ``PROCESSENTRY32W`` as filled by ``Process32FirstW``/``NextW``."""

    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


_KERNEL32: Any | None = None


def is_supported() -> bool:
    """Return whether native Windows process inspection can run here."""

    return os.name == "nt" and hasattr(ctypes, "WinDLL")


def _kernel32() -> Any:
    """Return ``kernel32`` with explicit signatures, loading it once.

    Explicit ``restype`` declarations matter: the ctypes default of ``int``
    would truncate 64-bit handles.
    """

    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p
    dword = ctypes.c_uint32
    boolean = ctypes.c_int32
    kernel32.OpenProcess.argtypes = [dword, boolean, dword]
    kernel32.OpenProcess.restype = handle
    kernel32.CloseHandle.argtypes = [handle]
    kernel32.CloseHandle.restype = boolean
    kernel32.GetProcessTimes.argtypes = [
        handle,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = boolean
    kernel32.WaitForSingleObject.argtypes = [handle, dword]
    kernel32.WaitForSingleObject.restype = dword
    kernel32.GetExitCodeProcess.argtypes = [handle, ctypes.POINTER(dword)]
    kernel32.GetExitCodeProcess.restype = boolean
    kernel32.CreateToolhelp32Snapshot.argtypes = [dword, dword]
    kernel32.CreateToolhelp32Snapshot.restype = handle
    kernel32.Process32FirstW.argtypes = [handle, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32FirstW.restype = boolean
    kernel32.Process32NextW.argtypes = [handle, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.restype = boolean
    _KERNEL32 = kernel32
    return kernel32


def _invalid_handle_value() -> int | None:
    """Return ``INVALID_HANDLE_VALUE`` as ctypes reports a ``c_void_p``."""

    return ctypes.c_void_p(-1).value


def _creation_filetime(kernel32: Any, handle: Any) -> int | None:
    """Return a process handle's creation time in FILETIME ticks."""

    creation = _FileTime()
    exited = _FileTime()
    kernel_time = _FileTime()
    user_time = _FileTime()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exited),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        return None
    return creation.ticks()


def probe_process(pid: int) -> ProcessProbe:
    """Report whether ``pid`` is running and when it was created.

    The first attempt also requests ``SYNCHRONIZE`` so a zero-timeout wait
    can tell a running process from an exited one. Some processes refuse
    that right, so after access denied the probe retries with query rights
    alone and checks for the ``STILL_ACTIVE`` exit code instead.
    """

    if pid <= 0:
        return ProcessProbe(PROCESS_MISSING)
    if not is_supported():
        return ProcessProbe(PROCESS_UNSUPPORTED)
    try:
        kernel32 = _kernel32()
    except (AttributeError, OSError):
        return ProcessProbe(PROCESS_UNSUPPORTED)

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    can_wait = True
    if not handle:
        error = ctypes.get_last_error()
        if error == ERROR_INVALID_PARAMETER:
            return ProcessProbe(PROCESS_MISSING)
        if error != ERROR_ACCESS_DENIED:
            return ProcessProbe(PROCESS_INACCESSIBLE)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        can_wait = False
        if not handle:
            if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:
                return ProcessProbe(PROCESS_MISSING)
            return ProcessProbe(PROCESS_INACCESSIBLE)
    try:
        creation = _creation_filetime(kernel32, handle)
        if can_wait:
            wait_result = kernel32.WaitForSingleObject(handle, 0)
            if wait_result == WAIT_OBJECT_0:
                return ProcessProbe(PROCESS_EXITED, creation)
            if wait_result == WAIT_TIMEOUT:
                return ProcessProbe(PROCESS_RUNNING, creation)
            return ProcessProbe(PROCESS_INACCESSIBLE, creation)
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return ProcessProbe(PROCESS_INACCESSIBLE, creation)
        # A process that really exited with code 259 looks running here.
        # That rare false positive only delays stale-file cleanup.
        if exit_code.value == STILL_ACTIVE:
            return ProcessProbe(PROCESS_RUNNING, creation)
        return ProcessProbe(PROCESS_EXITED, creation)
    finally:
        kernel32.CloseHandle(handle)


def process_creation_filetime(pid: int) -> int | None:
    """Return a live process's creation time in FILETIME ticks, if readable."""

    probe = probe_process(pid)
    if probe.state != PROCESS_RUNNING:
        return None
    return probe.creation_filetime


def current_filetime(now_seconds: float | None = None) -> int:
    """Return the current UTC time, or ``now_seconds``, as FILETIME ticks."""

    seconds = time.time() if now_seconds is None else now_seconds
    return int((seconds + FILETIME_EPOCH_OFFSET_SECONDS) * FILETIME_TICKS_PER_SECOND)


def filetime_age_seconds(
    creation_filetime: int | None, now_filetime: int
) -> float | None:
    """Return seconds elapsed since ``creation_filetime``, never negative."""

    if creation_filetime is None:
        return None
    return max(0.0, (now_filetime - creation_filetime) / FILETIME_TICKS_PER_SECOND)


def snapshot_processes() -> tuple[ProcessEntry, ...]:
    """Return every process visible in one Toolhelp snapshot."""

    if not is_supported():
        return ()
    try:
        kernel32 = _kernel32()
    except (AttributeError, OSError):
        return ()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _invalid_handle_value():
        return ()
    entries: list[ProcessEntry] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            entries.append(
                ProcessEntry(
                    pid=int(entry.th32ProcessID),
                    ppid=int(entry.th32ParentProcessID),
                    exe_name=str(entry.szExeFile),
                )
            )
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return tuple(entries)


def build_process_tree(
    root_pid: int,
    entries: Iterable[ProcessEntry],
    creation_time: Callable[[int], int | None],
) -> tuple[ProcessEntry, ...]:
    """Return ``root_pid`` and its descendants in depth-first PID order.

    Windows records the creator's PID but keeps no live parent link, so a
    child can name a PID that has since been reused by an unrelated process.
    A child created before its claimed parent therefore cannot belong to
    that parent and is excluded. Unknown creation times are accepted, since
    they cannot disprove the link.
    """

    by_pid: dict[int, ProcessEntry] = {}
    children_by_parent: dict[int, list[int]] = {}
    for entry in entries:
        by_pid[entry.pid] = entry
        if entry.pid != entry.ppid:
            children_by_parent.setdefault(entry.ppid, []).append(entry.pid)
    if root_pid not in by_pid:
        return ()
    created: dict[int, int | None] = {}

    def creation_of(pid: int) -> int | None:
        if pid not in created:
            created[pid] = creation_time(pid)
        return created[pid]

    ordered: list[ProcessEntry] = []
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        ordered.append(by_pid[pid])
        parent_created = creation_of(pid)
        children = []
        for child_pid in sorted(children_by_parent.get(pid, [])):
            child_created = creation_of(child_pid)
            if (
                parent_created is not None
                and child_created is not None
                and child_created < parent_created
            ):
                continue
            children.append(child_pid)
        stack.extend(reversed(children))
    return tuple(ordered)
