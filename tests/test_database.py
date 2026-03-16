"""Tests for the high-level Database API."""

NUM_TRACKS = 21


def test_open_database(ipod_fs_path):
    """Open database from mount point."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    assert len(db.tracks) == NUM_TRACKS
    assert len(db.playlists) >= 1


def test_from_file(itunesdb_path):
    """Open database from standalone file."""
    import pygpod

    db = pygpod.Database.from_file(itunesdb_path)
    assert len(db.tracks) == NUM_TRACKS


def test_track_properties(ipod_fs_path):
    """Verify track property access."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    track = db.tracks[0]

    assert track.title == "Highway Ride"
    assert track.artist == "The Rockers"
    assert track.album == "Road Trip"
    assert track.bitrate in (0, 127, 128)  # 0 when mutagen not installed
    assert track.samplerate == 44100
    assert track.year == 2018
    assert track.duration_ms > 0
    assert track.track_id > 0
    assert track.dbid > 0


def test_master_playlist(ipod_fs_path):
    """Verify master playlist."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    master = db.master_playlist
    assert master is not None
    assert master.is_master
    assert master.track_count == NUM_TRACKS


def test_device_detection(ipod_fs_path):
    """Verify device model detection."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    dev = db.device
    assert dev is not None
    assert "Classic" in dev.model
    assert dev.requires_hash
    assert dev.supports_artwork
    assert not dev.is_shuffle


def test_add_and_remove_track(writable_ipod, generated_mp3):
    """Test adding and removing a track."""
    import pygpod

    db = pygpod.Database(writable_ipod)
    assert len(db.tracks) == NUM_TRACKS

    # Add
    track = db.add_track(generated_mp3)
    assert len(db.tracks) == NUM_TRACKS + 1
    assert track.track_id > 0
    assert track.ipod_path.startswith(":iPod_Control:Music:F")

    # Save and re-read
    db.save()
    db2 = pygpod.Database(writable_ipod)
    assert len(db2.tracks) == NUM_TRACKS + 1

    # Remove
    db2.remove_track(db2.tracks[-1])
    assert len(db2.tracks) == NUM_TRACKS
    db2.save()

    db3 = pygpod.Database(writable_ipod)
    assert len(db3.tracks) == NUM_TRACKS


def test_invalid_mountpoint():
    """Should raise MountPointError for invalid path."""
    import pygpod

    try:
        pygpod.Database("/nonexistent/path")
        assert False, "Should have raised MountPointError"
    except pygpod.MountPointError:
        pass


def test_get_track(ipod_fs_path):
    """Test getting track by ID."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    first_track = db.tracks[0]
    track = db.get_track(first_track.track_id)
    assert track is not None
    assert track.title == "Highway Ride"

    missing = db.get_track(9999)
    assert missing is None


def test_database_repr(ipod_fs_path):
    """Test string representation."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    s = repr(db)
    assert f"{NUM_TRACKS} tracks" in s
    assert "playlists" in s
