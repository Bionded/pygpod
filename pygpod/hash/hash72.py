"""hash72 implementation for iPod Classic 6G+ and Nano 3G+.

Ported from libgpod's itdb_hash72.c. This hash is required for the iPod
to accept the iTunesDB as valid.

The hash72 signature is 46 bytes stored at offset 0x72 in the MHBD header.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# AES key used for hash72 encryption
AES_KEY = bytes(
    [
        0x61,
        0x8C,
        0xA1,
        0x0D,
        0xC7,
        0xF5,
        0x7F,
        0xD3,
        0xB4,
        0x72,
        0x3E,
        0x08,
        0x15,
        0x74,
        0x63,
        0xD7,
    ]
)


def compute_itunesdb_sha1(data: bytes) -> bytes:
    """Compute SHA1 of iTunesDB with hash-related fields zeroed.

    Before hashing, these fields in the MHBD header are zeroed:
    - db_id at offset 0x18 (8 bytes)
    - hash58 at offset 0x58 (26 bytes)
    - hash72 at offset 0x72 (46 bytes)

    Args:
        data: Raw iTunesDB bytes.

    Returns:
        20-byte SHA1 digest.
    """
    buf = bytearray(data)
    # Zero db_id (8 bytes at offset 0x18)
    buf[0x18:0x20] = b"\x00" * 8
    # Zero hash58 (26 bytes at offset 0x58)
    buf[0x58:0x72] = b"\x00" * 26
    # Zero hash72 (46 bytes at offset 0x72)
    buf[0x72 : 0x72 + 46] = b"\x00" * 46
    return hashlib.sha1(bytes(buf)).digest()


def hash_generate(sha1: bytes, iv: bytes, random_bytes: bytes) -> bytes:
    """Generate the 46-byte hash72 signature.

    signature[0]     = 0x01
    signature[1]     = 0x00
    signature[2:14]  = random_bytes[0:12]
    signature[14:46] = AES_CBC_encrypt(key, iv, sha1[0:20] + random_bytes[0:12])

    Args:
        sha1: 20-byte SHA1 digest of the database.
        iv: 16-byte AES IV.
        random_bytes: 12-byte random data.

    Returns:
        46-byte hash72 signature.
    """
    plaintext = sha1[:20] + random_bytes[:12]  # 32 bytes

    # AES-CBC encrypt
    encrypted = _aes_cbc_encrypt(AES_KEY, iv, plaintext)

    sig = bytearray(46)
    sig[0] = 0x01
    sig[1] = 0x00
    sig[2:14] = random_bytes[:12]
    sig[14:46] = encrypted[:32]

    return bytes(sig)


def hash_extract(signature: bytes, sha1: bytes) -> Tuple[bytes, bytes]:
    """Extract IV and random bytes from an existing valid hash72 signature.

    Used to recover device-specific parameters from an existing valid database.

    Args:
        signature: 46-byte hash72 signature.
        sha1: 20-byte SHA1 of the database (with hash fields zeroed).

    Returns:
        Tuple of (iv, random_bytes).
    """
    random_bytes = bytes(signature[2:14])

    # Reconstruct plaintext
    plaintext = sha1[:20] + random_bytes[:12]

    # Decrypt to recover IV
    ciphertext = bytes(signature[14:46])
    iv = _aes_cbc_extract_iv(AES_KEY, ciphertext, plaintext)

    return iv, random_bytes


def compute_hash72(
    db_data: bytes,
    firewire_guid: Optional[str] = None,
    iv: Optional[bytes] = None,
    random_bytes: Optional[bytes] = None,
) -> bytes:
    """Compute and insert hash72 into iTunesDB data.

    If iv and random_bytes are not provided, tries to extract them from
    the existing hash72 in the data. If that fails, generates new random values.

    Args:
        db_data: Raw iTunesDB bytes.
        firewire_guid: Device FirewireGuid (not directly used in hash72,
                       but may be needed for HashInfo file lookup).
        iv: 16-byte AES IV (optional).
        random_bytes: 12-byte random data (optional).

    Returns:
        Modified iTunesDB bytes with updated hash72.
    """
    logger.debug("Computing hash72")
    if len(db_data) < 0x72 + 46:
        return db_data

    # Compute SHA1
    sha1 = compute_itunesdb_sha1(db_data)

    # Get IV and random bytes
    if iv is None or random_bytes is None:
        existing_sig = db_data[0x72 : 0x72 + 46]
        if existing_sig[0] == 0x01:
            try:
                iv, random_bytes = hash_extract(existing_sig, sha1)
            except Exception:
                logger.debug("Failed to extract existing hash72 IV, generating new", exc_info=True)
                iv = os.urandom(16)
                random_bytes = os.urandom(12)
        else:
            iv = os.urandom(16)
            random_bytes = os.urandom(12)

    # Generate new signature
    sig = hash_generate(sha1, iv, random_bytes)

    # Insert into data
    result = bytearray(db_data)
    result[0x72 : 0x72 + 46] = sig

    return bytes(result)


def _get_aes_backend():
    """Find available AES backend: pycryptodome, cryptography, or pure Python."""
    try:
        from Crypto.Cipher import AES  # type: ignore

        def encrypt_cbc(key, iv, plaintext):
            return AES.new(key, AES.MODE_CBC, iv).encrypt(plaintext)

        def decrypt_ecb_block(key, block):
            return AES.new(key, AES.MODE_ECB).decrypt(block[:16])

        return encrypt_cbc, decrypt_ecb_block
    except ImportError:
        pass

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # type: ignore

        def encrypt_cbc(key, iv, plaintext):
            enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
            return enc.update(plaintext) + enc.finalize()

        def decrypt_ecb_block(key, block):
            dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            return dec.update(block[:16]) + dec.finalize()

        return encrypt_cbc, decrypt_ecb_block
    except ImportError:
        pass

    logger.info("Using pure-Python AES fallback (install pycryptodome for better performance)")

    def decrypt_ecb_block(key, block):
        return _aes_ecb_decrypt_pure(key, block[:16])

    return _aes_cbc_encrypt_pure, decrypt_ecb_block


# Lazy-initialized AES backend
_aes_backend = None


def _aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-CBC encryption using best available backend."""
    global _aes_backend
    if _aes_backend is None:
        _aes_backend = _get_aes_backend()
    return _aes_backend[0](key, iv, plaintext)


