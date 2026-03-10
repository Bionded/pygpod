"""DatabaseConfig - configurable parameters for iPod database generation.

Defaults match libgpod's output for maximum device compatibility,
except filenames which use pygpod's own style.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class DatabaseConfig:
    """Configuration for iPod database generation.

    Defaults match libgpod for maximum device compatibility.
    Only filenames differ (pygpod prefix + alphanumeric).
    """

    # -- String encoding --
    # 1 = UTF-16LE (libgpod default, iPod-compatible)
    string_encoding: int = 1

    # -- MHOD ordering per track --
    # libgpod order: title, artist, album, filetype, path, genre
    # None means "use whatever order add_track produces".
    mhod_order: Optional[Tuple[int, ...]] = None

    # -- MHIT unknown header fields (libgpod values) --
    unk_0x7e: int = 0xFFFF
    unk_0x84: int = 0
    unk_0x90: int = 12
    mark_unplayed: int = 1
    unk_0x12c_from_filesize: bool = True
    unk_0x134_fill: int = 0x80
    unk_0x168: int = 1

    # -- Time fields --
    # libgpod leaves time_modified and time_added as 0
    set_time_fields_zero: bool = True

    # -- MHYP (playlist) header fields --
    mhyp_unk_0x28: int = 1
    mhyp_sort_order: int = 1

    # -- Album / artist list generation --
    generate_album_list: bool = True   # MHSD type 4
    generate_artist_list: bool = True  # MHSD type 8

    # -- Chapter data format --
    # "atom" = libgpod's QuickTime atom-box format (sean/chap/name)
    chapter_format: str = "atom"

    # -- SPL prefs body size --
    # libgpod: 72 bytes (14 core + 58 zero-pad)
    spl_prefs_padded_size: int = 72

    # -- Database header defaults --
    master_playlist_name: str = "iPod"
    platform: int = 1  # 1 = Mac
    language: int = 0x656E  # "en" as uint16 LE

    # -- File naming --
    # pygpod style filenames (only difference from libgpod defaults)
    filename_prefix: str = "pygpod"
    filename_rand_len: int = 8
    filename_charset: str = "alnum"  # "alnum" = A-Z0-9

    # -- Track ID start --
    # libgpod starts at 52
    track_id_start: int = 52

    # -- Deterministic mode --
    # Set to an int seed for reproducible random values (DBIDs, playlist IDs).
    # None means use system randomness.
    random_seed: Optional[int] = None

    @classmethod
    def libgpod_compat(cls) -> "DatabaseConfig":
        """Create a config that produces output binary-identical to libgpod.

        Same as defaults except for filenames (libgpod prefix + digits only).
        """
        from ..db.constants import (
            MHOD_ID_ALBUM,
            MHOD_ID_ARTIST,
            MHOD_ID_FILETYPE,
            MHOD_ID_GENRE,
            MHOD_ID_PATH,
            MHOD_ID_TITLE,
        )

        return cls(
            mhod_order=(
                MHOD_ID_TITLE,  # 1
                MHOD_ID_ARTIST,  # 4
                MHOD_ID_ALBUM,  # 3
                MHOD_ID_FILETYPE,  # 6
                MHOD_ID_PATH,  # 2
                MHOD_ID_GENRE,  # 5
            ),
            filename_prefix="libgpod",
            filename_rand_len=6,
            filename_charset="digits",
        )
