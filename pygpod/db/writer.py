"""iTunesDB binary writer.

Serializes a tree of Record objects back to binary format.
Preserves unknown fields and padding from original data for round-trip integrity.
"""

from __future__ import annotations

import logging

from ..utils.compat import put32lint
from ..utils.encoding import encode_mhod_string
from .constants import (
    MHIA_MAGIC,
    MHII_MAGIC,
    MHIP_MAGIC,
    MHIT_MAGIC,
    MHLA_MAGIC,
    MHLI_MAGIC,
    MHLP_MAGIC,
    MHLT_MAGIC,
    MHOD_MAGIC,
    MHSD_MAGIC,
    MHYP_MAGIC,
    STRING_MHOD_TYPES,
)
from .parser import Record

logger = logging.getLogger(__name__)


def write_itunesdb(root: Record) -> bytes:
    """Serialize a parsed iTunesDB record tree back to binary.

    For round-trip integrity, this uses raw_header bytes as the base and
    only updates size fields that may have changed.

    Args:
        root: Root MHBD Record from parse_itunesdb().

    Returns:
        Complete iTunesDB binary data.
    """
    logger.debug("Writing iTunesDB")
    result = _write_mhbd(root)
    logger.debug("Wrote %d bytes", len(result))
    return result


def _write_mhbd(rec: Record) -> bytes:
    """Write MHBD (database header) record."""
    header = bytearray(rec.raw_header)
    children_data = _write_children(rec)
    put32lint(header, 8, len(header) + len(children_data))
    put32lint(header, 0x14, len(rec.children))
    return bytes(header) + children_data


# Records that are list containers: header stores child count at offset 8
_LIST_RECORDS = {MHLT_MAGIC, MHLP_MAGIC, MHLA_MAGIC, MHLI_MAGIC}

# Records that are items with MHOD children: header stores MHOD count at 0x0C and total_len at 8
_ITEM_RECORDS = {MHIT_MAGIC, MHIP_MAGIC, MHIA_MAGIC, MHII_MAGIC}


def _write_record(rec: Record) -> bytes:
    """Dispatch record writing based on magic type."""
    if rec.magic == MHSD_MAGIC:
        return _write_mhsd(rec)
    elif rec.magic in _LIST_RECORDS:
        return _write_list_record(rec)
    elif rec.magic == MHYP_MAGIC:
        return _write_mhyp(rec)
    elif rec.magic in _ITEM_RECORDS:
        return _write_item_record(rec)
    elif rec.magic == MHOD_MAGIC:
        return _write_mhod(rec)
    else:
        return rec.raw_bytes


def _write_mhsd(rec: Record) -> bytes:
    """Write MHSD (section descriptor) record."""
    if not rec.children:
        # No parsed children - use raw_bytes verbatim to preserve content
        return rec.raw_bytes

    header = bytearray(rec.raw_header)

    children_data = b""
    for child in rec.children:
        children_data += _write_record(child)

    total_len = len(header) + len(children_data)
    put32lint(header, 8, total_len)

    return bytes(header) + children_data


def _write_children(rec: Record) -> bytes:
    """Serialize all children of a record."""
    return b"".join(_write_record(c) for c in rec.children)


def _write_list_record(rec: Record) -> bytes:
    """Write a list container record (MHLT, MHLP, MHLA, MHLI).

    These store child count at offset 8, no total_len field.
    """
    header = bytearray(rec.raw_header)
    put32lint(header, 8, len(rec.children))
    return bytes(header) + _write_children(rec)


def _write_item_record(rec: Record) -> bytes:
    """Write an item record with MHOD children (MHIT, MHIP, MHIA, MHII).

    These store MHOD count at offset 0x0C and total_len at offset 8.
    """
    header = bytearray(rec.raw_header)
    mhod_count = sum(1 for c in rec.children if c.magic == MHOD_MAGIC)
    put32lint(header, 0x0C, mhod_count)
    children_data = _write_children(rec)
    put32lint(header, 8, len(header) + len(children_data))
    return bytes(header) + children_data


