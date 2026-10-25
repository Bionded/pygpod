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


GUID = "000A270012345678"


def _signed_classic(tmp_path):
    from pygpod import init_ipod
    from pygpod.hash.hash58 import compute_hash58

    mp = tmp_path / "ipod"
    mp.mkdir()
    init_ipod(
        str(mp),
        name="iPod",
        sysinfo_files={"SysInfo": f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n".encode()},
    )
    signed = (mp / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert signed[0x58:0x6C] != bytes(20) and compute_hash58(signed, GUID) == signed
    return mp


@pytest.mark.parametrize(
    "sysinfo",
    [
        b"FirewireGuid: 0x000A270012345678\n",
        b"ModelNumStr: xZZZZ\nFirewireGuid: 0x000A270012345678\n",
    ],
)
@pytest.mark.parametrize("change", [False, True])
def test_save_keeps_signature_when_model_unidentified(tmp_path, sysinfo, change, generated_mp3):
    """A signed Classic database opened without a usable ModelNumStr is re-signed
    with the scheme it carries (hash58 with the SysInfo GUID), not zeroed or left stale."""
    from pygpod.hash.hash58 import compute_hash58

    mp = _signed_classic(tmp_path)
    (mp / "iPod_Control" / "Device" / "SysInfo").write_bytes(sysinfo)
    db = Database(str(mp))
    assert db.device.checksum_type is ChecksumType.NONE  # model not identifiable
    assert not db.device.checksum_known
    if change:
        db.add_track(generated_mp3, copy=True)
    db.save()
    data = (mp / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert data[0x58:0x6C] != bytes(20) and compute_hash58(data, GUID) == data
    assert len(Database(str(mp)).tracks) == (1 if change else 0)


def test_save_unidentified_signed_without_guid_raises_and_keeps_db(tmp_path, generated_mp3):
    """SysInfo missing and no GUID from USB -> HashError, the signed DB stays."""
    from unittest import mock

    mp = _signed_classic(tmp_path)
    os.remove(str(mp / "iPod_Control" / "Device" / "SysInfo"))
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    before = db_path.read_bytes()
    db = Database(str(mp))
    db.add_track(generated_mp3, copy=True)
    with mock.patch("pygpod.device.usb.detect_ipod_usb", return_value=[]):
        with pytest.raises(HashError, match="FirewireGuid"):
            db.save()
    assert db_path.read_bytes() == before


def test_save_unidentified_hash72_db_resigns_with_hashinfo(tmp_path, generated_mp3):
    """An unidentified model whose DB carries a hash72 is re-signed with HashInfo."""
    from pygpod import init_ipod
    from pygpod.hash.hash72 import compute_hash72, write_hash_info

    mp = tmp_path / "nano"
    mp.mkdir()
    init_ipod(
        str(mp),
        name="Nano",
        sysinfo_files={"SysInfo": f"ModelNumStr: xC027\nFirewireGuid: 0x{GUID}\n".encode()},
    )
    iv, rnd = os.urandom(16), os.urandom(12)
    write_hash_info(str(mp / "iPod_Control" / "Device" / "HashInfo"), GUID, iv, rnd)
    Database(str(mp)).save()
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    assert db_path.read_bytes()[0x72] == 0x01
    (mp / "iPod_Control" / "Device" / "SysInfo").write_text(f"FirewireGuid: 0x{GUID}\n")
    db = Database(str(mp))
    assert not db.device.checksum_known
    db.add_track(generated_mp3, copy=True)
    db.save()
    data = db_path.read_bytes()
    assert compute_hash72(data, iv=iv, random_bytes=rnd) == data


def test_save_unidentified_unsigned_db_stays_unsigned(tmp_path, generated_mp3):
    """An unidentified model with an unsigned DB (e.g. a 5G) is written unsigned, no error."""
    from pygpod import init_ipod

    mp = tmp_path / "ipod"
    mp.mkdir()
    init_ipod(
        str(mp),
        name="iPod",
        sysinfo_files={"SysInfo": f"ModelNumStr: xA003\nFirewireGuid: 0x{GUID}\n".encode()},
    )
    os.remove(str(mp / "iPod_Control" / "Device" / "SysInfo"))
    db = Database(str(mp))
    db.add_track(generated_mp3, copy=True)
    db.save()
    data = (mp / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert data[0x58:0x6C] == bytes(20) and data[0x72] == 0


@pytest.mark.parametrize("model,scheme", [("xA003", 0), ("xA477", 0), ("xB029", 1), (None, 1)])
def test_hashing_scheme_field_on_save(tmp_path, generated_mp3, model, scheme):
    """Mhbd 0x30 = 0 for identified unsigned models (also when an older pygpod wrote
    1 there), 1 for signed and unidentified models; 0x10 is not touched by it."""
    from pygpod import init_ipod

    mp = tmp_path / "ipod"
    mp.mkdir()
    sysinfo = f"ModelNumStr: {model or 'xA003'}\nFirewireGuid: 0x{GUID}\n"
    init_ipod(str(mp), name="iPod", sysinfo_files={"SysInfo": sysinfo.encode()})
    if model is None:
        os.remove(str(mp / "iPod_Control" / "Device" / "SysInfo"))
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    old = bytearray(db_path.read_bytes())
    old[0x30:0x34] = (1).to_bytes(4, "little")  # what pygpod <= 0.1.0 wrote
    db_path.write_bytes(bytes(old))
    db = Database(str(mp))
    db.add_track(generated_mp3, title="Обійми", copy=True)
    db.save()
    data = db_path.read_bytes()
    assert int.from_bytes(data[0x30:0x32], "little") == scheme
    assert data[0x32:0x34] == b"\x00\x00"
