"""Tests for pixel format conversions in pygpod.model.artwork.

Covers create_thumbnail() across all pixel formats and the internal
pack/unpack round-trip functions. Uses Pillow for image generation.
"""

from __future__ import annotations

import os
import struct

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from pygpod.model.artwork import (  # noqa: E402
    Artwork,
    PixelFormat,
    _pack_i420,
    _pack_rgb555,
    _pack_rgb565,
    _pack_rgb888,
    _pack_uyvy,
    _unpack_i420,
    _unpack_rgb555,
    _unpack_rgb565,
    _unpack_rgb888,
    _unpack_uyvy,
    create_thumbnail,
)


def _make_test_image(width: int, height: int, color: tuple = (255, 0, 0)) -> Image.Image:
    """Create a solid-color test image."""
    img = Image.new("RGB", (width, height), color)
    return img


def _save_test_image(img: Image.Image, tmp_path: str) -> str:
    """Save a PIL Image to a temporary PNG file and return the path."""
    path = os.path.join(tmp_path, "test_image.png")
    img.save(path)
    return path


# ============================================================================
# Format IDs used in parametrized tests, chosen to cover each pixel format
# ============================================================================

# format_id -> (width, height, pixel_format, bytes_per_pixel) from ARTWORK_FORMATS
RGB565_LE_FORMAT_ID = 1061  # 56x56
RGB565_BE_FORMAT_ID = 1013  # 220x176
UYVY_FORMAT_ID = 1019  # 720x480
I420_FORMAT_ID = 1067  # 720x480


class TestCreateThumbnailRGB565LE:
    """Test create_thumbnail for RGB565_LE pixel format."""

    def test_basic(self, tmp_path):
        img = _make_test_image(100, 100)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        # format 1061 is 56x56, RGB565 = 2 bytes/pixel
        assert len(result) == 56 * 56 * 2

    def test_red_pixel_encoding(self, tmp_path):
        """Verify pure red encodes correctly in RGB565_LE."""
        img = _make_test_image(56, 56, color=(255, 0, 0))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        # Pure red = 11111_000000_00000 = 0xF800
        pixel = struct.unpack_from("<H", result, 0)[0]
        assert pixel == 0xF800

    def test_green_pixel_encoding(self, tmp_path):
        """Verify pure green encodes correctly in RGB565_LE."""
        img = _make_test_image(56, 56, color=(0, 255, 0))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        # Pure green = 00000_111111_00000 = 0x07E0
        pixel = struct.unpack_from("<H", result, 0)[0]
        assert pixel == 0x07E0

    def test_blue_pixel_encoding(self, tmp_path):
        """Verify pure blue encodes correctly in RGB565_LE."""
        img = _make_test_image(56, 56, color=(0, 0, 255))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        # Pure blue = 00000_000000_11111 = 0x001F
        pixel = struct.unpack_from("<H", result, 0)[0]
        assert pixel == 0x001F

    def test_all_pixels_same_solid_color(self, tmp_path):
        """All pixels in a solid-color image should be identical."""
        img = _make_test_image(56, 56, color=(128, 64, 32))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        first_pixel = struct.unpack_from("<H", result, 0)[0]
        for i in range(56 * 56):
            pixel = struct.unpack_from("<H", result, i * 2)[0]
            assert pixel == first_pixel


class TestCreateThumbnailRGB565BE:
    """Test create_thumbnail for RGB565_BE pixel format."""

    def test_basic(self, tmp_path):
        img = _make_test_image(300, 300)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_BE_FORMAT_ID)
        assert result is not None
        # format 1013 is 220x176, RGB565 = 2 bytes/pixel
        assert len(result) == 220 * 176 * 2

    def test_red_pixel_encoding(self, tmp_path):
        """Verify pure red encodes correctly in RGB565_BE."""
        img = _make_test_image(220, 176, color=(255, 0, 0))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_BE_FORMAT_ID)
        assert result is not None
        # Big-endian: 0xF800 stored as 0xF8, 0x00
        pixel = struct.unpack_from(">H", result, 0)[0]
        assert pixel == 0xF800

    def test_endianness_differs_from_le(self, tmp_path):
        """The first two raw bytes should differ from LE for non-symmetric values."""
        img = _make_test_image(220, 176, color=(255, 0, 0))
        path = _save_test_image(img, str(tmp_path))
        be_result = create_thumbnail(path, RGB565_BE_FORMAT_ID)
        assert be_result is not None
        # 0xF800 in BE = [0xF8, 0x00], in LE = [0x00, 0xF8]
        assert be_result[0] == 0xF8
        assert be_result[1] == 0x00


