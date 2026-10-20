"""An explicitly requested checksum scheme is applied or refused, never substituted."""

import os

import pytest

from pygpod.device.device import Device
from pygpod.device.models import ChecksumType
from pygpod.exceptions import HashError
from pygpod.hash.checksum import update_checksums
from pygpod.model.database import Database


def _db():
    data = bytearray(0xC0)
    data[0:4] = b"mhbd"
    return bytes(data)


@pytest.mark.parametrize("hash_type", ["HASHAB", "hashab", "HASH99", ""])
def test_unsupported_explicit_type_raises(hash_type):
    with pytest.raises(HashError):
        update_checksums(_db(), firewire_guid="000A2700213749FF", hash_type=hash_type)


def test_hash58_without_guid_raises():
    with pytest.raises(HashError):
        update_checksums(_db(), hash_type="HASH58")


def test_explicit_type_on_short_data_raises():
    with pytest.raises(HashError):
        update_checksums(b"\x00" * 10, hash_type="HASH72")


def test_none_type_returns_unchanged():
    assert update_checksums(_db(), firewire_guid="000A2700213749FF", hash_type="NONE") == _db()


def test_nano6_save_refuses_before_touching_files(writable_ipod, monkeypatch):
    db = Database(writable_ipod)
    db.save()
    db.tracks[0].title = "nano6"
    itunes = os.path.join(writable_ipod, "iPod_Control", "iTunes")
    before = {n: open(os.path.join(itunes, n), "rb").read() for n in sorted(os.listdir(itunes))}

    monkeypatch.setattr(Device, "requires_hash", property(lambda self: True))
    monkeypatch.setattr(Device, "firewire_guid", property(lambda self: "000A2700213749FF"))
    monkeypatch.setattr(Device, "checksum_type", property(lambda self: ChecksumType.HASHAB))
    with pytest.raises(HashError, match="HASHAB"):
        db.save()
    after = {n: open(os.path.join(itunes, n), "rb").read() for n in sorted(os.listdir(itunes))}
    assert after == before
