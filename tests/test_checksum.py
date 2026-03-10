"""Tests for hash58, hash72, and checksum dispatcher."""

import hashlib
import struct


# ============================================================================
# hash58
# ============================================================================
class TestHash58:

    def test_derive_key_length(self):
        from pygpod.hash.hash58 import _derive_key
        key = _derive_key("000A2700213749FF")
        assert len(key) == 64

    def test_derive_key_deterministic(self):
        from pygpod.hash.hash58 import _derive_key
        k1 = _derive_key("000A2700213749FF")
        k2 = _derive_key("000A2700213749FF")
        assert k1 == k2

    def test_derive_key_different_guids(self):
        from pygpod.hash.hash58 import _derive_key
        k1 = _derive_key("000A2700213749FF")
        k2 = _derive_key("FFFFFFFFFFFFFFFF")
        assert k1 != k2

    def test_hmac_sha1(self):
        from pygpod.hash.hash58 import _hmac_sha1
        # RFC 2104 test vector
        key = b"\x0b" * 20 + b"\x00" * 44
        data = b"Hi There"
        result = _hmac_sha1(key, data)
        assert len(result) == 20
        # Verify against Python's hmac
        import hmac as hmac_mod
        expected = hmac_mod.new(key[:20], data, hashlib.sha1).digest()
        assert result == expected

    def test_hmac_sha1_short_key(self):
        from pygpod.hash.hash58 import _hmac_sha1
        key = b"short"
        result = _hmac_sha1(key, b"data")
        assert len(result) == 20

    def test_compute_hash58_roundtrip(self):
        from pygpod.hash.hash58 import HASH58_LEN, HASH58_OFFSET, compute_hash58
        # Create minimal MHBD header (large enough for hash fields)
        data = bytearray(0xA0)
        data[0:4] = b"mhbd"
        struct.pack_into("<I", data, 4, len(data))

        guid = "000A2700213749FF"
        result = compute_hash58(bytes(data), guid)
        assert len(result) == len(data)
        # Hash should be non-zero at offset 0x58
        assert result[HASH58_OFFSET:HASH58_OFFSET + HASH58_LEN] != b"\x00" * HASH58_LEN

    def test_compute_hash58_idempotent(self):
        from pygpod.hash.hash58 import compute_hash58
        data = bytearray(0xA0)
        data[0:4] = b"mhbd"
        guid = "000A2700213749FF"
        r1 = compute_hash58(bytes(data), guid)
        r2 = compute_hash58(r1, guid)
        # Applying hash58 again should produce the same result
        assert r1[0x58:0x58 + 20] == r2[0x58:0x58 + 20]

    def test_compute_hash58_too_small(self):
        from pygpod.hash.hash58 import compute_hash58
        data = b"\x00" * 10
        result = compute_hash58(data, "000A2700213749FF")
        assert result == data  # unchanged

    def test_lcm(self):
        from pygpod.hash.hash58 import _lcm
        assert _lcm(4, 6) == 12
        assert _lcm(0, 5) == 1
        assert _lcm(7, 0) == 1
        assert _lcm(3, 3) == 3

    def test_compute_itunesdb_sha1_58(self):
        from pygpod.hash.hash58 import compute_itunesdb_sha1_58
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        data[0x18:0x20] = b"\xFF" * 8  # db_id
        data[0x58:0x6C] = b"\xFF" * 20  # hash58
        result = compute_itunesdb_sha1_58(bytes(data))
        assert len(result) == 20
        # With fields zeroed, SHA1 should be different from raw
        raw_sha = hashlib.sha1(data).digest()
        assert result != raw_sha


# ============================================================================
# hash72
# ============================================================================
class TestHash72:

    def test_compute_itunesdb_sha1(self):
        from pygpod.hash.hash72 import compute_itunesdb_sha1
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        result = compute_itunesdb_sha1(bytes(data))
        assert len(result) == 20

    def test_hash_generate(self):
        from pygpod.hash.hash72 import hash_generate
        sha1 = b"\xAA" * 20
        iv = b"\xBB" * 16
        rand = b"\xCC" * 12
        sig = hash_generate(sha1, iv, rand)
        assert len(sig) == 46
        assert sig[0] == 0x01
        assert sig[1] == 0x00
        assert sig[2:14] == rand

    def test_compute_hash72_roundtrip(self):
        from pygpod.hash.hash72 import compute_hash72
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        struct.pack_into("<I", data, 4, len(data))

        result = compute_hash72(bytes(data))
        assert len(result) == len(data)
        # Signature byte should be 0x01
        assert result[0x72] == 0x01

    def test_compute_hash72_with_explicit_params(self):
        from pygpod.hash.hash72 import compute_hash72
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        iv = b"\x11" * 16
        rand = b"\x22" * 12
        result = compute_hash72(bytes(data), iv=iv, random_bytes=rand)
        assert result[0x72] == 0x01
        assert result[0x74:0x80] == rand

    def test_hash_extract(self):
        from pygpod.hash.hash72 import compute_itunesdb_sha1, hash_extract, hash_generate
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        sha1 = compute_itunesdb_sha1(bytes(data))
        iv = b"\x33" * 16
        rand = b"\x44" * 12
        sig = hash_generate(sha1, iv, rand)
        # Extract should recover the same random bytes
        _, recovered_rand = hash_extract(sig, sha1)
        assert recovered_rand == rand

    def test_pure_python_aes(self):
        from pygpod.hash.hash72 import _aes_cbc_encrypt_pure
        key = bytes(range(16))
        iv = bytes(range(16, 32))
        plaintext = bytes(range(32, 64))
        encrypted = _aes_cbc_encrypt_pure(key, iv, plaintext)
        assert len(encrypted) == 32
        assert encrypted != plaintext

    def test_aes_ecb_decrypt_inverts_encrypt(self):
        from pygpod.hash.hash72 import (
            _aes_decrypt_block,
            _aes_encrypt_block,
            _bytes_to_state,
            _key_expansion,
            _state_to_bytes,
        )
        key = bytes(range(16))
        rk = _key_expansion(key)
        plaintext = bytes(range(16))
        state = _bytes_to_state(plaintext)
        enc_state = _aes_encrypt_block(state, rk)
        enc_bytes = _state_to_bytes(enc_state)

        dec_state = _bytes_to_state(enc_bytes)
        dec_state = _aes_decrypt_block(dec_state, rk)
        dec_bytes = _state_to_bytes(dec_state)
        assert dec_bytes == plaintext


# ============================================================================
# checksum dispatcher
# ============================================================================
class TestChecksumDispatcher:

    def test_update_checksums_hash72(self):
        from pygpod.hash.checksum import update_checksums
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        # Set hash72 signature byte to indicate hash72
        data[0x72] = 0x01
        result = update_checksums(bytes(data))
        assert result[0x72] == 0x01

    def test_update_checksums_hash58(self):
        from pygpod.hash.checksum import update_checksums
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        # No hash72 signature, but provide GUID
        data[0x72] = 0x00
        result = update_checksums(bytes(data), firewire_guid="000A2700213749FF")
        # hash58 should be applied
        assert result[0x58:0x58 + 20] != b"\x00" * 20

    def test_update_checksums_no_hash(self):
        from pygpod.hash.checksum import update_checksums
        data = bytearray(0xC0)
        data[0:4] = b"mhbd"
        data[0x72] = 0x00
        # No GUID → should return as-is
        result = update_checksums(bytes(data))
        assert result == bytes(data)

    def test_update_checksums_too_small(self):
        from pygpod.hash.checksum import update_checksums
        data = b"\x00" * 10
        result = update_checksums(data)
        assert result == data
