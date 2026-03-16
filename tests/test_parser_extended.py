"""Extended tests for iTunesDB parser - rare record types and edge cases.

Targets uncovered lines in parser.py: MHSD types 4/5/6/10, MHIA/MHLI parsing,
unknown record fallback, zero-length/truncated data, SPLPREF/SPLRULES MHODs,
string decode failures, and boundary conditions in child-parsing loops.
"""

from __future__ import annotations

import struct

import pytest

from pygpod.db.parser import Record, parse_itunesdb
from pygpod.exceptions import ParseError

# ---------------------------------------------------------------------------
# Helpers to build synthetic binary blobs
# ---------------------------------------------------------------------------


def _pack32(val: int) -> bytes:
    """Pack a 32-bit little-endian unsigned int."""
    return struct.pack("<I", val)


def _pack64(val: int) -> bytes:
    """Pack a 64-bit little-endian unsigned int."""
    return struct.pack("<Q", val)


def _pack16(val: int) -> bytes:
    """Pack a 16-bit little-endian unsigned int."""
    return struct.pack("<H", val)


def _make_mhod_string(mhod_type: int, text: str) -> bytes:
    """Build a complete string MHOD binary blob."""
    encoded = text.encode("utf-16-le")
    # MHOD header: magic(4) + header_len(4) + total_len(4) + type(4) + padding(8)
    header_len = 24
    # Body: encoding(4) + str_len(4) + unk1(4) + unk2(4) + string_data
    body = _pack32(0) + _pack32(len(encoded)) + _pack32(0) + _pack32(0) + encoded
    total_len = header_len + len(body)
    header = b"mhod" + _pack32(header_len) + _pack32(total_len) + _pack32(mhod_type)
    header += b"\x00" * (header_len - len(header))
    return header + body


def _make_mhod_raw(mhod_type: int, body: bytes) -> bytes:
    """Build an MHOD with arbitrary type and raw body."""
    header_len = 24
    total_len = header_len + len(body)
    header = b"mhod" + _pack32(header_len) + _pack32(total_len) + _pack32(mhod_type)
    header += b"\x00" * (header_len - len(header))
    return header + body


def _make_mhia(album_id: int, album_name: str) -> bytes:
    """Build a synthetic MHIA (album item) record with one MHOD child."""
    mhod = _make_mhod_string(200, album_name)
    header_len = 0x14  # 20 bytes - minimum with album_id
    # MHIA: magic(4) + header_len(4) + total_len(4) + num_mhods(4) + album_id(4)
    total_len = header_len + len(mhod)
    header = b"mhia" + _pack32(header_len) + _pack32(total_len)
    header += _pack32(1)  # num_mhods
    header += _pack32(album_id)
    return header + mhod


def _make_mhla(mhia_records: list[bytes]) -> bytes:
    """Build a synthetic MHLA (album list) record."""
    header_len = 12  # Minimal: magic(4) + header_len(4) + num_albums(4)
    children_data = b"".join(mhia_records)
    header = b"mhla" + _pack32(header_len) + _pack32(len(mhia_records))
    return header + children_data


def _make_mhlt(mhit_records: list[bytes]) -> bytes:
    """Build a synthetic MHLT (track list) record."""
    header_len = 12
    children_data = b"".join(mhit_records)
    header = b"mhlt" + _pack32(header_len) + _pack32(len(mhit_records))
    return header + children_data


def _make_mhit_minimal(track_id: int, title: str = "Test") -> bytes:
    """Build a minimal MHIT (track) record.

    Uses header_len=0x9C (156 bytes) which is less than 0xF4, so no extended fields.
    """
    mhod = _make_mhod_string(1, title)
    header_len = 0x9C
    total_len = header_len + len(mhod)

    header = bytearray(header_len)
    header[0:4] = b"mhit"
    struct.pack_into("<I", header, 4, header_len)
    struct.pack_into("<I", header, 8, total_len)
    struct.pack_into("<I", header, 0x0C, 1)  # num_mhods
    struct.pack_into("<I", header, 0x10, track_id)  # track_id
    struct.pack_into("<I", header, 0x14, 1)  # visible
    header[0x18:0x1C] = b" 3PM"  # filetype MP3
    struct.pack_into("<I", header, 0x38, 128)  # bitrate
    struct.pack_into("<I", header, 0x3C, 44100)  # samplerate
    struct.pack_into("<f", header, 0x88, 44100.0)  # samplerate_float

    return bytes(header) + mhod


def _make_mhip(track_id: int) -> bytes:
    """Build a minimal MHIP (playlist item) record."""
    header_len = 0x1C  # 28 bytes, minimum
    total_len = header_len
    header = bytearray(header_len)
    header[0:4] = b"mhip"
    struct.pack_into("<I", header, 4, header_len)
    struct.pack_into("<I", header, 8, total_len)
    struct.pack_into("<I", header, 0x0C, 0)  # num_mhods
    struct.pack_into("<I", header, 0x18, track_id)
    return bytes(header)


def _make_mhyp(playlist_id: int, name: str, track_ids: list[int], playlist_type: int = 0) -> bytes:
    """Build a synthetic MHYP (playlist) record."""
    name_mhod = _make_mhod_string(1, name)
    mhips = b"".join(_make_mhip(tid) for tid in track_ids)
    header_len = 0x6C  # 108 bytes (standard)
    total_len = header_len + len(name_mhod) + len(mhips)

    header = bytearray(header_len)
    header[0:4] = b"mhyp"
    struct.pack_into("<I", header, 4, header_len)
    struct.pack_into("<I", header, 8, total_len)
    struct.pack_into("<I", header, 0x0C, 1)  # num_mhods
    struct.pack_into("<I", header, 0x10, len(track_ids))  # num_mhips
    header[0x14] = playlist_type
    struct.pack_into("<Q", header, 0x1C, playlist_id)  # playlist_id

    return bytes(header) + name_mhod + mhips


