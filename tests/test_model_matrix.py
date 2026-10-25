"""Model matrix: the full write path for every iPod generation/capacity class in
pygpod's model table, on emulated volumes.

Per model: SysInfo (+ SysInfoExtended / HashInfo where the real device has them) ->
init_ipod(name) -> add_track (with artwork where the model shows it) -> a playlist -> save.
pygpod-only checks: the model is identified, the database is signed with the scheme of that
model and the signature is valid, doctor.check() finds nothing, an unchanged re-save is
byte-identical, shuffles get an iTunesSD. With libgpod as the judge (marker ``libgpod``):
libgpod reads tracks, playlist, file paths and artwork back, writes the same checksum scheme
for that model, and writes the same iTunesSD for shuffles. hashAB models (nano 6G, and the
nano 7G pygpod does not know) cannot be signed: save() raises HashError and writes nothing.

One representative per generation runs by default; the other capacity classes are ``slow``.
mhbd offset 0x30 (hashing scheme) is checked in test_libgpod_interop.py.
"""

import os
import plistlib
import subprocess
from typing import NamedTuple

import pytest

from pygpod import HashError, init_ipod
from pygpod.device.artwork_formats import ThumbFormat, get_cover_art_formats
from pygpod.device.models import IpodGeneration as G
from pygpod.doctor import check
from pygpod.hash.hash58 import compute_hash58
from pygpod.hash.hash72 import compute_hash72
from pygpod.model.database import Database

from .conftest import _make_minimal_png
from .test_libgpod_interop import _dump

GUID = "000A270012345678"
IV = bytes(range(0x64, 0x74))
RND = bytes(range(1, 13))
NAME = "Мій iPod"
TITLE, ARTIST, ALBUM = "Обійми", "Океан Ельзи", "Ґанок"
PLAYLIST = "Улюблене"
ZERO20 = b"\x00" * 20
# last three characters of the serial number of the models with a SysInfoExtended row
# (libgpod's serial_to_model_mapping); the nano 7G is in neither table
SERIAL_SUFFIX = {
    "xB029": "Y5N",
    "xC293": "9ZS",
    "xA978": "Y0P",
    "xB598": "2ME",
    "xC027": "71V",
    "xC060": "72Q",
    "xC525": "CMN",
    "xC526": "CMP",
}


class Row(NamedTuple):
    model: str  # ModelNumStr
    gen: G  # expected generation (G.UNKNOWN: not in the table)
    scheme: str  # "none", "hash58", "hash72", "hashab"
    artwork: bool  # libgpod shows cover art on this model
    shuffle: bool  # iTunesSD written next to the iTunesDB
    ext: bool = False  # SysInfoExtended on the volume (as libgpod's udev callout writes it)


def _row(rid, row, slow=False):
    """One matrix row; ``slow``: an extra capacity class of an already covered generation."""
    return pytest.param(row, id=rid, marks=[pytest.mark.slow] if slow else [])


