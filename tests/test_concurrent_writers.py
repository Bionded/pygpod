"""Concurrent writers never overwrite each other silently (syncDirector task 49)."""

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
    """syncDirector task 110: B judged the dead writer's lock stale, A took it over meanwhile;
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