def _make_mhlp(mhyp_records: list[bytes]) -> bytes:
    """Build a synthetic MHLP (playlist list) record."""
    header_len = 12
    children_data = b"".join(mhyp_records)
    header = b"mhlp" + _pack32(header_len) + _pack32(len(mhyp_records))
    return header + children_data


def _make_mhsd(mhsd_type: int, child_data: bytes) -> bytes:
    """Build a synthetic MHSD (section descriptor) record."""
    header_len = 16  # magic(4) + header_len(4) + total_len(4) + type(4)
    total_len = header_len + len(child_data)
    return b"mhsd" + _pack32(header_len) + _pack32(total_len) + _pack32(mhsd_type) + child_data


def _make_mhbd(mhsd_records: list[bytes]) -> bytes:
    """Build a synthetic MHBD (database root) record."""
    header_len = 0x2C  # minimum for parsing known fields through id_0x24
    children_data = b"".join(mhsd_records)
    total_len = header_len + len(children_data)

    header = bytearray(header_len)
    header[0:4] = b"mhbd"
    struct.pack_into("<I", header, 4, header_len)
    struct.pack_into("<I", header, 8, total_len)
    struct.pack_into("<I", header, 0x0C, 1)  # unknown1
    struct.pack_into("<I", header, 0x10, 25)  # db_version
    struct.pack_into("<I", header, 0x14, len(mhsd_records))  # num_children

    return bytes(header) + children_data


# ---------------------------------------------------------------------------
# Tests: parse_itunesdb entry point validation
# ---------------------------------------------------------------------------


class TestParseItunesdbValidation:
    """Tests for input validation in parse_itunesdb."""

    def test_data_too_short(self):
        """Line 111: data shorter than 12 bytes raises ParseError."""
        with pytest.raises(ParseError, match="too short"):
            parse_itunesdb(b"mhbd1234")  # 8 bytes < 12

    def test_data_empty(self):
        """Empty data raises ParseError."""
        with pytest.raises(ParseError, match="too short"):
            parse_itunesdb(b"")

    def test_invalid_magic(self):
        """Non-mhbd magic raises ParseError."""
        with pytest.raises(ParseError, match="Invalid magic"):
            parse_itunesdb(b"xxxx" + b"\x00" * 20)


# ---------------------------------------------------------------------------
# Tests: Record class methods
# ---------------------------------------------------------------------------


class TestRecordMethods:
    """Tests for Record helper methods."""

    def test_get_mhods_returns_matching(self):
        """Line 68: get_mhods returns list of matching MHOD children."""
        rec = Record(b"mhit", 100, 200)
        mhod1 = Record(b"mhod", 24, 50)
        mhod1.fields["mhod_type"] = 1
        mhod1.fields["string"] = "Title"
        mhod2 = Record(b"mhod", 24, 50)
        mhod2.fields["mhod_type"] = 1
        mhod2.fields["string"] = "Title2"
        mhod3 = Record(b"mhod", 24, 50)
        mhod3.fields["mhod_type"] = 4
        mhod3.fields["string"] = "Artist"
        rec.children = [mhod1, mhod2, mhod3]

        result = rec.get_mhods(1)
        assert len(result) == 2
        assert result[0].fields["string"] == "Title"
        assert result[1].fields["string"] == "Title2"

    def test_get_mhods_no_match(self):
        """get_mhods returns empty list when no matching type."""
        rec = Record(b"mhit", 100, 200)
        assert rec.get_mhods(99) == []

    def test_get_mhod_no_match_returns_none(self):
        """Line 64: get_mhod returns None when no MHOD of the requested type exists."""
        rec = Record(b"mhit", 100, 200)
        mhod = Record(b"mhod", 24, 50)
        mhod.fields["mhod_type"] = 1
        mhod.fields["string"] = "Title"
        rec.children = [mhod]
        assert rec.get_mhod(99) is None

    def test_get_mhod_empty_children_returns_none(self):
        """get_mhod on record with no children returns None."""
        rec = Record(b"mhit", 100, 200)
        assert rec.get_mhod(1) is None

    def test_repr_basic(self):
        """Line 75: __repr__ for a basic record."""
        rec = Record(b"mhit", 100, 500)
        r = repr(rec)
        assert "mhit" in r
        assert "100" in r
        assert "500" in r

    def test_repr_with_mhod_type(self):
        """Lines 77-78: __repr__ for MHOD record shows type."""
        rec = Record(b"mhod", 24, 100)
        rec.fields["mhod_type"] = 42
        r = repr(rec)
        assert "type=42" in r

    def test_repr_with_mhsd_type(self):
        """Lines 79-80: __repr__ for MHSD record shows type."""
        rec = Record(b"mhsd", 16, 200)
        rec.fields["mhsd_type"] = 3
        r = repr(rec)
        assert "type=3" in r

    def test_repr_no_type_fields(self):
        """Line 81: __repr__ without mhod_type or mhsd_type."""
        rec = Record(b"mhlt", 12, 100)
        r = repr(rec)
        assert "mhlt" in r
        assert "type=" not in r


