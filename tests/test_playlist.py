"""Tests for Playlist model."""

NUM_TRACKS = 21


def test_playlist_from_record(ipod_fs_path):
    """Test creating a Playlist from a parsed database."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    assert master is not None
    assert master.is_master
    assert master.name == "iPod"
    assert master.track_count == NUM_TRACKS


def test_playlist_properties(ipod_fs_path):
    """Test playlist property access."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    assert isinstance(master.playlist_id, int)
    assert master.playlist_id > 0
    assert isinstance(master.sort_order, int)
    assert isinstance(master.is_podcast, bool)


def test_playlist_track_ids(ipod_fs_path):
    """Test getting track IDs from a playlist."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    ids = master.track_ids
    assert len(ids) == NUM_TRACKS
    assert all(isinstance(tid, int) for tid in ids)
    assert all(tid > 0 for tid in ids)


def test_playlist_tracks(ipod_fs_path):
    """Test getting Track objects from a playlist."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    tracks = master.tracks
    assert len(tracks) == NUM_TRACKS
    assert tracks[0].title == "Highway Ride"


def test_playlist_iteration(ipod_fs_path):
    """Test iterating over playlist tracks."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    track_list = list(master)
    assert len(track_list) == NUM_TRACKS


def test_playlist_len(ipod_fs_path):
    """Test len() on playlist."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    assert len(master) == NUM_TRACKS


def test_playlist_repr(ipod_fs_path):
    """Test string representation."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist

    r = repr(master)
    assert "iPod" in r
    assert f"{NUM_TRACKS} tracks" in r

    s = str(master)
    assert "iPod" in s


def test_playlist_non_master(ipod_fs_path):
    """Test non-master playlist."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    # Should have at least 2 playlists (master + user playlists)
    assert len(db.playlists) >= 2

    non_master = [p for p in db.playlists if not p.is_master]
    if non_master:
        pl = non_master[0]
        assert not pl.is_master
        assert isinstance(pl.name, str)


def test_empty_playlist():
    """Test a Playlist with no record."""
    from pygpod.model.playlist import Playlist

    pl = Playlist()
    assert pl.name == ""
    assert pl.track_count == 0
    assert pl.track_ids == []
    assert pl.tracks == []
    assert not pl.is_master
    assert not pl.is_podcast
    assert pl.playlist_id == 0
