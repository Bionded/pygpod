"""Comprehensive smart playlist tests - rule evaluation, limits, binary format."""

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


# ============================================================================
# Mock track for testing rule evaluation
# ============================================================================
class MockRecord:
    def __init__(self, fields=None):
        self.fields = fields or {}


class MockTrack:
    def __init__(self, **kwargs):
        self.title = kwargs.get("title", "")
        self.artist = kwargs.get("artist", "")
        self.album = kwargs.get("album", "")
        self.genre = kwargs.get("genre", "")
        self.comment = kwargs.get("comment", "")
        self.composer = kwargs.get("composer", "")
        self.grouping = kwargs.get("grouping", "")
        self.albumartist = kwargs.get("albumartist", "")
        self.filetype_str = kwargs.get("filetype_str", "")
        self.description = kwargs.get("description", "")
        self.tvshow = kwargs.get("tvshow", "")
        self.category = kwargs.get("category", "")
        self.sort_title = kwargs.get("sort_title", "")
        self.sort_album = kwargs.get("sort_album", "")
        self.sort_artist = kwargs.get("sort_artist", "")
        self.sort_albumartist = kwargs.get("sort_albumartist", "")
        self.sort_composer = kwargs.get("sort_composer", "")
        self.sort_tvshow = kwargs.get("sort_tvshow", "")
        self.bitrate = kwargs.get("bitrate", 0)
        self.samplerate = kwargs.get("samplerate", 0)
        self.year = kwargs.get("year", 0)
        self.track_number = kwargs.get("track_number", 0)
        self.file_size = kwargs.get("file_size", 0)
        self.duration_ms = kwargs.get("duration_ms", 0)
        self.play_count = kwargs.get("play_count", 0)
        self.cd_number = kwargs.get("cd_number", 0)
        self.rating = kwargs.get("rating", 0)
        self.bpm = kwargs.get("bpm", 0)
        self.season_number = kwargs.get("season_number", 0)
        self.skip_count = kwargs.get("skip_count", 0)
        self.is_podcast = kwargs.get("is_podcast", False)
        self.is_audiobook = kwargs.get("is_audiobook", False)
        self.record = MockRecord(kwargs.get("record_fields", {}))


# ============================================================================
# Field type lookup
# ============================================================================
class TestFieldTypes:
    def test_string_fields(self):
        for f in [
            SPLField.SONG_NAME,
            SPLField.ALBUM,
            SPLField.ARTIST,
            SPLField.GENRE,
            SPLField.COMMENT,
            SPLField.COMPOSER,
        ]:
            assert get_field_type(f) == SPLFieldType.STRING

    def test_int_fields(self):
        for f in [
            SPLField.BITRATE,
            SPLField.YEAR,
            SPLField.RATING,
            SPLField.PLAYCOUNT,
            SPLField.SIZE,
            SPLField.TIME,
        ]:
            assert get_field_type(f) == SPLFieldType.INT

    def test_date_fields(self):
        for f in [
            SPLField.DATE_MODIFIED,
            SPLField.DATE_ADDED,
            SPLField.LAST_PLAYED,
            SPLField.LAST_SKIPPED,
        ]:
            assert get_field_type(f) == SPLFieldType.DATE

    def test_boolean_fields(self):
        assert get_field_type(SPLField.COMPILATION) == SPLFieldType.BOOLEAN
        assert get_field_type(SPLField.PURCHASE) == SPLFieldType.BOOLEAN

    def test_playlist_field(self):
        assert get_field_type(SPLField.PLAYLIST) == SPLFieldType.PLAYLIST

    def test_binary_and_field(self):
        assert get_field_type(SPLField.VIDEO_KIND) == SPLFieldType.BINARY_AND


