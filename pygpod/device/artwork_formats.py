"""Per-generation artwork format tables - ported from libgpod itdb_device.c.

Each table defines the cover art thumbnail formats that an iPod generation
supports. Format entries specify format_id, width, height, pixel format,
and optional padding (block size in the .ithmb file).

These are the FALLBACK tables used when SysInfoExtended is not available.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List, NamedTuple, Optional

from .models import IpodGeneration


class ThumbFormat(IntEnum):
    """Pixel format for artwork thumbnails (matches libgpod ItdbThumbFormat)."""

    RGB565_LE = 0
    RGB565_BE = 1
    RGB555_LE = 2
    RGB555_BE = 3
    UYVY_LE = 4
    UYVY_BE = 5
    I420_LE = 6
    I420_BE = 7


class ArtworkFormatInfo(NamedTuple):
    """Artwork format specification."""

    format_id: int
    width: int
    height: int
    pixel_format: ThumbFormat
    padding: int = 0  # block size (0 = use exact image size)


def bytes_per_pixel(fmt: ThumbFormat) -> float:
    """Get bytes per pixel for a pixel format."""
    if fmt in (
        ThumbFormat.RGB565_LE,
        ThumbFormat.RGB565_BE,
        ThumbFormat.RGB555_LE,
        ThumbFormat.RGB555_BE,
        ThumbFormat.UYVY_LE,
        ThumbFormat.UYVY_BE,
    ):
        return 2.0
    if fmt in (ThumbFormat.I420_LE, ThumbFormat.I420_BE):
        return 1.5
    return 2.0


def image_data_size(fmt: ArtworkFormatInfo) -> int:
    """Get the size in bytes that one image occupies in the .ithmb file.

    If padding is set, the image slot is exactly padding bytes.
    Otherwise it's width * height * bpp.
    """
    if fmt.padding > 0:
        return fmt.padding
    return int(fmt.width * fmt.height * bytes_per_pixel(fmt.pixel_format))


# ============================================================================
# Per-generation cover art format tables
# Ported exactly from libgpod src/itdb_device.c
# ============================================================================

# iPod Photo (4G Color)
_PHOTO_COVER_ART = [
    ArtworkFormatInfo(1017, 56, 56, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1016, 140, 140, ThumbFormat.RGB565_LE),
]

# iPod Nano 1G & 2G
_NANO_COVER_ART = [
    ArtworkFormatInfo(1031, 42, 42, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1027, 100, 100, ThumbFormat.RGB565_LE),
]

# iPod Video (5G & 5.5G)
_VIDEO_COVER_ART = [
    ArtworkFormatInfo(1028, 100, 100, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1029, 200, 200, ThumbFormat.RGB565_LE),
]

# iPod Classic 1G/2G/3G & Nano 3G (share same tables)
_CLASSIC_COVER_ART = [
    ArtworkFormatInfo(1061, 56, 56, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1055, 128, 128, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1068, 128, 128, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1060, 320, 320, ThumbFormat.RGB565_LE),
]

# iPod Nano 4G
_NANO4G_COVER_ART = [
    ArtworkFormatInfo(1055, 128, 128, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1068, 128, 128, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1071, 240, 240, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1074, 50, 50, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1078, 80, 80, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1084, 240, 240, ThumbFormat.RGB565_LE),
]

# iPod Nano 5G
_NANO5G_COVER_ART = [
    ArtworkFormatInfo(1056, 128, 128, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1078, 80, 80, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1073, 240, 240, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1074, 50, 50, ThumbFormat.RGB565_LE),
]


# ============================================================================
# Generation → cover art format table mapping
# From libgpod's ipod_artwork_capabilities[] array
# ============================================================================
_GENERATION_COVER_ART: Dict[IpodGeneration, List[ArtworkFormatInfo]] = {
    IpodGeneration.PHOTO: _PHOTO_COVER_ART,
    IpodGeneration.VIDEO_1: _VIDEO_COVER_ART,
    IpodGeneration.VIDEO_2: _VIDEO_COVER_ART,
    IpodGeneration.NANO_1: _NANO_COVER_ART,
    IpodGeneration.NANO_2: _NANO_COVER_ART,
    IpodGeneration.NANO_3: _CLASSIC_COVER_ART,
    IpodGeneration.NANO_4: _NANO4G_COVER_ART,
    IpodGeneration.NANO_5: _NANO5G_COVER_ART,
    IpodGeneration.CLASSIC_1: _CLASSIC_COVER_ART,
    IpodGeneration.CLASSIC_2: _CLASSIC_COVER_ART,
    IpodGeneration.CLASSIC_3: _CLASSIC_COVER_ART,
}


def get_cover_art_formats(generation: IpodGeneration) -> Optional[List[ArtworkFormatInfo]]:
    """Get cover art format table for an iPod generation.

    Returns None if the generation doesn't support artwork.
    """
    return _GENERATION_COVER_ART.get(generation)
