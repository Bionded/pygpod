"""General checksum utilities for iPod databases."""

from __future__ import annotations

import logging
from typing import Optional

from .hash72 import compute_hash72

logger = logging.getLogger(__name__)


def update_checksums(db_data: bytes, firewire_guid: Optional[str] = None) -> bytes:
    """Update all checksums in iTunesDB data.

    Detects the hashing scheme and applies the appropriate hash.
    Supports hash58 (older iPods) and hash72 (Classic/Nano).

    Args:
        db_data: Raw iTunesDB bytes.
        firewire_guid: Device FirewireGuid (needed for hash58).

    Returns:
        iTunesDB bytes with updated checksums.
    """
    logger.info("Updating checksums")
    if len(db_data) < 0x72 + 46:
        return db_data

    # Check existing hash type by looking at hash72 signature byte
    existing_sig = db_data[0x72 : 0x72 + 46]

    if existing_sig[0] == 0x01:
        # Has hash72 - update it
        logger.debug("Applied hash type: %s", "hash72")
        return compute_hash72(db_data, firewire_guid)

    # Compute hash58 when GUID is available - covers both fresh databases
    # and databases with existing hash58 data
    if firewire_guid and len(db_data) >= 0x58 + 20:
        from .hash58 import compute_hash58

        logger.debug("Applied hash type: %s", "hash58")
        return compute_hash58(db_data, firewire_guid)

    # No hash or hash not computable - return as-is
    return db_data
