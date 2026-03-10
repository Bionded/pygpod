"""ArtworkDB binary parser.

Parses the iPod ArtworkDB file which stores album art thumbnails.
The format uses the same MHXX record structure as iTunesDB.
"""

from __future__ import annotations

import logging

from ..exceptions import ParseError
from ..utils.compat import get16lint, get32lint, get64lint
from .parser import Record

logger = logging.getLogger(__name__)


# ArtworkDB record magics
MHFD_MAGIC = b"mhfd"  # File header (like MHBD)
MHSD_MAGIC = b"mhsd"
MHLI_MAGIC = b"mhli"  # Image list
MHII_MAGIC = b"mhii"  # Image item
MHNI_MAGIC = b"mhni"  # Image name/thumbnail info
MHOD_MAGIC = b"mhod"
MHLA_MAGIC = b"mhla"  # Album list (in artwork context)
MHBA_MAGIC = b"mhba"  # Album
MHIF_MAGIC = b"mhif"  # Image file info


def parse_artworkdb(data: bytes) -> Record:
    """Parse an ArtworkDB binary blob.

    Args:
        data: Raw bytes of the ArtworkDB file.

    Returns:
        Root MHFD Record.

    Raises:
        ParseError: If the data is not a valid ArtworkDB.
    """
    logger.debug("Parsing ArtworkDB: %d bytes", len(data))
    if len(data) < 12:
        raise ParseError("Data too short for ArtworkDB")

    magic = data[0:4]
    if magic != MHFD_MAGIC:
        raise ParseError(f"Invalid ArtworkDB magic: expected 'mhfd', got {magic!r}")

    header_len = get32lint(data, 4)
    total_len = get32lint(data, 8)

    root = Record(MHFD_MAGIC, header_len, total_len)
    root.raw_header = bytes(data[:header_len])
    root.raw_bytes = bytes(data[:total_len])

    root.fields["unknown1"] = get32lint(data, 0x0C)
    root.fields["num_children"] = get32lint(data, 0x10)

    # Parse child sections
    pos = header_len
    while pos + 12 < len(data) and pos < total_len:
        child_magic = data[pos : pos + 4]
        if child_magic != MHSD_MAGIC:
            break

        child = _parse_artwork_mhsd(data, pos)
        root.children.append(child)
        pos += child.total_len

    return root


def _parse_artwork_mhsd(data: bytes, offset: int) -> Record:
    """Parse MHSD in artwork context."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHSD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])
    rec.fields["mhsd_type"] = get32lint(data, offset + 0x0C)

    # Parse children based on type
    pos = offset + header_len
    end = offset + total_len

    if pos + 4 <= len(data):
        child_magic = data[pos : pos + 4]

        if child_magic == MHLI_MAGIC:
            child = _parse_mhli(data, pos, end)
            rec.children.append(child)
        elif child_magic == MHLA_MAGIC:
            child = _parse_artwork_mhla(data, pos, end)
            rec.children.append(child)
        elif child_magic == b"mhlf":  # Image file list
            child = _parse_mhlf(data, pos, end)
            rec.children.append(child)

    return rec


def _parse_mhli(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLI (image list)."""
    header_len = get32lint(data, offset + 4)
    num_items = get32lint(data, offset + 8)

    rec = Record(MHLI_MAGIC, header_len, num_items)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_items"] = num_items

    pos = offset + header_len
    for _ in range(num_items):
        if pos + 4 > end or pos + 4 > len(data):
            break
        if data[pos : pos + 4] == MHII_MAGIC:
            child = _parse_mhii(data, pos)
            rec.children.append(child)
            pos += child.total_len
        else:
            break

    rec.raw_bytes = bytes(data[offset:pos])
    return rec


def _parse_mhii(data: bytes, offset: int) -> Record:
    """Parse MHII (image item)."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHII_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    rec.fields["num_children"] = get32lint(data, offset + 0x0C)
    rec.fields["image_id"] = get32lint(data, offset + 0x10)
    rec.fields["song_id"] = get64lint(data, offset + 0x14)

    # Parse children: MHOD containers (type=2, wrapping MHNI) or bare MHNI
    pos = offset + header_len
    end = offset + total_len
    for _ in range(rec.fields["num_children"]):
        if pos + 4 > end:
            break
        child_magic = data[pos : pos + 4]
        if child_magic == MHNI_MAGIC:
            child = _parse_mhni(data, pos)
            rec.children.append(child)
            pos += child.total_len
        elif child_magic == MHOD_MAGIC:
            child = _parse_mhii_mhod(data, pos)
            rec.children.append(child)
            pos += child.total_len
        else:
            break

    return rec


def _parse_mhni(data: bytes, offset: int) -> Record:
    """Parse MHNI (image thumbnail info)."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHNI_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    rec.fields["num_children"] = get32lint(data, offset + 0x0C)
    rec.fields["format_id"] = get32lint(data, offset + 0x10)
    rec.fields["ithumb_offset"] = get32lint(data, offset + 0x14)
    rec.fields["image_size"] = get32lint(data, offset + 0x18)
    # 0x1C: vertical_padding (16-bit), 0x1E: horizontal_padding (16-bit)
    if offset + 0x24 <= len(data):
        rec.fields["image_height"] = get16lint(data, offset + 0x20)
        rec.fields["image_width"] = get16lint(data, offset + 0x22)

    # Parse child MHODs (contain filename of .ithmb file)
    pos = offset + header_len
    end = offset + total_len
    for _ in range(rec.fields.get("num_children", 0)):
        if pos + 12 > end:
            break
        if data[pos : pos + 4] == MHOD_MAGIC:
            child = _parse_artwork_mhod(data, pos)
            rec.children.append(child)
            pos += child.total_len
        else:
            break

    return rec


