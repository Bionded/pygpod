"""Edits through Track/Playlist setters mark the Database modified (context manager saves them)."""

import pytest

from pygpod.model.database import Database


@pytest.fixture
def ipod(writable_ipod):
    Database(writable_ipod).save()
    return writable_ipod


def test_fresh_open_is_clean(ipod):
    assert Database(ipod).modified is False


@pytest.mark.parametrize(
    "attr,value",
    [
        ("title", "Edited title"),
        ("artist", "Edited artist"),
        ("rating", 60),
        ("play_count", 42),
        ("year", 1999),
    ],
)
def test_track_edit_in_with_block_is_saved(ipod, attr, value):
    with Database(ipod) as db:
        setattr(db.tracks[0], attr, value)
        assert db.modified
    assert getattr(Database(ipod).tracks[0], attr) == value


def test_playlist_rename_in_with_block_is_saved(ipod):
    with Database(ipod) as db:
        pl = db.create_playlist("Before")
        db.save()
        pl.name = "After"
        assert db.modified
    assert "After" in [p.name for p in Database(ipod).playlists]


def test_exception_in_block_does_not_save(ipod):
    original = Database(ipod).tracks[0].title
    with pytest.raises(RuntimeError):
        with Database(ipod) as db:
            db.tracks[0].title = "should not persist"
            raise RuntimeError("boom")
    assert Database(ipod).tracks[0].title == original


def test_explicit_save_clears_flag(ipod):
    db = Database(ipod)
    db.tracks[0].title = "Explicit"
    assert db.modified
    db.save()
    assert db.modified is False
    assert Database(ipod).tracks[0].title == "Explicit"


def test_added_track_edits_tracked(writable_ipod_with_mp3):
    mp, mp3 = writable_ipod_with_mp3
    db = Database(mp)
    track = db.add_track(mp3, title="t")
    db.save()
    track.album = "Later"
    assert db.modified
