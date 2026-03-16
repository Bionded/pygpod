"""Smart Playlist engine - rule evaluation, creation, and updating.

Ported from libgpod's itdb_playlist.c smart playlist support. Implements
the full SPL rule evaluation engine with 40+ searchable fields, 20+
comparison operators, sorting, and limit enforcement.
"""

from __future__ import annotations

import logging
import random
import struct
import time
from enum import IntEnum
from typing import TYPE_CHECKING, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .track import Track


# ============================================================================
# Smart Playlist Field IDs (what metadata field the rule matches against)
# ============================================================================
class SPLField(IntEnum):
    SONG_NAME = 0x02
    ALBUM = 0x03
    ARTIST = 0x04
    BITRATE = 0x05
    SAMPLE_RATE = 0x06
    YEAR = 0x07
    GENRE = 0x08
    KIND = 0x09
    DATE_MODIFIED = 0x0A
    TRACKNUMBER = 0x0B
    SIZE = 0x0C
    TIME = 0x0D  # Duration in ms
    COMMENT = 0x0E
    DATE_ADDED = 0x10
    COMPOSER = 0x12
    PLAYCOUNT = 0x16
    LAST_PLAYED = 0x17
    DISC_NUMBER = 0x18
    RATING = 0x19
    COMPILATION = 0x1F
    BPM = 0x23
    GROUPING = 0x27
    PLAYLIST = 0x28
    PURCHASE = 0x29
    DESCRIPTION = 0x36
    CATEGORY = 0x37
    PODCAST = 0x39
    VIDEO_KIND = 0x3C
    TVSHOW = 0x3E
    SEASON_NR = 0x3F
    SKIPCOUNT = 0x44
    LAST_SKIPPED = 0x45
    ALBUMARTIST = 0x47
    SORT_SONG_NAME = 0x4E
    SORT_ALBUM = 0x4F
    SORT_ARTIST = 0x50
    SORT_ALBUMARTIST = 0x51
    SORT_COMPOSER = 0x52
    SORT_TVSHOW = 0x53
    ALBUM_RATING = 0x5A


# ============================================================================
# Smart Playlist Actions (comparison operators)
# ============================================================================
class SPLAction(IntEnum):
    IS_INT = 0x00000001
    IS_GREATER_THAN = 0x00000010
    IS_LESS_THAN = 0x00000040
    IS_IN_THE_RANGE = 0x00000100
    IS_IN_THE_LAST = 0x00000200
    BINARY_AND = 0x00000400
    BINARY_UNKNOWN1 = 0x00000800
    IS_STRING = 0x01000001
    CONTAINS = 0x01000002
    STARTS_WITH = 0x01000004
    ENDS_WITH = 0x01000008
    IS_NOT_INT = 0x02000001
    IS_NOT_GREATER_THAN = 0x02000010
    IS_NOT_LESS_THAN = 0x02000040
    IS_NOT_IN_THE_RANGE = 0x02000100
    IS_NOT_IN_THE_LAST = 0x02000200
    NOT_BINARY_AND = 0x02000400
    BINARY_UNKNOWN2 = 0x02000800
    IS_NOT = 0x03000001
    DOES_NOT_CONTAIN = 0x03000002
    DOES_NOT_START_WITH = 0x03000004
    DOES_NOT_END_WITH = 0x03000008


# ============================================================================
# Field types - how to interpret the value
# ============================================================================
class SPLFieldType(IntEnum):
    STRING = 1
    INT = 2
    BOOLEAN = 3
    DATE = 4
    PLAYLIST = 5
    UNKNOWN = 6
    BINARY_AND = 7


# ============================================================================
# Action types - what kind of comparison
# ============================================================================
class SPLActionType(IntEnum):
    STRING = 1
    INT = 2
    DATE = 3
    RANGE_INT = 4
    RANGE_DATE = 5
    INTHELAST = 6
    PLAYLIST = 7
    NONE = 8
    INVALID = 9
    UNKNOWN = 10
    BINARY_AND = 11


# ============================================================================
# Match operator (AND/OR)
# ============================================================================
class SPLMatch(IntEnum):
    AND = 0
    OR = 1


