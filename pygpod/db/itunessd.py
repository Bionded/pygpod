"""iTunesSD parser and writer for the iPod shuffle (1G/2G: version 1, 3G/4G: version 2).

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

Shuffle 3G/4G (shadow DB version 2, bdhs/hths/rths/hphs/lphs): see ITunesSDV2 below.
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


# ---------------------------------------------------------------------------
# Shadow DB version 2: iPod shuffle 3G/4G (libgpod 0.8.3 write_bdhs/hths/rths/hphs/lphs).
# Little-endian. bdhs (64-byte header) -> hths (track list: offsets) -> rths (372 bytes
# per track) -> hphs (playlist list: offsets) -> lphs (per non-empty playlist: indexes
# into the track list). Voiceover is announced as on, volume limit off, as libgpod does.
# ---------------------------------------------------------------------------

V2_MAGIC_VERSION = 0x02000003
RTHS_SIZE = 0x174  # 372
V2_PATH_BYTES = 256
PLAYLIST_MASTER, PLAYLIST_NORMAL, PLAYLIST_PODCAST, PLAYLIST_AUDIOBOOK = 1, 2, 3, 4
_PODCAST, _AUDIOBOOK = 0x04, 0x08  # media types not counted as "standard"


class ITunesSDV2Track(NamedTuple):
    """A track record (rths) of the shuffle 3G/4G iTunesSD."""

    start_ms: int
    stop_ms: int
    volume: int  # iTunesDB value, -255..255
    file_type: int  # 1 MP3, 2 AAC, 4 WAV
    path: str  # "/iPod_Control/Music/F00/x.mp3"
    bookmark_ms: int
    shuffle_flag: int  # libgpod writes !skip_when_shuffling (1 for normal songs)
    remember_position: int
    pregap: int
    postgap: int
    sample_count: int
    gapless_data: int
    album_id: int
    track_number: int
    cd_number: int
    dbid: int
    artist_id: int
    media_type: int = 1  # not stored; counts podcasts/audiobooks in the headers


class ITunesSDV2Playlist(NamedTuple):
    """A playlist record (lphs): its type, id and members as indexes into the track list."""

    kind: int  # PLAYLIST_MASTER / NORMAL / PODCAST / AUDIOBOOK
    playlist_id: int  # 0 for the master playlist
    track_indexes: List[int]


class ITunesSDV2:
    """iTunesSD of the iPod shuffle 3G/4G (shadow DB version 2)."""

    def __init__(self) -> None:
        self.tracks: List[ITunesSDV2Track] = []
        self.playlists: List[ITunesSDV2Playlist] = []
        self.voiceover = 1
        self.max_volume = 0

    def _nonstd(self, indexes) -> int:
        return sum(1 for i in indexes if self.tracks[i].media_type & (_PODCAST | _AUDIOBOOK))

    def write(self) -> bytes:
        """Serialize exactly as libgpod 0.8.3 does."""
        import struct

        playlists = [p for p in self.playlists if p.track_indexes]
        n = len(self.tracks)
        hths = 0x40
        hths_len = 20 + 4 * n
        first_track = hths + hths_len
        hphs = first_track + n * RTHS_SIZE
        hphs_len = 20 + 4 * len(playlists)
        nonstd_tracks = self._nonstd(range(n))

        out = bytearray()
        out += b"bdhs" + struct.pack("<III", V2_MAGIC_VERSION, 0x40, n)
        out += struct.pack("<I8x", len(playlists))
        out += struct.pack("<BBH", self.max_volume, self.voiceover, 0)
        out += struct.pack("<III20x", n - nonstd_tracks, hths, hphs)

        out += b"hths" + struct.pack("<II8x", hths_len, n)
        out += b"".join(struct.pack("<I", first_track + i * RTHS_SIZE) for i in range(n))
        for t in self.tracks:
            path = t.path.replace(":", "/").encode("utf-8")[:V2_PATH_BYTES]
            rec = b"rths" + struct.pack(
                "<IIIiI", RTHS_SIZE, t.start_ms, t.stop_ms, t.volume, t.file_type
            )
            rec += path + b"\x00" * (V2_PATH_BYTES - len(path))
            rec += struct.pack(
                "<IBBBBIII4xI4xIHH8xQI32x",
                t.bookmark_ms,
                t.shuffle_flag,
                t.remember_position,
                0,  # gapless album (libgpod: 0)
                0,
                t.pregap,
                t.postgap,
                t.sample_count & 0xFFFFFFFF,
                t.gapless_data,
                t.album_id,
                t.track_number & 0xFFFF,
                t.cd_number & 0xFFFF,
                t.dbid,
                t.artist_id,
            )
            out += rec

        kinds = [p.kind for p in playlists]
        out += b"hphs" + struct.pack(
            "<IHHHHHH",
            hphs_len,
            len(playlists),
            0,
            len(playlists) - kinds.count(PLAYLIST_PODCAST),
            kinds.count(PLAYLIST_MASTER),
            len(playlists) - kinds.count(PLAYLIST_AUDIOBOOK),
            0,
        )
        pos = hphs + hphs_len
        offsets = []
        for p in playlists:
            offsets.append(pos)
            pos += 44 + 4 * len(p.track_indexes)
        out += b"".join(struct.pack("<I", o) for o in offsets)
        for p in playlists:
            m = len(p.track_indexes)
            out += b"lphs" + struct.pack(
                "<IIIQI16x", 44 + 4 * m, m, m - self._nonstd(p.track_indexes),
                p.playlist_id if p.kind != PLAYLIST_MASTER else 0, p.kind,
            )  # fmt: skip
            out += b"".join(struct.pack("<I", i) for i in p.track_indexes)
        return bytes(out)

    @classmethod
    def parse(cls, data: bytes) -> "ITunesSDV2":
        """Parse a shuffle 3G/4G iTunesSD (media types are not stored: podcasts and
        audiobooks are not told apart from music)."""
        import struct

        from ..exceptions import ParseError

        if len(data) < 0x40 or data[:4] != b"bdhs":
            raise ParseError("not a shuffle 3G/4G iTunesSD (no bdhs header)")
        sd = cls()
        n = struct.unpack_from("<I", data, 12)[0]
        sd.max_volume, sd.voiceover = data[28], data[29]
        hths, hphs = struct.unpack_from("<II", data, 36)
        if data[hths : hths + 4] != b"hths" or data[hphs : hphs + 4] != b"hphs":
            raise ParseError("iTunesSD: bad hths/hphs offsets")
        for i in range(n):
            off = struct.unpack_from("<I", data, hths + 20 + 4 * i)[0]
            if data[off : off + 4] != b"rths":
                raise ParseError(f"iTunesSD: no rths at {off}")
            start, stop, volume, ftype = struct.unpack_from("<IIiI", data, off + 8)
            raw = data[off + 24 : off + 24 + V2_PATH_BYTES]
            path = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            v = struct.unpack_from("<IBBBBIII4xI4xIHH8xQI", data, off + 24 + V2_PATH_BYTES)
            sd.tracks.append(
                ITunesSDV2Track(
                    start,
                    stop,
                    volume,
                    ftype,
                    path,
                    v[0],
                    v[1],
                    v[2],
                    v[5],
                    v[6],
                    v[7],
                    v[8],
                    v[9],
                    v[10],
                    v[11],
                    v[12],
                    v[13],
                )  # fmt: skip
            )
        count = struct.unpack_from("<H", data, hphs + 8)[0]
        for i in range(count):
            off = struct.unpack_from("<I", data, hphs + 20 + 4 * i)[0]
            if data[off : off + 4] != b"lphs":
                raise ParseError(f"iTunesSD: no lphs at {off}")
            m = struct.unpack_from("<I", data, off + 8)[0]
            pid, kind = struct.unpack_from("<QI", data, off + 16)
            idx = list(struct.unpack_from(f"<{m}I", data, off + 44)) if m else []
            sd.playlists.append(ITunesSDV2Playlist(kind, pid, idx))
        return sd

    @classmethod
    def from_database(cls, tracks: List[Any], playlists: List[Any]) -> "ITunesSDV2":
        """Build it from database tracks (database order) and playlists, after the save
        fixups have set the album/artist ids (MHIT 0x120 / 0x1E0) like libgpod's
        prepare_itdb_for_write."""
        import struct

        sd = cls()
        index = {}
        for i, tr in enumerate(tracks):
            index[tr.track_id] = i
            header = tr.record.raw_header if tr.record is not None else b""
            album_id = struct.unpack_from("<I", header, 0x120)[0] if len(header) >= 0x124 else 0
            artist_id = struct.unpack_from("<I", header, 0x1E0)[0] if len(header) >= 0x1E4 else 0
            sd.tracks.append(
                ITunesSDV2Track(
                    start_ms=tr.start_time,
                    stop_ms=tr.stop_time,
                    volume=tr.volume,
                    file_type=filetype_from_description(tr.filetype_str),
                    path=tr.ipod_path,
                    bookmark_ms=tr.bookmark_time,
                    shuffle_flag=0 if tr.skip_when_shuffling else 1,
                    remember_position=int(bool(tr.remember_position)),
                    pregap=tr.pregap,
                    postgap=tr.postgap,
                    sample_count=tr.sample_count,
                    gapless_data=tr.gapless_data,
                    album_id=album_id,
                    track_number=tr.track_number,
                    cd_number=tr.cd_number,
                    dbid=tr.dbid,
                    artist_id=artist_id,
                    media_type=tr.media_type,
                )
            )
        for pl in playlists:
            if pl.is_master:
                kind = PLAYLIST_MASTER
            elif pl.is_podcast:
                kind = PLAYLIST_PODCAST
            else:
                kind = PLAYLIST_NORMAL
            idx = [index[tid] for tid in pl.track_ids if tid in index]
            sd.playlists.append(ITunesSDV2Playlist(kind, pl.playlist_id, idx))
        return sd
