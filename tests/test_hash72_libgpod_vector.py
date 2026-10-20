"""hash72 against a libgpod-signed reference database (tests/data/hash72_libgpod, README)."""

import os
import shutil

import pytest

from pygpod.exceptions import HashError
from pygpod.hash.hash72 import (
    compute_hash72,
    compute_itunesdb_sha1,
    extract_hash72_params,
    read_hash_info,
)
from pygpod.model.database import Database

VECTOR = os.path.join(os.path.dirname(__file__), "data", "hash72_libgpod")
GUID = "0x000A270012345678"
IV = bytes(range(0x64, 0x74))
RND = bytes(range(1, 13))


def _read(name):
    with open(os.path.join(VECTOR, name), "rb") as f:
        return f.read()


@pytest.fixture
def nano(tmp_path):
    mp = tmp_path / "nano"
    for d in ("iTunes", "Device", "Music/F00"):
        (mp / "iPod_Control" / d).mkdir(parents=True)
    shutil.copy(os.path.join(VECTOR, "iTunesDB"), mp / "iPod_Control" / "iTunes" / "iTunesDB")
    for name in ("SysInfo", "SysInfoExtended", "HashInfo"):
        shutil.copy(os.path.join(VECTOR, name), mp / "iPod_Control" / "Device" / name)
    return mp


def test_signature_identical_to_libgpod():
    db = _read("iTunesDB")
    assert compute_hash72(db, iv=IV, random_bytes=RND) == db
    assert extract_hash72_params(db) == (IV, RND)
    assert read_hash_info(os.path.join(VECTOR, "HashInfo"), GUID) == (IV, RND)


def test_sha1_covers_timezone_and_field_0x70():
    db = bytearray(_read("iTunesDB"))
    before = compute_itunesdb_sha1(bytes(db))
    db[0x70] ^= 0xFF  # not part of hash58: libgpod hashes it
    assert compute_itunesdb_sha1(bytes(db)) != before
    db = bytearray(_read("iTunesDB"))
    db[0x32] ^= 0xFF  # unk_0x32 is zeroed by libgpod
    assert compute_itunesdb_sha1(bytes(db)) == before


def test_edited_db_resigned_with_the_device_iv(nano):
    db = Database(str(nano))
    assert db._device.checksum_type.name == "HASH72"
    db.tracks[0].title = "Edited"
    db.save()
    data = (nano / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert extract_hash72_params(data) == (IV, RND)
    assert compute_hash72(data, iv=IV, random_bytes=RND) == data
    assert Database(str(nano)).tracks[0].title == "Edited"


def test_hash_info_created_from_signed_db(nano):
    (nano / "iPod_Control" / "Device" / "HashInfo").unlink()
    db = Database(str(nano))
    db.tracks[0].title = "Edited"
    db.save()
    hi = nano / "iPod_Control" / "Device" / "HashInfo"
    assert hi.read_bytes() == _read("HashInfo")
    data = (nano / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert extract_hash72_params(data) == (IV, RND)


def test_no_iv_available_refuses_to_write(nano):
    (nano / "iPod_Control" / "Device" / "HashInfo").unlink()
    path = nano / "iPod_Control" / "iTunes" / "iTunesDB"
    unsigned = bytearray(path.read_bytes())
    unsigned[0x72 : 0x72 + 46] = bytes(46)
    path.write_bytes(bytes(unsigned))
    db = Database(str(nano))
    db.tracks[0].title = "x"
    with pytest.raises(HashError, match="HashInfo"):
        db.save()
    assert path.read_bytes() == bytes(unsigned)


def test_hash_info_of_another_device_ignored(tmp_path):
    other = tmp_path / "HashInfo"
    other.write_bytes(_read("HashInfo"))
    assert read_hash_info(str(other), "0x000A27FFFFFFFFFF") is None
    assert read_hash_info(str(tmp_path / "missing"), GUID) is None
