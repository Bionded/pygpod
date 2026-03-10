"""Tests for newly implemented features: hash58, smart playlists,
playcounts, chapter data, artwork formats, timezone, photo db."""

import struct

# ============================================================================
# hash58 tests
# ============================================================================


def test_hash58_key_derivation():
    """Test HMAC key derivation from FirewireGuid."""
    from pygpod.hash.hash58 import _derive_key

    key = _derive_key("000A2700131A2BFC")
    assert len(key) == 64
    # Key should be deterministic
    key2 = _derive_key("000A2700131A2BFC")
    assert key == key2
    # Different GUID = different key
    key3 = _derive_key("FFFFFFFFFFFFFFFF")
    assert key3 != key


def test_hash58_hmac():
    """Test HMAC-SHA1 computation."""
    import hashlib
    import hmac

    from pygpod.hash.hash58 import _hmac_sha1

    key = b"\x0b" * 20
    data = b"Hi There"

    our_result = _hmac_sha1(key + b"\x00" * 44, data)
    ref_result = hmac.new(key, data, hashlib.sha1).digest()

    assert our_result == ref_result


def test_hash58_compute():
    """Test full hash58 computation."""
    from pygpod.hash.hash58 import compute_hash58

    # Create minimal fake iTunesDB header
    data = bytearray(256)
    data[0:4] = b"mhbd"
    data[4:8] = struct.pack("<I", 244)  # header_len
    data[8:12] = struct.pack("<I", 256)  # total_len

    result = compute_hash58(bytes(data), "000A2700131A2BFC")
    # hash58 should be non-zero at offset 0x58
    assert result[0x58 : 0x58 + 20] != b"\x00" * 20
    # Magic should be preserved
    assert result[0:4] == b"mhbd"


# ============================================================================
# Smart playlist tests
# ============================================================================


def test_spl_field_types():
    """Test SPL field type lookup."""
    from pygpod.model.smartplaylist import SPLField, SPLFieldType, get_field_type

    assert get_field_type(SPLField.SONG_NAME) == SPLFieldType.STRING
    assert get_field_type(SPLField.BITRATE) == SPLFieldType.INT
    assert get_field_type(SPLField.COMPILATION) == SPLFieldType.BOOLEAN
    assert get_field_type(SPLField.DATE_ADDED) == SPLFieldType.DATE
    assert get_field_type(SPLField.PLAYLIST) == SPLFieldType.PLAYLIST


def test_spl_rule_eval_string():
    """Test string rule evaluation."""
    from pygpod.db.parser import Record
    from pygpod.db.writer import make_string_mhod
    from pygpod.model.smartplaylist import SPLAction, SPLField, SPLRule, eval_rule
    from pygpod.model.track import Track

    # Build a track record
    rec = Record(b"mhit", 100, 200)
    rec.raw_header = b"\x00" * 100
    rec.fields = {"track_id": 1, "dbid": 1}
    rec.children.append(make_string_mhod(1, "Rolling in the Deep"))  # title
    rec.children.append(make_string_mhod(4, "Adele"))  # artist

    track = Track.from_record(rec)

    # IS_STRING match
    rule = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Adele")
    assert eval_rule(rule, track)

    # CONTAINS match
    rule = SPLRule(field=SPLField.SONG_NAME, action=SPLAction.CONTAINS, string="Rolling")
    assert eval_rule(rule, track)

    # DOES_NOT_CONTAIN
    rule = SPLRule(field=SPLField.ARTIST, action=SPLAction.DOES_NOT_CONTAIN, string="Beatles")
    assert eval_rule(rule, track)

    # IS_NOT
    rule = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_NOT, string="Adele")
    assert not eval_rule(rule, track)


def test_spl_rule_eval_int():
    """Test integer rule evaluation."""
    from pygpod.db.parser import Record
    from pygpod.model.smartplaylist import SPLAction, SPLField, SPLRule, eval_rule
    from pygpod.model.track import Track

    rec = Record(b"mhit", 100, 200)
    rec.raw_header = b"\x00" * 100
    rec.fields = {"track_id": 1, "dbid": 1, "bitrate": 320, "year": 2011}

    track = Track.from_record(rec)

    # IS_INT
    rule = SPLRule(field=SPLField.BITRATE, action=SPLAction.IS_INT, fromvalue=320)
    assert eval_rule(rule, track)

    # IS_GREATER_THAN
    rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_GREATER_THAN, fromvalue=2000)
    assert eval_rule(rule, track)

    # IS_LESS_THAN
    rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_LESS_THAN, fromvalue=2000)
    assert not eval_rule(rule, track)

    # IS_IN_THE_RANGE
    rule = SPLRule(
        field=SPLField.YEAR, action=SPLAction.IS_IN_THE_RANGE, fromvalue=2010, tovalue=2015
    )
    assert eval_rule(rule, track)


