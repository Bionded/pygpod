"""Track model - high-level representation of an iPod track.

Covers all 100+ fields from libgpod's Itdb_Track struct.
String properties (title, artist, etc.) and safe numeric properties
(rating, play_count, etc.) have setters. Structural fields (track_id,
dbid, path, filetype) are read-only.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from ..db.constants import (
    MEDIATYPE_AUDIO,
    MHOD_ID_ALBUM,
    MHOD_ID_ALBUMARTIST,
    MHOD_ID_ARTIST,
    MHOD_ID_CATEGORY,
    MHOD_ID_COMMENT,
    MHOD_ID_COMPOSER,
    MHOD_ID_DESCRIPTION,
    MHOD_ID_FILETYPE,
    MHOD_ID_GENRE,
    MHOD_ID_GROUPING,
    MHOD_ID_KEYWORDS,
    MHOD_ID_PATH,
    MHOD_ID_PODCASTRSS,
    MHOD_ID_PODCASTURL,
    MHOD_ID_SORT_ALBUM,
    MHOD_ID_SORT_ALBUMARTIST,
    MHOD_ID_SORT_ARTIST,
    MHOD_ID_SORT_COMPOSER,
    MHOD_ID_SORT_TITLE,
    MHOD_ID_SORT_TVSHOW,
    MHOD_ID_SUBTITLE,
    MHOD_ID_TITLE,
    MHOD_ID_TVEPISODE,
    MHOD_ID_TVNETWORK,
    MHOD_ID_TVSHOW,
    MHOD_MAGIC,
)
from ..db.parser import Record
from ..utils.datetime import mac_to_datetime

logger = logging.getLogger(__name__)


class Track:
    """High-level representation of an iPod track.

    Wraps a parsed MHIT Record with Pythonic property access.
    String and safe numeric fields have setters.
    """

    def __init__(self, record: Optional[Record] = None) -> None:
        self._record = record

    @classmethod
    def from_record(cls, record: Record) -> "Track":
        """Create a Track from a parsed MHIT Record."""
        track_id = record.fields.get("track_id", 0)
        logger.debug("Track from record: id=%d", track_id)
        return cls(record)

    @property
    def record(self) -> Optional[Record]:
        """Underlying MHIT Record."""
        return self._record

    def _get_field(self, key: str, default: Any = 0) -> Any:
        if self._record:
            return self._record.fields.get(key, default)
        return default

    def _get_mhod(self, mhod_type: int) -> Optional[str]:
        if self._record:
            return self._record.get_mhod(mhod_type)
        return None

    def _set_mhod(self, mhod_type: int, value: str) -> None:
        """Replace or create an MHOD string child on the record."""
        if not self._record:
            return
        from ..db.writer import make_string_mhod

        self._record.children = [
            c
            for c in self._record.children
            if not (c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == mhod_type)
        ]
        if value:
            self._record.children.append(make_string_mhod(mhod_type, value))

    def _set_header_u8(self, offset: int, value: int) -> None:
        """Set a uint8 in the MHIT raw header."""
        if not self._record:
            return
        from ..utils.compat import put8int

        header = bytearray(self._record.raw_header)
        if len(header) > offset:
            put8int(header, offset, value)
            self._record.raw_header = bytes(header)

    def _set_header_u16(self, offset: int, value: int) -> None:
        """Set a uint16 LE in the MHIT raw header."""
        if not self._record:
            return
        from ..utils.compat import put16lint

        header = bytearray(self._record.raw_header)
        if len(header) > offset + 1:
            put16lint(header, offset, value)
            self._record.raw_header = bytes(header)

    def _set_header_u32(self, offset: int, value: int) -> None:
        """Set a uint32 LE in the MHIT raw header."""
        if not self._record:
            return
        from ..utils.compat import put32lint

        header = bytearray(self._record.raw_header)
        if len(header) > offset + 3:
            put32lint(header, offset, value)
            self._record.raw_header = bytes(header)

    def _set_field_and_header_u32(self, key: str, offset: int, value: int) -> None:
        if self._record:
            self._record.fields[key] = value
        self._set_header_u32(offset, value)

    def _set_field_and_header_u8(self, key: str, offset: int, value: int) -> None:
        if self._record:
            self._record.fields[key] = value
        self._set_header_u8(offset, value)

    # ========================================================================
    # String properties (editable via MHOD replacement)
    # ========================================================================

    @property
    def title(self) -> str:
        return self._get_mhod(MHOD_ID_TITLE) or ""

    @title.setter
    def title(self, value: str) -> None:
        self._set_mhod(MHOD_ID_TITLE, value)

    @property
    def artist(self) -> str:
        return self._get_mhod(MHOD_ID_ARTIST) or ""

    @artist.setter
    def artist(self, value: str) -> None:
        self._set_mhod(MHOD_ID_ARTIST, value)

    @property
    def album(self) -> str:
        return self._get_mhod(MHOD_ID_ALBUM) or ""

    @album.setter
    def album(self, value: str) -> None:
        self._set_mhod(MHOD_ID_ALBUM, value)

    @property
    def genre(self) -> str:
        return self._get_mhod(MHOD_ID_GENRE) or ""

    @genre.setter
    def genre(self, value: str) -> None:
        self._set_mhod(MHOD_ID_GENRE, value)

    @property
    def composer(self) -> str:
        return self._get_mhod(MHOD_ID_COMPOSER) or ""

    @composer.setter
    def composer(self, value: str) -> None:
        self._set_mhod(MHOD_ID_COMPOSER, value)

    @property
    def comment(self) -> str:
        return self._get_mhod(MHOD_ID_COMMENT) or ""

    @comment.setter
    def comment(self, value: str) -> None:
        self._set_mhod(MHOD_ID_COMMENT, value)

    @property
    def albumartist(self) -> str:
        return self._get_mhod(MHOD_ID_ALBUMARTIST) or ""

    @albumartist.setter
    def albumartist(self, value: str) -> None:
        self._set_mhod(MHOD_ID_ALBUMARTIST, value)

    @property
    def grouping(self) -> str:
        return self._get_mhod(MHOD_ID_GROUPING) or ""

    @grouping.setter
    def grouping(self, value: str) -> None:
        self._set_mhod(MHOD_ID_GROUPING, value)

    @property
    def description(self) -> str:
        return self._get_mhod(MHOD_ID_DESCRIPTION) or ""

    @description.setter
    def description(self, value: str) -> None:
        self._set_mhod(MHOD_ID_DESCRIPTION, value)

    @property
    def subtitle(self) -> str:
        return self._get_mhod(MHOD_ID_SUBTITLE) or ""

    @subtitle.setter
    def subtitle(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SUBTITLE, value)

    @property
    def podcast_url(self) -> str:
        return self._get_mhod(MHOD_ID_PODCASTURL) or ""

    @podcast_url.setter
    def podcast_url(self, value: str) -> None:
        self._set_mhod(MHOD_ID_PODCASTURL, value)

    @property
    def podcast_rss(self) -> str:
        return self._get_mhod(MHOD_ID_PODCASTRSS) or ""

    @podcast_rss.setter
    def podcast_rss(self, value: str) -> None:
        self._set_mhod(MHOD_ID_PODCASTRSS, value)

    @property
    def keywords(self) -> str:
        return self._get_mhod(MHOD_ID_KEYWORDS) or ""

    @keywords.setter
    def keywords(self, value: str) -> None:
        self._set_mhod(MHOD_ID_KEYWORDS, value)

    @property
    def category(self) -> str:
        return self._get_mhod(MHOD_ID_CATEGORY) or ""

    @category.setter
    def category(self, value: str) -> None:
        self._set_mhod(MHOD_ID_CATEGORY, value)

    @property
    def tvshow(self) -> str:
        return self._get_mhod(MHOD_ID_TVSHOW) or ""

    @tvshow.setter
    def tvshow(self, value: str) -> None:
        self._set_mhod(MHOD_ID_TVSHOW, value)

    @property
    def tvepisode(self) -> str:
        return self._get_mhod(MHOD_ID_TVEPISODE) or ""

    @tvepisode.setter
    def tvepisode(self, value: str) -> None:
        self._set_mhod(MHOD_ID_TVEPISODE, value)

    @property
    def tvnetwork(self) -> str:
        return self._get_mhod(MHOD_ID_TVNETWORK) or ""

    @tvnetwork.setter
    def tvnetwork(self, value: str) -> None:
        self._set_mhod(MHOD_ID_TVNETWORK, value)

    # ---- Sort string properties (editable) ----

    @property
    def sort_title(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_TITLE) or ""

    @sort_title.setter
    def sort_title(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SORT_TITLE, value)

    @property
    def sort_artist(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_ARTIST) or ""

    @sort_artist.setter
    def sort_artist(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SORT_ARTIST, value)

    @property
    def sort_album(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_ALBUM) or ""

    @sort_album.setter
    def sort_album(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SORT_ALBUM, value)

    @property
    def sort_albumartist(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_ALBUMARTIST) or ""

    @sort_albumartist.setter
    def sort_albumartist(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SORT_ALBUMARTIST, value)

    @property
    def sort_composer(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_COMPOSER) or ""

    @sort_composer.setter
    def sort_composer(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SORT_COMPOSER, value)

    @property
    def sort_tvshow(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_TVSHOW) or ""

    @sort_tvshow.setter
    def sort_tvshow(self, value: str) -> None:
        self._set_mhod(MHOD_ID_SORT_TVSHOW, value)

    # ========================================================================
    # Editable numeric properties (MHIT header fields)
    # ========================================================================

    # -- Offsets from _create_mhit in database.py --
    # 0x10: track_id (read-only)
    # 0x14: visible
    # 0x18: filetype_marker (read-only)
    # 0x1C: type1 (read-only, derived from media_type)
    # 0x1D: type2 (read-only, derived from media_type)
    # 0x1E: compilation
    # 0x20: time_modified
    # 0x24: file_size (read-only)
    # 0x28: tracklen/duration_ms
    # 0x2C: track_number
    # 0x30: total_tracks
    # 0x34: year
    # 0x38: bitrate (read-only, from file)
    # 0x3C: samplerate (read-only, from file)
    # 0x5C: cd_number
    # 0x60: total_cds
    # 0x70: dbid (read-only)
    # 0xA5: skip_when_shuffling
    # 0xA6: remember_position
    # 0xA7: flag4
    # 0xB2: mark_unplayed
    # 0xD0: media_type

    @property
    def track_number(self) -> int:
        return self._get_field("track_number")

    @track_number.setter
    def track_number(self, value: int) -> None:
        self._set_field_and_header_u32("track_number", 0x2C, value)

    @property
    def total_tracks(self) -> int:
        return self._get_field("total_tracks")

    @total_tracks.setter
    def total_tracks(self, value: int) -> None:
        self._set_field_and_header_u32("total_tracks", 0x30, value)

    @property
    def year(self) -> int:
        return self._get_field("year")

    @year.setter
    def year(self, value: int) -> None:
        self._set_field_and_header_u32("year", 0x34, value)

    @property
    def cd_number(self) -> int:
        return self._get_field("cd_number")

    @cd_number.setter
    def cd_number(self, value: int) -> None:
        self._set_field_and_header_u32("cd_number", 0x5C, value)

    @property
    def total_cds(self) -> int:
        return self._get_field("total_cds")

    @total_cds.setter
    def total_cds(self, value: int) -> None:
        self._set_field_and_header_u32("total_cds", 0x60, value)

    @property
    def play_count(self) -> int:
        return self._get_field("play_count")

    @play_count.setter
    def play_count(self, value: int) -> None:
        self._set_field_and_header_u32("play_count", 0x50, value)

    @property
    def rating(self) -> int:
        """Rating 0-100 (each star = 20)."""
        return self._get_field("rating")

    @rating.setter
    def rating(self, value: int) -> None:
        self._set_field_and_header_u8("rating", 0x1F, max(0, min(100, value)))

    @property
    def rating_stars(self) -> int:
        """Rating as 0-5 stars."""
        return self.rating // 20

    @rating_stars.setter
    def rating_stars(self, value: int) -> None:
        self.rating = max(0, min(5, value)) * 20

    @property
    def bpm(self) -> int:
        return self._get_field("bpm")

    @bpm.setter
    def bpm(self, value: int) -> None:
        self._set_field_and_header_u16("bpm", 0x7A, value)

    def _set_field_and_header_u16(self, key: str, offset: int, value: int) -> None:
        if self._record:
            self._record.fields[key] = value
        self._set_header_u16(offset, value)

    @property
    def media_type(self) -> int:
        return self._get_field("media_type", MEDIATYPE_AUDIO)

    @media_type.setter
    def media_type(self, value: int) -> None:
        from ..db.constants import (
            MEDIATYPE_AUDIOBOOK,
            MEDIATYPE_MUSICVIDEO,
            MEDIATYPE_PODCAST,
            MEDIATYPE_TVSHOW,
            MEDIATYPE_VIDEO,
        )

        self._set_field_and_header_u32("media_type", 0xD0, value)
        # Also update type1/type2 derived fields
        type1 = 1 if (value & MEDIATYPE_AUDIOBOOK) else 0
        _video_types = MEDIATYPE_VIDEO | MEDIATYPE_MUSICVIDEO | MEDIATYPE_TVSHOW
        type2 = 1 if (value & _video_types) else 0
        self._set_header_u8(0x1C, type1)
        self._set_header_u8(0x1D, type2)
        # Podcast/audiobook flags
        is_pod_or_ab = value & (MEDIATYPE_PODCAST | MEDIATYPE_AUDIOBOOK)
        if is_pod_or_ab:
            self._set_header_u8(0xA5, 1)  # skip_when_shuffling
            self._set_header_u8(0xA6, 1)  # remember_position
            self._set_header_u8(0xA7, 1)  # flag4
        if self._record:
            self._record.fields["type1"] = type1
            self._record.fields["type2"] = type2

    @property
    def compilation(self) -> bool:
        return bool(self._get_field("compilation"))

    @compilation.setter
    def compilation(self, value: bool) -> None:
        self._set_field_and_header_u8("compilation", 0x1E, 1 if value else 0)

    @property
    def skip_when_shuffling(self) -> bool:
        return bool(self._get_field("skip_when_shuffling", 0))

    @skip_when_shuffling.setter
    def skip_when_shuffling(self, value: bool) -> None:
        self._set_field_and_header_u8("skip_when_shuffling", 0xA5, 1 if value else 0)

    @property
    def remember_position(self) -> bool:
        """Remember playback position (audiobooks/podcasts)."""
        return bool(self._get_field("remember_position", 0))

    @remember_position.setter
    def remember_position(self, value: bool) -> None:
        self._set_field_and_header_u8("remember_position", 0xA6, 1 if value else 0)

    @property
    def mark_unplayed(self) -> bool:
        return bool(self._get_field("mark_unplayed", 0))

    @mark_unplayed.setter
    def mark_unplayed(self, value: bool) -> None:
        self._set_field_and_header_u8("mark_unplayed", 0xB2, 0x02 if value else 0x01)

    @property
    def season_number(self) -> int:
        return self._get_field("season_number", 0)

    @season_number.setter
    def season_number(self, value: int) -> None:
        self._set_field_and_header_u32("season_number", 0xD4, value)

    @property
    def episode_number(self) -> int:
        return self._get_field("episode_number", 0)

    @episode_number.setter
    def episode_number(self, value: int) -> None:
        self._set_field_and_header_u32("episode_number", 0xD8, value)

    @property
    def volume(self) -> int:
        """Volume adjustment (-255 to +255)."""
        return self._get_field("volume", 0)

    @volume.setter
    def volume(self, value: int) -> None:
        self._set_field_and_header_u32("volume", 0x40, max(-255, min(255, value)))

    @property
    def start_time(self) -> int:
        """Start time in ms (for partial playback)."""
        return self._get_field("start_time", 0)

    @start_time.setter
    def start_time(self, value: int) -> None:
        self._set_field_and_header_u32("start_time", 0x44, value)

    @property
    def stop_time(self) -> int:
        """Stop time in ms (for partial playback)."""
        return self._get_field("stop_time", 0)

    @stop_time.setter
    def stop_time(self, value: int) -> None:
        self._set_field_and_header_u32("stop_time", 0x48, value)

    # ========================================================================
    # Read-only properties (structural - not safe to change)
    # ========================================================================

    @property
    def track_id(self) -> int:
        return self._get_field("track_id")

    @property
    def dbid(self) -> int:
        return self._get_field("dbid")

    @property
    def dbid2(self) -> int:
        return self._get_field("dbid2", 0)

    @property
    def visible(self) -> int:
        return self._get_field("visible", 1)

    @property
    def filetype_marker(self) -> bytes:
        return self._get_field("filetype_marker", b"\x00\x00\x00\x00")

    @property
    def filetype_str(self) -> str:
        return self._get_mhod(MHOD_ID_FILETYPE) or ""

    @property
    def ipod_path(self) -> str:
        """iPod-style colon-separated file path (read-only)."""
        return self._get_mhod(MHOD_ID_PATH) or ""

    @property
    def type1(self) -> int:
        return self._get_field("type1")

    @property
    def type2(self) -> int:
        return self._get_field("type2")

    @property
    def file_size(self) -> int:
        return self._get_field("file_size")

    @property
    def duration_ms(self) -> int:
        """Track duration in milliseconds."""
        return self._get_field("tracklen")

    @property
    def duration(self) -> float:
        """Track duration in seconds."""
        return self.duration_ms / 1000.0

    @property
    def bitrate(self) -> int:
        """Bitrate in kbps."""
        return self._get_field("bitrate")

    @property
    def samplerate(self) -> int:
        """Sample rate in Hz (extracted from packed format)."""
        raw = self._get_field("samplerate")
        return (raw >> 16) & 0xFFFF

    @property
    def samplerate_float(self) -> float:
        """Sample rate as float (from extended field)."""
        return self._get_field("samplerate_float", 0.0)

    @property
    def soundcheck(self) -> int:
        """Soundcheck value (for volume normalization)."""
        return self._get_field("soundcheck", 0)

    @property
    def play_count2(self) -> int:
        return self._get_field("play_count2", 0)

    @property
    def skip_count(self) -> int:
        return self._get_field("skip_count", 0)

    @property
    def app_rating(self) -> int:
        return self._get_field("app_rating", 0)

    @property
    def checked(self) -> bool:
        return not bool(self._get_field("checked", 0))

    @property
    def drm_userid(self) -> int:
        return self._get_field("drm_userid", 0)

    @property
    def bookmark_time(self) -> int:
        return self._get_field("bookmark_time", 0)

    # ---- Artwork (read-only) ----

    @property
    def artwork_count(self) -> int:
        return self._get_field("artwork_count", 0)

    @property
    def artwork_size(self) -> int:
        return self._get_field("artwork_size", 0)

    @property
    def has_artwork(self) -> bool:
        return bool(self._get_field("has_artwork", 0))

    @property
    def mhii_link(self) -> int:
        return self._get_field("mhii_link", 0)

    # ---- Flags (read-only) ----

    @property
    def lyrics_flag(self) -> bool:
        return bool(self._get_field("lyrics_flag", 0))

    @property
    def movie_flag(self) -> bool:
        return bool(self._get_field("movie_flag", 0))

    @property
    def explicit_flag(self) -> int:
        """0=none, 1=explicit, 2=clean."""
        return self._get_field("explicit_flag", 0)

    # ---- Gapless (read-only) ----

    @property
    def pregap(self) -> int:
        return self._get_field("pregap", 0)

    @property
    def postgap(self) -> int:
        return self._get_field("postgap", 0)

    @property
    def sample_count(self) -> int:
        return self._get_field("sample_count", 0)

    @property
    def gapless_data(self) -> int:
        return self._get_field("gapless_data", 0)

    @property
    def gapless_track_flag(self) -> bool:
        return bool(self._get_field("gapless_track_flag", 0))

    @property
    def gapless_album_flag(self) -> bool:
        return bool(self._get_field("gapless_album_flag", 0))

    # ---- Timestamps (read-only) ----

    @property
    def time_added(self) -> datetime.datetime:
        return mac_to_datetime(self._get_field("time_added"))

    @property
    def time_modified(self) -> datetime.datetime:
        return mac_to_datetime(self._get_field("time_modified"))

    @property
    def time_played(self) -> datetime.datetime:
        return mac_to_datetime(self._get_field("time_played"))

    @property
    def time_released(self) -> datetime.datetime:
        return mac_to_datetime(self._get_field("time_released", 0))

    @property
    def time_skipped(self) -> datetime.datetime:
        return mac_to_datetime(self._get_field("time_skipped", 0))

    # ---- Convenience ----

    @property
    def is_podcast(self) -> bool:
        from ..db.constants import MEDIATYPE_PODCAST

        return bool(self.media_type & MEDIATYPE_PODCAST)

    @property
    def is_audiobook(self) -> bool:
        from ..db.constants import MEDIATYPE_AUDIOBOOK

        return bool(self.media_type & MEDIATYPE_AUDIOBOOK)

    @property
    def is_video(self) -> bool:
        from ..db.constants import MEDIATYPE_MUSICVIDEO, MEDIATYPE_TVSHOW, MEDIATYPE_VIDEO

        return bool(self.media_type & (MEDIATYPE_VIDEO | MEDIATYPE_MUSICVIDEO | MEDIATYPE_TVSHOW))

    def __repr__(self) -> str:
        return f"<Track {self.track_id}: {self.artist} - {self.title}>"

    def __str__(self) -> str:
        parts = []
        if self.artist:
            parts.append(self.artist)
        parts.append(self.title or "(untitled)")
        if self.album:
            parts.append(f"({self.album})")
        return " - ".join(parts)