ROWS = [
    # iPod 1G-4G: no artwork, unsigned
    _row("1g-5gb", Row("M8513", G.FIRST, "none", False, False)),
    _row("1g-10gb", Row("M8709", G.FIRST, "none", False, False), True),
    _row("2g-10gb", Row("M8737", G.SECOND, "none", False, False)),
    _row("2g-20gb", Row("M8738", G.SECOND, "none", False, False), True),
    _row("3g-10gb", Row("M8976", G.THIRD, "none", False, False)),
    _row("3g-15gb", Row("M8946", G.THIRD, "none", False, False), True),
    _row("3g-20gb", Row("M9244", G.THIRD, "none", False, False), True),
    _row("3g-30gb", Row("M8948", G.THIRD, "none", False, False), True),
    _row("3g-40gb", Row("M9245", G.THIRD, "none", False, False), True),
    _row("4g-20gb", Row("M9282", G.FOURTH, "none", False, False)),
    _row("4g-u2-25gb", Row("M9787", G.FOURTH, "none", False, False), True),
    _row("4g-40gb", Row("M9268", G.FOURTH, "none", False, False), True),
    _row("4g-hp-40gb", Row("ME436", G.FOURTH, "none", False, False), True),
    # mini
    _row("mini1-4gb", Row("M9160", G.MINI_1, "none", False, False)),
    _row("mini2-4gb", Row("M9800", G.MINI_2, "none", False, False)),
    _row("mini2-6gb", Row("M9801", G.MINI_2, "none", False, False), True),
    # photo / color
    _row("photo-20gb", Row("MA079", G.PHOTO, "none", True, False)),
    _row("photo-30gb", Row("M9829", G.PHOTO, "none", True, False), True),
    _row("photo-40gb", Row("M9585", G.PHOTO, "none", True, False), True),
    _row("photo-60gb", Row("M9830", G.PHOTO, "none", True, False), True),
    _row("photo-hp-30gb", Row("MS492", G.PHOTO, "none", True, False), True),
    # video 5G / 5.5G
    _row("5g-30gb", Row("MA002", G.VIDEO_1, "none", True, False)),
    _row("5g-60gb", Row("MA003", G.VIDEO_1, "none", True, False), True),
    _row("5.5g-30gb", Row("MA444", G.VIDEO_2, "none", True, False)),
    _row("5.5g-80gb", Row("MA448", G.VIDEO_2, "none", True, False), True),
    # Classic 6G / 6.5G / 7G
    _row("classic6g-80gb", Row("xB029", G.CLASSIC_1, "hash58", True, False)),
    _row("classic6g-160gb", Row("xB145", G.CLASSIC_1, "hash58", True, False), True),
    _row("classic6.5g-120gb", Row("xB562", G.CLASSIC_2, "hash58", True, False)),
    _row("classic7g-160gb", Row("xC293", G.CLASSIC_3, "hash58", True, False)),
    # nano 1G-5G
    _row("nano1-1gb", Row("MA350", G.NANO_1, "none", True, False)),
    _row("nano1-2gb", Row("MA004", G.NANO_1, "none", True, False), True),
    _row("nano1-4gb", Row("MA005", G.NANO_1, "none", True, False), True),
    _row("nano2-2gb", Row("MA477", G.NANO_2, "none", True, False)),
    _row("nano2-4gb", Row("MA426", G.NANO_2, "none", True, False), True),
    _row("nano2-8gb", Row("MA497", G.NANO_2, "none", True, False), True),
    _row("nano3-4gb", Row("xA978", G.NANO_3, "hash58", True, False)),
    _row("nano3-8gb", Row("xA980", G.NANO_3, "hash58", True, False), True),
    _row("nano4-4gb", Row("xB480", G.NANO_4, "hash58", True, False)),
    _row("nano4-8gb", Row("xB598", G.NANO_4, "hash58", True, False), True),
    _row("nano4-16gb", Row("xB903", G.NANO_4, "hash58", True, False), True),
    # the same with the SysInfoExtended libgpod's udev callout reads over SCSI (libgpod then
    # takes the model from SerialNumber, the scheme from DBVersion and the art formats from it)
    _row("classic6g-80gb-ext", Row("xB029", G.CLASSIC_1, "hash58", True, False, True), True),
    _row("classic7g-160gb-ext", Row("xC293", G.CLASSIC_3, "hash58", True, False, True)),
    _row("nano3-4gb-ext", Row("xA978", G.NANO_3, "hash58", True, False, True)),
    _row("nano4-8gb-ext", Row("xB598", G.NANO_4, "hash58", True, False, True)),
    # nano 5G: SysInfoExtended (DBVersion 4) always, else libgpod writes an iTunesCDB
    _row("nano5-8gb", Row("xC027", G.NANO_5, "hash72", True, False, True)),
    _row("nano5-16gb", Row("xC060", G.NANO_5, "hash72", True, False, True), True),
    # shuffles
    _row("shuffle1-512mb", Row("M9724", G.SHUFFLE_1, "none", False, True)),
    _row("shuffle1-1gb", Row("M9725", G.SHUFFLE_1, "none", False, True), True),
    _row("shuffle2-1gb", Row("MA546", G.SHUFFLE_2, "none", False, True)),
    _row("shuffle2-2gb", Row("MB518", G.SHUFFLE_2, "none", False, True), True),
    _row("shuffle3-2gb", Row("MC306", G.SHUFFLE_3, "none", False, True)),
    _row("shuffle3-4gb", Row("MB867", G.SHUFFLE_3, "none", False, True), True),
    _row("shuffle4-2gb", Row("MC584", G.SHUFFLE_4, "none", False, True)),
]

