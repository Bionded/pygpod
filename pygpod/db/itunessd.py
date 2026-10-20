"""iTunesSD parser and writer for iPod Shuffle (1st and 2nd generation).

The shuffle plays from iTunesSD, a simple database written next to the
iTunesDB. Format as written by libgpod 0.8.3 (itdb_shuffle_write_file, shadow
DB version 1 = shuffle 1G/2G); every number is a 24-bit big-endian integer:

- Header (18 bytes): number of tracks, 0x010600 (version), 0x000012 (header
  size), 3 x 0.
- Per track (558 bytes): 0x00022e (entry size), 0x5aa501, start time / 256 ms,
  0, 0, stop time / 256 ms, 0, 0, volume (0-200; iTunesDB -255..255 scaled),
  file type (1 MP3, 2 AAC, 4 WAV), 0x200, path as UTF-16LE with "/" separators
  padded to 522 bytes (261 characters), then u8 "use in shuffle"
  (libgpod writes the track's skip_when_shuffling flag), u8 bookmarkable
  (remember position), u8 0.

The shuffle 3G/4G format (bdhs/hths/hphs, shadow DB version 2) is not supported.
"""

from __future__ import annotations

import logging
from typing import Any, List, NamedTuple

logger = logging.getLogger(__name__)

HEADER_SIZE = 0x12
ENTRY_SIZE = 0x22E  # 558
VERSION_V1 = 0x010600
PATH_CHARS = 261


def _get24bint(data: bytes, offset: int) -> int:
    """Read a 3-byte big-endian integer."""
    return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]


def _u24be(val: int) -> bytes:
    return int(val & 0xFFFFFF).to_bytes(3, "big")


def filetype_from_description(filetype: str) -> int:
    """libgpod's convert_filetype: 2 for AAC/MP4/M4A, 4 for WAV, else 1 (MP3)."""
    if any(k in (filetype or "") for k in ("AAC", "MP4", "M4A", "aac", "mp4", "m4a")):
        return 2
    if any(k in (filetype or "") for k in ("WAV", "wav")):
        return 4
    return 1


def _put24bint(buf: bytearray, offset: int, val: int) -> None:
    """Write a 3-byte big-endian integer."""
    buf[offset] = (val >> 16) & 0xFF
    buf[offset + 1] = (val >> 8) & 0xFF
    buf[offset + 2] = val & 0xFF


class ITunesSDTrack(NamedTuple):
    """A track entry in iTunesSD."""

    start_pos_ms: int  # stored in 256 ms units
    stop_pos_ms: int
    volume: int  # 0-200 (100 = unchanged)
    file_type: int  # 1=MP3, 2=AAC, 4=WAV
    filename: str  # iPod path; written with "/" separators
    shuffle_flag: int
    bookmark_flag: int


class ITunesSD:
    """Parsed iTunesSD database (shuffle 1G/2G)."""

    def __init__(self) -> None:
        self.tracks: List[ITunesSDTrack] = []
        self.db_version: int = VERSION_V1
        self.raw_header: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "ITunesSD":
        """Parse an iTunesSD file (entries that do not fit are ignored)."""
        sd = cls()
        logger.debug("Parsing iTunesSD: %d bytes", len(data))
        if len(data) < HEADER_SIZE:
            return sd
        num_songs = _get24bint(data, 0)
        sd.db_version = _get24bint(data, 3)
        header_size = _get24bint(data, 6) or HEADER_SIZE
        sd.raw_header = bytes(data[:header_size])
        pos = header_size
        for _ in range(num_songs):
            entry_size = _get24bint(data, pos) if pos + 3 <= len(data) else 0
            if entry_size < ENTRY_SIZE or pos + ENTRY_SIZE > len(data):
                break
            raw_path = data[pos + 33 : pos + 33 + 2 * PATH_CHARS]
            try:
                filename = raw_path.decode("utf-16-le").split("\x00", 1)[0]
            except UnicodeDecodeError:
                logger.debug("iTunesSD: undecodable path at offset %d", pos + 33)
                filename = ""
            sd.tracks.append(
                ITunesSDTrack(
                    start_pos_ms=_get24bint(data, pos + 6) * 256,
                    stop_pos_ms=_get24bint(data, pos + 15) * 256,
                    volume=_get24bint(data, pos + 24),
                    file_type=_get24bint(data, pos + 27),
                    filename=filename,
                    shuffle_flag=data[pos + 555],
                    bookmark_flag=data[pos + 556],
                )
            )
            pos += entry_size
        return sd

    def write(self) -> bytes:
        """Serialize to the iTunesSD format libgpod writes for shuffle 1G/2G."""
        logger.debug("Writing iTunesSD: %d tracks", len(self.tracks))
        out = bytearray()
        for value in (len(self.tracks), self.db_version, HEADER_SIZE, 0, 0, 0):
            out += _u24be(value)
        for t in self.tracks:
            entry = bytearray()
            for value in (
                ENTRY_SIZE,
                0x5AA501,
                t.start_pos_ms // 256,
                0,
                0,
                t.stop_pos_ms // 256,
                0,
                0,
                t.volume,
                t.file_type,
                0x200,
            ):
                entry += _u24be(value)
            path = t.filename.replace(":", "/").encode("utf-16-le")[: 2 * PATH_CHARS]
            entry += path + b"\x00" * (2 * PATH_CHARS - len(path))
            entry += bytes([t.shuffle_flag & 0xFF, t.bookmark_flag & 0xFF, 0])
            out += entry
        return bytes(out)

    @classmethod
    def from_tracks(cls, tracks: List[Any]) -> "ITunesSD":
        """Build the iTunesSD for database tracks (in database order), as libgpod does."""
        sd = cls()
        for tr in tracks:
            sd.tracks.append(
                ITunesSDTrack(
                    start_pos_ms=tr.start_time,
                    stop_pos_ms=tr.stop_time,
                    volume=((tr.volume + 255) * 201) // 511,
                    file_type=filetype_from_description(tr.filetype_str),
                    filename=tr.ipod_path,
                    shuffle_flag=int(bool(tr.skip_when_shuffling)),
                    bookmark_flag=int(bool(tr.remember_position)),
                )
            )
        return sd

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
