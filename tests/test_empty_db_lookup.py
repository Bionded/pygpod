"""Playlists share the database's track lookup, even when it starts empty."""

import os

from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


def test_empty_db_playlists_see_added_tracks(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)

    db = Database(mp)
    assert db.tracks == []
    master = db.master_playlist
    assert master is not None
    pl = db.create_playlist("Mine")

    track = db.add_track(mp3, title="one")
    db.add_track_to_playlist(pl, track)
    assert track in pl.tracks
    assert track in master.tracks

    db.remove_track_from_playlist(pl, track)
    assert track not in pl.tracks
    db.remove_track(track)
    assert track not in master.tracks

    # and after a save/reload cycle
    track2 = db.add_track(mp3, title="two")
    db.add_track_to_playlist(pl, track2)
    db.save()
    db2 = Database(mp)
    pl2 = next(p for p in db2.playlists if p.name == "Mine")
    assert [t.title for t in pl2.tracks] == ["two"]
