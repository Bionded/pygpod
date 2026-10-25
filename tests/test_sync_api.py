"""APIs for driving an iPod from already processed files (a sync tool on top of pygpod)."""

import datetime
import os

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import TrackError
from pygpod.model.database import Database
from pygpod.utils.encoding import ipod_path_to_os

from .conftest import _make_minimal_mp3, _make_minimal_png


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="Test")
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    return mp, mp3


def _png(tmp_path, color):
    path = str(tmp_path / f"{color}.png")
    _make_minimal_png(path, 120, 120, color)
    with open(path, "rb") as f:
        return f.read()


def test_explicit_metadata_wins_and_persists(ipod):
    mp, mp3 = ipod
    played = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
    db = Database(mp)
    db.add_track(
        mp3,
        title="Explicit",
        artist="Океан Ельзи",
        album="Модель",
        album_artist="Святослав Вакарчук",
        genre="Rock",
        year=1988,
        track_number=3,
        total_tracks=11,
        cd_number=1,
        total_cds=2,
        compilation=True,
        rating=80,
        play_count=5,
        skip_count=2,
        bookmark_time=123456,
        time_played=played,
        remember_position=True,
    )
    db.save()
    t = Database(mp).tracks[0]
    assert (t.title, t.artist, t.album, t.albumartist, t.genre) == (
        "Explicit",
        "Океан Ельзи",
        "Модель",
        "Святослав Вакарчук",
        "Rock",
    )
    assert (t.year, t.track_number, t.total_tracks, t.cd_number, t.total_cds) == (1988, 3, 11, 1, 2)
    assert t.compilation and t.rating == 80 and t.play_count == 5 and t.skip_count == 2
    assert t.bookmark_time == 123456 and t.remember_position
    assert t.time_played == played


def test_update_in_place_persists(ipod):
    mp, mp3 = ipod
    db = Database(mp)
    db.add_track(mp3, title="Before")
    db.save()
    db = Database(mp)
    t = db.tracks[0]
    t.title, t.album, t.rating, t.bookmark_time = "After", "New album", 40, 999
    db.save()
    t = Database(mp).tracks[0]
    assert (t.title, t.album, t.rating, t.bookmark_time) == ("After", "New album", 40, 999)


def test_add_without_copy_references_file_in_place(ipod):
    mp, mp3 = ipod
    target = os.path.join(mp, "iPod_Control", "Music", "F07", "SDXY.mp3")
    with open(mp3, "rb") as src, open(target, "wb") as dst:
        dst.write(src.read())
    db = Database(mp)
    t = db.add_track(target, copy=False, title="in place")
    assert t.ipod_path == ":iPod_Control:Music:F07:SDXY.mp3"
    assert ipod_path_to_os(t.ipod_path, mp) == target
    assert len(os.listdir(os.path.join(mp, "iPod_Control", "Music", "F07"))) == 1
    with pytest.raises(TrackError):
        db.add_track(mp3, copy=False)  # outside the iPod


def test_artwork_from_bytes_add_replace_remove(ipod, tmp_path):
    from pygpod.model.artwork_manager import ArtworkManager

    mp, mp3 = ipod
    db = Database(mp)
    t = db.add_track(mp3, title="cover", cover_art=_png(tmp_path, (255, 0, 0)))
    db.save()
    t = Database(mp).tracks[0]
    assert t.has_artwork and t.mhii_link

    def images():
        return [c.fields.get("image_id") for c in ArtworkManager(mp)._get_or_create_mhli().children]

    first = t.mhii_link
    assert images() == [first]

    db = Database(mp)
    t = db.tracks[0]
    db.set_artwork(t, _png(tmp_path, (0, 255, 0)))
    db.save()
    t = Database(mp).tracks[0]
    assert t.has_artwork and t.mhii_link != first and images() == [t.mhii_link]

    db = Database(mp)
    db.set_artwork(db.tracks[0], None)
    db.save()
    t = Database(mp).tracks[0]
    assert not t.has_artwork and images() == []