# ============================================================================
# Limit sort order
# ============================================================================
class SPLLimitSort(IntEnum):
    RANDOM = 0x02
    SONG_NAME = 0x03
    ALBUM = 0x04
    ARTIST = 0x05
    GENRE = 0x07
    MOST_RECENTLY_ADDED = 0x10
    LEAST_RECENTLY_ADDED = 0x80000010
    MOST_OFTEN_PLAYED = 0x14
    LEAST_OFTEN_PLAYED = 0x80000014
    MOST_RECENTLY_PLAYED = 0x15
    LEAST_RECENTLY_PLAYED = 0x80000015
    HIGHEST_RATING = 0x17
    LOWEST_RATING = 0x80000017


# ============================================================================
# Limit types
# ============================================================================
class SPLLimitType(IntEnum):
    MINUTES = 0x01
    MB = 0x02
    SONGS = 0x03
    HOURS = 0x04
    GB = 0x05


# ============================================================================
# Smart Playlist Rule
# ============================================================================
class SPLRule:
    """A single smart playlist rule."""

    def __init__(
        self,
        field: int = 0,
        action: int = 0,
        fromvalue: int = 0,
        tovalue: int = 0,
        fromdate: int = 0,
        todate: int = 0,
        fromunits: int = 0,
        tounits: int = 0,
        string: str = "",
    ) -> None:
        self.field = field
        self.action = action
        self.fromvalue = fromvalue
        self.tovalue = tovalue
        self.fromdate = fromdate
        self.todate = todate
        self.fromunits = fromunits
        self.tounits = tounits
        self.string = string


# ============================================================================
# Smart Playlist Preferences
# ============================================================================
class SPLPrefs:
    """Smart playlist preferences (from MHOD type 50)."""

    def __init__(self) -> None:
        self.liveupdate: bool = True
        self.checkrules: bool = True
        self.checklimits: bool = False
        self.limittype: int = SPLLimitType.SONGS
        self.limitsort: int = SPLLimitSort.RANDOM
        self.limitvalue: int = 25
        self.matchcheckedonly: bool = False
        self.limitsort_opposite: bool = False


# ============================================================================
# Field type lookup
# ============================================================================
def get_field_type(field: int) -> SPLFieldType:
    """Determine the field type for an SPL field."""
    _STRING_FIELDS = {
        SPLField.SONG_NAME,
        SPLField.ALBUM,
        SPLField.ARTIST,
        SPLField.GENRE,
        SPLField.KIND,
        SPLField.COMMENT,
        SPLField.COMPOSER,
        SPLField.GROUPING,
        SPLField.DESCRIPTION,
        SPLField.CATEGORY,
        SPLField.TVSHOW,
        SPLField.ALBUMARTIST,
        SPLField.SORT_SONG_NAME,
        SPLField.SORT_ALBUM,
        SPLField.SORT_ARTIST,
        SPLField.SORT_ALBUMARTIST,
        SPLField.SORT_COMPOSER,
        SPLField.SORT_TVSHOW,
    }
    _INT_FIELDS = {
        SPLField.BITRATE,
        SPLField.SAMPLE_RATE,
        SPLField.YEAR,
        SPLField.TRACKNUMBER,
        SPLField.SIZE,
        SPLField.TIME,
        SPLField.PLAYCOUNT,
        SPLField.DISC_NUMBER,
        SPLField.RATING,
        SPLField.BPM,
        SPLField.SEASON_NR,
        SPLField.SKIPCOUNT,
        SPLField.PODCAST,
        SPLField.ALBUM_RATING,
    }
    _BOOLEAN_FIELDS = {SPLField.COMPILATION, SPLField.PURCHASE}
    _DATE_FIELDS = {
        SPLField.DATE_MODIFIED,
        SPLField.DATE_ADDED,
        SPLField.LAST_PLAYED,
        SPLField.LAST_SKIPPED,
    }

    if field in _STRING_FIELDS:
        return SPLFieldType.STRING
    elif field in _INT_FIELDS:
        return SPLFieldType.INT
    elif field in _BOOLEAN_FIELDS:
        return SPLFieldType.BOOLEAN
    elif field in _DATE_FIELDS:
        return SPLFieldType.DATE
    elif field == SPLField.PLAYLIST:
        return SPLFieldType.PLAYLIST
    elif field == SPLField.VIDEO_KIND:
        return SPLFieldType.BINARY_AND
    return SPLFieldType.UNKNOWN


