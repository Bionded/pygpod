"""Extended tests for smartplaylist.py — date rules, range rules, binary_and,
limit types, sort orders, parse/write edge cases."""

import struct
import time

import pytest

from pygpod.db.parser import Record
from pygpod.db.writer import make_string_mhod
from pygpod.model.smartplaylist import (
    SPLAction,
    SPLField,
    SPLFieldType,
    SPLLimitSort,
    SPLLimitType,
    SPLMatch,
    SPLPrefs,
    SPLRule,
    apply_limit,
    eval_rule,
    evaluate_smart_playlist,
    get_field_type,
    parse_spl_prefs,
    parse_spl_rules,
    write_spl_prefs,
    write_spl_rules,
)
from pygpod.model.track import Track
from pygpod.utils.datetime import MAC_EPOCH_OFFSET


def _make_track(**kwargs):
    """Create a Track with given field values."""
    hdr_len = 0x248
    rec = Record(b"mhit", hdr_len, 0)
    rec.raw_header = bytearray(hdr_len)
    rec.raw_header[0:4] = b"mhit"
    struct.pack_into("<I", rec.raw_header, 4, hdr_len)
    defaults = {
        "track_id": 1, "dbid": 1, "media_type": 1,
        "play_count": 0, "rating": 0, "year": 0,
        "bitrate": 128, "tracklen": 180000, "file_size": 5000000,
        "track_number": 1, "samplerate": 44100 << 16,
        "time_added": 0, "time_played": 0, "time_modified": 0,
    }
    defaults.update(kwargs)
    rec.fields = defaults
    if "title" in kwargs:
        rec.children.append(make_string_mhod(1, kwargs["title"]))
    if "artist" in kwargs:
        rec.children.append(make_string_mhod(4, kwargs["artist"]))
    if "album" in kwargs:
        rec.children.append(make_string_mhod(3, kwargs["album"]))
    if "genre" in kwargs:
        rec.children.append(make_string_mhod(5, kwargs["genre"]))
    if "composer" in kwargs:
        rec.children.append(make_string_mhod(12, kwargs["composer"]))
    return Track.from_record(rec)


# =========================================================================
# get_field_type — cover UNKNOWN and BINARY_AND
# =========================================================================


class TestFieldType:
    def test_string_field(self):
        assert get_field_type(SPLField.SONG_NAME) == SPLFieldType.STRING

    def test_int_field(self):
        assert get_field_type(SPLField.BITRATE) == SPLFieldType.INT

    def test_boolean_field(self):
        assert get_field_type(SPLField.COMPILATION) == SPLFieldType.BOOLEAN

    def test_date_field(self):
        assert get_field_type(SPLField.DATE_ADDED) == SPLFieldType.DATE

    def test_playlist_field(self):
        assert get_field_type(SPLField.PLAYLIST) == SPLFieldType.PLAYLIST

    def test_video_kind(self):
        assert get_field_type(SPLField.VIDEO_KIND) == SPLFieldType.BINARY_AND

    def test_unknown_field(self):
        assert get_field_type(0xFFFF) == SPLFieldType.UNKNOWN


# =========================================================================
# Boolean rules
# =========================================================================


class TestBooleanRules:
    def test_compilation_is_zero(self):
        # COMPILATION not in _FIELD_ATTR_MAP, so _get_track_int returns 0
        track = _make_track(compilation=0)
        rule = SPLRule(field=SPLField.COMPILATION, action=SPLAction.IS_INT, fromvalue=0)
        assert eval_rule(rule, track) is True

    def test_compilation_is_not_match(self):
        track = _make_track(compilation=0)
        rule = SPLRule(field=SPLField.COMPILATION, action=SPLAction.IS_NOT_INT, fromvalue=1)
        assert eval_rule(rule, track) is True


# =========================================================================
# Date rules
# =========================================================================


