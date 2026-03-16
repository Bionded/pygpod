"""Playlist model - high-level representation of an iPod playlist."""

from __future__ import annotations

from typing import List, Optional

from ..db.constants import MHIP_MAGIC, MHOD_ID_TITLE, MHOD_MAGIC
from ..db.parser import Record
from .track import Track


class Playlist:
    """High-level representation of an iPod playlist.

    The ``name`` property has a setter. Structural properties like
    ``playlist_id`` and ``is_master`` are read-only.
    """

    def __init__(
        self, record: Optional[Record] = None, track_lookup: Optional[dict] = None
    ) -> None:
        self._record = record
        self._track_lookup = track_lookup or {}  # track_id -> Track

    @classmethod
    def from_record(cls, record: Record, track_lookup: Optional[dict] = None) -> "Playlist":
        """Create a Playlist from a parsed MHYP Record."""
        return cls(record, track_lookup)

    @property
    def record(self) -> Optional[Record]:
        return self._record

    @property
    def name(self) -> str:
        """Playlist name."""
        if self._record:
            return self._record.get_mhod(MHOD_ID_TITLE) or ""
        return ""

    @name.setter
    def name(self, value: str) -> None:
        """Rename the playlist."""
        if not self._record:
            return
        from ..db.writer import make_string_mhod

        self._record.children = [
            c
            for c in self._record.children
            if not (c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_TITLE)
        ]
        if value:
            self._record.children.insert(0, make_string_mhod(MHOD_ID_TITLE, value))

    @property
    def playlist_id(self) -> int:
        if self._record:
            return self._record.fields.get("playlist_id", 0)
        return 0

    @property
    def is_master(self) -> bool:
        """Whether this is the master (library) playlist."""
        if self._record:
            return self._record.fields.get("playlist_type", 0) == 1
        return False

    @property
    def is_podcast(self) -> bool:
        """Whether this is a podcast playlist."""
        if self._record:
            return bool(self._record.fields.get("podcast_flag", 0))
        return False

    @property
    def is_smart(self) -> bool:
        """Whether this is a smart playlist (has SPL rules/prefs)."""
        if not self._record:
            return False
        for child in self._record.children:
            if child.magic == b"mhod":
                mhod_type = child.fields.get("mhod_type", 0)
                if mhod_type in (50, 51):
                    return True
        return False

    @property
    def sort_order(self) -> int:
        if self._record:
            return self._record.fields.get("sort_order", 0)
        return 0

    @property
    def track_ids(self) -> List[int]:
        """List of track IDs in this playlist."""
        if not self._record:
            return []
        return [c.fields["track_id"] for c in self._record.children if c.magic == MHIP_MAGIC]

    @property
    def tracks(self) -> List[Track]:
        """List of Track objects in this playlist."""
        result = []
        for tid in self.track_ids:
            track = self._track_lookup.get(tid)
            if track:
                result.append(track)
        return result

    @property
    def track_count(self) -> int:
        return len(self.track_ids)

    def __repr__(self) -> str:
        return f"<Playlist '{self.name}' ({self.track_count} tracks)>"

    def __str__(self) -> str:
        return f"{self.name} ({self.track_count} tracks)"

    def __len__(self) -> int:
        return self.track_count

    def __iter__(self):
        return iter(self.tracks)
