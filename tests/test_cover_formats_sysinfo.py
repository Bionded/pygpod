"""Cover art formats from the device's SysInfoExtended (AlbumArt), as libgpod reads them.

A Classic 7G's SysInfoExtended (serial number and FireWire GUID replaced) lists its cover
formats under AlbumArt; they differ from the static table: an extra 1069 142x142, 1061 is
55x55 stored in 56-pixel rows, 1055 and 1060 are cropped to fill the slot, 1068 has white
bars. libgpod uses these specifications whenever the file has them; pygpod must write the same
formats, geometry, sizes and order, which libgpod (`libgpod_tool build` / `dump-artwork`)
checks.
"""

import os
import pathlib
import struct
import subprocess

import pytest

from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.device.artwork_formats import ThumbFormat, get_cover_art_formats
from pygpod.device.device import Device
from pygpod.device.models import IpodGeneration
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

PIL = pytest.importorskip("PIL.Image")

FIXTURE = pathlib.Path(__file__).parent / "data" / "classic7g_SysInfoExtended"
GUID = "000A27001A2B3C4D"
L565 = ThumbFormat.RGB565_LE
# landscape 4:3, portrait 3:4, square, and a thin strip (scaled side rounds to 1-2 pixels)
COVERS = {"landscape": (400, 300), "portrait": (300, 400), "square": (300, 300), "strip": (7, 1000)}


def _volume(path, ext=True):
    os.makedirs(path)
    files = {"SysInfo": f"ModelNumStr: xC293\nFirewireGuid: 0x{GUID}\n".encode()}
    if ext:
        files["SysInfoExtended"] = FIXTURE.read_bytes()
    init_ipod(path, name="pygpod", sysinfo_files=files)
    return path


def _artwork(mp, name):
    return os.path.join(mp, "iPod_Control", "Artwork", name)


def _walk(rec, magic):
    if rec.magic == magic:
        yield rec
    for child in rec.children:
        yield from _walk(child, magic)


def _records(mp, magic):
    with open(_artwork(mp, "ArtworkDB"), "rb") as fh:
        return list(_walk(parse_artworkdb(fh.read()), magic))


