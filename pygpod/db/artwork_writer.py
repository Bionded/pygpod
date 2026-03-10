"""ArtworkDB binary writer.

Writes back the ArtworkDB in binary format, preserving round-trip integrity.
"""

from __future__ import annotations

import logging

from ..utils.compat import put32lint
from .parser import Record

logger = logging.getLogger(__name__)


def write_artworkdb(root: Record) -> bytes:
    """Serialize a parsed ArtworkDB record tree back to binary.

    For round-trip integrity, uses raw_bytes for unmodified records.

    Args:
        root: Root MHFD Record.

    Returns:
        Complete ArtworkDB binary data.
    """
    logger.debug("Writing ArtworkDB")
    if not root.children:
        return root.raw_bytes

    header = bytearray(root.raw_header)
    children_data = b""
    for child in root.children:
        children_data += _write_artwork_record(child)

    total_len = len(header) + len(children_data)
    put32lint(header, 8, total_len)
    # Update num_children at offset 0x14 (MHFD struct field)
    if len(header) > 0x18:
        put32lint(header, 0x14, len(root.children))

    return bytes(header) + children_data


# Records where offset 8 is num_items (count), not total_len
_COUNT_MAGICS = {b"mhli", b"mhlf", b"mhla"}


def _write_artwork_record(rec: Record) -> bytes:
    """Write an artwork record, falling back to raw_bytes."""
    if not rec.children:
        return rec.raw_bytes

    header = bytearray(rec.raw_header)
    children_data = b""
    for child in rec.children:
        children_data += _write_artwork_record(child)

    total_len = len(header) + len(children_data)
    if len(header) >= 12:
        if rec.magic in _COUNT_MAGICS:
            # MHLI, MHLF, MHLA store num_items at offset 8
            put32lint(header, 8, len(rec.children))
        else:
            put32lint(header, 8, total_len)

    return bytes(header) + children_data
