"""Play counts are consumed only after a durable save (no loss, no double count)."""

import os

import pytest

from pygpod.db.playcounts import PLAY_COUNTS_COMMIT_FILE
from pygpod.model.database import Database

from .test_playcount_merge import _write_play_counts_file


def _itunes(mp):
    return os.path.join(mp, "iPod_Control", "iTunes")


@pytest.fixture
def ipod(writable_ipod):
    """Writable iPod with a fresh DB (no play count files) and its baseline counts."""
    for fname in ("Play Counts", "iTunesStats", "PlayCounts.plist", PLAY_COUNTS_COMMIT_FILE):
        path = os.path.join(_itunes(writable_ipod), fname)
        if os.path.exists(path):
            os.unlink(path)
    db = Database(writable_ipod)
    db.save()
    return writable_ipod, [t.play_count for t in db.tracks]


def _counts(mp):
    return [t.play_count for t in Database(mp, apply_play_counts=False).tracks]


def test_abandoned_session_keeps_file_and_applies_once(ipod):
    mp, base = ipod
    pc = _write_play_counts_file(_itunes(mp), [{"play_count": 3}] * len(base))

    db = Database(mp)  # merged in memory, never saved
    assert db.tracks[0].play_count == base[0] + 3
    del db
    assert os.path.isfile(pc)

    db = Database(mp)
    assert db.tracks[0].play_count == base[0] + 3  # not lost, not doubled
    db.save()
    assert not os.path.isfile(pc)
    assert not os.path.exists(os.path.join(_itunes(mp), PLAY_COUNTS_COMMIT_FILE))
    assert _counts(mp) == [c + 3 for c in base]


def test_no_apply_leaves_tracks_and_exposes_pending(ipod):
    mp, base = ipod
    entries = [{"play_count": 0}] * len(base)
    entries[0] = {
        "play_count": 2,
        "time_played": 3700000000,
        "rating": 80,
        "bookmark_time": 1234,
        "skip_count": 1,
    }
    pc = _write_play_counts_file(_itunes(mp), entries)

    db = Database(mp, apply_play_counts=False)
    assert db.tracks[0].play_count == base[0]
    assert db._modified is False
    pending = db.pending_play_counts()
    assert len(pending) == 1
    p = pending[0]
    assert p.track is db.tracks[0]
    assert (p.play_count, p.time_played, p.rating, p.bookmark_time, p.skip_count) == (
        2,
        3700000000,
        80,
        1234,
        1,
    )

    db.save()  # nothing merged or cleared: the file stays
    assert os.path.isfile(pc)

    assert db.merge_play_counts() == 1
    assert db.pending_play_counts() == []
    assert db.tracks[0].play_count == base[0] + 2
    db.save()
    assert not os.path.isfile(pc)
    assert _counts(mp)[0] == base[0] + 2


def test_clear_drops_counts_after_save(ipod):
    mp, base = ipod
    pc = _write_play_counts_file(_itunes(mp), [{"play_count": 4}] * len(base))
    db = Database(mp, apply_play_counts=False)
    db.clear_play_counts()
    assert db.pending_play_counts() == []
    assert os.path.isfile(pc)
    db.save()
    assert not os.path.isfile(pc)
    assert _counts(mp) == base


def test_failed_save_keeps_file_and_retry_applies_once(ipod, monkeypatch):
    mp, base = ipod
    pc = _write_play_counts_file(_itunes(mp), [{"play_count": 5}] * len(base))
    db = Database(mp)

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "_write_db_file", staticmethod(boom))
    with pytest.raises(OSError):
        db.save()
    monkeypatch.undo()
    assert os.path.isfile(pc)

    # A fresh session after the failure: the stale marker must not suppress the counts.
    assert _counts(mp) == base
    db2 = Database(mp)
    assert db2.tracks[0].play_count == base[0] + 5
    db2.save()
    assert _counts(mp) == [c + 5 for c in base]

    # Retrying on the original object also works (counts applied once in its memory).
    assert not os.path.isfile(pc)


def test_retry_save_on_same_object(ipod, monkeypatch):
    mp, base = ipod
    pc = _write_play_counts_file(_itunes(mp), [{"play_count": 1}] * len(base))
    db = Database(mp)
    calls = []
    real = Database._write_db_file

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("transient")
        real(path, data)

    monkeypatch.setattr(Database, "_write_db_file", staticmethod(flaky))
    with pytest.raises(OSError):
        db.save()
    db.save()
    assert not os.path.isfile(pc)
    assert _counts(mp) == [c + 1 for c in base]


def test_crash_between_commit_and_cleanup_no_double_count(ipod, monkeypatch):
    mp, base = ipod
    pc = _write_play_counts_file(_itunes(mp), [{"play_count": 2}] * len(base))
    db = Database(mp)
    monkeypatch.setattr(Database, "_finish_play_count_cleanup", lambda self: None)
    db.save()  # iTunesDB written, "crash" before the file is deleted
    monkeypatch.undo()
    assert os.path.isfile(pc)
    assert os.path.exists(os.path.join(_itunes(mp), PLAY_COUNTS_COMMIT_FILE))

    db2 = Database(mp)
    assert [t.play_count for t in db2.tracks] == [c + 2 for c in base]
    assert db2.pending_play_counts() == []
    assert not os.path.isfile(pc)  # cleanup finished on open
    assert not os.path.exists(os.path.join(_itunes(mp), PLAY_COUNTS_COMMIT_FILE))


