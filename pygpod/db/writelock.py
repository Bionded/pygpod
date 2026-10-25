"""Writer lock for an iPod database (one writer at a time).

``iPod_Control/iTunes/pygpod.lock`` is created exclusively (O_CREAT | O_EXCL) for the duration
of a save and holds ``{"pid", "host", "time", "token"}``, on Linux also the holder's boot id and
process start ticks (``"boot"``, ``"start"``). The holder touches the file every ``HEARTBEAT_S``
seconds while it holds it (a restore may copy music for an hour). A lock is stale - and removed -
when its process is gone on this host (or its pid now belongs to another process: a different start
tick count or boot id; these do not move with the wall clock, so a clock step after boot cannot make
a live writer look stale), or, for a writer on another machine, when it was not touched
for ``STALE_AFTER_S`` (that writer crashed). A live local writer is never taken over. Limits: FAT
has no ``flock``/advisory locks, so this only coordinates pygpod writers; the exclusive create is
atomic on local file systems, a network or FUSE mount may not guarantee it. Taking over a stale lock
renames it aside first (atomic) and checks it is still the lock judged stale; a writer only ever
deletes the lock with its own token.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from typing import Iterator, Optional

from ..exceptions import ConflictError

LOCK_NAME = "pygpod.lock"
STALE_AFTER_S = 600.0
HEARTBEAT_S = 60.0
# How long opening a database waits for a running save before it reads without recovering
OPEN_WAIT_S = 5.0


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


def _start_ticks(pid: object = "self") -> Optional[int]:
    """Start time of process ``pid`` in clock ticks since boot (Linux /proc), None if unknown."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return int(f.read().rsplit(b")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _boot_id() -> Optional[str]:
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
            return f.read().strip() or None
    except (OSError, ValueError):
        return None


def _same_process(pid: int, boot: str, start: int) -> Optional[bool]:
    """Is ``pid`` still the process that wrote a lock with ``boot`` / ``start``? None when this
    cannot be told here (no /proc)."""
    current = _boot_id()
    if current is None:
        return None
    if current != boot:
        return False  # written before a reboot: its process is gone
    ticks = _start_ticks(pid)
    return None if ticks is None else ticks == start


def _started_after(pid: int, when: float) -> bool:
    """Did the process ``pid`` start after ``when`` (so it reused a crashed writer's pid)?
    False when that cannot be told (only Linux' /proc is consulted)."""
    ticks = _start_ticks(pid)
    if ticks is None:
        return False
    try:
        with open("/proc/stat", "rb") as f:
            btime = next(int(line.split()[1]) for line in f if line.startswith(b"btime "))
        started = btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration, AttributeError):
        return False
    return started > when + 2.0  # btime is whole seconds


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
    if host == socket.gethostname():
        if not _alive(pid):
            return True
        boot, start = info.get("boot"), info.get("start")
        if isinstance(boot, str) and isinstance(start, int):
            same = _same_process(pid, boot, start)
            if same is not None:
                return not same
        # a lock without them (older pygpod) or no /proc: compare wall-clock start times
        return _started_after(pid, started)
    try:
        touched = max(started, os.path.getmtime(path))
    except OSError:
        touched = started
    return time.time() - touched > STALE_AFTER_S


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


def writer_active(itunes_dir: str) -> bool:
    """Does a live writer hold the lock? Reads only (works on a read-only mount)."""
    path = os.path.join(itunes_dir, LOCK_NAME)
    data = _read(path)
    return data is not None and not _stale(path, data)


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
    boot, start = _boot_id(), _start_ticks()
    if boot is not None and start is not None:
        info["boot"], info["start"] = boot, start
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
        f.flush()
        os.fsync(f.fileno())
    if not _owned(path, token):
        raise busy  # taken over between create and write (judged garbage by age): not ours
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(HEARTBEAT_S):
            try:
                os.utime(path)
            except OSError:
                pass

    beat = threading.Thread(target=heartbeat, name="pygpod-lock-heartbeat", daemon=True)
    beat.start()
    try:
        yield
    finally:
        stop.set()
        beat.join()
        if _owned(path, token):  # never remove another writer's lock
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


@contextmanager
def try_writer_lock(itunes_dir: str, wait: Optional[float] = None) -> Iterator[bool]:
    """Hold the writer lock if possible: yields True while held, False when another live writer
    still has it after ``wait`` seconds (default ``OPEN_WAIT_S``) or the lock cannot be created
    (a read-only mount)."""
    deadline = time.monotonic() + (OPEN_WAIT_S if wait is None else wait)
    stack = ExitStack()
    while True:
        try:
            stack.enter_context(writer_lock(itunes_dir))
            held = True
        except ConflictError:
            if time.monotonic() < deadline:
                time.sleep(0.1)
                continue
            held = False
        except OSError:
            held = False
        break
    with stack:
        yield held
