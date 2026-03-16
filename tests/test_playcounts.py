"""Tests for Play Counts, iTunesStats, PlayCounts.plist, and OTG playlists."""

import plistlib
import struct

from pygpod.db.playcounts import (
    PlayCountEntry,
    apply_play_counts,
    parse_itunesstats,
    parse_otg_playlists,
    parse_play_counts,
    parse_playcounts_plist,
    read_otg_playlists,
    read_play_counts,
)

# =========================================================================
# Play Counts (binary)
# =========================================================================


class TestParsePlayCounts:
    def _build(self, entries, entry_size=28, header_size=96):
        """Build Play Counts binary with mhdp magic."""
        buf = b"mhdp"
        buf += struct.pack("<III", header_size, entry_size, len(entries))
        buf += b"\x00" * (header_size - 16)
        for e in entries:
            entry = bytearray(entry_size)
            struct.pack_into("<I", entry, 0, e.get("play_count", 0))
            if entry_size >= 8:
                struct.pack_into("<I", entry, 4, e.get("time_played", 0))
            if entry_size >= 12:
                struct.pack_into("<I", entry, 8, e.get("bookmark_time", 0))
            if entry_size >= 16:
                struct.pack_into("<I", entry, 12, e.get("rating", 0))
            if entry_size >= 28:
                struct.pack_into("<I", entry, 20, e.get("skip_count", 0))
                struct.pack_into("<I", entry, 24, e.get("time_skipped", 0))
            buf += bytes(entry)
        return buf

    def test_basic(self):
        data = self._build(
            [
                {"play_count": 5, "time_played": 3700000000, "rating": 80},
                {"play_count": 0, "time_played": 0, "rating": 0},
                {"play_count": 3, "time_played": 3700001000, "rating": 60, "skip_count": 1},
            ]
        )
        entries = parse_play_counts(data)
        assert len(entries) == 3
        assert entries[0].play_count == 5
        assert entries[0].time_played == 3700000000
        assert entries[0].rating == 80
        assert entries[1].play_count == 0
        assert entries[1].rating == -1  # 0 means unchanged
        assert entries[2].skip_count == 1

    def test_minimal_entry_size(self):
        data = self._build([{"play_count": 10}], entry_size=4)
        entries = parse_play_counts(data)
        assert len(entries) == 1
        assert entries[0].play_count == 10
        assert entries[0].time_played == 0

    def test_with_bookmark(self):
        data = self._build(
            [
                {"play_count": 1, "bookmark_time": 45000},
            ],
            entry_size=28,
        )
        entries = parse_play_counts(data)
        assert entries[0].bookmark_time == 45000

    def test_empty(self):
        assert parse_play_counts(b"") == []

    def test_too_short(self):
        assert parse_play_counts(b"\x00" * 8) == []

    def test_zero_entry_size(self):
        data = struct.pack("<III", 16, 0, 5)
        data += b"\x00" * 4
        assert parse_play_counts(data) == []

    def test_truncated_entries(self):
        # Says 10 entries but only data for 2
        data = self._build(
            [
                {"play_count": 1},
                {"play_count": 2},
            ]
        )
        # Overwrite entry count to 10
        data = bytearray(data)
        struct.pack_into("<I", data, 8, 10)
        entries = parse_play_counts(bytes(data))
        assert len(entries) == 2


# =========================================================================
# iTunesStats (text)
# =========================================================================


class TestParseItunesStats:
    def test_basic(self):
        text = "2\n5\n3700000000\n1\n3700001000\n45000\n\n3\n3700002000\n0\n0\n0\n\n"
        entries = parse_itunesstats(text)
        assert len(entries) == 2
        assert entries[0].play_count == 5
        assert entries[0].time_played == 3700000000
        assert entries[0].skip_count == 1
        assert entries[0].bookmark_time == 45000
        assert entries[1].play_count == 3

    def test_empty(self):
        assert parse_itunesstats("") == []

    def test_invalid_header(self):
        assert parse_itunesstats("not_a_number") == []

    def test_truncated_entry(self):
        text = "1\n5\n3700000000\n"
        entries = parse_itunesstats(text)
        assert len(entries) == 1
        assert entries[0].play_count == 5

    def test_rating_always_minus_one(self):
        text = "1\n1\n0\n0\n0\n0\n\n"
        entries = parse_itunesstats(text)
        assert entries[0].rating == -1


