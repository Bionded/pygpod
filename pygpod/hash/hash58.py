"""hash58 implementation for iPod 5G, Nano 1-2, and older.

Ported from libgpod's itdb_hash58.c. Uses HMAC-SHA1 with a key derived
from the device's FirewireGuid via two substitution tables.

The hash58 signature is 20 bytes stored at offset 0x58 in the MHBD header.
"""

from __future__ import annotations

import hashlib
import logging
from math import gcd

logger = logging.getLogger(__name__)

# fmt: off
# Substitution table 1 (256 bytes) - from libgpod (AES S-box)
_TABLE1 = (b"\x63\x7c\x77\x7b\xf2\x6b\x6f\xc5\x30\x01\x67\x2b\xfe\xd7\xab\x76"
           b"\xca\x82\xc9\x7d\xfa\x59\x47\xf0\xad\xd4\xa2\xaf\x9c\xa4\x72\xc0"
           b"\xb7\xfd\x93\x26\x36\x3f\xf7\xcc\x34\xa5\xe5\xf1\x71\xd8\x31\x15"
           b"\x04\xc7\x23\xc3\x18\x96\x05\x9a\x07\x12\x80\xe2\xeb\x27\xb2\x75"
           b"\x09\x83\x2c\x1a\x1b\x6e\x5a\xa0\x52\x3b\xd6\xb3\x29\xe3\x2f\x84"
           b"\x53\xd1\x00\xed\x20\xfc\xb1\x5b\x6a\xcb\xbe\x39\x4a\x4c\x58\xcf"
           b"\xd0\xef\xaa\xfb\x43\x4d\x33\x85\x45\xf9\x02\x7f\x50\x3c\x9f\xa8"
           b"\x51\xa3\x40\x8f\x92\x9d\x38\xf5\xbc\xb6\xda\x21\x10\xff\xf3\xd2"
           b"\xcd\x0c\x13\xec\x5f\x97\x44\x17\xc4\xa7\x7e\x3d\x64\x5d\x19\x73"
           b"\x60\x81\x4f\xdc\x22\x2a\x90\x88\x46\xee\xb8\x14\xde\x5e\x0b\xdb"
           b"\xe0\x32\x3a\x0a\x49\x06\x24\x5c\xc2\xd3\xac\x62\x91\x95\xe4\x79"
           b"\xe7\xc8\x37\x6d\x8d\xd5\x4e\xa9\x6c\x56\xf4\xea\x65\x7a\xae\x08"
           b"\xba\x78\x25\x2e\x1c\xa6\xb4\xc6\xe8\xdd\x74\x1f\x4b\xbd\x8b\x8a"
           b"\x70\x3e\xb5\x66\x48\x03\xf6\x0e\x61\x35\x57\xb9\x86\xc1\x1d\x9e"
           b"\xe1\xf8\x98\x11\x69\xd9\x8e\x94\x9b\x1e\x87\xe9\xce\x55\x28\xdf"
           b"\x8c\xa1\x89\x0d\xbf\xe6\x42\x68\x41\x99\x2d\x0f\xb0\x54\xbb\x16")

# Substitution table 2 (256 bytes) - from libgpod (inverse S-box permutation)
_TABLE2 = (b"\x52\x09\x6a\xd5\x30\x36\xa5\x38\xbf\x40\xa3\x9e\x81\xf3\xd7\xfb"
           b"\x7c\xe3\x39\x82\x9b\x2f\xff\x87\x34\x8e\x43\x44\xc4\xde\xe9\xcb"
           b"\x54\x7b\x94\x32\xa6\xc2\x23\x3d\xee\x4c\x95\x0b\x42\xfa\xc3\x4e"
           b"\x08\x2e\xa1\x66\x28\xd9\x24\xb2\x76\x5b\xa2\x49\x6d\x8b\xd1\x25"
           b"\x72\xf8\xf6\x64\x86\x68\x98\x16\xd4\xa4\x5c\xcc\x5d\x65\xb6\x92"
           b"\x6c\x70\x48\x50\xfd\xed\xb9\xda\x5e\x15\x46\x57\xa7\x8d\x9d\x84"
           b"\x90\xd8\xab\x00\x8c\xbc\xd3\x0a\xf7\xe4\x58\x05\xb8\xb3\x45\x06"
           b"\xd0\x2c\x1e\x8f\xca\x3f\x0f\x02\xc1\xaf\xbd\x03\x01\x13\x8a\x6b"
           b"\x3a\x91\x11\x41\x4f\x67\xdc\xea\x97\xf2\xcf\xce\xf0\xb4\xe6\x73"
           b"\x96\xac\x74\x22\xe7\xad\x35\x85\xe2\xf9\x37\xe8\x1c\x75\xdf\x6e"
           b"\x47\xf1\x1a\x71\x1d\x29\xc5\x89\x6f\xb7\x62\x0e\xaa\x18\xbe\x1b"
           b"\xfc\x56\x3e\x4b\xc6\xd2\x79\x20\x9a\xdb\xc0\xfe\x78\xcd\x5a\xf4"
           b"\x1f\xdd\xa8\x33\x88\x07\xc7\x31\xb1\x12\x10\x59\x27\x80\xec\x5f"
           b"\x60\x51\x7f\xa9\x19\xb5\x4a\x0d\x2d\xe5\x7a\x9f\x93\xc9\x9c\xef"
           b"\xa0\xe0\x3b\x4d\xae\x2a\xf5\xb0\xc8\xeb\xbb\x3c\x83\x53\x99\x61"
           b"\x17\x2b\x04\x7e\xba\x77\xd6\x26\xe1\x69\x14\x63\x55\x21\x0c\x7d")

