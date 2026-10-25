"""Fuller tag import: compilation, sort tags, rating, BPM, grouping, explicit,
podcast fields from ID3 / MP4 / Vorbis comments.

MP3 fixtures are made with mutagen only; M4A and FLAC need ffmpeg (skipped without it).
libgpod reading the sort MHODs back is tested in test_libgpod_interop.py.
"""

import datetime
import os
import shutil
import subprocess

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database
from pygpod.tags import _rating_from_popm, _rating_from_text, read_tags

from .conftest import _make_minimal_mp3

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

RELEASED = datetime.datetime(2003, 5, 12)
EXPECTED = {
    "compilation": True,
    "sort_artist": "Океан Ельзи, гурт",
    "sort_album": "Ґанок (видання)",
    "sort_title": "Обійми, пісня",
    "sort_albumartist": "Різні виконавці",
    "sort_composer": "Вакарчук Святослав",
    "rating": 80,
    "bpm": 124,
    "grouping": "Їжаки та ґанки",
    "explicit_flag": 1,
}
PODCAST = {
    "description": "Опис випуску про їжаків",
    "podcast_url": "https://xn--80aa0ap.xn--e1afmkfd/feed.xml",  # ID3 URL frames are Latin-1
    "podcast_rss": "urn:епізод:42",
    "time_released": RELEASED,
    "category": "Освіта",
    "keywords": "їжаки, ґанок",
}


def _ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1"] + list(args),
        check=True,
    )


def make_mp3(path, v2_version=4, podcast=True):
    from mutagen.id3 import (
        GRP1,
        ID3,
        POPM,
        TBPM,
        TCAT,
        TCMP,
        TDES,
        TDRC,
        TDRL,
        TGID,
        TIT1,
        TIT2,
        TKWD,
        TPE1,
        TSO2,
        TSOA,
        TSOC,
        TSOP,
        TSOT,
        TXXX,
        WFED,
    )

    _make_minimal_mp3(path)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Обійми"]))
    tags.add(TPE1(encoding=3, text=["Океан Ельзи"]))
    tags.add(TCMP(encoding=3, text=["1"]))
    tags.add(TSOP(encoding=3, text=[EXPECTED["sort_artist"]]))
    tags.add(TSOA(encoding=3, text=[EXPECTED["sort_album"]]))
    tags.add(TSOT(encoding=3, text=[EXPECTED["sort_title"]]))
    tags.add(TSO2(encoding=3, text=[EXPECTED["sort_albumartist"]]))
    tags.add(TSOC(encoding=3, text=[EXPECTED["sort_composer"]]))
    tags.add(POPM(email="Windows Media Player 9 Series", rating=196, count=0))
    tags.add(TBPM(encoding=3, text=["124"]))
    tags.add(TIT1(encoding=3, text=["Твір (TIT1, програє GRP1)"]))
    tags.add(GRP1(encoding=3, text=[EXPECTED["grouping"]]))
    tags.add(TXXX(encoding=3, desc="ITUNESADVISORY", text=["1"]))
    if podcast:
        tags.add(TDES(encoding=3, text=[PODCAST["description"]]))
        tags.add(WFED(encoding=3, url=PODCAST["podcast_url"]))
        tags.add(TGID(encoding=3, text=[PODCAST["podcast_rss"]]))
        tags.add(TDRL(encoding=3, text=["2003-05-12"]))
        tags.add(TDRC(encoding=3, text=["1999"]))
        tags.add(TCAT(encoding=3, text=[PODCAST["category"]]))
        tags.add(TKWD(encoding=3, text=[PODCAST["keywords"]]))
    tags.save(path, v2_version=v2_version)
    return path


def make_m4a(path):
    from mutagen.mp4 import MP4

    _ffmpeg("-c:a", "aac", "-b:a", "32k", path)
    audio = MP4(path)
    audio["\xa9nam"] = ["Обійми"]
    audio["cpil"] = True
    audio["soar"] = [EXPECTED["sort_artist"]]
    audio["soal"] = [EXPECTED["sort_album"]]
    audio["sonm"] = [EXPECTED["sort_title"]]
    audio["soaa"] = [EXPECTED["sort_albumartist"]]
    audio["soco"] = [EXPECTED["sort_composer"]]
    audio["sosn"] = ["Шоу, сортування"]
    audio["tmpo"] = [124]
    audio["\xa9grp"] = [EXPECTED["grouping"]]
    audio["rtng"] = [4]
    audio["ldes"] = [PODCAST["description"]]
    audio["desc"] = ["Короткий опис"]
    audio["purl"] = [PODCAST["podcast_url"]]
    audio["egid"] = [PODCAST["podcast_rss"]]
    audio["\xa9day"] = ["2003-05-12T07:00:00Z"]
    audio["catg"] = [PODCAST["category"]]
    audio["keyw"] = [PODCAST["keywords"]]
    audio.save()
    return path