class TestCreateThumbnailUYVY:
    """Test create_thumbnail for UYVY pixel format."""

    def test_basic(self, tmp_path):
        img = _make_test_image(800, 600)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, UYVY_FORMAT_ID)
        assert result is not None
        # format 1019 is 720x480, UYVY = 2 bytes/pixel
        assert len(result) == 720 * 480 * 2

    def test_white_encoding(self, tmp_path):
        """White in YCbCr should be high Y, neutral U/V (~128)."""
        img = _make_test_image(720, 480, color=(255, 255, 255))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, UYVY_FORMAT_ID)
        assert result is not None
        # UYVY is [U, Y0, V, Y1] for each pair of pixels
        u, y0, v, y1 = result[0], result[1], result[2], result[3]
        # Y should be close to 255 for white
        assert y0 > 200
        assert y1 > 200
        # U and V should be near 128 for neutral
        assert 100 < u < 160
        assert 100 < v < 160

    def test_black_encoding(self, tmp_path):
        """Black in YCbCr should be low Y, neutral U/V (~128)."""
        img = _make_test_image(720, 480, color=(0, 0, 0))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, UYVY_FORMAT_ID)
        assert result is not None
        u, y0, v, y1 = result[0], result[1], result[2], result[3]
        assert y0 < 20
        assert y1 < 20
        assert 100 < u < 160
        assert 100 < v < 160


class TestCreateThumbnailI420:
    """Test create_thumbnail for I420 pixel format."""

    def test_basic(self, tmp_path):
        img = _make_test_image(800, 600)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, I420_FORMAT_ID)
        assert result is not None
        # format 1067 is 720x480, I420 = 1.5 bytes/pixel
        w, h = 720, 480
        expected = w * h * 3 // 2
        assert len(result) == expected

    def test_plane_structure(self, tmp_path):
        """I420 has Y plane, then U plane, then V plane."""
        w, h = 720, 480
        img = _make_test_image(w, h, color=(255, 255, 255))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, I420_FORMAT_ID)
        assert result is not None
        y_size = w * h
        uv_size = (w // 2) * (h // 2)
        assert len(result) == y_size + 2 * uv_size
        # Y plane: all high for white
        y_plane = result[:y_size]
        assert all(p > 200 for p in y_plane[:100])
        # U and V planes: near 128 for white
        u_plane = result[y_size : y_size + uv_size]
        v_plane = result[y_size + uv_size :]
        assert all(100 < p < 160 for p in u_plane[:100])
        assert all(100 < p < 160 for p in v_plane[:100])


class TestCreateThumbnailEdgeCases:
    """Test edge cases for create_thumbnail."""

    def test_invalid_format_id(self, tmp_path):
        """Unknown format_id should return None."""
        img = _make_test_image(100, 100)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, 99999)
        assert result is None

    def test_downscale_large_image(self, tmp_path):
        """Large image should be downscaled to format dimensions."""
        img = _make_test_image(2000, 2000)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        assert len(result) == 56 * 56 * 2

    def test_non_square_source_to_square_target(self, tmp_path):
        """Non-square image should be cropped to square target."""
        img = _make_test_image(400, 100)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        assert len(result) == 56 * 56 * 2

    def test_tall_source_to_square_target(self, tmp_path):
        """Tall image should be cropped to square target."""
        img = _make_test_image(100, 400)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        assert len(result) == 56 * 56 * 2

    def test_non_square_target(self, tmp_path):
        """Non-square format (220x176) should produce correct size."""
        img = _make_test_image(100, 100)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_BE_FORMAT_ID)
        assert result is not None
        assert len(result) == 220 * 176 * 2

    def test_tiny_1x1_source(self, tmp_path):
        """1x1 source image should scale up to target size."""
        img = _make_test_image(1, 1, color=(100, 150, 200))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        assert len(result) == 56 * 56 * 2

    def test_target_size_override(self, tmp_path):
        """Explicit target_width/height should override format defaults."""
        img = _make_test_image(100, 100)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID, target_width=32, target_height=32)
        assert result is not None
        assert len(result) == 32 * 32 * 2

    def test_grayscale_source(self, tmp_path):
        """Grayscale source image should be converted to RGB internally."""
        img = Image.new("L", (100, 100), 128)
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        assert len(result) == 56 * 56 * 2

    def test_rgba_source(self, tmp_path):
        """RGBA source image should be converted to RGB internally."""
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, RGB565_LE_FORMAT_ID)
        assert result is not None
        assert len(result) == 56 * 56 * 2


