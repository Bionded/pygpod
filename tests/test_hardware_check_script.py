"""scripts/hardware_check.py on emulated iPods: backup first, test set, cleanup, restore."""

import importlib.util
import os
import re
import shutil
import struct
import subprocess

import pytest

import pygpod
from pygpod.backup_archive import BackupError, verify
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import PROJECT_ROOT, _make_minimal_mp3

pytest.importorskip("PIL")
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

GUID = "000A2700213749FF"
MODELS = {"classic7g": "C293", "video5g": "A002", "nano3g": "A978"}


def _script():
    path = os.path.join(PROJECT_ROOT, "scripts", "hardware_check.py")
    spec = importlib.util.spec_from_file_location("hardware_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hc = _script()


def _ipod(tmp_path, model):
    """An iPod with two tracks, a playlist and (where the model shows photos) one photo."""
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = f"ModelNumStr: x{MODELS[model]}\nFirewireGuid: 0x{GUID}\n".encode()
    init_ipod(mp, name="Мій iPod", sysinfo_files={"SysInfo": sysinfo})
    mp3 = str(tmp_path / "own.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    a = db.add_track(mp3, title="Без бою", artist="Океан Ельзи")
    b = db.add_track(mp3, title="Човен", artist="Океан Ельзи")
    db.set_playlist("Найкраще", [b, a])
    db.save()
    from PIL import Image

    photo = str(tmp_path / "Говерла.jpg")
    Image.new("RGB", (640, 480), (40, 120, 60)).save(photo)
    photos = pygpod.PhotoDB(mp)
    photos.add_photo(photo)
    photos.save()
    return mp


def _files(mp, *parts):
    """{relative path: bytes} below mp/parts."""
    root = os.path.join(mp, *parts)
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, mp)] = f.read()
    return out


def _state(mp):
    db = Database(mp)
    tracks = [(t.track_id, t.title, t.ipod_path) for t in db.tracks]
    playlists = [(p.name, [t.title for t in p.tracks]) for p in db.playlists]
    photos = [p.photo_id for p in pygpod.PhotoDB(mp).photos]
    return tracks, playlists, photos


@pytest.mark.parametrize("model", list(MODELS))
def test_write_cleanup_and_restore(tmp_path, model):
    mp = _ipod(tmp_path, model)
    before_db = _files(mp, "iPod_Control", "iTunes")
    before_music = set(_files(mp, "iPod_Control", "Music"))
    before = _state(mp)
    log = []

    archive = str(tmp_path / "before.tar.gz")
    checks = hc.write_test_set(mp, archive, seconds=1, yes=True, log=log.append)
    info = verify(archive)  # the backup was made before anything was written
    archived = {f.path for f in info.files}
    assert "iPod_Control/iTunes/iTunesDB" in archived
    assert not any("/Music/" in p for p in archived)  # database only by default
    db = Database(mp)
    ours = [t for t in db.tracks if t.grouping == hc.MARKER]
    video = 2 if db.device.supports_video else 0
    assert len(ours) == 21 + video
    assert [t.title for t in db.get_playlist(hc.PLAYLIST).tracks] == [t.title for t in ours]
    assert db.check().ok
    alac = [t for t in ours if t.album == "Безшовний альбом (FLAC)"]
    assert all(t.sample_count and t.gapless_track_flag for t in alac)
    loud, quiet = (next(t for t in ours if t.title.startswith(s)) for s in ("Голосно", "Тихо"))
    assert quiet.soundcheck < 1000 < loud.soundcheck  # quiet: boosted, loud: lowered
    assert next(t for t in ours if t.title.startswith("Аудіокнига")).chapters
    photo_album = pygpod.PhotoDB(mp).album_by_name(hc.PLAYLIST)
    assert photo_album is not None and photo_album.photo_count == 3
    assert any("Photos" in c for c in checks) and any("Sound Check" in c for c in checks)
    assert "backup verified" in "\n".join(log)

    hc.cleanup(mp, log=log.append)
    assert _state(mp) == before
    assert set(_files(mp, "iPod_Control", "Music")) == before_music
    assert pygpod.PhotoDB(mp).album_by_name(hc.PLAYLIST) is None
    assert Database(mp).check().ok

    # the iTunesDB is the same again; the ArtworkDB keeps its file entries (mhif)
    assert _files(mp, "iPod_Control", "iTunes") == before_db
    cleaned_art = _files(mp, "iPod_Control", "Artwork")
    cleaned_photos = _files(mp, "Photos")
    second = str(tmp_path / "second.tar")
    hc.write_test_set(mp, second, seconds=1, yes=True, log=log.append)
    hc.restore_backup(mp, second, yes=True, log=log.append)
    assert _files(mp, "iPod_Control", "iTunes") == before_db  # byte-identical database files
    assert _files(mp, "iPod_Control", "Artwork") == cleaned_art  # test covers' .ithmb deleted
    assert _files(mp, "Photos") == cleaned_photos
    assert set(_files(mp, "iPod_Control", "Music")) == before_music
    assert _state(mp) == before
    assert Database(mp).check().ok


