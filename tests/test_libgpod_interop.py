"""Interop with libgpod, the reference implementation (independent of pygpod's own fixtures).

- libgpod writes a database -> pygpod reads the same tracks/playlists, and pygpod's
  hash58 of those bytes equals libgpod's.
- pygpod writes a database (adds, edits, playlists, podcasts, removals) -> libgpod
  parses it and sees exactly that content.
Regression checks for confirmed pygpod bugs are included where libgpod is the judge.

The C helper is integration_tests/libgpod_tool.c, compiled once per session. Without
gcc/pkg-config/libgpod the module is skipped, or fails when PYGPOD_REQUIRE_LIBGPOD=1
(the CI interop job sets it).
"""

import os
import shutil
import subprocess

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.hash.hash58 import compute_hash58
from pygpod.model.database import Database

from .conftest import PROJECT_ROOT, _make_minimal_mp3

pytestmark = pytest.mark.libgpod

GUID = "000A2700213749FF"
SYSINFO = f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n"  # Classic: hash58


def _dump(tool, mp):
    out = subprocess.run([tool, "dump", mp], capture_output=True, text=True, check=True).stdout
    tracks, playlists = [], {}
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "T":
            tracks.append(
                {
                    "dbid": int(f[2]),
                    "title": f[3],
                    "artist": f[4],
                    "album": f[5],
                    "genre": f[6],
                    "track_nr": int(f[7]),
                    "year": int(f[8]),
                    "mediatype": int(f[9]),
                    "rating": int(f[10]),
                    "playcount": int(f[11]),
                    "path": f[12],
                    "compilation": int(f[13]),
                    "albumartist": f[14],
                    "bookmark": int(f[15]),
                    "remember": int(f[16]),
                }
            )
        elif f[0] == "P":
            playlists[f[1]] = {
                "mpl": f[2] == "1",
                "podcasts": f[3] == "1",
                "members": f[5].split("|") if f[5] else [],
            }
    return tracks, playlists


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO.encode()})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    return mp, mp3


def test_libgpod_written_db_read_by_pygpod(tool, tmp_path):
    mp = str(tmp_path / "lg")
    for d in ("iTunes", "Device", "Music/F00"):
        os.makedirs(os.path.join(mp, "iPod_Control", d))
    with open(os.path.join(mp, "iPod_Control", "Device", "SysInfo"), "w") as f:
        f.write(SYSINFO)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(
        "name\tLib iPod\n"
        f"track\t{mp3}\tБез бою\tОкеан Ельзи\tБез бою\tRock\t1\t1988\t1\t80\t5\n"
        f"track\t{mp3}\tЧовен\tОкеан Ельзи\tЗемля\tRock\t9\t1990\t1\t60\t0\n"
        f"track\t{mp3}\tВипуск 1\tРадіо-Т\tРадіо-Т\tPodcast\t1\t2024\t4\t0\t0\n"
        "playlist\tНайкраще\t0\t1,0\n"
        "playlist\tPodcasts\t1\t2\n"
    )
    subprocess.run([tool, "build", mp, str(spec)], check=True)
    lg_tracks, lg_playlists = _dump(tool, mp)

    db = Database(mp)
    got = [
        (
            t.title,
            t.artist,
            t.album,
            t.genre,
            t.track_number,
            t.year,
            t.rating,
            t.play_count,
            t.media_type,
        )
        for t in db.tracks
    ]
    want = [
        (
            t["title"],
            t["artist"],
            t["album"],
            t["genre"],
            t["track_nr"],
            t["year"],
            t["rating"],
            t["playcount"],
            t["mediatype"],
        )
        for t in lg_tracks
    ]
    assert got == want and len(got) == 3
    assert [t.dbid for t in db.tracks] == [t["dbid"] for t in lg_tracks]
    assert db.master_playlist.name == "Lib iPod"
    assert [t.title for t in db.get_playlist("Найкраще").tracks] == lg_playlists["Найкраще"][
        "members"
    ]
    assert [p.name for p in db.playlists if p.is_podcast] == ["Podcasts"]

    data = open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    assert data[0x58:0x6C] != b"\x00" * 20
    assert compute_hash58(data, GUID)[0x58:0x72] == data[0x58:0x72], "hash58 differs from libgpod"