# Fixed 18-byte array used in key derivation
_FIXED_ARRAY = b"\x67\x23\xfe\x30\x45\x33\xf8\x90\x99\x21\x07\xc1\xd0\x12\xb2\xa1\x07\x81"
# fmt: on

# Hash58 field offset and length in MHBD header
HASH58_OFFSET = 0x58
HASH58_LEN = 20


def _lcm(a: int, b: int) -> int:
    """Compute least common multiple.

    Returns 1 (not 0) when either argument is 0, matching libgpod behavior.
    """
    if a == 0 or b == 0:
        return 1
    return abs(a * b) // gcd(a, b)


def _derive_key(firewire_guid: str) -> bytes:
    """Derive the 64-byte HMAC key from a FirewireGuid.

    Args:
        firewire_guid: 16-char hex string (e.g., '000A2700131A2BFC').

    Returns:
        64-byte HMAC key.
    """
    # Parse firewire GUID into bytes
    guid = firewire_guid.replace("0x", "").replace(" ", "")
    if len(guid) < 16:
        guid = guid.zfill(16)
    guid_bytes = bytes.fromhex(guid[:16])

    # Process pairs of bytes through LCM + table lookups
    intermediate = bytearray(16)
    for i in range(4):
        a = guid_bytes[i * 2]
        b = guid_bytes[i * 2 + 1]
        lcm_val = _lcm(a, b) & 0xFFFF
        hi = (lcm_val >> 8) & 0xFF
        lo = lcm_val & 0xFF
        intermediate[i * 4] = _TABLE1[hi]
        intermediate[i * 4 + 1] = _TABLE2[hi]
        intermediate[i * 4 + 2] = _TABLE1[lo]
        intermediate[i * 4 + 3] = _TABLE2[lo]

    # Derive key via SHA1 with fixed prefix, zero-pad to 64 bytes
    sha = hashlib.sha1()
    sha.update(_FIXED_ARRAY)
    sha.update(intermediate)
    digest = sha.digest()  # 20 bytes

    # Zero-pad to 64 bytes (standard HMAC key padding)
    return digest + b"\x00" * 44


def compute_itunesdb_sha1_58(data: bytes) -> bytes:
    """Compute SHA1 of iTunesDB for hash58 with relevant fields zeroed.

    Before hashing, these fields in the MHBD header are zeroed:
    - db_id at offset 0x18 (8 bytes)
    - hash58 at offset 0x58 (20 bytes)

    Args:
        data: Raw iTunesDB bytes.

    Returns:
        20-byte SHA1 digest.
    """
    buf = bytearray(data)
    # Zero db_id (8 bytes at offset 0x18)
    buf[0x18:0x20] = b"\x00" * 8
    # Zero hash58 (20 bytes at offset 0x58)
    buf[HASH58_OFFSET : HASH58_OFFSET + HASH58_LEN] = b"\x00" * HASH58_LEN
    # Also zero hash72 if present (46 bytes at offset 0x72)
    if len(buf) >= 0x72 + 46:
        buf[0x72 : 0x72 + 46] = b"\x00" * 46
    return hashlib.sha1(bytes(buf)).digest()


def _hmac_sha1(key: bytes, data: bytes) -> bytes:
    """Compute HMAC-SHA1 manually.

    Args:
        key: 64-byte key.
        data: Data to authenticate.

    Returns:
        20-byte HMAC digest.
    """
    if len(key) < 64:
        key = key + b"\x00" * (64 - len(key))
    elif len(key) > 64:
        key = hashlib.sha1(key).digest() + b"\x00" * 44

    # Inner padding
    ipad = bytes(k ^ 0x36 for k in key)
    # Outer padding
    opad = bytes(k ^ 0x5C for k in key)

    # HMAC = SHA1(opad || SHA1(ipad || data))
    inner = hashlib.sha1(ipad + data).digest()
    return hashlib.sha1(opad + inner).digest()


def compute_hash58(db_data: bytes, firewire_guid: str) -> bytes:
    """Compute and insert hash58 into iTunesDB data.

    Args:
        db_data: Raw iTunesDB bytes.
        firewire_guid: Device FirewireGuid string.

    Returns:
        Modified iTunesDB bytes with updated hash58.
    """
    logger.debug("Computing hash58 for GUID %s", firewire_guid)
    if len(db_data) < HASH58_OFFSET + HASH58_LEN:
        return db_data

    # Derive HMAC key from firewire GUID
    key = _derive_key(firewire_guid)

    # Zero hash-sensitive fields before computing HMAC (matches libgpod)
    buf = bytearray(db_data)
    buf[0x18:0x20] = b"\x00" * 8  # db_id
    buf[HASH58_OFFSET : HASH58_OFFSET + HASH58_LEN] = b"\x00" * HASH58_LEN  # hash58

    # Compute HMAC-SHA1
    hmac_digest = _hmac_sha1(key, bytes(buf))

    # Insert hash into data
    result = bytearray(db_data)
    result[HASH58_OFFSET : HASH58_OFFSET + HASH58_LEN] = hmac_digest

    return bytes(result)