# =========================================================================
# PlayCounts.plist
# =========================================================================


class TestParsePlaycountsPlist:
    def _build_plist(self, items):
        return plistlib.dumps({"items": items})

    def test_basic(self):
        data = self._build_plist(
            [
                {"playcount": 5, "rating": 80, "skipcount": 1, "bookmark": 1000},
                {"playcount": 0},
            ]
        )
        entries = parse_playcounts_plist(data)
        assert len(entries) == 2
        assert entries[0].play_count == 5
        assert entries[0].rating == 80
        assert entries[0].skip_count == 1
        assert entries[0].bookmark_time == 1000
        assert entries[1].play_count == 0
        assert entries[1].rating == -1

    def test_empty_items(self):
        data = self._build_plist([])
        assert parse_playcounts_plist(data) == []

    def test_non_dict_item(self):
        data = self._build_plist(["garbage", "also_garbage"])
        entries = parse_playcounts_plist(data)
        assert len(entries) == 2
        assert entries[0].play_count == 0

    def test_invalid_plist(self):
        assert parse_playcounts_plist(b"not a plist at all") == []

    def test_not_a_dict(self):
        data = plistlib.dumps(["just", "a", "list"])
        assert parse_playcounts_plist(data) == []


# =========================================================================
# OTG Playlists
# =========================================================================


class TestParseOTGPlaylists:
    def _build_otg(self, indices, header_len=0x14, entry_len=4):
        buf = b"mhpo"
        buf += struct.pack("<III", header_len, entry_len, len(indices))
        buf += b"\x00" * (header_len - 16)
        for idx in indices:
            entry = bytearray(entry_len)
            struct.pack_into("<I", entry, 0, idx)
            buf += bytes(entry)
        return buf

    def test_basic(self):
        data = self._build_otg([0, 3, 7, 15])
        playlists = parse_otg_playlists(data)
        assert len(playlists) == 1
        assert playlists[0] == [0, 3, 7, 15]

    def test_empty(self):
        assert parse_otg_playlists(b"") == []

    def test_too_short(self):
        assert parse_otg_playlists(b"\x00" * 10) == []

    def test_wrong_magic(self):
        data = b"XXXX" + b"\x00" * 16
        assert parse_otg_playlists(data) == []

    def test_reversed_magic(self):
        data = b"ophm"
        data += struct.pack("<III", 0x14, 4, 2)
        data += b"\x00" * 4  # pad header to 0x14
        data += struct.pack("<II", 5, 10)
        playlists = parse_otg_playlists(data)
        assert len(playlists) == 1
        assert playlists[0] == [5, 10]

    def test_zero_entries(self):
        data = self._build_otg([])
        assert parse_otg_playlists(data) == []

    def test_truncated_entries(self):
        data = self._build_otg([1, 2])
        # Overwrite count to 100 (more than data)
        data = bytearray(data)
        struct.pack_into("<I", data, 12, 100)
        playlists = parse_otg_playlists(bytes(data))
        assert len(playlists) == 1
        assert len(playlists[0]) == 2

    def test_large_entry_size(self):
        data = self._build_otg([42], entry_len=16)
        playlists = parse_otg_playlists(data)
        assert playlists[0] == [42]

    def test_invalid_header_length(self):
        data = b"mhpo"
        data += struct.pack("<III", 0x10, 4, 1)  # header too small (< 0x14)
        data += struct.pack("<I", 0)
        assert parse_otg_playlists(data) == []


# =========================================================================
# Apply play counts
# =========================================================================


