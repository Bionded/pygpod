"""Big databases stay linear (syncDirector task 98)."""

import os
import time

import pytest

from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

SYSINFO = b"ModelNumStr: xB029\nFirewireGuid: 0x000A2700213749FF\n"


def _src(mp):
    path = os.path.join(mp, "iPod_Control", "Music", "F00", "scale.mp3")
    _make_minimal_mp3(path)
    return path


def test_ids_unique_after_interleaved_add_and_remove(writable_ipod):
    db = Database(writable_ipod)
    src = _src(writable_ipod)
    added = [db.add_track(src, copy=False, title=f"T{i}") for i in range(30)]
    db.remove_tracks(added[::3])
    db.remove_track(added[1])
    more = [db.add_track(src, copy=False, title=f"U{i}") for i in range(30)]
    ids = [t.track_id for t in db.tracks]
    dbids = [t.dbid for t in db.tracks]
    assert len(ids) == len(set(ids)) and len(dbids) == len(set(dbids))
    gone = {t.track_id for t in added[::3]} | {added[1].track_id}
    assert not gone & {t.track_id for t in more}  # removed ids are not reused in the session
    db.save()
    db = Database(writable_ipod)
    master = db.master_playlist
    assert sorted(master.track_ids) == sorted(t.track_id for t in db.tracks)
    assert not gone & set(master.track_ids)


def test_remove_tracks_from_playlists_and_files(writable_ipod):
    db = Database(writable_ipod)
    src = _src(writable_ipod)
    import shutil

    copies = []
    for i in range(4):
        dst = os.path.join(writable_ipod, "iPod_Control", "Music", "F01", f"c{i}.mp3")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(src, dst)
        copies.append(db.add_track(dst, copy=False, title=f"C{i}"))
    pl = db.create_playlist("Mix")
    for t in copies:
        db.add_track_to_playlist(pl, t)
    db.remove_tracks(copies[:2], delete_files=True)
    assert [t.title for t in pl.tracks] == ["C2", "C3"]
    assert not os.path.exists(os.path.join(writable_ipod, "iPod_Control", "Music", "F01", "c0.mp3"))
    assert os.path.exists(os.path.join(writable_ipod, "iPod_Control", "Music", "F01", "c2.mp3"))
    db.save()
    assert [t.title for t in Database(writable_ipod).get_playlist("Mix").tracks] == ["C2", "C3"]
    db.remove_tracks([])  # no-op


def _populate(writable_ipod, n):
    db = Database(writable_ipod)
    src = _src(writable_ipod)
    t = time.perf_counter()
    tracks = [db.add_track(src, copy=False, title=f"T{i}", album=f"A{i % 300}") for i in range(n)]
    db.save()
    return db, tracks, time.perf_counter() - t


@pytest.mark.slow
def test_add_and_remove_scale_linearly(tmp_path):
    from pygpod.device.mountpoint import init_ipod

    def fresh(name):
        mp = str(tmp_path / name)
        os.makedirs(mp)
        init_ipod(mp, sysinfo_files={"SysInfo": SYSINFO})
        return mp

    _, _, small = _populate(fresh("small"), 2000)
    db, tracks, big = _populate(fresh("big"), 8000)
    assert big / small < 6, (small, big)
    db2, tracks2, t10k = _populate(fresh("ten"), 10000)
    assert t10k < 30, t10k
    t = time.perf_counter()
    db2.remove_tracks(tracks2[:2500])
    db2.save()
    assert time.perf_counter() - t < 10
    assert len(Database(db2._mountpoint).tracks) == 7500