HASHAB_ROWS = [
    _row("nano6-8gb", Row("xC525", G.NANO_6, "hashab", True, False, True)),
    _row("nano6-16gb", Row("xC526", G.NANO_6, "hashab", True, False, True), True),
    # nano 7G (MD480): not in pygpod's (or libgpod's) table; SysInfoExtended says hashAB
    _row("nano7-16gb-unknown", Row("xD480", G.UNKNOWN, "hashab", True, False, True)),
]


@pytest.fixture(autouse=True)
def _no_usb(monkeypatch):
    """Emulated volumes only: model detection must never fall back to the real USB bus."""
    import pygpod.device.usb

    monkeypatch.setattr(pygpod.device.usb, "detect_ipod_usb", lambda *a, **k: [])


@pytest.fixture(scope="module")
def cover(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("cover") / "cover.png")
    _make_minimal_png(path, 200, 200, (0, 87, 183))
    return path


def _device_files(row):
    """SysInfo files of an emulated device: SysInfo, SysInfoExtended, HashInfo."""
    files = {"SysInfo": f"ModelNumStr: {row.model}\nFirewireGuid: 0x{GUID}\n".encode()}
    if row.ext:
        ext = {
            "FireWireGUID": GUID,
            "DBVersion": {"hash58": 3, "hash72": 4, "hashab": 5}[row.scheme],
            "SerialNumber": "8K1483" + SERIAL_SUFFIX.get(row.model, "XXX"),
        }
        formats = get_cover_art_formats(row.gen) or []
        assert all(f.pixel_format is ThumbFormat.RGB565_LE for f in formats)
        ext["AlbumArt"] = [  # as the iPod reports it: PixelFormat is a hex string ('L565')
            {
                "FormatId": f.format_id,
                "RenderWidth": f.width,
                "RenderHeight": f.height,
                "PixelFormat": "4C353635",
            }
            for f in formats
        ]
        files["SysInfoExtended"] = plistlib.dumps(ext)
    if row.scheme == "hash72":
        uuid = bytes.fromhex(GUID).ljust(20, b"\x00")
        files["HashInfo"] = b"HASHv0" + uuid + RND + IV
    return files


def _volume(path, row):
    """An empty emulated volume with the device's files in iPod_Control/Device."""
    dev = path / "iPod_Control" / "Device"
    dev.mkdir(parents=True)
    for name, data in _device_files(row).items():
        (dev / name).write_bytes(data)
    return path


def _files(mp):
    """Database files pygpod writes (None where absent)."""
    out = {}
    for rel in ("iTunes/iTunesDB", "iTunes/iTunesSD", "Artwork/ArtworkDB"):
        p = os.path.join(mp, "iPod_Control", *rel.split("/"))
        out[rel] = open(p, "rb").read() if os.path.isfile(p) else None
    return out


def _write_with_pygpod(tmp_path, row, mp3, cover):
    """init_ipod -> add_track (+ cover) -> playlist -> save; returns the mount point."""
    mp = _volume(tmp_path / "py", row)
    files = _device_files(row)
    init_ipod(str(mp), name=NAME, sysinfo_files={k: files[k] for k in files if k != "HashInfo"})
    db = Database(str(mp))
    assert db.device.generation is row.gen
    t = db.add_track(mp3, title=TITLE, artist=ARTIST, album=ALBUM, cover_art=cover)
    db.set_playlist(PLAYLIST, [t])
    return str(mp), db, t


def _assert_signed(data, row):
    if row.scheme == "none":
        assert data[0x58:0x6C] == ZERO20, "hash58 area of an unsigned model is not zero"
        assert data[0x72 : 0x72 + 46] == b"\x00" * 46, "hash72 area of an unsigned model"
    elif row.scheme == "hash58":
        assert data[0x58:0x6C] != ZERO20
        assert compute_hash58(data, GUID) == data, "hash58 signature invalid"
    elif row.scheme == "hash72":
        assert data[0x72] == 0x01
        assert compute_hash72(data, iv=IV, random_bytes=RND) == data, "hash72 signature invalid"