# ============================================================================
# Track field extraction - unified mapping from SPL field to track attribute
# ============================================================================
_FIELD_ATTR_MAP = {
    # String fields → track attribute name
    SPLField.SONG_NAME: "title",
    SPLField.ALBUM: "album",
    SPLField.ARTIST: "artist",
    SPLField.GENRE: "genre",
    SPLField.KIND: "filetype_str",
    SPLField.COMMENT: "comment",
    SPLField.COMPOSER: "composer",
    SPLField.GROUPING: "grouping",
    SPLField.DESCRIPTION: "description",
    SPLField.TVSHOW: "tvshow",
    SPLField.CATEGORY: "category",
    SPLField.ALBUMARTIST: "albumartist",
    SPLField.SORT_SONG_NAME: "sort_title",
    SPLField.SORT_ALBUM: "sort_album",
    SPLField.SORT_ARTIST: "sort_artist",
    SPLField.SORT_ALBUMARTIST: "sort_albumartist",
    SPLField.SORT_COMPOSER: "sort_composer",
    SPLField.SORT_TVSHOW: "sort_tvshow",
    # Integer fields → track attribute name
    SPLField.BITRATE: "bitrate",
    SPLField.SAMPLE_RATE: "samplerate",
    SPLField.YEAR: "year",
    SPLField.TRACKNUMBER: "track_number",
    SPLField.SIZE: "file_size",
    SPLField.TIME: "duration_ms",
    SPLField.PLAYCOUNT: "play_count",
    SPLField.DISC_NUMBER: "cd_number",
    SPLField.RATING: "rating",
    SPLField.BPM: "bpm",
    SPLField.SEASON_NR: "season_number",
    SPLField.SKIPCOUNT: "skip_count",
}

# Date fields → record field name (read from record.fields, not track attrs)
_DATE_FIELD_MAP = {
    SPLField.DATE_MODIFIED: "time_modified",
    SPLField.DATE_ADDED: "time_added",
    SPLField.LAST_PLAYED: "time_played",
    SPLField.LAST_SKIPPED: "time_skipped",
}


def _get_track_string(track: "Track", field: int) -> str:
    attr = _FIELD_ATTR_MAP.get(field, "")
    return (getattr(track, attr, "") or "") if attr else ""


def _get_track_int(track: "Track", field: int) -> int:
    attr = _FIELD_ATTR_MAP.get(field, "")
    return (getattr(track, attr, 0) or 0) if attr else 0


def _get_track_date(track: "Track", field: int) -> int:
    if not track.record:
        return 0
    rec_field = _DATE_FIELD_MAP.get(field, "")
    return track.record.fields.get(rec_field, 0) if rec_field else 0


# ============================================================================
# Rule Evaluation
# ============================================================================
def eval_rule(rule: SPLRule, track: "Track") -> bool:
    """Evaluate whether a track matches a single SPL rule.

    Args:
        rule: The smart playlist rule.
        track: The track to test.

    Returns:
        True if the track matches the rule.
    """
    field_type = get_field_type(rule.field)
    action = rule.action

    if field_type == SPLFieldType.STRING:
        return _eval_string_rule(rule, track)
    elif field_type == SPLFieldType.INT:
        return _eval_int_rule(rule, track)
    elif field_type == SPLFieldType.BOOLEAN:
        val = _get_track_int(track, rule.field)
        if action in (SPLAction.IS_INT, SPLAction.IS_STRING):
            return bool(val) == bool(rule.fromvalue)
        return bool(val) != bool(rule.fromvalue)
    elif field_type == SPLFieldType.DATE:
        return _eval_date_rule(rule, track)
    elif field_type == SPLFieldType.BINARY_AND:
        val = _get_track_int(track, rule.field)
        if action == SPLAction.BINARY_AND:
            return bool(val & rule.fromvalue)
        elif action == SPLAction.NOT_BINARY_AND:
            return not bool(val & rule.fromvalue)
        return False

    return False