def test_spl_evaluate_playlist():
    """Test evaluating rules against multiple tracks."""
    from pygpod.db.parser import Record
    from pygpod.db.writer import make_string_mhod
    from pygpod.model.smartplaylist import (
        SPLAction,
        SPLField,
        SPLMatch,
        SPLRule,
        evaluate_smart_playlist,
    )
    from pygpod.model.track import Track

    tracks = []
    for i, (artist, year) in enumerate(
        [("Adele", 2011), ("Beatles", 1965), ("Cypress Hill", 1993)]
    ):
        rec = Record(b"mhit", 100, 200)
        rec.raw_header = b"\x00" * 100
        rec.fields = {"track_id": i + 1, "dbid": i + 1, "year": year}
        rec.children.append(make_string_mhod(4, artist))
        tracks.append(Track.from_record(rec))

    # Rule: year > 2000
    rules = [SPLRule(field=SPLField.YEAR, action=SPLAction.IS_GREATER_THAN, fromvalue=2000)]
    result = evaluate_smart_playlist(rules, SPLMatch.AND, tracks)
    assert len(result) == 1
    assert result[0].record.fields["year"] == 2011


def test_spl_prefs_roundtrip():
    """Test SPL preferences serialization round-trip."""
    from pygpod.model.smartplaylist import (
        SPLLimitSort,
        SPLLimitType,
        SPLPrefs,
        parse_spl_prefs,
        write_spl_prefs,
    )

    prefs = SPLPrefs()
    prefs.liveupdate = True
    prefs.checklimits = True
    prefs.limittype = SPLLimitType.SONGS
    prefs.limitsort = SPLLimitSort.RANDOM
    prefs.limitvalue = 25

    data = write_spl_prefs(prefs)
    prefs2 = parse_spl_prefs(data)

    assert prefs2.liveupdate == prefs.liveupdate
    assert prefs2.checklimits == prefs.checklimits
    assert prefs2.limittype == prefs.limittype
    assert prefs2.limitvalue == prefs.limitvalue


# ============================================================================
# Playcounts tests
# ============================================================================


def test_parse_play_counts_binary():
    """Test parsing binary Play Counts file."""
    from pygpod.db.playcounts import parse_play_counts

    # Build a fake Play Counts file
    header = struct.pack("<III", 16, 16, 2) + b"\x00" * 4
    entry1 = struct.pack("<IIII", 5, 3819698947, 80, 1)  # 5 plays, rating 80
    entry2 = struct.pack("<IIII", 0, 0, 0, 0)

    data = header + entry1 + entry2

    entries = parse_play_counts(data)
    assert len(entries) == 2
    assert entries[0].play_count == 5
    assert entries[0].rating == 80
    assert entries[0].skip_count == 1
    assert entries[1].play_count == 0


def test_parse_itunesstats():
    """Test parsing iTunesStats text file."""
    from pygpod.db.playcounts import parse_itunesstats

    # Format: first line = count, then 6 lines per entry
    # (play_count, time_played, skip_count, time_skipped, bookmark, empty)
    text = "2\n3\n3819698947\n0\n0\n0\n0\n1\n0\n0\n0\n0\n0\n"
    entries = parse_itunesstats(text)
    assert len(entries) == 2
    assert entries[0].play_count == 3
    assert entries[0].time_played == 3819698947
    assert entries[1].play_count == 1


def test_parse_otg_playlists():
    """Test parsing OTG playlist file."""
    from pygpod.db.playcounts import parse_otg_playlists

    # Build a fake OTG file with correct "mhpo" format:
    # header(20): magic "mhpo" + header_length(0x14) + entry_length(4) + entry_count(3) + pad(4)
    header = b"mhpo" + struct.pack("<III", 0x14, 4, 3) + b"\x00" * 4
    indices = struct.pack("<III", 0, 5, 2)

    data = header + indices
    playlists = parse_otg_playlists(data)
    assert len(playlists) == 1
    assert playlists[0] == [0, 5, 2]