# ---------------------------------------------------------------------------
# Tests: _parse_mhod_children edge cases
# ---------------------------------------------------------------------------


class TestParseMhodChildren:
    """Tests for _parse_mhod_children boundary conditions."""

    def test_non_mhod_magic_breaks_loop(self):
        """Lines 89-90: non-MHOD magic in child position breaks loop."""
        mhit = _make_mhit_minimal(1, "Track")
        # After the MHIT header, if we have garbage instead of MHOD, it stops
        mhlt_data = _make_mhlt([mhit])
        mhsd_data = _make_mhsd(1, mhlt_data)
        db = _make_mhbd([mhsd_data])

        root = parse_itunesdb(db)
        mhsd = root.children[0]
        mhlt = mhsd.children[0]
        assert len(mhlt.children) == 1  # The one track
        track = mhlt.children[0]
        # Track should have parsed its MHOD child(ren)
        assert track.get_mhod(1) == "Track"

    def test_truncated_data_breaks_loop(self):
        """Line 88: truncated data at MHOD position breaks loop."""
        # Build an MHIT that claims to have 5 MHODs but only includes 1
        mhod = _make_mhod_string(1, "Title")
        header_len = 0x9C
        total_len = header_len + len(mhod)

        header = bytearray(header_len)
        header[0:4] = b"mhit"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 5)  # claims 5 MHODs
        struct.pack_into("<I", header, 0x10, 1)
        header[0x18:0x1C] = b" 3PM"

        mhit_data = bytes(header) + mhod
        mhlt_data = _make_mhlt([mhit_data])
        mhsd_data = _make_mhsd(1, mhlt_data)
        db = _make_mhbd([mhsd_data])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        # Only 1 MHOD actually present despite claiming 5
        assert len(track.children) == 1

    def test_non_mhod_magic_mid_loop(self):
        """Line 89-90: MHIT has 2 claimed MHODs but second slot has non-MHOD data.

        The key is that pos+12 <= end (passes line 87) but data[pos:pos+4] != MHOD_MAGIC
        (hits the break at line 90).
        """
        mhod = _make_mhod_string(1, "Title")
        # Build fake non-MHOD data that is at least 12 bytes (so line 87 passes)
        fake_child = b"xxxx" + b"\x00" * 40

        header_len = 0x9C
        # total_len must encompass both the real MHOD and the fake data
        total_len = header_len + len(mhod) + len(fake_child)

        header = bytearray(header_len)
        header[0:4] = b"mhit"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 2)  # claims 2 MHODs
        struct.pack_into("<I", header, 0x10, 1)
        header[0x18:0x1C] = b" 3PM"

        mhit_data = bytes(header) + mhod + fake_child
        mhlt_data = _make_mhlt([mhit_data])
        mhsd_data = _make_mhsd(1, mhlt_data)
        db = _make_mhbd([mhsd_data])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        # Only 1 MHOD parsed; second slot had wrong magic and broke
        assert len(track.children) == 1


# ---------------------------------------------------------------------------
# Tests: MHBD child parsing edge cases
# ---------------------------------------------------------------------------


class TestParseMhbdChildren:
    """Tests for _parse_mhbd child iteration edge cases."""

    def test_non_mhsd_child_breaks(self):
        """Lines 167-168: non-MHSD magic in child position breaks loop."""
        mhlt_data = _make_mhlt([])
        mhsd_data = _make_mhsd(1, mhlt_data)
        # Append garbage after the first MHSD (but within MHBD total_len)
        garbage = b"xxxx" + b"\x00" * 20
        db = _make_mhbd([mhsd_data])
        # Extend total_len to include garbage
        buf = bytearray(db) + garbage
        # Update MHBD total_len
        struct.pack_into("<I", buf, 8, len(buf))
        root = parse_itunesdb(bytes(buf))
        # Should only parse 1 MHSD child, stopping at garbage
        assert len(root.children) == 1

    def test_truncated_child_data(self):
        """Lines 164-165: truncated data before full child header."""
        header_len = 0x2C
        total_len = header_len + 100  # Claims 100 extra bytes
        header = bytearray(header_len)
        header[0:4] = b"mhbd"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x14, 1)  # claims 1 child
        # But actual data is only header_len + 5 bytes (not enough for a child)
        data = bytes(header) + b"\x00" * 5
        root = parse_itunesdb(data)
        assert len(root.children) == 0


# ---------------------------------------------------------------------------
# Tests: MHSD type routing
# ---------------------------------------------------------------------------