# ============================================================================
# Parametrized tests across all pixel formats using direct pack functions
# ============================================================================

# (PixelFormat enum, pack function, unpack function, bytes_per_pixel)
PACK_UNPACK_PARAMS = [
    pytest.param(
        PixelFormat.RGB565_LE,
        _pack_rgb565,
        _unpack_rgb565,
        2.0,
        {"big_endian": False},
        id="RGB565_LE",
    ),
    pytest.param(
        PixelFormat.RGB565_BE,
        _pack_rgb565,
        _unpack_rgb565,
        2.0,
        {"big_endian": True},
        id="RGB565_BE",
    ),
    pytest.param(
        PixelFormat.RGB555_LE,
        _pack_rgb555,
        _unpack_rgb555,
        2.0,
        {"big_endian": False},
        id="RGB555_LE",
    ),
    pytest.param(
        PixelFormat.RGB555_BE,
        _pack_rgb555,
        _unpack_rgb555,
        2.0,
        {"big_endian": True},
        id="RGB555_BE",
    ),
    pytest.param(
        PixelFormat.RGB888_LE, _pack_rgb888, _unpack_rgb888, 3.0, {"bgr": True}, id="RGB888_LE_bgr"
    ),
    pytest.param(
        PixelFormat.RGB888_BE, _pack_rgb888, _unpack_rgb888, 3.0, {"bgr": False}, id="RGB888_BE_rgb"
    ),
]

PACK_UNPACK_YUV_PARAMS = [
    pytest.param(PixelFormat.UYVY, _pack_uyvy, _unpack_uyvy, 2.0, id="UYVY"),
    pytest.param(PixelFormat.I420, _pack_i420, _unpack_i420, 1.5, id="I420"),
]


