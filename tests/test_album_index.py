"""Album list (MHSD type 4): compilations are one album, album artist wins over track artist."""

import os
import struct

import pytest

from pygpod.db.constants import MHOD_ID_ALBUM_ALBUM, MHOD_ID_ALBUM_ARTIST
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


@pytest.fixture
def db(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    d = Database(mp)
    d._mp3 = mp3
    return d


def _albums(db):
    db.save()
    db2 = Database(db.mountpoint)
    mhsd4 = next(c for c in db2._root.children if c.fields.get("mhsd_type") == 4)
    out = {}
    for mhia in mhsd4.children[0].children:
        out[mhia.fields.get("album_id")] = (
            mhia.get_mhod(MHOD_ID_ALBUM_ALBUM),
            mhia.get_mhod(MHOD_ID_ALBUM_ARTIST),
        )
    links = {}
    for t in db2.tracks:
        links[t.title] = struct.unpack_from("<I", t.record.raw_header, 0x120)[0]
    return out, links


def test_compilation_with_different_artists_is_one_album(db):
    for i, artist in enumerate(["Океан Ельзи", "Скрябін", "ДахаБраха"]):
        db.add_track(db._mp3, title=f"t{i}", artist=artist, album="Рок-збірка", compilation=True)
    albums, links = _albums(db)
    assert len(albums) == 1 and list(albums.values()) == [("Рок-збірка", None)]
    assert len(set(links.values())) == 1 and set(links.values()) <= set(albums)


def test_album_artist_groups_tracks(db):
    db.add_track(db._mp3, title="a", artist="Kalush feat. X", album="Йо-Йо", albumartist="Kalush")
    db.add_track(db._mp3, title="b", artist="Kalush", album="Йо-Йо", albumartist="Kalush")
    albums, links = _albums(db)
    assert list(albums.values()) == [("Йо-Йо", "Kalush")]
    assert links["a"] == links["b"]


def test_missing_album_artist_falls_back_to_track_artist(db):
    db.add_track(db._mp3, title="a", artist="Океан Ельзи", album="Модель")
    db.add_track(db._mp3, title="b", artist="Океан Ельзи", album="Модель")
    albums, links = _albums(db)
    assert list(albums.values()) == [("Модель", "Океан Ельзи")] and links["a"] == links["b"]


def test_same_album_name_by_different_artists_is_two_albums(db):
    db.add_track(db._mp3, title="a", artist="Океан Ельзи", album="Greatest Hits")
    db.add_track(db._mp3, title="b", artist="Queen", album="Greatest Hits")
    albums, links = _albums(db)
    assert sorted(albums.values()) == sorted(
        [("Greatest Hits", "Океан Ельзи"), ("Greatest Hits", "Queen")]
    )
    assert links["a"] != links["b"] and {links["a"], links["b"]} == set(albums)
