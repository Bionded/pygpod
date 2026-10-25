"""Cover art (ArtworkDB) thumbnails compared with libgpod.

The same track with the same non-square cover is written once by pygpod (Database.set_artwork)
and once by libgpod (itdb_track_set_thumbnails, `libgpod_tool build`) on emulated volumes;
libgpod (`libgpod_tool dump-artwork`) reads both.
"""

import os
import subprocess

import pytest

from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.db.artwork_writer import write_artworkdb
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

PIL = pytest.importorskip("PIL.Image")

pytestmark = pytest.mark.libgpod

GUID = "000A2700213749FF"
MODELS = {"classic7g": "C293", "video5g": "A002", "nano3g": "A978", "nano4g": "B732"}
# landscape 4:3 and portrait 3:4 covers, and a thin strip (scaled side rounds to 1-2 pixels)
COVERS = {"landscape": (400, 300), "portrait": (300, 400), "strip": (7, 1000)}


def _volume(path, model):
    os.makedirs(path)
    sysinfo = f"ModelNumStr: x{MODELS[model]}\nFirewireGuid: 0x{GUID}\n".encode()
    init_ipod(path, name="pygpod", sysinfo_files={"SysInfo": sysinfo})
    return path


def _dump(tool, mp):
    """{format id: {"width", "height", "size", "hpad", "vpad"}} of the only track, n decoded."""
    out = subprocess.run(
        [tool, "dump-artwork", mp], capture_output=True, text=True, check=True
    ).stdout
    thumbs, n_items, n_decoded = {}, 0, 0
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "K":
            n_items, n_decoded = int(f[2]), int(f[3])
        elif f[0] == "F":
            thumbs[int(f[2])] = dict(
                zip(("width", "height", "size", "hpad", "vpad"), map(int, f[3:8]))
            )
    assert len(thumbs) == n_items
    return thumbs, n_decoded


@pytest.fixture
def volumes(tool, tmp_path):
    def build(model, cover_size):
        cover = str(tmp_path / "Глорія.jpg")
        img = PIL.new("RGB", cover_size, (200, 30, 30))
        img.paste((240, 240, 240), (0, 0, cover_size[0], cover_size[1] // 10))
        img.save(cover)
        mp3 = str(tmp_path / "Обійми.mp3")
        _make_minimal_mp3(mp3)

        lg = _volume(str(tmp_path / "lg"), model)
        spec = tmp_path / "spec.tsv"
        spec.write_text(
            f"track\t{mp3}\tОбійми\tОкеан Ельзи\tГлорія\tRock\t1\t2005\t1\t0\t0\n"
            f"artwork\t0\t{cover}\n",
            encoding="utf-8",
        )
        subprocess.run([tool, "build", lg, str(spec)], check=True)

        py = _volume(str(tmp_path / "py"), model)
        db = Database(py)
        track = db.add_track(mp3, title="Обійми", artist="Океан Ельзи", album="Глорія")
        with open(cover, "rb") as fh:
            db.set_artwork(track, fh.read())
        db.save()
        return _dump(tool, py), _dump(tool, lg)

    return build


@pytest.mark.parametrize("cover", list(COVERS))
@pytest.mark.parametrize("model", list(MODELS))
def test_cover_art_formats_sizes_and_decoding_match_libgpod(volumes, model, cover):
    (ours, ours_decoded), (ref, ref_decoded) = volumes(model, COVERS[cover])
    assert ref and ref_decoded == len(ref)
    assert ours_decoded == len(ours)
    assert sorted(ours) == sorted(ref)
    assert {f: t["size"] for f, t in ours.items()} == {f: t["size"] for f, t in ref.items()}


@pytest.mark.parametrize("cover", list(COVERS))
@pytest.mark.parametrize("model", list(MODELS))
def test_cover_art_geometry_matches_libgpod(volumes, model, cover):
    """libgpod letterboxes: a 4:3 cover in a 128x128 slot is 128x96 plus 16 lines of padding."""
    (ours, _), (ref, _) = volumes(model, COVERS[cover])
    assert ours == ref


def _cover_thumbs(mp):
    """{format id: (mhni fields, pixel bytes)} of the only cover in the ArtworkDB."""
    root = parse_artworkdb(_read(os.path.join(mp, "iPod_Control", "Artwork", "ArtworkDB")))
    out = {}
    for mhni in _walk(root, b"mhni"):
        fmt = mhni.fields["format_id"]
        path = os.path.join(mp, "iPod_Control", "Artwork", f"F{fmt}_1.ithmb")
        with open(path, "rb") as fh:
            fh.seek(mhni.fields["ithumb_offset"])
            out[fmt] = (mhni.fields, fh.read(mhni.fields["image_size"]))
    return out


def _walk(rec, magic):
    if rec.magic == magic:
        yield rec
    for child in rec.children:
        yield from _walk(child, magic)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


@pytest.mark.parametrize("size", [(300, 300), (400, 300)])
def test_cover_pixels_square_unchanged_non_square_letterboxed(tmp_path, size):
    """A square cover fills the slot (same pixels as a plain resize); others get black bars."""
    from pygpod.model.artwork import PixelFormat, _encode

    cover = tmp_path / "Глорія.png"
    img = PIL.new("RGB", size, (200, 30, 30))
    img.save(cover)
    mp = _volume(str(tmp_path / "py"), "classic7g")
    mp3 = str(tmp_path / "Обійми.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    db.set_artwork(db.add_track(mp3, title="Обійми"), cover.read_bytes())
    db.save()

    thumbs = _cover_thumbs(mp)
    assert sorted(thumbs) == [1055, 1060, 1061, 1068]
    for fmt, (fields, data) in thumbs.items():
        slot = {1055: 128, 1060: 320, 1061: 56, 1068: 128}[fmt]
        if size[0] == size[1]:
            want = _encode(img.resize((slot, slot), PIL.LANCZOS), PixelFormat.RGB565_LE)
            assert data == want
            assert (fields["vertical_padding"], fields["horizontal_padding"]) == (0, 0)
        else:
            scaled_h = {128: 96, 320: 240, 56: 43}[slot]
            vpad = (slot - scaled_h) // 2
            assert (fields["image_width"], fields["image_height"]) == (slot, scaled_h + vpad)
            assert fields["vertical_padding"] == vpad
            assert not any(data[: slot * vpad * 2])  # black bar on top
            assert any(data[slot * vpad * 2 : slot * (vpad + 1) * 2])  # then the image

    raw = _read(os.path.join(mp, "iPod_Control", "Artwork", "ArtworkDB"))
    assert write_artworkdb(parse_artworkdb(raw)) == raw