# ============================================================================
# String rule evaluation
# ============================================================================
class TestStringRules:
    def test_is_string(self):
        track = MockTrack(artist="Beatles")
        rule = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles")
        assert eval_rule(rule, track)

    def test_is_string_case_insensitive(self):
        track = MockTrack(artist="BEATLES")
        rule = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="beatles")
        assert eval_rule(rule, track)

    def test_is_not(self):
        track = MockTrack(artist="Beatles")
        rule = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_NOT, string="Stones")
        assert eval_rule(rule, track)

    def test_contains(self):
        track = MockTrack(title="Hello World")
        rule = SPLRule(field=SPLField.SONG_NAME, action=SPLAction.CONTAINS, string="World")
        assert eval_rule(rule, track)

    def test_does_not_contain(self):
        track = MockTrack(title="Hello World")
        rule = SPLRule(field=SPLField.SONG_NAME, action=SPLAction.DOES_NOT_CONTAIN, string="Foo")
        assert eval_rule(rule, track)

    def test_starts_with(self):
        track = MockTrack(album="Abbey Road")
        rule = SPLRule(field=SPLField.ALBUM, action=SPLAction.STARTS_WITH, string="Abbey")
        assert eval_rule(rule, track)

    def test_does_not_start_with(self):
        track = MockTrack(album="Abbey Road")
        rule = SPLRule(field=SPLField.ALBUM, action=SPLAction.DOES_NOT_START_WITH, string="Let")
        assert eval_rule(rule, track)

    def test_ends_with(self):
        track = MockTrack(genre="Classic Rock")
        rule = SPLRule(field=SPLField.GENRE, action=SPLAction.ENDS_WITH, string="rock")
        assert eval_rule(rule, track)

    def test_does_not_end_with(self):
        track = MockTrack(genre="Pop")
        rule = SPLRule(field=SPLField.GENRE, action=SPLAction.DOES_NOT_END_WITH, string="rock")
        assert eval_rule(rule, track)


# ============================================================================
# Integer rule evaluation
# ============================================================================
class TestIntRules:
    def test_is_int(self):
        track = MockTrack(year=2020)
        rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_INT, fromvalue=2020)
        assert eval_rule(rule, track)

    def test_is_not_int(self):
        track = MockTrack(year=2020)
        rule = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_NOT_INT, fromvalue=2019)
        assert eval_rule(rule, track)

    def test_greater_than(self):
        track = MockTrack(rating=80)
        rule = SPLRule(field=SPLField.RATING, action=SPLAction.IS_GREATER_THAN, fromvalue=60)
        assert eval_rule(rule, track)

    def test_not_greater_than(self):
        track = MockTrack(rating=50)
        rule = SPLRule(field=SPLField.RATING, action=SPLAction.IS_NOT_GREATER_THAN, fromvalue=60)
        assert eval_rule(rule, track)

    def test_less_than(self):
        track = MockTrack(bitrate=128)
        rule = SPLRule(field=SPLField.BITRATE, action=SPLAction.IS_LESS_THAN, fromvalue=256)
        assert eval_rule(rule, track)

    def test_not_less_than(self):
        track = MockTrack(bitrate=320)
        rule = SPLRule(field=SPLField.BITRATE, action=SPLAction.IS_NOT_LESS_THAN, fromvalue=256)
        assert eval_rule(rule, track)

    def test_in_range(self):
        track = MockTrack(year=2015)
        rule = SPLRule(
            field=SPLField.YEAR, action=SPLAction.IS_IN_THE_RANGE, fromvalue=2010, tovalue=2020
        )
        assert eval_rule(rule, track)

    def test_not_in_range(self):
        track = MockTrack(year=2005)
        rule = SPLRule(
            field=SPLField.YEAR, action=SPLAction.IS_NOT_IN_THE_RANGE, fromvalue=2010, tovalue=2020
        )
        assert eval_rule(rule, track)


# ============================================================================
# Boolean rule evaluation
# ============================================================================
class TestBooleanRules:
    def test_is_compilation(self):
        track = MockTrack(rating=1)  # compilation is mapped to rating?
        # Actually, compilation maps to a record field
        # Use the generic approach
        rule = SPLRule(field=SPLField.COMPILATION, action=SPLAction.IS_INT, fromvalue=0)
        # Track has no compilation field, defaults to 0
        assert eval_rule(rule, track)


