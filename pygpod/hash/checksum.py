"""General checksum utilities for iPod databases."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from ..exceptions import HashError
from .hash58 import compute_hash58
from .hash72 import compute_hash72

logger = logging.getLogger(__name__)


def detect_checksum_type(db_data: bytes) -> Optional[str]:
    """The signature scheme an iTunesDB carries: "HASH72" (signature marker 0x01 at 0x72),
    "HASH58" (non-zero hash58 area 0x58..0x6C) or None (unsigned or too short).
    """
    if len(db_data) < 0x72 + 46:
        return None
    if db_data[0x72] == 0x01:
        return "HASH72"
    if any(db_data[0x58:0x6C]):
        return "HASH58"
    return None


def update_checksums(
    db_data: bytes,
    firewire_guid: Optional[str] = None,
    hash_type: Optional[str] = None,
    hash72_params: Optional[Tuple[bytes, bytes]] = None,
) -> bytes:
    """Update all checksums in iTunesDB data.

    With ``hash_type`` set, exactly that scheme is applied; without it the
    scheme is detected from the data (hash72 signature, else hash58 when a
    GUID is given, else unchanged).

    Args:
        db_data: Raw iTunesDB bytes.
        firewire_guid: Device FirewireGuid (needed for hash58).
        hash_type: "HASH58", "HASH72", "NONE" or None (auto-detect).
        hash72_params: (iv, random_bytes) of the device for hash72 (HashInfo).

    Returns:
        iTunesDB bytes with updated checksums.

    Raises:
        HashError: for an explicit scheme that is not supported (e.g. "HASHAB",
            used by the Nano 6G) or cannot be computed. Never substitutes
            another scheme: the iPod would reject the database.
    """

    logger.info("Updating checksums")
    if hash_type is not None and hash_type not in ("HASH58", "HASH72", "NONE"):
        raise HashError(
            f"Unsupported database checksum type {hash_type!r}; refusing to write a database "
            "this iPod would not accept"
        )
    if hash_type == "NONE":
        return db_data
    if len(db_data) < 0x72 + 46:
        if hash_type is not None:
            raise HashError(f"Database too short ({len(db_data)} bytes) to apply {hash_type}")
        return db_data

    # New hash algorithm - @WoodcraftWorld
    if hash_type == "HASH58":
        if not firewire_guid:
            raise HashError("HASH58 requires the device FirewireGuid")
        logger.debug("Applied hash type: %s", "hash58")
        return compute_hash58(db_data, firewire_guid)
    elif hash_type == "HASH72":
        logger.debug("Applied hash type: %s", "hash72")
        iv, rnd = hash72_params if hash72_params else (None, None)
        return compute_hash72(db_data, firewire_guid, iv=iv, random_bytes=rnd)
    # Check existing hash type by looking at hash72 signature byte

    existing_sig = db_data[0x72 : 0x72 + 46]

    if existing_sig[0] == 0x01:
        # Has hash72 - update it
        logger.debug("Applied hash type: %s", "hash72")
        return compute_hash72(db_data, firewire_guid)

    # Compute hash58 when GUID is available - covers both fresh databases
    # and databases with existing hash58 data
    if firewire_guid and len(db_data) >= 0x58 + 20:
        logger.debug("Applied hash type: %s", "hash58")
        return compute_hash58(db_data, firewire_guid)
    # No hash or hash not computable - return as-is
    return db_data
