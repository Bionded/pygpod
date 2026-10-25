"""Backup and restore of an iPod to/from a tar archive, on emulated volumes only."""

import io
import json
import os
import shutil
import subprocess
import tarfile

import pytest

import pygpod
from pygpod.backup_archive import MANIFEST_NAME, BackupError, read_manifest, verify
from pygpod.cli import main
from pygpod.device.mountpoint import init_ipod
from pygpod.hash.hash58 import compute_hash58
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3, _make_minimal_png

GUID = "000A27001A2B3C4D"
OTHER_GUID = "000A2700FFEEDDCC"


def _sysinfo(guid):
    return f"ModelNumStr: xC293\nFirewireGuid: 0x{guid}\n".encode()  # Classic 7G: hash58


def _snapshot(mp):
    """{relative path: bytes} of every file below the mount point."""
    out = {}
    for d, _, files in os.walk(mp):
        for name in files:
            path = os.path.join(d, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, mp)] = f.read()
    return out


def _ipod(tmp_path, name="ipod", guid=GUID):
    mp = str(tmp_path / name)
    os.makedirs(mp)
    init_ipod(mp, name="Мій iPod", sysinfo_files={"SysInfo": _sysinfo(guid)})
    return mp


@pytest.fixture
def full_ipod(tmp_path):
    """A Classic 7G with two tracks (one with artwork), a playlist and an iTunesPrefs file."""
    mp = _ipod(tmp_path)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    png = str(tmp_path / "cover.png")
    _make_minimal_png(png, 120, 120)
    db = Database(mp)
    a = db.add_track(mp3, title="Без бою", artist="Океан Ельзи", cover_art=png)
    b = db.add_track(mp3, title="Човен", artist="Океан Ельзи")
    db.set_playlist("Найкраще", [b, a])
    db.save()
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesPrefs"), "wb") as f:
        f.write(b"frpd" + bytes(range(60)))
    return mp


def _wipe_and_init(mp, guid=GUID):
    shutil.rmtree(mp)
    os.makedirs(mp)
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": _sysinfo(guid)})


@pytest.mark.parametrize("suffix", [".tar", ".tar.gz", ".tar.xz"])
def test_backup_wipe_init_restore_is_byte_identical(full_ipod, tmp_path, suffix):
    before = _snapshot(full_ipod)
    archive = str(tmp_path / f"ipod{suffix}")
    calls = []
    info = pygpod.backup(full_ipod, archive, progress=lambda *a: calls.append(a))
    assert info.firewire_guid == GUID and info.model == "C293" and info.include_media
    assert info.pygpod_version == pygpod.__version__ and len(info.db_id) == 16
    assert calls[-1][0] == calls[-1][1] == info.total_size
    assert any("/Music/" in f.path for f in info.files)
    assert any(f.path.endswith(".ithmb") for f in info.files)
    assert read_manifest(archive) == info

    _wipe_and_init(full_ipod)
    result = pygpod.restore(archive, full_ipod)
    assert not result.resigned and not result.dry_run and result.skipped == []
    assert result.written[-1] == "iPod_Control/iTunes/iTunesDB"  # the database last
    after = _snapshot(full_ipod)
    for rel, data in before.items():
        assert after.get(rel) == data, rel
    assert not [p for p in after if p.endswith(".pygpod-restore")]
    db = Database(full_ipod)
    assert sorted(t.title for t in db.tracks) == ["Без бою", "Човен"]
    assert [t.title for t in db.get_playlist("Найкраще").tracks] == ["Човен", "Без бою"]


