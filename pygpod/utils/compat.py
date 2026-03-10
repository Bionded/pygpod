"""Byte manipulation helpers for binary parsing/writing."""

from __future__ import annotations

import logging
import struct

logger = logging.getLogger(__name__)


def get8int(data: bytes, offset: int) -> int:
    """Read unsigned 8-bit integer."""
    return data[offset]


def get16lint(data: bytes, offset: int) -> int:
    """Read unsigned 16-bit little-endian integer."""
    return struct.unpack_from("<H", data, offset)[0]


def get32lint(data: bytes, offset: int) -> int:
    """Read unsigned 32-bit little-endian integer."""
    return struct.unpack_from("<I", data, offset)[0]


def get64lint(data: bytes, offset: int) -> int:
    """Read unsigned 64-bit little-endian integer."""
    return struct.unpack_from("<Q", data, offset)[0]


def get32bint(data: bytes, offset: int) -> int:
    """Read unsigned 32-bit big-endian integer."""
    return struct.unpack_from(">I", data, offset)[0]


def get_float(data: bytes, offset: int) -> float:
    """Read 32-bit little-endian float."""
    return struct.unpack_from("<f", data, offset)[0]


def put8int(buf: bytearray, offset: int, val: int) -> None:
    """Write unsigned 8-bit integer."""
    buf[offset] = val & 0xFF


def put16lint(buf: bytearray, offset: int, val: int) -> None:
    """Write unsigned 16-bit little-endian integer."""
    struct.pack_into("<H", buf, offset, val & 0xFFFF)


def put32lint(buf: bytearray, offset: int, val: int) -> None:
    """Write unsigned 32-bit little-endian integer."""
    struct.pack_into("<I", buf, offset, val & 0xFFFFFFFF)


def put64lint(buf: bytearray, offset: int, val: int) -> None:
    """Write unsigned 64-bit little-endian integer."""
    struct.pack_into("<Q", buf, offset, val & 0xFFFFFFFFFFFFFFFF)


def put_float(buf: bytearray, offset: int, val: float) -> None:
    """Write 32-bit little-endian float."""
    struct.pack_into("<f", buf, offset, val)
