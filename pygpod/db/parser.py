"""iTunesDB binary parser.

Parses the complete iTunesDB binary format including all MHXX record types.
Preserves unknown fields and padding for round-trip integrity.
"""

from __future__ import annotations

import logging
import struct
from typing import Any, Dict, List, Optional

from ..exceptions import ParseError
from ..utils.compat import get8int, get16lint, get32lint, get64lint, get_float
from ..utils.encoding import decode_mhod_string
from .constants import (
    MHBD_MAGIC,
    MHIA_MAGIC,
    MHII_MAGIC,
    MHIP_MAGIC,
    MHIT_MAGIC,
    MHLA_MAGIC,
    MHLI_MAGIC,
    MHLP_MAGIC,
    MHLT_MAGIC,
    MHOD_MAGIC,
    MHSD_MAGIC,
    MHYP_MAGIC,
    STRING_MHOD_TYPES,
)

logger = logging.getLogger(__name__)


class Record:
    """Base class for parsed binary records.

    Stores all parsed fields plus raw bytes for unknown regions to enable
    exact round-trip serialization.
    """

    __slots__ = (
        "magic",
        "header_len",
        "total_len",
        "fields",
        "raw_header",
        "children",
        "raw_bytes",
    )

    def __init__(self, magic: bytes, header_len: int, total_len: int) -> None:
        self.magic = magic
        self.header_len = header_len
        self.total_len = total_len
        self.fields: Dict[str, Any] = {}
        self.raw_header: bytes = b""  # Complete raw header bytes for preservation
        self.children: List[Record] = []
        self.raw_bytes: bytes = b""  # Full raw bytes of this record (header + body)

    def get_mhod(self, mhod_type: int) -> Optional[str]:
        """Get the string value of an MHOD child by type."""
        for child in self.children:
            if child.magic == MHOD_MAGIC and child.fields.get("mhod_type") == mhod_type:
                return child.fields.get("string")
        return None

    def get_mhods(self, mhod_type: int) -> List["Record"]:
        """Get all MHOD children of a given type."""
        return [
            c
            for c in self.children
            if c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == mhod_type
        ]

    def __repr__(self) -> str:
        m = self.magic.decode("ascii", errors="replace")
        extra = ""
        if "mhod_type" in self.fields:
            extra = f" type={self.fields['mhod_type']}"
        elif "mhsd_type" in self.fields:
            extra = f" type={self.fields['mhsd_type']}"
        return f"<Record {m} header={self.header_len} total={self.total_len}{extra}>"


def _parse_mhod_children(data: bytes, rec: Record, pos: int, end: int, count: int) -> int:
    """Parse MHOD children into rec, returning new position."""
    for _ in range(count):
        if pos + 12 > end or pos + 12 > len(data):
            break
        if data[pos : pos + 4] != MHOD_MAGIC:
            break
        mhod = _parse_mhod(data, pos)
        rec.children.append(mhod)
        pos += mhod.total_len
    return pos


def parse_itunesdb(data: bytes) -> Record:
    """Parse an iTunesDB binary blob into a tree of Record objects.

    Args:
        data: Raw bytes of the iTunesDB file.

    Returns:
        Root MHBD Record with all children parsed.

    Raises:
        ParseError: If the data is not a valid iTunesDB.
    """
    logger.debug("Parsing iTunesDB: %d bytes", len(data))
    if len(data) < 12:
        raise ParseError("Data too short to be an iTunesDB")
    if data[0:4] != MHBD_MAGIC:
        raise ParseError(f"Invalid magic: expected 'mhbd', got {data[0:4]!r}")

    return _parse_mhbd(data, 0)


