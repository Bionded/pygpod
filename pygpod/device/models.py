"""iPod model database and device autodetection.

Complete mapping of model numbers to iPod models, generations, and capabilities.
Ported from libgpod's ipod_info_table and itdb_device.c.

Includes:
- Model lookup by model number (from SysInfo ModelNumStr)
- Model lookup by serial number suffix (3-char)
- Checksum type detection (hash58/hash72)
- DB version detection
- USB product ID mapping
- Capability detection (artwork, video, podcast, etc.)
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Dict, List, NamedTuple, Optional


class IpodGeneration(Enum):
    """iPod generation identifier."""

    UNKNOWN = auto()
    FIRST = auto()
    SECOND = auto()
    THIRD = auto()
    FOURTH = auto()
    PHOTO = auto()
    MINI_1 = auto()
    MINI_2 = auto()
    SHUFFLE_1 = auto()
    SHUFFLE_2 = auto()
    SHUFFLE_3 = auto()
    SHUFFLE_4 = auto()
    NANO_1 = auto()
    NANO_2 = auto()
    NANO_3 = auto()
    NANO_4 = auto()
    NANO_5 = auto()
    NANO_6 = auto()
    VIDEO_1 = auto()
    VIDEO_2 = auto()
    CLASSIC_1 = auto()
    CLASSIC_2 = auto()
    CLASSIC_3 = auto()


class ChecksumType(Enum):
    """iPod database checksum type."""

    NONE = auto()
    HASH58 = auto()
    HASH72 = auto()
    HASHAB = auto()


class IpodModel(Enum):
    """iPod model/color variant."""

    INVALID = auto()
    UNKNOWN = auto()
    REGULAR = auto()
    REGULAR_U2 = auto()
    MINI = auto()
    MINI_BLUE = auto()
    MINI_PINK = auto()
    MINI_GREEN = auto()
    MINI_GOLD = auto()
    SHUFFLE = auto()
    SHUFFLE_SILVER = auto()
    SHUFFLE_PINK = auto()
    SHUFFLE_BLUE = auto()
    SHUFFLE_GREEN = auto()
    SHUFFLE_ORANGE = auto()
    SHUFFLE_PURPLE = auto()
    SHUFFLE_RED = auto()
    SHUFFLE_GOLD = auto()
    SHUFFLE_BLACK = auto()
    SHUFFLE_STAINLESS = auto()
    NANO_WHITE = auto()
    NANO_BLACK = auto()
    NANO_SILVER = auto()
    NANO_BLUE = auto()
    NANO_GREEN = auto()
    NANO_PINK = auto()
    NANO_RED = auto()
    NANO_YELLOW = auto()
    NANO_PURPLE = auto()
    NANO_ORANGE = auto()
    COLOR = auto()
    COLOR_U2 = auto()
    VIDEO_WHITE = auto()
    VIDEO_BLACK = auto()
    VIDEO_U2 = auto()
    CLASSIC_SILVER = auto()
    CLASSIC_BLACK = auto()


class IpodInfo(NamedTuple):
    """iPod model information entry."""

    model_number: str  # e.g., "B029"
    capacity_gb: float
    model: IpodModel
    generation: IpodGeneration
    musicdirs: int  # Number of Fxx music directories


# Generations that require hash58 (HMAC-SHA1) - from libgpod itdb_device.c
# These are the FALLBACK types when SysInfoExtended is not available.
HASH58_GENERATIONS = {
    IpodGeneration.VIDEO_1,
    IpodGeneration.VIDEO_2,
    IpodGeneration.NANO_1,
    IpodGeneration.NANO_2,
    IpodGeneration.NANO_3,
    IpodGeneration.NANO_4,
    IpodGeneration.CLASSIC_1,
    IpodGeneration.CLASSIC_2,
    IpodGeneration.CLASSIC_3,
}

# Generations that require hash72 (AES-CBC)
HASH72_GENERATIONS = {
    IpodGeneration.NANO_5,
}

# Generations that require hashAB (newer scheme)
HASHAB_GENERATIONS = {
    IpodGeneration.NANO_6,
}

# Generations that use iTunesSD (Shuffles)
SHUFFLE_GENERATIONS = {
    IpodGeneration.SHUFFLE_1,
    IpodGeneration.SHUFFLE_2,
    IpodGeneration.SHUFFLE_3,
    IpodGeneration.SHUFFLE_4,
}

# Generations that support artwork
ARTWORK_GENERATIONS = {
    IpodGeneration.PHOTO,
    IpodGeneration.VIDEO_1,
    IpodGeneration.VIDEO_2,
    IpodGeneration.NANO_1,
    IpodGeneration.NANO_2,
    IpodGeneration.NANO_3,
    IpodGeneration.NANO_4,
    IpodGeneration.NANO_5,
    IpodGeneration.NANO_6,
    IpodGeneration.CLASSIC_1,
    IpodGeneration.CLASSIC_2,
    IpodGeneration.CLASSIC_3,
}

# Generations that support video playback
VIDEO_GENERATIONS = {
    IpodGeneration.VIDEO_1,
    IpodGeneration.VIDEO_2,
    IpodGeneration.NANO_3,
    IpodGeneration.NANO_4,
    IpodGeneration.NANO_5,
    IpodGeneration.CLASSIC_1,
    IpodGeneration.CLASSIC_2,
    IpodGeneration.CLASSIC_3,
}

# Generations that support podcasts
PODCAST_GENERATIONS = {
    IpodGeneration.FOURTH,
    IpodGeneration.PHOTO,
    IpodGeneration.MINI_1,
    IpodGeneration.MINI_2,
    IpodGeneration.SHUFFLE_1,
    IpodGeneration.SHUFFLE_2,
    IpodGeneration.SHUFFLE_3,
    IpodGeneration.SHUFFLE_4,
    IpodGeneration.VIDEO_1,
    IpodGeneration.VIDEO_2,
    IpodGeneration.NANO_1,
    IpodGeneration.NANO_2,
    IpodGeneration.NANO_3,
    IpodGeneration.NANO_4,
    IpodGeneration.NANO_5,
    IpodGeneration.NANO_6,
    IpodGeneration.CLASSIC_1,
    IpodGeneration.CLASSIC_2,
    IpodGeneration.CLASSIC_3,
}

# Complete iPod info table - ported from libgpod itdb_device.c
IPOD_INFO_TABLE: List[IpodInfo] = [
    IpodInfo("Invalid", 0, IpodModel.INVALID, IpodGeneration.UNKNOWN, 0),
    IpodInfo("Unknown", 0, IpodModel.UNKNOWN, IpodGeneration.UNKNOWN, 0),
    # 1st Gen
    IpodInfo("8513", 5, IpodModel.REGULAR, IpodGeneration.FIRST, 20),
    IpodInfo("8541", 5, IpodModel.REGULAR, IpodGeneration.FIRST, 20),
    IpodInfo("8697", 5, IpodModel.REGULAR, IpodGeneration.FIRST, 20),
    IpodInfo("8709", 10, IpodModel.REGULAR, IpodGeneration.FIRST, 20),
    # 2nd Gen
    IpodInfo("8737", 10, IpodModel.REGULAR, IpodGeneration.SECOND, 20),
    IpodInfo("8740", 10, IpodModel.REGULAR, IpodGeneration.SECOND, 20),
    IpodInfo("8738", 20, IpodModel.REGULAR, IpodGeneration.SECOND, 50),
    IpodInfo("8741", 20, IpodModel.REGULAR, IpodGeneration.SECOND, 50),
    # 3rd Gen
    IpodInfo("8976", 10, IpodModel.REGULAR, IpodGeneration.THIRD, 20),
    IpodInfo("8946", 15, IpodModel.REGULAR, IpodGeneration.THIRD, 50),
    IpodInfo("9460", 15, IpodModel.REGULAR, IpodGeneration.THIRD, 50),
    IpodInfo("9244", 20, IpodModel.REGULAR, IpodGeneration.THIRD, 50),
    IpodInfo("8948", 30, IpodModel.REGULAR, IpodGeneration.THIRD, 50),
    IpodInfo("9245", 40, IpodModel.REGULAR, IpodGeneration.THIRD, 50),
    # 4th Gen
    IpodInfo("9282", 20, IpodModel.REGULAR, IpodGeneration.FOURTH, 50),
    IpodInfo("9787", 25, IpodModel.REGULAR_U2, IpodGeneration.FOURTH, 50),
    IpodInfo("9268", 40, IpodModel.REGULAR, IpodGeneration.FOURTH, 50),
    # Mini 1st Gen
    IpodInfo("9160", 4, IpodModel.MINI, IpodGeneration.MINI_1, 6),
    IpodInfo("9436", 4, IpodModel.MINI_BLUE, IpodGeneration.MINI_1, 6),
    IpodInfo("9435", 4, IpodModel.MINI_PINK, IpodGeneration.MINI_1, 6),
    IpodInfo("9434", 4, IpodModel.MINI_GREEN, IpodGeneration.MINI_1, 6),
    IpodInfo("9437", 4, IpodModel.MINI_GOLD, IpodGeneration.MINI_1, 6),
    # Mini 2nd Gen
    IpodInfo("9800", 4, IpodModel.MINI, IpodGeneration.MINI_2, 6),
    IpodInfo("9802", 4, IpodModel.MINI_BLUE, IpodGeneration.MINI_2, 6),
    IpodInfo("9804", 4, IpodModel.MINI_PINK, IpodGeneration.MINI_2, 6),
    IpodInfo("9806", 4, IpodModel.MINI_GREEN, IpodGeneration.MINI_2, 6),
    IpodInfo("9801", 6, IpodModel.MINI, IpodGeneration.MINI_2, 20),
    IpodInfo("9803", 6, IpodModel.MINI_BLUE, IpodGeneration.MINI_2, 20),
    IpodInfo("9805", 6, IpodModel.MINI_PINK, IpodGeneration.MINI_2, 20),
    IpodInfo("9807", 6, IpodModel.MINI_GREEN, IpodGeneration.MINI_2, 20),
    # Photo / 4th Gen Color
    IpodInfo("A079", 20, IpodModel.COLOR, IpodGeneration.PHOTO, 50),
    IpodInfo("A127", 20, IpodModel.COLOR_U2, IpodGeneration.PHOTO, 50),
    IpodInfo("9829", 30, IpodModel.COLOR, IpodGeneration.PHOTO, 50),
    IpodInfo("9585", 40, IpodModel.COLOR, IpodGeneration.PHOTO, 50),
    IpodInfo("9830", 60, IpodModel.COLOR, IpodGeneration.PHOTO, 50),
    IpodInfo("9586", 60, IpodModel.COLOR, IpodGeneration.PHOTO, 50),
    # Shuffle 1st Gen
    IpodInfo("9724", 0.5, IpodModel.SHUFFLE, IpodGeneration.SHUFFLE_1, 3),
    IpodInfo("9725", 1, IpodModel.SHUFFLE, IpodGeneration.SHUFFLE_1, 3),
    # Shuffle 2nd Gen
    IpodInfo("A546", 1, IpodModel.SHUFFLE_SILVER, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("A947", 1, IpodModel.SHUFFLE_PINK, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("A949", 1, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("A951", 1, IpodModel.SHUFFLE_GREEN, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("A953", 1, IpodModel.SHUFFLE_ORANGE, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("C167", 1, IpodModel.SHUFFLE_GOLD, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B225", 1, IpodModel.SHUFFLE_SILVER, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B233", 1, IpodModel.SHUFFLE_PURPLE, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B231", 1, IpodModel.SHUFFLE_RED, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B227", 1, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B228", 1, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B229", 1, IpodModel.SHUFFLE_GREEN, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B518", 2, IpodModel.SHUFFLE_SILVER, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B520", 2, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B522", 2, IpodModel.SHUFFLE_GREEN, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B524", 2, IpodModel.SHUFFLE_RED, IpodGeneration.SHUFFLE_2, 3),
    IpodInfo("B526", 2, IpodModel.SHUFFLE_PURPLE, IpodGeneration.SHUFFLE_2, 3),
    # Shuffle 3rd Gen
    IpodInfo("C306", 2, IpodModel.SHUFFLE_SILVER, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C323", 2, IpodModel.SHUFFLE_BLACK, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C381", 2, IpodModel.SHUFFLE_GREEN, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C384", 2, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C387", 2, IpodModel.SHUFFLE_PINK, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("B867", 4, IpodModel.SHUFFLE_SILVER, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C164", 4, IpodModel.SHUFFLE_BLACK, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C303", 4, IpodModel.SHUFFLE_STAINLESS, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C307", 4, IpodModel.SHUFFLE_GREEN, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C328", 4, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_3, 3),
    IpodInfo("C331", 4, IpodModel.SHUFFLE_PINK, IpodGeneration.SHUFFLE_3, 3),
    # Shuffle 4th Gen
    IpodInfo("C584", 2, IpodModel.SHUFFLE_SILVER, IpodGeneration.SHUFFLE_4, 3),
    IpodInfo("C585", 2, IpodModel.SHUFFLE_PINK, IpodGeneration.SHUFFLE_4, 3),
    IpodInfo("C749", 2, IpodModel.SHUFFLE_ORANGE, IpodGeneration.SHUFFLE_4, 3),
    IpodInfo("C750", 2, IpodModel.SHUFFLE_GREEN, IpodGeneration.SHUFFLE_4, 3),
    IpodInfo("C751", 2, IpodModel.SHUFFLE_BLUE, IpodGeneration.SHUFFLE_4, 3),
    # Nano 1st Gen
    IpodInfo("A350", 1, IpodModel.NANO_WHITE, IpodGeneration.NANO_1, 3),
    IpodInfo("A352", 1, IpodModel.NANO_BLACK, IpodGeneration.NANO_1, 3),
    IpodInfo("A004", 2, IpodModel.NANO_WHITE, IpodGeneration.NANO_1, 3),
    IpodInfo("A099", 2, IpodModel.NANO_BLACK, IpodGeneration.NANO_1, 3),
    IpodInfo("A005", 4, IpodModel.NANO_WHITE, IpodGeneration.NANO_1, 6),
    IpodInfo("A107", 4, IpodModel.NANO_BLACK, IpodGeneration.NANO_1, 6),
    # Video / 5th Gen
    IpodInfo("A002", 30, IpodModel.VIDEO_WHITE, IpodGeneration.VIDEO_1, 50),
    IpodInfo("A146", 30, IpodModel.VIDEO_BLACK, IpodGeneration.VIDEO_1, 50),
    IpodInfo("A003", 60, IpodModel.VIDEO_WHITE, IpodGeneration.VIDEO_1, 50),
    IpodInfo("A147", 60, IpodModel.VIDEO_BLACK, IpodGeneration.VIDEO_1, 50),
    IpodInfo("A452", 30, IpodModel.VIDEO_U2, IpodGeneration.VIDEO_1, 50),
    # Video / 5.5G (6th Gen)
    IpodInfo("A444", 30, IpodModel.VIDEO_WHITE, IpodGeneration.VIDEO_2, 50),
    IpodInfo("A446", 30, IpodModel.VIDEO_BLACK, IpodGeneration.VIDEO_2, 50),
    IpodInfo("A664", 30, IpodModel.VIDEO_U2, IpodGeneration.VIDEO_2, 50),
    IpodInfo("A448", 80, IpodModel.VIDEO_WHITE, IpodGeneration.VIDEO_2, 50),
    IpodInfo("A450", 80, IpodModel.VIDEO_BLACK, IpodGeneration.VIDEO_2, 50),
    # Nano 2nd Gen
    IpodInfo("A477", 2, IpodModel.NANO_SILVER, IpodGeneration.NANO_2, 3),
    IpodInfo("A426", 4, IpodModel.NANO_SILVER, IpodGeneration.NANO_2, 6),
    IpodInfo("A428", 4, IpodModel.NANO_BLUE, IpodGeneration.NANO_2, 6),
    IpodInfo("A487", 4, IpodModel.NANO_GREEN, IpodGeneration.NANO_2, 6),
    IpodInfo("A489", 4, IpodModel.NANO_PINK, IpodGeneration.NANO_2, 6),
    IpodInfo("A725", 4, IpodModel.NANO_RED, IpodGeneration.NANO_2, 6),
    IpodInfo("A726", 8, IpodModel.NANO_RED, IpodGeneration.NANO_2, 6),
    IpodInfo("A497", 8, IpodModel.NANO_BLACK, IpodGeneration.NANO_2, 14),
    # HP iPods
    IpodInfo("E436", 40, IpodModel.REGULAR, IpodGeneration.FOURTH, 50),
    IpodInfo("S492", 30, IpodModel.COLOR, IpodGeneration.PHOTO, 50),
    # Classic 1st Gen (6th Gen overall, "iPod Classic")
    IpodInfo("B029", 80, IpodModel.CLASSIC_SILVER, IpodGeneration.CLASSIC_1, 50),
    IpodInfo("B147", 80, IpodModel.CLASSIC_BLACK, IpodGeneration.CLASSIC_1, 50),
    IpodInfo("B145", 160, IpodModel.CLASSIC_SILVER, IpodGeneration.CLASSIC_1, 50),
    IpodInfo("B150", 160, IpodModel.CLASSIC_BLACK, IpodGeneration.CLASSIC_1, 50),
    # Classic 2nd Gen
    IpodInfo("B562", 120, IpodModel.CLASSIC_SILVER, IpodGeneration.CLASSIC_2, 50),
    IpodInfo("B565", 120, IpodModel.CLASSIC_BLACK, IpodGeneration.CLASSIC_2, 50),
    # Classic 3rd Gen
    IpodInfo("C293", 160, IpodModel.CLASSIC_SILVER, IpodGeneration.CLASSIC_3, 50),
    IpodInfo("C297", 160, IpodModel.CLASSIC_BLACK, IpodGeneration.CLASSIC_3, 50),
    # Nano 3rd Gen (video)
    IpodInfo("A978", 4, IpodModel.NANO_SILVER, IpodGeneration.NANO_3, 6),
    IpodInfo("A980", 8, IpodModel.NANO_SILVER, IpodGeneration.NANO_3, 14),
    IpodInfo("B261", 8, IpodModel.NANO_BLACK, IpodGeneration.NANO_3, 14),
    IpodInfo("B249", 8, IpodModel.NANO_BLUE, IpodGeneration.NANO_3, 14),
    IpodInfo("B253", 8, IpodModel.NANO_GREEN, IpodGeneration.NANO_3, 14),
    IpodInfo("B257", 8, IpodModel.NANO_RED, IpodGeneration.NANO_3, 14),
    # Nano 4th Gen
    IpodInfo("B480", 4, IpodModel.NANO_SILVER, IpodGeneration.NANO_4, 14),
    IpodInfo("B651", 4, IpodModel.NANO_BLUE, IpodGeneration.NANO_4, 14),
    IpodInfo("B654", 4, IpodModel.NANO_PINK, IpodGeneration.NANO_4, 14),
    IpodInfo("B657", 4, IpodModel.NANO_PURPLE, IpodGeneration.NANO_4, 14),
    IpodInfo("B660", 4, IpodModel.NANO_ORANGE, IpodGeneration.NANO_4, 14),
    IpodInfo("B663", 4, IpodModel.NANO_GREEN, IpodGeneration.NANO_4, 14),
    IpodInfo("B666", 4, IpodModel.NANO_YELLOW, IpodGeneration.NANO_4, 14),
    IpodInfo("B598", 8, IpodModel.NANO_SILVER, IpodGeneration.NANO_4, 14),
    IpodInfo("B732", 8, IpodModel.NANO_BLUE, IpodGeneration.NANO_4, 14),
    IpodInfo("B735", 8, IpodModel.NANO_PINK, IpodGeneration.NANO_4, 14),
    IpodInfo("B739", 8, IpodModel.NANO_PURPLE, IpodGeneration.NANO_4, 14),
    IpodInfo("B742", 8, IpodModel.NANO_ORANGE, IpodGeneration.NANO_4, 14),
    IpodInfo("B745", 8, IpodModel.NANO_GREEN, IpodGeneration.NANO_4, 14),
    IpodInfo("B748", 8, IpodModel.NANO_YELLOW, IpodGeneration.NANO_4, 14),
    IpodInfo("B751", 8, IpodModel.NANO_RED, IpodGeneration.NANO_4, 14),
    IpodInfo("B754", 8, IpodModel.NANO_BLACK, IpodGeneration.NANO_4, 14),
    IpodInfo("B903", 16, IpodModel.NANO_SILVER, IpodGeneration.NANO_4, 14),
    IpodInfo("B905", 16, IpodModel.NANO_BLUE, IpodGeneration.NANO_4, 14),
    IpodInfo("B907", 16, IpodModel.NANO_PINK, IpodGeneration.NANO_4, 14),
    IpodInfo("B909", 16, IpodModel.NANO_PURPLE, IpodGeneration.NANO_4, 14),
    IpodInfo("B911", 16, IpodModel.NANO_ORANGE, IpodGeneration.NANO_4, 14),
    IpodInfo("B913", 16, IpodModel.NANO_GREEN, IpodGeneration.NANO_4, 14),
    IpodInfo("B915", 16, IpodModel.NANO_YELLOW, IpodGeneration.NANO_4, 14),
    IpodInfo("B917", 16, IpodModel.NANO_RED, IpodGeneration.NANO_4, 14),
    IpodInfo("B918", 16, IpodModel.NANO_BLACK, IpodGeneration.NANO_4, 14),
    # Nano 5th Gen
    IpodInfo("C027", 8, IpodModel.NANO_SILVER, IpodGeneration.NANO_5, 14),
    IpodInfo("C031", 8, IpodModel.NANO_BLACK, IpodGeneration.NANO_5, 14),
    IpodInfo("C034", 8, IpodModel.NANO_PURPLE, IpodGeneration.NANO_5, 14),
    IpodInfo("C037", 8, IpodModel.NANO_BLUE, IpodGeneration.NANO_5, 14),
    IpodInfo("C040", 8, IpodModel.NANO_GREEN, IpodGeneration.NANO_5, 14),
    IpodInfo("C043", 8, IpodModel.NANO_YELLOW, IpodGeneration.NANO_5, 14),
    IpodInfo("C046", 8, IpodModel.NANO_ORANGE, IpodGeneration.NANO_5, 14),
    IpodInfo("C049", 8, IpodModel.NANO_RED, IpodGeneration.NANO_5, 14),
    IpodInfo("C050", 8, IpodModel.NANO_PINK, IpodGeneration.NANO_5, 14),
    IpodInfo("C060", 16, IpodModel.NANO_SILVER, IpodGeneration.NANO_5, 14),
    IpodInfo("C062", 16, IpodModel.NANO_BLACK, IpodGeneration.NANO_5, 14),
    IpodInfo("C064", 16, IpodModel.NANO_PURPLE, IpodGeneration.NANO_5, 14),
    IpodInfo("C066", 16, IpodModel.NANO_BLUE, IpodGeneration.NANO_5, 14),
    IpodInfo("C068", 16, IpodModel.NANO_GREEN, IpodGeneration.NANO_5, 14),
    IpodInfo("C070", 16, IpodModel.NANO_YELLOW, IpodGeneration.NANO_5, 14),
    IpodInfo("C072", 16, IpodModel.NANO_ORANGE, IpodGeneration.NANO_5, 14),
    IpodInfo("C074", 16, IpodModel.NANO_RED, IpodGeneration.NANO_5, 14),
    IpodInfo("C075", 16, IpodModel.NANO_PINK, IpodGeneration.NANO_5, 14),
    # Nano 6th Gen (multi-touch)
    IpodInfo("C525", 8, IpodModel.NANO_SILVER, IpodGeneration.NANO_6, 14),
    IpodInfo("C688", 8, IpodModel.NANO_BLACK, IpodGeneration.NANO_6, 14),
    IpodInfo("C689", 8, IpodModel.NANO_BLUE, IpodGeneration.NANO_6, 14),
    IpodInfo("C690", 8, IpodModel.NANO_GREEN, IpodGeneration.NANO_6, 14),
    IpodInfo("C691", 8, IpodModel.NANO_ORANGE, IpodGeneration.NANO_6, 14),
    IpodInfo("C692", 8, IpodModel.NANO_PINK, IpodGeneration.NANO_6, 14),
    IpodInfo("C693", 8, IpodModel.NANO_RED, IpodGeneration.NANO_6, 14),
    IpodInfo("C526", 16, IpodModel.NANO_SILVER, IpodGeneration.NANO_6, 14),
    IpodInfo("C694", 16, IpodModel.NANO_BLACK, IpodGeneration.NANO_6, 14),
    IpodInfo("C695", 16, IpodModel.NANO_BLUE, IpodGeneration.NANO_6, 14),
    IpodInfo("C696", 16, IpodModel.NANO_GREEN, IpodGeneration.NANO_6, 14),
    IpodInfo("C697", 16, IpodModel.NANO_ORANGE, IpodGeneration.NANO_6, 14),
    IpodInfo("C698", 16, IpodModel.NANO_PINK, IpodGeneration.NANO_6, 14),
    IpodInfo("C699", 16, IpodModel.NANO_RED, IpodGeneration.NANO_6, 14),
]

# Build lookup dict: model_number -> IpodInfo
_MODEL_LOOKUP: Dict[str, IpodInfo] = {}
for _info in IPOD_INFO_TABLE:
    if _info.model_number not in ("Invalid", "Unknown"):
        _MODEL_LOOKUP[_info.model_number.upper()] = _info

# Apple USB vendor ID
APPLE_VENDOR_ID = 0x05AC

# Apple USB product IDs mapping to iPod models.
# Sources: linux-usb.org usb.ids, ipod-gadget apple-usb.ids,
# freemyipod wInd3x, Device Hunt, USB ID Repository.
# Note: iPod 1G and 2G were FireWire-only (no USB product ID).
APPLE_USB_PRODUCT_IDS: Dict[int, str] = {
    0x1201: "iPod 3G",
    0x1203: "iPod 4G (Click Wheel)",
    0x1204: "iPod Photo (4G Color)",
    0x1205: "iPod Mini",
    0x1209: "iPod Video (5G/5.5G)",
    0x120A: "iPod Nano 1G",
    0x1260: "iPod Nano 2G",
    0x1261: "iPod Classic",
    0x1262: "iPod Nano 3G",
    0x1263: "iPod Nano 4G",
    0x1265: "iPod Nano 5G",
    0x1266: "iPod Nano 6G",
    0x1300: "iPod Shuffle 1G",
    0x1301: "iPod Shuffle 2G",
    0x1302: "iPod Shuffle 3G",
    0x1303: "iPod Shuffle 4G",
}

# USB product ID to generation mapping for device identification from USB.
# Some PIDs are shared across generations; the list form allows
# capacity-based disambiguation in lookup_by_usb_product_id().
USB_PRODUCT_TO_GENERATIONS: Dict[int, List[IpodGeneration]] = {
    0x1201: [IpodGeneration.THIRD],
    0x1203: [IpodGeneration.FOURTH],
    0x1204: [IpodGeneration.PHOTO],
    0x1205: [IpodGeneration.MINI_1, IpodGeneration.MINI_2],
    0x1209: [IpodGeneration.VIDEO_1, IpodGeneration.VIDEO_2],
    0x120A: [IpodGeneration.NANO_1],
    0x1260: [IpodGeneration.NANO_2],
    0x1261: [IpodGeneration.CLASSIC_1, IpodGeneration.CLASSIC_2, IpodGeneration.CLASSIC_3],
    0x1262: [IpodGeneration.NANO_3],
    0x1263: [IpodGeneration.NANO_4],
    0x1265: [IpodGeneration.NANO_5],
    0x1266: [IpodGeneration.NANO_6],
    0x1300: [IpodGeneration.SHUFFLE_1],
    0x1301: [IpodGeneration.SHUFFLE_2],
    0x1302: [IpodGeneration.SHUFFLE_3],
    0x1303: [IpodGeneration.SHUFFLE_4],
}


# Generation display names
GENERATION_NAMES: Dict[IpodGeneration, str] = {
    IpodGeneration.UNKNOWN: "Unknown",
    IpodGeneration.FIRST: "iPod 1st Gen",
    IpodGeneration.SECOND: "iPod 2nd Gen",
    IpodGeneration.THIRD: "iPod 3rd Gen",
    IpodGeneration.FOURTH: "iPod 4th Gen",
    IpodGeneration.PHOTO: "iPod Photo",
    IpodGeneration.MINI_1: "iPod Mini 1st Gen",
    IpodGeneration.MINI_2: "iPod Mini 2nd Gen",
    IpodGeneration.SHUFFLE_1: "iPod Shuffle 1st Gen",
    IpodGeneration.SHUFFLE_2: "iPod Shuffle 2nd Gen",
    IpodGeneration.SHUFFLE_3: "iPod Shuffle 3rd Gen",
    IpodGeneration.SHUFFLE_4: "iPod Shuffle 4th Gen",
    IpodGeneration.NANO_1: "iPod Nano 1st Gen",
    IpodGeneration.NANO_2: "iPod Nano 2nd Gen",
    IpodGeneration.NANO_3: "iPod Nano 3rd Gen",
    IpodGeneration.NANO_4: "iPod Nano 4th Gen",
    IpodGeneration.NANO_5: "iPod Nano 5th Gen",
    IpodGeneration.NANO_6: "iPod Nano 6th Gen",
    IpodGeneration.VIDEO_1: "iPod Video (5th Gen)",
    IpodGeneration.VIDEO_2: "iPod Video (5.5G)",
    IpodGeneration.CLASSIC_1: "iPod Classic 1st Gen (6G)",
    IpodGeneration.CLASSIC_2: "iPod Classic 2nd Gen",
    IpodGeneration.CLASSIC_3: "iPod Classic 3rd Gen",
}


def lookup_model(model_number: str) -> IpodInfo:
    """Look up iPod info by model number string.

    The model number is typically found in SysInfo as ModelNumStr
    (e.g., 'xB029' - strip the leading character to get 'B029').

    Args:
        model_number: The 4-character model number (e.g., 'B029').

    Returns:
        Matching IpodInfo, or the Unknown entry if not found.
    """
    key = model_number.upper().lstrip("X").lstrip("M")
    info = _MODEL_LOOKUP.get(key)
    if info:
        return info
    return IPOD_INFO_TABLE[1]  # Unknown


def lookup_model_by_serial(serial: str) -> Optional[IpodInfo]:
    """Look up iPod info by serial number.

    Uses the last 3 characters of the serial number to identify the model,
    as per libgpod's serial_to_model_mapping table.

    Args:
        serial: Full serial number string.

    Returns:
        Matching IpodInfo, or None if not found.
    """
    if not serial or len(serial) < 3:
        return None
    suffix = serial[-3:].upper()
    model_num = _SERIAL_TO_MODEL.get(suffix)
    if model_num:
        return _MODEL_LOOKUP.get(model_num)
    return None


def get_checksum_type(
    gen: IpodGeneration, sysinfo_db_version: Optional[int] = None
) -> ChecksumType:
    """Determine checksum type for an iPod generation.

    Priority:
    1. SysInfoExtended db_version field (if available)
    2. Generation-based fallback mapping

    Args:
        gen: iPod generation.
        sysinfo_db_version: db_version from SysInfoExtended (0-4), if available.

    Returns:
        ChecksumType enum value.
    """
    # Priority: SysInfoExtended db_version
    if sysinfo_db_version is not None:
        if sysinfo_db_version <= 2:
            return ChecksumType.NONE
        elif sysinfo_db_version == 3:
            return ChecksumType.HASH58
        elif sysinfo_db_version >= 4:
            return ChecksumType.HASH72

    # Fallback: generation-based mapping (from libgpod itdb_device.c)
    if gen in HASHAB_GENERATIONS:
        return ChecksumType.HASHAB
    elif gen in HASH72_GENERATIONS:
        return ChecksumType.HASH72
    elif gen in HASH58_GENERATIONS:
        return ChecksumType.HASH58
    return ChecksumType.NONE


def get_db_version(gen: IpodGeneration) -> int:
    """Get the iTunesDB version number for a generation.

    This is the db_version field written at offset 0x10 in the MHBD header.
    Different iPod generations expect different versions.

    Args:
        gen: iPod generation.

    Returns:
        DB version number.
    """
    if gen in {
        IpodGeneration.UNKNOWN,
        IpodGeneration.FIRST,
        IpodGeneration.SECOND,
        IpodGeneration.THIRD,
        IpodGeneration.FOURTH,
    }:
        return 0x09  # 9 - very early iPods
    elif gen in {IpodGeneration.PHOTO}:
        return 0x0D  # 13
    elif gen in {
        IpodGeneration.MINI_1,
        IpodGeneration.MINI_2,
        IpodGeneration.SHUFFLE_1,
    }:
        return 0x0C  # 12
    elif gen in {
        IpodGeneration.VIDEO_1,
        IpodGeneration.VIDEO_2,
        IpodGeneration.NANO_1,
        IpodGeneration.NANO_2,
        IpodGeneration.SHUFFLE_2,
    }:
        return 0x19  # 25
    elif gen in {
        IpodGeneration.CLASSIC_1,
        IpodGeneration.CLASSIC_2,
        IpodGeneration.CLASSIC_3,
        IpodGeneration.NANO_3,
        IpodGeneration.NANO_4,
        IpodGeneration.NANO_5,
        IpodGeneration.NANO_6,
        IpodGeneration.SHUFFLE_3,
        IpodGeneration.SHUFFLE_4,
    }:
        return 0x30  # 48
    return 0x30  # 48 - safe default


def get_generation_name(gen: IpodGeneration) -> str:
    """Get human-readable name for an iPod generation."""
    return GENERATION_NAMES.get(gen, "Unknown")


def lookup_by_usb_product_id(
    product_id: int, capacity_gb: Optional[float] = None
) -> Optional[IpodInfo]:
    """Look up iPod info by USB product ID, optionally using disk capacity.

    When a USB product ID maps to a single generation, returns a
    representative model.  When shared across generations (e.g. 0x1261
    for iPod Classic 1G and 3G), returns a match only if capacity
    uniquely identifies the generation.  Returns None when ambiguous.

    Args:
        product_id: USB product ID (e.g., 0x1261).
        capacity_gb: Disk capacity in GB (base-10), from sysfs. Optional.

    Returns:
        IpodInfo for the matched generation, or None if ambiguous.
    """
    gens = USB_PRODUCT_TO_GENERATIONS.get(product_id)
    if not gens:
        return None

    # Collect all candidate models across possible generations
    candidates = []
    for info in IPOD_INFO_TABLE:
        if info.generation in gens and info.model_number not in ("Invalid", "Unknown"):
            candidates.append(info)

    if not candidates:
        return None

    # Single generation - unambiguous
    if len(gens) == 1:
        return candidates[0]

    # Multiple possible generations - need capacity to disambiguate
    if capacity_gb is not None:
        # Allow 15% tolerance for formatted vs raw capacity
        cap_matches = []
        for info in candidates:
            if info.capacity_gb > 0:
                ratio = capacity_gb / info.capacity_gb
                if 0.85 <= ratio <= 1.15:
                    cap_matches.append(info)
        if cap_matches:
            # Check if all capacity matches belong to the same generation
            matched_gens = {info.generation for info in cap_matches}
            if len(matched_gens) == 1:
                return cap_matches[0]

    # Ambiguous - can't determine the exact model
    return None


# ============================================================================
# Serial number to model number mapping
# Ported from libgpod's serial_to_model_mapping[] in itdb_device.c
# (extracted from podsleuth ipod-model-table, 2008-06-14)
# Uses the last 3 characters of the serial number.
# ============================================================================
_SERIAL_TO_MODEL: Dict[str, str] = {
    # iPod 1st Gen
    "LG6": "8541",
    "NAM": "8541",
    "MJ2": "8541",
    "ML1": "8709",
    "MME": "8709",
    # iPod 2nd Gen
    "MMB": "8737",
    "MMC": "8738",
    "NGE": "8740",
    "NGH": "8740",
    "MMF": "8741",
    # iPod 3rd Gen
    "NLW": "8946",
    "NRH": "8976",
    "QQF": "9460",
    "PQ5": "9244",
    "PNT": "9244",
    "NLY": "8948",
    "NM7": "8948",
    "PNU": "9245",
    # iPod 4th Gen
    "PS9": "9282",
    "Q8U": "9282",
    "V9V": "9787",
    "S2X": "9787",
    "PQ7": "9268",
    # iPod Photo / Color
    "TDU": "A079",
    "TDS": "A079",
    "TM2": "A127",
    "SAZ": "9830",
    "SB1": "9830",
    "SAY": "9829",
    "R5Q": "9585",
    "R5R": "9586",
    "R5T": "9586",
    # Mini 1st Gen
    "PFW": "9160",
    "PRC": "9160",
    "QKL": "9436",
    "QKQ": "9436",
    "QKK": "9435",
    "QKP": "9435",
    "QKJ": "9434",
    "QKN": "9434",
    "QKM": "9437",
    "QKR": "9437",
    # Mini 2nd Gen
    "S41": "9800",
    "S4C": "9800",
    "S43": "9802",
    "S45": "9804",
    "S47": "9806",
    "S4J": "9806",
    "S42": "9801",
    "S44": "9803",
    "S48": "9807",
    # Shuffle 1st Gen
    "RS9": "9724",
    "QGV": "9724",
    "TSX": "9724",
    "PFV": "9724",
    "R80": "9724",
    "RSA": "9725",
    "TSY": "9725",
    "C60": "9725",
    # Shuffle 2nd Gen (from libgpod)
    "VTE": "A546",
    "VTF": "A546",
    "XQ5": "A947",
    "XQS": "A947",
    "XQV": "A949",
    "XQX": "A949",
    "XQY": "A951",
    "YX8": "A951",
    "XR1": "A953",
    "YXA": "B233",
    "YX6": "B225",
    "YX7": "B228",  # libgpod duplicate; last entry wins (B228 over A949)
    "YX9": "B225",
    "8CQ": "C167",
    "1ZH": "B518",
    # Shuffle 2nd Gen (additional serials)
    "1GK": "A546",
    "1GL": "A947",
    "1GM": "A949",
    "1GN": "A951",
    "1GP": "A953",
    # Nano 1st Gen
    "UNA": "A350",
    "UNB": "A350",
    "UPR": "A352",
    "UPS": "A352",
    "SZB": "A004",
    "SZV": "A004",
    "SZW": "A004",
    "SZC": "A005",
    "SZT": "A005",
    "TJT": "A099",
    "TJU": "A099",
    "TK2": "A107",
    "TK3": "A107",
    # Nano 2nd Gen
    "VQ5": "A477",
    "VQ6": "A477",
    "V8T": "A426",
    "V8U": "A426",
    "V8W": "A428",
    "V8X": "A428",
    "VQH": "A487",
    "VQJ": "A487",
    "VQK": "A489",
    "VKL": "A489",
    "WL2": "A725",
    "WL3": "A725",
    "X9A": "A726",
    "X9B": "A726",
    "VQT": "A497",
    "VQU": "A497",
    # Nano 3rd Gen
    "Y0P": "A978",
    "Y0R": "A980",
    "YXR": "B249",
    "YXV": "B257",
    "YXT": "B253",
    "YXX": "B261",
    # Nano 3rd Gen (additional serials)
    "1P7": "A980",
    "6V2": "B261",
    "6V4": "B249",
    "6V6": "B253",
    "6V9": "B257",
    # Video / 5th Gen
    "SZ9": "A002",
    "WEC": "A002",
    "WED": "A002",
    "WEG": "A002",
    "WEH": "A002",
    "WEL": "A002",
    "TXK": "A146",
    "TXM": "A146",
    "WEE": "A146",
    "WEF": "A146",
    "WEJ": "A146",
    "WEK": "A146",
    "SZA": "A003",
    "SZU": "A003",
    "TXL": "A147",
    "TXN": "A147",
    # Video / 5.5G (6th Gen)
    "V9K": "A444",
    "V9L": "A444",
    "WU9": "A444",
    "VQM": "A446",
    "V9M": "A446",
    "V9N": "A446",
    "V9P": "A448",
    "V9Q": "A448",
    "V9R": "A450",
    "V9S": "A450",
    "V95": "A450",
    "V96": "A450",
    "WUC": "A450",
    "W9G": "A664",
    # Classic 1G (6G)
    "Y5N": "B029",
    "YMV": "B147",  # 80GB Black
    "YMU": "B145",  # 160GB Silver
    "YMX": "B150",  # 160GB Black
    # Classic 1G (additional serials)
    "YQ7": "B147",
    "YXH": "B145",
    "YXK": "B150",
    # Classic 2G
    "2C5": "B562",  # 120GB Silver
    "2C7": "B565",  # 120GB Black
    # Classic 2G (additional serials)
    "1ZF": "B562",
    "3NZ": "B565",
    # Classic 3G
    "9ZS": "C293",  # 160GB Silver
    "9ZU": "C297",  # 160GB Black
    # Nano 4th Gen
    "37P": "B663",  # 4GB Green
    "37Q": "B666",  # 4GB Yellow
    "37H": "B654",  # 4GB Pink
    "1P1": "B480",  # 4GB Silver
    "37K": "B657",  # 4GB Purple
    "37L": "B660",  # 4GB Orange
    "2ME": "B598",  # 8GB Silver
    "3QS": "B732",  # 8GB Blue
    "3QT": "B735",  # 8GB Pink
    "3QU": "B739",  # 8GB Purple
    "3QW": "B742",  # 8GB Orange
    "3QX": "B745",  # 8GB Green
    "3QY": "B748",  # 8GB Yellow
    "3R0": "B754",  # 8GB Black
    "3QZ": "B751",  # 8GB Red
    "5B7": "B903",  # 16GB Silver
    "5B8": "B905",  # 16GB Blue
    "5B9": "B907",  # 16GB Pink
    "5BA": "B909",  # 16GB Purple
    "5BB": "B911",  # 16GB Orange
    "5BC": "B913",  # 16GB Green
    "5BD": "B915",  # 16GB Yellow
    "5BE": "B917",  # 16GB Red
    "5BF": "B918",  # 16GB Black
    # Nano 4th Gen (additional serials)
    "3R2": "B480",
    "71M": "B598",
    "71P": "B732",
    "71R": "B735",
    "728": "B745",
    "72C": "B748",
    "72E": "B751",
    "72G": "B754",
    "5C1": "B907",
    "5C3": "B909",
    "5C5": "B911",
    "5C7": "B913",
    "5C9": "B915",
    "5D1": "B917",
    "5D3": "B918",
    # Nano 5th Gen
    "71V": "C027",  # 8GB Silver
    "71Y": "C031",  # 8GB Black
    "721": "C034",  # 8GB Purple
    "726": "C037",  # 8GB Blue
    "72A": "C040",  # 8GB Green
    "72F": "C046",  # 8GB Orange
    "72K": "C049",  # 8GB Red
    "72L": "C050",  # 8GB Pink
    "72Q": "C060",  # 16GB Silver
    "72R": "C062",  # 16GB Black
    "72S": "C064",  # 16GB Purple
    "72X": "C066",  # 16GB Blue
    "734": "C068",  # 16GB Green
    "738": "C070",  # 16GB Yellow
    "739": "C072",  # 16GB Orange
    "73A": "C074",  # 16GB Red
    "73B": "C075",  # 16GB Pink
    # Nano 5th Gen (additional serials)
    "0K5": "C031",
    "0K7": "C034",
    "0K9": "C037",
    "0KG": "C040",
    "0KJ": "C043",
    "0KL": "C046",
    "0KN": "C049",
    "0KQ": "C050",
    "0KS": "C060",
    "0KU": "C062",
    "0KW": "C064",
    "0KY": "C066",
    "0L0": "C068",
    "0L2": "C070",
    "0L4": "C072",
    "0L6": "C074",
    "0L8": "C075",
    # Nano 6th Gen (multi-touch)
    "CMN": "C525",  # 8GB Silver
    "DVX": "C688",  # 8GB Black
    "DVY": "C689",  # 8GB Blue
    "DW0": "C690",  # 8GB Green
    "DW1": "C691",  # 8GB Orange
    "DW2": "C692",  # 8GB Pink
    "DW3": "C693",  # 8GB Red
    "CMP": "C526",  # 16GB Silver
    "DW4": "C694",  # 16GB Black
    "DW5": "C695",  # 16GB Blue
    "DW6": "C696",  # 16GB Green
    "DW7": "C697",  # 16GB Orange
    "DW8": "C698",  # 16GB Pink
    "DW9": "C699",  # 16GB Red
    # Shuffle 3rd Gen
    "A1S": "C306",  # 2GB Silver
    "A78": "C323",  # 2GB Black
    "ALB": "C381",  # 2GB Green
    "ALD": "C384",  # 2GB Blue
    "ALG": "C387",  # 2GB Pink
    "4NZ": "B867",  # 4GB Silver
    "891": "C164",  # 4GB Black
    "A1L": "C303",  # 4GB Stainless
    "A1U": "C307",  # 4GB Green
    "A7B": "C328",  # 4GB Blue
    "A7D": "C331",  # 4GB Pink
    # Shuffle 3rd Gen (additional serials)
    "7U4": "C306",
    "7U6": "C323",
    "7U8": "C381",
    "7UA": "C384",
    "7UC": "C387",
    "2MG": "B867",
    "4NJ": "C164",
    # Shuffle 4th Gen
    "CMJ": "C584",  # 2GB Silver
    "CMK": "C585",  # 2GB Pink
    "FDM": "C749",  # 2GB Orange
    "FDN": "C750",  # 2GB Green
    "FDP": "C751",  # 2GB Blue
    # Shuffle 4th Gen (additional serials)
    "DT6": "C584",
    "DT8": "C585",
    "DTA": "C749",
    "DTC": "C750",
    "DTE": "C751",
}
