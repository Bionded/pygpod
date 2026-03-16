"""Constants for iTunesDB binary format.

All magic bytes, record type enums, field offsets, and MHOD type constants.
"""

from __future__ import annotations

# ============================================================================
# Magic bytes for record types
# ============================================================================
MHBD_MAGIC = b"mhbd"  # Database header
MHSD_MAGIC = b"mhsd"  # Section descriptor
MHLT_MAGIC = b"mhlt"  # Track list
MHIT_MAGIC = b"mhit"  # Track item
MHOD_MAGIC = b"mhod"  # Data object
MHLP_MAGIC = b"mhlp"  # Playlist list
MHYP_MAGIC = b"mhyp"  # Playlist
MHIP_MAGIC = b"mhip"  # Playlist item
MHLA_MAGIC = b"mhla"  # Album list
MHIA_MAGIC = b"mhia"  # Album item
MHBA_MAGIC = b"mhba"  # Album record (podcast?)
MHLI_MAGIC = b"mhli"  # List record (used in MHSD type 8 for artists, ArtworkDB for images)
MHII_MAGIC = b"mhii"  # Item record (used in MHSD type 8 for artists, ArtworkDB for images)

# ============================================================================
# MHSD section types
# ============================================================================
MHSD_TYPE_TRACKS = 1
MHSD_TYPE_PLAYLISTS = 2
MHSD_TYPE_PODCASTS = 3
MHSD_TYPE_ALBUMS = 4
MHSD_TYPE_ARTISTS = 8

# ============================================================================
# MHOD types (data object types)
# ============================================================================
MHOD_ID_TITLE = 1
MHOD_ID_PATH = 2
MHOD_ID_ALBUM = 3
MHOD_ID_ARTIST = 4
MHOD_ID_GENRE = 5
MHOD_ID_FILETYPE = 6
MHOD_ID_EQ_SETTING = 7
MHOD_ID_COMMENT = 8
MHOD_ID_CATEGORY = 9
MHOD_ID_COMPOSER = 12
MHOD_ID_GROUPING = 13
MHOD_ID_DESCRIPTION = 14
MHOD_ID_PODCASTURL = 15
MHOD_ID_PODCASTRSS = 16
MHOD_ID_CHAPTERDATA = 17
MHOD_ID_SUBTITLE = 18
MHOD_ID_TVSHOW = 19
MHOD_ID_TVEPISODE = 20
MHOD_ID_TVNETWORK = 21
MHOD_ID_ALBUMARTIST = 22
MHOD_ID_SORT_ARTIST = 23
MHOD_ID_KEYWORDS = 24
MHOD_ID_SORT_TITLE = 27
MHOD_ID_SORT_ALBUM = 28
MHOD_ID_SORT_ALBUMARTIST = 29
MHOD_ID_SORT_COMPOSER = 30
MHOD_ID_SORT_TVSHOW = 31

MHOD_ID_SPLPREF = 50
MHOD_ID_SPLRULES = 51
MHOD_ID_LIBPLAYLISTINDEX = 52
MHOD_ID_LIBPLAYLISTJUMPTABLE = 53

MHOD_ID_PLAYLIST = 100

# Album list MHODs
MHOD_ID_ALBUM_ALBUM = 200
MHOD_ID_ALBUM_ARTIST = 201
MHOD_ID_ALBUM_SORT_ARTIST = 202
MHOD_ID_ALBUM_ARTIST_MHII = 300

# Set of MHOD types that contain UTF-16LE/UTF-8 strings
STRING_MHOD_TYPES = {
    MHOD_ID_TITLE,
    MHOD_ID_PATH,
    MHOD_ID_ALBUM,
    MHOD_ID_ARTIST,
    MHOD_ID_GENRE,
    MHOD_ID_FILETYPE,
    MHOD_ID_EQ_SETTING,
    MHOD_ID_COMMENT,
    MHOD_ID_CATEGORY,
    MHOD_ID_COMPOSER,
    MHOD_ID_GROUPING,
    MHOD_ID_DESCRIPTION,
    MHOD_ID_PODCASTURL,
    MHOD_ID_PODCASTRSS,
    MHOD_ID_SUBTITLE,
    MHOD_ID_TVSHOW,
    MHOD_ID_TVEPISODE,
    MHOD_ID_TVNETWORK,
    MHOD_ID_ALBUMARTIST,
    MHOD_ID_SORT_ARTIST,
    MHOD_ID_KEYWORDS,
    MHOD_ID_SORT_TITLE,
    MHOD_ID_SORT_ALBUM,
    MHOD_ID_SORT_ALBUMARTIST,
    MHOD_ID_SORT_COMPOSER,
    MHOD_ID_SORT_TVSHOW,
    MHOD_ID_ALBUM_ALBUM,
    MHOD_ID_ALBUM_ARTIST,
    MHOD_ID_ALBUM_SORT_ARTIST,
    MHOD_ID_ALBUM_ARTIST_MHII,
}