# ============================================================================
# Chapter data tests
# ============================================================================


def test_chapter_data_basic():
    """Test basic chapter data operations."""
    from pygpod.model.chapterdata import ChapterData

    cd = ChapterData()
    assert cd.chapter_count == 0

    # First chapter startpos=0 should be bumped to 1
    ch1 = cd.add_chapter("Intro", 0)
    assert ch1.startpos == 1

    ch2 = cd.add_chapter("Chapter 1", 60000)
    cd.add_chapter("Chapter 2", 120000)

    assert cd.chapter_count == 3
    assert len(list(cd)) == 3

    # Remove
    cd.remove_chapter(ch2)
    assert cd.chapter_count == 2


def test_chapter_data_roundtrip():
    """Test chapter data serialization round-trip."""
    from pygpod.model.chapterdata import ChapterData, parse_chapter_data, write_chapter_data

    cd = ChapterData()
    cd.add_chapter("Intro", 1)
    cd.add_chapter("Main", 60000)
    cd.add_chapter("Finale", 180000)

    data = write_chapter_data(cd)
    cd2 = parse_chapter_data(data)

    assert cd2 is not None
    assert cd2.chapter_count == 3
    assert cd2.chapters[0].title == "Intro"
    assert cd2.chapters[0].startpos == 1
    assert cd2.chapters[1].title == "Main"
    assert cd2.chapters[1].startpos == 60000
    assert cd2.chapters[2].title == "Finale"
    assert cd2.chapters[2].startpos == 180000


def test_chapter_data_duplicate():
    """Test chapter data deep copy."""
    from pygpod.model.chapterdata import ChapterData

    cd = ChapterData()
    cd.add_chapter("A", 1)
    cd.add_chapter("B", 5000)

    cd2 = cd.duplicate()
    assert cd2.chapter_count == 2
    assert cd2.chapters[0].title == "A"

    # Modifying copy shouldn't affect original
    cd2.chapters[0].title = "Modified"
    assert cd.chapters[0].title == "A"


# ============================================================================
# Artwork format tests
# ============================================================================


def test_artwork_rgb565_roundtrip():
    """Test RGB565 pack/unpack."""
    from pygpod.model.artwork import _unpack_rgb565

    # Create simple 2x2 pixel data
    data = struct.pack(
        "<HH HH",
        0xF800,
        0x07E0,  # Red, Green
        0x001F,
        0xFFFF,  # Blue, White
    )

    rgb = _unpack_rgb565(data, 2, 2)
    assert len(rgb) == 12  # 4 pixels * 3 bytes

    # Red pixel
    assert rgb[0] >= 248  # R
    assert rgb[1] == 0  # G
    assert rgb[2] == 0  # B


def test_artwork_rgb555_roundtrip():
    """Test RGB555 pack/unpack."""
    from pygpod.model.artwork import _unpack_rgb555

    # 1 pixel: R=31, G=0, B=0
    data = struct.pack("<H", 0x7C00)
    rgb = _unpack_rgb555(data, 1, 1)
    assert rgb[0] >= 248  # R
    assert rgb[1] == 0  # G
    assert rgb[2] == 0  # B


def test_artwork_format_table():
    """Test artwork format table has expected entries."""
    from pygpod.model.artwork import ARTWORK_FORMATS

    # iPod Photo/Video photo format (1015 = 130x88 RGB565_LE per libgpod)
    assert 1015 in ARTWORK_FORMATS
    w, h, fmt, bpp = ARTWORK_FORMATS[1015]
    assert w == 130
    assert h == 88
    assert bpp == 2


# ============================================================================
# Track extended fields tests
# ============================================================================


