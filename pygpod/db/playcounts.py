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

import logging
import os
import struct
from typing import List, NamedTuple

logger = logging.getLogger(__name__)


class PlayCountEntry(NamedTuple):
    """A single play count update from the iPod."""

    play_count: int
    time_played: int  # Mac timestamp
    rating: int  # 0-100, or -1 if unchanged
    skip_count: int
    time_skipped: int  # Mac timestamp
    bookmark_time: int


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
def parse_itunesstats(text: str) -> List[PlayCountEntry]:
    """Parse an 'iTunesStats' text file.

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

    items = plist.get("items", [])
    entries = []
    for item in items:
        if not isinstance(item, dict):
            entries.append(PlayCountEntry(0, 0, -1, 0, 0, 0))
            continue

        play_count = item.get("playcount", 0) or 0
        # PersistentID-based lookup may be needed; we return positional data
        rating = item.get("rating", 0) or 0
        skip_count = item.get("skipcount", 0) or 0
        bookmark = item.get("bookmark", 0) or 0

        entries.append(
            PlayCountEntry(
                play_count=play_count,
                time_played=0,  # Not always available in plist
                rating=rating if rating > 0 else -1,
                skip_count=skip_count,
                time_skipped=0,
                bookmark_time=bookmark,
            )
        )

    return entries


# ============================================================================
# Apply play counts to database tracks
# ============================================================================
def apply_play_counts(tracks: list, entries: List[PlayCountEntry]) -> int:
    """Apply play count entries to tracks.

    Tracks and entries are matched by position (index). The iPod writes
    one entry per track in database order. Updates both fields dict and
    raw_header bytes so changes survive serialization.

    Args:
        tracks: List of Track objects (in database order).
        entries: List of PlayCountEntry (in database order).

    Returns:
        Number of tracks updated.
    """
    updated = 0
    for i, entry in enumerate(entries):
        if i >= len(tracks):
            break

        track = tracks[i]
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
    # libgpod also tries byte-reversed magic
    if magic not in (b"mhpo", b"ophm"):
        return []

    header_length = struct.unpack_from("<I", data, 4)[0]
    entry_length = struct.unpack_from("<I", data, 8)[0]
    entry_count = struct.unpack_from("<I", data, 12)[0]

    if header_length < 0x14 or entry_length < 4 or entry_count == 0:
        return []

    track_indices = []
    for i in range(entry_count):
        offset = header_length + entry_length * i
        if offset + 4 > len(data):
            break
        idx = struct.unpack_from("<I", data, offset)[0]
        track_indices.append(idx)

    return [track_indices] if track_indices else []


# ============================================================================
# Convenience: read all play count files from mountpoint
# ============================================================================
def read_play_counts(mountpoint: str) -> List[PlayCountEntry]:
    """Read play count data from an iPod mount point.

    Tries the following files in order:
    1. PlayCounts.plist (Touch/iPhone)
    2. iTunesStats (5G+)
    3. Play Counts (pre-5G)

    Args:
        mountpoint: iPod mount point path.

    Returns:
        List of PlayCountEntry, or empty list if none found.
    """
    itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")

    # Try PlayCounts.plist first
    plist_path = os.path.join(itunes_dir, "PlayCounts.plist")
    if os.path.isfile(plist_path):
        with open(plist_path, "rb") as f:
            return parse_playcounts_plist(f.read())

    # Try iTunesStats
    stats_path = os.path.join(itunes_dir, "iTunesStats")
    if os.path.isfile(stats_path):
        with open(stats_path, "r", encoding="utf-8", errors="replace") as f:
            return parse_itunesstats(f.read())

    # Try Play Counts
    pc_path = os.path.join(itunes_dir, "Play Counts")
    if os.path.isfile(pc_path):
        with open(pc_path, "rb") as f:
            return parse_play_counts(f.read())

    return []


def read_otg_playlists(mountpoint: str) -> List[List[int]]:
    """Read OTG playlists from an iPod mount point.

    Args:
        mountpoint: iPod mount point path.

    Returns:
        List of playlists (each a list of track indices).
    """
    itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")
    otg_path = os.path.join(itunes_dir, "OTGPlaylistInfo")
    if os.path.isfile(otg_path):
        with open(otg_path, "rb") as f:
            return parse_otg_playlists(f.read())
    return []
