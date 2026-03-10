"""Tests for Playlist model - edge cases and falsy behavior."""



class TestPlaylistBoolBehavior:
    """Regression: empty playlists are falsy due to __len__."""

    def test_empty_playlist_is_falsy(self, ipod_fs_path):
        # Find or create an empty playlist record
        from pygpod.db.parser import Record
        from pygpod.model.playlist import Playlist
        rec = Record(b"mhyp", 148, 0)
        rec.raw_header = bytearray(148)
        rec.raw_header[0:4] = b"mhyp"
        rec.fields["playlist_type"] = 0
        pl = Playlist.from_record(rec)
        assert pl.track_count == 0
        assert len(pl) == 0
        # bool(pl) is False because __len__ returns 0
        assert not bool(pl)
        # But pl is not None
        assert pl is not None

    def test_playlist_with_tracks_is_truthy(self, ipod_fs_path):
        from pygpod.model.database import Database
        db = Database(ipod_fs_path)
        master = db.master_playlist
        if master and master.track_count > 0:
            assert bool(master)

    def test_playlist_iteration(self, ipod_fs_path):
        from pygpod.model.database import Database
        db = Database(ipod_fs_path)
        for pl in db.playlists:
            # Should be iterable
            for track in pl:
                assert track is not None
                break  # just test first track per playlist
            break  # just test first playlist

    def test_playlist_repr(self, ipod_fs_path):
        from pygpod.model.database import Database
        db = Database(ipod_fs_path)
        for pl in db.playlists:
            r = repr(pl)
            assert "Playlist" in r
            s = str(pl)
            assert "tracks" in s
            break

    def test_playlist_sort_order(self, ipod_fs_path):
        from pygpod.model.database import Database
        db = Database(ipod_fs_path)
        for pl in db.playlists:
            assert isinstance(pl.sort_order, int)
            break

    def test_playlist_is_podcast(self, ipod_fs_path):
        from pygpod.model.database import Database
        db = Database(ipod_fs_path)
        for pl in db.playlists:
            assert isinstance(pl.is_podcast, bool)
            break

    def test_playlist_is_smart(self, ipod_fs_path):
        from pygpod.model.database import Database
        db = Database(ipod_fs_path)
        for pl in db.playlists:
            assert isinstance(pl.is_smart, bool)
            break
