"""Artwork model - artwork/thumbnail support with image format handling.

Supports reading and writing .ithmb thumbnail files in various pixel
formats (RGB565, RGB555, RGB888, UYVY, I420). Handles scaling and
format conversion with optional Pillow dependency.

Ported from libgpod's itdb_thumb.c and ithumb-writer.c.
"""

from __future__ import annotations

import logging
import struct
from enum import IntEnum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class PixelFormat(IntEnum):
    """Pixel format identifiers used in artwork thumbnails."""

    RGB565_LE = 1  # 16-bit: 5R 6G 5B, little-endian
    RGB565_BE = 2  # 16-bit: 5R 6G 5B, big-endian
    RGB555_LE = 3  # 16-bit: 1X 5R 5G 5B, little-endian
    RGB555_BE = 4  # 16-bit: 1X 5R 5G 5B, big-endian
    RGB888_LE = 5  # 24-bit: B G R
    RGB888_BE = 6  # 24-bit: R G B
    UYVY = 7  # YCbCr 4:2:2
    I420 = 8  # YCbCr 4:2:0 planar


# iPod artwork format table: (format_id, width, height, pixel_format, bytes_per_pixel)
# Ported from libgpod itdb_device.c artwork format tables.
ARTWORK_FORMATS: Dict[int, Tuple[int, int, PixelFormat, float]] = {
    # iPod Photo cover art
    1016: (140, 140, PixelFormat.RGB565_LE, 2),
    1017: (56, 56, PixelFormat.RGB565_LE, 2),
    # iPod Photo photos
    1009: (42, 30, PixelFormat.RGB565_LE, 2),
    1013: (220, 176, PixelFormat.RGB565_BE, 2),
    1015: (130, 88, PixelFormat.RGB565_LE, 2),
    1019: (720, 480, PixelFormat.UYVY, 2),
    # iPod Nano 1-2G cover art
    1027: (100, 100, PixelFormat.RGB565_LE, 2),
    1031: (42, 42, PixelFormat.RGB565_LE, 2),
    # iPod Nano 1-2G photos
    1023: (176, 132, PixelFormat.RGB565_BE, 2),
    1032: (42, 37, PixelFormat.RGB565_LE, 2),
    # iPod Video cover art
    1028: (100, 100, PixelFormat.RGB565_LE, 2),
    1029: (200, 200, PixelFormat.RGB565_LE, 2),
    # iPod Video photos
    1024: (320, 240, PixelFormat.RGB565_LE, 2),
    1036: (50, 41, PixelFormat.RGB565_LE, 2),
    # iPod Classic 1-3G & Nano 3G cover art
    1055: (128, 128, PixelFormat.RGB565_LE, 2),
    1060: (320, 320, PixelFormat.RGB565_LE, 2),
    1061: (56, 56, PixelFormat.RGB565_LE, 2),
    1068: (128, 128, PixelFormat.RGB565_LE, 2),
    # iPod Classic photos
    1066: (64, 64, PixelFormat.RGB565_LE, 2),
    1067: (720, 480, PixelFormat.I420, 1.5),
    # iPod Nano 4G cover art
    1071: (240, 240, PixelFormat.RGB565_LE, 2),
    1074: (50, 50, PixelFormat.RGB565_LE, 2),
    1078: (80, 80, PixelFormat.RGB565_LE, 2),
    1084: (240, 240, PixelFormat.RGB565_LE, 2),
    # iPod Nano 4G photos
    1079: (80, 80, PixelFormat.RGB565_LE, 2),
    1083: (240, 320, PixelFormat.RGB565_LE, 2),
    # iPod Nano 5G cover art
    1056: (128, 128, PixelFormat.RGB565_LE, 2),
    1073: (240, 240, PixelFormat.RGB565_LE, 2),
    # iPod Nano 5G photos
    1087: (384, 384, PixelFormat.RGB565_LE, 2),
}