class TestApplyPlayCounts:
    def _make_track(self, play_count=0, rating=0, skip_count=0):
        from pygpod.db.parser import Record
        from pygpod.model.track import Track

        rec = Record(b"mhit", 100, 0)
        rec.raw_header = bytearray(100)
        rec.fields = {
            "track_id": 1,
            "play_count": play_count,
            "rating": rating,
            "skip_count": skip_count,
            "time_played": 0,
            "time_skipped": 0,
            "bookmark_time": 0,
        }
        return Track.from_record(rec)

    def test_apply_basic(self):
        tracks = [self._make_track(play_count=10), self._make_track()]
        entries = [
            PlayCountEntry(
                play_count=3,
                time_played=3700000000,
                rating=80,
                skip_count=0,
                time_skipped=0,
                bookmark_time=0,
            ),
            PlayCountEntry(
                play_count=1,
                time_played=0,
                rating=-1,
                skip_count=2,
                time_skipped=3700001000,
                bookmark_time=5000,
            ),
        ]
        updated = apply_play_counts(tracks, entries)
        assert updated == 2
        assert tracks[0].record.fields["play_count"] == 13  # 10 + 3
        assert tracks[0].record.fields["rating"] == 80
        assert tracks[1].record.fields["play_count"] == 1
        assert tracks[1].record.fields["skip_count"] == 2
        assert tracks[1].record.fields["bookmark_time"] == 5000

    def test_more_entries_than_tracks(self):
        tracks = [self._make_track()]
        entries = [
            PlayCountEntry(1, 0, -1, 0, 0, 0),
            PlayCountEntry(5, 0, -1, 0, 0, 0),
        ]
        updated = apply_play_counts(tracks, entries)
        assert updated == 1

    def test_no_changes(self):
        tracks = [self._make_track()]
        entries = [PlayCountEntry(0, 0, -1, 0, 0, 0)]
        updated = apply_play_counts(tracks, entries)
        assert updated == 0

    def test_empty(self):
        assert apply_play_counts([], []) == 0


# =========================================================================
# Convenience readers
# =========================================================================


class TestReadFromMountpoint:
    def test_read_play_counts_binary(self, tmp_path):
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        # Write binary Play Counts file with mhdp header
        header_size = 96
        data = b"mhdp" + struct.pack("<III", header_size, 12, 1)
        data += b"\x00" * (header_size - 16)
        data += struct.pack("<III", 2, 3700000000, 0)
        (itunes / "Play Counts").write_bytes(data)

        entries = read_play_counts(str(tmp_path))
        assert len(entries) == 1
        assert entries[0].play_count == 2

    def test_read_itunesstats(self, tmp_path):
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        (itunes / "iTunesStats").write_text("1\n5\n3700000000\n0\n0\n0\n\n")

        entries = read_play_counts(str(tmp_path))
        assert len(entries) == 1
        assert entries[0].play_count == 5

    def test_read_playcounts_plist(self, tmp_path):
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        data = plistlib.dumps({"items": [{"playcount": 7}]})
        (itunes / "PlayCounts.plist").write_bytes(data)

        entries = read_play_counts(str(tmp_path))
        assert len(entries) == 1
        assert entries[0].play_count == 7

    def test_read_plist_priority(self, tmp_path):
        """Plist takes priority over iTunesStats and binary."""
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        (itunes / "PlayCounts.plist").write_bytes(plistlib.dumps({"items": [{"playcount": 99}]}))
        (itunes / "iTunesStats").write_text("1\n1\n0\n0\n0\n0\n\n")

        entries = read_play_counts(str(tmp_path))
        assert entries[0].play_count == 99  # plist wins

    def test_read_none_found(self, tmp_path):
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        assert read_play_counts(str(tmp_path)) == []

    def test_read_otg(self, tmp_path):
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        data = b"mhpo"
        data += struct.pack("<III", 0x14, 4, 3)
        data += b"\x00" * 4
        data += struct.pack("<III", 0, 5, 10)
        (itunes / "OTGPlaylistInfo").write_bytes(data)

        playlists = read_otg_playlists(str(tmp_path))
        assert len(playlists) == 1
        assert playlists[0] == [0, 5, 10]

    def test_read_otg_not_found(self, tmp_path):
        itunes = tmp_path / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        assert read_otg_playlists(str(tmp_path)) == []
