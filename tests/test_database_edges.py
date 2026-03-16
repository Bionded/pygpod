"""Tests for database.py edge cases, error paths, and from_file()."""

import pytest

from pygpod.exceptions import DatabaseError, MountPointError, TrackError


class TestDatabaseFromFile:
    def test_from_file(self, itunesdb_path):
        from pygpod.model.database import Database

        db = Database.from_file(itunesdb_path)
        assert len(db.tracks) > 0
        assert len(db.playlists) > 0
        assert db.device is None  # no mountpoint

    def test_from_file_tracks_readable(self, itunesdb_path):
        from pygpod.model.database import Database

        db = Database.from_file(itunesdb_path)
        for track in db.tracks:
            assert isinstance(track.title, str)
            assert track.track_id > 0


class TestDatabaseErrors:
    def test_invalid_mountpoint(self, tmp_path):
        from pygpod.model.database import Database

        with pytest.raises((DatabaseError, MountPointError)):
            Database(str(tmp_path / "nonexistent"))

    def test_add_track_nonexistent_file(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        with pytest.raises(TrackError):
            db.add_track("/nonexistent/file.mp3")

    def test_delete_master_playlist(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        master = db.master_playlist
        assert master is not None
        with pytest.raises(DatabaseError):
            db.delete_playlist(master)

    def test_get_track_missing(self, ipod_fs_path):
        from pygpod.model.database import Database

        db = Database(ipod_fs_path)
        assert db.get_track(999999) is None


class TestDatabasePodcastType3:
    def test_podcast_type3_rebuild(self, writable_ipod_with_mp3):
        """Podcast playlists get hierarchical type 3 structure on save."""
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        # Add a podcast track
        track = db.add_track(mp3, media_type=0x00000004, album="Test Show", category="Test Show")
        assert track.is_podcast

        db.save()

        # Verify type 3 has the podcast playlist
        db2 = Database(ipod)
        podcast_pls = [p for p in db2.playlists if p.is_podcast]
        assert len(podcast_pls) >= 1


class TestDatabaseSortIndexes:
    def test_sort_indexes_on_empty_db(self, writable_ipod):
        """Sort indexes are generated even with zero tracks after purge-like scenario."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        # Remove all tracks
        for track in list(db.tracks):
            db.remove_track(track)
        db.save()

        # Should not crash — sort indexes are just empty
        db2 = Database(writable_ipod)
        assert len(db2.tracks) == 0

    def test_sort_with_unicode_titles(self, writable_ipod_with_mp3):
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        db.add_track(mp3, title="Скрябін", artist="Українська Рок")
        db.save()

        db2 = Database(ipod)
        found = [t for t in db2.tracks if t.title == "Скрябін"]
        assert len(found) == 1

    def test_sort_with_empty_title(self, writable_ipod_with_mp3):
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        db.add_track(mp3, title="", artist="")
        db.save()

        # Should not crash
        db2 = Database(ipod)
        assert len(db2.tracks) > 0
