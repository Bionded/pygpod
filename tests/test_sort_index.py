"""Library sort indexes (MHOD 52), jump tables (MHOD 53) and the album/artist lists."""

import os
import struct

import pytest

from pygpod.db.sortindex import (
    SORT_ALBUM,
    SORT_ARTIST,
    SORT_COMPOSER,
    SORT_GENRE,
    SORT_TITLE,
    SortEntry,
    build_indexes,
    collate_key,
    jump_letter,
    sort_artist_string,
)
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


@pytest.fixture
def ipod_volume(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A2700213749FF\n"  # Classic 6G
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": sysinfo})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    return mp, mp3


def _entry(title="", artist="", album="", genre="", composer="", track_nr=0, cd_nr=0, **sort):
    return SortEntry(
        title,
        artist,
        album,
        genre,
        composer,
        sort.get("sort_title", ""),
        sort.get("sort_artist", ""),
        sort.get("sort_album", ""),
        sort.get("sort_composer", ""),
        track_nr,
        cd_nr,
    )


def test_jump_letter_like_libgpod():
    assert jump_letter("Hello") == ord("H")
    assert jump_letter("abba") == ord("A")
    assert jump_letter("(Live) Обійми") == ord("L")
    assert jump_letter("«Обійми»") == ord("О")
    assert jump_letter("ґанок") == ord("Ґ")
    assert jump_letter("їжак") == ord("Ї")
    assert jump_letter("éclair") == ord("É")
    assert jump_letter("1999") == ord("0")
    assert jump_letter("...") == ord("0")
    assert jump_letter("") == ord("0")
    assert jump_letter(None) == ord("0")
    assert jump_letter("ßpiel") == ord("ß")  # no single-letter upper case: kept
    assert jump_letter("\U0001d400x") == 0xD835  # first UTF-16 unit


def test_sort_artist_like_libgpod():
    assert sort_artist_string("The Beatles", "") == "Beatles, The\x01\x01\x01\x01\x01"
    assert sort_artist_string("the who", "") == "who, The\x01\x01\x01\x01\x01"
    assert sort_artist_string("The Beatles", "Beatles") == "Beatles"
    assert sort_artist_string("Theatre", "") is None
    assert sort_artist_string("", "") is None


def test_collation_is_case_insensitive_and_knows_ukrainian_letters():
    words = ["Ялта", "ґанок", "Гора", "дим", "Їжак", "іній", "Иней", "Йод", "єнот", "Ехо", "Жук"]
    assert sorted(words, key=collate_key) == [
        "Гора",
        "ґанок",
        "дим",
        "Ехо",
        "єнот",
        "Жук",
        "Иней",
        "іній",
        "Їжак",
        "Йод",
        "Ялта",
    ]
    assert sorted(["Zebra", "abba", "Éclair", "eagle", "(Live) Mama"], key=collate_key) == [
        "abba",
        "eagle",
        "Éclair",
        "(Live) Mama",
        "Zebra",
    ]


def test_indexes_use_sort_fields_and_libgpod_tie_breaks():
    entries = [
        _entry("Song B", "The Zombies", "Odessey", "Rock", track_nr=2),
        _entry("Song A", "Abba", "Gold", "Rock", track_nr=1, sort_title="Zzz"),
        _entry("Song C", "Zombies", "Odessey", "Rock", track_nr=1),
        _entry("Song D", "Abba", "Gold", "Pop", cd_nr=2, track_nr=1, sort_album="Arrival"),
    ]
    got = {t: (pos, jumps) for t, pos, jumps in build_indexes(entries)}
    assert [t for t, _, _ in build_indexes(entries)] == [
        SORT_TITLE,
        SORT_ARTIST,
        SORT_ALBUM,
        SORT_GENRE,
        SORT_COMPOSER,
    ]
    assert got[SORT_TITLE][0] == [0, 2, 3, 1]  # sort_title "Zzz" last
    # "The Zombies" sorts as "Zombies, The": after "Zombies"; ties by album, disc, track
    assert got[SORT_ARTIST][0] == [3, 1, 2, 0]
    assert got[SORT_ALBUM][0] == [3, 1, 2, 0]  # "Arrival" (sort_album), Gold, Odessey 1, 2
    assert got[SORT_GENRE][0] == [3, 1, 2, 0]
    assert got[SORT_ARTIST][1] == [(ord("A"), 0, 2), (ord("Z"), 2, 2)]
    # no composer anywhere: one '0' group, ordered by album, disc, track, title
    assert got[SORT_COMPOSER] == ([3, 1, 2, 0], [(ord("0"), 0, 4)])


def _sort_mhods(data):
    """[(type, body)] of the MHOD 52/53 records in file order."""
    found, pos = [], 0
    while True:
        pos = data.find(b"mhod", pos)
        if pos < 0:
            return found
        total, mtype = struct.unpack_from("<I", data, pos + 8)[0], data[pos + 12]
        if mtype in (52, 53) and struct.unpack_from("<I", data, pos + 4)[0] == 24:
            found.append((mtype, data[pos + 24 : pos + total]))
        pos += 4


def test_unchanged_save_keeps_indexes_edit_rebuilds(ipod_volume, tmp_path):
    mp, mp3 = ipod_volume
    db = Database(mp)
    db.add_track(mp3, title="(Live) Обійми", artist="Океан Ельзи", album="Земля", composer="")
    db.add_track(mp3, title="1999", artist="The Beatles", album="Abbey Road")
    db.save()
    path = f"{mp}/iPod_Control/iTunes/iTunesDB"
    first = open(path, "rb").read()
    title53 = [b for t, b in _sort_mhods(first) if t == 53][0]
    letters = [struct.unpack_from("<H", title53, 16 + 12 * i)[0] for i in range(2)]
    assert letters == [ord("0"), ord("L")]
    composer53 = [b for t, b in _sort_mhods(first) if t == 53][4]
    assert struct.unpack_from("<H", composer53, 16)[0] == ord("0")  # empty composer

    Database(mp).save()
    assert open(path, "rb").read() == first

    db = Database(mp)
    db.tracks[1].sort_title = "Zzz"
    db.save()
    title52 = [b for t, b in _sort_mhods(open(path, "rb").read()) if t == 52][0]
    assert struct.unpack_from("<II", title52, 48) == (0, 1)  # "(Live) Обійми", then "Zzz"


def test_album_and_artist_items_like_libgpod(ipod_volume):
    mp, mp3 = ipod_volume
    db = Database(mp)
    db.add_track(mp3, title="Обійми", artist="Океан Ельзи", album="Земля")
    db.save()
    data = open(f"{mp}/iPod_Control/iTunes/iTunesDB", "rb").read()
    for magic in (b"mhia", b"mhii"):
        pos = data.find(magic)
        assert pos > 0
        assert struct.unpack_from("<Q", data, pos + 0x14)[0] == 0  # new: no SQLite id
        assert struct.unpack_from("<I", data, pos + 0x1C)[0] == 2


def test_new_record_at_a_reused_address_gets_its_album_and_artist_links(ipod_volume, monkeypatch):
    """A removed track's MHIT record may be freed and its address reused by the record of a
    track added with the same album and artist; the lists must still be rebuilt."""
    import pygpod.model.database as database_module

    mp, mp3 = ipod_volume
    db = Database(mp)
    db.add_track(mp3, title="Обійми", artist="Океан Ельзи", album="Земля")
    db.add_track(mp3, title="Ґанок", artist="Океан Ельзи", album="Земля")
    db.save()

    db = Database(mp)
    old = db.tracks[1]
    old_record_id = id(old.record)
    db.remove_track(old)
    new = db.add_track(mp3, title="Ґанок", artist="Океан Ельзи", album="Земля")

    real_id = id

    def reused_id(obj):  # the new record takes the freed record's address
        return old_record_id if obj is new.record else real_id(obj)

    monkeypatch.setattr(database_module, "id", reused_id, raising=False)
    db.save()
    monkeypatch.undo()

    data = open(f"{mp}/iPod_Control/iTunes/iTunesDB", "rb").read()
    links = []
    pos = 0
    while True:
        pos = data.find(b"mhit", pos)
        if pos < 0:
            break
        album_id = struct.unpack_from("<I", data, pos + 0x120)[0]
        artist_id = struct.unpack_from("<I", data, pos + 0x1E0)[0]
        links.append((album_id, artist_id))
        pos += 4
    assert links == [(1, 1), (1, 1)]  # one album, one artist; the new track linked too
