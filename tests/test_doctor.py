"""Database.check() / repair() and 'pygpod doctor', on emulated volumes only."""

import hashlib
import json
import os
import subprocess

import pytest

from pygpod.cli import main
from pygpod.db.constants import MHIP_MAGIC
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3, _make_minimal_png

SYSINFO = b"ModelNumStr: xB029\nFirewireGuid: 0x000A2700213749FF\n"  # Classic: hash58
FAULTS = {
    "orphan_file",
    "missing_file",
    "empty_file",
    "size_mismatch",
    "dangling_playlist_item",
    "artwork_missing",
    "checksum_mismatch",
    "stale_lock",
    "temp_file",
}


def _tree(mp):
    """Hash of every file (path, content) below the mount point."""
    h = hashlib.sha1()
    for d, dirs, files in sorted(os.walk(mp)):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(d, name)
            h.update(os.path.relpath(path, mp).encode())
            with open(path, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _path(mp, track):
    return os.path.join(mp, *track.ipod_path.strip(":").split(":"))


@pytest.fixture
def clean(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    png = str(tmp_path / "cover.png")
    _make_minimal_png(png, 120, 120)
    db = Database(mp)
    for title in ("gone", "empty", "short", "ghost", "art", "fine"):
        db.add_track(mp3, title=title, cover_art=png if title in ("art", "fine") else None)
    t = {x.title: x for x in db.tracks}
    db.set_playlist("Mix", [t["ghost"], t["fine"]])
    db.save()
    return mp


@pytest.fixture
def broken(clean):
    mp = clean
    db = Database(mp)
    t = {x.title: x for x in db.tracks}
    # a track that vanished from the track list but is still in a playlist
    ghost_id = t["ghost"].track_id
    mhlt = db._get_or_create_mhlt()
    mhlt.children = [c for c in mhlt.children if c.fields.get("track_id") != ghost_id]
    db._tracks = [x for x in db._tracks if x.track_id != ghost_id]
    db._track_lookup.pop(ghost_id)
    master = db.master_playlist.record
    master.children = [
        c
        for c in master.children
        if not (c.magic == MHIP_MAGIC and c.fields["track_id"] == ghost_id)
    ]
    db._set_artwork_fields(t["art"].record, 1, 1234, 99999)  # no such ArtworkDB entry
    db._modified = True
    db.save(refresh_smart_playlists=False)
    os.unlink(_path(mp, t["gone"]))
    open(_path(mp, t["empty"]), "w").close()
    with open(_path(mp, t["short"]), "rb+") as f:
        f.truncate(100)
    music = os.path.join(mp, "iPod_Control", "Music", "F00")
    with open(os.path.join(music, "ORPHAN.mp3"), "wb") as f:
        f.write(b"\xff\xfb" + b"\0" * 200)
    itunes = os.path.join(mp, "iPod_Control", "iTunes")
    db_path = os.path.join(itunes, "iTunesDB")
    with open(db_path, "rb+") as f:  # break the hash58
        f.seek(0x60)
        b = f.read(1)
        f.seek(0x60)
        f.write(bytes([b[0] ^ 0xFF]))
    with open(os.path.join(itunes, "pygpod.lock"), "w") as f:
        json.dump({"pid": 1, "host": "elsewhere", "time": 0, "token": "x"}, f)
    with open(os.path.join(itunes, "iTunesDB.pygpod-new"), "wb") as f:
        f.write(b"partial")
    return mp


def test_clean_database_has_no_issues(clean, capsys):
    report = Database(clean).check()
    assert report.ok and not report and report.summary() == "no issues found"
    assert main(["-m", clean, "doctor"]) == 0
    assert main(["-m", clean, "doctor", "--no-verbose"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "no issues found"


def test_check_reports_every_injected_fault(broken):
    before = _tree(broken)
    report = Database(broken, apply_play_counts=False, recover=False).check()
    assert FAULTS <= report.kinds(), report.kinds()
    by = {i.kind: i for i in report}
    assert by["missing_file"].subject.startswith("track ")
    # the ghost track's audio file is an orphan too, so look for ORPHAN.mp3 among them
    orphans = [i.subject for i in report if i.kind == "orphan_file"]
    assert any(s.endswith("ORPHAN.mp3") for s in orphans), orphans
    assert by["dangling_playlist_item"].subject == "playlist Mix"
    assert by["artwork_missing"].image_id == 99999
    assert all(i.fixable for i in report if i.kind in FAULTS)
    assert _tree(broken) == before  # read-only


def test_doctor_lists_issues_and_changes_nothing(broken, capsys):
    before = _tree(broken)
    assert main(["-m", broken, "doctor"]) == 1
    out = capsys.readouterr().out
    for kind in FAULTS:
        assert kind in out, kind
    assert _tree(broken) == before


def test_doctor_no_verbose_prints_only_a_summary(broken, capsys):
    before = _tree(broken)
    assert main(["-m", broken, "doctor", "--no-verbose"]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and "issues found" in lines[0]
    assert _tree(broken) == before


def test_no_verbose_with_fix_and_orphans_without_fix_are_rejected(broken, capsys):
    before = _tree(broken)
    for argv in (["doctor", "--no-verbose", "--fix"], ["doctor", "--delete-orphans"]):
        with pytest.raises(SystemExit) as e:
            main(["-m", broken, *argv])
        assert e.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
    assert _tree(broken) == before


def test_repair_dry_run_changes_nothing(broken):
    before = _tree(broken)
    db = Database(broken, apply_play_counts=False, recover=False)
    actions = db.repair(dry_run=True)
    assert actions and not any(a.done for a in actions)
    assert _tree(broken) == before


def test_doctor_fix_repairs_and_keeps_orphans(broken, capsys):
    orphan = os.path.join(broken, "iPod_Control", "Music", "F00", "ORPHAN.mp3")
    assert main(["-m", broken, "doctor", "--fix"]) == 1  # the orphan is still there
    out = capsys.readouterr().out
    assert "kept (use delete_orphans" in out and os.path.isfile(orphan)
    after = Database(broken).check()
    assert after.kinds() == {"orphan_file"}
    t = {x.title for x in Database(broken).tracks}
    assert t == {"short", "art", "fine"}  # missing and empty files' tracks removed
    db = Database(broken)
    short = next(x for x in db.tracks if x.title == "short")
    assert short.file_size == 100
    art = next(x for x in db.tracks if x.title == "art")
    assert not art.has_artwork
    assert [x.title for x in db.get_playlist("Mix").tracks] == ["fine"]
    assert len(db.get_playlist("Mix").track_ids) == 1
    itunes = os.path.join(broken, "iPod_Control", "iTunes")
    assert not os.path.exists(os.path.join(itunes, "pygpod.lock"))
    assert not os.path.exists(os.path.join(itunes, "iTunesDB.pygpod-new"))

    assert main(["-m", broken, "doctor", "--fix", "--delete-orphans"]) == 0
    assert not os.path.exists(orphan)
    assert Database(broken).check().ok


def test_repair_api_returns_actions(broken):
    db = Database(broken, apply_play_counts=False, recover=False)
    report = db.check()
    actions = db.repair(report, delete_orphans=True)
    assert {a.kind for a in actions} >= FAULTS
    assert all(a.done for a in actions)
    assert Database(broken).check().ok


@pytest.mark.libgpod
def test_libgpod_reads_the_repaired_database(tool, broken):
    Database(broken, apply_play_counts=False).repair(delete_orphans=True)
    out = subprocess.run([tool, "dump", broken], capture_output=True, text=True, check=True)
    titles = sorted(ln.split("\t")[3] for ln in out.stdout.splitlines() if ln[0] == "T")
    assert titles == ["art", "fine", "short"]
    mix = [ln for ln in out.stdout.splitlines() if ln.startswith("P\tMix\t")]
    assert mix and mix[0].split("\t")[-1] == "fine"


def test_interrupted_commit_is_reported_then_completed(clean, capsys):
    itunes = os.path.join(clean, "iPod_Control", "iTunes")
    with open(os.path.join(itunes, "iTunesDB"), "rb") as f:
        db_bytes = f.read()
    with open(os.path.join(itunes, "iTunesDB.pygpod-new"), "wb") as f:
        f.write(db_bytes)
    with open(os.path.join(itunes, "pygpod.journal"), "w") as f:
        json.dump(
            {
                "replace": [
                    ["iPod_Control/iTunes/iTunesDB.pygpod-new", "iPod_Control/iTunes/iTunesDB"]
                ]
            },
            f,
        )
    before = _tree(clean)
    db = Database(clean, recover=False)
    assert "interrupted_commit" in db.check().kinds()
    assert _tree(clean) == before
    from pygpod.exceptions import DatabaseError

    with pytest.raises(DatabaseError, match="interrupted save"):
        db.save()
    assert main(["-m", clean, "doctor", "--fix"]) == 0
    assert "interrupted save completed" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(itunes, "pygpod.journal"))
    assert Database(clean).check().ok


def test_duplicate_path_and_not_in_master(clean):
    db = Database(clean)
    t = {x.title: x for x in db.tracks}
    from pygpod.db.constants import MHOD_ID_PATH

    t["short"]._set_mhod(MHOD_ID_PATH, t["fine"].ipod_path)
    master = db.master_playlist.record
    art_id = t["art"].track_id
    master.children = [
        c for c in master.children if not (c.magic == MHIP_MAGIC and c.fields["track_id"] == art_id)
    ]
    db._modified = True
    db.save(refresh_smart_playlists=False)
    report = Database(clean, recover=False).check()
    dup = report.by_kind("duplicate_path")
    assert [i.track_id for i in dup] == [t["fine"].track_id]  # the later track
    assert [i.track_id for i in report.by_kind("not_in_master")] == [art_id]
    fine_file = _path(clean, t["fine"])
    Database(clean).repair(delete_orphans=True)
    db = Database(clean)
    assert "fine" not in {x.title for x in db.tracks} and os.path.isfile(fine_file)
    assert art_id in db.master_playlist.track_ids
    assert db.check().ok


def test_unreadable_artworkdb_clears_no_artwork(clean):
    adb = os.path.join(clean, "iPod_Control", "Artwork", "ArtworkDB")
    with open(adb, "wb") as f:
        f.write(b"garbage that is not an ArtworkDB")
    report = Database(clean, recover=False).check()
    assert "artwork_db_unreadable" in report.kinds()
    assert "artwork_missing" not in report.kinds()
    Database(clean).repair(report)
    assert any(t.has_artwork for t in Database(clean).tracks)


def test_repair_refuses_while_a_writer_holds_the_lock(broken):
    from pygpod.exceptions import ConflictError

    lock = os.path.join(broken, "iPod_Control", "iTunes", "pygpod.lock")
    open(lock, "w").close()  # fresh: a live writer
    before = _tree(broken)
    db = Database(broken, apply_play_counts=False, recover=False)
    report = db.check()
    assert "write_in_progress" in report.kinds()
    with pytest.raises(ConflictError):
        db.repair(report)
    assert _tree(broken) == before


def test_recover_false_open_keeps_the_play_count_commit_marker(clean):
    marker = os.path.join(clean, "iPod_Control", "iTunes", "pygpod.playcounts-commit")
    with open(marker, "w") as f:
        json.dump(
            {"file": "Play Counts", "file_sha256": "x", "itunesdb_sha256": "y", "others": []}, f
        )
    before = _tree(clean)
    Database(clean, recover=False).check()
    assert _tree(clean) == before


def test_unsigned_video_5g_database_is_clean(tmp_path):
    """A 5G database has no hash (as libgpod/iTunes write it); doctor reports no error."""
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(
        mp,
        name="5G",
        sysinfo_files={"SysInfo": b"ModelNumStr: xA003\nFirewireGuid: 0x000A2700213749FF\n"},
    )
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    db.add_track(mp3, title="Song")
    db.save()
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        assert f.read()[0x58:0x6C] == b"\x00" * 20
    assert not Database(mp).device.requires_hash
    assert main(["-m", mp, "doctor"]) == 0


def test_unidentified_model_with_signed_db_is_reported(clean, tmp_path):
    """SysInfo without ModelNumStr on a signed Classic -> model_unidentified warning;
    the hash58 is still verified with the SysInfo GUID (valid here, broken after a byte flip)."""
    sysinfo = os.path.join(clean, "iPod_Control", "Device", "SysInfo")
    with open(sysinfo, "wb") as f:
        f.write(b"FirewireGuid: 0x000A2700213749FF\n")
    kinds = [i.kind for i in Database(clean).check()]
    assert kinds == ["model_unidentified"]

    db_path = os.path.join(clean, "iPod_Control", "iTunes", "iTunesDB")
    data = bytearray(open(db_path, "rb").read())
    data[0x58] ^= 0xFF
    with open(db_path, "wb") as f:
        f.write(bytes(data))
    kinds = [i.kind for i in Database(clean, recover=False).check()]
    assert kinds == ["model_unidentified", "checksum_mismatch"]