def test_file_rewritten_after_commit_is_applied(ipod, monkeypatch):
    mp, base = ipod
    _write_play_counts_file(_itunes(mp), [{"play_count": 2}] * len(base))
    db = Database(mp)
    monkeypatch.setattr(Database, "_finish_play_count_cleanup", lambda self: None)
    db.save()
    monkeypatch.undo()
    # The iPod was played again before the next sync and rewrote the file.
    _write_play_counts_file(_itunes(mp), [{"play_count": 7}] * len(base))
    db2 = Database(mp)
    assert [t.play_count for t in db2.tracks] == [c + 2 + 7 for c in base]


def test_file_rewritten_during_session_is_kept(ipod):
    mp, base = ipod
    _write_play_counts_file(_itunes(mp), [{"play_count": 2}] * len(base))
    db = Database(mp)
    pc = _write_play_counts_file(_itunes(mp), [{"play_count": 9}] * len(base))
    db.save()
    assert os.path.isfile(pc)  # not the file that was merged


def test_unrelated_files_untouched(ipod):
    mp, base = ipod
    itunes = _itunes(mp)
    _write_play_counts_file(itunes, [{"play_count": 1}] * len(base))
    others = {}
    for name, data in (("OTGPlaylistInfo", b"mhpo\x14\x00\x00\x00"), ("notes.txt", b"x")):
        path = os.path.join(itunes, name)
        with open(path, "wb") as f:
            f.write(data)
        others[path] = data
    Database(mp).save()
    assert not os.path.isfile(os.path.join(itunes, "Play Counts"))
    for path, data in others.items():
        with open(path, "rb") as f:
            assert f.read() == data


def _write_stats(itunes, n, plays=2):
    stats = os.path.join(itunes, "iTunesStats")
    with open(stats, "w") as f:
        f.write(f"{n}\n" + f"{plays}\n0\n0\n0\n0\n\n" * n)
    return stats


def _write_plist(itunes):
    path = os.path.join(itunes, "PlayCounts.plist")
    with open(path, "wb") as f:
        f.write(b'<?xml version="1.0"?><plist version="1.0"><dict></dict></plist>')
    return path


def test_all_play_count_files_deleted_after_save(ipod):
    """Like libgpod: every variant goes after a save, not only the one that was read (task 96)."""
    mp, base = ipod
    itunes = _itunes(mp)
    pc = _write_play_counts_file(itunes, [{"play_count": 1}] * len(base))
    stats = _write_stats(itunes, len(base))
    plist = _write_plist(itunes)
    db = Database(mp)
    assert db.tracks[0].play_count == base[0] + 1  # only Play Counts is applied
    db.save()
    for path in (pc, stats, plist):
        assert not os.path.isfile(path)
    assert not os.path.exists(os.path.join(itunes, PLAY_COUNTS_COMMIT_FILE))
    assert _counts(mp) == [c + 1 for c in base]


def test_all_play_count_files_kept_without_save(ipod, monkeypatch):
    mp, base = ipod
    itunes = _itunes(mp)
    paths = [
        _write_play_counts_file(itunes, [{"play_count": 1}] * len(base)),
        _write_stats(itunes, len(base)),
        _write_plist(itunes),
    ]
    Database(mp)  # abandoned session
    assert all(os.path.isfile(p) for p in paths)

    db = Database(mp)

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "_write_db_file", staticmethod(boom))
    with pytest.raises(OSError):
        db.save()
    assert all(os.path.isfile(p) for p in paths)


def test_lower_priority_file_rewritten_during_session_is_kept(ipod):
    mp, base = ipod
    itunes = _itunes(mp)
    pc = _write_play_counts_file(itunes, [{"play_count": 1}] * len(base))
    _write_stats(itunes, len(base), plays=2)
    plist = _write_plist(itunes)
    db = Database(mp)
    stats = _write_stats(itunes, len(base), plays=5)  # rewritten after open: not seen
    db.save()
    assert not os.path.isfile(pc)
    assert not os.path.isfile(plist)
    assert os.path.isfile(stats)


def test_file_created_during_session_is_kept(ipod):
    mp, base = ipod
    itunes = _itunes(mp)
    _write_play_counts_file(itunes, [{"play_count": 1}] * len(base))
    db = Database(mp)
    stats = _write_stats(itunes, len(base))
    db.save()
    assert os.path.isfile(stats)


def test_crash_during_cleanup_deletes_other_variants_on_open(ipod, monkeypatch):
    mp, base = ipod
    itunes = _itunes(mp)
    pc = _write_play_counts_file(itunes, [{"play_count": 2}] * len(base))
    stats = _write_stats(itunes, len(base))
    db = Database(mp)
    monkeypatch.setattr(Database, "_finish_play_count_cleanup", lambda self: None)
    db.save()
    monkeypatch.undo()
    assert os.path.isfile(pc) and os.path.isfile(stats)

    db2 = Database(mp)
    assert [t.play_count for t in db2.tracks] == [c + 2 for c in base]
    assert not os.path.isfile(pc)
    assert not os.path.isfile(stats)
    assert not os.path.exists(os.path.join(itunes, PLAY_COUNTS_COMMIT_FILE))