def test_refuses_without_a_safe_setup(tmp_path, monkeypatch):
    mp = _ipod(tmp_path, "classic7g")
    snapshot = _files(mp)
    with pytest.raises(SystemExit):  # the mount point is never guessed
        hc.main(["--backup", str(tmp_path / "x.tar")])
    assert hc.main([str(tmp_path), "--backup", str(tmp_path / "x.tar"), "--yes"]) == 2
    with pytest.raises(hc.CheckError, match="not be on the iPod"):
        hc.write_test_set(mp, os.path.join(mp, "x.tar"), yes=True, log=lambda s: None)
    (tmp_path / "exists.tar").write_bytes(b"")
    with pytest.raises(hc.CheckError, match="exists already"):
        hc.write_test_set(mp, str(tmp_path / "exists.tar"), yes=True, log=lambda s: None)

    def broken(*a, **kw):
        raise BackupError("disk full")

    monkeypatch.setattr(pygpod, "backup", broken)
    with pytest.raises(hc.CheckError, match="backup failed, nothing was written"):
        hc.write_test_set(mp, str(tmp_path / "x.tar"), yes=True, log=lambda s: None)
    monkeypatch.undo()
    monkeypatch.setattr("builtins.input", lambda q: "n")
    with pytest.raises(hc.CheckError, match="cancelled"):
        hc.write_test_set(mp, str(tmp_path / "y.tar"), log=lambda s: None)
    assert _files(mp) == snapshot  # nothing changed on the iPod
    assert not os.path.exists(str(tmp_path / "y.tar"))

    monkeypatch.undo()
    hc.write_test_set(mp, str(tmp_path / "z.tar"), seconds=1, yes=True, log=lambda s: None)
    with pytest.raises(hc.CheckError, match="run --cleanup first"):
        hc.write_test_set(mp, str(tmp_path / "w.tar"), yes=True, log=lambda s: None)
    assert hc.main([mp, "--doctor"]) == 0
    assert hc.main([mp, "--cleanup", "--yes"]) == 0
    assert not [t for t in Database(mp).tracks if t.grouping == hc.MARKER]


def test_restore_from_an_archive_without_photos_keeps_the_photo_thumbnails(tmp_path):
    import io
    import json
    import tarfile

    from pygpod.backup_archive import MANIFEST_NAME

    mp = _ipod(tmp_path, "classic7g")
    archive = str(tmp_path / "v2.tar")
    hc.write_test_set(mp, archive, seconds=1, yes=True, log=lambda s: None)
    old = str(tmp_path / "v1.tar")  # as an older pygpod wrote it: no Photos/ entries
    with tarfile.open(archive) as src, tarfile.open(old, "w") as dst:
        for member in src:
            if member.name.startswith("Photos/"):
                continue
            data = src.extractfile(member).read()
            if member.name == MANIFEST_NAME:
                m = json.loads(data)
                m["format_version"] = 1
                m["files"] = [f for f in m["files"] if not f["path"].startswith("Photos/")]
                data = json.dumps(m).encode()
                member.size = len(data)
            dst.addfile(member, io.BytesIO(data))
    count = len(pygpod.PhotoDB(mp).photos)
    hc.restore_backup(mp, old, yes=True, log=lambda s: None)
    photos = pygpod.PhotoDB(mp).photos
    assert len(photos) == count - 3  # only the test photos removed
    thumbs = os.path.join(mp, "Photos", "Thumbs")
    for t in photos[0].thumbnails:  # the user's photo still has its thumbnail files
        name = f"F{t.format_id}_{t.ithmb_file}.ithmb"
        assert os.path.getsize(os.path.join(thumbs, name)) >= t.image_offset + t.image_size