def _aes_cbc_extract_iv(key: bytes, ciphertext: bytes, plaintext: bytes) -> bytes:
    """Extract IV from AES-CBC by decrypting first block."""
    global _aes_backend
    if _aes_backend is None:
        _aes_backend = _get_aes_backend()
    decrypted = _aes_backend[1](key, ciphertext)
    return bytes(a ^ b for a, b in zip(decrypted, plaintext[:16]))


# ============================================================================
# Pure Python AES implementation (minimal, for when no crypto library available)
# ============================================================================

# fmt: off
# AES S-box
_SBOX = list(b"\x63\x7c\x77\x7b\xf2\x6b\x6f\xc5\x30\x01\x67\x2b\xfe\xd7\xab\x76"
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
# fmt: on

_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _mix_column(col: list) -> list:
    t = col[0] ^ col[1] ^ col[2] ^ col[3]
    u = col[0]
    col[0] ^= _xtime(col[0] ^ col[1]) ^ t
    col[1] ^= _xtime(col[1] ^ col[2]) ^ t
    col[2] ^= _xtime(col[2] ^ col[3]) ^ t
    col[3] ^= _xtime(col[3] ^ u) ^ t
    return col


def _key_expansion(key: bytes) -> list:
    nk = 4  # AES-128
    nr = 10
    w = [0] * (4 * (nr + 1))
    for i in range(nk):
        w[i] = int.from_bytes(key[4 * i : 4 * i + 4], "big")
    for i in range(nk, 4 * (nr + 1)):
        temp = w[i - 1]
        if i % nk == 0:
            # RotWord
            temp = ((temp << 8) | (temp >> 24)) & 0xFFFFFFFF
            # SubWord
            temp = (
                _SBOX[(temp >> 24) & 0xFF] << 24
                | _SBOX[(temp >> 16) & 0xFF] << 16
                | _SBOX[(temp >> 8) & 0xFF] << 8
                | _SBOX[temp & 0xFF]
            )
            temp ^= _RCON[i // nk - 1] << 24
        w[i] = w[i - nk] ^ temp
    return w


def _aes_encrypt_block(state: list, round_keys: list) -> list:
    nr = 10
    # AddRoundKey
    for i in range(4):
        for j in range(4):
            state[i][j] ^= (round_keys[j] >> (24 - 8 * i)) & 0xFF

    for rnd in range(1, nr + 1):
        # SubBytes
        for i in range(4):
            for j in range(4):
                state[i][j] = _SBOX[state[i][j]]
        # ShiftRows
        state[1] = state[1][1:] + state[1][:1]
        state[2] = state[2][2:] + state[2][:2]
        state[3] = state[3][3:] + state[3][:3]
        # MixColumns (not in last round)
        if rnd < nr:
            for j in range(4):
                col = [state[i][j] for i in range(4)]
                col = _mix_column(col)
                for i in range(4):
                    state[i][j] = col[i]
        # AddRoundKey
        rk = round_keys[4 * rnd : 4 * rnd + 4]
        for i in range(4):
            for j in range(4):
                state[i][j] ^= (rk[j] >> (24 - 8 * i)) & 0xFF

    return state


def _aes_decrypt_block(state: list, round_keys: list) -> list:
    nr = 10

    def mul(a, b):
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return p

    # AddRoundKey (last round key)
    rk = round_keys[4 * nr : 4 * nr + 4]
    for i in range(4):
        for j in range(4):
            state[i][j] ^= (rk[j] >> (24 - 8 * i)) & 0xFF

    for rnd in range(nr - 1, -1, -1):
        # InvShiftRows
        state[1] = state[1][-1:] + state[1][:-1]
        state[2] = state[2][-2:] + state[2][:-2]
        state[3] = state[3][-3:] + state[3][:-3]
        # InvSubBytes
        for i in range(4):
            for j in range(4):
                state[i][j] = _INV_SBOX[state[i][j]]
        # AddRoundKey
        rk = round_keys[4 * rnd : 4 * rnd + 4]
        for i in range(4):
            for j in range(4):
                state[i][j] ^= (rk[j] >> (24 - 8 * i)) & 0xFF
        # InvMixColumns (not in round 0)
        if rnd > 0:
            for j in range(4):
                col = [state[i][j] for i in range(4)]
                c = col[:]
                col[0] = mul(c[0], 14) ^ mul(c[1], 11) ^ mul(c[2], 13) ^ mul(c[3], 9)
                col[1] = mul(c[0], 9) ^ mul(c[1], 14) ^ mul(c[2], 11) ^ mul(c[3], 13)
                col[2] = mul(c[0], 13) ^ mul(c[1], 9) ^ mul(c[2], 14) ^ mul(c[3], 11)
                col[3] = mul(c[0], 11) ^ mul(c[1], 13) ^ mul(c[2], 9) ^ mul(c[3], 14)
                for i in range(4):
                    state[i][j] = col[i]

    return state


def _bytes_to_state(data: bytes) -> list:
    return [[data[i + 4 * j] for j in range(4)] for i in range(4)]


def _state_to_bytes(state: list) -> bytes:
    result = bytearray(16)
    for i in range(4):
        for j in range(4):
            result[i + 4 * j] = state[i][j]
    return bytes(result)


def _aes_cbc_encrypt_pure(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    rk = _key_expansion(key)
    result = bytearray()
    prev = iv

    for offset in range(0, len(plaintext), 16):
        block = plaintext[offset : offset + 16]
        # XOR with previous ciphertext (or IV)
        xored = bytes(a ^ b for a, b in zip(block, prev))
        state = _bytes_to_state(xored)
        state = _aes_encrypt_block(state, rk)
        encrypted = _state_to_bytes(state)
        result.extend(encrypted)
        prev = encrypted

    return bytes(result)


def _aes_ecb_decrypt_pure(key: bytes, ciphertext: bytes) -> bytes:
    rk = _key_expansion(key)
    state = _bytes_to_state(ciphertext[:16])
    state = _aes_decrypt_block(state, rk)
    return _state_to_bytes(state)