def _cover(path, size):
    img = PIL.new("RGB", size, (200, 30, 30))
    img.paste((240, 240, 240), (0, 0, size[0], size[1] // 10))
    img.save(path)
    return path


def _mp3(tmp_path):
    path = str(tmp_path / "Обійми.mp3")
    _make_minimal_mp3(path)
    return path


def _add(mp, mp3, cover, title="Обійми"):
    db = Database(mp)
    track = db.add_track(mp3, title=title, artist="Океан Ельзи", album="Глорія")
    with open(cover, "rb") as fh:
        db.set_artwork(track, fh.read())
    db.save()
    return track


# -------------------------------------------------------------------------
# the device's formats
# -------------------------------------------------------------------------
def test_device_cover_formats_prefer_sysinfo_extended(tmp_path):
    dev = Device.from_mountpoint(_volume(str(tmp_path / "ipod")))
    formats = dev.get_cover_art_formats()
    assert [(f.format_id, f.width, f.height) for f in formats] == [
        (1069, 142, 142),
        (1055, 128, 128),
        (1068, 128, 128),
        (1060, 320, 320),
        (1061, 55, 55),
    ]
    assert formats == dev.get_sysinfo_cover_art_formats()
    # how a sync tool sizes its covers: the biggest side the iPod shows
    assert max(max(f.width, f.height) for f in formats) == 320


def test_without_sysinfo_extended_the_table_is_used(tmp_path):
    dev = Device.from_mountpoint(_volume(str(tmp_path / "ipod"), ext=False))
    assert dev.get_sysinfo_cover_art_formats() is None
    assert dev.get_cover_art_formats() == get_cover_art_formats(IpodGeneration.CLASSIC_3)


def test_table_volume_keeps_the_table_order_and_sizes(tmp_path):
    mp = _volume(str(tmp_path / "ipod"), ext=False)
    _add(mp, _mp3(tmp_path), _cover(str(tmp_path / "Глорія.jpg"), (400, 300)))
    mhnis = _records(mp, b"mhni")
    assert [m.fields["format_id"] for m in mhnis] == [1061, 1055, 1068, 1060]
    assert [m.fields["image_size"] for m in mhnis] == [6272, 32768, 32768, 204800]
    assert [(m.fields["format_id"], m.fields["image_size"]) for m in _records(mp, b"mhif")] == [
        (1061, 6272),
        (1055, 32768),
        (1068, 32768),
        (1060, 204800),
    ]


# -------------------------------------------------------------------------
# pixels: crop, back colour, row alignment
# -------------------------------------------------------------------------
def _thumbs(mp):
    """{format id: (mhni fields, slot bytes)} of the only cover."""
    out = {}
    for mhni in _records(mp, b"mhni"):
        fmt = mhni.fields["format_id"]
        with open(_artwork(mp, f"F{fmt}_1.ithmb"), "rb") as fh:
            fh.seek(mhni.fields["ithumb_offset"])
            out[fmt] = (mhni.fields, fh.read(mhni.fields["image_size"]))
    return out


def _pixel(data, row_width, x, y):
    return struct.unpack_from("<H", data, 2 * (y * row_width + x))[0]


def test_cropped_white_and_row_aligned_slots(tmp_path):
    mp = _volume(str(tmp_path / "ipod"))
    _add(mp, _mp3(tmp_path), _cover(str(tmp_path / "Глорія.jpg"), (400, 300)))
    thumbs = _thumbs(mp)
    # cropped formats: no bars, the image fills the slot
    for fmt in (1055, 1060):
        fields, data = thumbs[fmt]
        assert (fields["horizontal_padding"], fields["vertical_padding"]) == (0, 0)
        assert _pixel(data, fields["image_width"], 0, fields["image_height"] - 1) != 0
    # 1068: letterboxed with white bars
    fields, data = thumbs[1068]
    assert fields["vertical_padding"] == 16
    assert _pixel(data, 128, 0, 0) == 0xFFFF
    assert _pixel(data, 128, 0, 127) == 0xFFFF
    # 1069: black bars; 1061: 55 pixels in 56-pixel rows, the extra pixel in the back colour
    assert _pixel(thumbs[1069][1], 142, 0, 0) == 0
    fields, data = thumbs[1061]
    assert len(data) == 56 * 55 * 2
    assert (fields["image_width"], fields["image_height"]) == (55, 48)
    assert _pixel(data, 56, 55, 20) == 0
    assert _pixel(data, 56, 30, 20) != 0


# -------------------------------------------------------------------------
# libgpod comparison
# -------------------------------------------------------------------------
def _dump(tool, mp):
    """[(format id, width, height, size, hpad, vpad)] of the only track, and n decoded."""
    out = subprocess.run(
        [tool, "dump-artwork", mp], capture_output=True, text=True, check=True
    ).stdout
    thumbs, n_items, n_decoded = [], 0, 0
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "K":
            n_items, n_decoded = int(f[2]), int(f[3])
        elif f[0] == "F":
            thumbs.append(tuple(map(int, f[2:8])))
    assert len(thumbs) == n_items
    return thumbs, n_decoded


@pytest.fixture
def both(tool, tmp_path):
    def build(cover_size):
        cover = _cover(str(tmp_path / "Глорія.jpg"), cover_size)
        mp3 = _mp3(tmp_path)
        lg = _volume(str(tmp_path / "lg"))
        spec = tmp_path / "spec.tsv"
        spec.write_text(
            f"track\t{mp3}\tОбійми\tОкеан Ельзи\tГлорія\tRock\t1\t2005\t1\t0\t0\n"
            f"artwork\t0\t{cover}\n",
            encoding="utf-8",
        )
        subprocess.run([tool, "build", lg, str(spec)], check=True)
        py = _volume(str(tmp_path / "py"))
        _add(py, mp3, cover)
        return py, lg

    return build


@pytest.mark.libgpod
@pytest.mark.parametrize("cover", list(COVERS))
def test_libgpod_sees_the_same_covers_as_its_own(tool, both, cover):
    py, lg = both(COVERS[cover])
    (ours, ours_decoded), (ref, ref_decoded) = _dump(tool, py), _dump(tool, lg)
    assert len(ref) == 5 and ref_decoded == 5
    assert ours_decoded == len(ours)
    assert ours == ref


@pytest.mark.libgpod
@pytest.mark.parametrize("cover", ["landscape", "portrait"])
def test_on_disk_order_mhif_and_ithmb_sizes_match_libgpod(both, cover):
    py, lg = both(COVERS[cover])

    def layout(mp):
        mhnis = [(m.fields["format_id"], m.fields["image_size"]) for m in _records(mp, b"mhni")]
        mhifs = [(m.fields["format_id"], m.fields["image_size"]) for m in _records(mp, b"mhif")]
        files = {
            n: os.path.getsize(_artwork(mp, n))
            for n in os.listdir(_artwork(mp, ""))
            if n.endswith(".ithmb")
        }
        return mhnis, mhifs, files

    assert layout(py) == layout(lg)
    assert layout(py)[1][0] == (1061, 55 * 55 * 2)  # libgpod's mhif size ignores row alignment


# -------------------------------------------------------------------------
# databases written with the table: doctor and re-adding the artwork
# -------------------------------------------------------------------------
@pytest.fixture
def old_sizes(tmp_path):
    """A Classic whose covers were written with the table, then its SysInfoExtended appears."""
    mp = _volume(str(tmp_path / "ipod"), ext=False)
    cover = _cover(str(tmp_path / "Глорія.jpg"), (400, 300))
    _add(mp, _mp3(tmp_path), cover)
    with open(os.path.join(mp, "iPod_Control", "Device", "SysInfoExtended"), "wb") as fh:
        fh.write(FIXTURE.read_bytes())
    return mp, cover


def test_doctor_reports_cover_thumbnails_of_another_size(old_sizes):
    mp, _ = old_sizes
    issues = [i for i in Database(mp, recover=False).check() if i.kind == "artwork_thumbnail_size"]
    assert len(issues) == 1
    assert "1061: 6272 bytes" in issues[0].message and "needs 6160" in issues[0].message
    assert issues[0].severity == "warning" and not issues[0].fixable


def test_doctor_quiet_for_covers_in_the_device_formats(tmp_path):
    mp = _volume(str(tmp_path / "ipod"))
    _add(mp, _mp3(tmp_path), _cover(str(tmp_path / "Глорія.jpg"), (400, 300)))
    assert not [i for i in Database(mp, recover=False).check() if i.kind.startswith("artwork")]


def test_setting_the_artwork_again_fixes_old_sizes(old_sizes):
    mp, cover = old_sizes
    db = Database(mp)
    with open(cover, "rb") as fh:
        db.set_artwork(db.tracks[0], fh.read())
    db.save()
    assert not [i for i in Database(mp, recover=False).check() if i.kind.startswith("artwork")]
    assert [(m.fields["format_id"], m.fields["image_size"]) for m in _records(mp, b"mhif")] == [
        (1061, 6050),
        (1060, 204800),
        (1068, 32768),
        (1055, 32768),
        (1069, 40328),
    ]


def test_unchanged_resave_keeps_an_old_artworkdb_byte_identical(old_sizes):
    mp, _ = old_sizes
    with open(_artwork(mp, "ArtworkDB"), "rb") as fh:
        before = fh.read()
    db = Database(mp)
    db.save()
    with open(_artwork(mp, "ArtworkDB"), "rb") as fh:
        assert fh.read() == before