def _beep_count(path):
    """Beeps in an audio file: rising edges of the 10 ms loudness envelope."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    loud = [max(map(abs, samples[i : i + 80])) > 3000 for i in range(0, len(samples), 80)]
    return sum(1 for a, b in zip([False] + loud, loud) if b and not a)


def _otg(mp, indices):
    data = b"mhpo" + struct.pack("<III", 0x14, 4, len(indices)) + b"\xa4\x24\xf1\x69"
    data += b"".join(struct.pack("<I", i) for i in indices)
    with open(os.path.join(mp, "iPod_Control", "iTunes", "OTGPlaylistInfo"), "wb") as f:
        f.write(data)


def test_control_album_beep_tracks_and_otg_step(tmp_path, monkeypatch):
    mp = _ipod(tmp_path, "classic7g")
    sources = []
    add_track = Database.add_track

    def recording(self, path, *a, **kw):
        sources.append(path)
        return add_track(self, path, *a, **kw)

    monkeypatch.setattr(Database, "add_track", recording)
    checks = hc.write_test_set(mp, str(tmp_path / "b.tar"), seconds=1, yes=True, log=print)
    monkeypatch.undo()
    # every test file was generated in the script's own scratch directory
    assert sources and all(
        os.path.basename(os.path.dirname(p)).startswith("pygpod-check-") for p in sources
    )
    db = Database(mp)
    ours = [t for t in db.tracks if t.grouping == hc.MARKER]
    seamless = [t for t in ours if t.album == "Безшовний альбом (MP3)"]
    control = [t for t in ours if t.album == "Контроль: з паузою (MP3)"]
    assert len(seamless) == len(control) == 3
    assert all(t.sample_count and t.pregap and t.gapless_track_flag for t in seamless)
    for t in control:
        assert (t.pregap, t.postgap, t.sample_count, t.gapless_data) == (0, 0, 0, 0)
        assert (t.gapless_track_flag, t.gapless_album_flag) == (0, 0)
    assert any("Контроль: з паузою (MP3)" in c for c in checks)
    assert any("--otg-step" in c for c in checks)

    beeps = {t.title: t for t in ours if t.album == hc.OTG_ALBUM}
    assert sorted(beeps) == [f"OTG {n}" for n in range(1, 6)]
    for n in range(1, 6):
        assert _beep_count(hc._track_file(db, beeps[f"OTG {n}"])) == n
        assert 5500 <= beeps[f"OTG {n}"].duration_ms <= 6500

    with pytest.raises(hc.CheckError, match="no On-The-Go playlist"):
        hc.otg_step(mp, yes=True, log=lambda s: None)
    position = {t.title: i for i, t in enumerate(db.tracks)}
    _otg(mp, [position["OTG 2"], position["OTG 4"]])
    removed_files = [hc._track_file(db, beeps[n]) for n in ("OTG 1", "OTG 3")]
    assert hc.otg_step(mp, yes=True, log=lambda s: None) == [["OTG 2", "OTG 4"]]
    db = Database(mp)
    # imported by the save (default otg_on_save='import'), On-The-Go itself emptied
    assert [t.title for t in db.get_playlist("OTG Playlist 1").tracks] == ["OTG 2", "OTG 4"]
    assert db.pending_otg_playlists() == []
    titles = [t.title for t in db.tracks]
    assert "OTG 1" not in titles and "OTG 3" not in titles
    assert titles.index("OTG 2") < position["OTG 2"]  # the OTG entries had to move
    assert not any(os.path.exists(f) for f in removed_files)
    assert db.check().ok
    with pytest.raises(hc.CheckError, match="no On-The-Go playlist"):  # imported, gone
        hc.otg_step(mp, yes=True, log=lambda s: None)
    _otg(mp, [0])
    with pytest.raises(hc.CheckError, match="not on the iPod"):
        hc.otg_step(mp, yes=True, log=lambda s: None)
    os.unlink(os.path.join(mp, "iPod_Control", "iTunes", "OTGPlaylistInfo"))

    assert hc.main([mp, "--cleanup", "--yes"]) == 0
    db = Database(mp)
    assert not [t for t in db.tracks if t.grouping == hc.MARKER]
    assert [t.title for t in db.tracks] == ["Без бою", "Човен"]
    assert db.get_playlist("OTG Playlist 1") is None  # emptied by the cleanup, removed


def test_script_reads_no_existing_media():
    with open(os.path.join(PROJECT_ROOT, "scripts", "hardware_check.py"), encoding="utf-8") as f:
        source = f.read()
    assert not re.search(
        r"expanduser|/home/|/Users/|/media/|[Mm]usic/|glob", source.replace("/media/<you>", "")
    )
