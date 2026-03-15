"""Tests for iTunesDB parser and writer."""

NUM_TRACKS = 21


def test_parse_itunesdb(itunesdb_path):
    """Parse the test iTunesDB and verify structure."""
    from pygpod.db.parser import parse_itunesdb

    with open(itunesdb_path, "rb") as f:
        data = f.read()

    root = parse_itunesdb(data)
    assert root.magic == b"mhbd"
    assert root.fields["db_version"] > 0
    assert len(root.children) >= 2  # At least track + playlist MHSDs


def test_parse_tracks(itunesdb_path):
    """Verify all tracks are parsed with correct metadata."""
    from pygpod.db.parser import parse_itunesdb

    with open(itunesdb_path, "rb") as f:
        root = parse_itunesdb(f.read())

    # Find track list (MHSD type 1)
    track_mhsd = [c for c in root.children if c.fields.get("mhsd_type") == 1][0]
    mhlt = track_mhsd.children[0]
    assert mhlt.fields["num_tracks"] == NUM_TRACKS
    assert len(mhlt.children) == NUM_TRACKS

    # Verify first track
    t1 = mhlt.children[0]
    assert t1.get_mhod(1) == "Highway Ride"
    assert t1.get_mhod(4) == "The Rockers"
    assert t1.get_mhod(3) == "Road Trip"
    assert t1.fields["bitrate"] in (127, 128)
    assert t1.fields["year"] == 2018


def test_parse_playlists(itunesdb_path):
    """Verify playlists are parsed."""
    from pygpod.db.parser import parse_itunesdb

    with open(itunesdb_path, "rb") as f:
        root = parse_itunesdb(f.read())

    # Find playlist list (MHSD type 2)
    pl_mhsd = [c for c in root.children if c.fields.get("mhsd_type") == 2][0]
    mhlp = pl_mhsd.children[0]
    assert mhlp.fields["num_playlists"] >= 3  # at least master + 2 user playlists

    # Master playlist
    master = mhlp.children[0]
    assert master.fields["playlist_type"] == 1
    assert master.get_mhod(1) == "iPod"


def test_round_trip(itunesdb_path):
    """Parse and write back should produce identical bytes (ignoring hash regions)."""
    from pygpod.db.parser import parse_itunesdb
    from pygpod.db.writer import write_itunesdb

    with open(itunesdb_path, "rb") as f:
        original = f.read()

    root = parse_itunesdb(original)
    written = write_itunesdb(root)

    # Zero hash regions for comparison (libgpod writes hashes pygpod preserves)
    orig = bytearray(original)
    rewr = bytearray(written)
    for buf in (orig, rewr):
        if len(buf) >= 0x72:
            buf[0x58:0x72] = b"\x00" * 26
        if len(buf) >= 0x72 + 46:
            buf[0x72:0x72 + 46] = b"\x00" * 46
        if len(buf) >= 0xE4:
            buf[0xAB:0xE4] = b"\x00" * 57

    assert len(orig) == len(rewr), (
        f"Size mismatch: original={len(original)}, written={len(written)}"
    )
    assert orig == rewr


def test_invalid_magic():
    """Should raise ParseError on invalid data."""
    from pygpod.db.parser import parse_itunesdb
    from pygpod.exceptions import ParseError

    try:
        parse_itunesdb(b"not a valid database")
        assert False, "Should have raised ParseError"
    except ParseError:
        pass


def test_make_string_mhod():
    """Test creating a new string MHOD."""
    from pygpod.db.writer import make_string_mhod

    mhod = make_string_mhod(1, "Test Title")
    assert mhod.magic == b"mhod"
    assert mhod.fields["mhod_type"] == 1
    assert mhod.fields["string"] == "Test Title"