class Artwork:
    """Represents a track's artwork/thumbnail."""

    def __init__(
        self,
        image_data: Optional[bytes] = None,
        width: int = 0,
        height: int = 0,
        format_id: int = 0,
    ) -> None:
        self.image_data = image_data
        self.width = width
        self.height = height
        self.format_id = format_id

    @property
    def pixel_format(self) -> Optional[PixelFormat]:
        info = ARTWORK_FORMATS.get(self.format_id)
        if info:
            return info[2]
        return None

    def to_rgb(self) -> Optional[bytes]:
        """Convert pixel data to RGB888 format.

        Returns:
            Raw RGB bytes (3 bytes per pixel), or None if conversion fails.
        """
        if not self.image_data or self.width == 0 or self.height == 0:
            return None

        pf = self.pixel_format
        if pf is None:
            return None

        if pf in (PixelFormat.RGB565_LE, PixelFormat.RGB565_BE):
            return _unpack_rgb565(
                self.image_data, self.width, self.height, big_endian=(pf == PixelFormat.RGB565_BE)
            )
        elif pf in (PixelFormat.RGB555_LE, PixelFormat.RGB555_BE):
            return _unpack_rgb555(
                self.image_data, self.width, self.height, big_endian=(pf == PixelFormat.RGB555_BE)
            )
        elif pf in (PixelFormat.RGB888_LE, PixelFormat.RGB888_BE):
            return _unpack_rgb888(
                self.image_data, self.width, self.height, bgr=(pf == PixelFormat.RGB888_LE)
            )
        elif pf == PixelFormat.UYVY:
            return _unpack_uyvy(self.image_data, self.width, self.height)
        elif pf == PixelFormat.I420:
            return _unpack_i420(self.image_data, self.width, self.height)

        return None

    def to_pil_image(self) -> Any:
        """Convert to a Pillow Image object.

        Returns:
            PIL.Image.Image, or None if Pillow not available or conversion fails.
        """
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            logger.debug("Pillow not installed, cannot convert artwork to PIL Image")
            return None

        rgb_data = self.to_rgb()
        if rgb_data is None:
            return None

        return Image.frombytes("RGB", (self.width, self.height), rgb_data)

    def __repr__(self) -> str:
        return f"<Artwork {self.width}x{self.height} format={self.format_id}>"


class ArtworkThumbnail:
    """A thumbnail within an artwork record (MHNI child of MHII)."""

    def __init__(self) -> None:
        self.format_id: int = 0
        self.width: int = 0
        self.height: int = 0
        self.image_offset: int = 0  # Offset within .ithmb file
        self.image_size: int = 0  # Size in bytes
        self.ithmb_file: int = 0  # .ithmb file index (F1234_1.ithmb)
        self.horizontal_padding: int = 0
        self.vertical_padding: int = 0

    def read_image_data(self, artwork_dir: str) -> Optional[bytes]:
        """Read raw pixel data from the .ithmb file.

        Args:
            artwork_dir: Path to iPod_Control/Artwork/ directory.

        Returns:
            Raw pixel bytes, or None if file not found.
        """
        import os

        # .ithmb filename format: F{format_id}_{ithmb_file}.ithmb
        filename = f"F{self.format_id}_{self.ithmb_file}.ithmb"
        filepath = os.path.join(artwork_dir, filename)
        if not os.path.isfile(filepath):
            return None

        try:
            with open(filepath, "rb") as f:
                f.seek(self.image_offset)
                return f.read(self.image_size)
        except (OSError, IOError):
            logger.debug(
                "Failed to read ithmb file %s at offset %d",
                filepath,
                self.image_offset,
                exc_info=True,
            )
            return None

    def to_artwork(self, artwork_dir: str) -> Optional[Artwork]:
        """Create an Artwork object with image data loaded.

        Args:
            artwork_dir: Path to iPod_Control/Artwork/ directory.

        Returns:
            Artwork object, or None if data can't be read.
        """
        data = self.read_image_data(artwork_dir)
        if data is None:
            return None
        return Artwork(
            image_data=data, width=self.width, height=self.height, format_id=self.format_id
        )


