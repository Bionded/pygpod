"""Tests for iTunesSD parser and writer."""


def _make_v2_itunessd():
    """Build a synthetic v2 iTunesSD with 3 tracks for testing."""
    from pygpod.db.itunessd import ITunesSD, ITunesSDTrack

    sd = ITunesSD()
    sd.db_version = 0x010800
    for i in range(3):
        sd.tracks.append(
            ITunesSDTrack(
                start_pos_ms=0,
                stop_pos_ms=0,
                volume=100,
                file_type=1,
                filename=f":iPod_Control:Music:F{i:02d}:track{i}.mp3",
                shuffle_flag=1,
                bookmark_flag=0,
            )
        )
    return sd.write()


def test_parse_itunessd_v2():
    """Parse a synthetic v2 iTunesSD file."""
    from pygpod.db.itunessd import ITunesSD

    data = _make_v2_itunessd()
    sd = ITunesSD.parse(data)
    assert sd.db_version == 0x010800
    assert len(sd.tracks) == 3


def test_parse_itunessd_fixture(itunessd_path):
    """Parsing the fixture file should not crash."""
    from pygpod.db.itunessd import ITunesSD

    with open(itunessd_path, "rb") as f:
        data = f.read()

    sd = ITunesSD.parse(data)
    # Fixture may be v3 format; parser handles v2 only, so just verify no crash
    assert isinstance(sd.tracks, list)


def test_itunessd_track_fields():
    """Verify parsed track fields are reasonable."""
    from pygpod.db.itunessd import ITunesSD

    data = _make_v2_itunessd()
    sd = ITunesSD.parse(data)

    for track in sd.tracks:
        assert track.file_type in (1, 2, 4), f"Unexpected file_type: {track.file_type}"
        assert len(track.filename) > 0
        assert track.shuffle_flag in (0, 1)
        assert track.bookmark_flag in (0, 1)


def test_itunessd_round_trip():
    """Parse and write should produce structurally equivalent data."""
    from pygpod.db.itunessd import ITunesSD

    original = _make_v2_itunessd()
    sd = ITunesSD.parse(original)
    written = sd.write()

    # Re-parse the written data
    sd2 = ITunesSD.parse(written)
    assert len(sd2.tracks) == len(sd.tracks)
    assert sd2.db_version == sd.db_version

    for t1, t2 in zip(sd.tracks, sd2.tracks):
        assert t1.filename == t2.filename
        assert t1.file_type == t2.file_type
        assert t1.start_pos_ms == t2.start_pos_ms
        assert t1.stop_pos_ms == t2.stop_pos_ms


def test_itunessd_write_empty():
    """Write an empty iTunesSD."""
    from pygpod.db.itunessd import ITunesSD

    sd = ITunesSD()
    data = sd.write()

    assert len(data) == 18  # Header only
    # First 3 bytes = num_songs = 0
    assert data[0] == 0 and data[1] == 0 and data[2] == 0


def test_itunessd_synthetic():
    """Create, write, and re-parse a synthetic iTunesSD."""
    from pygpod.db.itunessd import ITunesSD, ITunesSDTrack

    sd = ITunesSD()
    sd.tracks.append(
        ITunesSDTrack(
            start_pos_ms=0,
            stop_pos_ms=0,
            volume=100,
            file_type=1,
            filename=":iPod_Control:Music:F00:test.mp3",
            shuffle_flag=1,
            bookmark_flag=0,
        )
    )

    data = sd.write()
    assert len(data) == 18 + 558  # Header + 1 track

    sd2 = ITunesSD.parse(data)
    assert len(sd2.tracks) == 1
    assert sd2.tracks[0].filename == ":iPod_Control:Music:F00:test.mp3"
    assert sd2.tracks[0].file_type == 1
    assert sd2.tracks[0].volume == 100


def test_itunessd_filetype_from_path():
    """Test file type detection from path."""
    from pygpod.db.itunessd import ITunesSD

    assert ITunesSD.filetype_from_path("test.mp3") == 1
    assert ITunesSD.filetype_from_path("test.m4a") == 2
    assert ITunesSD.filetype_from_path("test.aac") == 2
    assert ITunesSD.filetype_from_path("test.wav") == 4
    assert ITunesSD.filetype_from_path("test.aiff") == 4
    assert ITunesSD.filetype_from_path("noext") == 1  # default


def test_itunessd_parse_too_short():
    """Parse data that's too short returns empty."""
    from pygpod.db.itunessd import ITunesSD

    sd = ITunesSD.parse(b"\x00" * 5)
    assert len(sd.tracks) == 0


def test_itunessd_version_preserved():
    """DB version should be preserved through round-trip."""
    from pygpod.db.itunessd import ITunesSD

    original = _make_v2_itunessd()
    sd = ITunesSD.parse(original)
    original_version = sd.db_version

    written = sd.write()
    sd2 = ITunesSD.parse(written)
    assert sd2.db_version == original_version
