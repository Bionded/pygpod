"""Tests for the pygpod public API (top-level imports from __init__.py)."""


class TestPublicImports:
    """Verify all public symbols are importable from pygpod."""

    def test_version(self):
        import pygpod

        assert isinstance(pygpod.__version__, str)
        assert len(pygpod.__version__) > 0

    def test_database(self):
        from pygpod import Database

        assert Database is not None

    def test_track(self):
        from pygpod import Track

        assert Track is not None

    def test_playlist(self):
        from pygpod import Playlist

        assert Playlist is not None

    def test_device(self):
        from pygpod import Device

        assert Device is not None

    def test_config(self):
        from pygpod import DatabaseConfig

        cfg = DatabaseConfig()
        assert cfg is not None

    def test_artwork(self):
        from pygpod import Artwork

        assert Artwork is not None

    def test_chapterdata(self):
        from pygpod import Chapter, ChapterData

        assert ChapterData is not None
        assert Chapter is not None

    def test_smartplaylist_types(self):
        from pygpod import (
            SPLAction,
            SPLField,
            SPLLimitSort,
            SPLLimitType,
            SPLMatch,
            SPLPrefs,
            SPLRule,
            apply_limit,
            evaluate_smart_playlist,
        )

        assert SPLRule is not None
        assert SPLField is not None
        assert SPLAction is not None
        assert SPLMatch is not None
        assert SPLPrefs is not None
        assert SPLLimitType is not None
        assert SPLLimitSort is not None
        assert evaluate_smart_playlist is not None
        assert apply_limit is not None

    def test_photo_types(self):
        from pygpod import Photo, PhotoAlbum, PhotoDB

        assert PhotoDB is not None
        assert Photo is not None
        assert PhotoAlbum is not None

    def test_playcounts(self):
        from pygpod import read_otg_playlists, read_play_counts

        assert read_play_counts is not None
        assert read_otg_playlists is not None

    def test_mountpoint_utils(self):
        from pygpod import init_ipod, validate_mountpoint

        assert init_ipod is not None
        assert validate_mountpoint is not None

    def test_exceptions(self):
        from pygpod import (
            ArtworkError,
            DatabaseError,
            DependencyError,
            DeviceError,
            HashError,
            MountPointError,
            ParseError,
            PlaylistError,
            PyGpodError,
            TrackError,
            WriteError,
        )

        # All should be subclasses of PyGpodError
        assert issubclass(DatabaseError, PyGpodError)
        assert issubclass(ParseError, PyGpodError)
        assert issubclass(WriteError, PyGpodError)
        assert issubclass(DeviceError, PyGpodError)
        assert issubclass(MountPointError, PyGpodError)
        assert issubclass(HashError, PyGpodError)
        assert issubclass(TrackError, PyGpodError)
        assert issubclass(PlaylistError, PyGpodError)
        assert issubclass(ArtworkError, PyGpodError)
        assert issubclass(DependencyError, PyGpodError)

    def test_all_exports(self):
        import pygpod

        for name in pygpod.__all__:
            assert hasattr(pygpod, name), f"Missing export: {name}"

    def test_discover_function(self):
        from pygpod import discover

        assert callable(discover)


class TestDatabaseViaPublicAPI:
    """Test Database using top-level import."""

    def test_open_database(self, ipod_fs_path):
        import pygpod

        db = pygpod.Database(ipod_fs_path)
        assert len(db.tracks) > 0
        assert len(db.playlists) > 0

    def test_database_config(self, ipod_fs_path):
        import pygpod

        config = pygpod.DatabaseConfig()
        db = pygpod.Database(ipod_fs_path, config=config)
        assert len(db.tracks) > 0

    def test_track_properties(self, ipod_fs_path):
        import pygpod

        db = pygpod.Database(ipod_fs_path)
        track = db.tracks[0]
        assert isinstance(track, pygpod.Track)
        assert isinstance(track.title, str)
        assert isinstance(track.artist, str)
        assert isinstance(track.duration, float)

    def test_playlist_properties(self, ipod_fs_path):
        import pygpod

        db = pygpod.Database(ipod_fs_path)
        for pl in db.playlists:
            assert isinstance(pl, pygpod.Playlist)
            assert isinstance(pl.name, str)
            break

    def test_validate_mountpoint(self, ipod_fs_path):
        import pygpod

        assert pygpod.validate_mountpoint(ipod_fs_path)
        assert not pygpod.validate_mountpoint("/nonexistent/path")


class TestDiscover:
    """Test discover() with mocked filesystem/USB."""

    def test_discover_returns_list(self):
        import pygpod

        results = pygpod.discover()
        assert isinstance(results, list)

    def test_discover_no_usb(self):
        """discover() handles missing pyusb gracefully."""
        import pygpod

        # discover() should not crash even if pyusb is missing
        results = pygpod.discover()
        assert isinstance(results, list)


class TestResolveUSBMounts:
    """Test platform-specific mount point resolution."""

    def test_resolve_linux_usb_mounts(self):
        from pygpod import _resolve_linux_usb_mounts

        # Should return a list, even if empty
        result = _resolve_linux_usb_mounts()
        assert isinstance(result, list)

    def test_resolve_usb_mount_points_linux(self):
        from pygpod import _resolve_usb_mount_points

        result = _resolve_usb_mount_points("Linux")
        assert isinstance(result, list)

    def test_resolve_usb_mount_points_unknown_os(self):
        from pygpod import _resolve_usb_mount_points

        result = _resolve_usb_mount_points("UnknownOS")
        assert result == []

    def test_resolve_macos(self):
        from pygpod import _resolve_usb_mount_points

        result = _resolve_usb_mount_points("Darwin")
        assert isinstance(result, list)

    def test_resolve_windows(self):
        from pygpod import _resolve_usb_mount_points

        result = _resolve_usb_mount_points("Windows")
        assert isinstance(result, list)
