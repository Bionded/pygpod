"""Track model - high-level representation of an iPod track.

Covers all 100+ fields from libgpod's Itdb_Track struct.
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
)
from ..db.parser import Record
from ..utils.datetime import mac_to_datetime

logger = logging.getLogger(__name__)


class Track:
    """High-level representation of an iPod track.

    Wraps a parsed MHIT Record with Pythonic property access.
    Provides all 100+ fields available in the iPod database.
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

    # ---- String properties via MHODs ----

    @property
    def title(self) -> str:
        return self._get_mhod(MHOD_ID_TITLE) or ""

    @property
    def artist(self) -> str:
        return self._get_mhod(MHOD_ID_ARTIST) or ""

    @property
    def album(self) -> str:
        return self._get_mhod(MHOD_ID_ALBUM) or ""

    @property
    def genre(self) -> str:
        return self._get_mhod(MHOD_ID_GENRE) or ""

    @property
    def composer(self) -> str:
        return self._get_mhod(MHOD_ID_COMPOSER) or ""

    @property
    def comment(self) -> str:
        return self._get_mhod(MHOD_ID_COMMENT) or ""

    @property
    def albumartist(self) -> str:
        return self._get_mhod(MHOD_ID_ALBUMARTIST) or ""

    @property
    def grouping(self) -> str:
        return self._get_mhod(MHOD_ID_GROUPING) or ""

    @property
    def description(self) -> str:
        return self._get_mhod(MHOD_ID_DESCRIPTION) or ""

    @property
    def filetype_str(self) -> str:
        return self._get_mhod(MHOD_ID_FILETYPE) or ""

    @property
    def ipod_path(self) -> str:
        """iPod-style colon-separated file path."""
        return self._get_mhod(MHOD_ID_PATH) or ""

    @property
    def subtitle(self) -> str:
        return self._get_mhod(MHOD_ID_SUBTITLE) or ""

    @property
    def podcast_url(self) -> str:
        return self._get_mhod(MHOD_ID_PODCASTURL) or ""

    @property
    def podcast_rss(self) -> str:
        return self._get_mhod(MHOD_ID_PODCASTRSS) or ""

    @property
    def keywords(self) -> str:
        return self._get_mhod(MHOD_ID_KEYWORDS) or ""

    @property
    def category(self) -> str:
        return self._get_mhod(MHOD_ID_CATEGORY) or ""

    @property
    def tvshow(self) -> str:
        return self._get_mhod(MHOD_ID_TVSHOW) or ""

    @property
    def tvepisode(self) -> str:
        return self._get_mhod(MHOD_ID_TVEPISODE) or ""

    @property
    def tvnetwork(self) -> str:
        return self._get_mhod(MHOD_ID_TVNETWORK) or ""

    # ---- Sort string properties ----

    @property
    def sort_title(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_TITLE) or ""

    @property
    def sort_artist(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_ARTIST) or ""

    @property
    def sort_album(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_ALBUM) or ""

    @property
    def sort_albumartist(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_ALBUMARTIST) or ""

    @property
    def sort_composer(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_COMPOSER) or ""

    @property
    def sort_tvshow(self) -> str:
        return self._get_mhod(MHOD_ID_SORT_TVSHOW) or ""

    # ---- Core numeric properties from MHIT fields ----

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
    def track_number(self) -> int:
        return self._get_field("track_number")

    @property
    def total_tracks(self) -> int:
        return self._get_field("total_tracks")

    @property
    def cd_number(self) -> int:
        return self._get_field("cd_number")

    @property
    def total_cds(self) -> int:
        return self._get_field("total_cds")

    @property
    def year(self) -> int:
        return self._get_field("year")

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
    def volume(self) -> int:
        """Volume adjustment (-255 to +255)."""
        return self._get_field("volume", 0)

    @property
    def start_time(self) -> int:
        """Start time in ms (for partial playback)."""
        return self._get_field("start_time", 0)

    @property
    def stop_time(self) -> int:
        """Stop time in ms (for partial playback)."""
        return self._get_field("stop_time", 0)

    @property
    def soundcheck(self) -> int:
        """Soundcheck value (for volume normalization)."""
        return self._get_field("soundcheck", 0)

    @property
    def play_count(self) -> int:
        return self._get_field("play_count")

    @property
    def play_count2(self) -> int:
        return self._get_field("play_count2", 0)

    @property
    def skip_count(self) -> int:
        return self._get_field("skip_count", 0)

    @property
    def rating(self) -> int:
        """Rating 0-100 (each star = 20)."""
        return self._get_field("rating")

    @property
    def rating_stars(self) -> int:
        """Rating as 0-5 stars."""
        return self.rating // 20

    @property
    def app_rating(self) -> int:
        """Application rating."""
        return self._get_field("app_rating", 0)

    @property
    def bpm(self) -> int:
        return self._get_field("bpm")

    @property
    def media_type(self) -> int:
        return self._get_field("media_type", MEDIATYPE_AUDIO)

    @property
    def compilation(self) -> bool:
        return bool(self._get_field("compilation"))

    @property
    def checked(self) -> bool:
        """Whether the track is checked (selected) in iTunes."""
        return not bool(self._get_field("checked", 0))

    @property
    def drm_userid(self) -> int:
        return self._get_field("drm_userid", 0)

    @property
    def bookmark_time(self) -> int:
        """Bookmark time in ms (for audiobooks/podcasts)."""
        return self._get_field("bookmark_time", 0)

    # ---- Artwork fields ----

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
        """Link to MHII record in ArtworkDB."""
        return self._get_field("mhii_link", 0)

    # ---- Extended fields (header >= 0xF4) ----

    @property
    def skip_when_shuffling(self) -> bool:
        return bool(self._get_field("skip_when_shuffling", 0))

    @property
    def remember_position(self) -> bool:
        """Remember playback position (audiobooks/podcasts)."""
        return bool(self._get_field("remember_position", 0))

    @property
    def lyrics_flag(self) -> bool:
        return bool(self._get_field("lyrics_flag", 0))

    @property
    def movie_flag(self) -> bool:
        return bool(self._get_field("movie_flag", 0))

    @property
    def mark_unplayed(self) -> bool:
        return bool(self._get_field("mark_unplayed", 0))

    @property
    def explicit_flag(self) -> int:
        """0=none, 1=explicit, 2=clean."""
        return self._get_field("explicit_flag", 0)

    # ---- Gapless playback fields (header >= 0x148) ----

    @property
    def pregap(self) -> int:
        """Number of pregap samples."""
        return self._get_field("pregap", 0)

    @property
    def postgap(self) -> int:
        """Number of postgap samples."""
        return self._get_field("postgap", 0)

    @property
    def sample_count(self) -> int:
        """Total sample count for gapless playback."""
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

    # ---- TV Show / Podcast fields ----

    @property
    def season_number(self) -> int:
        return self._get_field("season_number", 0)

    @property
    def episode_number(self) -> int:
        return self._get_field("episode_number", 0)

    # ---- Timestamp properties ----

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