# ============================================================================
# Thumbnail Generation
# ============================================================================
def create_thumbnail(
    image_path: str, format_id: int, target_width: int = 0, target_height: int = 0
) -> Optional[bytes]:
    """Create thumbnail pixel data from an image file.

    Uses Pillow to resize the image and convert to the target pixel format.

    Args:
        image_path: Path to source image file.
        format_id: Target artwork format ID.
        target_width: Override width (0 = use format default).
        target_height: Override height (0 = use format default).

    Returns:
        Raw pixel data in the target format, or None if Pillow not available.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        logger.debug("Pillow not installed, cannot create artwork thumbnail")
        return None

    fmt = ARTWORK_FORMATS.get(format_id)
    if fmt is None:
        return None

    w = target_width or fmt[0]
    h = target_height or fmt[1]
    logger.debug("Processing artwork format %d: %dx%d", format_id, w, h)
    pixel_fmt = fmt[2]

    img = Image.open(image_path).convert("RGB")

    # Resize with aspect ratio preservation + crop to center
    img_ratio = img.width / img.height
    target_ratio = w / h

    if img_ratio > target_ratio:
        # Image is wider - scale by height, crop width
        new_h = h
        new_w = int(h * img_ratio)
    else:
        # Image is taller - scale by width, crop height
        new_w = w
        new_h = int(w / img_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    img = img.crop((left, top, left + w, top + h))

    # Convert to pixel format
    if pixel_fmt in (PixelFormat.RGB565_LE, PixelFormat.RGB565_BE):
        return _pack_rgb565(img, big_endian=(pixel_fmt == PixelFormat.RGB565_BE))
    elif pixel_fmt in (PixelFormat.RGB555_LE, PixelFormat.RGB555_BE):
        return _pack_rgb555(img, big_endian=(pixel_fmt == PixelFormat.RGB555_BE))
    elif pixel_fmt == PixelFormat.UYVY:
        return _pack_uyvy(img)
    elif pixel_fmt == PixelFormat.I420:
        return _pack_i420(img)
    elif pixel_fmt in (PixelFormat.RGB888_LE, PixelFormat.RGB888_BE):
        return _pack_rgb888(img, bgr=(pixel_fmt == PixelFormat.RGB888_LE))

    return None


# ============================================================================
# Pixel format unpacking (iPod raw → RGB888)
# ============================================================================
def _unpack_rgb565(data: bytes, w: int, h: int, big_endian: bool = False) -> bytes:
    """Unpack RGB565 pixel data to RGB888."""
    fmt = ">" if big_endian else "<"
    result = bytearray(w * h * 3)
    for i in range(min(w * h, len(data) // 2)):
        pixel = struct.unpack_from(f"{fmt}H", data, i * 2)[0]
        r = ((pixel >> 11) & 0x1F) << 3
        g = ((pixel >> 5) & 0x3F) << 2
        b = (pixel & 0x1F) << 3
        result[i * 3] = r
        result[i * 3 + 1] = g
        result[i * 3 + 2] = b
    return bytes(result)


def _unpack_rgb555(data: bytes, w: int, h: int, big_endian: bool = False) -> bytes:
    """Unpack RGB555 pixel data to RGB888."""
    fmt = ">" if big_endian else "<"
    result = bytearray(w * h * 3)
    for i in range(min(w * h, len(data) // 2)):
        pixel = struct.unpack_from(f"{fmt}H", data, i * 2)[0]
        r = ((pixel >> 10) & 0x1F) << 3
        g = ((pixel >> 5) & 0x1F) << 3
        b = (pixel & 0x1F) << 3
        result[i * 3] = r
        result[i * 3 + 1] = g
        result[i * 3 + 2] = b
    return bytes(result)


def _unpack_rgb888(data: bytes, w: int, h: int, bgr: bool = False) -> bytes:
    """Unpack RGB888 (or BGR888) to standard RGB888."""
    if not bgr:
        return bytes(data[: w * h * 3])
    result = bytearray(w * h * 3)
    for i in range(min(w * h, len(data) // 3)):
        result[i * 3] = data[i * 3 + 2]  # R
        result[i * 3 + 1] = data[i * 3 + 1]  # G
        result[i * 3 + 2] = data[i * 3]  # B
    return bytes(result)


def _unpack_uyvy(data: bytes, w: int, h: int) -> bytes:
    """Unpack UYVY (YCbCr 4:2:2) to RGB888."""
    result = bytearray(w * h * 3)
    for i in range(0, min(w * h * 2, len(data)), 4):
        u = data[i] - 128
        y0 = data[i + 1]
        v = data[i + 2] - 128
        y1 = data[i + 3]

        px = (i // 4) * 2
        for j, y in enumerate((y0, y1)):
            r = max(0, min(255, int(y + 1.402 * v)))
            g = max(0, min(255, int(y - 0.344136 * u - 0.714136 * v)))
            b = max(0, min(255, int(y + 1.772 * u)))
            idx = (px + j) * 3
            if idx + 2 < len(result):
                result[idx] = r
                result[idx + 1] = g
                result[idx + 2] = b
    return bytes(result)


def _unpack_i420(data: bytes, w: int, h: int) -> bytes:
    """Unpack I420 (YCbCr 4:2:0 planar) to RGB888."""
    y_size = w * h
    uv_size = (w // 2) * (h // 2)

    if len(data) < y_size + 2 * uv_size:
        return b"\x00" * (w * h * 3)

    y_plane = data[:y_size]
    u_plane = data[y_size : y_size + uv_size]
    v_plane = data[y_size + uv_size : y_size + 2 * uv_size]

    result = bytearray(w * h * 3)
    for row in range(h):
        for col in range(w):
            y = y_plane[row * w + col]
            uv_idx = (row // 2) * (w // 2) + (col // 2)
            u = u_plane[uv_idx] - 128
            v = v_plane[uv_idx] - 128

            r = max(0, min(255, int(y + 1.402 * v)))
            g = max(0, min(255, int(y - 0.344136 * u - 0.714136 * v)))
            b = max(0, min(255, int(y + 1.772 * u)))

            idx = (row * w + col) * 3
            result[idx] = r
            result[idx + 1] = g
            result[idx + 2] = b
    return bytes(result)


# ============================================================================
# Pixel format packing (RGB → iPod format)
# ============================================================================
def _pack_rgb565(img: Any, big_endian: bool = False) -> bytes:
    """Pack PIL Image to RGB565."""
    w, h = img.size
    pixels = img.tobytes()
    fmt = ">" if big_endian else "<"
    result = bytearray(w * h * 2)
    for i in range(w * h):
        r = pixels[i * 3]
        g = pixels[i * 3 + 1]
        b = pixels[i * 3 + 2]
        pixel = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        struct.pack_into(f"{fmt}H", result, i * 2, pixel)
    return bytes(result)


def _pack_rgb555(img: Any, big_endian: bool = False) -> bytes:
    """Pack PIL Image to RGB555."""
    w, h = img.size
    pixels = img.tobytes()
    fmt = ">" if big_endian else "<"
    result = bytearray(w * h * 2)
    for i in range(w * h):
        r = pixels[i * 3]
        g = pixels[i * 3 + 1]
        b = pixels[i * 3 + 2]
        pixel = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
        struct.pack_into(f"{fmt}H", result, i * 2, pixel)
    return bytes(result)


def _pack_rgb888(img: Any, bgr: bool = False) -> bytes:
    """Pack PIL Image to RGB888 or BGR888."""
    pixels = img.tobytes()
    if not bgr:
        return pixels
    w, h = img.size
    result = bytearray(w * h * 3)
    for i in range(w * h):
        result[i * 3] = pixels[i * 3 + 2]
        result[i * 3 + 1] = pixels[i * 3 + 1]
        result[i * 3 + 2] = pixels[i * 3]
    return bytes(result)


def _pack_uyvy(img: Any) -> bytes:
    """Pack PIL Image to UYVY (YCbCr 4:2:2)."""
    w, h = img.size
    pixels = img.tobytes()
    result = bytearray(w * h * 2)

    for row in range(h):
        for col in range(0, w, 2):
            idx0 = (row * w + col) * 3
            idx1 = (row * w + col + 1) * 3 if col + 1 < w else idx0

            r0, g0, b0 = pixels[idx0], pixels[idx0 + 1], pixels[idx0 + 2]
            r1, g1, b1 = pixels[idx1], pixels[idx1 + 1], pixels[idx1 + 2]

            y0 = int(0.299 * r0 + 0.587 * g0 + 0.114 * b0)
            y1 = int(0.299 * r1 + 0.587 * g1 + 0.114 * b1)
            avg_r = (r0 + r1) / 2
            avg_g = (g0 + g1) / 2
            avg_b = (b0 + b1) / 2
            u = int(-0.169 * avg_r - 0.331 * avg_g + 0.5 * avg_b + 128)
            v = int(0.5 * avg_r - 0.419 * avg_g - 0.081 * avg_b + 128)

            out_idx = (row * w + col) * 2
            result[out_idx] = max(0, min(255, u))
            result[out_idx + 1] = max(0, min(255, y0))
            result[out_idx + 2] = max(0, min(255, v))
            result[out_idx + 3] = max(0, min(255, y1))

    return bytes(result)


def _pack_i420(img: Any) -> bytes:
    """Pack PIL Image to I420 (YCbCr 4:2:0 planar)."""
    w, h = img.size
    pixels = img.tobytes()

    y_plane = bytearray(w * h)
    u_plane = bytearray((w // 2) * (h // 2))
    v_plane = bytearray((w // 2) * (h // 2))

    for row in range(h):
        for col in range(w):
            idx = (row * w + col) * 3
            r, g, b = pixels[idx], pixels[idx + 1], pixels[idx + 2]

            y = int(0.299 * r + 0.587 * g + 0.114 * b)
            y_plane[row * w + col] = max(0, min(255, y))

            if row % 2 == 0 and col % 2 == 0:
                u = int(-0.169 * r - 0.331 * g + 0.5 * b + 128)
                v = int(0.5 * r - 0.419 * g - 0.081 * b + 128)
                uv_idx = (row // 2) * (w // 2) + (col // 2)
                u_plane[uv_idx] = max(0, min(255, u))
                v_plane[uv_idx] = max(0, min(255, v))

    return bytes(y_plane) + bytes(u_plane) + bytes(v_plane)