def test_database_only_backup_and_restore(full_ipod, tmp_path):
    full = str(tmp_path / "full.tar")
    pygpod.backup(full_ipod, full)
    db_only = str(tmp_path / "db.tar")
    info = pygpod.backup(full_ipod, db_only, include_media=False)
    assert not info.include_media
    assert not [f for f in info.files if f.path.startswith("iPod_Control/Music/")]
    assert "iPod_Control/iTunes/iTunesDB" in {f.path for f in info.files}

    before = _snapshot(full_ipod)
    _wipe_and_init(full_ipod)
    result = pygpod.restore(full, full_ipod, include_media=False)
    assert result.skipped and all(p.startswith("iPod_Control/Music/") for p in result.skipped)
    after = _snapshot(full_ipod)
    music = [p for p in before if p.startswith(os.path.join("iPod_Control", "Music") + os.sep)]
    assert music and not [p for p in music if p in after]
    db_file = os.path.join("iPod_Control", "iTunes", "iTunesDB")
    assert after[db_file] == before[db_file]


def _rewrite(archive, dest, change):
    """Copy a tar archive, passing every (TarInfo, bytes) through change()."""
    with tarfile.open(archive) as src, tarfile.open(dest, "w") as out:
        for m in src:
            data = src.extractfile(m).read()
            m, data = change(m, data)
            m.size = len(data)
            out.addfile(m, io.BytesIO(data))
    return dest


