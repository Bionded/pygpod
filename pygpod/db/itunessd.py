"""iTunesSD parser and writer for iPod Shuffle.

The iTunesSD format is a simpler binary database used by iPod Shuffle
for playback. It coexists with the iTunesDB.

iTunesSD v2 format (used by Shuffle 2G+):
- Header: 18 bytes
  - num_songs (3 bytes big-endian)
  - header_size (3 bytes big-endian) = 18 (0x000012)
  - db_version (3 bytes big-endian) = 0x010800 (v2) or 0x020A00 (v3)
  - remaining bytes padding/unknown
- Per track: 558 bytes
  - header_size (4 bytes LE) = 558
  - unk1 (4 bytes)
  - start_pos (4 bytes LE) - start position in ms
  - stop_pos (4 bytes LE) - stop position in ms
  - volume (4 bytes LE)
  - file_type (4 bytes LE) - 1=MP3, 2=AAC, 4=WAV
  - filename (522 bytes) - UTF-16LE null-terminated path
  - shuffle_flag (1 byte)
  - bookmark_flag (1 byte)
  - unk2 (1 byte)

iTunesSD v1 format (Shuffle 1G):
- Header: 18 bytes
- Per track: 558 bytes (same structure but older version number)
"""

from __future__ import annotations

import logging
import struct
from typing import List, NamedTuple

logger = logging.getLogger(__name__)


def _get24bint(data: bytes, offset: int) -> int:
    """Read a 3-byte big-endian integer."""
    return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]


def _put24bint(buf: bytearray, offset: int, val: int) -> None:
    """Write a 3-byte big-endian integer."""
    buf[offset] = (val >> 16) & 0xFF
    buf[offset + 1] = (val >> 8) & 0xFF
    buf[offset + 2] = val & 0xFF


class ITunesSDTrack(NamedTuple):
    """A track entry in iTunesSD."""

    start_pos_ms: int
    stop_pos_ms: int
    volume: int
    file_type: int  # 1=MP3, 2=AAC, 4=WAV
    filename: str  # iPod path (colon-separated)
    shuffle_flag: int
    bookmark_flag: int


class ITunesSD:
    """Parsed iTunesSD database."""

    def __init__(self) -> None:
        self.tracks: List[ITunesSDTrack] = []
        self.db_version: int = 0x010800
        self.raw_header: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "ITunesSD":
        """Parse an iTunesSD file.

        Args:
            data: Raw bytes of the iTunesSD file.

        Returns:
            Parsed ITunesSD object.
        """
        sd = cls()
        logger.debug("Parsing iTunesSD: %d bytes", len(data))
        if len(data) < 18:
            return sd

        # Header
        num_songs = _get24bint(data, 0)
        header_size = _get24bint(data, 3)
        sd.db_version = _get24bint(data, 6)
        sd.raw_header = bytes(data[:header_size])

        # Tracks
        pos = header_size
        for _ in range(num_songs):
            if pos + 558 > len(data):
                break

            entry_size = struct.unpack_from("<I", data, pos)[0]
            start_pos = struct.unpack_from("<I", data, pos + 8)[0]
            stop_pos = struct.unpack_from("<I", data, pos + 12)[0]
            volume = struct.unpack_from("<I", data, pos + 16)[0]
            file_type = struct.unpack_from("<I", data, pos + 20)[0]

            # Filename: 522 bytes of UTF-16LE starting at pos+24
            fname_data = data[pos + 24 : pos + 24 + 522]
            try:
                filename = fname_data.decode("utf-16-le").rstrip("\x00")
            except UnicodeDecodeError:
                logger.debug("iTunesSD: failed to decode filename at offset %d", pos + 24)
                filename = ""

            shuffle_flag = data[pos + 546] if pos + 546 < len(data) else 0
            bookmark_flag = data[pos + 547] if pos + 547 < len(data) else 0

            sd.tracks.append(
                ITunesSDTrack(
                    start_pos_ms=start_pos,
                    stop_pos_ms=stop_pos,
                    volume=volume,
                    file_type=file_type,
                    filename=filename,
                    shuffle_flag=shuffle_flag,
                    bookmark_flag=bookmark_flag,
                )
            )

            pos += max(entry_size, 558)

        return sd

    def write(self) -> bytes:
        """Serialize to iTunesSD binary format.

        Returns:
            Complete iTunesSD binary data.
        """
        logger.debug("Writing iTunesSD: %d tracks", len(self.tracks))
        header_size = 18
        entry_size = 558

        # Header
        header = bytearray(header_size)
        _put24bint(header, 0, len(self.tracks))
        _put24bint(header, 3, header_size)
        _put24bint(header, 6, self.db_version)

        result = bytearray(header)

        # Tracks
        for track in self.tracks:
            entry = bytearray(entry_size)
            struct.pack_into("<I", entry, 0, entry_size)
            struct.pack_into("<I", entry, 8, track.start_pos_ms)
            struct.pack_into("<I", entry, 12, track.stop_pos_ms)
            struct.pack_into("<I", entry, 16, track.volume)
            struct.pack_into("<I", entry, 20, track.file_type)

            # Filename as UTF-16LE
            fname_bytes = track.filename.encode("utf-16-le")[:520]
            entry[24 : 24 + len(fname_bytes)] = fname_bytes

            entry[546] = track.shuffle_flag
            entry[547] = track.bookmark_flag

            result.extend(entry)

        return bytes(result)

    @staticmethod
    def filetype_from_path(path: str) -> int:
        """Get iTunesSD file type code from file path.

        Returns:
            1 for MP3, 2 for AAC/M4A, 4 for WAV.
        """
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return {
            "mp3": 1,
            "m4a": 2,
            "m4b": 2,
            "m4p": 2,
            "aac": 2,
            "wav": 4,
            "aiff": 4,
            "aif": 4,
        }.get(ext, 1)
