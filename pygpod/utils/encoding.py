"""String encoding utilities for iTunesDB."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Encoding constants used in MHOD string records
ENCODING_UTF16LE = 0  # Also treated as UTF-16LE when value is 1
ENCODING_UTF8 = 2


def _mhod_codec(encoding: int) -> str:
    """Return Python codec name for an MHOD encoding flag."""
    return "utf-8" if encoding == ENCODING_UTF8 else "utf-16-le"


def decode_mhod_string(data: bytes, encoding: int) -> str:
    """Decode a string from an MHOD record.

    Args:
        data: Raw bytes of the string.
        encoding: Encoding flag (0 or 1 = UTF-16LE, 2 = UTF-8).

    Returns:
        Decoded Python string.
    """
    return data.decode(_mhod_codec(encoding))


def encode_mhod_string(text: str, encoding: int = ENCODING_UTF16LE) -> bytes:
    """Encode a string for an MHOD record.

    Args:
        text: Python string to encode.
        encoding: Encoding flag (0 or 1 = UTF-16LE, 2 = UTF-8).

    Returns:
        Encoded bytes.
    """
    return text.encode(_mhod_codec(encoding))


def ipod_path_to_os(ipod_path: str, mountpoint: str) -> str:
    """Convert an iPod-style colon-separated path to an OS path.

    iPod paths look like ':iPod_Control:Music:F00:file.mp3'.
    The leading colon means relative to the mount root.

    Args:
        ipod_path: iPod-style path with colon separators.
        mountpoint: Filesystem path to the iPod mount point.

    Returns:
        OS-native path string.
    """
    import pathlib

    # Strip leading colon and split
    parts = ipod_path.lstrip(":").split(":")
    return str(pathlib.Path(mountpoint).joinpath(*parts))


def os_path_to_ipod(os_path: str, mountpoint: str) -> str:
    """Convert an OS path to an iPod-style colon-separated path.

    Args:
        os_path: OS-native path to a file on the iPod.
        mountpoint: Filesystem path to the iPod mount point.

    Returns:
        iPod-style path string (e.g., ':iPod_Control:Music:F00:file.mp3').
    """
    import pathlib

    rel = pathlib.Path(os_path).relative_to(pathlib.Path(mountpoint))
    return ":" + ":".join(rel.parts)