def _parse_mhii_mhod(data: bytes, offset: int) -> Record:
    """Parse MHOD container inside MHII (type=2 wrapping MHNI, or type=5)."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHOD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])
    rec.fields["mhod_type"] = get16lint(data, offset + 0x0C)

    # Parse MHNI child inside this container
    pos = offset + header_len
    end = offset + total_len
    if pos + 4 <= end and data[pos : pos + 4] == MHNI_MAGIC:
        child = _parse_mhni(data, pos)
        rec.children.append(child)

    return rec


def _parse_artwork_mhod(data: bytes, offset: int) -> Record:
    """Parse MHOD in artwork context."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHOD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])
    rec.fields["mhod_type"] = get32lint(data, offset + 0x0C)

    return rec


def _parse_artwork_mhla(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLA in artwork context (album list with MHBA children)."""
    header_len = get32lint(data, offset + 4)
    num_items = get32lint(data, offset + 8)

    rec = Record(MHLA_MAGIC, header_len, num_items)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_items"] = num_items

    # Parse MHBA children (album records)
    pos = offset + header_len
    for _ in range(num_items):
        if pos + 12 > end or pos + 12 > len(data):
            break
        if data[pos : pos + 4] == MHBA_MAGIC:
            child = _parse_mhba(data, pos)
            rec.children.append(child)
            pos += child.total_len
        else:
            break

    rec.raw_bytes = bytes(data[offset:pos])
    return rec


def _parse_mhba(data: bytes, offset: int) -> Record:
    """Parse MHBA (photo album record).

    MHBA contains MHOD children (album name) and MHIA children (photo refs).
    """
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHBA_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    rec.fields["num_mhods"] = get32lint(data, offset + 0x0C)
    rec.fields["num_mhias"] = get32lint(data, offset + 0x10)
    rec.fields["album_id"] = get32lint(data, offset + 0x14)
    if header_len > 0x1E:
        rec.fields["album_type"] = data[offset + 0x1E]

    # Parse children: MHODs (name) followed by MHIAs (photo refs)
    pos = offset + header_len
    end = offset + total_len
    while pos + 8 < end:
        child_magic = data[pos : pos + 4]
        if child_magic == MHOD_MAGIC:
            child = _parse_mhba_mhod(data, pos)
            rec.children.append(child)
            pos += child.total_len
        elif child_magic == b"mhia":
            child = _parse_mhia(data, pos)
            rec.children.append(child)
            pos += child.total_len
        else:
            break

    return rec


def _parse_mhba_mhod(data: bytes, offset: int) -> Record:
    """Parse MHOD inside MHBA (photo album name, UTF-8 string)."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHOD_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])
    rec.fields["mhod_type"] = data[offset + 0x0C]  # Lower byte only

    # Photo album MHODs use UTF-8 strings
    body_offset = offset + header_len
    if body_offset + 12 <= offset + total_len:
        str_len = get32lint(data, body_offset)
        str_start = body_offset + 12
        str_end = str_start + str_len
        if str_end <= offset + total_len:
            try:
                rec.fields["string"] = data[str_start:str_end].decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                logger.debug("Artwork MHOD string decode failed at offset %d", str_start)
                rec.fields["string"] = ""

    return rec


def _parse_mhia(data: bytes, offset: int) -> Record:
    """Parse MHIA (photo album item reference, fixed 40 bytes)."""
    header_len = get32lint(data, offset + 4)

    rec = Record(b"mhia", header_len, header_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + header_len])

    if header_len > 0x10:
        rec.fields["image_id"] = get32lint(data, offset + 0x10)

    return rec


def _parse_mhlf(data: bytes, offset: int, end: int) -> Record:
    """Parse MHLF (image file list)."""
    header_len = get32lint(data, offset + 4)
    num_items = get32lint(data, offset + 8)

    rec = Record(b"mhlf", header_len, num_items)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.fields["num_items"] = num_items

    pos = offset + header_len
    for _ in range(num_items):
        if pos + 4 > end or pos + 4 > len(data):
            break
        if data[pos : pos + 4] == MHIF_MAGIC:
            child = _parse_mhif(data, pos)
            rec.children.append(child)
            pos += child.total_len
        else:
            break

    rec.raw_bytes = bytes(data[offset:pos])
    return rec


def _parse_mhif(data: bytes, offset: int) -> Record:
    """Parse MHIF (image file info)."""
    header_len = get32lint(data, offset + 4)
    total_len = get32lint(data, offset + 8)

    rec = Record(MHIF_MAGIC, header_len, total_len)
    rec.raw_header = bytes(data[offset : offset + header_len])
    rec.raw_bytes = bytes(data[offset : offset + total_len])

    rec.fields["corr_id"] = get32lint(data, offset + 0x0C)
    rec.fields["format_id"] = get32lint(data, offset + 0x10)
    rec.fields["image_size"] = get32lint(data, offset + 0x14)

    return rec
