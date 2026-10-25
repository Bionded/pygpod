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


@pytest.mark.parametrize(
    "model,signed",
    [("xA003", False), ("xA350", False), ("xA477", False), ("xB029", True), ("xA978", True)],
)
def test_checksum_scheme_matches_libgpod(tool, tmp_path, model, signed):
    """Libgpod signs Classic and nano 3G/4G databases (hash58) and leaves the 5G and nano
    1G/2G ones unsigned; pygpod writes the same hash area and check() accepts libgpod's database.
    The hashing-scheme field at 0x30 matches libgpod's too."""
    from pygpod.doctor import check

    guid = "000A270012345678"
    sysinfo = f"ModelNumStr: {model}\nFirewireGuid: 0x{guid}\n"
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(f"name\tiPod\ntrack\t{mp3}\tSong\tArtist\tAlbum\tPop\t1\t2009\t1\t0\t0\n")

    lg = tmp_path / "lg"
    for d in ("iTunes", "Device", "Music/F00"):
        (lg / "iPod_Control" / d).mkdir(parents=True)
    (lg / "iPod_Control" / "Device" / "SysInfo").write_text(sysinfo)
    subprocess.run([tool, "build", str(lg), str(spec)], check=True)
    lg_data = (lg / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert (lg_data[0x58:0x6C] != b"\x00" * 20) is signed
    db = Database(str(lg))
    assert "checksum_mismatch" not in check(db).kinds()

    py = tmp_path / "py"
    py.mkdir()
    init_ipod(str(py), name="iPod", sysinfo_files={"SysInfo": sysinfo.encode()})
    db = Database(str(py))
    db.add_track(mp3, title="Song", artist="Artist", album="Album", copy=True)
    db.save()
    py_data = (py / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    if signed:
        assert compute_hash58(py_data, guid)[0x58:0x6C] == py_data[0x58:0x6C] != b"\x00" * 20
    else:
        assert py_data[0x58:0x6C] == lg_data[0x58:0x6C] == b"\x00" * 20
    assert "checksum_mismatch" not in check(Database(str(py))).kinds()
    assert [t["title"] for t in _dump(tool, str(py))[0]] == ["Song"]
    # Mhbd 0x30 (hashing scheme) like libgpod: 0 unsigned, 1 hash58
    assert py_data[0x30:0x32] == lg_data[0x30:0x32] == (b"\x01\x00" if signed else b"\x00\x00")
    # ... while the db version at 0x10 stays per generation (pygpod's own mapping)
    assert py_data[0x10] == Database(str(py))._device.db_version
    assert not check(Database(str(py)))
    Database(str(py)).save()  # unchanged: byte-identical
    assert (py / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes() == py_data


def test_init_ipod_signed_like_libgpod(tool, tmp_path):
    """Init_ipod signs an empty Classic database (as libgpod does) and libgpod reads it."""
    sysinfo = SYSINFO.encode()
    lg = tmp_path / "lg"
    for d in ("iTunes", "Device", "Music/F00"):
        (lg / "iPod_Control" / d).mkdir(parents=True)
    (lg / "iPod_Control" / "Device" / "SysInfo").write_bytes(sysinfo)
    spec = tmp_path / "spec.tsv"
    spec.write_text("name\tEmpty\n")
    subprocess.run([tool, "build", str(lg), str(spec)], check=True)
    lg_data = (lg / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert lg_data[0x58:0x6C] != b"\x00" * 20

    py = tmp_path / "py"
    py.mkdir()
    init_ipod(str(py), name="Empty", sysinfo_files={"SysInfo": sysinfo})
    py_data = (py / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert py_data[0x58:0x6C] != b"\x00" * 20
    assert compute_hash58(py_data, GUID) == py_data
    tracks, playlists = _dump(tool, str(py))
    assert tracks == [] and playlists["Empty"]["mpl"]


def test_sysinfo_extended_dbversion_signs_unknown_model_like_libgpod(tool, tmp_path):
    """An unknown ModelNumStr with SysInfoExtended DBVersion 3 is signed with hash58 by
    libgpod and by pygpod (init_ipod and save())."""
    import plistlib

    guid = "000A270012345678"
    sysinfo = f"ModelNumStr: xZZZZ\nFirewireGuid: 0x{guid}\n".encode()
    ext = plistlib.dumps({"FireWireGUID": guid, "DBVersion": 3})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(f"name\tiPod\ntrack\t{mp3}\tSong\tArtist\tAlbum\tPop\t1\t2009\t1\t0\t0\n")

    lg = tmp_path / "lg"
    for d in ("iTunes", "Device", "Music/F00"):
        (lg / "iPod_Control" / d).mkdir(parents=True)
    (lg / "iPod_Control" / "Device" / "SysInfo").write_bytes(sysinfo)
    (lg / "iPod_Control" / "Device" / "SysInfoExtended").write_bytes(ext)
    subprocess.run([tool, "build", str(lg), str(spec)], check=True, capture_output=True)
    lg_data = (lg / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert compute_hash58(lg_data, guid) == lg_data != b""
    assert lg_data[0x58:0x6C] != b"\x00" * 20

    py = tmp_path / "py"
    py.mkdir()
    init_ipod(str(py), name="iPod", sysinfo_files={"SysInfo": sysinfo, "SysInfoExtended": ext})
    db_path = py / "iPod_Control" / "iTunes" / "iTunesDB"
    fresh = db_path.read_bytes()
    assert compute_hash58(fresh, guid) == fresh and fresh[0x58:0x6C] != b"\x00" * 20
    db = Database(str(py))
    # unknown model: the music dirs are counted on disk
    db.add_track(mp3, title="Song", artist="Artist", album="Album", copy=True)
    db.save()
    data = db_path.read_bytes()
    assert compute_hash58(data, guid) == data and data[0x58:0x6C] != b"\x00" * 20
    tracks = _dump(tool, str(py))[0]
    assert [t["title"] for t in tracks] == ["Song"]
    assert os.path.isfile(os.path.join(str(py), *tracks[0]["path"].strip(":").split(":")))


@pytest.mark.parametrize("model", ["xA003", "xA350"])
def test_unsigned_model_drops_stale_hash58_on_save(tool, tmp_path, model):
    """A 5G/nano 1G database that pygpod <= 0.1.0 signed with hash58 gets the zeroed
    hash area libgpod writes once it is saved again; a with-block without changes does not
    rewrite it."""
    from pygpod.doctor import check

    guid = "000A270012345678"
    mp = tmp_path / "ipod"
    mp.mkdir()
    sysinfo = f"ModelNumStr: {model}\nFirewireGuid: 0x{guid}\n".encode()
    init_ipod(str(mp), name="iPod", sysinfo_files={"SysInfo": sysinfo})
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(str(mp))
    db.add_track(mp3, title="Old", copy=True)
    db.save()
    signed = compute_hash58(db_path.read_bytes(), guid)  # what 0.1.0 wrote
    assert signed[0x58:0x6C] != b"\x00" * 20
    db_path.write_bytes(signed)

    with Database(str(mp)):  # nothing changed: nothing written
        pass
    assert db_path.read_bytes() == signed

    db = Database(str(mp))
    db.add_track(mp3, title="New", copy=True)
    db.save()
    data = db_path.read_bytes()
    assert data[0x58:0x6C] == b"\x00" * 20
    assert data[0x72 : 0x72 + 46] == b"\x00" * 46
    assert sorted(t["title"] for t in _dump(tool, str(mp))[0]) == ["New", "Old"]
    assert not check(Database(str(mp)))


def test_unidentified_signed_classic_resigned_libgpod_reads(tool, tmp_path):
    """A signed Classic opened with a SysInfo that names no model (FirewireGuid
    only) is re-signed on save; with the full SysInfo back, libgpod reads every track and
    doctor is clean."""
    from pygpod.doctor import check

    guid = "000A270012345678"
    mp = tmp_path / "ipod"
    mp.mkdir()
    full = f"ModelNumStr: xB029\nFirewireGuid: 0x{guid}\n"
    init_ipod(str(mp), name="iPod", sysinfo_files={"SysInfo": full.encode()})
    sysinfo = mp / "iPod_Control" / "Device" / "SysInfo"
    sysinfo.write_text(f"FirewireGuid: 0x{guid}\n")
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    for title in ("Один", "Два"):
        db = Database(str(mp))
        assert not db.device.checksum_known
        db.add_track(mp3, title=title, copy=True)
        db.save()
        data = db_path.read_bytes()
        assert data[0x58:0x6C] != b"\x00" * 20 and compute_hash58(data, guid) == data
    assert [i.kind for i in check(Database(str(mp)))] == ["model_unidentified"]
    sysinfo.write_text(full)
    assert sorted(t["title"] for t in _dump(tool, str(mp))[0]) == ["Два", "Один"]
    assert not check(Database(str(mp)))


def _dump_chapters(tool, mp):
    out = subprocess.run(
        [tool, "dump-chapters", mp], capture_output=True, text=True, check=True
    ).stdout
    result = []
    for line in out.splitlines():
        f = line.split("\t")
        chapters = [c.split(":", 1) for c in f[3].split("|")] if f[3] else []
        result.append((f[1], [(int(s), t) for s, t in chapters]))
    return result


def _mhod17_bodies(data):
    """Bodies of the MHOD type 17 records in an iTunesDB, in file order."""
    import struct

    bodies, i = [], 0
    while True:
        i = data.find(b"mhod", i)
        if i < 0:
            return bodies
        if struct.unpack_from("<I", data, i + 12)[0] == 17:
            header_len, total_len = struct.unpack_from("<II", data, i + 4)
            bodies.append(data[i + header_len : i + total_len])
        i += 4


@pytest.mark.parametrize("model", ["xA003", "xB029"])
def test_chapters_match_libgpod(tool, tmp_path, model):
    """Libgpod-written chapters are read by pygpod and pygpod writes the same MHOD 17
    bytes; chapters pygpod imports from a file are read back by libgpod."""
    from pygpod.chapters import read_chapters

    from .test_chapters_import import STORED, make_mp3_with_chap

    sysinfo = f"ModelNumStr: {model}\nFirewireGuid: 0x{GUID}\n"
    lg = str(tmp_path / "lg")
    for d in ("iTunes", "Device", "Music/F00"):
        os.makedirs(os.path.join(lg, "iPod_Control", d))
    with open(os.path.join(lg, "iPod_Control", "Device", "SysInfo"), "w") as f:
        f.write(sysinfo)
    mp3 = make_mp3_with_chap(str(tmp_path / "book.mp3"))
    chapters = read_chapters(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(
        f"track\t{mp3}\tКнига\tОкеан Ельзи\tОбійми\tAudiobook\t1\t2020\t8\t0\t0\n"
        f"track\t{mp3}\tБез розділів\tОкеан Ельзи\tОбійми\tRock\t2\t2020\t1\t0\t0\n"
        "chapters\t0\t" + "|".join(f"{s}:{t}" for s, t in chapters) + "\n"
    )
    subprocess.run([tool, "build", lg, str(spec)], check=True)
    lg_db = Database(lg)
    assert [t.chapters.to_list() if t.chapters else None for t in lg_db.tracks] == [STORED, None]
    lg_bytes = open(os.path.join(lg, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    lg_db.save()  # re-written by pygpod: chapter data kept as is
    resaved = open(os.path.join(lg, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    assert _mhod17_bodies(resaved) == _mhod17_bodies(lg_bytes)
    assert _dump_chapters(tool, lg) == [("Книга", STORED), ("Без розділів", [])]

    py = str(tmp_path / "py")
    os.makedirs(py)
    init_ipod(py, name="iPod", sysinfo_files={"SysInfo": sysinfo.encode()})
    db = Database(py)
    db.add_track(mp3, title="Книга", media_type=0x08)
    db.add_track(mp3, title="Без розділів", chapters=[])
    db.save()
    assert _dump_chapters(tool, py) == [("Книга", STORED), ("Без розділів", [])]
    py_bytes = open(os.path.join(py, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    assert _mhod17_bodies(py_bytes) == _mhod17_bodies(lg_bytes)
    assert len(_mhod17_bodies(py_bytes)) == 1


def test_imported_tags_read_by_libgpod(tool, ipod, tmp_path):
    """Compilation, sort MHODs, rating, BPM, grouping, advisory and podcast fields
    imported from the file are read by libgpod."""
    from .test_tag_import import EXPECTED, PODCAST, RELEASED, make_mp3

    mp, _ = ipod
    db = Database(mp)
    db.add_track(make_mp3(str(tmp_path / "tags.mp3")), media_type=0x04)
    db.save()
    out = subprocess.run([tool, "dump-tags", mp], capture_output=True, text=True, check=True)
    (line,) = out.stdout.splitlines()
    f = line.split("\t")
    assert f[0:3] == ["G", "Обійми", "1"]
    assert f[3:8] == [
        EXPECTED[k]
        for k in ("sort_artist", "sort_title", "sort_album", "sort_albumartist", "sort_composer")
    ]
    assert [int(f[9]), int(f[10]), f[11], int(f[12])] == [80, 124, EXPECTED["grouping"], 1]
    assert f[13] == PODCAST["description"]
    # same day; libgpod reads Mac timestamps as device-local time (a UTC offset apart)
    assert abs(int(f[16]) - int(RELEASED.timestamp())) <= 14 * 3600
    assert f[17:19] == [PODCAST["category"], PODCAST["keywords"]]


def test_imported_podcast_urls_read_by_libgpod(tool, ipod, tmp_path):
    """Podcast URL (WFED) and RSS/GUID (TGID) imported from the file reach libgpod."""
    from .test_tag_import import PODCAST, make_mp3

    mp, _ = ipod
    db = Database(mp)
    db.add_track(make_mp3(str(tmp_path / "tags.mp3")), media_type=0x04)
    db.save()
    out = subprocess.run([tool, "dump-tags", mp], capture_output=True, text=True, check=True)
    f = out.stdout.splitlines()[0].split("\t")
    assert f[14:16] == [PODCAST["podcast_url"], PODCAST["podcast_rss"]]


def _mhod_bodies(data, mhod_type):
    import struct

    bodies, i = [], 0
    while True:
        i = data.find(b"mhod", i)
        if i < 0:
            return bodies
        if struct.unpack_from("<I", data, i + 12)[0] == mhod_type:
            header_len, total_len = struct.unpack_from("<II", data, i + 4)
            bodies.append(data[i + header_len : i + total_len])
        i += 4


def test_podcast_urls_match_libgpod(tool, tmp_path):
    """Podcast URL / RSS MHODs (15/16) hold plain UTF-8 after the header, as libgpod writes
    them: pygpod reads libgpod's, keeps them on a re-save and writes the same bytes."""
    url, rss = "https://example.org/випуск-1.mp3", "urn:епізод:42"
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    lg = tmp_path / "lg"
    for d in ("iTunes", "Device", "Music/F00"):
        (lg / "iPod_Control" / d).mkdir(parents=True)
    (lg / "iPod_Control" / "Device" / "SysInfo").write_text("ModelNumStr: xA003\n")
    spec = tmp_path / "spec.tsv"
    spec.write_text(
        f"track\t{mp3}\tВипуск\tРадіо\tПодкаст\tPodcast\t1\t2020\t4\t0\t0\n"
        f"podcast\t0\t{url}\t{rss}\n"
    )
    subprocess.run([tool, "build", str(lg), str(spec)], check=True)
    lg_bytes = (lg / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    db = Database(str(lg))
    assert (db.tracks[0].podcast_url, db.tracks[0].podcast_rss) == (url, rss)
    db.tracks[0].title = "Випуск 1"
    db.save()
    resaved = (lg / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    for mhod_type in (15, 16):
        assert _mhod_bodies(resaved, mhod_type) == _mhod_bodies(lg_bytes, mhod_type)

    py = tmp_path / "py"
    py.mkdir()
    init_ipod(str(py), name="iPod", sysinfo_files={"SysInfo": b"ModelNumStr: xA003\n"})
    db = Database(str(py))
    db.add_track(mp3, title="Випуск", media_type=0x04, podcast_url=url, podcast_rss=rss)
    db.save()
    py_bytes = (py / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    for mhod_type in (15, 16):
        assert _mhod_bodies(py_bytes, mhod_type) == _mhod_bodies(lg_bytes, mhod_type)
    out = subprocess.run([tool, "dump-tags", str(py)], capture_output=True, text=True, check=True)
    assert out.stdout.split("\t")[14:16] == [url, rss]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_analyzed_soundcheck_read_by_libgpod(tool, ipod, tmp_path):
    """A Sound Check measured with soundcheck='analyze' is what libgpod reads."""
    from .test_soundcheck_analyze import _tone

    mp, _ = ipod
    db = Database(mp)
    track = db.add_track(_tone(str(tmp_path / "Обійми.mp3"), -12), soundcheck="analyze")
    db.save()
    out = subprocess.run([tool, "dump-video", mp], capture_output=True, text=True, check=True)
    assert int(out.stdout.split("\t")[18]) == track.soundcheck > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_estimated_gapless_read_by_libgpod(tool, ipod, tmp_path):
    """Estimated gapless fields of an MP3 without LAME tag are what libgpod reads."""
    from .test_gapless_estimate import _mp3

    mp, _ = ipod
    db = Database(mp)
    t = db.add_track(_mp3(str(tmp_path / "Без тегу.mp3")), gapless="estimate")
    db.save()
    out = subprocess.run([tool, "dump-video", mp], capture_output=True, text=True, check=True)
    f = out.stdout.split("\t")
    assert [int(v) for v in f[19:24]] == [
        t.pregap,
        t.sample_count,
        t.postgap,
        t.gapless_data,
        1,
    ]


# ---- library sort indexes (MHOD 52/53) and album/artist lists ----

_SORT_SPEC = [
    # title, artist, album, genre, track_nr, composer, cd_nr, sort_title, sort_artist,
    # sort_album, sort_composer, albumartist
    ("Hello", "Abba", "Gold", "Pop", 1, "", 1, "", "", "", "", ""),
    ("1999", "Prince", "1999", "Funk", 1, "Prince", 1, "", "", "", "", ""),
    ("(Live) Обійми", "Океан Ельзи", "Глорія", "Rock", 2, "Вакарчук", 1, "", "", "", "", ""),
    ("ґанок", "єва", "ї", "Folk", 1, "", 0, "", "", "", "", ""),
    ("Let It Be", "The Beatles", "Let It Be", "Rock", 6, "", 1, "", "", "", "", ""),
    ("abba song", "abba", "éclair", "pop", 3, "...", 2, "", "", "", "", ""),
    ("Їжак", "Я", "Ігри", "Rock", 1, "", 1, "Ялинка", "Артист", "Альбом", "Композитор", "Збірка"),
    ("Something", "The Beatles", "Abbey Road", "Rock", 2, "Harrison", 1, "", "Beatles", "", "", ""),
]


def _sort_spec_file(tmp_path, mp3, rows):
    lines = []
    for i, r in enumerate(rows):
        lines.append(f"track\t{mp3}\t{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}\t2005\t1\t0\t0")
        lines.append("sort\t%d\t%s" % (i, "\t".join(str(v) for v in r[5:])))
    spec = tmp_path / "sort.tsv"
    spec.write_text("\n".join(lines) + "\n")
    return str(spec)


def _libgpod_build(tool, mp, spec):
    # C locale: libgpod's collation (g_utf8_collate_key) is byte order there
    env = dict(os.environ, LC_ALL="C")
    subprocess.run([tool, "build", mp, spec], check=True, env=env)


def _sort_records(data):
    """MHOD 52/53 bodies (file order) and MHIA/MHII headers of an iTunesDB."""
    import struct

    mhods, items, pos = [], [], 0
    while True:
        pos = min(
            (p for p in (data.find(m, pos) for m in (b"mhod", b"mhia", b"mhii")) if p >= 0),
            default=-1,
        )
        if pos < 0:
            return mhods, items
        if data[pos : pos + 4] == b"mhod":
            hlen, total, mtype = struct.unpack_from("<III", data, pos + 4)
            if mtype in (52, 53) and hlen == 24:
                mhods.append((mtype, data[pos + 24 : pos + total]))
        else:
            hlen = struct.unpack_from("<I", data, pos + 4)[0]
            items.append(data[pos : pos + hlen])
        pos += 4


def _member_letters(mhods):
    """[{sort type: {member position: jump letter}}] per master playlist (from 52 + 53)."""
    import struct

    tables, out = {}, []
    for mtype, body in mhods:
        sort_type, n = struct.unpack_from("<II", body, 0)
        if mtype == 52:
            tables[sort_type] = list(struct.unpack_from(f"<{n}I", body, 48))
            continue
        positions, letters = tables[sort_type], {}
        for i in range(n):
            letter, _, start, count = struct.unpack_from("<HHII", body, 16 + 12 * i)
            for member in positions[start : start + count]:
                letters[member] = letter
        if sort_type == 3:
            out.append({})
        out[-1][sort_type] = letters
    return out


def _pygpod_build(mp, mp3, rows):
    db = Database(mp)
    for r in rows:
        db.add_track(
            mp3,
            title=r[0],
            artist=r[1],
            album=r[2],
            genre=r[3],
            track_number=r[4],
            composer=r[5],
            cd_number=r[6],
            sort_title=r[7],
            sort_artist=r[8],
            sort_album=r[9],
            sort_composer=r[10],
            albumartist=r[11],
        )
    db.save()


def test_libgpod_db_resaved_unchanged_is_byte_identical(tool, tmp_path):
    mp = str(tmp_path / "lg")
    os.makedirs(mp)
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO.encode()})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    _libgpod_build(tool, mp, _sort_spec_file(tmp_path, mp3, _SORT_SPEC))
    path = os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB")
    before = open(path, "rb").read()
    Database(mp).save()
    assert open(path, "rb").read() == before

    # an edit rebuilds them: the libgpod album/artist SQLite ids of items that still exist stay
    db = Database(mp)
    db.add_track(mp3, title="Новий", artist="Abba", album="Gold")
    db.save()
    _, lg_items = _sort_records(before)
    _, items = _sort_records(open(path, "rb").read())
    assert {i[0x14:0x1C] for i in lg_items} <= {i[0x14:0x1C] for i in items}
    assert all(i[0x1C:0x20] == b"\x02\x00\x00\x00" for i in items)
    tracks, _ = _dump(tool, mp)
    assert len(tracks) == len(_SORT_SPEC) + 1


def test_sort_index_letters_match_libgpod(tool, tmp_path):
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    built = {}
    for who in ("libgpod", "pygpod"):
        mp = str(tmp_path / who)
        os.makedirs(mp)
        init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO.encode()})
        if who == "libgpod":
            _libgpod_build(tool, mp, _sort_spec_file(tmp_path, mp3, _SORT_SPEC))
        else:
            _pygpod_build(mp, mp3, _SORT_SPEC)
        built[who] = _sort_records(open(f"{mp}/iPod_Control/iTunes/iTunesDB", "rb").read())
    (lg_mhods, lg_items), (py_mhods, py_items) = built["libgpod"], built["pygpod"]
    assert [t for t, _ in py_mhods] == [t for t, _ in lg_mhods]
    lg_letters, py_letters = _member_letters(lg_mhods), _member_letters(py_mhods)
    assert py_letters == lg_letters and len(py_letters) == 2
    assert py_letters[0][3][2] == ord("L")  # '(Live) Обійми'
    assert py_letters[0][3][1] == ord("0")  # '1999'
    assert py_letters[0][18][0] == ord("0")  # empty composer
    assert py_letters[0][5][4] == ord("B")  # 'The Beatles' -> 'Beatles, The'
    assert py_letters[0][3][6] == ord("Я")  # sort_title
    assert (
        {i[0x1C:0x20] for i in py_items}
        == {i[0x1C:0x20] for i in lg_items}
        == {b"\x02\x00\x00\x00"}
    )


def test_sort_index_order_matches_libgpod_for_ascii(tool, tmp_path):
    """Same order as libgpod in the C locale for ASCII strings of one capitalisation (the
    collation differs on purpose for case, punctuation and non-ASCII letters)."""
    rows = [
        ("Delta", "The Zombies", "Odessey", "Rock", 2, "Argent", 1, "", "", "", "", ""),
        ("Alpha", "Abba", "Gold", "Pop", 1, "", 1, "", "", "", "", ""),
        ("Charlie", "Zombies", "Odessey", "Rock", 1, "Argent", 1, "", "", "", "", ""),
        ("Bravo", "Abba", "Gold", "Pop", 1, "", 2, "", "", "", "", ""),
        ("Echo", "Abba", "Arrival", "Rock", 3, "Andersson", 1, "", "", "", "", ""),
        ("Alpha", "Abba", "Gold", "Pop", 1, "", 1, "", "", "", "", ""),
        ("Foxtrot", "Blondie", "Parallel", "Pop", 1, "Harry", 1, "Golf", "Abba", "Gold", "", ""),
    ]
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    mhods = {}
    for who in ("libgpod", "pygpod"):
        mp = str(tmp_path / who)
        os.makedirs(mp)
        init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO.encode()})
        if who == "libgpod":
            _libgpod_build(tool, mp, _sort_spec_file(tmp_path, mp3, rows))
        else:
            _pygpod_build(mp, mp3, rows)
        mhods[who] = _sort_records(open(f"{mp}/iPod_Control/iTunes/iTunesDB", "rb").read())[0]
    assert mhods["pygpod"] == mhods["libgpod"]