class TestDateRules:
    def _now_mac(self):
        return int(time.time()) + MAC_EPOCH_OFFSET

    def test_is_in_the_last(self):
        now = self._now_mac()
        track = _make_track(time_added=now - 3600)  # 1 hour ago
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_IN_THE_LAST,
            fromvalue=7200,  # last 2 hours
        )
        assert eval_rule(rule, track) is True

    def test_is_not_in_the_last(self):
        now = self._now_mac()
        track = _make_track(time_added=now - 86400)  # 24h ago
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_NOT_IN_THE_LAST,
            fromvalue=3600,  # last 1 hour
        )
        assert eval_rule(rule, track) is True

    def test_is_not_in_the_last_zero_timestamp(self):
        track = _make_track(time_added=0)
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_NOT_IN_THE_LAST,
            fromvalue=3600,
        )
        assert eval_rule(rule, track) is True  # 0 counts as "not in last"

    def test_date_is_equal(self):
        track = _make_track(time_added=3700000000)
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_INT,
            fromvalue=3700000000,
        )
        assert eval_rule(rule, track) is True

    def test_date_is_not_equal(self):
        track = _make_track(time_added=3700000000)
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_NOT_INT,
            fromvalue=3700000000,
        )
        assert eval_rule(rule, track) is False

    def test_date_greater_than(self):
        track = _make_track(time_added=3700000000)
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_GREATER_THAN,
            fromvalue=3600000000,
        )
        assert eval_rule(rule, track) is True

    def test_date_less_than(self):
        track = _make_track(time_added=3600000000)
        rule = SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_LESS_THAN,
            fromvalue=3700000000,
        )
        assert eval_rule(rule, track) is True

    def test_date_in_range(self):
        track = _make_track(time_played=3650000000)
        rule = SPLRule(
            field=SPLField.LAST_PLAYED,
            action=SPLAction.IS_IN_THE_RANGE,
            fromdate=3600000000,
            todate=3700000000,
        )
        assert eval_rule(rule, track) is True

    def test_date_not_in_range(self):
        track = _make_track(time_played=3800000000)
        rule = SPLRule(
            field=SPLField.LAST_PLAYED,
            action=SPLAction.IS_NOT_IN_THE_RANGE,
            fromdate=3600000000,
            todate=3700000000,
        )
        assert eval_rule(rule, track) is True

    def test_date_unknown_action(self):
        track = _make_track(time_added=3700000000)
        rule = SPLRule(field=SPLField.DATE_ADDED, action=0xFFFFFFFF, fromvalue=0)
        assert eval_rule(rule, track) is False

    def test_date_no_record(self):
        track = Track()
        rule = SPLRule(field=SPLField.DATE_ADDED, action=SPLAction.IS_INT, fromvalue=0)
        # val=0 (no record) == fromvalue=0 → True
        assert eval_rule(rule, track) is True


# =========================================================================
# Binary AND rules (VIDEO_KIND)
# =========================================================================


class TestBinaryAndRules:
    def test_binary_and_zero(self):
        # VIDEO_KIND not in attr map, so val=0; 0 & anything = 0 → False
        track = _make_track()
        rule = SPLRule(field=SPLField.VIDEO_KIND, action=SPLAction.BINARY_AND, fromvalue=0x0002)
        assert eval_rule(rule, track) is False

    def test_not_binary_and_zero(self):
        # val=0, not(0 & 2) = not 0 = True
        track = _make_track()
        rule = SPLRule(field=SPLField.VIDEO_KIND, action=SPLAction.NOT_BINARY_AND, fromvalue=0x0002)
        assert eval_rule(rule, track) is True

    def test_unknown_binary_action(self):
        track = _make_track()
        rule = SPLRule(field=SPLField.VIDEO_KIND, action=0xFFFF, fromvalue=0x0002)
        assert eval_rule(rule, track) is False


# =========================================================================
# Int range rules
# =========================================================================


class TestIntRangeRules:
    def test_in_range(self):
        track = _make_track(year=2020)
        rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_IN_THE_RANGE,
                       fromvalue=2018, tovalue=2022)
        assert eval_rule(rule, track) is True

    def test_not_in_range(self):
        track = _make_track(year=2015)
        rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_NOT_IN_THE_RANGE,
                       fromvalue=2018, tovalue=2022)
        assert eval_rule(rule, track) is True

    def test_in_range_boundary(self):
        track = _make_track(year=2018)
        rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_IN_THE_RANGE,
                       fromvalue=2018, tovalue=2022)
        assert eval_rule(rule, track) is True

    def test_unknown_int_action(self):
        track = _make_track(year=2020)
        rule = SPLRule(field=SPLField.YEAR, action=0xFFFFFFFF, fromvalue=0)
        assert eval_rule(rule, track) is False


# =========================================================================
# String rule — unknown action falls through
# =========================================================================


class TestStringEdges:
    def test_unknown_string_action(self):
        track = _make_track(title="Hello")
        rule = SPLRule(field=SPLField.SONG_NAME, action=0xFFFFFFFF, string="Hello")
        assert eval_rule(rule, track) is False


# =========================================================================
# Unknown field type falls through
# =========================================================================


class TestUnknownField:
    def test_unknown_field_returns_false(self):
        track = _make_track()
        rule = SPLRule(field=0xFFFF, action=SPLAction.IS_INT, fromvalue=0)
        assert eval_rule(rule, track) is False


# =========================================================================
# Limit types (minutes, hours, MB, GB)
# =========================================================================