def _eval_string_rule(rule: SPLRule, track: "Track") -> bool:
    """Evaluate a string-type rule."""
    val = _get_track_string(track, rule.field).lower()
    test = rule.string.lower()
    action = rule.action

    if action == SPLAction.IS_STRING:
        return val == test
    elif action == SPLAction.IS_NOT:
        return val != test
    elif action == SPLAction.CONTAINS:
        return test in val
    elif action == SPLAction.DOES_NOT_CONTAIN:
        return test not in val
    elif action == SPLAction.STARTS_WITH:
        return val.startswith(test)
    elif action == SPLAction.DOES_NOT_START_WITH:
        return not val.startswith(test)
    elif action == SPLAction.ENDS_WITH:
        return val.endswith(test)
    elif action == SPLAction.DOES_NOT_END_WITH:
        return not val.endswith(test)

    return False


def _eval_int_rule(rule: SPLRule, track: "Track") -> bool:
    """Evaluate an integer-type rule."""
    val = _get_track_int(track, rule.field)
    action = rule.action

    if action == SPLAction.IS_INT:
        return val == rule.fromvalue
    elif action == SPLAction.IS_NOT_INT:
        return val != rule.fromvalue
    elif action == SPLAction.IS_GREATER_THAN:
        return val > rule.fromvalue
    elif action == SPLAction.IS_NOT_GREATER_THAN:
        return val <= rule.fromvalue
    elif action == SPLAction.IS_LESS_THAN:
        return val < rule.fromvalue
    elif action == SPLAction.IS_NOT_LESS_THAN:
        return val >= rule.fromvalue
    elif action == SPLAction.IS_IN_THE_RANGE:
        return rule.fromvalue <= val <= rule.tovalue
    elif action == SPLAction.IS_NOT_IN_THE_RANGE:
        return not (rule.fromvalue <= val <= rule.tovalue)

    return False


def _eval_date_rule(rule: SPLRule, track: "Track") -> bool:
    """Evaluate a date-type rule."""
    from ..utils.datetime import MAC_EPOCH_OFFSET

    val = _get_track_date(track, rule.field)
    action = rule.action
    now_mac = int(time.time()) + MAC_EPOCH_OFFSET

    if action == SPLAction.IS_IN_THE_LAST:
        # fromvalue is the duration in seconds
        threshold = now_mac - abs(rule.fromvalue)
        return val >= threshold
    elif action == SPLAction.IS_NOT_IN_THE_LAST:
        threshold = now_mac - abs(rule.fromvalue)
        return val < threshold or val == 0
    elif action == SPLAction.IS_INT:
        return val == rule.fromvalue
    elif action == SPLAction.IS_NOT_INT:
        return val != rule.fromvalue
    elif action == SPLAction.IS_GREATER_THAN:
        return val > rule.fromvalue
    elif action == SPLAction.IS_LESS_THAN:
        return val < rule.fromvalue
    elif action == SPLAction.IS_IN_THE_RANGE:
        return rule.fromdate <= val <= rule.todate
    elif action == SPLAction.IS_NOT_IN_THE_RANGE:
        return not (rule.fromdate <= val <= rule.todate)

    return False


# ============================================================================
# Smart Playlist Evaluation
# ============================================================================
def evaluate_smart_playlist(
    rules: List[SPLRule], match: SPLMatch, tracks: List["Track"]
) -> List["Track"]:
    """Evaluate all rules against all tracks and return matching ones.

    Args:
        rules: List of SPL rules.
        match: SPLMatch.AND or SPLMatch.OR.
        tracks: List of tracks to evaluate.

    Returns:
        List of tracks that match the rules.
    """
    logger.debug("Evaluating %d rules against %d tracks", len(rules), len(tracks))
    result = []
    for track in tracks:
        if match == SPLMatch.AND:
            if all(eval_rule(r, track) for r in rules):
                result.append(track)
        else:  # OR
            if any(eval_rule(r, track) for r in rules):
                result.append(track)
    return result


def apply_limit(tracks: List["Track"], prefs: SPLPrefs) -> List["Track"]:
    """Apply limit and sort to matched tracks.

    Args:
        tracks: Matched tracks.
        prefs: Smart playlist preferences.

    Returns:
        Limited and sorted track list.
    """
    if not prefs.checklimits:
        return tracks

    # Sort first
    sorted_tracks = _sort_tracks(tracks, prefs.limitsort)

    # Apply limit
    limit_type = prefs.limittype
    limit_value = prefs.limitvalue

    if limit_type == SPLLimitType.SONGS:
        return sorted_tracks[:limit_value]

    result = []
    accumulated = 0
    for track in sorted_tracks:
        if limit_type == SPLLimitType.MINUTES:
            accumulated += track.duration_ms / 60000.0
        elif limit_type == SPLLimitType.HOURS:
            accumulated += track.duration_ms / 3600000.0
        elif limit_type == SPLLimitType.MB:
            accumulated += track.file_size / (1024 * 1024)
        elif limit_type == SPLLimitType.GB:
            accumulated += track.file_size / (1024 * 1024 * 1024)

        if accumulated > limit_value:
            break
        result.append(track)

    return result


