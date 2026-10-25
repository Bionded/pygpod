"""Per-generation artwork format tables - ported from libgpod itdb_device.c.

Each table defines the cover art thumbnail formats that an iPod generation
supports. Format entries specify format_id, width, height, pixel format,
and optional padding (block size in the .ithmb file).

These are the FALLBACK tables used when SysInfoExtended is not available; with it, the
device's own image specifications win (:func:`formats_from_sysinfo_extended`).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

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
    # SysInfoExtended image specification extras (libgpod Itdb_ArtworkFormat):
    crop: bool = False  # fill the whole slot (scale up and cut) instead of letterboxing
    back_color: Tuple[int, int, int] = (0, 0, 0)  # RGB of the letterbox bars (RGB formats)
    row_bytes_alignment: int = 0  # each row padded to a multiple of this many bytes


def aligned_width(fmt: ArtworkFormatInfo) -> int:
    """Pixels per stored row: the width rounded up to ``row_bytes_alignment`` (libgpod
    get_aligned_width; only the 2-byte RGB formats use it)."""
    if fmt.pixel_format not in (
        ThumbFormat.RGB565_LE,
        ThumbFormat.RGB565_BE,
        ThumbFormat.RGB555_LE,
        ThumbFormat.RGB555_BE,
    ):
        return fmt.width
    align = fmt.row_bytes_alignment // 2
    if align and fmt.width % align:
        return fmt.width + align - fmt.width % align
    return fmt.width


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

    If padding is set, the image slot is exactly padding bytes. Otherwise it's
    width * height * bpp, except I420: libgpod stores those padded to 2 bytes per pixel
    (planes of 1.5 bytes per pixel, then zeros).
    """
    if fmt.padding > 0:
        return fmt.padding
    if fmt.pixel_format in (ThumbFormat.I420_LE, ThumbFormat.I420_BE):
        return fmt.width * fmt.height * 2
    return int(aligned_width(fmt) * fmt.height * bytes_per_pixel(fmt.pixel_format))


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


# ============================================================================
# Per-generation photo thumbnail formats (libgpod itdb_device.c *_photo_info).
# Only pixel formats pygpod can encode are listed; skipped from libgpod's tables:
# Nano 4G/5G 1081 (JPEG, unimplemented in libgpod too).
# ============================================================================
_PHOTO_PHOTOS = [
    ArtworkFormatInfo(1009, 42, 30, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1015, 130, 88, ThumbFormat.RGB565_LE),
    # RGB565_BE_90 in libgpod's table, but libgpod writes it unrotated: plain RGB565_BE
    ArtworkFormatInfo(1013, 220, 176, ThumbFormat.RGB565_BE),
    ArtworkFormatInfo(1019, 720, 480, ThumbFormat.UYVY_BE),  # TV out
]
_NANO_PHOTOS = [
    ArtworkFormatInfo(1032, 42, 37, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1023, 176, 132, ThumbFormat.RGB565_BE),
]
_VIDEO_PHOTOS = [
    ArtworkFormatInfo(1036, 50, 41, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1015, 130, 88, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1024, 320, 240, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1019, 720, 480, ThumbFormat.UYVY_BE),  # TV out
]
_CLASSIC_PHOTOS = [  # also Nano 3G
    ArtworkFormatInfo(1067, 720, 480, ThumbFormat.I420_LE),
    ArtworkFormatInfo(1024, 320, 240, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1066, 64, 64, ThumbFormat.RGB565_LE),
]
_NANO4G_PHOTOS = [
    ArtworkFormatInfo(1024, 320, 240, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1066, 64, 64, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1079, 80, 80, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1083, 240, 320, ThumbFormat.RGB565_LE),
]
_NANO5G_PHOTOS = [
    ArtworkFormatInfo(1087, 384, 384, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1079, 80, 80, ThumbFormat.RGB565_LE),
    ArtworkFormatInfo(1066, 64, 64, ThumbFormat.RGB565_LE),
]

