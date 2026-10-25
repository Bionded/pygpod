"""On-The-Go files when save() writes a new database: DatabaseConfig.otg_on_save."""

import os
import struct
import subprocess

import pytest

from pygpod.db.parser import parse_itunesdb
from pygpod.db.writer import write_itunesdb
from pygpod.device.mountpoint import init_ipod
from pygpod.model.config import DatabaseConfig
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

SYSINFO = b"ModelNumStr: xB029\nFirewireGuid: 0x000A2700213749FF\n"
UNKNOWN = b"\xa4\x24\xf1\x69"  # the 4 header bytes after the count, as on a real Classic
KEEP = DatabaseConfig(otg_on_save="keep")


def _itunes(mp):
    return os.path.join(mp, "iPod_Control", "iTunes")


def _otg(mp, name, indices):
    data = b"mhpo" + struct.pack("<III", 0x14, 4, len(indices)) + UNKNOWN
    data += b"".join(struct.pack("<I", i) for i in indices)
    with open(os.path.join(_itunes(mp), name), "wb") as f:
        f.write(data)
    return os.path.join(_itunes(mp), name)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    for i in range(5):
        db.add_track(mp3, title=f"T{i}")
    db.save()
    _otg(mp, "OTGPlaylistInfo", [3, 1, 4])
    _otg(mp, "OTGPlaylistInfo_1", [2])
    return mp, mp3


def _pending_titles(mp):
    return [[t.title for t in pl] for pl in Database(mp).pending_otg_playlists()]


def test_keep_rewrites_positions_when_tracks_move(ipod):
    mp, _ = ipod
    db = Database(mp, config=KEEP)
    by = {t.title: t for t in db.tracks}
    db.remove_tracks([by["T0"], by["T1"]], delete_files=True)  # shifts every later track
    db.save()
    first = _read(os.path.join(_itunes(mp), "OTGPlaylistInfo"))
    assert first[16:20] == UNKNOWN  # header bytes kept
    assert struct.unpack_from("<I", first, 12)[0] == 2  # T1's entry dropped
    assert _pending_titles(mp) == [["T3", "T4"], ["T2"]]  # still the same songs


def test_keep_leaves_files_alone_when_nothing_moved(ipod):
    mp, mp3 = ipod
    before = [_read(os.path.join(_itunes(mp), n)) for n in ("OTGPlaylistInfo", "OTGPlaylistInfo_1")]
    db = Database(mp, config=KEEP)
    db.add_track(mp3, title="new")  # appended: existing positions unchanged
    db.tracks[0].title = "T0 edited"
    db.save()
    after = [_read(os.path.join(_itunes(mp), n)) for n in ("OTGPlaylistInfo", "OTGPlaylistInfo_1")]
    assert after == before


def test_keep_twice_in_one_session(ipod):
    mp, _ = ipod
    db = Database(mp, config=KEEP)
    db.remove_track(next(t for t in db.tracks if t.title == "T0"), delete_file=True)
    db.save()
    db.remove_track(next(t for t in db.tracks if t.title == "T2"), delete_file=True)
    db.save()
    assert _pending_titles(mp) == [["T3", "T1", "T4"]]  # T2's playlist is empty now
    assert [[t.title for t in pl] for pl in db.pending_otg_playlists()] == [["T3", "T1", "T4"]]


def test_file_changed_on_the_ipod_is_not_touched(ipod):
    mp, _ = ipod
    db = Database(mp, config=KEEP)
    db.remove_track(db.tracks[0], delete_file=True)
    path = _otg(mp, "OTGPlaylistInfo", [0])  # the iPod wrote a new one meanwhile
    db.save()
    assert _read(path)[20:] == struct.pack("<I", 0)


def test_default_is_import():
    assert DatabaseConfig().otg_on_save == "import"


@pytest.mark.parametrize(
    "config", [None, DatabaseConfig(otg_on_save="import")], ids=["default", "import"]
)
def test_import_mode_imports_like_libgpod_and_removes_the_files(ipod, config):
    mp, _ = ipod
    db = Database(mp, config=config)
    db.tracks[0].title = "T0"  # any change
    db.save()
    assert not [n for n in os.listdir(_itunes(mp)) if n.startswith("OTGPlaylistInfo")]
    db = Database(mp)
    assert [t.title for t in db.get_playlist("OTG Playlist 1").tracks] == ["T3", "T1", "T4"]
    assert [t.title for t in db.get_playlist("OTG Playlist 2").tracks] == ["T2"]
    with open(os.path.join(_itunes(mp), "iTunesDB"), "rb") as f:
        data = f.read()
    assert write_itunesdb(parse_itunesdb(data)) == data


def test_clear_mode_and_explicit_calls(ipod):
    mp, _ = ipod
    db = Database(mp, config=DatabaseConfig(otg_on_save="clear"))
    db.save()
    assert not [n for n in os.listdir(_itunes(mp)) if n.startswith("OTGPlaylistInfo")]
    assert [p.name for p in Database(mp).playlists if not p.is_master] == []

    _otg(mp, "OTGPlaylistInfo", [0])
    db = Database(mp, config=DatabaseConfig(otg_on_save="clear"))
    db.import_otg_playlists()  # explicit call wins over the mode
    db.save()
    assert Database(mp).get_playlist("OTG Playlist 1") is not None

    _otg(mp, "OTGPlaylistInfo", [0])
    db = Database(mp)
    db.clear_otg_playlists()
    assert os.path.isfile(os.path.join(_itunes(mp), "OTGPlaylistInfo"))  # only after save
    db.save()
    assert not os.path.exists(os.path.join(_itunes(mp), "OTGPlaylistInfo"))


def test_bad_mode_is_refused_before_writing(ipod):
    mp, _ = ipod
    before = _read(os.path.join(_itunes(mp), "iTunesDB"))
    db = Database(mp, config=DatabaseConfig(otg_on_save="drop"))
    with pytest.raises(ValueError, match="otg_on_save"):
        db.save()
    assert _read(os.path.join(_itunes(mp), "iTunesDB")) == before


@pytest.mark.libgpod
def test_imported_playlists_equal_libgpods_import(tool, ipod):
    mp, _ = ipod
    out = subprocess.run([tool, "dump", mp], capture_output=True, text=True, check=True)
    theirs = {
        f[1]: f[5].split("|")
        for f in (ln.split("\t") for ln in out.stdout.splitlines())
        if f[0] == "P" and f[1].startswith("OTG")
    }
    assert theirs == {"OTG Playlist 1": ["T3", "T1", "T4"], "OTG Playlist 2": ["T2"]}
    db = Database(mp, config=DatabaseConfig(otg_on_save="import"))
    db.save()
    out = subprocess.run([tool, "dump", mp], capture_output=True, text=True, check=True)
    ours = {
        f[1]: f[5].split("|")
        for f in (ln.split("\t") for ln in out.stdout.splitlines())
        if f[0] == "P" and f[1].startswith("OTG")
    }
    assert ours == theirs  # same names and members, and nothing imported twice