def test_corrupted_archive_aborts_without_touching_the_target(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)

    def flip(m, data):
        if m.name.startswith("iPod_Control/Music/"):
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        return m, data

    bad = _rewrite(archive, str(tmp_path / "bad.tar"), flip)
    target = _ipod(tmp_path, "target")
    before = _snapshot(target)
    with pytest.raises(BackupError, match="corrupted"):
        pygpod.restore(bad, target)
    assert _snapshot(target) == before

    truncated = str(tmp_path / "truncated.tar.gz")
    pygpod.backup(full_ipod, truncated)
    with open(truncated, "rb+") as f:
        f.truncate(os.path.getsize(truncated) // 2)
    with pytest.raises(BackupError):
        pygpod.restore(truncated, target)
    assert _snapshot(target) == before


def test_unsafe_or_unlisted_entries_are_refused(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    target = _ipod(tmp_path, "target")
    before = _snapshot(target)

    def escape(m, data):
        if m.name.endswith("iTunesPrefs"):
            m.name = "iPod_Control/../../evil"
        return m, data

    bad = _rewrite(archive, str(tmp_path / "bad.tar"), escape)
    with pytest.raises(BackupError, match="unexpected entry"):
        pygpod.restore(bad, target)

    def drop_manifest_entry(m, data):
        if m.name == MANIFEST_NAME:
            manifest = json.loads(data)
            manifest["files"] = manifest["files"][1:]
            data = json.dumps(manifest).encode()
        return m, data

    bad = _rewrite(archive, str(tmp_path / "bad2.tar"), drop_manifest_entry)
    with pytest.raises(BackupError, match="differ from the manifest"):
        pygpod.restore(bad, target)
    plain = str(tmp_path / "plain.tar")
    with tarfile.open(plain, "w") as t:
        t.add(os.path.join(full_ipod, "iPod_Control", "iTunes", "iTunesDB"), "iPod_Control/x")
    with pytest.raises(BackupError, match="not a pygpod backup"):
        pygpod.restore(plain, target)
    assert _snapshot(target) == before


def test_dry_run_writes_nothing(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar.gz")
    info = pygpod.backup(full_ipod, archive)
    target = _ipod(tmp_path, "target")
    before = _snapshot(target)
    result = pygpod.restore(archive, target, dry_run=True)
    assert result.dry_run and result.bytes_written == 0
    assert sorted(result.written) == sorted(f.path for f in info.files)
    assert _snapshot(target) == before


def test_other_ipod_refused_without_force_and_resigned_with_it(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    other = _ipod(tmp_path, "other", guid=OTHER_GUID)
    before = _snapshot(other)
    with pytest.raises(BackupError, match="another iPod"):
        pygpod.restore(archive, other)
    assert _snapshot(other) == before

    result = pygpod.restore(archive, other, force=True)
    assert result.resigned
    assert "iPod_Control/Device/SysInfo" in result.skipped
    sysinfo = os.path.join(other, "iPod_Control", "Device", "SysInfo")
    with open(sysinfo, "rb") as f:
        assert f.read() == _sysinfo(OTHER_GUID)  # the target keeps its identity
    with open(os.path.join(other, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        data = f.read()
    assert compute_hash58(data, OTHER_GUID) == data  # signed for the target
    assert compute_hash58(data, GUID) != data
    assert sorted(t.title for t in Database(other).tracks) == ["Без бою", "Човен"]


def test_not_enough_space(full_ipod, tmp_path, monkeypatch):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    target = _ipod(tmp_path, "target")
    usage = shutil.disk_usage(target)
    monkeypatch.setattr(
        "pygpod.backup_archive.shutil.disk_usage",
        lambda p: usage._replace(free=1000),
    )
    before = _snapshot(target)
    with pytest.raises(BackupError, match="not enough free space"):
        pygpod.restore(archive, target)
    assert _snapshot(target) == before


def test_backup_errors(full_ipod, tmp_path):
    with pytest.raises(BackupError, match="no iPod_Control"):
        pygpod.backup(str(tmp_path), str(tmp_path / "x.tar"))
    with pytest.raises(BackupError, match="unknown archive type"):
        pygpod.backup(full_ipod, str(tmp_path / "x.zip"))
    journal = os.path.join(full_ipod, "iPod_Control", "iTunes", "pygpod.journal")
    with open(journal, "w") as f:
        f.write("{}")
    with pytest.raises(BackupError, match="interrupted save"):
        pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    os.unlink(journal)
    lock = os.path.join(full_ipod, "iPod_Control", "iTunes", "pygpod.lock")
    with open(lock, "w") as f:  # left behind by a crashed writer
        json.dump({"pid": 1, "host": "elsewhere", "time": 0, "token": "x"}, f)
    info = pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    assert not [f for f in info.files if f.path.endswith("pygpod.lock")]
    assert os.path.exists(lock)  # a backup never removes or creates anything on the iPod
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".pygpod-backup-")]


def _make_read_only(mp):
    for d, _, files in os.walk(mp):
        for name in files:
            os.chmod(os.path.join(d, name), 0o444)
        os.chmod(d, 0o555)


def _make_writable(mp):
    for d, _, files in os.walk(mp):
        os.chmod(d, 0o755)
        for name in files:
            os.chmod(os.path.join(d, name), 0o644)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_backup_and_read_only_checks_work_on_a_read_only_ipod(full_ipod, tmp_path, capsys):
    before = _snapshot(full_ipod)
    _make_read_only(full_ipod)
    try:
        archive = str(tmp_path / "ro.tar.gz")
        info = pygpod.backup(full_ipod, archive)
        assert verify(archive).files == info.files
        assert pygpod.restore(archive, full_ipod, dry_run=True).dry_run
        assert main(["-m", full_ipod, "doctor"]) == 0, capsys.readouterr()
        assert main(["-m", full_ipod, "backup", str(tmp_path / "cli.tar")]) == 0
        assert _snapshot(full_ipod) == before  # nothing created or changed
    finally:
        _make_writable(full_ipod)
    target = _ipod(tmp_path, "target")
    pygpod.restore(archive, target)
    assert _snapshot(target) == before


def test_backup_on_a_file_system_refusing_writes(full_ipod, tmp_path, monkeypatch):
    real_open = os.open
    ipod = os.path.realpath(full_ipod)

    def ro_open(path, flags, *a, **kw):
        if os.path.realpath(path).startswith(ipod) and flags & (os.O_WRONLY | os.O_RDWR):
            raise OSError(30, "Read-only file system", path)
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(os, "open", ro_open)
    before = _snapshot(full_ipod)
    info = pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    assert info.files and _snapshot(full_ipod) == before


def test_backup_waits_for_a_live_writer_then_goes_on(full_ipod, tmp_path, monkeypatch, caplog):
    import socket

    from pygpod import backup_archive

    monkeypatch.setattr(backup_archive, "_WRITER_WAIT_S", 0.3)
    lock = os.path.join(full_ipod, "iPod_Control", "iTunes", "pygpod.lock")
    import time

    held = {"pid": os.getpid(), "host": socket.gethostname(), "time": time.time(), "token": "t"}
    with open(lock, "w") as f:  # this live process holds the writer lock
        json.dump(held, f)
    before = _snapshot(full_ipod)
    with caplog.at_level("WARNING", logger="pygpod.backup_archive"):
        info = pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    assert "writer lock" in caplog.text
    assert _snapshot(full_ipod) == before  # the lock is left alone
    archived = {f.path[len("iPod_Control/") :]: f.size for f in info.files}
    assert archived["iTunes/iTunesDB"] == len(
        before[os.path.join("iPod_Control", "iTunes", "iTunesDB")]
    )

    os.unlink(lock)  # a writer that finishes while the backup waits: no warning
    with open(lock, "w") as f:
        json.dump(held, f)
    real_sleep = time.sleep
    monkeypatch.setattr(backup_archive, "_WRITER_WAIT_S", 5.0)
    monkeypatch.setattr(
        backup_archive.time,
        "sleep",
        lambda s: (os.path.exists(lock) and os.unlink(lock), real_sleep(0)),
    )
    caplog.clear()
    with caplog.at_level("WARNING", logger="pygpod.backup_archive"):
        pygpod.backup(full_ipod, str(tmp_path / "y.tar"))
    assert "writer lock" not in caplog.text


def test_backup_is_made_again_when_the_database_changes(full_ipod, tmp_path, monkeypatch):
    from pygpod import backup_archive

    real = backup_archive._write_backup
    db_path = os.path.join(full_ipod, "iPod_Control", "iTunes", "iTunesDB")
    calls, stamp = [], [0]

    def changing(*a):
        calls.append(1)
        info = real(*a)
        if len(calls) <= changing.times:  # a save lands while the files are archived
            stamp[0] += 1
            os.utime(db_path, ns=(1, stamp[0] * 10**9))
        return info

    changing.times = 1
    monkeypatch.setattr(backup_archive, "_write_backup", changing)
    archive = str(tmp_path / "x.tar")
    pygpod.backup(full_ipod, archive)
    assert len(calls) == 2 and verify(archive)

    calls.clear()
    changing.times = 99
    with pytest.raises(BackupError, match="kept changing"):
        pygpod.backup(full_ipod, str(tmp_path / "y.tar"))
    assert len(calls) == backup_archive._BACKUP_ATTEMPTS
    assert not os.path.exists(str(tmp_path / "y.tar"))


def test_cli_backup_and_restore(full_ipod, tmp_path, capsys):
    archive = str(tmp_path / "ipod.tar.gz")
    assert main(["-m", full_ipod, "backup", archive]) == 0
    assert "Backup written" in capsys.readouterr().out
    target = _ipod(tmp_path, "target")
    before = _snapshot(target)
    assert main(["-m", target, "restore", archive, "--dry-run"]) == 0
    assert "Would restore" in capsys.readouterr().out
    assert _snapshot(target) == before
    other = _ipod(tmp_path, "other", guid=OTHER_GUID)
    assert main(["-m", other, "restore", archive]) == 1
    assert "another iPod" in capsys.readouterr().err
    assert main(["-m", target, "restore", archive, "--no-media"]) == 0
    out = capsys.readouterr().out
    assert "Restored" in out and "Skipped" in out
    db_only = str(tmp_path / "db.tar")
    assert main(["-m", full_ipod, "backup", db_only, "--no-media"]) == 0
    assert "database only" in capsys.readouterr().out


@pytest.mark.libgpod
def test_libgpod_reads_the_restored_database(tool, full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    other = _ipod(tmp_path, "other", guid=OTHER_GUID)
    pygpod.restore(archive, other, force=True)
    out = subprocess.run([tool, "dump", other], capture_output=True, text=True, check=True)
    titles = sorted(line.split("\t")[3] for line in out.stdout.splitlines() if line[0] == "T")
    assert titles == ["Без бою", "Човен"]


def test_restore_refuses_a_target_with_an_interrupted_save(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    journal = os.path.join(full_ipod, "iPod_Control", "iTunes", "pygpod.journal")
    with open(journal, "w") as f:
        json.dump({"replace": []}, f)
    before = _snapshot(full_ipod)
    with pytest.raises(BackupError, match="interrupted save"):
        pygpod.restore(archive, full_ipod)
    assert _snapshot(full_ipod) == before


def test_restore_respects_a_live_writer(full_ipod, tmp_path):
    from pygpod.exceptions import ConflictError

    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    lock = os.path.join(full_ipod, "iPod_Control", "iTunes", "pygpod.lock")
    open(lock, "w").close()  # fresh, unreadable: a writer is starting
    before = _snapshot(full_ipod)
    with pytest.raises(ConflictError):
        pygpod.restore(archive, full_ipod)
    assert _snapshot(full_ipod) == before


def test_position_indexed_files_not_in_the_archive_are_moved_aside(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    itunes = os.path.join(full_ipod, "iPod_Control", "iTunes")
    for name in ("Play Counts", "OTGPlaylistInfo"):
        with open(os.path.join(itunes, name), "wb") as f:
            f.write(b"recorded after the backup")
    result = pygpod.restore(archive, full_ipod)
    for name in ("Play Counts", "OTGPlaylistInfo"):
        assert not os.path.exists(os.path.join(itunes, name))
        with open(os.path.join(itunes, name + ".pre-restore"), "rb") as f:
            assert f.read() == b"recorded after the backup"
    assert any("Play Counts" in s for s in result.skipped)


def test_failed_restore_leaves_no_partial_files(full_ipod, tmp_path, monkeypatch):
    import pygpod.backup_archive as ba

    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    target = _ipod(tmp_path, "target")
    before = _snapshot(target)
    real = ba._copy_member
    calls = []

    def failing(src, path, *a):
        calls.append(path)
        if len(calls) == 3:
            with open(path, "wb") as f:
                f.write(b"half")
            raise OSError(28, "No space left on device")
        return real(src, path, *a)

    monkeypatch.setattr(ba, "_copy_member", failing)
    with pytest.raises(OSError):
        pygpod.restore(archive, target)
    after = _snapshot(target)
    assert not [p for p in after if p.endswith((".pygpod-new", ".pygpod-restore"))]
    db_file = os.path.join("iPod_Control", "iTunes", "iTunesDB")
    assert after[db_file] == before[db_file]  # database files: all or none


@pytest.fixture
def photo_ipod(full_ipod, tmp_path):
    """full_ipod with two photos in an album (<mp>/Photos) and a full resolution original."""
    pytest.importorskip("PIL")
    from PIL import Image

    photos = pygpod.PhotoDB(full_ipod)
    album = photos.create_album("Карпати")
    for name, size in (("Говерла.jpg", (800, 600)), ("Ґанок.png", (300, 500))):
        path = str(tmp_path / name)
        Image.new("RGB", size, (40, 120, 60)).save(path)
        photos.add_photo_to_album(album, photos.add_photo(path))
    photos.save()
    full_res = os.path.join(full_ipod, "Photos", "Full Resolution", "2026")
    os.makedirs(full_res)
    with open(os.path.join(full_res, "Говерла.jpg"), "wb") as f:
        f.write(b"\xff\xd8 original \xff\xd9")
    return full_ipod


def test_photos_are_backed_up_and_restored(photo_ipod, tmp_path):
    before = _snapshot(photo_ipod)
    assert any(p.startswith("Photos" + os.sep) and p.endswith(".ithmb") for p in before)
    archive = str(tmp_path / "ipod.tar.gz")
    info = pygpod.backup(photo_ipod, archive)
    names = {f.path for f in info.files}
    assert "Photos/Photo Database" in names
    assert "Photos/Full Resolution/2026/Говерла.jpg" in names
    assert read_manifest(archive).files == info.files
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile(MANIFEST_NAME))
    assert manifest["format_version"] == 2

    _wipe_and_init(photo_ipod)
    result = pygpod.restore(archive, photo_ipod)
    assert result.written[-1] == "iPod_Control/iTunes/iTunesDB"
    assert _snapshot(photo_ipod) == before  # Photos included, byte-identical
    album = pygpod.PhotoDB(photo_ipod).album_by_name("Карпати")
    assert album is not None and album.photo_count == 2


def test_database_only_backup_keeps_photo_thumbnails_not_originals(photo_ipod, tmp_path):
    archive = str(tmp_path / "db.tar")
    names = {f.path for f in pygpod.backup(photo_ipod, archive, include_media=False).files}
    assert "Photos/Photo Database" in names and any(n.endswith(".ithmb") for n in names)
    assert not [n for n in names if "Full Resolution" in n or "/Music/" in n]
    full = str(tmp_path / "full.tar")
    pygpod.backup(photo_ipod, full)
    result = pygpod.restore(full, photo_ipod, include_media=False, dry_run=True)
    assert "Photos/Full Resolution/2026/Говерла.jpg" in result.skipped


def test_version_1_archive_still_restores(full_ipod, tmp_path):
    archive = str(tmp_path / "v2.tar")
    pygpod.backup(full_ipod, archive)
    old = str(tmp_path / "v1.tar")
    with tarfile.open(archive) as src, tarfile.open(old, "w") as dst:
        for member in src:
            data = src.extractfile(member).read()
            if member.name == MANIFEST_NAME:
                m = json.loads(data)
                m["format_version"] = 1
                data = json.dumps(m).encode()
                member.size = len(data)
            dst.addfile(member, io.BytesIO(data))
    before = _snapshot(full_ipod)
    assert verify(old).files == read_manifest(archive).files
    _wipe_and_init(full_ipod)
    pygpod.restore(old, full_ipod)
    after = _snapshot(full_ipod)
    assert all(after.get(rel) == data for rel, data in before.items())


@pytest.mark.libgpod
def test_libgpod_reads_the_restored_photo_database(tool, photo_ipod, tmp_path):
    def dump():
        return subprocess.run(
            [tool, "dump-photos-full", photo_ipod], capture_output=True, text=True, check=True
        ).stdout

    expected = dump()
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(photo_ipod, archive)
    _wipe_and_init(photo_ipod)
    pygpod.restore(archive, photo_ipod)
    assert dump() == expected and "\tКарпати\t" in expected


def test_backup_retries_when_a_file_vanishes_while_archived(full_ipod, tmp_path, monkeypatch):
    from pygpod import backup_archive

    real = backup_archive._collect
    prefs = os.path.join(full_ipod, "iPod_Control", "iTunes", "iTunesPrefs")
    calls = []

    def collect(*a, **kw):
        files = real(*a, **kw)
        calls.append(1)
        if len(calls) == 2 and os.path.exists(prefs):  # 1st: before-state, 2nd: archiving
            os.unlink(prefs)  # a save removes it after the list was taken
        return files

    monkeypatch.setattr(backup_archive, "_collect", collect)
    info = pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    assert not [f for f in info.files if f.path.endswith("iTunesPrefs")]
    assert verify(str(tmp_path / "x.tar")).files == info.files


def test_backup_stops_when_a_save_is_interrupted_meanwhile(full_ipod, tmp_path, monkeypatch):
    from pygpod import backup_archive

    real = backup_archive._write_backup
    journal = os.path.join(full_ipod, "iPod_Control", "iTunes", "pygpod.journal")

    def crashing_writer(*a):
        info = real(*a)
        with open(journal, "w") as f:  # a writer crashed mid-commit during the backup
            f.write("{}")
        return info

    monkeypatch.setattr(backup_archive, "_write_backup", crashing_writer)
    with pytest.raises(BackupError, match="interrupted save"):
        pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    assert not os.path.exists(str(tmp_path / "x.tar"))


def test_backup_read_errors_are_not_retried(full_ipod, tmp_path, monkeypatch):
    from pygpod import backup_archive

    calls = []

    def failing(*a):
        calls.append(1)
        raise BackupError("backup failed: disk error")

    monkeypatch.setattr(backup_archive, "_write_backup", failing)
    with pytest.raises(BackupError, match="disk error"):
        pygpod.backup(full_ipod, str(tmp_path / "x.tar"))
    assert len(calls) == 1


def _symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not available")


def _tree_bytes(path):
    """{relative path: bytes} below ``path``, symbolic links listed, not followed."""
    out = {}
    for d, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(d, name)
            if os.path.islink(p):
                out[os.path.relpath(p, path)] = "-> " + os.readlink(p)
            elif os.path.isfile(p):
                with open(p, "rb") as f:
                    out[os.path.relpath(p, path)] = f.read()
    return out


def _track_file(mp):
    track = Database(mp).tracks[0]
    return os.path.join(mp, *track.ipod_path.strip(":").split(":"))


@pytest.mark.parametrize("dry_run", [False, True])
def test_restore_refuses_a_symlinked_music_folder(full_ipod, tmp_path, dry_run):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    song = _track_file(full_ipod)
    folder = os.path.dirname(song)
    outside = tmp_path / "outside"
    shutil.move(folder, str(outside))  # the folder now lives outside, a link points to it
    victim = outside / os.path.basename(song)
    victim.write_bytes(b"not the iPod's")
    _symlink(str(outside), folder)
    outside_before = _tree_bytes(str(outside))
    before = _tree_bytes(full_ipod)
    with pytest.raises(BackupError, match="outside the mount point"):
        pygpod.restore(archive, full_ipod, dry_run=dry_run)
    assert victim.read_bytes() == b"not the iPod's"
    assert _tree_bytes(str(outside)) == outside_before  # no temporary file either
    assert _tree_bytes(full_ipod) == before


def test_restore_refuses_a_symlink_in_place_of_a_temporary_file(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep me")
    _symlink(str(victim), _track_file(full_ipod) + ".pygpod-restore")
    before = _tree_bytes(full_ipod)
    with pytest.raises(BackupError, match="outside the mount point"):
        pygpod.restore(archive, full_ipod)
    assert victim.read_bytes() == b"keep me"
    assert _tree_bytes(full_ipod) == before


def test_restore_refuses_a_symlinked_pre_restore_file(full_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(full_ipod, archive)
    itunes = os.path.join(full_ipod, "iPod_Control", "iTunes")
    with open(os.path.join(itunes, "Play Counts"), "wb") as f:
        f.write(b"recorded after the backup")  # moved aside by a restore
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep me")
    _symlink(str(victim), os.path.join(itunes, "Play Counts.pre-restore"))
    before = _tree_bytes(full_ipod)
    with pytest.raises(BackupError, match="outside the mount point"):
        pygpod.restore(archive, full_ipod)
    assert victim.read_bytes() == b"keep me"
    assert _tree_bytes(full_ipod) == before


def test_restore_refuses_a_symlinked_photos_folder(photo_ipod, tmp_path):
    archive = str(tmp_path / "ipod.tar")
    pygpod.backup(photo_ipod, archive)
    outside = tmp_path / "outside-photos"
    photos = os.path.join(photo_ipod, "Photos")
    shutil.move(photos, str(outside))
    _symlink(str(outside), photos)
    outside_before = _tree_bytes(str(outside))
    before = _tree_bytes(photo_ipod)
    with pytest.raises(BackupError, match="outside the mount point"):
        pygpod.restore(archive, photo_ipod)
    assert _tree_bytes(str(outside)) == outside_before
    assert _tree_bytes(photo_ipod) == before


HASH72_VECTOR = os.path.join(os.path.dirname(__file__), "data", "hash72_libgpod")
HASH72_IV, HASH72_RND = bytes(range(0x64, 0x74)), bytes(range(1, 13))


def _hash72_target(tmp_path, hash_info=False):
    """A nano 5G (hash72) with its iTunesDB signed by libgpod; HashInfo only if asked."""
    mp = tmp_path / "nano"
    for d in ("iTunes", "Device", "Music/F00"):
        (mp / "iPod_Control" / d).mkdir(parents=True)
    shutil.copy(os.path.join(HASH72_VECTOR, "iTunesDB"), str(mp / "iPod_Control" / "iTunes"))
    names = ["SysInfo", "SysInfoExtended"] + (["HashInfo"] if hash_info else [])
    for name in names:
        shutil.copy(os.path.join(HASH72_VECTOR, name), str(mp / "iPod_Control" / "Device"))
    return str(mp)


def _assert_signed_for_the_nano(mp):
    from pygpod.hash.hash72 import compute_hash72, extract_hash72_params

    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        data = f.read()
    assert extract_hash72_params(data) == (HASH72_IV, HASH72_RND)
    assert compute_hash72(data, iv=HASH72_IV, random_bytes=HASH72_RND) == data
    with open(os.path.join(mp, "iPod_Control", "Device", "HashInfo"), "rb") as f, open(
        os.path.join(HASH72_VECTOR, "HashInfo"), "rb"
    ) as g:
        assert f.read() == g.read()


@pytest.mark.parametrize("hash_info", [False, True])
def test_forced_restore_onto_a_hash72_ipod_keeps_its_signing_parameters(
    full_ipod, tmp_path, hash_info
):
    archive = str(tmp_path / "classic.tar")
    pygpod.backup(full_ipod, archive)
    nano = _hash72_target(tmp_path, hash_info=hash_info)
    result = pygpod.restore(archive, nano, force=True)
    assert result.resigned
    _assert_signed_for_the_nano(nano)
    db = Database(nano, recover=False)
    assert sorted(t.title for t in db.tracks) == ["Без бою", "Човен"]
    assert not ({"checksum_mismatch", "checksum_unknown"} & db.check().kinds())


def test_forced_restore_commits_a_database_already_signed_for_the_target(
    full_ipod, tmp_path, monkeypatch
):
    """Even if the re-save after the commit fails, the committed database is signed with
    the target's hash72 parameters and HashInfo exists."""
    archive = str(tmp_path / "classic.tar")
    pygpod.backup(full_ipod, archive)
    nano = _hash72_target(tmp_path)

    def fail(self, *a, **kw):
        raise RuntimeError("interrupted after the commit")

    monkeypatch.setattr(Database, "save", fail)
    with pytest.raises(RuntimeError):
        pygpod.restore(archive, nano, force=True)
    _assert_signed_for_the_nano(nano)


@pytest.mark.parametrize("dry_run", [False, True])
def test_forced_restore_refuses_when_the_hash72_parameters_are_lost(full_ipod, tmp_path, dry_run):
    archive = str(tmp_path / "classic.tar")
    pygpod.backup(full_ipod, archive)
    nano = _hash72_target(tmp_path)
    db_path = os.path.join(nano, "iPod_Control", "iTunes", "iTunesDB")
    with open(db_path, "rb+") as f:  # not signed by iTunes/libgpod: nothing to recover
        f.seek(0x72)
        f.write(bytes(46))
    before = _snapshot(nano)
    with pytest.raises(BackupError, match="hash72"):
        pygpod.restore(archive, nano, force=True, dry_run=dry_run)
    assert _snapshot(nano) == before