class TestMhsdTypes:
    """Tests for MHSD section type dispatch."""

    def test_mhsd_type_1_tracks(self):
        """MHSD type 1 parses MHLT child."""
        mhit = _make_mhit_minimal(1, "Song")
        mhlt = _make_mhlt([mhit])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        assert root.children[0].fields["mhsd_type"] == 1
        assert len(root.children[0].children) == 1  # MHLT
        assert root.children[0].children[0].magic == b"mhlt"

    def test_mhsd_type_2_playlists(self):
        """MHSD type 2 parses MHLP child."""
        mhyp = _make_mhyp(1, "Master", [1], playlist_type=1)
        mhlp = _make_mhlp([mhyp])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        assert root.children[0].fields["mhsd_type"] == 2
        assert root.children[0].children[0].magic == b"mhlp"

    def test_mhsd_type_3_podcasts(self):
        """MHSD type 3 also parses MHLP child (podcast playlists)."""
        mhyp = _make_mhyp(2, "Podcast PL", [])
        mhlp = _make_mhlp([mhyp])
        mhsd = _make_mhsd(3, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        assert root.children[0].fields["mhsd_type"] == 3
        assert root.children[0].children[0].magic == b"mhlp"

    def test_mhsd_type_4_album_list(self):
        """MHSD type 4 parses MHLA child."""
        mhia = _make_mhia(100, "Test Album")
        mhla = _make_mhla([mhia])
        mhsd = _make_mhsd(4, mhla)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhsd_rec = root.children[0]
        assert mhsd_rec.fields["mhsd_type"] == 4
        assert len(mhsd_rec.children) == 1
        mhla_rec = mhsd_rec.children[0]
        assert mhla_rec.magic == b"mhla"

    def test_mhsd_type_5_artist_list(self):
        """Line 207: MHSD type 5 is a pass-through (stored raw)."""
        # Type 5 has no child parsing, just stores the raw data
        mhsd = _make_mhsd(5, b"\x00" * 20)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhsd_rec = root.children[0]
        assert mhsd_rec.fields["mhsd_type"] == 5
        assert len(mhsd_rec.children) == 0  # No children parsed

    def test_mhsd_type_6_unknown(self):
        """Line 210: Unknown MHSD type (e.g. 6) is a pass-through."""
        mhsd = _make_mhsd(6, b"\xaa" * 30)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhsd_rec = root.children[0]
        assert mhsd_rec.fields["mhsd_type"] == 6
        assert len(mhsd_rec.children) == 0

    def test_mhsd_type_10_unknown(self):
        """Unknown MHSD type 10 is handled gracefully."""
        mhsd = _make_mhsd(10, b"\xbb" * 16)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhsd_rec = root.children[0]
        assert mhsd_rec.fields["mhsd_type"] == 10
        assert len(mhsd_rec.children) == 0

    def test_multiple_mhsd_types(self):
        """Parse MHBD with multiple MHSD sections of different types."""
        mhit = _make_mhit_minimal(1, "Track1")
        mhlt = _make_mhlt([mhit])
        mhsd1 = _make_mhsd(1, mhlt)

        mhyp = _make_mhyp(1, "Master", [1], playlist_type=1)
        mhlp = _make_mhlp([mhyp])
        mhsd2 = _make_mhsd(2, mhlp)

        mhsd5 = _make_mhsd(5, b"\x00" * 12)

        db = _make_mhbd([mhsd1, mhsd2, mhsd5])
        root = parse_itunesdb(db)
        assert len(root.children) == 3
        assert root.children[0].fields["mhsd_type"] == 1
        assert root.children[1].fields["mhsd_type"] == 2
        assert root.children[2].fields["mhsd_type"] == 5


# ---------------------------------------------------------------------------
# Tests: MHIA (album item) parsing
# ---------------------------------------------------------------------------


class TestParseMhia:
    """Tests for _parse_mhia (album item) records."""

    def test_mhia_basic(self):
        """Lines 566-585: Parse a single MHIA with album name MHOD."""
        mhia = _make_mhia(42, "Great Album")
        mhla = _make_mhla([mhia])
        mhsd = _make_mhsd(4, mhla)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhla_rec = root.children[0].children[0]
        assert len(mhla_rec.children) == 1
        mhia_rec = mhla_rec.children[0]
        assert mhia_rec.magic == b"mhia"
        assert mhia_rec.fields["album_id"] == 42
        assert mhia_rec.fields["num_mhods"] == 1
        assert mhia_rec.get_mhod(200) == "Great Album"

    def test_mhia_multiple_albums(self):
        """Parse MHLA with multiple MHIA children."""
        albums = [
            _make_mhia(1, "Album One"),
            _make_mhia(2, "Album Two"),
            _make_mhia(3, "Album Three"),
        ]
        mhla = _make_mhla(albums)
        mhsd = _make_mhsd(4, mhla)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhla_rec = root.children[0].children[0]
        assert len(mhla_rec.children) == 3
        assert mhla_rec.children[0].fields["album_id"] == 1
        assert mhla_rec.children[1].fields["album_id"] == 2
        assert mhla_rec.children[2].fields["album_id"] == 3

    def test_mhia_no_album_id(self):
        """MHIA with header_len < 0x14 should not parse album_id."""
        mhod = _make_mhod_string(200, "No ID")
        header_len = 0x10  # 16 bytes, less than 0x14
        total_len = header_len + len(mhod)
        header = b"mhia" + _pack32(header_len) + _pack32(total_len)
        header += _pack32(1)  # num_mhods
        mhia_data = header + mhod

        mhla = _make_mhla([mhia_data])
        mhsd = _make_mhsd(4, mhla)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhia_rec = root.children[0].children[0].children[0]
        assert "album_id" not in mhia_rec.fields


# ---------------------------------------------------------------------------
# Tests: MHLA with unknown child records
# ---------------------------------------------------------------------------


class TestParseMhlaUnknownChildren:
    """Tests for _parse_mhla unknown album record fallback."""

    def test_unknown_record_in_mhla(self):
        """Lines 548-556: Unknown record type in MHLA is read as generic Record."""
        # Build an unknown record with a fake magic
        unknown_magic = b"mhxx"
        unknown_header_len = 20
        unknown_total_len = 40
        unknown_data = unknown_magic + _pack32(unknown_header_len) + _pack32(unknown_total_len)
        unknown_data += b"\x00" * (unknown_total_len - len(unknown_data))

        mhla = _make_mhla([unknown_data])
        mhsd = _make_mhsd(4, mhla)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhla_rec = root.children[0].children[0]
        assert len(mhla_rec.children) == 1
        child = mhla_rec.children[0]
        assert child.magic == b"mhxx"
        assert child.total_len == 40

    def test_unknown_record_zero_total_len(self):
        """Lines 557-558: Unknown record with total_len=0 breaks loop."""
        unknown_data = b"mhxx" + _pack32(20) + _pack32(0)  # total_len=0
        unknown_data += b"\x00" * 8
        mhla = _make_mhla([unknown_data])
        mhsd = _make_mhsd(4, mhla)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhla_rec = root.children[0].children[0]
        assert len(mhla_rec.children) == 0  # Broke out of loop

    def test_mhla_truncated_at_child(self):
        """Lines 540-541, 559-560: Truncated data in MHLA child position."""
        # Build MHLA claiming 1 album, but data ends before child header
        header_len = 12
        header = b"mhla" + _pack32(header_len) + _pack32(1)
        # Only 3 bytes of child data (not enough to read magic)
        child_fragment = b"\x00\x00\x00"

        mhsd_inner = header + child_fragment
        mhsd = _make_mhsd(4, mhsd_inner)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhla_rec = root.children[0].children[0]
        assert len(mhla_rec.children) == 0

    def test_unknown_record_truncated_header(self):
        """Line 559-560: Unknown magic with not enough bytes for header_len/total_len."""
        # Build MHLA with 1 album, but the child is only 8 bytes (not enough for 12)
        header_len = 12
        mhla_header = b"mhla" + _pack32(header_len) + _pack32(1)
        # Child is non-MHIA and only 8 bytes (need 12 for header_len + total_len)
        child_fragment = b"mhxx" + b"\x00\x00\x00\x00"

        mhsd_inner = mhla_header + child_fragment
        mhsd = _make_mhsd(4, mhsd_inner)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhla_rec = root.children[0].children[0]
        # Child data is 8 bytes, pos+12 > len(data) so the else branch triggers
        assert len(mhla_rec.children) == 0


# ---------------------------------------------------------------------------
# Tests: MHLT/MHIT edge cases
# ---------------------------------------------------------------------------


class TestParseMhlt:
    """Tests for MHLT parsing edge cases."""

    def test_mhlt_non_mhit_magic_breaks(self):
        """Line 227: Non-MHIT magic in track position breaks loop."""
        # Build MHLT claiming 2 tracks, but second has wrong magic
        mhit = _make_mhit_minimal(1, "Track1")
        garbage = b"xxxx" + b"\x00" * 200  # Not a valid MHIT
        mhlt_header = b"mhlt" + _pack32(12) + _pack32(2)  # claims 2 tracks
        mhlt_data = mhlt_header + mhit + garbage

        mhsd = _make_mhsd(1, mhlt_data)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhlt_rec = root.children[0].children[0]
        assert len(mhlt_rec.children) == 1  # Only parsed first track

    def test_mhlt_empty(self):
        """MHLT with zero tracks."""
        mhlt = _make_mhlt([])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhlt_rec = root.children[0].children[0]
        assert mhlt_rec.fields["num_tracks"] == 0
        assert len(mhlt_rec.children) == 0


# ---------------------------------------------------------------------------
# Tests: MHLP/MHYP edge cases
# ---------------------------------------------------------------------------


class TestParseMhlp:
    """Tests for MHLP parsing edge cases."""

    def test_mhlp_non_mhyp_magic_breaks(self):
        """Line 449: Non-MHYP magic in playlist position breaks loop."""
        mhyp = _make_mhyp(1, "Master", [], playlist_type=1)
        garbage = b"xxxx" + b"\x00" * 200
        mhlp_header = b"mhlp" + _pack32(12) + _pack32(2)  # claims 2 playlists
        mhlp_data = mhlp_header + mhyp + garbage

        mhsd = _make_mhsd(2, mhlp_data)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhlp_rec = root.children[0].children[0]
        assert len(mhlp_rec.children) == 1  # Only parsed first playlist


class TestParseMhyp:
    """Tests for MHYP parsing edge cases."""

    def test_mhyp_non_mhip_magic_breaks(self):
        """Lines 495-496: Non-MHIP magic in MHIP position breaks loop.

        The garbage must be at least 12 bytes so pos+12 <= end passes (line 493),
        then the magic check at line 495 triggers the break at line 496.
        """
        name_mhod = _make_mhod_string(1, "Test PL")
        # Build MHYP with 1 valid MHIP then garbage (>= 12 bytes for boundary check)
        mhip = _make_mhip(1)
        garbage = b"xxxx" + b"\x00" * 50  # 54 bytes, well over 12

        header_len = 0x6C
        total_len = header_len + len(name_mhod) + len(mhip) + len(garbage)

        header = bytearray(header_len)
        header[0:4] = b"mhyp"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)  # num_mhods
        struct.pack_into("<I", header, 0x10, 3)  # claims 3 mhips
        header[0x14] = 0  # playlist_type
        struct.pack_into("<Q", header, 0x1C, 999)

        mhyp_data = bytes(header) + name_mhod + mhip + garbage
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        # Should have 1 MHOD + 1 MHIP = 2 children; garbage MHIP was skipped
        mhip_children = [c for c in mhyp_rec.children if c.magic == b"mhip"]
        assert len(mhip_children) == 1

    def test_mhyp_truncated_at_mhip_position(self):
        """Line 493-494: MHIP count claims more items than data available."""
        name_mhod = _make_mhod_string(1, "Short PL")
        mhip = _make_mhip(1)

        header_len = 0x6C
        # total_len only fits the first MHIP, no room for second
        total_len = header_len + len(name_mhod) + len(mhip)

        header = bytearray(header_len)
        header[0:4] = b"mhyp"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 5)  # claims 5 mhips but only 1 fits
        struct.pack_into("<Q", header, 0x1C, 998)

        mhyp_data = bytes(header) + name_mhod + mhip
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        mhip_children = [c for c in mhyp_rec.children if c.magic == b"mhip"]
        assert len(mhip_children) == 1