def _sort_tracks(tracks: List["Track"], sort_order: int) -> List["Track"]:
    """Sort tracks by the given sort order."""
    result = list(tracks)

    if sort_order == SPLLimitSort.RANDOM:
        random.shuffle(result)
        return result

    reverse = bool(sort_order & 0x80000000)
    base = sort_order & 0x7FFFFFFF

    key_funcs: Dict[int, Callable] = {
        SPLLimitSort.SONG_NAME: lambda t: (t.title or "").lower(),
        SPLLimitSort.ALBUM: lambda t: (t.album or "").lower(),
        SPLLimitSort.ARTIST: lambda t: (t.artist or "").lower(),
        SPLLimitSort.GENRE: lambda t: (t.genre or "").lower(),
        0x10: lambda t: t.record.fields.get("time_added", 0) if t.record else 0,
        0x14: lambda t: t.play_count,
        0x15: lambda t: t.record.fields.get("time_played", 0) if t.record else 0,
        0x17: lambda t: t.rating,
    }

    key_fn = key_funcs.get(base)
    if key_fn:
        result.sort(key=key_fn, reverse=not reverse)
    return result


# ============================================================================
# SPL Rule Binary Parsing (MHOD type 51 body, big-endian!)
# ============================================================================
SPL_RULE_SIZE = 136  # Each rule is 136 bytes


def parse_spl_rules(data: bytes) -> Tuple[SPLMatch, List[SPLRule]]:
    """Parse SPL rules from MHOD type 51 raw body.

    Matches libgpod format: variable-length rules, big-endian throughout.

    Layout:
    - 136 bytes: header (SLst magic + unk + numrules + match_op + 120 zeros)
    - For each rule:
      - 52 bytes: field(4) + action(4) + 44 zeros
      - String rule: string_len(4) + utf16be string data
      - Non-string rule: data_len(4=0x44) + 6x int64 values + 5x int32 unknowns

    Args:
        data: Raw bytes of the MHOD type 51 body.

    Returns:
        Tuple of (match_operator, list_of_rules).
    """
    if len(data) < 16:
        return SPLMatch.AND, []

    magic = data[0:4]
    if magic != b"SLst":
        return SPLMatch.AND, []

    num_rules = struct.unpack_from(">I", data, 8)[0]
    match_op = struct.unpack_from(">I", data, 12)[0]
    match = SPLMatch(match_op) if match_op in (0, 1) else SPLMatch.AND

    rules = []
    offset = 136  # Rules start after the 136-byte header

    for i in range(num_rules):
        if offset + 52 > len(data):
            break

        field = struct.unpack_from(">I", data, offset)[0]
        action = struct.unpack_from(">I", data, offset + 4)[0]
        offset += 52  # field(4) + action(4) + 44 zeros

        ft = get_field_type(field)
        string = ""
        fromvalue = 0
        tovalue = 0
        fromdate = 0
        todate = 0
        fromunits = 0
        tounits = 0

        if ft == SPLFieldType.STRING:
            if offset + 4 > len(data):
                break
            str_len = struct.unpack_from(">I", data, offset)[0]
            offset += 4
            if str_len > 0 and offset + str_len <= len(data):
                try:
                    string = data[offset : offset + str_len].decode("utf-16-be")
                except (UnicodeDecodeError, ValueError):
                    logger.debug(
                        "Failed to decode SPL rule string at offset %d", offset, exc_info=True
                    )
                offset += str_len
        else:
            if offset + 4 > len(data):
                break
            data_len = struct.unpack_from(">I", data, offset)[0]
            offset += 4
            if offset + data_len <= len(data) and data_len >= 48:
                fromvalue = struct.unpack_from(">q", data, offset)[0]
                fromdate = struct.unpack_from(">q", data, offset + 8)[0]
                fromunits = struct.unpack_from(">Q", data, offset + 16)[0]
                tovalue = struct.unpack_from(">q", data, offset + 24)[0]
                todate = struct.unpack_from(">q", data, offset + 32)[0]
                tounits = struct.unpack_from(">Q", data, offset + 40)[0]
            offset += data_len

        rule = SPLRule(
            field=field,
            action=action,
            fromvalue=fromvalue,
            tovalue=tovalue,
            fromdate=fromdate,
            todate=todate,
            fromunits=fromunits,
            tounits=tounits,
            string=string,
        )
        rules.append(rule)

    return match, rules


