"""Numeric track values must fit their field: no silent wrap-around on save."""

import pytest

from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


@pytest.fixture
def db_and_mp3(writable_ipod, tmp_path):
    mp3 = str(tmp_path / "пісня.mp3")
    _make_minimal_mp3(mp3)
    return Database(writable_ipod), mp3


@pytest.mark.parametrize(
    "field, value",
    [
        ("play_count", -1),
        ("play_count", 1 << 32),
        ("skip_count", -5),
        ("bookmark_time", 1 << 40),
        ("bpm", 70000),
        ("bpm", -1),
        ("year", -2024),
        ("track_number", 1 << 32),
        ("explicit_flag", 1 << 16),
        ("play_count", 3.5),
        ("play_count", "7"),
        ("rating", "п'ять"),
    ],
)
def test_out_of_range_or_non_int_raises_and_keeps_the_track(db_and_mp3, field, value):
    db, mp3 = db_and_mp3
    track = db.add_track(mp3, title="Гуцулка Ксеня", play_count=2, bpm=120, year=1999)
    before = (getattr(track, field), bytes(track.record.raw_header))
    with pytest.raises(ValueError):
        setattr(track, field, value)
    assert (getattr(track, field), bytes(track.record.raw_header)) == before


@pytest.mark.parametrize("kwargs", [{"play_count": -1}, {"bpm": 70000}, {"year": "1999"}])
def test_add_track_validates_numeric_kwargs_before_copying(db_and_mp3, kwargs):
    db, mp3 = db_and_mp3
    n = len(db.tracks)
    with pytest.raises(ValueError):
        db.add_track(mp3, title="Ой, хто п'є", **kwargs)
    assert len(db.tracks) == n


def test_boundary_values_round_trip(db_and_mp3, writable_ipod):
    db, mp3 = db_and_mp3
    track = db.add_track(mp3, title="Межі")
    track.play_count = 0xFFFFFFFF
    track.bpm = 0xFFFF
    track.skip_count = 0
    track.volume = -255
    track.rating = 100
    db.save()
    again = next(t for t in Database(writable_ipod).tracks if t.title == "Межі")
    assert (again.play_count, again.bpm, again.skip_count, again.rating) == (
        0xFFFFFFFF,
        0xFFFF,
        0,
        100,
    )
    assert again.volume == -255


def test_rating_still_clamps_like_before(db_and_mp3):
    db, mp3 = db_and_mp3
    track = db.add_track(mp3, title="Рейтинг")
    track.rating = 150
    assert track.rating == 100
    track.rating = -20
    assert track.rating == 0