def make_flac(path):
    from mutagen.flac import FLAC

    _ffmpeg("-c:a", "flac", path)
    audio = FLAC(path)
    audio["TITLE"] = "Обійми"
    audio["COMPILATION"] = "1"
    audio["ARTISTSORT"] = EXPECTED["sort_artist"]
    audio["ALBUMSORT"] = EXPECTED["sort_album"]
    audio["TITLESORT"] = EXPECTED["sort_title"]
    audio["ALBUMARTISTSORT"] = EXPECTED["sort_albumartist"]
    audio["COMPOSERSORT"] = EXPECTED["sort_composer"]
    audio["FMPS_RATING"] = "0.8"
    audio["BPM"] = "124"
    audio["GROUPING"] = EXPECTED["grouping"]
    audio["ITUNESADVISORY"] = "1"
    audio.save()
    return path


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A270012345678\n"
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": sysinfo})
    return mp


def _subset(tags, keys):
    return {k: tags.get(k) for k in keys}


# ---- read_tags ----


@pytest.mark.parametrize("v2_version", [3, 4])
def test_read_mp3_tags(tmp_path, v2_version):
    tags = read_tags(make_mp3(str(tmp_path / "a.mp3"), v2_version))
    assert _subset(tags, EXPECTED) == EXPECTED
    assert _subset(tags, PODCAST) == PODCAST


@needs_ffmpeg
def test_read_m4a_tags(tmp_path):
    tags = read_tags(make_m4a(str(tmp_path / "a.m4a")))
    assert _subset(tags, EXPECTED) == dict(EXPECTED, rating=None)  # no rating atom in MP4
    assert _subset(tags, PODCAST) == PODCAST  # ldes wins over desc
    assert tags["sort_tvshow"] == "Шоу, сортування"


@needs_ffmpeg
def test_read_flac_tags(tmp_path):
    tags = read_tags(make_flac(str(tmp_path / "a.flac")))
    assert _subset(tags, EXPECTED) == EXPECTED


def test_file_without_extra_tags_has_no_new_keys(tmp_path):
    path = str(tmp_path / "plain.mp3")
    _make_minimal_mp3(path)
    tags = read_tags(path)
    assert not set(tags) & (set(EXPECTED) | set(PODCAST) | {"sort_tvshow"})


@pytest.mark.parametrize(
    "popm, rating", [(0, 0), (1, 20), (64, 40), (128, 60), (196, 80), (255, 100)]
)
def test_popm_star_bands(popm, rating):
    assert _rating_from_popm(popm) == rating


@pytest.mark.parametrize(
    "text, rating",
    [("0.0", 0), ("0.2", 20), ("0.8", 80), ("1.0", 100), ("3", 60), ("5", 100), ("80", 80)],
)
def test_text_ratings(text, rating):
    assert _rating_from_text(text) == rating


def test_tit1_is_grouping_without_grp1(tmp_path):
    from mutagen.id3 import ID3, TIT1

    path = str(tmp_path / "a.mp3")
    _make_minimal_mp3(path)
    tags = ID3()
    tags.add(TIT1(encoding=3, text=["Ґрупа"]))
    tags.save(path)
    assert read_tags(path)["grouping"] == "Ґрупа"


def test_year_only_date_is_no_release_date(tmp_path):
    path = make_mp3(str(tmp_path / "a.mp3"), podcast=False)
    from mutagen.id3 import ID3, TDRC

    tags = ID3(path)
    tags.add(TDRC(encoding=3, text=["2003"]))
    tags.save(path)
    assert "time_released" not in read_tags(path)


# ---- add_track: saved and read back ----


def _check_track(track, expected):
    for key, value in expected.items():
        if key == "time_released":  # naive datetimes are local time, the getter is aware
            assert getattr(track, key) == value.astimezone(), key
        else:
            assert getattr(track, key) == value, key


def test_add_track_mp3_fields_survive_save(ipod, tmp_path):
    db = Database(ipod)
    db.add_track(make_mp3(str(tmp_path / "a.mp3")), media_type=0x04)
    db.save()
    (track,) = Database(ipod).tracks
    _check_track(track, EXPECTED)
    _check_track(track, PODCAST)


@needs_ffmpeg
def test_add_track_m4a_fields_survive_save(ipod, tmp_path):
    db = Database(ipod)
    db.add_track(make_m4a(str(tmp_path / "a.m4a")))
    db.save()
    (track,) = Database(ipod).tracks
    _check_track(track, dict(EXPECTED, rating=0))
    _check_track(track, PODCAST)
    assert track.sort_tvshow == "Шоу, сортування"