def test_track_extended_fields(ipod_fs_path):
    """Test new track properties."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    track = db.tracks[0]

    # These should all work without errors
    assert isinstance(track.has_artwork, bool)
    assert isinstance(track.skip_when_shuffling, bool)
    assert isinstance(track.remember_position, bool)
    assert isinstance(track.lyrics_flag, bool)
    assert isinstance(track.movie_flag, bool)
    assert isinstance(track.mark_unplayed, bool)
    assert isinstance(track.pregap, int)
    assert isinstance(track.postgap, int)
    assert isinstance(track.sample_count, int)
    assert isinstance(track.gapless_track_flag, bool)
    assert isinstance(track.gapless_album_flag, bool)
    assert isinstance(track.explicit_flag, int)
    assert isinstance(track.volume, int)
    assert isinstance(track.start_time, int)
    assert isinstance(track.stop_time, int)
    assert isinstance(track.soundcheck, int)
    assert isinstance(track.bookmark_time, int)
    assert isinstance(track.artwork_count, int)
    assert isinstance(track.artwork_size, int)
    assert isinstance(track.is_podcast, bool)
    assert isinstance(track.is_audiobook, bool)
    assert isinstance(track.is_video, bool)


def test_track_podcast_and_tv_fields(ipod_fs_path):
    """Test podcast/TV show MHOD properties don't crash."""
    import pygpod

    db = pygpod.Database(ipod_fs_path)
    track = db.tracks[0]

    # String MHODs that may be empty but should not error
    assert isinstance(track.subtitle, str)
    assert isinstance(track.podcast_url, str)
    assert isinstance(track.podcast_rss, str)
    assert isinstance(track.keywords, str)
    assert isinstance(track.category, str)
    assert isinstance(track.tvshow, str)
    assert isinstance(track.tvepisode, str)
    assert isinstance(track.tvnetwork, str)
    assert isinstance(track.sort_tvshow, str)


# ============================================================================
# Timezone tests
# ============================================================================


def test_timezone_local():
    """Test getting local timezone offset."""
    from pygpod.utils.timezone import get_local_tz_offset

    offset = get_local_tz_offset()
    # Should be within ±12 hours
    assert -43200 <= offset <= 43200


def test_timezone_validate():
    """Test timezone validation."""
    from pygpod.utils.timezone import _validate_tz

    assert _validate_tz(3600) == 3600
    assert _validate_tz(-18000) == -18000
    assert _validate_tz(50000) is None  # > 12 hours
    assert _validate_tz(-50000) is None


# ============================================================================
# Photo DB tests
# ============================================================================


def test_photo_album_basic():
    """Test photo album basic operations."""
    from pygpod.db.photodb import ALBUM_TYPE_MASTER, PhotoAlbum

    album = PhotoAlbum(name="Test", album_type=ALBUM_TYPE_MASTER)
    assert album.is_master
    assert album.photo_count == 0

    album.photo_ids.append(1)
    album.photo_ids.append(2)
    assert album.photo_count == 2
    assert len(album) == 2


def test_photodb_create_album():
    """Test creating and managing photo albums."""
    from pygpod.db.photodb import PhotoDB

    db = PhotoDB()
    album = db.create_album("Vacation")
    assert album.name == "Vacation"
    assert not album.is_master
    assert len(db.albums) == 1


# ============================================================================
# Integration: imports work
# ============================================================================


def test_imports():
    """Verify all new public API imports work."""
    import pygpod

    assert hasattr(pygpod, "ChapterData")
    assert hasattr(pygpod, "Chapter")
    assert hasattr(pygpod, "SPLRule")
    assert hasattr(pygpod, "SPLPrefs")
    assert hasattr(pygpod, "SPLField")
    assert hasattr(pygpod, "SPLAction")
    assert hasattr(pygpod, "SPLMatch")
    assert hasattr(pygpod, "evaluate_smart_playlist")
    assert hasattr(pygpod, "apply_limit")
    assert hasattr(pygpod, "PhotoDB")
    assert hasattr(pygpod, "Photo")
    assert hasattr(pygpod, "PhotoAlbum")
    assert hasattr(pygpod, "read_play_counts")
    assert hasattr(pygpod, "read_otg_playlists")
    assert hasattr(pygpod, "Artwork")


def test_hash58_generation_set():
    """Verify hash58 generation set exists and includes correct devices."""
    from pygpod.device.models import HASH58_GENERATIONS, IpodGeneration

    assert IpodGeneration.VIDEO_1 in HASH58_GENERATIONS
    assert IpodGeneration.VIDEO_2 in HASH58_GENERATIONS
    assert IpodGeneration.NANO_1 in HASH58_GENERATIONS
    assert IpodGeneration.NANO_2 in HASH58_GENERATIONS
    # Classics and Nano 3-4 also use hash58 (per libgpod itdb_device.c)
    assert IpodGeneration.CLASSIC_1 in HASH58_GENERATIONS
    assert IpodGeneration.CLASSIC_3 in HASH58_GENERATIONS
    assert IpodGeneration.NANO_3 in HASH58_GENERATIONS
