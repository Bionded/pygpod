"""iPod shuffle 3G/4G: iTunesSD version 2 (bdhs/hths/rths/hphs/lphs)."""

import os
import subprocess

import pytest

from pygpod.db.constants import MEDIATYPE_PODCAST
from pygpod.db.itunessd import (
    PLAYLIST_MASTER,
    PLAYLIST_NORMAL,
    PLAYLIST_PODCAST,
    ITunesSD,
    ITunesSDV2,
)
from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import ParseError
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


def _sd_path(mp):
    return os.path.join(mp, "iPod_Control", "iTunes", "iTunesSD")


@pytest.fixture
def shuffle4(tmp_path):
    mp = str(tmp_path / "shuffle")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xC584\nFirewireGuid: 0x000A270012345678\n"  # shuffle 4G
    init_ipod(mp, name="Shuffle", sysinfo_files={"SysInfo": sysinfo})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    a = db.add_track(mp3, title="Без бою", artist="Океан Ельзи", album="A", track_number=3)
    b = db.add_track(mp3, title="B", artist="X", album="B", bookmark_time=5000, cd_number=2)
    db.add_track(mp3, title="Випуск", artist="R", album="R", media_type=MEDIATYPE_PODCAST)
    b.volume, b.start_time, b.stop_time = 100, 1000, 9000
    a.skip_when_shuffling = True
    db.set_playlist("Mix", [b, a])
    db.create_playlist("Empty")
    db.save()
    return mp


def test_save_writes_v2_and_it_parses_back(shuffle4):
    with open(_sd_path(shuffle4), "rb") as f:
        data = f.read()
    sd = ITunesSDV2.parse(data)
    db = Database(shuffle4)
    by_title = {t.title: t for t in db.tracks}
    assert [t.path for t in sd.tracks] == [t.ipod_path.replace(":", "/") for t in db.tracks]
    a = by_title["Без бою"]
    ta, tb = sd.tracks[0], sd.tracks[1]
    assert (ta.shuffle_flag, ta.track_number, ta.dbid) == (0, 3, a.dbid)  # skipped in shuffle
    assert (tb.volume, tb.start_ms, tb.stop_ms, tb.bookmark_ms) == (100, 1000, 9000, 5000)
    assert (tb.cd_number, tb.shuffle_flag, tb.file_type) == (2, 1, 1)
    assert ta.album_id and ta.artist_id and ta.album_id != tb.album_id
    kinds = sorted(p.kind for p in sd.playlists)
    assert kinds == [PLAYLIST_MASTER, PLAYLIST_NORMAL, PLAYLIST_PODCAST]  # "Empty" left out
    assert [p.kind for p in sd.playlists][0] == PLAYLIST_MASTER
    mix = next(p for p in sd.playlists if p.kind == PLAYLIST_NORMAL)
    assert mix.track_indexes == [1, 0]
    assert mix.playlist_id == db.get_playlist("Mix").playlist_id
    assert next(p for p in sd.playlists if p.kind == PLAYLIST_PODCAST).track_indexes == [2]
    assert sd.voiceover == 1 and sd.max_volume == 0
    # round trip: parse -> write is byte-identical (media types are only header counts)
    sd.tracks = [t._replace(media_type=x.media_type) for t, x in zip(sd.tracks, db.tracks)]
    assert sd.write() == data


def test_header_counts(shuffle4):
    with open(_sd_path(shuffle4), "rb") as f:
        data = f.read()
    assert data[:4] == b"bdhs" and int.from_bytes(data[12:16], "little") == 3
    assert int.from_bytes(data[16:20], "little") == 3  # non-empty playlists
    assert int.from_bytes(data[32:36], "little") == 2  # tracks without the podcast


def test_removing_a_track_rewrites_it(shuffle4):
    db = Database(shuffle4)
    db.remove_track(db.tracks[0], delete_file=True)
    db.save()
    with open(_sd_path(shuffle4), "rb") as f:
        sd = ITunesSDV2.parse(f.read())
    mix = next(p for p in sd.playlists if p.kind == PLAYLIST_NORMAL)
    assert len(sd.tracks) == 2 and mix.track_indexes == [0]


def test_parse_rejects_other_data():
    with pytest.raises(ParseError):
        ITunesSDV2.parse(b"\x00" * 100)
    with pytest.raises(ParseError):
        ITunesSDV2.parse(b"bdhs" + b"\x00" * 60)
    assert ITunesSD.parse(b"").tracks == []


@pytest.mark.libgpod
@pytest.mark.parametrize("model", ["xC306", "xC584"])  # shuffle 3G, 4G
def test_byte_identical_to_libgpod(tool, tmp_path, model, shuffle4):
    mp = shuffle4
    if model == "xC306":
        sysinfo = os.path.join(mp, "iPod_Control", "Device", "SysInfo")
        with open(sysinfo, "w") as f:
            f.write(f"ModelNumStr: {model}\nFirewireGuid: 0x000A270012345678\n")
        db = Database(mp)
        db._mark_modified()
        db.save()
    out = str(tmp_path / "libgpod.sd")
    subprocess.run([tool, "shuffle-sd", mp, out], check=True, capture_output=True)
    with open(out, "rb") as f, open(_sd_path(mp), "rb") as g:
        assert g.read() == f.read()
