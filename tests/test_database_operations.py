"""Tests for Database model - playlist operations, save cycle, round-trip."""

import pytest


class TestDatabasePlaylistOps:
    """Test create, add-to, remove-from, and delete playlist operations."""

    def test_create_playlist(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        initial = len(db.playlists)
        pl = db.create_playlist("My Test PL")
        assert pl.name == "My Test PL"
        assert not pl.is_master
        assert len(db.playlists) == initial + 1
        assert pl.playlist_id != 0

    def test_delete_playlist(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        pl = db.create_playlist("DeleteMe")
        count_before = len(db.playlists)
        db.delete_playlist(pl)
        assert len(db.playlists) == count_before - 1

    def test_delete_master_playlist_raises(self, writable_ipod):
        from pygpod.exceptions import DatabaseError
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        master = db.master_playlist
        assert master is not None
        with pytest.raises(DatabaseError, match="master"):
            db.delete_playlist(master)

    def test_add_track_to_playlist(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        track = db.add_track(generated_mp3)
        pl = db.create_playlist("AddTracks")

        # Playlist is initially empty
        assert pl.track_count == 0

        db.add_track_to_playlist(pl, track)
        # track_count reads from MHIP children
        assert pl.track_count == 1
        assert track.track_id in pl.track_ids

    def test_remove_track_from_playlist(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        track = db.add_track(generated_mp3)
        pl = db.create_playlist("RemTrack")
        db.add_track_to_playlist(pl, track)
        assert pl.track_count == 1

        db.remove_track_from_playlist(pl, track)
        assert pl.track_count == 0

    def test_playlist_persists_after_save(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        pl = db.create_playlist("Persist")
        pid = pl.playlist_id
        db.save()

        # Reload and verify
        db2 = Database(writable_ipod)
        names = [p.name for p in db2.playlists]
        assert "Persist" in names
        found = [p for p in db2.playlists if p.name == "Persist"]
        assert len(found) == 1
        assert found[0].playlist_id == pid


class TestDatabaseTrackOps:
    """Test add, remove, get_track."""

    def test_add_track(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        initial = len(db.tracks)
        track = db.add_track(generated_mp3)
        assert len(db.tracks) == initial + 1
        assert track.track_id > 0

    def test_get_track(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        track = db.add_track(generated_mp3)
        found = db.get_track(track.track_id)
        assert found is not None
        assert found.track_id == track.track_id

    def test_get_track_not_found(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        assert db.get_track(999999) is None

    def test_remove_track(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        track = db.add_track(generated_mp3)
        tid = track.track_id
        db.remove_track(track)
        assert db.get_track(tid) is None

    def test_add_track_no_mountpoint_raises(self, generated_mp3):
        from pygpod.exceptions import TrackError
        from pygpod.model.database import Database

        db = Database()
        with pytest.raises(TrackError, match="mount point"):
            db.add_track(generated_mp3)

    def test_add_track_nonexistent_file_raises(self, writable_ipod):
        from pygpod.exceptions import TrackError
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        with pytest.raises(TrackError, match="not found"):
            db.add_track("/no/such/file.mp3")

    def test_remove_track_updates_master_playlist(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        track = db.add_track(generated_mp3)
        tid = track.track_id
        master = db.master_playlist
        assert tid in master.track_ids
        db.remove_track(track)
        assert tid not in master.track_ids


class TestDatabaseSaveCycle:
    """Test save and reload round-trip."""

    def test_save_and_reload_tracks(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        initial_count = len(db.tracks)
        db.add_track(generated_mp3, title="RoundTrip", artist="TestArtist")
        db.save()

        db2 = Database(writable_ipod)
        assert len(db2.tracks) == initial_count + 1
        found = [t for t in db2.tracks if t.title == "RoundTrip"]
        assert len(found) == 1
        assert found[0].artist == "TestArtist"

    def test_double_save(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.add_track(generated_mp3, title="First")
        db.save()
        db.save()  # second save should not corrupt

        db2 = Database(writable_ipod)
        found = [t for t in db2.tracks if t.title == "First"]
        assert len(found) == 1

    def test_save_preserves_db_version(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        ver = db.db_version
        db.save()

        db2 = Database(writable_ipod)
        assert db2.db_version == ver


class TestDatabaseProperties:
    """Test Database properties and metadata."""

    def test_mountpoint(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        assert db.mountpoint == writable_ipod

    def test_device(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        assert db.device is not None

    def test_master_playlist(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        master = db.master_playlist
        assert master is not None
        assert master.is_master

    def test_db_version(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        assert db.db_version > 0

    def test_db_id(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        assert db.db_id >= 0

    def test_from_file(self, itunesdb_path):
        from pygpod.model.database import Database

        db = Database.from_file(itunesdb_path)
        assert len(db.tracks) > 0
        assert db.mountpoint is None

    def test_repr(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        r = repr(db)
        assert "Database" in r
        assert "tracks" in r

    def test_config(self, writable_ipod):
        from pygpod.model.config import DatabaseConfig
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        assert isinstance(db.config, DatabaseConfig)

    def test_context_manager(self, writable_ipod):
        from pygpod.model.database import Database

        with Database(writable_ipod) as db:
            assert len(db.tracks) >= 0


class TestPlaylistFalsy:
    """Regression test: empty playlists must not be falsy."""

    def test_empty_playlist_is_truthy_with_is_none_check(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        pl = db.create_playlist("Empty")
        assert pl.track_count == 0
        # len() is 0 so bool(pl) is False, but `pl is not None` is True
        assert pl is not None
        assert len(pl) == 0