class TestPackOutputSize:
    """Test that pack functions produce correct byte count for all formats."""

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_output_size_16x16(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        img = _make_test_image(16, 16)
        result = pack_fn(img, **kwargs)
        assert result is not None
        assert len(result) == int(16 * 16 * bpp)

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_output_size_1x1(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        img = _make_test_image(1, 1)
        result = pack_fn(img, **kwargs)
        assert result is not None
        assert len(result) == int(1 * 1 * bpp)

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_output_size_non_square(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        img = _make_test_image(32, 8)
        result = pack_fn(img, **kwargs)
        assert result is not None
        assert len(result) == int(32 * 8 * bpp)

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp", PACK_UNPACK_YUV_PARAMS)
    def test_yuv_output_size_16x16(self, pixel_fmt, pack_fn, unpack_fn, bpp):
        img = _make_test_image(16, 16)
        result = pack_fn(img)
        assert result is not None
        assert len(result) == int(16 * 16 * bpp)

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp", PACK_UNPACK_YUV_PARAMS)
    def test_yuv_output_size_non_square(self, pixel_fmt, pack_fn, unpack_fn, bpp):
        # Use even dimensions for YUV (chroma subsampling requires it)
        img = _make_test_image(32, 8)
        result = pack_fn(img)
        assert result is not None
        assert len(result) == int(32 * 8 * bpp)


class TestPackUnpackRoundTrip:
    """Test that pack followed by unpack recovers pixel values (within quantization)."""

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_solid_red_roundtrip(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(255, 0, 0))
        packed = pack_fn(img, **kwargs)
        unpacked = unpack_fn(packed, w, h, **kwargs)
        assert len(unpacked) == w * h * 3
        # Red channel should be high (quantization may reduce it)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r > 200, f"Red channel too low at pixel {i}: {r}"
            assert g < 30, f"Green channel too high at pixel {i}: {g}"
            assert b < 30, f"Blue channel too high at pixel {i}: {b}"

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_solid_green_roundtrip(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(0, 255, 0))
        packed = pack_fn(img, **kwargs)
        unpacked = unpack_fn(packed, w, h, **kwargs)
        assert len(unpacked) == w * h * 3
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r < 30, f"Red channel too high at pixel {i}: {r}"
            assert g > 200, f"Green channel too low at pixel {i}: {g}"
            assert b < 30, f"Blue channel too high at pixel {i}: {b}"

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_solid_blue_roundtrip(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(0, 0, 255))
        packed = pack_fn(img, **kwargs)
        unpacked = unpack_fn(packed, w, h, **kwargs)
        assert len(unpacked) == w * h * 3
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r < 30, f"Red channel too high at pixel {i}: {r}"
            assert g < 30, f"Green channel too high at pixel {i}: {g}"
            assert b > 200, f"Blue channel too low at pixel {i}: {b}"

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_white_roundtrip(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(255, 255, 255))
        packed = pack_fn(img, **kwargs)
        unpacked = unpack_fn(packed, w, h, **kwargs)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r > 200
            assert g > 200
            assert b > 200

    @pytest.mark.parametrize("pixel_fmt,pack_fn,unpack_fn,bpp,kwargs", PACK_UNPACK_PARAMS)
    def test_black_roundtrip(self, pixel_fmt, pack_fn, unpack_fn, bpp, kwargs):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(0, 0, 0))
        packed = pack_fn(img, **kwargs)
        unpacked = unpack_fn(packed, w, h, **kwargs)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r < 30
            assert g < 30
            assert b < 30


class TestPackUnpackRoundTripYUV:
    """Round-trip tests for YUV formats (looser tolerances due to chroma subsampling)."""

    def test_uyvy_white_roundtrip(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(255, 255, 255))
        packed = _pack_uyvy(img)
        unpacked = _unpack_uyvy(packed, w, h)
        assert len(unpacked) == w * h * 3
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r > 200, f"R too low at pixel {i}: {r}"
            assert g > 200, f"G too low at pixel {i}: {g}"
            assert b > 200, f"B too low at pixel {i}: {b}"

    def test_uyvy_black_roundtrip(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(0, 0, 0))
        packed = _pack_uyvy(img)
        unpacked = _unpack_uyvy(packed, w, h)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r < 30
            assert g < 30
            assert b < 30

    def test_uyvy_red_roundtrip(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(255, 0, 0))
        packed = _pack_uyvy(img)
        unpacked = _unpack_uyvy(packed, w, h)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            # YUV conversions are lossy, use wider tolerance
            assert r > 180, f"R too low at pixel {i}: {r}"
            assert g < 80, f"G too high at pixel {i}: {g}"
            assert b < 80, f"B too high at pixel {i}: {b}"

    def test_i420_white_roundtrip(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(255, 255, 255))
        packed = _pack_i420(img)
        unpacked = _unpack_i420(packed, w, h)
        assert len(unpacked) == w * h * 3
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r > 200
            assert g > 200
            assert b > 200

    def test_i420_black_roundtrip(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(0, 0, 0))
        packed = _pack_i420(img)
        unpacked = _unpack_i420(packed, w, h)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r < 30
            assert g < 30
            assert b < 30

    def test_i420_red_roundtrip(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(255, 0, 0))
        packed = _pack_i420(img)
        unpacked = _unpack_i420(packed, w, h)
        for i in range(w * h):
            r, g, b = unpacked[i * 3], unpacked[i * 3 + 1], unpacked[i * 3 + 2]
            assert r > 180, f"R too low at pixel {i}: {r}"
            assert g < 80, f"G too high at pixel {i}: {g}"
            assert b < 80, f"B too high at pixel {i}: {b}"


# ============================================================================
# Parametrized create_thumbnail tests across format_ids for each pixel format
# ============================================================================

# All format_ids grouped by pixel format with expected dimensions and bpp
FORMAT_ID_PARAMS = [
    # RGB565_LE
    pytest.param(1061, 56, 56, 2.0, id="1061_rgb565le_56x56"),
    pytest.param(1055, 128, 128, 2.0, id="1055_rgb565le_128x128"),
    pytest.param(1060, 320, 320, 2.0, id="1060_rgb565le_320x320"),
    pytest.param(1017, 56, 56, 2.0, id="1017_rgb565le_56x56"),
    pytest.param(1016, 140, 140, 2.0, id="1016_rgb565le_140x140"),
    pytest.param(1027, 100, 100, 2.0, id="1027_rgb565le_100x100"),
    pytest.param(1074, 50, 50, 2.0, id="1074_rgb565le_50x50"),
    pytest.param(1083, 240, 320, 2.0, id="1083_rgb565le_240x320"),
    # RGB565_BE
    pytest.param(1013, 220, 176, 2.0, id="1013_rgb565be_220x176"),
    pytest.param(1023, 176, 132, 2.0, id="1023_rgb565be_176x132"),
    # UYVY
    pytest.param(1019, 720, 480, 2.0, id="1019_uyvy_720x480"),
    # I420
    pytest.param(1067, 720, 480, 1.5, id="1067_i420_720x480"),
]


class TestCreateThumbnailParametrized:
    """Parametrized tests across multiple format_ids."""

    @pytest.mark.parametrize("format_id,w,h,bpp", FORMAT_ID_PARAMS)
    def test_output_size(self, tmp_path, format_id, w, h, bpp):
        # Use a source image big enough for all targets
        img = _make_test_image(max(w, 100), max(h, 100))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, format_id)
        assert result is not None
        expected_size = int(w * h * bpp)
        assert len(result) == expected_size, (
            f"Format {format_id}: expected {expected_size} bytes, got {len(result)}"
        )

    @pytest.mark.parametrize("format_id,w,h,bpp", FORMAT_ID_PARAMS)
    def test_not_all_zeros(self, tmp_path, format_id, w, h, bpp):
        """Result for a non-black image should not be all zeros."""
        img = _make_test_image(max(w, 100), max(h, 100), color=(128, 64, 200))
        path = _save_test_image(img, str(tmp_path))
        result = create_thumbnail(path, format_id)
        assert result is not None
        assert any(b != 0 for b in result)


# ============================================================================
# Artwork class: to_rgb conversion for each pixel format
# ============================================================================


class TestArtworkToRGB:
    """Test the Artwork.to_rgb() method for all pixel formats."""

    def test_rgb565_le_to_rgb(self):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(255, 0, 0))
        packed = _pack_rgb565(img)
        artwork = Artwork(image_data=packed, width=w, height=h, format_id=1061)
        rgb = artwork.to_rgb()
        assert rgb is not None
        assert len(rgb) == w * h * 3
        # Should recover red
        assert rgb[0] > 200

    def test_rgb565_be_to_rgb(self):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(0, 255, 0))
        packed = _pack_rgb565(img, big_endian=True)
        artwork = Artwork(image_data=packed, width=w, height=h, format_id=1013)
        rgb = artwork.to_rgb()
        assert rgb is not None
        assert len(rgb) == w * h * 3
        assert rgb[1] > 200  # green channel

    def test_uyvy_to_rgb(self):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(255, 255, 255))
        packed = _pack_uyvy(img)
        artwork = Artwork(image_data=packed, width=w, height=h, format_id=1019)
        rgb = artwork.to_rgb()
        assert rgb is not None
        assert len(rgb) == w * h * 3
        # All channels should be high for white
        assert rgb[0] > 200

    def test_i420_to_rgb(self):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(0, 0, 255))
        packed = _pack_i420(img)
        artwork = Artwork(image_data=packed, width=w, height=h, format_id=1067)
        rgb = artwork.to_rgb()
        assert rgb is not None
        assert len(rgb) == w * h * 3
        # Blue channel should be high
        assert rgb[2] > 180

    def test_no_image_data(self):
        artwork = Artwork(image_data=None, width=4, height=4, format_id=1061)
        assert artwork.to_rgb() is None

    def test_zero_dimensions(self):
        artwork = Artwork(image_data=b"\x00" * 32, width=0, height=0, format_id=1061)
        assert artwork.to_rgb() is None

    def test_unknown_format_id(self):
        artwork = Artwork(image_data=b"\x00" * 32, width=4, height=4, format_id=99999)
        assert artwork.to_rgb() is None


