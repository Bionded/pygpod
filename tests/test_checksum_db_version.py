"""SysInfoExtended DBVersion values outside the known 0-5 range.

An iPod Classic 6G (MB029) has been seen reporting ``DBVersion`` 48 in its SysInfoExtended.
Only 0-5 name a checksum scheme; any other value must not pick hashAB but leave the choice to
the model's generation (hash58 for a Classic).
"""

import plistlib

import pytest

from pygpod import Database, init_ipod
from pygpod.device.device import Device
from pygpod.device.models import ChecksumType, IpodGeneration, get_checksum_type
from pygpod.hash.hash58 import compute_hash58

GUID = "000A27001301297E"


@pytest.fixture(autouse=True)
def _no_usb(monkeypatch):
    """Emulated volumes only: model detection must never fall back to the real USB bus."""
    import pygpod.device.usb

    monkeypatch.setattr(pygpod.device.usb, "detect_ipod_usb", lambda *a, **k: [])


@pytest.mark.parametrize(
    "gen, version, expected",
    [
        (IpodGeneration.CLASSIC_1, 0, ChecksumType.NONE),
        (IpodGeneration.CLASSIC_1, 2, ChecksumType.NONE),
        (IpodGeneration.CLASSIC_1, 3, ChecksumType.HASH58),
        (IpodGeneration.NANO_5, 4, ChecksumType.HASH72),
        (IpodGeneration.NANO_6, 5, ChecksumType.HASHAB),
        (IpodGeneration.CLASSIC_1, None, ChecksumType.HASH58),
    ],
)
def test_known_db_versions_name_the_scheme(gen, version, expected):
    assert get_checksum_type(gen, version) is expected


@pytest.mark.parametrize("version", [6, 48, -1])
def test_unknown_db_version_falls_back_to_the_generation(version):
    assert get_checksum_type(IpodGeneration.CLASSIC_1, version) is ChecksumType.HASH58


def _classic_6g(path, db_version=48):
    files = {
        "SysInfo": f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n".encode(),
        "SysInfoExtended": plistlib.dumps(
            {"FireWireGUID": GUID, "DBVersion": db_version, "SerialNumber": "8K1483YF9ZU"}
        ),
    }
    path.mkdir()
    init_ipod(str(path), name="Класика", sysinfo_files=files)
    return str(path)


def test_classic_6g_with_db_version_48_is_signed_with_hash58(tmp_path):
    mp = _classic_6g(tmp_path / "ipod")
    assert Device.from_mountpoint(mp).checksum_type is ChecksumType.HASH58
    db = Database(mp)
    db.create_playlist("Вечірні пісні")
    db.save()
    data = (tmp_path / "ipod" / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert any(data[0x58:0x72])
    assert compute_hash58(data, GUID) == data


def test_unknown_db_version_on_an_unidentified_model_is_not_a_known_checksum(tmp_path):
    files = {
        "SysInfo": f"FirewireGuid: 0x{GUID}\n".encode(),
        "SysInfoExtended": plistlib.dumps({"FireWireGUID": GUID, "DBVersion": 48}),
    }
    mp = tmp_path / "ipod"
    mp.mkdir()
    init_ipod(str(mp), sysinfo_files=files)
    dev = Device.from_mountpoint(str(mp))
    assert dev.generation is IpodGeneration.UNKNOWN
    assert not dev.checksum_known
    assert dev.checksum_type is ChecksumType.NONE


def test_unknown_db_version_lets_the_legacy_key_decide(tmp_path):
    files = {
        "SysInfo": f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n".encode(),
        "SysInfoExtended": plistlib.dumps(
            {"FireWireGUID": GUID, "DBVersion": 48, "DatabaseVersion": 4}
        ),
    }
    mp = tmp_path / "ipod"
    mp.mkdir()
    init_ipod(str(mp), sysinfo_files=files)
    assert Device.from_mountpoint(str(mp)).checksum_type is ChecksumType.HASH72
