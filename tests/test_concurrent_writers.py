"""Concurrent writers never overwrite each other silently."""

import json
import os
import socket
import subprocess
import sys
import time

import pytest

from pygpod.db import writelock
from pygpod.exceptions import ConflictError
from pygpod.model.database import Database


def _lock(mp):
    return os.path.join(mp, "iPod_Control", "iTunes", writelock.LOCK_NAME)


def test_second_instance_conflicts_after_first_saved(writable_ipod):
    a, b = Database(writable_ipod), Database(writable_ipod)
    a.tracks[0].title = "From A"
    a.save()
    b.tracks[0].title = "From B"
    with pytest.raises(ConflictError):
        b.save()
    assert Database(writable_ipod).tracks[0].title == "From A"  # the newer database is kept
    a.tracks[0].title = "A again"  # the first writer may keep saving: it wrote the current DB
    a.save()
    b.save(force=True)  # explicit overwrite
    assert Database(writable_ipod).tracks[0].title == "From B"
    assert not os.path.exists(_lock(writable_ipod))


def test_other_process_save_detected(writable_ipod):
    db = Database(writable_ipod)
    code = (
        "import sys; from pygpod.model.database import Database\n"
        "d = Database(sys.argv[1]); d.tracks[0].title = 'Other process'; d.save()\n"
    )
    subprocess.run([sys.executable, "-c", code, writable_ipod], check=True)
    db.tracks[0].title = "Mine"
    with pytest.raises(ConflictError):
        db.save()
    assert Database(writable_ipod).tracks[0].title == "Other process"


def test_live_lock_refuses_and_stale_lock_is_recovered(writable_ipod, monkeypatch):
    db = Database(writable_ipod)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with open(_lock(writable_ipod), "w") as f:
            json.dump({"pid": holder.pid, "host": socket.gethostname(), "time": time.time()}, f)
        with pytest.raises(ConflictError, match="another process"):
            db.save()
    finally:
        holder.kill()
        holder.wait()
    db.save()  # the holder is gone: stale lock removed, save works
    assert not os.path.exists(_lock(writable_ipod))

    with open(_lock(writable_ipod), "w") as f:  # another host, too old
        json.dump({"pid": 1, "host": "elsewhere", "time": time.time() - 3600}, f)
    os.utime(_lock(writable_ipod), (time.time() - 3600,) * 2)  # and not touched since
    db.save()
    with open(_lock(writable_ipod), "w") as f:  # another host, recent: cannot tell, refuse
        json.dump({"pid": 1, "host": "elsewhere", "time": time.time()}, f)
    with pytest.raises(ConflictError):
        db.save()
    with open(_lock(writable_ipod), "w") as f:  # garbage written by a crashed writer, old
        f.write("{")
    old = time.time() - 3600
    os.utime(_lock(writable_ipod), (old, old))
    db.save()


def test_lock_released_when_save_fails(writable_ipod, monkeypatch):
    db = Database(writable_ipod)

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "_write_db_file", staticmethod(boom))
    with pytest.raises(OSError):
        db.save()
    assert not os.path.exists(_lock(writable_ipod))


def _dead_lock(path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    data = json.dumps({"pid": dead.pid, "host": socket.gethostname(), "time": time.time()})
    with open(path, "w") as f:
        f.write(data)
    return data.encode()


def test_two_takers_of_a_stale_lock_only_one_wins(tmp_path):
    """B judged the dead writer's lock stale, A took it over meanwhile;
    B's takeover must not remove A's fresh lock."""
    path = str(tmp_path / writelock.LOCK_NAME)
    seen_by_b = _dead_lock(path)
    assert writelock._stale(path, seen_by_b)  # B's judgement
    with writelock.writer_lock(str(tmp_path)):  # A takes over and holds the lock
        a_lock = open(path, "rb").read()
        assert writelock._take_over(path, seen_by_b) is False  # B finds A's lock, not the stale one
        assert open(path, "rb").read() == a_lock  # A's lock is back in place
        assert not [n for n in os.listdir(tmp_path) if ".stale-" in n]
        with pytest.raises(ConflictError):  # and B's writer_lock is refused
            with writelock.writer_lock(str(tmp_path)):
                pass
    assert not os.path.exists(path)


def test_release_never_removes_another_writers_lock(tmp_path):
    path = str(tmp_path / writelock.LOCK_NAME)
    with writelock.writer_lock(str(tmp_path)):
        other = json.dumps({"pid": 1, "host": "elsewhere", "time": time.time(), "token": "x"})
        with open(path, "w") as f:  # our lock was judged stale and replaced by another writer
            f.write(other)
    assert open(path).read() == other


def test_stale_takeover_by_rename(tmp_path):
    path = str(tmp_path / writelock.LOCK_NAME)
    _dead_lock(path)
    with writelock.writer_lock(str(tmp_path)):
        assert json.load(open(path))["pid"] == os.getpid()
    assert os.listdir(tmp_path) == []


def test_deleted_itunesdb_is_a_conflict(writable_ipod):
    db = Database(writable_ipod)
    db.tracks[0].title = "Червона рута"
    path = os.path.join(writable_ipod, "iPod_Control", "iTunes", "iTunesDB")
    os.unlink(path)
    with pytest.raises(ConflictError, match="deleted"):
        db.save()
    assert not os.path.exists(path)
    db.save(force=True)
    assert Database(writable_ipod).tracks[0].title == "Червона рута"


def _write_lock(path, pid, host, started, touched=None):
    with open(path, "w") as f:
        json.dump({"pid": pid, "host": host, "time": started, "token": "x"}, f)
    if touched is not None:
        os.utime(path, (touched, touched))


def test_live_local_writer_is_never_taken_over(tmp_path):
    path = str(tmp_path / writelock.LOCK_NAME)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.1)
        long_ago = time.time() - 2 * writelock.STALE_AFTER_S
        _write_lock(path, holder.pid, socket.gethostname(), time.time(), touched=long_ago)
        assert not writelock._stale(path)  # e.g. a restore copying music for an hour
        with pytest.raises(ConflictError):
            with writelock.writer_lock(str(tmp_path)):
                pass
    finally:
        holder.kill()
        holder.wait()
    assert writelock._stale(path)  # its process is gone