# ============================================================================
# Playlist evaluation (AND/OR)
# ============================================================================
class TestPlaylistEval:
    def test_and_match(self):
        track = MockTrack(artist="Beatles", year=1969)
        rules = [
            SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles"),
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_INT, fromvalue=1969),
        ]
        result = evaluate_smart_playlist(rules, SPLMatch.AND, [track])
        assert len(result) == 1

    def test_and_no_match(self):
        track = MockTrack(artist="Beatles", year=1970)
        rules = [
            SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles"),
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_INT, fromvalue=1969),
        ]
        result = evaluate_smart_playlist(rules, SPLMatch.AND, [track])
        assert len(result) == 0

    def test_or_match(self):
        track = MockTrack(artist="Stones", year=1969)
        rules = [
            SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles"),
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_INT, fromvalue=1969),
        ]
        result = evaluate_smart_playlist(rules, SPLMatch.OR, [track])
        assert len(result) == 1

    def test_or_no_match(self):
        track = MockTrack(artist="Stones", year=1970)
        rules = [
            SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles"),
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_INT, fromvalue=1969),
        ]
        result = evaluate_smart_playlist(rules, SPLMatch.OR, [track])
        assert len(result) == 0

    def test_multiple_tracks(self):
        tracks = [
            MockTrack(artist="Beatles", year=1969),
            MockTrack(artist="Stones", year=1969),
            MockTrack(artist="Beatles", year=1970),
        ]
        rules = [SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles")]
        result = evaluate_smart_playlist(rules, SPLMatch.AND, tracks)
        assert len(result) == 2

    def test_empty_rules(self):
        tracks = [MockTrack(artist="Beatles")]
        result = evaluate_smart_playlist([], SPLMatch.AND, tracks)
        # No rules → AND matches all (vacuous truth)
        assert len(result) == 1


# ============================================================================
# Limits
# ============================================================================
class TestLimits:
    def _make_tracks(self, n):
        return [
            MockTrack(title=f"Track {i}", duration_ms=180000, file_size=5_000_000) for i in range(n)
        ]

    def test_limit_songs(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.SONGS
        prefs.limitvalue = 5
        prefs.limitsort = SPLLimitSort.SONG_NAME
        tracks = self._make_tracks(10)
        result = apply_limit(tracks, prefs)
        assert len(result) == 5

    def test_no_limit(self):
        prefs = SPLPrefs()
        prefs.checklimits = False
        tracks = self._make_tracks(10)
        result = apply_limit(tracks, prefs)
        assert len(result) == 10

    def test_limit_minutes(self):
        prefs = SPLPrefs()
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.MINUTES
        prefs.limitvalue = 6  # 6 minutes
        prefs.limitsort = SPLLimitSort.SONG_NAME
        tracks = self._make_tracks(10)  # each 3 min
        result = apply_limit(tracks, prefs)
        assert len(result) == 2  # 2 * 3 = 6 min


# ============================================================================
# SPL binary format round-trip
# ============================================================================
class TestSPLBinaryFormat:
    def test_write_and_parse_rules(self):
        rules = [
            SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Beatles"),
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_INT, fromvalue=1969),
        ]
        data = write_spl_rules(SPLMatch.AND, rules)
        match, parsed = parse_spl_rules(data)
        assert match == SPLMatch.AND
        assert len(parsed) == 2
        assert parsed[0].field == SPLField.ARTIST
        assert parsed[0].string == "Beatles"
        assert parsed[1].field == SPLField.YEAR
        assert parsed[1].fromvalue == 1969

    def test_write_and_parse_prefs(self):
        prefs = SPLPrefs()
        prefs.liveupdate = True
        prefs.checklimits = True
        prefs.limittype = SPLLimitType.SONGS
        prefs.limitvalue = 25
        data = write_spl_prefs(prefs)
        parsed = parse_spl_prefs(data)
        assert parsed.liveupdate is True
        assert parsed.checklimits is True
        assert parsed.limittype == SPLLimitType.SONGS
        assert parsed.limitvalue == 25

    def test_empty_rules(self):
        data = write_spl_rules(SPLMatch.OR, [])
        match, rules = parse_spl_rules(data)
        assert match == SPLMatch.OR
        assert len(rules) == 0

    def test_parse_invalid_magic(self):
        data = b"XXXX" + b"\x00" * 200
        match, rules = parse_spl_rules(data)
        assert match == SPLMatch.AND
        assert len(rules) == 0

    def test_parse_short_data(self):
        match, rules = parse_spl_rules(b"\x00" * 4)
        assert len(rules) == 0

    def test_prefs_round_trip_padded(self):
        prefs = SPLPrefs()
        prefs.limitsort = 0x17  # HIGHEST_RATING
        data = write_spl_prefs(prefs, padded_size=72)
        assert len(data) == 72
        parsed = parse_spl_prefs(data)
        assert parsed.limitsort == 0x17