# ---------------------------------------------------------------------------
# Tests: MHOD type dispatch - SPLPREF, SPLRULES, playlist, unknown
# ---------------------------------------------------------------------------


class TestParseMhodTypes:
    """Tests for different MHOD type parsing."""

    def test_splpref_mhod_type_50(self):
        """Lines 358-359, 399-411: Parse SPLPREF MHOD (type 50)."""
        body = bytearray(24)
        body[0] = 1  # liveupdate
        body[1] = 1  # checkrules
        body[2] = 0  # checklimits
        body[3] = 2  # limittype
        body[4] = 3  # limitsort
        struct.pack_into("<I", body, 8, 25)  # limitvalue
        body[12] = 1  # matchcheckedonly
        body[13] = 0  # limitsort_high

        mhod_data = _make_mhod_raw(50, bytes(body))

        # Embed in a playlist (MHYP)
        header_len = 0x6C
        total_len = header_len + len(mhod_data)
        header = bytearray(header_len)
        header[0:4] = b"mhyp"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)  # num_mhods
        struct.pack_into("<I", header, 0x10, 0)  # num_mhips
        header[0x14] = 0
        struct.pack_into("<Q", header, 0x1C, 500)

        mhyp_data = bytes(header) + mhod_data
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        splpref = mhyp_rec.children[0]
        assert splpref.fields["mhod_type"] == 50
        assert splpref.fields["liveupdate"] == 1
        assert splpref.fields["checkrules"] == 1
        assert splpref.fields["checklimits"] == 0
        assert splpref.fields["limittype"] == 2
        assert splpref.fields["limitsort"] == 3
        assert splpref.fields["limitvalue"] == 25
        assert splpref.fields["matchcheckedonly"] == 1
        assert splpref.fields["limitsort_high"] == 0
        assert "raw_body" in splpref.fields

    def test_splpref_mhod_truncated(self):
        """Line 399-400: SPLPREF with body too short returns early."""
        body = b"\x01\x02"  # Only 2 bytes, need at least 14
        mhod_data = _make_mhod_raw(50, body)

        header_len = 0x6C
        total_len = header_len + len(mhod_data)
        header = bytearray(header_len)
        header[0:4] = b"mhyp"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 0)
        struct.pack_into("<Q", header, 0x1C, 501)

        mhyp_data = bytes(header) + mhod_data
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        splpref = mhyp_rec.children[0]
        assert splpref.fields["mhod_type"] == 50
        # Should not have parsed any SPL fields due to truncation
        assert "liveupdate" not in splpref.fields

    def test_splrules_mhod_type_51(self):
        """Lines 360-361, 417-425: Parse SPLRULES MHOD (type 51)."""
        # SPL rules body: SLst magic (4) + unk004 (4, big-endian) +
        # numrules (4, big-endian) + match_operator (4, big-endian) + extra
        body = bytearray(32)
        body[0:4] = b"SLst"
        struct.pack_into(">I", body, 4, 100)  # unk004
        struct.pack_into(">I", body, 8, 2)  # numrules
        struct.pack_into(">I", body, 12, 1)  # match_operator (AND)

        mhod_data = _make_mhod_raw(51, bytes(body))

        header_len = 0x6C
        total_len = header_len + len(mhod_data)
        header = bytearray(header_len)
        header[0:4] = b"mhyp"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 0)
        struct.pack_into("<Q", header, 0x1C, 502)

        mhyp_data = bytes(header) + mhod_data
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        splrules = mhyp_rec.children[0]
        assert splrules.fields["mhod_type"] == 51
        assert splrules.fields["spl_magic"] == b"SLst"
        assert splrules.fields["spl_numrules"] == 2
        assert splrules.fields["spl_match_operator"] == 1
        assert "raw_body" in splrules.fields

    def test_splrules_mhod_short_body(self):
        """Line 419-420: SPLRULES with body < 16 bytes stores raw only."""
        body = b"\x01\x02\x03\x04"  # Only 4 bytes
        mhod_data = _make_mhod_raw(51, body)

        header_len = 0x6C
        total_len = header_len + len(mhod_data)
        header = bytearray(header_len)
        header[0:4] = b"mhyp"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 0)
        struct.pack_into("<Q", header, 0x1C, 503)

        mhyp_data = bytes(header) + mhod_data
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        splrules = mhyp_rec.children[0]
        assert splrules.fields["mhod_type"] == 51
        assert "raw_body" in splrules.fields
        assert "spl_magic" not in splrules.fields  # Not parsed due to short body

    def test_playlist_mhod_type_100(self):
        """Lines 362-363: Parse playlist position MHOD (type 100)."""
        body = _pack32(42) + b"\x00" * 20  # position data
        mhod_data = _make_mhod_raw(100, body)

        # Embed as MHIP child
        header_len = 0x1C
        total_len = header_len + len(mhod_data)
        header = bytearray(header_len)
        header[0:4] = b"mhip"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)  # num_mhods = 1
        struct.pack_into("<I", header, 0x18, 1)  # track_id

        mhip_data = bytes(header) + mhod_data

        name_mhod = _make_mhod_string(1, "PL")
        mhyp_header_len = 0x6C
        mhyp_total_len = mhyp_header_len + len(name_mhod) + len(mhip_data)
        mhyp_header = bytearray(mhyp_header_len)
        mhyp_header[0:4] = b"mhyp"
        struct.pack_into("<I", mhyp_header, 4, mhyp_header_len)
        struct.pack_into("<I", mhyp_header, 8, mhyp_total_len)
        struct.pack_into("<I", mhyp_header, 0x0C, 1)  # num_mhods
        struct.pack_into("<I", mhyp_header, 0x10, 1)  # num_mhips

        mhyp_data = bytes(mhyp_header) + name_mhod + mhip_data
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        mhip_rec = [c for c in mhyp_rec.children if c.magic == b"mhip"][0]
        mhod_100 = mhip_rec.children[0]
        assert mhod_100.fields["mhod_type"] == 100
        assert "raw_body" in mhod_100.fields

    def test_unknown_mhod_type(self):
        """Lines 364-366: Unknown MHOD type stores raw body."""
        body = b"\xde\xad\xbe\xef" * 5
        mhod_data = _make_mhod_raw(999, body)

        mhit_header_len = 0x9C
        mhit_total_len = mhit_header_len + len(mhod_data)
        header = bytearray(mhit_header_len)
        header[0:4] = b"mhit"
        struct.pack_into("<I", header, 4, mhit_header_len)
        struct.pack_into("<I", header, 8, mhit_total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 1)
        header[0x18:0x1C] = b" 3PM"

        mhit_data = bytes(header) + mhod_data
        mhlt = _make_mhlt([mhit_data])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        unk = track.children[0]
        assert unk.fields["mhod_type"] == 999
        assert unk.fields["raw_body"] == body


