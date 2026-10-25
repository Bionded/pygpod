"""Artwork model - artwork/thumbnail support with image format handling.

Supports reading and writing .ithmb thumbnail files in various pixel
formats (RGB565, RGB555, RGB888, UYVY, I420). Handles scaling and
format conversion with optional Pillow dependency.

Ported from libgpod's itdb_thumb.c and ithumb-writer.c.
"""

from __future__ import annotations

import io
import logging
import math
import struct
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Union

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
    fmt = ARTWORK_FORMATS.get(format_id)
    if fmt is None:
        return None

    w = target_width or fmt[0]
    h = target_height or fmt[1]
    logger.debug("Processing artwork format %d: %dx%d", format_id, w, h)
    return render_thumbnail(image_path, w, h, fmt[2])


class FittedThumbnail:
    """A letterboxed thumbnail: pixel data of the whole ``w`` x ``h`` slot plus its geometry.

    ``scaled_width`` x ``scaled_height`` is the image inside the slot, centred with
    ``horizontal_padding`` / ``vertical_padding`` pixels on the left / top (the rest of the
    slot is black). ``width`` / ``height`` are the values libgpod records in the database:
    the scaled size plus the left/top padding.
    """

    def __init__(
        self, data: bytes, scaled_width: int, scaled_height: int, slot_width: int, slot_height: int
    ) -> None:
        self.data = data
        self.scaled_width = scaled_width
        self.scaled_height = scaled_height
        self.horizontal_padding = (slot_width - scaled_width) // 2
        self.vertical_padding = (slot_height - scaled_height) // 2

    @property
    def width(self) -> int:
        return self.scaled_width + self.horizontal_padding

    @property
    def height(self) -> int:
        return self.scaled_height + self.vertical_padding


def fit_size(src_width: int, src_height: int, width: int, height: int) -> Tuple[int, int]:
    """Size of a ``src_width`` x ``src_height`` image scaled to fit ``width`` x ``height``.

    The aspect ratio is kept; the limiting side gets the full slot size, the other side is
    scaled in floating point and rounded up, but not past the slot (libgpod's rule for photo
    thumbnails and cover art: 400x300 in 56x56 is 56x43). Images smaller than the slot are
    scaled up.
    """
    if src_width <= 0 or src_height <= 0:
        return width, height
    if src_width * height > src_height * width:  # wider than the slot
        return width, min(height, math.ceil(src_height * (width / src_width)))
    return min(width, math.ceil(src_width * (height / src_height))), height


def _open_rgb(image: Union[str, bytes]) -> Any:
    from PIL import Image  # type: ignore

    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image))).convert("RGB")
    return Image.open(image).convert("RGB")


def render_fitted_thumbnail(
    image: Union[str, bytes],
    w: int,
    h: int,
    pixel_fmt: PixelFormat,
    *,
    crop: bool = False,
    back_color: Tuple[int, int, int] = (0, 0, 0),
    row_width: int = 0,
) -> Optional[FittedThumbnail]:
    """Scale an image (path or encoded bytes) to fit ``w`` x ``h`` like libgpod does for photos.

    The image keeps its aspect ratio (:func:`fit_size`) and is centred on a ``w`` x ``h`` slot
    of ``back_color``, which is encoded as ``pixel_fmt``. With ``crop`` it is scaled to fill
    the whole slot and the overhang cut off (centred; no padding). ``row_width`` (> ``w``)
    stores each row that many pixels wide, the extra pixels in ``back_color`` (libgpod's
    RowBytesAlignment). None without Pillow.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        logger.debug("Pillow not installed, cannot create a thumbnail")
        return None

    img = _open_rgb(image)
    slot = Image.new("RGB", (max(w, row_width), h), tuple(back_color))
    if crop:
        sw, sh = w, h
        slot.paste(_scale_and_crop(img, w, h), (0, 0))
    else:
        sw, sh = fit_size(img.width, img.height, w, h)
        slot.paste(img.resize((sw, sh), Image.LANCZOS), ((w - sw) // 2, (h - sh) // 2))
    data = _encode(slot, pixel_fmt)
    if data is None:
        return None
    return FittedThumbnail(data, sw, sh, w, h)


def _scale_and_crop(img: Any, w: int, h: int) -> Any:
    """``img`` scaled to cover ``w`` x ``h`` (aspect ratio kept), centre cut to ``w`` x ``h``."""
    from PIL import Image  # type: ignore

    scale = max(w / img.width, h / img.height)
    new_w = max(w, round(img.width * scale))
    new_h = max(h, round(img.height * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left, top = (new_w - w) // 2, (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def render_thumbnail(image_path: str, w: int, h: int, pixel_fmt: PixelFormat) -> Optional[bytes]:
    """Scale/crop an image to ``w`` x ``h``, encode it as ``pixel_fmt`` (None without Pillow)."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        logger.debug("Pillow not installed, cannot create a thumbnail")
        return None

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
    return _encode(img, pixel_fmt)