# ============================================================================
# Artwork class: to_pil_image conversion
# ============================================================================


class TestArtworkToPilImage:
    """Test Artwork.to_pil_image() method."""

    def test_basic_conversion(self):
        w, h = 8, 8
        img = _make_test_image(w, h, color=(255, 0, 0))
        packed = _pack_rgb565(img)
        artwork = Artwork(image_data=packed, width=w, height=h, format_id=1061)
        pil_img = artwork.to_pil_image()
        assert pil_img is not None
        assert pil_img.size == (w, h)
        assert pil_img.mode == "RGB"

    def test_none_on_no_data(self):
        artwork = Artwork(image_data=None, width=8, height=8, format_id=1061)
        assert artwork.to_pil_image() is None


# ============================================================================
# Direct pack function tests: specific bit patterns
# ============================================================================


class TestPackRGB565BitPatterns:
    """Test specific bit patterns in RGB565 packing."""

    def test_white_pixel_le(self):
        img = _make_test_image(1, 1, color=(255, 255, 255))
        result = _pack_rgb565(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        # White: 11111_111111_11111 = 0xFFFF
        assert pixel == 0xFFFF

    def test_black_pixel_le(self):
        img = _make_test_image(1, 1, color=(0, 0, 0))
        result = _pack_rgb565(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        assert pixel == 0x0000

    def test_white_pixel_be(self):
        img = _make_test_image(1, 1, color=(255, 255, 255))
        result = _pack_rgb565(img, big_endian=True)
        pixel = struct.unpack_from(">H", result, 0)[0]
        assert pixel == 0xFFFF

    def test_byte_order_le_vs_be(self):
        """Same pixel value, different byte order."""
        img = _make_test_image(1, 1, color=(255, 0, 0))
        le = _pack_rgb565(img)
        be = _pack_rgb565(img, big_endian=True)
        # 0xF800: LE = [0x00, 0xF8], BE = [0xF8, 0x00]
        assert le[0] == 0x00 and le[1] == 0xF8
        assert be[0] == 0xF8 and be[1] == 0x00


class TestPackRGB555BitPatterns:
    """Test specific bit patterns in RGB555 packing."""

    def test_white_pixel_le(self):
        img = _make_test_image(1, 1, color=(255, 255, 255))
        result = _pack_rgb555(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        # White: 0_11111_11111_11111 = 0x7FFF
        assert pixel == 0x7FFF

    def test_black_pixel_le(self):
        img = _make_test_image(1, 1, color=(0, 0, 0))
        result = _pack_rgb555(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        assert pixel == 0x0000

    def test_pure_red_le(self):
        img = _make_test_image(1, 1, color=(255, 0, 0))
        result = _pack_rgb555(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        # Red: 0_11111_00000_00000 = 0x7C00
        assert pixel == 0x7C00

    def test_pure_green_le(self):
        img = _make_test_image(1, 1, color=(0, 255, 0))
        result = _pack_rgb555(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        # Green: 0_00000_11111_00000 = 0x03E0
        assert pixel == 0x03E0

    def test_pure_blue_le(self):
        img = _make_test_image(1, 1, color=(0, 0, 255))
        result = _pack_rgb555(img)
        pixel = struct.unpack_from("<H", result, 0)[0]
        # Blue: 0_00000_00000_11111 = 0x001F
        assert pixel == 0x001F

    def test_byte_order_le_vs_be(self):
        img = _make_test_image(1, 1, color=(255, 0, 0))
        le = _pack_rgb555(img)
        be = _pack_rgb555(img, big_endian=True)
        # 0x7C00: LE = [0x00, 0x7C], BE = [0x7C, 0x00]
        assert le[0] == 0x00 and le[1] == 0x7C
        assert be[0] == 0x7C and be[1] == 0x00


class TestPackRGB888:
    """Test RGB888 pack/unpack."""

    def test_rgb_passthrough(self):
        """RGB888_BE (bgr=False) should be a passthrough."""
        img = _make_test_image(2, 2, color=(10, 20, 30))
        result = _pack_rgb888(img, bgr=False)
        # Should be raw RGB bytes from PIL
        assert result == img.tobytes()

    def test_bgr_swaps_channels(self):
        """RGB888_LE (bgr=True) should swap R and B channels."""
        img = _make_test_image(1, 1, color=(10, 20, 30))
        result = _pack_rgb888(img, bgr=True)
        assert result[0] == 30  # B goes to position 0
        assert result[1] == 20  # G stays
        assert result[2] == 10  # R goes to position 2

    def test_bgr_roundtrip(self):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(100, 150, 200))
        packed = _pack_rgb888(img, bgr=True)
        unpacked = _unpack_rgb888(packed, w, h, bgr=True)
        assert len(unpacked) == w * h * 3
        # Should recover original pixel values exactly
        for i in range(w * h):
            assert unpacked[i * 3] == 100
            assert unpacked[i * 3 + 1] == 150
            assert unpacked[i * 3 + 2] == 200

    def test_rgb_roundtrip(self):
        w, h = 4, 4
        img = _make_test_image(w, h, color=(100, 150, 200))
        packed = _pack_rgb888(img, bgr=False)
        unpacked = _unpack_rgb888(packed, w, h, bgr=False)
        assert len(unpacked) == w * h * 3
        for i in range(w * h):
            assert unpacked[i * 3] == 100
            assert unpacked[i * 3 + 1] == 150
            assert unpacked[i * 3 + 2] == 200


# ============================================================================
# I420 edge cases
# ============================================================================


class TestI420EdgeCases:
    """Test I420-specific edge cases."""

    def test_unpack_insufficient_data(self):
        """_unpack_i420 with too-short data should return zeroed output."""
        w, h = 4, 4
        too_short = b"\x00" * 10  # way less than 4*4*1.5 = 24 bytes needed
        result = _unpack_i420(too_short, w, h)
        assert len(result) == w * h * 3
        assert all(b == 0 for b in result)

    def test_even_dimensions_required(self):
        """I420 with even dimensions should produce valid output."""
        w, h = 8, 6
        img = _make_test_image(w, h, color=(128, 128, 128))
        packed = _pack_i420(img)
        y_size = w * h
        uv_size = (w // 2) * (h // 2)
        assert len(packed) == y_size + 2 * uv_size
        unpacked = _unpack_i420(packed, w, h)
        assert len(unpacked) == w * h * 3


# ============================================================================
# UYVY edge cases
# ============================================================================


class TestUYVYEdgeCases:
    """Test UYVY-specific edge cases."""

    def test_two_pixel_minimum(self):
        """UYVY processes 2 pixels at a time. A 2x1 image is the minimum pair."""
        img = _make_test_image(2, 1, color=(128, 128, 128))
        packed = _pack_uyvy(img)
        assert len(packed) == 2 * 1 * 2  # 4 bytes: U Y0 V Y1

    def test_odd_width_raises(self):
        """UYVY with odd width hits a buffer overrun -- iPod formats always use even widths."""
        img = _make_test_image(3, 2)
        with pytest.raises(IndexError):
            _pack_uyvy(img)


# ============================================================================
# Artwork.__repr__ and pixel_format property
# ============================================================================


class TestArtworkProperties:
    """Test Artwork helper methods and properties."""

    def test_repr(self):
        artwork = Artwork(width=56, height=56, format_id=1061)
        assert "56x56" in repr(artwork)
        assert "1061" in repr(artwork)

    def test_pixel_format_known(self):
        artwork = Artwork(format_id=1061)
        assert artwork.pixel_format == PixelFormat.RGB565_LE

    def test_pixel_format_be(self):
        artwork = Artwork(format_id=1013)
        assert artwork.pixel_format == PixelFormat.RGB565_BE

    def test_pixel_format_uyvy(self):
        artwork = Artwork(format_id=1019)
        assert artwork.pixel_format == PixelFormat.UYVY

    def test_pixel_format_i420(self):
        artwork = Artwork(format_id=1067)
        assert artwork.pixel_format == PixelFormat.I420

    def test_pixel_format_unknown(self):
        artwork = Artwork(format_id=99999)
        assert artwork.pixel_format is None
