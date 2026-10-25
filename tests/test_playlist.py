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


def test_fixed_seed_gives_unique_playlist_ids_across_reopen(writable_ipod):
    from pygpod.model.config import DatabaseConfig
    from pygpod.model.database import Database

    cfg = DatabaseConfig(random_seed=7)
    db = Database(writable_ipod, config=cfg)
    one = db.create_playlist("Перший").playlist_id
    db.save()
    db = Database(writable_ipod, config=cfg)  # the same sequence again
    two = db.create_playlist("Другий").playlist_id
    assert one != two
    db.save()
    db = Database(writable_ipod, config=cfg)
    db.delete_playlist(db.get_playlist("Другий"))
    db.save()
    assert Database(writable_ipod).get_playlist("Перший") is not None


def _positions(db, playlist_id):
    """Item positions (MHOD 100) of one playlist, per playlist list (MHSD 2 and 3)."""
    import struct

    from pygpod.db.constants import MHIP_MAGIC, MHOD_ID_PLAYLIST, MHYP_MAGIC

    out = {}
    for mhsd in db._root.children:
        kind = mhsd.fields.get("mhsd_type")
        if kind not in (2, 3) or not mhsd.children:
            continue
        for mhyp in mhsd.children[0].children:
            if mhyp.magic != MHYP_MAGIC or mhyp.fields.get("playlist_id") != playlist_id:
                continue
            found = []
            for mhip in mhyp.children:
                if mhip.magic != MHIP_MAGIC:
                    continue
                for mhod in mhip.children:
                    if mhod.fields.get("mhod_type") == MHOD_ID_PLAYLIST:
                        found.append(struct.unpack_from("<I", mhod.raw_bytes, mhod.header_len)[0])
            out[kind] = found
    return out


def test_positions_are_renumbered_after_remove_and_add(writable_ipod, tmp_path):
    from pygpod.model.database import Database

    from .conftest import _make_minimal_mp3

    mp3 = str(tmp_path / "пісня.mp3")
    _make_minimal_mp3(mp3)
    db = Database(writable_ipod)
    a, b, c = (db.add_track(mp3, title=t) for t in ("Аркан", "Бандура", "Вербова"))
    pl = db.create_playlist("Танці")
    db.add_track_to_playlist(pl, a)
    db.add_track_to_playlist(pl, b)
    db.remove_track_from_playlist(pl, a)
    db.add_track_to_playlist(pl, c)
    assert _positions(db, pl.playlist_id) == {2: [0, 1], 3: [0, 1]}
    db.save()
    db = Database(writable_ipod)
    pl = db.get_playlist("Танці")
    assert [t.title for t in pl.tracks] == ["Бандура", "Вербова"]
    assert _positions(db, pl.playlist_id) == {2: [0, 1], 3: [0, 1]}
    db.remove_tracks([t for t in db.tracks if t.title == "Бандура"])
    assert _positions(db, pl.playlist_id) == {2: [0], 3: [0]}