class TestApplyLimit:
    def _tracks(self, n=5):
        return [_make_track(
            track_id=i, title=f"T{i}", tracklen=180000, file_size=5_000_000
        ) for i in range(n)]

    def test_limit_songs(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.SONGS
        prefs.limitvalue = 3
        prefs.limitsort = SPLLimitSort.SONG_NAME
        result = apply_limit(self._tracks(), prefs)
        assert len(result) == 3

    def test_limit_minutes(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.MINUTES
        prefs.limitvalue = 5  # 5 minutes, each track is 3 min
        prefs.limitsort = SPLLimitSort.SONG_NAME
        result = apply_limit(self._tracks(), prefs)
        assert 1 <= len(result) <= 2  # ~3 min each

    def test_limit_hours(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.HOURS
        prefs.limitvalue = 1  # 1 hour, 5 tracks * 3 min = 15 min
        prefs.limitsort = SPLLimitSort.SONG_NAME
        result = apply_limit(self._tracks(), prefs)
        assert len(result) == 5  # all fit in 1 hour

    def test_limit_mb(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.MB
        prefs.limitvalue = 10  # 10 MB, each track ~5 MB
        prefs.limitsort = SPLLimitSort.SONG_NAME
        result = apply_limit(self._tracks(), prefs)
        assert len(result) == 2

    def test_limit_gb(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.GB
        prefs.limitvalue = 1  # 1 GB, all tracks ~25 MB total
        prefs.limitsort = SPLLimitSort.SONG_NAME
        result = apply_limit(self._tracks(), prefs)
        assert len(result) == 5

    def test_no_limit(self):
        prefs = SPLPrefs()
        prefs.checklimits = False
        result = apply_limit(self._tracks(), prefs)
        assert len(result) == 5

    def test_sort_by_rating(self):
        tracks = [
            _make_track(track_id=1, title="A", rating=20),
            _make_track(track_id=2, title="B", rating=80),
            _make_track(track_id=3, title="C", rating=60),
        ]
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.SONGS
        prefs.limitvalue = 2
        prefs.limitsort = 0x17  # rating sort
        result = apply_limit(tracks, prefs)
        assert len(result) == 2


# =========================================================================
# Parse/write round-trip
# =========================================================================


class TestSPLRoundTrip:
    def test_write_parse_string_rule(self):
        rules = [SPLRule(field=SPLField.GENRE, action=SPLAction.CONTAINS, string="Rock")]
        data = write_spl_rules(SPLMatch.AND, rules)
        match, parsed = parse_spl_rules(data)
        assert match == SPLMatch.AND
        assert len(parsed) == 1
        assert parsed[0].field == SPLField.GENRE
        assert parsed[0].action == SPLAction.CONTAINS
        assert parsed[0].string == "Rock"

    def test_write_parse_int_rule(self):
        rules = [SPLRule(field=SPLField.PLAYCOUNT, action=SPLAction.IS_GREATER_THAN, fromvalue=10)]
        data = write_spl_rules(SPLMatch.OR, rules)
        match, parsed = parse_spl_rules(data)
        assert match == SPLMatch.OR
        assert parsed[0].fromvalue == 10

    def test_write_parse_date_rule(self):
        rules = [SPLRule(
            field=SPLField.DATE_ADDED,
            action=SPLAction.IS_IN_THE_RANGE,
            fromdate=3600000000,
            todate=3700000000,
        )]
        data = write_spl_rules(SPLMatch.AND, rules)
        match, parsed = parse_spl_rules(data)
        assert parsed[0].fromdate == 3600000000
        assert parsed[0].todate == 3700000000

    def test_write_parse_multiple_rules(self):
        rules = [
            SPLRule(field=SPLField.ARTIST, action=SPLAction.CONTAINS, string="Beatles"),
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_GREATER_THAN, fromvalue=1965),
            SPLRule(field=SPLField.RATING, action=SPLAction.IS_IN_THE_RANGE, fromvalue=60, tovalue=100),
        ]
        data = write_spl_rules(SPLMatch.AND, rules)
        match, parsed = parse_spl_rules(data)
        assert len(parsed) == 3
        assert parsed[0].string == "Beatles"
        assert parsed[1].fromvalue == 1965
        assert parsed[2].fromvalue == 60
        assert parsed[2].tovalue == 100

    def test_write_parse_unicode_string(self):
        rules = [SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Скрябін")]
        data = write_spl_rules(SPLMatch.AND, rules)
        _, parsed = parse_spl_rules(data)
        assert parsed[0].string == "Скрябін"

    def test_parse_empty_data(self):
        match, rules = parse_spl_rules(b"")
        assert match == SPLMatch.AND
        assert rules == []

    def test_parse_bad_magic(self):
        match, rules = parse_spl_rules(b"XXXX" + b"\x00" * 132)
        assert rules == []

    def test_parse_truncated_rule(self):
        # Valid header but truncated rule data
        data = struct.pack(">4sIII", b"SLst", 0, 1, 0)
        data += b"\x00" * 120  # header padding
        data += b"\x00" * 20  # partial rule (< 52 bytes)
        match, rules = parse_spl_rules(data)
        assert len(rules) == 0

    def test_prefs_round_trip(self):
        prefs = SPLPrefs()
        prefs.liveupdate = True
        prefs.checkrules = True
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.SONGS
        prefs.limitsort = SPLLimitSort.MOST_OFTEN_PLAYED
        prefs.limitvalue = 500

        data = write_spl_prefs(prefs)
        parsed = parse_spl_prefs(data)
        assert parsed.liveupdate is True
        assert parsed.checkrules is True
        assert parsed.checklimits is True
        assert parsed.limittype == SPLLimitType.SONGS
        assert parsed.limitsort == SPLLimitSort.MOST_OFTEN_PLAYED
        assert parsed.limitvalue == 500

    def test_prefs_parse_short_data(self):
        # Too-short data returns default SPLPrefs (liveupdate=True by default)
        prefs = parse_spl_prefs(b"\x00" * 5)
        assert isinstance(prefs, SPLPrefs)
