"""Replacing a saved track's cover with Database.set_artwork() in a later session.

The cover is swapped in place (no remove/add of the track): after reopening, the track's
thumbnails show the new image, the ArtworkDB holds no image of the old one, and libgpod
reads the result. set_artwork(track, None) leaves the track without a cover.
"""

import os
import struct
import subprocess

import pytest

from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

PIL = pytest.importorskip("PIL.Image")

RED, BLUE = (255, 0, 0), (0, 0, 255)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _cover(tmp_path, name, color):
    path = tmp_path / f"{name}.png"
    PIL.new("RGB", (300, 300), color).save(str(path))
    return path.read_bytes()


def _walk(rec, magic):
    if rec.magic == magic:
        yield rec
    for child in rec.children:
        yield from _walk(child, magic)


def _artworkdb(mp):
    return parse_artworkdb(_read(os.path.join(mp, "iPod_Control", "Artwork", "ArtworkDB")))


def _center_pixels(mp):
    """{format id: (r, g, b)} of the middle pixel of every RGB565 thumbnail."""
    out = {}
    for mhni in _walk(_artworkdb(mp), b"mhni"):
        f = mhni.fields
        path = os.path.join(mp, "iPod_Control", "Artwork", f"F{f['format_id']}_1.ithmb")
        with open(path, "rb") as fh:
            fh.seek(f["ithumb_offset"])
            data = fh.read(f["image_size"])
        w, h = f["image_width"], f["image_height"]
        if len(data) != w * h * 2:
            continue  # padded or non-RGB565 formats: covered by the other artwork tests
        (px,) = struct.unpack_from("<H", data, ((h // 2) * w + w // 2) * 2)
        out[f["format_id"]] = ((px >> 11) << 3, ((px >> 5) & 0x3F) << 2, (px & 0x1F) << 3)
    return out


def _dominant(rgb):
    return max(range(3), key=lambda i: rgb[i])


@pytest.fixture
def classic(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xC293\nFirewireGuid: 0x000A2700213749FF\n"
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": sysinfo})
    return mp


@pytest.fixture
def saved_with_red(classic, tmp_path):
    mp3 = str(tmp_path / "Обійми.mp3")
    _make_minimal_mp3(mp3)
    db = Database(classic)
    db.add_track(mp3, title="Обійми", artist="Океан Ельзи", cover_art=_cover(tmp_path, "r", RED))
    db.save()
    return classic


def test_replace_cover_of_saved_track(saved_with_red, tmp_path):
    mp = saved_with_red
    pixels = _center_pixels(mp)
    assert pixels and all(_dominant(p) == 0 for p in pixels.values())
    (old_image,) = list(_walk(_artworkdb(mp), b"mhii"))

    db = Database(mp)
    (track,) = db.tracks
    dbid = track.dbid
    db.set_artwork(track, _cover(tmp_path, "b", BLUE))
    db.save()

    again = Database(mp)
    (track,) = again.tracks
    assert track.dbid == dbid and track.has_artwork and track.artwork_count == 1
    images = list(_walk(_artworkdb(mp), b"mhii"))
    assert len(images) == 1  # the red image is gone, not orphaned
    assert images[0].fields["image_id"] == track.mhii_link
    assert images[0].fields["image_id"] != old_image.fields["image_id"]
    pixels = _center_pixels(mp)
    assert pixels and all(_dominant(p) == 2 for p in pixels.values())
    assert again.check().ok


def test_remove_cover_of_saved_track(saved_with_red):
    mp = saved_with_red
    db = Database(mp)
    db.set_artwork(db.tracks[0], None)
    db.save()

    again = Database(mp)
    (track,) = again.tracks
    assert not track.has_artwork and track.artwork_count == 0 and track.mhii_link == 0
    assert not list(_walk(_artworkdb(mp), b"mhii"))
    assert again.check().ok


@pytest.mark.libgpod
def test_libgpod_reads_replaced_cover(tool, saved_with_red, tmp_path):
    mp = saved_with_red
    db = Database(mp)
    db.set_artwork(db.tracks[0], _cover(tmp_path, "b", BLUE))
    db.save()
    out = subprocess.run(
        [tool, "dump-artwork", mp], capture_output=True, text=True, check=True
    ).stdout
    counts = [line.split("\t") for line in out.splitlines() if line.startswith("K\t")]
    assert len(counts) == 1
    n_items, n_decoded = int(counts[0][2]), int(counts[0][3])
    assert n_items > 0 and n_decoded == n_items
