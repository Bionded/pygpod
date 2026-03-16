"""Additional database.py edge-case tests for coverage gaps."""

import os

import pytest

from pygpod.model.database import Database


class TestEmptyPlaylistIteration:
    def test_empty_playlist_tracks_property(self, writable_ipod):
        """A playlist with no tracks should return an empty list from .tracks."""
        db = Database(writable_ipod)
        db.create_playlist("EmptyPL")
        db.save()

        db2 = Database(writable_ipod)
        found = [p for p in db2.playlists if p.name == "EmptyPL"]
        assert len(found) == 1
        assert found[0].tracks == []
        assert found[0].track_count == 0
        assert list(found[0]) == []  # __iter__


class TestRemoveTrackFromPlaylistNotInIt:
    def test_remove_absent_track_no_crash(self, writable_ipod_with_mp3):
        """Removing a track from a playlist it doesn't belong to should not crash."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        # Add a track, create a separate playlist that does NOT contain it
        track = db.add_track(mp3, title="Orphan")
        pl = db.create_playlist("OtherPL")
        # Do NOT add track to OtherPL

        # This should be a no-op, not raise
        db.remove_track_from_playlist(pl, track)
        db.save()

        db2 = Database(ipod)
        other = [p for p in db2.playlists if p.name == "OtherPL"]
        assert len(other) == 1
        assert other[0].track_count == 0


class TestPodcastType3RebuildEmptyAlbum:
    def test_podcast_type3_with_empty_album(self, writable_ipod_with_mp3):
        """Podcast type 3 rebuild handles tracks with empty album names."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        # Add a podcast track with NO album (empty string)
        track = db.add_track(
            mp3, media_type=0x00000004, title="NoAlbumPod", album=""
        )
        assert track.is_podcast

        # Save triggers _rebuild_type3_podcasts which groups by album
        db.save()

        db2 = Database(ipod)
        podcast_pls = [p for p in db2.playlists if p.is_podcast]
        assert len(podcast_pls) >= 1

    def test_podcast_type3_multiple_albums(self, writable_ipod_with_mp3):
        """Podcast type 3 rebuild groups by album name correctly."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        db.add_track(mp3, media_type=0x00000004, title="Ep1", album="Show A")

        # Create a second mp3 for a different album
        mp3_2 = os.path.join(os.path.dirname(mp3), "track2.mp3")
        import shutil

        shutil.copy2(mp3, mp3_2)
        db.add_track(mp3_2, media_type=0x00000004, title="Ep2", album="Show B")

        db.save()

        db2 = Database(ipod)
        podcast_pls = [p for p in db2.playlists if p.is_podcast]
        assert len(podcast_pls) >= 1


class TestSortIndexesEmptyFields:
    def test_sort_with_all_empty_strings(self, writable_ipod_with_mp3):
        """Sort indexes should handle tracks where all string fields are empty."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        # Remove all existing tracks to start clean
        for t in list(db.tracks):
            db.remove_track(t)

        # Add track with every sortable field empty
        db.add_track(
            mp3, title="", artist="", album="", genre="", composer=""
        )
        # Should not crash during save (which rebuilds sort indexes)
        db.save()

        db2 = Database(ipod)
        assert len(db2.tracks) == 1