def test_playlists_by_name(ipod):
    mp, mp3 = ipod
    db = Database(mp)
    a, b, c = (db.add_track(mp3, title=x) for x in "abc")
    db.set_playlist("Mix", [a, b])
    db.save()
    db = Database(mp)
    by_title = {t.title: t for t in db.tracks}
    assert [t.title for t in db.get_playlist("Mix").tracks] == ["a", "b"]
    db.set_playlist("Mix", [by_title["c"], by_title["a"]])
    db.save()
    db = Database(mp)
    assert [t.title for t in db.get_playlist("Mix").tracks] == ["c", "a"]
    assert len([p for p in db.playlists if p.name == "Mix"]) == 1
    assert db.delete_playlist_by_name("Mix") is True
    assert db.delete_playlist_by_name("Mix") is False
    db.save()
    assert Database(mp).get_playlist("Mix") is None
    assert len(Database(mp).master_playlist.tracks) == 3


@pytest.mark.parametrize("n", [100, 1000])
def test_db_size_estimate_within_20_percent(ipod, n):
    mp, mp3 = ipod
    db = Database(mp)
    estimate_empty = db.estimate_db_size(extra_tracks=n)
    for i in range(n):
        db.add_track(
            mp3,
            title=f"Пісня {i} (Live)",
            artist=f"Artist {i % 40}",
            album=f"Album {i % 70}",
            genre="Rock",
            year=1990 + i % 30,
            track_number=i % 12 + 1,
        )
        if i == 49:
            estimate_50 = db.estimate_db_size(extra_tracks=n - 50)
    db.save()
    real = os.path.getsize(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"))
    assert abs(estimate_empty - real) <= 0.2 * real, (estimate_empty, real)
    assert abs(estimate_50 - real) <= 0.2 * real, (estimate_50, real)


def test_free_space(ipod):
    mp, _ = ipod
    assert Database(mp).free_space() > 0


def test_removing_last_image_empties_artworkdb(tmp_path):
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.device.models import IpodGeneration
    from pygpod.model.artwork_manager import ArtworkManager

    mgr = ArtworkManager(str(tmp_path), generation=IpodGeneration.CLASSIC_1)
    image_id = mgr.add_artwork_data(dbid=1, art_data=_png(tmp_path, (1, 2, 3)), save=False)
    mgr.save()
    mgr = ArtworkManager(str(tmp_path), generation=IpodGeneration.CLASSIC_1)
    assert mgr.remove_artwork(image_id)
    mgr.save()
    with open(tmp_path / "iPod_Control" / "Artwork" / "ArtworkDB", "rb") as f:
        root = parse_artworkdb(f.read())
    mhli = next(c for c in root.children if c.fields.get("mhsd_type") == 1).children[0]
    assert mhli.children == [] and mhli.total_len == 0


@pytest.mark.parametrize("model", ["M9282", "M9800", "xC584"])  # 4G, mini 2G, shuffle 4G
def test_no_cover_art_on_models_without_it(tmp_path, model):
    """iPods without cover art get none: add_track and set_artwork leave the track alone."""
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": f"ModelNumStr: {model}\n".encode()})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    track = db.add_track(mp3, title="Обійми", cover_art=_png(tmp_path, (200, 40, 60)))
    db.set_artwork(track, _png(tmp_path, (20, 40, 160)))
    assert not track.has_artwork
    db.save()
    art = os.path.join(mp, "iPod_Control", "Artwork")
    assert not os.path.isdir(art) or not [f for f in os.listdir(art) if f.endswith(".ithmb")]


def test_unidentified_model_still_gets_cover_art(tmp_path):
    """No ModelNumStr: the model is unknown, artwork uses the Classic formats as before."""
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": b"FirewireGuid: 0x000A270012345678\n"})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    track = db.add_track(mp3, title="Ґанок", cover_art=_png(tmp_path, (90, 70, 50)))
    assert track.has_artwork