def test_reused_pid_of_a_crashed_local_writer_is_stale(tmp_path):
    path = str(tmp_path / writelock.LOCK_NAME)
    if not os.path.exists("/proc/self/stat"):
        pytest.skip("process start times are only read from /proc")
    # the pid now belongs to this test process, which started long after the lock was taken
    _write_lock(path, os.getpid(), socket.gethostname(), time.time() - 30 * 24 * 3600)
    assert writelock._stale(path)


_LINUX_PROC = os.path.exists("/proc/self/stat") and os.path.exists(
    "/proc/sys/kernel/random/boot_id"
)


@pytest.mark.skipif(not _LINUX_PROC, reason="process identity is only read from /proc")
def test_live_writer_survives_a_clock_step(tmp_path):
    # a forward clock step (e.g. a board without RTC syncing after boot) makes the lock's wall-clock
    # time older than the holder's start computed from btime; the holder must still count as alive
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    path = str(tmp_path / writelock.LOCK_NAME)
    try:
        assert holder.stdout.readline().strip() == "held"
        with open(path) as f:
            info = json.load(f)
        assert info["pid"] == holder.pid and isinstance(info["start"], int) and info["boot"]
        info["time"] -= 3600
        with open(path, "w") as f:
            json.dump(info, f)
        assert not writelock._stale(path)
        with pytest.raises(ConflictError):
            with writelock.writer_lock(str(tmp_path)):
                pass
    finally:
        holder.kill()
        holder.wait()
    assert writelock._stale(path)
    with writelock.writer_lock(str(tmp_path)):
        pass


@pytest.mark.skipif(not _LINUX_PROC, reason="process identity is only read from /proc")
def test_lock_identity_decides_over_wall_clock(tmp_path):
    path = str(tmp_path / writelock.LOCK_NAME)
    me, boot = writelock._start_ticks(), writelock._boot_id()

    def write(**extra):
        info = {"pid": os.getpid(), "host": socket.gethostname(), "time": 0.0, "token": "x"}
        info.update(extra)
        with open(path, "w") as f:
            json.dump(info, f)

    write(boot=boot, start=me)  # this very process, lock time far in the past
    assert not writelock._stale(path)
    write(boot=boot, start=me - 1)  # the pid was reused by another process
    assert writelock._stale(path)
    write(boot="00000000-0000-0000-0000-000000000000", start=me)  # written before a reboot
    assert writelock._stale(path)
    write()  # a lock of an older version: wall-clock rule, this process started after time 0
    assert writelock._stale(path)


HOLD_LOCK = (
    "import sys, time\n"
    "from pygpod.db import writelock\n"
    "with writelock.writer_lock(sys.argv[1]):\n"
    "    print('held', flush=True)\n"
    "    time.sleep(60)\n"
)


def test_remote_lock_is_stale_only_without_heartbeat(tmp_path):
    path = str(tmp_path / writelock.LOCK_NAME)
    long_ago = time.time() - 2 * writelock.STALE_AFTER_S
    _write_lock(path, 1, "elsewhere", long_ago, touched=time.time())  # still touched
    assert not writelock._stale(path)
    _write_lock(path, 1, "elsewhere", long_ago, touched=long_ago)
    assert writelock._stale(path)


def test_holder_refreshes_its_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(writelock, "HEARTBEAT_S", 0.05)
    path = str(tmp_path / writelock.LOCK_NAME)
    with writelock.writer_lock(str(tmp_path)):
        old = time.time() - 3600
        os.utime(path, (old, old))
        deadline = time.time() + 5
        while os.path.getmtime(path) < old + 10 and time.time() < deadline:
            time.sleep(0.05)
        assert os.path.getmtime(path) > time.time() - 60
    assert not os.path.exists(path)


def test_restore_keeps_its_lock_fresh(writable_ipod, tmp_path, monkeypatch):
    import pygpod

    archive = str(tmp_path / "резервна.tar")
    pygpod.backup(writable_ipod, archive)
    monkeypatch.setattr(writelock, "HEARTBEAT_S", 0.02)
    seen = []
    real = writelock.os.utime

    def utime(path, *args, **kw):
        if str(path).endswith(writelock.LOCK_NAME):
            seen.append(path)
        return real(path, *args, **kw)

    from pygpod import backup_archive

    real_restore_files = backup_archive._restore_files

    def slow_restore_files(*args, **kw):
        time.sleep(0.2)  # a long copy
        return real_restore_files(*args, **kw)

    monkeypatch.setattr(writelock.os, "utime", utime)
    monkeypatch.setattr(backup_archive, "_restore_files", slow_restore_files)
    pygpod.restore(archive, writable_ipod)
    assert seen