def _parse_mhbd(data: bytes, offset: int) -> Record:
    """Parse MHBD (database header) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHBD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])

    # Parse known fields
    rec.fields["unknown1"] = get32lint(data, offset + 0x0C)
    rec.fields["db_version"] = get32lint(data, offset + 0x10)
    rec.fields["num_children"] = get32lint(data, offset + 0x14)
    rec.fields["db_id"] = get64lint(data, offset + 0x18)
    rec.fields["platform"] = get16lint(data, offset + 0x20)
    rec.fields["unk_0x22"] = get16lint(data, offset + 0x22)
    rec.fields["id_0x24"] = get64lint(data, offset + 0x24)

    if header_len >= 0x48:
        rec.fields["language"] = get16lint(data, offset + 0x46)
    if header_len >= 0x50:
        rec.fields["pid"] = get64lint(data, offset + 0x48)
    if header_len >= 0x54:
        rec.fields["unk_0x50"] = get32lint(data, offset + 0x50)
    if header_len >= 0x58:
        rec.fields["unk_0x54"] = get32lint(data, offset + 0x54)
    if header_len >= 0x72:
        rec.fields["hash58"] = bytes(data[offset + 0x58 : offset + 0x72])
    if header_len >= 0xA0:
        rec.fields["hash72"] = bytes(data[offset + 0x72 : offset + 0xA0])
    if header_len >= 0xA4:
        rec.fields["tz_offset"] = struct.unpack_from("<i", data, offset + 0x6C)[0]
    if header_len >= 0xAB:
        rec.fields["audio_language"] = get16lint(data, offset + 0xA0)
        rec.fields["subtitle_language"] = get16lint(data, offset + 0xA2)
        rec.fields["unk_0xa4"] = get16lint(data, offset + 0xA4)
        rec.fields["unk_0xa6"] = get16lint(data, offset + 0xA6)
        rec.fields["unk_0xa8"] = get16lint(data, offset + 0xA8)
    if header_len >= 0xE4:
        rec.fields["hashAB"] = bytes(data[offset + 0xAB : offset + 0xE4])

    rec.raw_bytes = bytes(data[offset : offset + total_len])

    # Parse child MHSD records
    pos = offset + header_len
    end = offset + total_len
    while pos < end:
        if pos + 12 > len(data):
            break
        child_magic = data[pos : pos + 4]
        if child_magic != MHSD_MAGIC:
            break
        child = _parse_mhsd(data, pos)
        rec.children.append(child)
        pos += child.total_len

    logger.debug("Parsed %d MHSD sections", len(rec.children))
    return rec


def _parse_mhsd(data: bytes, offset: int) -> Record:
    """Parse MHSD (section descriptor) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHSD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["mhsd_type"] = get32lint(data, offset + 0x0C)
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    mhsd_type = rec.fields["mhsd_type"]
    pos = offset + header_len

    if mhsd_type == 1:
        # Track list section - contains MHLT
        if pos + 4 <= len(data) and data[pos : pos + 4] == MHLT_MAGIC:
            child = _parse_mhlt(data, pos, offset + total_len)
            rec.children.append(child)
    elif mhsd_type in (2, 3):
        # Playlist list section - contains MHLP
        if pos + 4 <= len(data) and data[pos : pos + 4] == MHLP_MAGIC:
            child = _parse_mhlp(data, pos, offset + total_len)
            rec.children.append(child)
    elif mhsd_type == 4:
        # Album list section - contains MHLA
        if pos + 4 <= len(data) and data[pos : pos + 4] == MHLA_MAGIC:
            child = _parse_mhla(data, pos, offset + total_len)
            rec.children.append(child)
    elif mhsd_type == 5:
        # New artist list - store raw
        pass
    elif mhsd_type == 8:
        # Artist list section - contains MHLI with MHII children
        if pos + 4 <= len(data) and data[pos : pos + 4] == MHLI_MAGIC:
            child = _parse_mhli(data, pos, offset + total_len)
            rec.children.append(child)
    else:
        # Unknown section type - store raw for preservation
        pass

    return rec