def test_pygpod_written_db_read_by_libgpod(tool, ipod):
    mp, mp3 = ipod
    db = Database(mp)
    a = db.add_track(
        mp3,
        title="Без бою",
        artist="Океан Ельзи",
        album="Без бою",
        genre="Rock",
        track_number=1,
        year=1988,
        rating=80,
        play_count=5,
        albumartist="Океан Ельзи",
    )
    b = db.add_track(mp3, title="Човен", artist="Океан Ельзи", album="Збірка", compilation=True)
    gone = db.add_track(mp3, title="Видалити", artist="X")
    from pygpod.db.constants import MEDIATYPE_AUDIOBOOK, MEDIATYPE_PODCAST

    db.add_track(
        mp3,
        title="Випуск 1",
        artist="Радіо-Т",
        album="Радіо-Т",
        media_type=MEDIATYPE_PODCAST,
        category="Радіо-Т",
    )
    db.add_track(
        mp3,
        title="Розділ 1",
        artist="Кінг",
        album="Воно",
        media_type=MEDIATYPE_AUDIOBOOK,
        bookmark_time=42000,
        remember_position=True,
    )
    db.set_playlist("Найкраще", [b, a])
    db.save()
    db = Database(mp)
    by_title = {t.title: t for t in db.tracks}
    by_title["Човен"].title = "Човен (live)"  # edit in place
    db.remove_track(by_title["Видалити"], delete_file=True)
    db.save()

    tracks, playlists = _dump(tool, mp)
    t = {x["title"]: x for x in tracks}
    assert set(t) == {"Без бою", "Човен (live)", "Випуск 1", "Розділ 1"}
    g = t["Без бою"]
    assert (
        g["artist"],
        g["album"],
        g["genre"],
        g["track_nr"],
        g["year"],
        g["rating"],
        g["playcount"],
        g["albumartist"],
    ) == ("Океан Ельзи", "Без бою", "Rock", 1, 1988, 80, 5, "Океан Ельзи")
    assert t["Човен (live)"]["compilation"] == 1
    assert t["Випуск 1"]["mediatype"] & MEDIATYPE_PODCAST
    assert t["Розділ 1"]["mediatype"] & MEDIATYPE_AUDIOBOOK
    assert t["Розділ 1"]["bookmark"] == 42000 and t["Розділ 1"]["remember"] == 1
    for x in tracks:  # every path libgpod sees exists on disk
        assert os.path.isfile(os.path.join(mp, *x["path"].strip(":").split(":")))
    assert playlists["pygpod"]["mpl"] and len(playlists["pygpod"]["members"]) == 4
    assert playlists["Найкраще"]["members"] == ["Човен (live)", "Без бою"]
    assert [n for n, p in playlists.items() if p["podcasts"]] == ["Podcasts"]
    assert playlists["Podcasts"]["members"] == ["Випуск 1"]
    assert gone.ipod_path not in {x["path"] for x in tracks}


def test_regressions_seen_by_libgpod(tool, ipod):
    """libgpod as judge: bug 45 (with-block edits saved), 46 (empty-DB playlists)."""
    mp, mp3 = ipod
    with Database(mp) as db:
        t = db.add_track(mp3, title="first")
        pl = db.create_playlist("P")
        db.add_track_to_playlist(pl, t)
    with Database(mp) as db:
        db.tracks[0].title = "edited in with-block"
    tracks, playlists = _dump(tool, mp)
    assert [x["title"] for x in tracks] == ["edited in with-block"]
    assert playlists["P"]["members"] == ["edited in with-block"]


def test_hash72_matches_libgpod_and_survives_edits(tool, tmp_path):
    """Nano 5G (hash72): libgpod signs with a random HashInfo; pygpod reproduces the signature and
    re-signs an edited database with the same device IV."""
    from pygpod.hash.hash72 import compute_hash72, extract_hash72_params

    mp = tmp_path / "nano"
    for d in ("iTunes", "Device", "Music/F00"):
        (mp / "iPod_Control" / d).mkdir(parents=True)
    guid = "000A270012345678"  # = FireWireGUID in the copied SysInfoExtended
    (mp / "iPod_Control" / "Device" / "SysInfo").write_text(
        f"ModelNumStr: xC027\nFirewireGuid: 0x{guid}\n"
    )
    # with DBVersion 4 libgpod writes a plain iTunesDB (without it: iTunesCDB + SQLite, which pygpod
    # does not write)
    shutil.copy(
        os.path.join(PROJECT_ROOT, "tests", "data", "hash72_libgpod", "SysInfoExtended"),
        mp / "iPod_Control" / "Device" / "SysInfoExtended",
    )
    iv, rnd = os.urandom(16), os.urandom(12)
    uuid = bytes.fromhex(guid).ljust(20, b"\x00")
    (mp / "iPod_Control" / "Device" / "HashInfo").write_bytes(b"HASHv0" + uuid + rnd + iv)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(f"name\tNano\ntrack\t{mp3}\tSong\tArtist\tAlbum\tPop\t1\t2009\t1\t0\t0\n")
    subprocess.run([tool, "build", str(mp), str(spec)], check=True)
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    signed = db_path.read_bytes()
    assert len(signed) > 500 and signed[0x72] == 0x01
    assert compute_hash72(signed, iv=iv, random_bytes=rnd) == signed

    db = Database(str(mp))
    db.tracks[0].title = "Edited"
    db.save()
    assert extract_hash72_params(db_path.read_bytes()) == (iv, rnd)
    tracks, _ = _dump(tool, str(mp))
    assert [t["title"] for t in tracks] == ["Edited"]