def write_spl_rules(match: SPLMatch, rules: List[SPLRule], version: int = 1) -> bytes:
    """Write SPL rules to binary format for MHOD type 51.

    Matches libgpod itdb_itunesdb.c format exactly. All values are big-endian.

    Args:
        match: Match operator (AND/OR).
        rules: List of rules.
        version: Unused, kept for API compat.

    Returns:
        Raw bytes for MHOD type 51 body.
    """
    data = bytearray()

    # SLst header: magic(4) + unk(4) + numrules(4) + match_op(4) + 120 zeros
    data += struct.pack(">4sIII", b"SLst", 0, len(rules), int(match))
    data += b"\x00" * 120  # 30 x 4-byte zeros

    # Rules
    for rule in rules:
        ft = get_field_type(rule.field)

        # Rule header: field(4) + action(4) + 44 zeros
        data += struct.pack(">II", rule.field, rule.action)
        data += b"\x00" * 44  # 11 x 4-byte zeros

        if ft == SPLFieldType.STRING:
            # String rule: string_len(4) + utf16be string data
            encoded = rule.string.encode("utf-16-be") if rule.string else b""
            data += struct.pack(">I", len(encoded))
            data += encoded
        else:
            # Non-string rule: data_len(4) + 6x uint64 values + 5x uint32 unknowns
            # data_len is always 0x44 (68 bytes)
            data += struct.pack(">I", 0x44)
            data += struct.pack(">q", rule.fromvalue)  # fromvalue (int64)
            data += struct.pack(">q", rule.fromdate)  # fromdate (int64)
            data += struct.pack(">Q", rule.fromunits)  # fromunits (uint64)
            data += struct.pack(">q", rule.tovalue)  # tovalue (int64)
            data += struct.pack(">q", rule.todate)  # todate (int64)
            data += struct.pack(">Q", rule.tounits)  # tounits (uint64)
            data += b"\x00" * 20  # 5 x 4-byte unknown fields

    return bytes(data)


def parse_spl_prefs(data: bytes) -> SPLPrefs:
    """Parse SPL preferences from MHOD type 50 body.

    Args:
        data: Raw bytes of the MHOD type 50 body.

    Returns:
        SPLPrefs object.
    """
    prefs = SPLPrefs()
    if len(data) < 14:
        return prefs

    prefs.liveupdate = bool(data[0])
    prefs.checkrules = bool(data[1])
    prefs.checklimits = bool(data[2])
    prefs.limittype = data[3]
    prefs.limitsort = data[4]
    prefs.limitvalue = struct.unpack_from("<I", data, 8)[0]
    prefs.matchcheckedonly = bool(data[12])
    if len(data) > 13:
        prefs.limitsort_opposite = bool(data[13])

    return prefs


def write_spl_prefs(prefs: SPLPrefs, padded_size: int = 14) -> bytes:
    """Write SPL preferences to binary format for MHOD type 50.

    Args:
        prefs: SPLPrefs object.
        padded_size: Total body size (14 = pygpod default, 96 = libgpod compat).

    Returns:
        Raw bytes for MHOD type 50 body.
    """
    size = max(padded_size, 14)
    data = bytearray(size)
    data[0] = int(prefs.liveupdate)
    data[1] = int(prefs.checkrules)
    data[2] = int(prefs.checklimits)
    data[3] = prefs.limittype
    data[4] = prefs.limitsort & 0xFF
    struct.pack_into("<I", data, 8, prefs.limitvalue)
    data[12] = int(prefs.matchcheckedonly)
    data[13] = int(prefs.limitsort_opposite)
    return bytes(data)
