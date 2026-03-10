"""Round-trip tests - load, modify, save, reload, verify."""

import os


class TestRoundTrip:

    def test_add_track_roundtrip(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        t = db1.add_track(generated_mp3, title="RT Test", artist="RT Artist", album="RT Album")
        tid = t.track_id
        db1.save()

        db2 = Database(writable_ipod)
        t2 = db2.get_track(tid)
        assert t2 is not None
        assert t2.title == "RT Test"
        assert t2.artist == "RT Artist"
        assert t2.album == "RT Album"

    def test_playlist_with_tracks_roundtrip(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        t = db1.add_track(generated_mp3, title="PL RT")
        pl = db1.create_playlist("RT Playlist")
        db1.add_track_to_playlist(pl, t)
        db1.save()

        db2 = Database(writable_ipod)
        pl2 = None
        for p in db2.playlists:
            if p.name == "RT Playlist":
                pl2 = p
                break
        assert pl2 is not None
        assert pl2.track_count == 1
        assert t.track_id in pl2.track_ids

    def test_remove_track_roundtrip(self, writable_ipod, generated_mp3):
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        t = db1.add_track(generated_mp3, title="RemRT")
        tid = t.track_id
        db1.save()

        db2 = Database(writable_ipod)
        t2 = db2.get_track(tid)
        assert t2 is not None
        db2.remove_track(t2)
        db2.save()

        db3 = Database(writable_ipod)
        assert db3.get_track(tid) is None

    def test_multiple_playlists_roundtrip(self, writable_ipod):
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        db1.create_playlist("Alpha")
        db1.create_playlist("Beta")
        db1.create_playlist("Gamma")
        db1.save()

        db2 = Database(writable_ipod)
        names = [p.name for p in db2.playlists if not p.is_master]
        assert "Alpha" in names
        assert "Beta" in names
        assert "Gamma" in names

    def test_delete_playlist_roundtrip(self, writable_ipod):
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        db1.create_playlist("ToDelete")
        db1.save()

        db2 = Database(writable_ipod)
        found = [p for p in db2.playlists if p.name == "ToDelete"]
        assert len(found) == 1
        db2.delete_playlist(found[0])
        db2.save()

        db3 = Database(writable_ipod)
        found = [p for p in db3.playlists if p.name == "ToDelete"]
        assert len(found) == 0

    def test_binary_size_stable(self, writable_ipod):
        """Saving without changes should produce similar-sized DB."""
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        db_path = os.path.join(writable_ipod, "iPod_Control", "iTunes", "iTunesDB")
        size1 = os.path.getsize(db_path)
        db1.save()
        size2 = os.path.getsize(db_path)
        # Size should be within 20% (sort indexes may change)
        assert abs(size2 - size1) < size1 * 0.2

    def test_db_version_preserved(self, writable_ipod):
        from pygpod.model.database import Database

        db1 = Database(writable_ipod)
        ver = db1.db_version
        db1.save()

        db2 = Database(writable_ipod)
        assert db2.db_version == ver