class TestAlbumListDuplicates:
    def test_album_list_dedup(self, writable_ipod_with_mp3):
        """Album list rebuild deduplicates (album, artist) pairs."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        # Remove existing tracks for a clean test
        for t in list(db.tracks):
            db.remove_track(t)

        # Add two tracks with the SAME (album, artist) pair
        db.add_track(mp3, title="Track1", artist="ArtistX", album="AlbumY")
        mp3_2 = os.path.join(os.path.dirname(mp3), "track_dup.mp3")
        import shutil

        shutil.copy2(mp3, mp3_2)
        db.add_track(mp3_2, title="Track2", artist="ArtistX", album="AlbumY")

        db.save()

        # Re-open and verify album list was rebuilt without duplicates
        db2 = Database(ipod)
        assert len(db2.tracks) == 2

        # The album list (MHSD type 4) should have exactly 1 entry
        # for the (AlbumY, ArtistX) pair, not 2
        mhsd4 = None
        for mhsd in db2._root.children:
            if mhsd.fields.get("mhsd_type") == 4:
                mhsd4 = mhsd
                break
        assert mhsd4 is not None
        mhla = mhsd4.children[0]
        # Count MHIA children
        album_count = len(mhla.children)
        # Should be exactly 1 (both tracks share the same album+artist)
        assert album_count == 1


class TestArtistListRebuild:
    """Test _rebuild_artist_list by inspecting the in-memory record tree after save.

    The parser does not yet re-parse MHSD type 8, so we inspect the tree
    on the same Database instance that called save() (which triggers the rebuild).
    """

    def _find_mhsd8_mhli(self, db):
        """Helper: find MHSD type 8 and its MHLI child in the record tree."""
        for mhsd in db._root.children:
            if mhsd.fields.get("mhsd_type") == 8:
                assert len(mhsd.children) > 0, "MHSD type 8 should have an MHLI child"
                return mhsd.children[0]
        pytest.fail("MHSD type 8 not found in record tree")

    def test_artist_list_rebuilt_on_save(self, writable_ipod_with_mp3):
        """Artist list (MHSD type 8) is rebuilt from track metadata on save."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        # Remove existing tracks
        for t in list(db.tracks):
            db.remove_track(t)

        # Add tracks with distinct artists
        db.add_track(mp3, title="Song1", artist="Alice")
        mp3_2 = os.path.join(os.path.dirname(mp3), "track_art.mp3")
        import shutil

        shutil.copy2(mp3, mp3_2)
        db.add_track(mp3_2, title="Song2", artist="Bob")

        db.save()

        mhli = self._find_mhsd8_mhli(db)
        # Should have 2 artists: Alice and Bob
        assert len(mhli.children) == 2

    def test_artist_list_dedup(self, writable_ipod_with_mp3):
        """Artist list deduplicates artists with the same name."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        for t in list(db.tracks):
            db.remove_track(t)

        db.add_track(mp3, title="SongA", artist="SameArtist")
        mp3_2 = os.path.join(os.path.dirname(mp3), "track_art2.mp3")
        import shutil

        shutil.copy2(mp3, mp3_2)
        db.add_track(mp3_2, title="SongB", artist="SameArtist")

        db.save()

        mhli = self._find_mhsd8_mhli(db)
        assert len(mhli.children) == 1

    def test_artist_list_empty_artist_skipped(self, writable_ipod_with_mp3):
        """Empty artist strings are not included in the artist list."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)

        for t in list(db.tracks):
            db.remove_track(t)

        db.add_track(mp3, title="Untitled", artist="")
        db.save()

        mhli = self._find_mhsd8_mhli(db)
        # Empty artist should be skipped per the code: "if artist and artist not in artists"
        assert len(mhli.children) == 0


class TestDbVersionAndDbId:
    def test_db_version_no_root(self):
        """db_version returns 0 when no root record exists."""
        db = Database.__new__(Database)
        db._root = None
        db._tracks = []
        db._playlists = []
        db._track_lookup = {}
        assert db.db_version == 0

    def test_db_id_no_root(self):
        """db_id returns 0 when no root record exists."""
        db = Database.__new__(Database)
        db._root = None
        db._tracks = []
        db._playlists = []
        db._track_lookup = {}
        assert db.db_id == 0

    def test_master_playlist_none_when_empty(self):
        """master_playlist returns None when no playlists exist."""
        db = Database.__new__(Database)
        db._root = None
        db._tracks = []
        db._playlists = []
        db._track_lookup = {}
        assert db.master_playlist is None


class TestRemoveTrackDeleteFile:
    def test_remove_track_with_delete_file(self, writable_ipod_with_mp3):
        """remove_track with delete_file=True removes the file from disk."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        db.add_track(mp3, title="ToDelete")
        db.save()

        db2 = Database(ipod)
        t = [t for t in db2.tracks if t.title == "ToDelete"][0]
        ipod_path = t.ipod_path
        # Verify the file exists on disk
        from pygpod.utils.encoding import ipod_path_to_os

        fpath = ipod_path_to_os(ipod_path, ipod)
        assert os.path.isfile(fpath)

        db2.remove_track(t, delete_file=True)
        db2.save()

        # File should be gone
        assert not os.path.isfile(fpath)

    def test_remove_track_delete_file_missing(self, writable_ipod_with_mp3):
        """remove_track with delete_file=True handles missing files gracefully."""
        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        db.add_track(mp3, title="GhostFile")
        db.save()

        db2 = Database(ipod)
        t = [t for t in db2.tracks if t.title == "GhostFile"][0]
        # Manually delete the file first
        from pygpod.utils.encoding import ipod_path_to_os

        fpath = ipod_path_to_os(t.ipod_path, ipod)
        os.unlink(fpath)

        # Should not crash even though the file is already gone
        db2.remove_track(t, delete_file=True)
        db2.save()
