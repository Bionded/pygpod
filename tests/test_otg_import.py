"""On-The-Go playlists: numbered files, import, cleanup with the save."""

import os
import struct

import pytest

from pygpod.db.transaction import Transaction
from pygpod.model.database import Database


def _itunes(mp):
    return os.path.join(mp, "iPod_Control", "iTunes")


def _otg(mp, name, indices, order="<"):
    magic = b"mhpo" if order == "<" else b"ophm"
    data = magic + struct.pack(f"{order}III", 0x14, 4, len(indices)) + b"\0" * 4
    data += b"".join(struct.pack(f"{order}I", i) for i in indices)
    path = os.path.join(_itunes(mp), name)
    with open(path, "wb") as f:
        f.write(data)
    return path


@pytest.fixture
def ipod(writable_ipod):
    for n in os.listdir(_itunes(writable_ipod)):
        if n.startswith("OTGPlaylistInfo"):
            os.unlink(os.path.join(_itunes(writable_ipod), n))
    db = Database(writable_ipod)
    assert len(db.tracks) >= 3
    return writable_ipod, [t.title for t in db.tracks]


def _names(db):
    return [p.name for p in db.playlists]


def test_numbered_files_imported_once_and_cleaned_by_save(ipod):
    mp, titles = ipod
    files = [
        _otg(mp, "OTGPlaylistInfo", [2, 0]),
        _otg(mp, "OTGPlaylistInfo_1", [1], order=">"),
        _otg(mp, "OTGPlaylistInfo_2", []),
    ]
    _otg(mp, "OTGPlaylistInfo_4", [0])  # after a gap: not read (libgpod stops at the first missing)
    db = Database(mp)
    assert [[t.title for t in pl] for pl in db.pending_otg_playlists()] == [
        [titles[2], titles[0]],
        [titles[1]],
    ]
    created = db.import_otg_playlists()
    assert [p.name for p in created] == ["OTG Playlist 1", "OTG Playlist 2"]
    assert [t.title for t in created[0].tracks] == [titles[2], titles[0]]
    assert db.import_otg_playlists() == []  # no duplicates on a second call

    # reopen without saving: nothing happened on the iPod, import works again (no duplicate stored)
    assert all(os.path.isfile(f) for f in files)
    db2 = Database(mp)
    assert "OTG Playlist 1" not in _names(db2)
    db.save()
    assert not any(os.path.isfile(f) for f in files)
    assert os.path.isfile(os.path.join(_itunes(mp), "OTGPlaylistInfo_4"))
    db3 = Database(mp)
    assert _names(db3).count("OTG Playlist 1") == 1 and db3.pending_otg_playlists() == []


def _file(mp, track):
    from pygpod.utils.encoding import ipod_path_to_os

    return ipod_path_to_os(track.ipod_path, mp)


def test_bad_entries_left_out_valid_ones_imported(ipod, caplog):
    """Design: drop missing/broken entries, log them, keep the rest."""
    mp, titles = ipod
    db = Database(mp)
    t = db.tracks
    os.unlink(_file(mp, t[1]))  # missing media
    open(_file(mp, t[2]), "wb").close()  # empty
    assert len(t) >= 4
    with open(_file(mp, t[3]), "r+b") as f:  # truncated copy
        f.truncate(t[3].file_size // 2)
    bad = _otg(mp, "OTGPlaylistInfo", [0, 9999, 1, 2, 3])
    good = _otg(mp, "OTGPlaylistInfo_1", [0])
    allbad = _otg(mp, "OTGPlaylistInfo_2", [1, 9999])

    db = Database(mp)
    with caplog.at_level("WARNING"):
        assert [[x.title for x in pl] for pl in db.pending_otg_playlists()] == [
            [titles[0]],
            [titles[0]],
        ]
    skipped = db.otg_skipped_entries()
    reasons = [(e.file, e.position, e.index, e.reason) for e in skipped]
    half = t[3].file_size // 2
    assert reasons[:4] == [
        ("OTGPlaylistInfo", 1, 9999, "no such track"),
        ("OTGPlaylistInfo", 2, 1, "file missing"),
        ("OTGPlaylistInfo", 3, 2, "file is empty"),
        ("OTGPlaylistInfo", 4, 3, f"file truncated ({half} of {t[3].file_size} bytes)"),
    ]
    assert ("OTGPlaylistInfo_2", 0, 1, "file missing") in reasons
    assert "OTGPlaylistInfo entry 3 (track 1) left out: file missing" in caplog.text
    assert len(caplog.records) == len(skipped)  # each bad entry logged once

    created = db.import_otg_playlists()
    assert [p.name for p in created] == ["OTG Playlist 1", "OTG Playlist 2"]
    assert [x.title for x in created[0].tracks] == [titles[0]]
    assert db.import_otg_playlists() == []
    assert len(db.otg_skipped_entries()) == len(skipped)  # not logged again
    db.save()
    # every file consumed, including the one without a valid entry; no media file deleted
    assert not any(os.path.isfile(f) for f in (bad, good, allbad))
    assert os.path.isfile(_file(mp, t[2]))
    db2 = Database(mp)
    assert _names(db2).count("OTG Playlist 1") == 1 and "OTG Playlist 3" not in _names(db2)
    assert db2.pending_otg_playlists() == [] and db2.import_otg_playlists() == []


def test_removed_track_left_out(ipod):
    mp, titles = ipod
    _otg(mp, "OTGPlaylistInfo", [0, 1])
    db = Database(mp)
    db.remove_track(db.tracks[1])
    assert [[x.title for x in pl] for pl in db.pending_otg_playlists()] == [[titles[0]]]
    assert db.otg_skipped_entries()[0].reason == "track was removed"


def test_partial_import_failed_save_retry(ipod, monkeypatch):
    mp, titles = ipod
    path = _otg(mp, "OTGPlaylistInfo", [0, 9999, 1])
    db = Database(mp)
    db.import_otg_playlists()

    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(Transaction, "commit", boom)
    with pytest.raises(OSError):
        db.save()
    monkeypatch.undo()
    assert os.path.isfile(path) and "OTG Playlist 1" not in _names(Database(mp))
    db.save()  # retry on the same object
    assert not os.path.isfile(path)
    db2 = Database(mp)
    pls = [p for p in db2.playlists if p.name == "OTG Playlist 1"]
    assert len(pls) == 1 and [x.title for x in pls[0].tracks] == [titles[0], titles[1]]


def test_failed_save_keeps_files_and_clear_drops_them(ipod, monkeypatch):
    mp, _ = ipod
    path = _otg(mp, "OTGPlaylistInfo", [0])
    db = Database(mp)
    db.import_otg_playlists()

    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(Transaction, "commit", boom)
    with pytest.raises(OSError):
        db.save()
    monkeypatch.undo()
    assert os.path.isfile(path)
    assert "OTG Playlist 1" not in _names(Database(mp))

    db = Database(mp)
    db.clear_otg_playlists()
    db.save()
    assert not os.path.isfile(path) and "OTG Playlist 1" not in _names(Database(mp))


def test_file_rewritten_after_read_is_kept(ipod):
    mp, _ = ipod
    _otg(mp, "OTGPlaylistInfo", [0])
    db = Database(mp)
    db.import_otg_playlists()
    path = _otg(mp, "OTGPlaylistInfo", [0, 1])  # the iPod made another OTG list meanwhile
    db.save()
    assert os.path.isfile(path)
