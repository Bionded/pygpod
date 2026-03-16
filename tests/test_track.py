"""Tests for Track model."""

import datetime


def test_track_from_database(ipod_fs_path):
    """Test Track created via Database."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    track = db.tracks[0]

    assert track.title == "Highway Ride"
    assert track.artist == "The Rockers"
    assert track.album == "Road Trip"
    assert track.year == 2018
    assert track.bitrate in (0, 127, 128)  # 0 when mutagen not installed


def test_track_from_record():
    """Test Track.from_record with a synthetic MHIT."""
    from pygpod.db.parser import Record
    from pygpod.db.writer import make_string_mhod
    from pygpod.model.track import Track

    rec = Record(b"mhit", 100, 200)
    rec.raw_header = b"\x00" * 100
    rec.fields = {
        "track_id": 42,
        "dbid": 12345,
        "bitrate": 256,
        "year": 2020,
        "tracklen": 180000,
        "track_number": 3,
        "total_tracks": 10,
        "samplerate": 44100 << 16,
        "volume": -10,
        "play_count": 5,
        "rating": 80,
        "file_size": 4096000,
    }
    rec.children.append(make_string_mhod(1, "Test Title"))
    rec.children.append(make_string_mhod(4, "Test Artist"))
    rec.children.append(make_string_mhod(3, "Test Album"))

    track = Track.from_record(rec)

    assert track.track_id == 42
    assert track.dbid == 12345
    assert track.title == "Test Title"
    assert track.artist == "Test Artist"
    assert track.album == "Test Album"
    assert track.bitrate == 256
    assert track.year == 2020
    assert track.duration_ms == 180000
    assert track.duration == 180.0
    assert track.track_number == 3
    assert track.total_tracks == 10
    assert track.samplerate == 44100
    assert track.volume == -10
    assert track.play_count == 5
    assert track.rating == 80
    assert track.rating_stars == 4
    assert track.file_size == 4096000


def test_track_empty():
    """Test Track with no record."""
    from pygpod.model.track import Track

    track = Track()
    assert track.title == ""
    assert track.artist == ""
    assert track.album == ""
    assert track.track_id == 0
    assert track.dbid == 0
    assert track.duration_ms == 0
    assert track.bitrate == 0
    assert track.year == 0


def test_track_string_properties():
    """Test all string MHOD properties."""
    from pygpod.db.parser import Record
    from pygpod.db.writer import make_string_mhod
    from pygpod.model.track import Track

    rec = Record(b"mhit", 100, 200)
    rec.raw_header = b"\x00" * 100
    rec.fields = {"track_id": 1, "dbid": 1}

    # Add various string MHODs
    rec.children.append(make_string_mhod(1, "My Title"))
    rec.children.append(make_string_mhod(4, "My Artist"))
    rec.children.append(make_string_mhod(3, "My Album"))
    rec.children.append(make_string_mhod(5, "My Genre"))
    rec.children.append(make_string_mhod(12, "My Composer"))
    rec.children.append(make_string_mhod(8, "My Comment"))

    track = Track.from_record(rec)

    assert track.title == "My Title"
    assert track.artist == "My Artist"
    assert track.album == "My Album"
    assert track.genre == "My Genre"
    assert track.composer == "My Composer"
    assert track.comment == "My Comment"


def test_track_repr():
    """Test string representation."""
    from pygpod.db.parser import Record
    from pygpod.db.writer import make_string_mhod
    from pygpod.model.track import Track

    rec = Record(b"mhit", 100, 200)
    rec.raw_header = b"\x00" * 100
    rec.fields = {"track_id": 99, "dbid": 1}
    rec.children.append(make_string_mhod(1, "Song"))
    rec.children.append(make_string_mhod(4, "Band"))

    track = Track.from_record(rec)

    assert "99" in repr(track)
    assert "Band" in repr(track)
    assert "Song" in repr(track)

    s = str(track)
    assert "Band" in s
    assert "Song" in s


def test_track_media_type_flags():
    """Test media type convenience properties."""
    from pygpod.db.constants import (
        MEDIATYPE_AUDIO,
        MEDIATYPE_AUDIOBOOK,
        MEDIATYPE_PODCAST,
        MEDIATYPE_VIDEO,
    )
    from pygpod.db.parser import Record
    from pygpod.model.track import Track

    # Audio track
    rec = Record(b"mhit", 0x100, 0x200)
    rec.raw_header = b"\x00" * 0x100
    rec.fields = {"track_id": 1, "dbid": 1, "media_type": MEDIATYPE_AUDIO}
    t = Track.from_record(rec)
    assert not t.is_podcast
    assert not t.is_audiobook
    assert not t.is_video

    # Podcast
    rec2 = Record(b"mhit", 0x100, 0x200)
    rec2.raw_header = b"\x00" * 0x100
    rec2.fields = {"track_id": 2, "dbid": 2, "media_type": MEDIATYPE_PODCAST}
    t2 = Track.from_record(rec2)
    assert t2.is_podcast

    # Video
    rec3 = Record(b"mhit", 0x100, 0x200)
    rec3.raw_header = b"\x00" * 0x100
    rec3.fields = {"track_id": 3, "dbid": 3, "media_type": MEDIATYPE_VIDEO}
    t3 = Track.from_record(rec3)
    assert t3.is_video

    # Audiobook
    rec4 = Record(b"mhit", 0x100, 0x200)
    rec4.raw_header = b"\x00" * 0x100
    rec4.fields = {"track_id": 4, "dbid": 4, "media_type": MEDIATYPE_AUDIOBOOK}
    t4 = Track.from_record(rec4)
    assert t4.is_audiobook


def test_track_timestamps(ipod_fs_path):
    """Test timestamp properties return datetime objects."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    track = db.tracks[0]

    assert isinstance(track.time_added, datetime.datetime)
    assert isinstance(track.time_modified, datetime.datetime)