def _write_mhod(rec: Record) -> bytes:
    """Write MHOD (data object) record.

    For round-trip integrity, use raw_bytes if available and no modifications
    were made. Otherwise reconstruct from fields.
    """
    mhod_type = rec.fields.get("mhod_type", 0)

    if mhod_type in STRING_MHOD_TYPES and "string" in rec.fields:
        return _write_string_mhod(rec)
    else:
        # For non-string MHODs or unmodified ones, use raw bytes
        return rec.raw_bytes


def _write_string_mhod(rec: Record) -> bytes:
    """Write a string-type MHOD record."""
    mhod_type = rec.fields["mhod_type"]
    encoding = rec.fields.get("string_encoding", 0)
    unk1 = rec.fields.get("string_unk1", 1)
    unk2 = rec.fields.get("string_unk2", 0)

    # Use raw string bytes if available (preserves exact encoding)
    if "string_raw" in rec.fields:
        str_bytes = rec.fields["string_raw"]
    else:
        str_bytes = encode_mhod_string(rec.fields["string"], encoding)

    # Build header
    header_len = rec.header_len  # Preserve original header length
    header = bytearray(rec.raw_header) if rec.raw_header else bytearray(header_len)

    if not rec.raw_header:
        # Build fresh header
        header[0:4] = MHOD_MAGIC
        put32lint(header, 4, header_len)
        put32lint(header, 0x0C, mhod_type)

    # Build body: encoding(4) + str_len(4) + unk1(4) + unk2(4) + string_data
    body = bytearray(16 + len(str_bytes))
    put32lint(body, 0, encoding)
    put32lint(body, 4, len(str_bytes))
    put32lint(body, 8, unk1)
    put32lint(body, 12, unk2)
    body[16 : 16 + len(str_bytes)] = str_bytes

    total_len = header_len + len(body)
    put32lint(header, 8, total_len)

    return bytes(header) + bytes(body)


def _write_mhyp(rec: Record) -> bytes:
    """Write MHYP (playlist) record - counts both MHODs and MHIPs."""
    header = bytearray(rec.raw_header)
    mhod_count = 0
    mhip_count = 0
    for c in rec.children:
        if c.magic == MHOD_MAGIC:
            mhod_count += 1
        elif c.magic == MHIP_MAGIC:
            mhip_count += 1
    put32lint(header, 0x0C, mhod_count)
    put32lint(header, 0x10, mhip_count)
    children_data = _write_children(rec)
    put32lint(header, 8, len(header) + len(children_data))
    return bytes(header) + children_data


# ============================================================================
# Helper functions for creating new records
# ============================================================================


def make_string_mhod(mhod_type: int, text: str, encoding: int = 0, header_len: int = 24) -> Record:
    """Create a new string MHOD record.

    Args:
        mhod_type: MHOD type identifier.
        text: String content.
        encoding: String encoding (0=UTF-16LE, 2=UTF-8).
        header_len: Header length (default 24).

    Returns:
        New Record object.
    """
    str_bytes = encode_mhod_string(text, encoding)
    body_len = 16 + len(str_bytes)
    total_len = header_len + body_len

    rec = Record(MHOD_MAGIC, header_len, total_len)
    rec.fields["mhod_type"] = mhod_type
    rec.fields["string"] = text
    rec.fields["string_encoding"] = encoding
    rec.fields["string_unk1"] = 1
    rec.fields["string_unk2"] = 0
    rec.fields["string_raw"] = str_bytes

    # Build raw header
    header = bytearray(header_len)
    header[0:4] = MHOD_MAGIC
    put32lint(header, 4, header_len)
    put32lint(header, 8, total_len)
    put32lint(header, 0x0C, mhod_type)
    rec.raw_header = bytes(header)

    # Build raw_bytes for fallback
    body = bytearray(body_len)
    put32lint(body, 0, encoding)
    put32lint(body, 4, len(str_bytes))
    put32lint(body, 8, 1)
    put32lint(body, 12, 0)
    body[16 : 16 + len(str_bytes)] = str_bytes
    rec.raw_bytes = bytes(header) + bytes(body)

    return rec
