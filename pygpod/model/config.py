"""DatabaseConfig - configurable parameters for iPod database generation.

Defaults match libgpod's output for maximum device compatibility.
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
    """

    # -- String encoding --
    # 1 = UTF-16LE (libgpod default, iPod-compatible)
    string_encoding: int = 1

    # -- MHOD ordering per track --
    # libgpod order: title, artist, album, filetype, path, genre
    # None means "use whatever order add_track produces".
    mhod_order: Optional[Tuple[int, ...]] = (1, 4, 3, 6, 2, 5)

    # -- MHIT unknown header fields (libgpod values) --
    unk_0x7e: int = 0xFFFF
    unk_0x84: int = 0
    unk_0x90: int = 12
    mark_unplayed: int = 1
    unk_0x12c_from_filesize: bool = True
    unk_0x134_fill: int = 0x80
    unk_0x168: int = 1

    # -- Time fields --
    # When False, time_modified and time_added are set to current time.
    # When True, they are left as 0 (original libgpod behavior).
    set_time_fields_zero: bool = False

    # -- MHYP (playlist) header fields --
    mhyp_unk_0x28: int = 1
    mhyp_sort_order: int = 1

    # -- Album / artist list generation --
    generate_album_list: bool = True  # MHSD type 4
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
    # libgpod style: "pygpod" prefix + 6 digits
    filename_prefix: str = "pygpod"
    filename_rand_len: int = 6
    filename_charset: str = "digits"

    # -- Track ID start --
    # libgpod starts at 52
    track_id_start: int = 52

    # -- Deterministic mode --
    # Set to an int seed for reproducible random values (DBIDs, playlist IDs).
    # None means use system randomness.
    random_seed: Optional[int] = None
