"""A failing add_track() leaves the database and the iPod as they were."""

import os

import pytest

from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


def _music_files(mp):
    out = []
    for root, _, files in os.walk(os.path.join(mp, "iPod_Control", "Music")):
        out += [os.path.join(root, f) for f in files]
    return sorted(out)


def test_invalid_value_leaves_no_hidden_track_and_no_file(writable_ipod, tmp_path):
    src = str(tmp_path / "пісня.mp3")
    _make_minimal_mp3(src)
    db = Database(writable_ipod)
    before_tracks = len(db.tracks)
    before_files = _music_files(writable_ipod)
    mhits = len(db._get_or_create_mhlt().children)
    with pytest.raises((TypeError, ValueError)):
        db.add_track(src, title="Зламана", rating="invalid")
    assert len(db.tracks) == before_tracks
    assert len(db._get_or_create_mhlt().children) == mhits
    assert _music_files(writable_ipod) == before_files
    db.save()
    assert len(Database(writable_ipod).tracks) == before_tracks

    track = db.add_track(src, title="Ціла", rating=80)  # a valid add is unchanged
    assert track.rating == 80 and track in db.tracks
    db.save()
    assert [t.title for t in Database(writable_ipod).tracks].count("Ціла") == 1