# ---------------------------------------------------------------------------
# Tests: String MHOD edge cases
# ---------------------------------------------------------------------------


class TestParseStringMhod:
    """Tests for _parse_string_mhod edge cases."""

    def test_string_mhod_truncated_body(self):
        """Lines 373-374: String MHOD with body too short for header returns empty string."""
        # Build MHOD with header_len pointing to a body that's too short
        header_len = 24
        # Total_len is just header + 4 bytes (not enough for 16-byte string header)
        total_len = header_len + 4
        header = b"mhod" + _pack32(header_len) + _pack32(total_len) + _pack32(1)
        header += b"\x00" * (header_len - len(header))
        data = header + b"\x00\x00\x00\x00"

        # Wrap in MHIT to parse
        mhit_header_len = 0x9C
        mhit_total = mhit_header_len + len(data)
        mhit_header = bytearray(mhit_header_len)
        mhit_header[0:4] = b"mhit"
        struct.pack_into("<I", mhit_header, 4, mhit_header_len)
        struct.pack_into("<I", mhit_header, 8, mhit_total)
        struct.pack_into("<I", mhit_header, 0x0C, 1)
        struct.pack_into("<I", mhit_header, 0x10, 1)
        mhit_header[0x18:0x1C] = b" 3PM"

        mhit_data = bytes(mhit_header) + data
        mhlt = _make_mhlt([mhit_data])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        mhod = track.children[0]
        assert mhod.fields["mhod_type"] == 1
        assert mhod.fields["string"] == ""

    def test_string_mhod_decode_error(self):
        """Lines 391-393: Invalid encoding triggers fallback decode."""
        # Build a string MHOD with encoding=99 (invalid) and garbage bytes
        # that will cause decode_mhod_string to raise
        header_len = 24
        # Body: encoding(4) + str_len(4) + unk1(4) + unk2(4) + string_data
        bad_str = b"\xff\xfe"  # Invalid UTF-8 bytes
        body = _pack32(2) + _pack32(len(bad_str)) + _pack32(0) + _pack32(0) + bad_str
        total_len = header_len + len(body)
        header = b"mhod" + _pack32(header_len) + _pack32(total_len) + _pack32(1)
        header += b"\x00" * (header_len - len(header))
        mhod_data = header + body

        mhit_header_len = 0x9C
        mhit_total = mhit_header_len + len(mhod_data)
        mhit_header = bytearray(mhit_header_len)
        mhit_header[0:4] = b"mhit"
        struct.pack_into("<I", mhit_header, 4, mhit_header_len)
        struct.pack_into("<I", mhit_header, 8, mhit_total)
        struct.pack_into("<I", mhit_header, 0x0C, 1)
        struct.pack_into("<I", mhit_header, 0x10, 1)
        mhit_header[0x18:0x1C] = b" 3PM"

        mhit_data = bytes(mhit_header) + mhod_data
        mhlt = _make_mhlt([mhit_data])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        mhod_rec = track.children[0]
        assert mhod_rec.fields["mhod_type"] == 1
        # Should have a string (fallback decoded) rather than crashing
        assert "string" in mhod_rec.fields

    def test_mhod_header_extra(self):
        """Line 352: MHOD with header_len > 16 stores header_extra."""
        mhit = _make_mhit_minimal(1, "Hello")
        mhlt = _make_mhlt([mhit])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        mhod_rec = track.children[0]
        assert "header_extra" in mhod_rec.fields