@pytest.mark.parametrize("row", ROWS)
def test_pygpod_writes_model(tmp_path, row, generated_mp3_with_art, cover):
    mp, db, t = _write_with_pygpod(tmp_path, row, generated_mp3_with_art, cover)
    if row.artwork:  # models without artwork: test_no_artwork_on_models_without_it
        assert t.has_artwork
    db.save()

    first = _files(mp)
    _assert_signed(first["iTunes/iTunesDB"], row)
    assert (first["iTunes/iTunesSD"] is not None) is row.shuffle

    db = Database(mp)
    assert [(x.title, x.artist, x.album) for x in db.tracks] == [(TITLE, ARTIST, ALBUM)]
    assert db.master_playlist.name == NAME
    assert [x.title for x in db.get_playlist(PLAYLIST).tracks] == [TITLE]
    if row.artwork:
        assert db.tracks[0].has_artwork
    report = check(db)
    assert not report, [(i.kind, i.subject, i.message) for i in report]

    db.save()  # unchanged re-save of a pygpod-written database: byte-identical
    assert _files(mp) == first


@pytest.mark.libgpod
@pytest.mark.parametrize("row", ROWS)
def test_libgpod_reads_model(tool, tmp_path, row, generated_mp3_with_art, cover):
    mp, db, _ = _write_with_pygpod(tmp_path, row, generated_mp3_with_art, cover)
    db.save()

    tracks, playlists = _dump(tool, mp)
    assert [(x["title"], x["artist"], x["album"]) for x in tracks] == [(TITLE, ARTIST, ALBUM)]
    assert os.path.isfile(os.path.join(mp, *tracks[0]["path"].strip(":").split(":")))
    assert playlists[NAME]["mpl"] and playlists[NAME]["members"] == [TITLE]
    assert playlists[PLAYLIST]["members"] == [TITLE]
    video = subprocess.run([tool, "dump-video", mp], capture_output=True, text=True, check=True)
    v = video.stdout.splitlines()[0].split("\t")
    assert v[1] == TITLE
    assert (v[17] == "1") is row.artwork, "libgpod thumbnails"

    # the checksum scheme libgpod writes for the same device
    lg = _volume(tmp_path / "lg", row)
    for d in ("iTunes", "Music/F00"):
        (lg / "iPod_Control" / d).mkdir(parents=True)
    spec = tmp_path / "spec.tsv"
    spec.write_text(
        f"name\t{NAME}\ntrack\t{generated_mp3_with_art}\t{TITLE}\t{ARTIST}\t{ALBUM}"
        "\tRock\t1\t2004\t1\t0\t0\n"
        f"playlist\t{PLAYLIST}\t0\t0\n",
        encoding="utf-8",
    )
    subprocess.run([tool, "build", str(lg), str(spec)], check=True, capture_output=True)
    lg_data = (lg / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    py_data = _files(mp)["iTunes/iTunesDB"]
    assert (lg_data[0x58:0x6C] != ZERO20) is (py_data[0x58:0x6C] != ZERO20), "hash58 presence"
    assert (lg_data[0x72] == 0x01) is (py_data[0x72] == 0x01), "hash72 presence"
    _assert_signed(lg_data, row)  # libgpod agrees with the expected scheme

    if row.shuffle:  # libgpod's iTunesSD for pygpod's database is the one pygpod wrote
        out = str(tmp_path / "libgpod.sd")
        subprocess.run([tool, "shuffle-sd", mp, out], check=True, capture_output=True)
        assert open(out, "rb").read() == _files(mp)["iTunes/iTunesSD"]


@pytest.mark.parametrize(
    "row",
    [pytest.param(p.values[0], id=p.id, marks=p.marks) for p in ROWS if not p.values[0].artwork],
)
def test_no_artwork_on_models_without_it(tmp_path, row, generated_mp3_with_art, cover):
    """1G-4G, mini and shuffles show no cover art: the track gets none, no thumbnails written."""
    mp, db, t = _write_with_pygpod(tmp_path, row, generated_mp3_with_art, cover)
    db.save()
    assert not t.has_artwork and not Database(mp).tracks[0].has_artwork
    art = os.path.join(mp, "iPod_Control", "Artwork")
    assert not [f for f in os.listdir(art) if f.endswith(".ithmb")]


@pytest.mark.parametrize("row", HASHAB_ROWS)
def test_hashab_model_save_raises(tmp_path, row, generated_mp3_with_art, cover):
    """hashAB is not implemented (documented): save() raises HashError, nothing is written."""
    mp, db, _ = _write_with_pygpod(tmp_path, row, generated_mp3_with_art, cover)
    before = _files(mp)
    with pytest.raises(HashError):
        db.save()
    assert _files(mp) == before
