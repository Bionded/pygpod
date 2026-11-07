"""The iTunesDB sections a save writes match libgpod's layout.

libgpod (itdb_write) always writes the MHSD types 1, 3, 2, 4, 8, 6, 10, 5 in this order, with
empty type 6/10 track lists and an empty type 5 playlist list, and gives every album (MHIA) and
artist (MHII) list item a random 64-bit id for the SQLite databases. The iPod firmware ignores
the extra sections, but iTunes and Finder report an iPod whose database lacks them as corrupted.
"""

import os
import subprocess

import pytest

from pygpod.db.parser import parse_itunesdb
from pygpod.device.mountpoint import init_ipod
from pygpod.model.config import DatabaseConfig
from pygpod.model.database import Database
from pygpod.utils.compat import get64lint

from .conftest import _make_minimal_mp3

LIBGPOD_ORDER = [1, 3, 2, 4, 8, 6, 10, 5]


def _ipod(tmp_path, name="ipod"):
    mp = str(tmp_path / name)
    os.makedirs(os.path.join(mp, "iPod_Control", "Device"))
    with open(os.path.join(mp, "iPod_Control", "Device", "SysInfo"), "w") as f:
        f.write("ModelNumStr: xB480\nFirewireGuid: 0x000A27001234ABCD\n")  # nano 4G
    init_ipod(mp)
    return mp


def _root(mp):
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        return parse_itunesdb(f.read())


def _types(root):
    return [m.fields["mhsd_type"] for m in root.children]


def _sql_ids(root, mhsd_type):
    mhsd = next(m for m in root.children if m.fields["mhsd_type"] == mhsd_type)
    return [get64lint(item.raw_header, 0x14) for lst in mhsd.children for item in lst.children]


def _fill(mp, tmp_path, config=None):
    db = Database(mp, config=config)
    for i, (artist, album) in enumerate(
        [("Океан Ельзи", "Земля"), ("Океан Ельзи", "Без меж"), ("ДахаБраха", "Шлях")]
    ):
        mp3 = str(tmp_path / f"t{i}.mp3")
        _make_minimal_mp3(mp3)
        db.add_track(mp3, title=f"Пісня {i}", artist=artist, album=album)
    db.save()
    return db


def test_save_writes_libgpod_sections_in_order(tmp_path):
    mp = _ipod(tmp_path)
    _fill(mp, tmp_path)
    root = _root(mp)
    assert _types(root) == LIBGPOD_ORDER
    assert root.fields["num_children"] == len(LIBGPOD_ORDER)
    for m in root.children:
        if m.fields["mhsd_type"] in (5, 6, 10):
            body = m.raw_bytes[len(m.raw_header) :]
            assert body[:4] == (b"mhlp" if m.fields["mhsd_type"] == 5 else b"mhlt")
            assert int.from_bytes(body[8:12], "little") == 0  # no items
            assert len(m.raw_bytes) == 96 + 92  # as libgpod writes them


def test_album_and_artist_items_get_unique_sql_ids(tmp_path):
    mp = _ipod(tmp_path)
    _fill(mp, tmp_path)
    root = _root(mp)
    albums, artists = _sql_ids(root, 4), _sql_ids(root, 8)
    assert len(albums) == 3 and len(artists) == 2
    assert all(albums) and len(set(albums)) == 3
    assert all(artists) and len(set(artists)) == 2


def test_resave_keeps_sections_and_sql_ids(tmp_path):
    mp = _ipod(tmp_path)
    _fill(mp, tmp_path)
    path = os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB")
    with open(path, "rb") as f:
        first = f.read()
    Database(mp).save()
    with open(path, "rb") as f:
        assert f.read() == first


def test_existing_sections_kept_and_missing_ones_inserted_in_order(tmp_path):
    mp = _ipod(tmp_path)
    _fill(mp, tmp_path)
    db = Database(mp)
    root = db._root
    # A database from another writer: type 9 (Genius) at the end, its own type 5, no 6 or 10
    sec5 = next(m for m in root.children if m.fields["mhsd_type"] == 5)
    sec9 = db._create_mhsd(9)
    body = b"genius-cuid\x00\x00\x00\x00\x00"
    header = bytearray(sec9.raw_header)
    header[8:12] = (len(header) + len(body)).to_bytes(4, "little")
    sec9.raw_header = bytes(header)
    sec9.raw_bytes = bytes(header) + body
    root.children = [m for m in root.children if m.fields["mhsd_type"] not in (5, 6, 10)]
    root.children += [sec5, sec9]
    sec5_bytes, sec9_bytes = sec5.raw_bytes, sec9.raw_bytes
    db.save()
    root = _root(mp)
    assert _types(root) == LIBGPOD_ORDER + [9]
    assert root.children[-2].raw_bytes == sec5_bytes
    assert root.children[-1].raw_bytes == sec9_bytes


def test_zero_sql_ids_of_an_older_save_are_filled(tmp_path):
    mp = _ipod(tmp_path)
    _fill(mp, tmp_path)
    db = Database(mp)
    for mhsd in db._root.children:
        for lst in mhsd.children if mhsd.fields["mhsd_type"] in (4, 8) else []:
            for item in lst.children:
                item.raw_header = item.raw_header[:0x14] + bytes(8) + item.raw_header[0x1C:]
    db.save()
    root = _root(mp)
    assert all(_sql_ids(root, 4)) and all(_sql_ids(root, 8))


def test_empty_sections_can_be_turned_off(tmp_path):
    mp = _ipod(tmp_path)
    _fill(mp, tmp_path, config=DatabaseConfig(generate_empty_sections=False))
    assert _types(_root(mp)) == [1, 3, 2, 4, 8]


@pytest.mark.libgpod
def test_layout_matches_libgpod_and_libgpod_reads_it(tool, tmp_path):
    py = _ipod(tmp_path, "py")
    _fill(py, tmp_path)
    lg = _ipod(tmp_path, "lg")
    mp3 = str(tmp_path / "lg.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(f"track\t{mp3}\tПісня\tОкеан Ельзи\tЗемля\tРок\t1\t2020\t1\t0\t0\n")
    subprocess.run([tool, "build", lg, str(spec)], check=True)
    lg_root = _root(lg)
    assert _types(_root(py)) == _types(lg_root) == LIBGPOD_ORDER
    assert all(_sql_ids(lg_root, 4)) and all(_sql_ids(lg_root, 8))

    out = subprocess.run([tool, "dump", py], capture_output=True, text=True, check=True).stdout
    titles = sorted(line.split("\t")[3] for line in out.splitlines() if line.startswith("T\t"))
    assert titles == ["Пісня 0", "Пісня 1", "Пісня 2"]
