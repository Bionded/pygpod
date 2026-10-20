"""Writer lock for an iPod database (one writer at a time).

``iPod_Control/iTunes/pygpod.lock`` is created exclusively (O_CREAT | O_EXCL) for the duration
of a save and holds ``{"pid", "host", "time", "token"}``. A lock is stale - and removed - when its
process is gone on this host or it is older than ``STALE_AFTER_S`` (a writer on another machine
that crashed; a save takes seconds). Limits: FAT has no ``flock``/advisory locks, so this only
coordinates pygpod writers; the exclusive create is atomic on local file systems, a network or
FUSE mount may not guarantee it. Taking over a stale lock renames it aside first (atomic) and
checks it is still the lock judged stale; a writer only ever deletes the lock with its own token.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from ..exceptions import ConflictError

LOCK_NAME = "pygpod.lock"
STALE_AFTER_S = 600.0


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive_windows(pid: int) -> bool:
    # os.kill(pid, 0) on Windows is TerminateProcess(pid, 0): it would kill the lock holder
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information, error_access_denied, still_active = 0x1000, 5, 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active  # an exited process lingers while handles are open
    finally:
        kernel32.CloseHandle(handle)


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _stale(path: str, data: bytes | None = None) -> bool:
    """Was the lock at ``path`` (content ``data``, if already read) left behind by a dead writer?"""
    if data is None:
        data = _read(path)
    try:
        info = json.loads(data or b"")
        pid, host, started = int(info["pid"]), str(info["host"]), float(info["time"])
    except (ValueError, KeyError, TypeError):
        # unreadable: a writer crashed between create and write, or garbage; judge by age
        try:
            return time.time() - os.path.getmtime(path) > STALE_AFTER_S
        except OSError:
            return True
    if time.time() - started > STALE_AFTER_S:
        return True
    return host == socket.gethostname() and not _alive(pid)


def _take_over(path: str, seen: bytes) -> bool:
    """Remove the stale lock whose content was ``seen``. Atomic against other takers: the lock is
    renamed away first (only one rename of it can succeed) and deleted only if it is still the lock
    judged stale; another writer's fresh lock that got there in between is put back (False)."""
    aside = f"{path}.stale-{uuid.uuid4().hex}"
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return True  # already gone: try to create it
    if _read(aside) == seen:
        os.unlink(aside)
        return True
    try:
        os.rename(aside, path)  # not ours to remove
    except OSError:
        pass
    return False


def _owned(path: str, token: str) -> bool:
    try:
        return json.loads(_read(path) or b"").get("token") == token
    except (ValueError, AttributeError):
        return False


@contextmanager
def writer_lock(itunes_dir: str) -> Iterator[None]:
    """Hold the database writer lock; ConflictError if another live writer has it."""
    path = os.path.join(itunes_dir, LOCK_NAME)
    busy = ConflictError(f"the iPod database is being written by another process ({path})")
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            seen = _read(path)
            if seen is not None and _stale(path, seen) and _take_over(path, seen):
                continue
            if seen is None:
                continue  # removed meanwhile
            raise busy from None
        break
    else:
        raise ConflictError(f"could not take the database lock {path}")
    token = uuid.uuid4().hex
    info = {"pid": os.getpid(), "host": socket.gethostname(), "time": time.time(), "token": token}
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
        f.flush()
        os.fsync(f.fileno())
    if not _owned(path, token):
        raise busy  # taken over between create and write (judged garbage by age): not ours
    try:
        yield
    finally:
        if _owned(path, token):  # never remove another writer's lock
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