_GENERATION_PHOTOS: Dict[IpodGeneration, List[ArtworkFormatInfo]] = {
    IpodGeneration.PHOTO: _PHOTO_PHOTOS,
    IpodGeneration.VIDEO_1: _VIDEO_PHOTOS,
    IpodGeneration.VIDEO_2: _VIDEO_PHOTOS,
    IpodGeneration.NANO_1: _NANO_PHOTOS,
    IpodGeneration.NANO_2: _NANO_PHOTOS,
    IpodGeneration.NANO_3: _CLASSIC_PHOTOS,
    IpodGeneration.NANO_4: _NANO4G_PHOTOS,
    IpodGeneration.NANO_5: _NANO5G_PHOTOS,
    IpodGeneration.CLASSIC_1: _CLASSIC_PHOTOS,
    IpodGeneration.CLASSIC_2: _CLASSIC_PHOTOS,
    IpodGeneration.CLASSIC_3: _CLASSIC_PHOTOS,
}


def get_photo_formats(generation: IpodGeneration) -> Optional[List[ArtworkFormatInfo]]:
    """Photo thumbnail formats for an iPod generation (None: no photo support known)."""
    return _GENERATION_PHOTOS.get(generation)


# ============================================================================
# SysInfoExtended image specifications (libgpod itdb_sysinfo_extended_parser.c)
# ============================================================================
# PixelFormat four-character codes (hex) -> pixel format; others are skipped, like libgpod
_SPEC_PIXEL_FORMATS = {
    "32767579": ThumbFormat.UYVY_BE,  # '2vuy'
    "42353635": ThumbFormat.RGB565_BE,  # 'B565'
    "4C353635": ThumbFormat.RGB565_LE,  # 'L565'
    "79343230": ThumbFormat.I420_LE,  # 'y420'
    "4C353535": ThumbFormat.RGB555_LE,  # 'L555' (not with PixelOrder: a format pygpod lacks)
}

# SysInfoExtended keys of the three format lists
SPEC_KEY_COVER_ART = "AlbumArt"
SPEC_KEY_PHOTOS = "ImageSpecifications"
SPEC_KEY_CHAPTER_IMAGES = "ChapterImageSpecs"


def _spec_int(spec: Dict[str, Any], key: str) -> int:
    value = spec.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _spec_format(spec: Any) -> Optional[ArtworkFormatInfo]:
    if not isinstance(spec, dict):
        return None  # the iPhone mixes <string>s into the array
    pixel = spec.get("PixelFormat")
    if not isinstance(pixel, str) or pixel.upper() not in _SPEC_PIXEL_FORMATS:
        return None
    if pixel.upper() == "4C353535" and "PixelOrder" in spec:
        return None
    pixel_format = _SPEC_PIXEL_FORMATS[pixel.upper()]
    format_id = _spec_int(spec, "FormatId")
    width, height = _spec_int(spec, "RenderWidth"), _spec_int(spec, "RenderHeight")
    if format_id <= 0 or width <= 0 or height <= 0:
        return None
    back = spec.get("BackColor")
    try:
        rgba = int(back, 16) if isinstance(back, str) else 0
    except ValueError:
        rgba = 0
    rgba &= 0xFFFFFFFF
    alignment = _spec_int(spec, "RowBytesAlignment")
    if not alignment and spec.get("AlignRowBytes") is True:
        alignment = 4  # the older key (nano 3G, Classic): 4 bytes
    return ArtworkFormatInfo(
        format_id,
        width,
        height,
        pixel_format,
        crop=spec.get("Crop") is True,
        back_color=((rgba >> 24) & 0xFF, (rgba >> 16) & 0xFF, (rgba >> 8) & 0xFF),
        row_bytes_alignment=alignment,
    )


def formats_from_sysinfo_extended(
    extended: Dict[str, Any], key: str
) -> Optional[List[ArtworkFormatInfo]]:
    """The thumbnail formats a SysInfoExtended plist lists under ``key`` (``AlbumArt``,
    ``ImageSpecifications`` or ``ChapterImageSpecs``), as libgpod reads them.

    Uses FormatId, RenderWidth/RenderHeight, PixelFormat, Crop, BackColor and
    RowBytesAlignment/AlignRowBytes; entries with an unknown pixel format are skipped. The
    list is in plist order, like the static tables (libgpod writes the thumbnails of both in
    reverse order). None if the key is missing or yields no usable format.
    """
    specs: Iterable[Any] = extended.get(key) or []
    if not isinstance(specs, list):
        return None
    formats = [f for f in (_spec_format(s) for s in specs) if f is not None]
    return formats or None