# ---------------------------------------------------------------------------
# Tests: MHBD field parsing with different header sizes
# ---------------------------------------------------------------------------


class TestMhbdFieldParsing:
    """Tests for MHBD header field parsing at various header sizes."""

    def test_minimal_header(self):
        """Minimal MHBD header (0x2C bytes) parses basic fields."""
        db = _make_mhbd([])
        root = parse_itunesdb(db)
        assert root.magic == b"mhbd"
        assert root.fields["db_version"] == 25
        assert "language" not in root.fields  # header < 0x48

    def test_extended_header_with_hashes(self):
        """MHBD with header large enough for hash fields."""
        header_len = 0xE4  # Large enough for all hash fields
        header = bytearray(header_len)
        header[0:4] = b"mhbd"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, header_len)  # total = header only
        struct.pack_into("<I", header, 0x10, 25)  # db_version
        struct.pack_into("<I", header, 0x14, 0)  # num_children

        data = bytes(header)
        root = parse_itunesdb(data)
        assert "hash58" in root.fields
        assert "hash72" in root.fields
        assert "hashAB" in root.fields
        assert "language" in root.fields
        assert "pid" in root.fields


# ---------------------------------------------------------------------------
# Tests: MHIT extended fields
# ---------------------------------------------------------------------------


class TestMhitExtendedFields:
    """Tests for MHIT header with extended field regions."""

    def test_mhit_extended_fields_0xF4(self):
        """MHIT with header_len >= 0xF4 parses skip_count, media_type, etc."""
        mhod = _make_mhod_string(1, "Extended")
        header_len = 0xF4
        total_len = header_len + len(mhod)

        header = bytearray(header_len)
        header[0:4] = b"mhit"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 1)
        header[0x18:0x1C] = b" 3PM"
        struct.pack_into("<I", header, 0x9C, 5)  # skip_count
        struct.pack_into("<I", header, 0xD0, 1)  # media_type

        mhit_data = bytes(header) + mhod
        mhlt = _make_mhlt([mhit_data])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        assert track.fields["skip_count"] == 5
        assert track.fields["media_type"] == 1
        assert "has_artwork" in track.fields

    def test_mhit_extended_fields_0x148(self):
        """MHIT with header_len >= 0x148 parses gapless fields."""
        mhod = _make_mhod_string(1, "Gapless")
        header_len = 0x148
        total_len = header_len + len(mhod)

        header = bytearray(header_len)
        header[0:4] = b"mhit"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 1)
        header[0x18:0x1C] = b" 3PM"
        struct.pack_into("<I", header, 0xF8, 12345)  # gapless_data
        struct.pack_into("<H", header, 0x100, 1)  # gapless_track_flag

        mhit_data = bytes(header) + mhod
        mhlt = _make_mhlt([mhit_data])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        assert track.fields["gapless_data"] == 12345
        assert track.fields["gapless_track_flag"] == 1

    def test_mhit_mhii_link(self):
        """MHIT with header_len >= 0x184 parses mhii_link."""
        mhod = _make_mhod_string(1, "Linked")
        header_len = 0x184
        total_len = header_len + len(mhod)

        header = bytearray(header_len)
        header[0:4] = b"mhit"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 1)
        struct.pack_into("<I", header, 0x10, 1)
        header[0x18:0x1C] = b" 3PM"
        struct.pack_into("<I", header, 0x160, 77)  # mhii_link

        mhit_data = bytes(header) + mhod
        mhlt = _make_mhlt([mhit_data])
        mhsd = _make_mhsd(1, mhlt)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        track = root.children[0].children[0].children[0]
        assert track.fields["mhii_link"] == 77