def _encode(img: Any, pixel_fmt: PixelFormat) -> Optional[bytes]:
    """Encode an RGB Pillow image as ``pixel_fmt``."""
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
# YCbCr conversion: limited-range BT.601 in integer arithmetic, as libgpod does it
# (black Y=16, white Y=235, neutral chroma 128)
# ============================================================================
def _rgb_to_y(r: int, g: int, b: int) -> int:
    return ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16


def _rgb_to_yuv(r: int, g: int, b: int) -> Tuple[int, int, int]:
    return (
        ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16,
        ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128,
        ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128,
    )


def _yuv_to_rgb(y: int, u: int, v: int) -> Tuple[int, int, int]:
    c, d, e = 298 * (y - 16) + 128, u - 128, v - 128
    return (
        max(0, min(255, (c + 409 * e) >> 8)),
        max(0, min(255, (c - 100 * d - 208 * e) >> 8)),
        max(0, min(255, (c + 516 * d) >> 8)),
    )


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


def _field_rows(h: int) -> List[int]:
    """Source row of each stored row in TV field order: even rows, then odd rows."""
    return list(range(0, h, 2)) + list(range(1, h, 2))


def _unpack_uyvy(data: bytes, w: int, h: int) -> bytes:
    """Unpack UYVY (YCbCr 4:2:2, limited-range BT.601, rows in field order) to RGB888."""
    result = bytearray(w * h * 3)
    stride = w * 2
    for stored, row in enumerate(_field_rows(h)):
        line = data[stored * stride : (stored + 1) * stride]
        for i in range(0, len(line) - 3, 4):
            u, v = line[i], line[i + 2]
            for j, y in enumerate((line[i + 1], line[i + 3])):
                col = i // 2 + j
                if col < w:
                    idx = (row * w + col) * 3
                    result[idx : idx + 3] = bytes(_yuv_to_rgb(y, u, v))
    return bytes(result)


def _unpack_i420(data: bytes, w: int, h: int) -> bytes:
    """Unpack I420 (YCbCr 4:2:0 planar, limited-range BT.601) to RGB888."""
    y_size = w * h
    uv_size = (w // 2) * (h // 2)

    if len(data) < y_size + 2 * uv_size:
        return b"\x00" * (w * h * 3)

    y_plane = data[:y_size]
    u_plane = data[y_size : y_size + uv_size]
    v_plane = data[y_size + uv_size : y_size + 2 * uv_size]
    cw = w // 2

    result = bytearray(w * h * 3)
    for row in range(h):
        uv_row = min(row // 2, h // 2 - 1) * cw
        for col in range(w):
            uv_idx = uv_row + min(col // 2, cw - 1)
            r, g, b = _yuv_to_rgb(y_plane[row * w + col], u_plane[uv_idx], v_plane[uv_idx])
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
    """Pack PIL Image to UYVY (YCbCr 4:2:2) for TV out, the same bytes as libgpod.

    Limited-range BT.601 (``_rgb_to_yuv``), the chroma of each pixel pair taken from its left
    pixel, rows in TV field order (even rows, then odd rows).
    """
    w, h = img.size
    if w % 2:
        raise ValueError(f"UYVY needs an even width, got {w}")
    pixels = img.tobytes()
    result = bytearray(w * h * 2)
    out = 0
    for row in _field_rows(h):
        for col in range(0, w, 2):
            idx0 = (row * w + col) * 3
            idx1 = idx0 + 3
            y0, u, v = _rgb_to_yuv(pixels[idx0], pixels[idx0 + 1], pixels[idx0 + 2])
            y1 = _rgb_to_y(pixels[idx1], pixels[idx1 + 1], pixels[idx1 + 2])
            result[out : out + 4] = bytes((u, y0, v, y1))
            out += 4
    return bytes(result)


def _pack_i420(img: Any) -> bytes:
    """Pack PIL Image to I420 (YCbCr 4:2:0 planar), zero-padded to 2 bytes per pixel.

    Same bytes as libgpod: limited-range BT.601 (``_rgb_to_yuv``), the chroma of each 2x2
    block taken from its bottom-right pixel.
    """
    w, h = img.size
    pixels = img.tobytes()
    cw, ch = w // 2, h // 2

    y_plane = bytearray(w * h)
    u_plane = bytearray(cw * ch)
    v_plane = bytearray(cw * ch)

    for row in range(h):
        for col in range(w):
            idx = (row * w + col) * 3
            y_plane[row * w + col] = _rgb_to_y(pixels[idx], pixels[idx + 1], pixels[idx + 2])
    for row in range(ch):
        for col in range(cw):
            idx = ((2 * row + 1) * w + 2 * col + 1) * 3
            _, u, v = _rgb_to_yuv(pixels[idx], pixels[idx + 1], pixels[idx + 2])
            u_plane[row * cw + col] = u
            v_plane[row * cw + col] = v

    # libgpod stores I420 thumbnails in w*h*2 bytes: the Y, U and V planes, then zeros
    planes = bytes(y_plane) + bytes(u_plane) + bytes(v_plane)
    return planes + bytes(w * h * 2 - len(planes))
