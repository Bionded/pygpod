"""Play Counts, iTunesStats, and OTG playlist file parsers.

Ported from libgpod's itdb_itunesdb.c play count and OTG reading code.
These files are written by the iPod itself and contain data about
user interactions (play counts, ratings, etc.) since the last sync.

File locations (all under iPod_Control/iTunes/):
- Play Counts: Binary format (pre-5G)
- iTunesStats: Text format (5G+)
- PlayCounts.plist: Plist format (Touch/iPhone)
- OTGPlaylistInfo: On-The-Go playlists created on device
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
from typing import Any, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)


class PlayCountEntry(NamedTuple):
    """A single play count update from the iPod."""

    play_count: int
    time_played: int  # Mac timestamp
    rating: int  # 0-100, or -1 if unchanged
    skip_count: int
    time_skipped: int  # Mac timestamp
    bookmark_time: int
    persistent_id: Optional[int] = (
        None  # PlayCounts.plist: the track's dbid (match by id, not position)
    )


class PendingPlayCount(NamedTuple):
    """A play count update read from the iPod, matched to its track.

    Values are as written by the iPod: ``play_count`` and ``skip_count`` are
    increments since the last sync, the others are absolute values
    (0 / -1 for rating = not set).
    """

    track: Any  # Track
    play_count: int
    time_played: int  # Mac timestamp
    rating: int  # 0-100, or -1 if unchanged
    skip_count: int
    time_skipped: int  # Mac timestamp
    bookmark_time: int


# Play count files in the order libgpod reads them (only the first one found is used).
PLAY_COUNT_FILES = ("Play Counts", "iTunesStats", "PlayCounts.plist")

# Written next to iTunesDB by Database.save() while play counts are being consumed.
# Records which play count file was merged into which iTunesDB, so a crash between
# writing iTunesDB and deleting the play count file never applies the counts twice.
PLAY_COUNTS_COMMIT_FILE = "pygpod.playcounts-commit"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PlayCountSource(NamedTuple):
    """The play count file a database was opened with."""

    name: str  # file name under iPod_Control/iTunes
    sha256: str
    entries: List[PlayCountEntry]
    # The other play count files present at open (name -> sha256). Not read (libgpod
    # uses only the first one found) but deleted with the source after a save, like libgpod.
    others: Dict[str, str] = {}


# ============================================================================
# Play Counts (binary format, pre-5G)
# ============================================================================
def parse_play_counts(data: bytes) -> List[PlayCountEntry]:
    """Parse a 'Play Counts' binary file.

    Format (from libgpod itdb_itunesdb.c playcounts_read):
    - Header starts with "mhdp" magic:
      - 0x00: magic "mhdp" (4 bytes)
      - 0x04: header_length (32-bit LE, typically 0x60 = 96)
      - 0x08: entry_length (32-bit LE)
      - 0x0C: num_entries (32-bit LE)
    - Entries at offset header_length, entry_length bytes each:
      - 0x00: play_count (32-bit LE)
      - 0x04: time_played (32-bit LE, Mac timestamp)
      - 0x08: bookmark_time (32-bit LE)
      (if entry_length >= 0x10:)
      - 0x0C: rating (32-bit LE, 0-100 or 0 if unchanged)
      (if entry_length >= 0x1C:)
      - 0x14: skip_count (32-bit LE)
      - 0x18: time_skipped (32-bit LE, Mac timestamp)

    Args:
        data: Raw bytes of the Play Counts file.

    Returns:
        List of PlayCountEntry, one per track (indexed by track position).
    """
    if len(data) < 16:
        return []

    # Check for mhdp magic header
    magic = data[0:4]
    if magic in (b"mhdp", b"pdhm"):
        header_size = struct.unpack_from("<I", data, 4)[0]
        entry_size = struct.unpack_from("<I", data, 8)[0]
        num_entries = struct.unpack_from("<I", data, 12)[0]
    else:
        # Legacy format without magic
        header_size = struct.unpack_from("<I", data, 0)[0]
        entry_size = struct.unpack_from("<I", data, 4)[0]
        num_entries = struct.unpack_from("<I", data, 8)[0]

    if entry_size == 0 or header_size == 0:
        return []

    entries = []
    offset = header_size
    for _ in range(num_entries):
        if offset + entry_size > len(data):
            break

        play_count = struct.unpack_from("<I", data, offset)[0]
        time_played = struct.unpack_from("<I", data, offset + 4)[0] if entry_size >= 8 else 0
        bookmark_time = struct.unpack_from("<I", data, offset + 8)[0] if entry_size >= 12 else 0
        rating = struct.unpack_from("<I", data, offset + 12)[0] if entry_size >= 16 else 0
        skip_count = struct.unpack_from("<I", data, offset + 20)[0] if entry_size >= 28 else 0
        time_skipped = struct.unpack_from("<I", data, offset + 24)[0] if entry_size >= 28 else 0

        # Rating of 0 means unchanged (not "0 stars")
        r = rating if rating > 0 else -1

        entries.append(
            PlayCountEntry(
                play_count=play_count,
                time_played=time_played,
                rating=r,
                skip_count=skip_count,
                time_skipped=time_skipped,
                bookmark_time=bookmark_time,
            )
        )
        offset += entry_size

    logger.debug("Parsed %d play count entries", len(entries))
    return entries


# ============================================================================
# iTunesStats (text format, 5G+)
# ============================================================================
def parse_itunesstats(data, old_format: Optional[bool] = None) -> List[PlayCountEntry]:
    """Parse an 'iTunesStats' file (written by iPod shuffles), as libgpod 0.8.3 does.

    Binary, little-endian, one entry per track in database order:
    - old format (shuffle 2G): u24 entry count, entries from offset 6, each
      u24 entry_length (>= 18), u24 bookmark_time, u24 unk, u24 unk,
      u24 play_count, u24 skip_count;
    - new format: u32 entry count, entries from offset 8, each u32
      entry_length (>= 32), u32 bookmark_time, u32 play_count, u32 time_played,
      u32 skip_count, u32 time_skipped, 8 unknown bytes.
    ``old_format=None`` detects the layout. Unlike libgpod 0.8.3 (whose
    new-format loop never advances), entries are read at consecutive offsets.
    A ``str`` (or bytes that are only digits and newlines) is read in pygpod's
    earlier text form (count, then 6 lines per entry) for compatibility.
    """
    if isinstance(data, str):
        return _parse_itunesstats_text(data)
    if data and all(b in b"0123456789\r\n -" for b in data):
        return _parse_itunesstats_text(data.decode("ascii"))
    if old_format is None:
        old_format = not _looks_like_new_itunesstats(data)
    entries: List[PlayCountEntry] = []
    if old_format:
        if len(data) < 6:
            return []
        count, seek, min_len, width = _u24(data, 0), 6, 18, 3
    else:
        if len(data) < 8:
            return []
        count, seek, min_len, width = struct.unpack_from("<I", data, 0)[0], 8, 32, 4
    get = _u24 if old_format else (lambda d, o: struct.unpack_from("<I", d, o)[0])
    for _ in range(count):
        if seek + width > len(data):
            break
        entry_len = get(data, seek)
        if entry_len < min_len or seek + min_len > len(data):
            logger.warning(
                "iTunesStats: entry length %d smaller than expected (%d)", entry_len, min_len
            )
            break
        if old_format:
            entries.append(
                PlayCountEntry(
                    play_count=get(data, seek + 12),
                    time_played=0,
                    rating=-1,
                    skip_count=get(data, seek + 15),
                    time_skipped=0,
                    bookmark_time=get(data, seek + 3),
                )
            )
        else:
            entries.append(
                PlayCountEntry(
                    play_count=get(data, seek + 8),
                    time_played=get(data, seek + 12),
                    rating=-1,
                    skip_count=get(data, seek + 16),
                    time_skipped=get(data, seek + 20),
                    bookmark_time=get(data, seek + 4),
                )
            )
        seek += entry_len
    return entries


def _u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "little")


def _looks_like_new_itunesstats(data: bytes) -> bool:
    """New layout: u32 count, first entry at 8 with a u32 length >= 32 that fits the file."""
    if len(data) < 12:
        return False
    count = struct.unpack_from("<I", data, 0)[0]
    first = struct.unpack_from("<I", data, 8)[0]
    return count == 0 or (first >= 32 and 8 + first <= len(data))


def _parse_itunesstats_text(text: str) -> List[PlayCountEntry]:
    """Parse pygpod's earlier text form of 'iTunesStats' (not written by any iPod).

    Format: one entry per 6 lines, first line is count of entries.
    For each entry:
      line 1: play_count
      line 2: time_played (Mac timestamp)
      line 3: skip_count
      line 4: time_skipped (Mac timestamp)
      line 5: bookmark_time
      line 6: (empty or padding)

    Args:
        text: Content of the iTunesStats file.

    Returns:
        List of PlayCountEntry.
    """
    lines = text.strip().split("\n")
    if not lines:
        return []

    try:
        num_entries = int(lines[0].strip())
    except (ValueError, IndexError):
        logger.debug("Invalid iTunesStats header")
        return []

    entries = []
    idx = 1
    for _ in range(num_entries):
        if idx >= len(lines):
            break  # the count claims more entries than the file has
        try:
            play_count = int(lines[idx].strip()) if idx < len(lines) else 0
            time_played = int(lines[idx + 1].strip()) if idx + 1 < len(lines) else 0
            skip_count = int(lines[idx + 2].strip()) if idx + 2 < len(lines) else 0
            time_skipped = int(lines[idx + 3].strip()) if idx + 3 < len(lines) else 0
            bookmark_time = int(lines[idx + 4].strip()) if idx + 4 < len(lines) else 0
        except (ValueError, IndexError):
            logger.debug("Malformed iTunesStats entry at index %d", idx)
            idx += 6
            continue

        entries.append(
            PlayCountEntry(
                play_count=play_count,
                time_played=time_played,
                rating=-1,  # iTunesStats doesn't include rating
                skip_count=skip_count,
                time_skipped=time_skipped,
                bookmark_time=bookmark_time,
            )
        )
        idx += 6

    return entries


# ============================================================================
# PlayCounts.plist (iPod Touch / iPhone)
# ============================================================================
def parse_playcounts_plist(data: bytes) -> List[PlayCountEntry]:
    """Parse a 'PlayCounts.plist' file.

    Schema as read by libgpod (playcounts_plist_read): a dict with "tracks", a
    list of dicts keyed by "persistentID" (the track's dbid) with "playCount",
    "playMacOSDate", "skipCount", "skipMacOSDate", "userRating" (0-100) and
    "bookmarkTimeInMS". Entries carry ``persistent_id`` and are matched by it.
    The older pygpod form {"items": [{"playcount", "rating", "skipcount",
    "bookmark"}]} is still read, positionally. pygpod does not support the
    devices that write this file (iPod touch / iPhone); it is parsed so that a
    database copied from one can be read.

    Args:
        data: Raw plist file bytes.

    Returns:
        List of PlayCountEntry.
    """
    import plistlib

    try:
        plist = plistlib.loads(data)
    except Exception:
        logger.warning("Failed to parse PlayCounts.plist", exc_info=True)
        return []

    if not isinstance(plist, dict):
        return []

    def num(d: dict, key: str) -> int:
        v = d.get(key, 0)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    entries = []
    if isinstance(plist.get("tracks"), list):
        for item in plist["tracks"]:
            if not isinstance(item, dict) or not isinstance(item.get("persistentID"), int):
                logger.debug("PlayCounts.plist: entry without persistentID skipped")
                continue
            rating = num(item, "userRating")
            entries.append(
                PlayCountEntry(
                    play_count=num(item, "playCount"),
                    time_played=num(item, "playMacOSDate"),
                    rating=rating if rating > 0 else -1,
                    skip_count=num(item, "skipCount"),
                    time_skipped=num(item, "skipMacOSDate"),
                    bookmark_time=num(item, "bookmarkTimeInMS"),
                    persistent_id=item["persistentID"] & 0xFFFFFFFFFFFFFFFF,
                )
            )
        return entries

    for item in plist.get("items", []):
        if not isinstance(item, dict):
            entries.append(PlayCountEntry(0, 0, -1, 0, 0, 0))
            continue
        rating = item.get("rating", 0) or 0
        entries.append(
            PlayCountEntry(
                play_count=item.get("playcount", 0) or 0,
                time_played=0,
                rating=rating if rating > 0 else -1,
                skip_count=item.get("skipcount", 0) or 0,
                time_skipped=0,
                bookmark_time=item.get("bookmark", 0) or 0,
            )
        )
    return entries


def match_play_counts(tracks: list, entries: List[PlayCountEntry]) -> list:
    """(track, entry) pairs: by persistent ID (dbid) where entries have one, else by position.

    Unknown IDs are dropped; for a duplicated ID the last entry wins (as in libgpod).
    """
    by_id = [e for e in entries if e.persistent_id is not None]
    if not by_id:
        return list(zip(tracks, entries))
    lookup = {}
    for t in tracks:
        dbid = t.record.fields.get("dbid") if getattr(t, "record", None) is not None else None
        if dbid is not None:
            lookup[dbid] = t
    matched: Dict[int, PlayCountEntry] = {}
    for e in by_id:
        if e.persistent_id in matched:
            logger.warning("PlayCounts.plist: duplicate persistentID %016x", e.persistent_id)
        matched[e.persistent_id] = e
    unknown = [pid for pid in matched if pid not in lookup]
    if unknown:
        logger.info("PlayCounts.plist: %d entries for unknown tracks ignored", len(unknown))
    return [(lookup[pid], e) for pid, e in matched.items() if pid in lookup]


# ============================================================================
# Apply play counts to database tracks
# ============================================================================
def apply_play_counts(tracks: list, entries: List[PlayCountEntry]) -> int:
    """Apply play count entries to tracks.

    Tracks and entries are matched by persistent ID (PlayCounts.plist) or by
    position (Play Counts / iTunesStats: one entry per track in database
    order). Updates both fields dict and raw_header bytes so changes survive
    serialization.

    Args:
        tracks: List of Track objects (in database order).
        entries: List of PlayCountEntry (in database order).

    Returns:
        Number of tracks updated.
    """
    updated = 0
    for track, entry in match_play_counts(tracks, entries):
        if not track.record:
            continue

        fields = track.record.fields
        header = bytearray(track.record.raw_header)
        changed = False

        if entry.play_count > 0:
            new_pc = fields.get("play_count", 0) + entry.play_count
            fields["play_count"] = new_pc
            if len(header) > 0x54:
                struct.pack_into("<I", header, 0x50, new_pc)
            changed = True
        if entry.time_played > 0:
            fields["time_played"] = entry.time_played
            if len(header) > 0x5C:
                struct.pack_into("<I", header, 0x58, entry.time_played)
            changed = True
        if entry.rating >= 0:
            fields["rating"] = entry.rating
            if len(header) >= 0x20:
                header[0x1F] = min(entry.rating, 255)
            changed = True
        if entry.skip_count > 0:
            new_sc = fields.get("skip_count", 0) + entry.skip_count
            fields["skip_count"] = new_sc
            if len(header) > 0xA0:
                struct.pack_into("<I", header, 0x9C, new_sc)
            changed = True
        if entry.time_skipped > 0:
            fields["time_skipped"] = entry.time_skipped
            if len(header) > 0xA4:
                struct.pack_into("<I", header, 0xA0, entry.time_skipped)
            changed = True
        if entry.bookmark_time > 0:
            fields["bookmark_time"] = entry.bookmark_time
            if len(header) > 0x70:
                struct.pack_into("<I", header, 0x6C, entry.bookmark_time)
            changed = True

        if changed:
            track.record.raw_header = bytes(header)
            updated += 1

    return updated


# ============================================================================
# OTG Playlists (On-The-Go)
# ============================================================================
def parse_otg_playlists(data: bytes) -> List[List[int]]:
    """Parse an 'OTGPlaylistInfo' binary file.

    Format (from libgpod process_OTG_file):
    - Header (minimum 0x14 = 20 bytes):
      - 0x00: magic "mhpo" (4 bytes)
      - 0x04: header_length (32-bit LE, >= 0x14)
      - 0x08: entry_length (32-bit LE, >= 4)
      - 0x0C: entry_count (32-bit LE)
    - Then entry_count entries of entry_length bytes each,
      starting at offset header_length.
      Each entry is a 32-bit LE track index (0-based position in track list).

    Each OTGPlaylistInfo file represents one playlist. Multiple OTG
    playlists are stored in OTGPlaylistInfo, OTGPlaylistInfo_1, etc.

    Args:
        data: Raw bytes of the OTGPlaylistInfo file.

    Returns:
        List containing one playlist (a list of track indices).
    """
    if len(data) < 16:
        return []

    magic = data[0:4]
    # libgpod also tries byte-reversed magic: then every field is big-endian
    if magic not in (b"mhpo", b"ophm"):
        return []
    order = "<" if magic == b"mhpo" else ">"

    header_length = struct.unpack_from(f"{order}I", data, 4)[0]
    entry_length = struct.unpack_from(f"{order}I", data, 8)[0]
    entry_count = struct.unpack_from(f"{order}I", data, 12)[0]

    if header_length < 0x14 or entry_length < 4 or entry_count == 0:
        return []

    track_indices = []
    for i in range(entry_count):
        offset = header_length + entry_length * i
        if offset + 4 > len(data):
            break
        idx = struct.unpack_from(f"{order}I", data, offset)[0]
        track_indices.append(idx)

    return [track_indices] if track_indices else []


# ============================================================================
# Convenience: read all play count files from mountpoint
# ============================================================================
def read_play_count_source(mountpoint: str) -> Optional[PlayCountSource]:
    """Read and parse the play count file that read_play_counts() would use.

    Returns:
        PlayCountSource (file name, sha256 of its bytes, entries), or None.
    """
    itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")
    for i, name in enumerate(PLAY_COUNT_FILES):
        path = os.path.join(itunes_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        if name == "PlayCounts.plist":
            entries = parse_playcounts_plist(data)
        elif name == "iTunesStats":
            entries = parse_itunesstats(data)
        else:
            entries = parse_play_counts(data)
        others: Dict[str, str] = {}
        for other in PLAY_COUNT_FILES[i + 1 :]:
            digest = file_sha256(os.path.join(itunes_dir, other))
            if digest is not None:
                others[other] = digest
        return PlayCountSource(name, sha256_hex(data), entries, others)
    return None


def file_sha256(path: str) -> Optional[str]:
    """sha256 of a file's bytes, or None if it cannot be read."""
    try:
        with open(path, "rb") as f:
            return sha256_hex(f.read())
    except OSError:
        return None


def read_commit_marker(itunes_dir: str) -> Optional[Dict[str, Any]]:
    """Read the play counts commit marker, or None if absent or unreadable."""
    path = os.path.join(itunes_dir, PLAY_COUNTS_COMMIT_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            marker = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(marker, dict):
        return None
    others = marker.get("others")
    result: Dict[str, Any] = {k: str(v) for k, v in marker.items() if k != "others"}
    result["others"] = (
        {str(k): str(v) for k, v in others.items()} if isinstance(others, dict) else {}
    )
    return result


def write_commit_marker(itunes_dir: str, source: PlayCountSource, itunesdb_sha256: str) -> None:
    """Durably record that ``source`` is merged into the iTunesDB with ``itunesdb_sha256``."""
    path = os.path.join(itunes_dir, PLAY_COUNTS_COMMIT_FILE)
    tmp = path + ".tmp"
    marker = {
        "file": source.name,
        "file_sha256": source.sha256,
        "itunesdb_sha256": itunesdb_sha256,
        "others": dict(source.others),
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(marker, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def remove_consumed_files(itunes_dir: str, name: str, sha256: str, others: Dict[str, str]) -> bool:
    """Delete a consumed play count file and the other variants seen with it.

    A file is only deleted if its bytes are still the ones seen at open (the
    iPod may have rewritten it since; those plays are not merged yet). The other
    variants go first, the consumed file last, so an interrupted cleanup is
    finished on the next open (the commit marker still matches the consumed file).

    Returns:
        True if the consumed file is gone (the commit marker can be removed).
    """
    for other, digest in others.items():
        path = os.path.join(itunes_dir, other)
        if file_sha256(path) == digest:
            remove_file(path)
    path = os.path.join(itunes_dir, name)
    return file_sha256(path) in (sha256, None) and remove_file(path)


def remove_file(path: str) -> bool:
    """Delete a file; True if it is gone afterwards."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError:
        logger.warning("Failed to delete %s", path, exc_info=True)
        return False
    return True


def read_play_counts(mountpoint: str) -> List[PlayCountEntry]:
    """Read play count data from an iPod mount point.

    Uses the first file found in libgpod's order: Play Counts, iTunesStats,
    PlayCounts.plist.

    Args:
        mountpoint: iPod mount point path.

    Returns:
        List of PlayCountEntry, or empty list if none found.
    """
    source = read_play_count_source(mountpoint)
    return source.entries if source else []


OTG_FILE = "OTGPlaylistInfo"


class OTGFile(NamedTuple):
    """One On-The-Go playlist file as read from the iPod."""

    name: str  # OTGPlaylistInfo, OTGPlaylistInfo_1, ...
    sha256: str
    indices: List[int]  # positions in the iTunesDB track list


class OTGSkippedEntry(NamedTuple):
    """An On-The-Go entry left out of the import because its track or file is bad."""

    file: str  # OTGPlaylistInfo, OTGPlaylistInfo_1, ...
    position: int  # 0-based position of the entry in that file
    index: int  # track position it referred to
    reason: str


def read_otg_files(mountpoint: str) -> List[OTGFile]:
    """OTGPlaylistInfo, then OTGPlaylistInfo_1, _2, ... until one is missing.

    Same order and stop rule as libgpod's read_OTG_playlists.
    """
    itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")
    out: List[OTGFile] = []
    i = 0
    while True:
        name = OTG_FILE if i == 0 else f"{OTG_FILE}_{i}"
        path = os.path.join(itunes_dir, name)
        if not os.path.isfile(path):
            return out
        with open(path, "rb") as f:
            data = f.read()
        playlists = parse_otg_playlists(data)
        out.append(OTGFile(name, sha256_hex(data), playlists[0] if playlists else []))
        i += 1


def read_otg_playlists(mountpoint: str) -> List[List[int]]:
    """Read OTG playlists from an iPod mount point (every numbered file).

    Args:
        mountpoint: iPod mount point path.

    Returns:
        List of playlists (each a list of track indices); empty files are left out.
    """
    return [f.indices for f in read_otg_files(mountpoint) if f.indices]
