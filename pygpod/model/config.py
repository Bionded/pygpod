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

    Defaults match libgpod for maximum device compatibility. The fields most callers change:

    - ``otg_on_save``: what save() does with On-The-Go playlists made on the iPod:
      "import" (default, like iTunes and libgpod: playlists "OTG Playlist n", files removed),
      "keep" (files kept, positions rewritten) or "clear" (files removed).
    - ``timestamps``: "local" (default, the iPod's local time like iTunes and libgpod) or
      "utc" (pygpod 0.1.0's behaviour).
    - ``random_seed``: an int makes database/track/playlist ids and file names reproducible.
    - ``master_playlist_name``: name of the library playlist in a new database ("iPod").
    - ``set_time_fields_zero``: leave time_added/time_modified of new tracks 0 instead of now.

    The other fields are header values of the binary format, set to what libgpod writes.
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

    # -- Timestamp zone --
    # "local": iTunesDB times are the iPod's local wall time, like libgpod and iTunes: the
    #   timezone from iPod_Control/Device/Preferences, else the host's current UTC offset
    #   (Database.timezone_shift). "utc": read and write them as UTC (pygpod 0.1.0).
    timestamps: str = "local"

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

    # -- On-The-Go playlists made on the iPod, when save() writes a new database --
    # "import" (default): import them as playlists ("OTG Playlist n") and remove the files,
    #   like iTunes and libgpod (which imports on parse and removes OTGPlaylistInfo on write);
    #   the iPod's On-The-Go list is empty afterwards
    # "keep": the OTG files stay; their entries (track positions) are rewritten to the
    #   tracks' new positions when the save moved them (entries of removed tracks dropped).
    #   The iPod Classic firmware empties such a list anyway once the iTunesDB changed.
    # "clear": remove the files without importing
    # import_otg_playlists() / clear_otg_playlists() called explicitly always win.
    otg_on_save: str = "import"