def _parse_mhlt(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLT (track list) and all child MHIT records."""
    header_len = get32lint(data, offset + 4)
    num_tracks = get32lint(data, offset + 8)

    rec = Record(MHLT_MAGIC, header_len, num_tracks)  # total_len = num_tracks for MHLT
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_tracks"] = num_tracks

    pos = offset + header_len
    for _ in range(num_tracks):
        if pos + 4 > len(data) or data[pos : pos + 4] != MHIT_MAGIC:
            break
        track = _parse_mhit(data, pos)
        rec.children.append(track)
        pos += track.total_len

    # Store total bytes consumed
    rec.raw_bytes = bytes(data[offset:pos])
    logger.debug("Parsed %d tracks", num_tracks)
    return rec


def _parse_mhit(data: bytes, offset: int) -> Record:
    """Parse MHIT (track) record with all fields and child MHODs."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHIT_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    f = rec.fields
    f["num_mhods"] = get32lint(data, offset + 0x0C)
    f["track_id"] = get32lint(data, offset + 0x10)
    f["visible"] = get32lint(data, offset + 0x14)
    f["filetype_marker"] = bytes(data[offset + 0x18 : offset + 0x1C])
    f["type1"] = get8int(data, offset + 0x1C)
    f["type2"] = get8int(data, offset + 0x1D)
    f["compilation"] = get8int(data, offset + 0x1E)
    f["rating"] = get8int(data, offset + 0x1F)
    f["time_modified"] = get32lint(data, offset + 0x20)
    f["file_size"] = get32lint(data, offset + 0x24)
    f["tracklen"] = get32lint(data, offset + 0x28)
    f["track_number"] = get32lint(data, offset + 0x2C)
    f["total_tracks"] = get32lint(data, offset + 0x30)
    f["year"] = get32lint(data, offset + 0x34)
    f["bitrate"] = get32lint(data, offset + 0x38)
    f["samplerate"] = get32lint(data, offset + 0x3C)
    f["volume"] = struct.unpack_from("<i", data, offset + 0x40)[0]
    f["start_time"] = get32lint(data, offset + 0x44)
    f["stop_time"] = get32lint(data, offset + 0x48)
    f["soundcheck"] = get32lint(data, offset + 0x4C)
    f["play_count"] = get32lint(data, offset + 0x50)
    f["play_count2"] = get32lint(data, offset + 0x54)
    f["time_played"] = get32lint(data, offset + 0x58)
    f["cd_number"] = get32lint(data, offset + 0x5C)
    f["total_cds"] = get32lint(data, offset + 0x60)
    f["drm_userid"] = get32lint(data, offset + 0x64)
    f["time_added"] = get32lint(data, offset + 0x68)
    f["bookmark_time"] = get32lint(data, offset + 0x6C)
    f["dbid"] = get64lint(data, offset + 0x70)
    f["checked"] = get8int(data, offset + 0x78)
    f["app_rating"] = get8int(data, offset + 0x79)
    f["bpm"] = get16lint(data, offset + 0x7A)
    f["artwork_count"] = get16lint(data, offset + 0x7C)
    f["unk_0x7e"] = get16lint(data, offset + 0x7E)
    f["artwork_size"] = get32lint(data, offset + 0x80)
    f["unk_0x84"] = get32lint(data, offset + 0x84)
    f["samplerate_float"] = get_float(data, offset + 0x88)
    f["time_released"] = get32lint(data, offset + 0x8C)
    f["unk_0x90"] = get16lint(data, offset + 0x90)
    f["explicit_flag"] = get16lint(data, offset + 0x92)
    f["unk_0x94"] = get32lint(data, offset + 0x94)
    f["unk_0x98"] = get32lint(data, offset + 0x98)

    # Extended fields (header_len >= 0xF4)
    if header_len >= 0xF4:
        f["skip_count"] = get32lint(data, offset + 0x9C)
        f["time_skipped"] = get32lint(data, offset + 0xA0)
        f["has_artwork"] = get8int(data, offset + 0xA4)
        f["skip_when_shuffling"] = get8int(data, offset + 0xA5)
        f["remember_position"] = get8int(data, offset + 0xA6)
        f["flag4"] = get8int(data, offset + 0xA7)
        f["dbid2"] = get64lint(data, offset + 0xA8)
        f["lyrics_flag"] = get8int(data, offset + 0xB0)
        f["movie_flag"] = get8int(data, offset + 0xB1)
        f["mark_unplayed"] = get8int(data, offset + 0xB2)
        f["unk_0xb3"] = get8int(data, offset + 0xB3)
        f["unk_0xb4"] = get32lint(data, offset + 0xB4)
        f["pregap"] = get32lint(data, offset + 0xB8)
        f["sample_count"] = get64lint(data, offset + 0xBC)
        f["unk_0xc4"] = get32lint(data, offset + 0xC4)
        f["postgap"] = get32lint(data, offset + 0xC8)
        f["unk_0xcc"] = get32lint(data, offset + 0xCC)
        f["media_type"] = get32lint(data, offset + 0xD0)
        f["season_number"] = get32lint(data, offset + 0xD4)
        f["episode_number"] = get32lint(data, offset + 0xD8)
        f["unk_0xdc"] = get32lint(data, offset + 0xDC)
        f["unk_0xe0"] = get32lint(data, offset + 0xE0)
        f["unk_0xe4"] = get32lint(data, offset + 0xE4)
        f["unk_0xe8"] = get32lint(data, offset + 0xE8)
        f["unk_0xec"] = get32lint(data, offset + 0xEC)
        f["unk_0xf0"] = get32lint(data, offset + 0xF0)

    # More extended fields (header_len >= 0x148)
    if header_len >= 0x148:
        f["unk_0xf4"] = get32lint(data, offset + 0xF4)
        f["gapless_data"] = get32lint(data, offset + 0xF8)
        f["unk_0xfc"] = get32lint(data, offset + 0xFC)
        f["gapless_track_flag"] = get16lint(data, offset + 0x100)
        f["gapless_album_flag"] = get16lint(data, offset + 0x102)

    # MHII link (header_len >= 0x184)
    if header_len >= 0x184:
        f["mhii_link"] = get32lint(data, offset + 0x160)

    # Artist ID (header_len >= 0x1E4)
    if header_len >= 0x1E4:
        f["artist_id"] = get32lint(data, offset + 0x1E0)

    # Parse child MHOD records
    _parse_mhod_children(data, rec, offset + header_len, offset + total_len, f["num_mhods"])

    return rec


def _parse_mhod(data: bytes, offset: int) -> Record:
    """Parse MHOD (data object) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHOD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    mhod_type = get32lint(data, offset + 0x0C)
    rec.fields["mhod_type"] = mhod_type

    # Store the padding/unk bytes between type and body
    if header_len > 16:
        rec.fields["header_extra"] = bytes(data[offset + 16 : offset + header_len])

    body_offset = offset + header_len

    if mhod_type in STRING_MHOD_TYPES:
        _parse_string_mhod(data, body_offset, rec)
    elif mhod_type == 50:  # SPLPREF
        _parse_splpref_mhod(data, body_offset, rec)
    elif mhod_type == 51:  # SPLRULES
        _parse_splrules_mhod(data, body_offset, total_len - header_len, rec)
    elif mhod_type == 100:  # Playlist position
        _parse_playlist_mhod(data, offset, rec)
    else:
        # Types 52, 53, and unknown - store raw body for round-trip
        rec.fields["raw_body"] = bytes(data[body_offset : offset + total_len])

    return rec


def _parse_string_mhod(data: bytes, body_offset: int, rec: Record) -> None:
    """Parse a string-type MHOD body."""
    if body_offset + 16 > len(data):
        rec.fields["string"] = ""
        return

    encoding = get32lint(data, body_offset)
    str_len = get32lint(data, body_offset + 4)
    unk1 = get32lint(data, body_offset + 8)
    unk2 = get32lint(data, body_offset + 12)

    rec.fields["string_encoding"] = encoding
    rec.fields["string_unk1"] = unk1
    rec.fields["string_unk2"] = unk2

    str_start = body_offset + 16
    str_data = data[str_start : str_start + str_len]

    try:
        rec.fields["string"] = decode_mhod_string(str_data, encoding)
    except (UnicodeDecodeError, ValueError):
        logger.debug("MHOD string decode failed (encoding=%d), using fallback", encoding)
        rec.fields["string"] = str_data.decode("utf-16-le", errors="replace")
    rec.fields["string_raw"] = bytes(str_data)


def _parse_splpref_mhod(data: bytes, body_offset: int, rec: Record) -> None:
    """Parse smart playlist preferences (MHOD type 50)."""
    if body_offset + 14 > len(data):
        return
    rec.fields["liveupdate"] = get8int(data, body_offset)
    rec.fields["checkrules"] = get8int(data, body_offset + 1)
    rec.fields["checklimits"] = get8int(data, body_offset + 2)
    rec.fields["limittype"] = get8int(data, body_offset + 3)
    rec.fields["limitsort"] = get8int(data, body_offset + 4)
    rec.fields["limitvalue"] = get32lint(data, body_offset + 8)
    rec.fields["matchcheckedonly"] = get8int(data, body_offset + 12)
    rec.fields["limitsort_high"] = get8int(data, body_offset + 13)
    # Store raw for round-trip
    total_body = rec.total_len - rec.header_len
    rec.fields["raw_body"] = bytes(data[body_offset : body_offset + total_body])


def _parse_splrules_mhod(data: bytes, body_offset: int, body_len: int, rec: Record) -> None:
    """Parse smart playlist rules (MHOD type 51). Big-endian fields!"""
    # Store raw for round-trip - parsing SPL rules fully is complex
    rec.fields["raw_body"] = bytes(data[body_offset : body_offset + body_len])

    if body_len < 16:
        return
    magic = data[body_offset : body_offset + 4]
    rec.fields["spl_magic"] = magic  # Should be "SLst"
    rec.fields["spl_unk004"] = struct.unpack_from(">I", data, body_offset + 4)[0]
    rec.fields["spl_numrules"] = struct.unpack_from(">I", data, body_offset + 8)[0]
    rec.fields["spl_match_operator"] = struct.unpack_from(">I", data, body_offset + 12)[0]


def _parse_playlist_mhod(data: bytes, mhod_offset: int, rec: Record) -> None:
    """Parse playlist position MHOD (type 100)."""
    # track_pos is at mhod_offset + 24 (absolute), which is header_len (24) + 0
    body_offset = mhod_offset + rec.header_len
    total_body = rec.total_len - rec.header_len
    rec.fields["raw_body"] = bytes(data[body_offset : body_offset + total_body])
    # The position data is in the raw body


def _parse_mhlp(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLP (playlist list) and all child MHYP records."""
    header_len = get32lint(data, offset + 4)
    num_playlists = get32lint(data, offset + 8)

    rec = Record(MHLP_MAGIC, header_len, num_playlists)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_playlists"] = num_playlists

    pos = offset + header_len
    for _ in range(num_playlists):
        if pos + 4 > len(data) or data[pos : pos + 4] != MHYP_MAGIC:
            break
        playlist = _parse_mhyp(data, pos)
        rec.children.append(playlist)
        pos += playlist.total_len

    rec.raw_bytes = bytes(data[offset:pos])
    logger.debug("Parsed %d playlists", num_playlists)
    return rec


def _parse_mhyp(data: bytes, offset: int) -> Record:
    """Parse MHYP (playlist) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHYP_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    f = rec.fields
    f["num_mhods"] = get32lint(data, offset + 0x0C)
    f["num_mhips"] = get32lint(data, offset + 0x10)
    f["playlist_type"] = get8int(data, offset + 0x14)
    f["flag1"] = get8int(data, offset + 0x15)
    f["flag2"] = get8int(data, offset + 0x16)
    f["flag3"] = get8int(data, offset + 0x17)
    f["timestamp"] = get32lint(data, offset + 0x18)
    f["playlist_id"] = get64lint(data, offset + 0x1C)

    if header_len >= 0x2C:
        f["unk_0x24"] = get32lint(data, offset + 0x24)
        f["unk_0x28"] = get16lint(data, offset + 0x28)
        f["podcast_flag"] = get16lint(data, offset + 0x2A)
    if header_len >= 0x30:
        f["sort_order"] = get32lint(data, offset + 0x2C)
    if header_len >= 0x6C:
        f["mhsd5_type"] = get16lint(data, offset + 0x50)

    # Parse child records (MHODs then MHIPs)
    end = offset + total_len
    pos = _parse_mhod_children(data, rec, offset + header_len, end, f["num_mhods"])

    # Parse MHIPs
    for _ in range(f["num_mhips"]):
        if pos + 12 > end or pos + 12 > len(data):
            break
        if data[pos : pos + 4] != MHIP_MAGIC:
            break
        mhip = _parse_mhip(data, pos)
        rec.children.append(mhip)
        pos += mhip.total_len

    return rec


def _parse_mhip(data: bytes, offset: int) -> Record:
    """Parse MHIP (playlist item) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHIP_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    f = rec.fields
    f["num_mhods"] = get32lint(data, offset + 0x0C)
    f["podcastgroupflag"] = get32lint(data, offset + 0x10)
    f["podcastgroupid"] = get32lint(data, offset + 0x14)
    f["track_id"] = get32lint(data, offset + 0x18)

    if header_len >= 0x24:
        f["timestamp"] = get32lint(data, offset + 0x1C)
        f["podcastgroupref"] = get32lint(data, offset + 0x20)

    # Parse child MHODs
    _parse_mhod_children(data, rec, offset + header_len, offset + total_len, f["num_mhods"])

    return rec


def _parse_mhla(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLA (album list) and child MHIA records."""
    header_len = get32lint(data, offset + 4)
    num_albums = get32lint(data, offset + 8)

    rec = Record(MHLA_MAGIC, header_len, num_albums)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_albums"] = num_albums

    pos = offset + header_len
    for _ in range(num_albums):
        if pos + 4 > len(data):
            break
        magic = data[pos : pos + 4]
        if magic == MHIA_MAGIC:
            album = _parse_mhia(data, pos)
            rec.children.append(album)
            pos += album.total_len
        else:
            # Unknown album record type - try to read its size and skip
            if pos + 12 <= len(data):
                child_total = get32lint(data, pos + 8)
                if child_total > 0:
                    child = Record(magic, get32lint(data, pos + 4), child_total)
                    child.raw_bytes = bytes(data[pos : pos + child_total])
                    child.raw_header = bytes(data[pos : pos + child.header_len])
                    rec.children.append(child)
                    pos += child_total
                else:
                    break
            else:
                break

    rec.raw_bytes = bytes(data[offset:pos])
    return rec


def _parse_mhia(data: bytes, offset: int) -> Record:
    """Parse MHIA (album item) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHIA_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    f = rec.fields
    f["num_mhods"] = get32lint(data, offset + 0x0C)
    if header_len >= 0x14:
        f["album_id"] = get32lint(data, offset + 0x10)

    # Parse child MHODs
    _parse_mhod_children(data, rec, offset + header_len, offset + total_len, f.get("num_mhods", 0))

    return rec


def _parse_mhli(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLI (artist list) and child MHII records."""
    header_len = get32lint(data, offset + 4)
    num_artists = get32lint(data, offset + 8)

    rec = Record(MHLI_MAGIC, header_len, num_artists)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_artists"] = num_artists

    pos = offset + header_len
    for _ in range(num_artists):
        if pos + 4 > len(data):
            break
        magic = data[pos : pos + 4]
        if magic == MHII_MAGIC:
            artist = _parse_mhii(data, pos)
            rec.children.append(artist)
            pos += artist.total_len
        else:
            # Unknown record type - try to read its size and skip
            if pos + 12 <= len(data):
                child_total = get32lint(data, pos + 8)
                if child_total > 0:
                    child = Record(magic, get32lint(data, pos + 4), child_total)
                    child.raw_bytes = bytes(data[pos : pos + child_total])
                    child.raw_header = bytes(data[pos : pos + child.header_len])
                    rec.children.append(child)
                    pos += child_total
                else:
                    break
            else:
                break

    rec.raw_bytes = bytes(data[offset:pos])
    return rec


def _parse_mhii(data: bytes, offset: int) -> Record:
    """Parse MHII (artist item) record."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHII_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    f = rec.fields
    f["num_mhods"] = get32lint(data, offset + 0x0C)
    if header_len >= 0x14:
        f["artist_id"] = get32lint(data, offset + 0x10)

    # Parse child MHODs
    _parse_mhod_children(data, rec, offset + header_len, offset + total_len, f.get("num_mhods", 0))

    return rec