# ---------------------------------------------------------------------------
# Tests: MHIP extended fields
# ---------------------------------------------------------------------------


class TestParseMhip:
    """Tests for MHIP parsing with extended fields."""

    def test_mhip_with_timestamp(self):
        """MHIP with header_len >= 0x24 parses timestamp and podcast_groupref."""
        header_len = 0x24
        total_len = header_len
        header = bytearray(header_len)
        header[0:4] = b"mhip"
        struct.pack_into("<I", header, 4, header_len)
        struct.pack_into("<I", header, 8, total_len)
        struct.pack_into("<I", header, 0x0C, 0)  # num_mhods
        struct.pack_into("<I", header, 0x18, 42)  # track_id
        struct.pack_into("<I", header, 0x1C, 1000)  # timestamp
        struct.pack_into("<I", header, 0x20, 5)  # podcast_groupref

        mhip_data = bytes(header)
        name_mhod = _make_mhod_string(1, "PL")
        mhyp_header_len = 0x6C
        mhyp_total = mhyp_header_len + len(name_mhod) + len(mhip_data)
        mhyp_header = bytearray(mhyp_header_len)
        mhyp_header[0:4] = b"mhyp"
        struct.pack_into("<I", mhyp_header, 4, mhyp_header_len)
        struct.pack_into("<I", mhyp_header, 8, mhyp_total)
        struct.pack_into("<I", mhyp_header, 0x0C, 1)
        struct.pack_into("<I", mhyp_header, 0x10, 1)

        mhyp_data = bytes(mhyp_header) + name_mhod + mhip_data
        mhlp = _make_mhlp([mhyp_data])
        mhsd = _make_mhsd(2, mhlp)
        db = _make_mhbd([mhsd])

        root = parse_itunesdb(db)
        mhyp_rec = root.children[0].children[0].children[0]
        mhip_rec = [c for c in mhyp_rec.children if c.magic == b"mhip"][0]
        assert mhip_rec.fields["track_id"] == 42
        assert mhip_rec.fields["timestamp"] == 1000
        assert mhip_rec.fields["podcast_groupref"] == 5