# Map of MHOD type to human-readable name
MHOD_TYPE_NAMES = {
    1: "title",
    2: "path",
    3: "album",
    4: "artist",
    5: "genre",
    6: "filetype",
    7: "eq_setting",
    8: "comment",
    9: "category",
    12: "composer",
    13: "grouping",
    14: "description",
    15: "podcast_url",
    16: "podcast_rss",
    17: "chapter_data",
    18: "subtitle",
    19: "tvshow",
    20: "tvepisode",
    21: "tvnetwork",
    22: "albumartist",
    23: "sort_artist",
    24: "keywords",
    27: "sort_title",
    28: "sort_album",
    29: "sort_albumartist",
    30: "sort_composer",
    31: "sort_tvshow",
    50: "splpref",
    51: "splrules",
    52: "lib_playlist_index",
    53: "lib_playlist_jumptable",
    100: "playlist",
    200: "album_album",
    201: "album_artist",
    202: "album_sort_artist",
    300: "album_artist_mhii",
}

# ============================================================================
# Media types
# ============================================================================
MEDIATYPE_AUDIO = 0x0001
MEDIATYPE_VIDEO = 0x0002
MEDIATYPE_PODCAST = 0x0004
MEDIATYPE_AUDIOBOOK = 0x0008
MEDIATYPE_MUSICVIDEO = 0x0020
MEDIATYPE_TVSHOW = 0x0040
MEDIATYPE_HOME_VIDEO = 0x0400

# ============================================================================
# Playlist types
# ============================================================================
PLAYLIST_TYPE_NORMAL = 0
PLAYLIST_TYPE_MASTER = 1

# ============================================================================
# File type markers (stored as 4-byte little-endian strings in MHIT)
# ============================================================================
FILETYPE_MP3 = b" 3PM"  # 'MP3 ' reversed for LE
FILETYPE_M4A = b" A4M"  # 'M4A ' reversed
FILETYPE_WAV = b" VAW"  # 'WAV ' reversed
FILETYPE_AIFF = b"FFIA"  # 'AIFF' reversed

# ============================================================================
# Hash-related constants
# ============================================================================
HASH58_OFFSET = 0x58  # Offset of hash58 field in MHBD header
HASH58_LEN = 20  # Length of hash58 (HMAC-SHA1, 20 bytes)
HASH72_SIG_OFFSET = 0x72  # Offset of hash72 signature in MHBD header
HASH72_SIG_LEN = 46  # Length of hash72 signature

HASHAB_OFFSET = 0xAB  # Offset of hashAB in MHBD header
HASHAB_LEN = 57  # Length of hashAB signature

# Hashing scheme values (stored at a specific offset in MHBD)
CHECKSUM_NONE = 0x0000
CHECKSUM_HASH58 = 0x0001
CHECKSUM_HASH72 = 0x0002
CHECKSUM_HASHAB = 0x0003

# ============================================================================
# Default header sizes (observed values)
# ============================================================================
DEFAULT_MHBD_HEADER_LEN = 244
DEFAULT_MHSD_HEADER_LEN = 96
DEFAULT_MHLT_HEADER_LEN = 92
DEFAULT_MHIT_HEADER_LEN = 584
DEFAULT_MHOD_HEADER_LEN = 24
DEFAULT_MHLP_HEADER_LEN = 92
DEFAULT_MHYP_HEADER_LEN = 108
DEFAULT_MHIP_HEADER_LEN = 76
DEFAULT_MHLA_HEADER_LEN = 92
DEFAULT_MHIA_HEADER_LEN = 148
DEFAULT_MHLI_HEADER_LEN = 92
DEFAULT_MHII_HEADER_LEN = 80

# ============================================================================
# iTunesSD constants
# ============================================================================
ITUNESSD_HEADER_MAGIC = b"\x00\x00\x00"  # iTunesSD has no real magic, identified by structure
ITUNESSD_V2_HEADER_LEN = 18
ITUNESSD_V2_TRACK_LEN = 558