@needs_ffmpeg
def test_add_track_flac_converted_keeps_fields(ipod, tmp_path):
    db = Database(ipod)
    db.add_track(make_flac(str(tmp_path / "a.flac")), convert=True)
    db.save()
    (track,) = Database(ipod).tracks
    _check_track(track, EXPECTED)


def test_explicit_kwargs_win(ipod, tmp_path):
    db = Database(ipod)
    track = db.add_track(
        make_mp3(str(tmp_path / "a.mp3")),
        compilation=False,
        sort_artist="Скрябін",
        rating=40,
        bpm=90,
        grouping="Інша",
        explicit_flag=2,
        time_released=datetime.datetime(2020, 1, 2),
        podcast_url="https://інший/feed",
    )
    db.save()
    track = Database(ipod).tracks[0]
    assert (track.compilation, track.sort_artist, track.rating, track.bpm) == (
        False,
        "Скрябін",
        40,
        90,
    )
    assert (track.grouping, track.explicit_flag, track.podcast_url) == (
        "Інша",
        2,
        "https://інший/feed",
    )
    assert track.time_released == datetime.datetime(2020, 1, 2).astimezone()


def test_compilation_album_is_one_album(ipod, tmp_path):
    """Two artists on one compilation album (TCMP, no album artist) -> one album entry."""
    from mutagen.id3 import ID3, TALB, TPE1

    db = Database(ipod)
    for i, artist in enumerate(["Океан Ельзи", "Бумбокс"]):
        path = make_mp3(str(tmp_path / f"{i}.mp3"), podcast=False)
        tags = ID3(path)
        tags.add(TPE1(encoding=3, text=[artist]))
        tags.add(TALB(encoding=3, text=["Найкраще 2003"]))
        tags.save(path)
        db.add_track(path)
    db.save()
    db = Database(ipod)
    assert all(t.compilation for t in db.tracks)
    assert {t.artist for t in db.tracks} == {"Океан Ельзи", "Бумбокс"}
    assert len({db._album_key(t) for t in db.tracks}) == 1


def test_file_without_extra_tags_gives_the_same_bytes(tmp_path):
    """Regression: a plain file gives the same database as before these tags were read (no new
    MHODs or header fields); compared against the same add with every new key explicitly empty."""
    path = str(tmp_path / "plain.mp3")
    _make_minimal_mp3(path)
    sizes = []
    for name in ("a", "b"):
        mp = str(tmp_path / name)
        os.makedirs(mp)
        init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": b"ModelNumStr: xA003\n"})
        db = Database(mp)
        track = db.add_track(path, title="Обійми")
        assert (track.bpm, track.explicit_flag, track.rating, track.compilation) == (0, 0, 0, 0)
        assert track.time_released.year < 1910  # unset (Mac epoch)
        assert not [c for c in track.record.children if c.fields.get("mhod_type") in range(23, 32)]
        db.save_to_file(str(tmp_path / f"{name}.db"))
        sizes.append(os.path.getsize(str(tmp_path / f"{name}.db")))
    assert sizes[0] == sizes[1]


def test_old_string_form_podcast_url_reads_and_roundtrips(ipod, tmp_path):
    """Older pygpod versions wrote podcast URLs as UTF-16 string MHODs: still read, kept
    byte-identical when unchanged, written in the plain UTF-8 form once changed."""
    from pygpod.db.writer import make_raw_mhod
    from pygpod.utils.compat import put32lint

    path = str(tmp_path / "a.mp3")
    _make_minimal_mp3(path)
    db = Database(ipod)
    track = db.add_track(path, title="Випуск", media_type=0x04)
    text = "https://example.org/ґанок.mp3".encode("utf-16-le")
    body = bytearray(16 + len(text))
    put32lint(body, 4, len(text))
    put32lint(body, 8, 1)
    body[16:] = text
    track.record.children.append(make_raw_mhod(15, bytes(body)))
    db.save()
    db_path = os.path.join(ipod, "iPod_Control", "iTunes", "iTunesDB")
    before = open(db_path, "rb").read()
    db = Database(ipod)
    assert db.tracks[0].podcast_url == "https://example.org/ґанок.mp3"
    db.tracks[0].podcast_rss = "urn:епізод:1"  # another field: the old URL MHOD stays as it was
    db.save()
    assert bytes(body) in open(db_path, "rb").read()
    assert Database(ipod).tracks[0].podcast_rss == "urn:епізод:1"
    db = Database(ipod)
    db.tracks[0].podcast_url = "https://example.org/новий.mp3"
    db.save()
    data = open(db_path, "rb").read()
    assert bytes(body) not in data and "https://example.org/новий.mp3".encode() in data
    assert Database(ipod).tracks[0].podcast_url == "https://example.org/новий.mp3"
    assert before != data
