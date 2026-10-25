"""USB fallback with more than one iPod on the bus.

The mountpoints of the database tests are tmp dirs, so they can never be mapped to a USB
device: with two iPods connected the fallback must not guess (no GUID, no model, no SysInfo
written). The mapping itself is tested against a fake sysfs tree and recorded macOS/Windows
outputs. Emulated only: detect_ipod_usb, subprocess and SCSI reads are monkeypatched, no
real USB access.
"""

import json
import os
import plistlib
import sys
from unittest import mock

import pytest

from pygpod.device import usb as usb_mod
from pygpod.device.usb import USBDeviceInfo, detect_ipod_usb_for_mountpoint
from pygpod.exceptions import HashError
from pygpod.hash.hash58 import compute_hash58
from pygpod.model.database import Database

GUID = "000A270012345678"
OTHER_GUID = "000A2700AAAAAAAA"


def _usb(guid, serial):
    # 0x1261 = iPod Classic, identified via product id + capacity
    return USBDeviceInfo(
        vendor_id=0x05AC,
        product_id=0x1261,
        serial=serial,
        manufacturer="Apple Inc.",
        product="iPod",
        firewire_guid=guid,
        capacity_gb=80.0,
    )


def _signed_classic_without_sysinfo(tmp_path):
    from pygpod import init_ipod

    mp = tmp_path / "ipod"
    mp.mkdir()
    init_ipod(
        str(mp),
        name="iPod",
        sysinfo_files={"SysInfo": f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n".encode()},
    )
    signed = (mp / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
    assert compute_hash58(signed, GUID) == signed
    os.remove(str(mp / "iPod_Control" / "Device" / "SysInfo"))
    return mp


def test_two_ipods_unmapped_mountpoint_no_guess_on_save(tmp_path, generated_mp3):
    """Two iPods, backing device unknown -> HashError, SysInfo not written, DB untouched."""
    mp = _signed_classic_without_sysinfo(tmp_path)
    sysinfo = mp / "iPod_Control" / "Device" / "SysInfo"
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    before = db_path.read_bytes()
    bus = [_usb(OTHER_GUID, "OTHERSERIAL"), _usb(GUID, "THISSERIAL")]
    with mock.patch("pygpod.device.usb.detect_ipod_usb", return_value=bus):
        db = Database(str(mp))
        db.add_track(generated_mp3, copy=True)
        with pytest.raises(HashError, match="FirewireGuid"):
            db.save()
    assert not sysinfo.exists()
    assert db_path.read_bytes() == before


def test_two_ipods_unmapped_mountpoint_device_not_enriched(tmp_path):
    """Device detection must not take the GUID/serial of whichever iPod is first on the bus."""
    from pygpod.device.device import Device

    mp = _signed_classic_without_sysinfo(tmp_path)
    bus = [_usb(OTHER_GUID, "OTHERSERIAL"), _usb(GUID, "THISSERIAL")]
    with mock.patch("pygpod.device.usb.detect_ipod_usb", return_value=bus):
        dev = Device.from_mountpoint(str(mp))
    assert dev.firewire_guid in (None, "")


def test_one_ipod_unmapped_mountpoint_fallback_unchanged(tmp_path, generated_mp3):
    """Single iPod on the bus: the bus scan is still used (unchanged behaviour)."""
    mp = _signed_classic_without_sysinfo(tmp_path)
    db_path = mp / "iPod_Control" / "iTunes" / "iTunesDB"
    with mock.patch("pygpod.device.usb.detect_ipod_usb", return_value=[_usb(GUID, "THISSERIAL")]):
        db = Database(str(mp))
        db.add_track(generated_mp3, copy=True)
        db.save()
    data = db_path.read_bytes()
    assert data[0x58:0x6C] != bytes(20) and compute_hash58(data, GUID) == data
    assert len(Database(str(mp)).tracks) == 1


# ---- mountpoint -> USB device mapping ----

CLASSIC_SERIAL = "000A27001234567800000000"  # USB serial: FirewireGuid + padding
MINI_SERIAL = "000A2700BBBBBBBB00000000"


def _fake_sysfs(root):
    """Two iPods on the bus (Classic sdx, mini sdy) and an NVMe root disk."""
    usb_root = root / "devices" / "platform" / "xhci" / "usb1"
    disks = {}
    for port, pid, serial, disk, sectors, devnum in (
        ("1-1", "1261", CLASSIC_SERIAL, "sdx", 156250000, "8:2"),
        ("1-2", "1205", MINI_SERIAL, "sdy", 7812500, "8:18"),
    ):
        usb_dev = usb_root / port
        usb_dev.mkdir(parents=True)
        (usb_dev / "idVendor").write_text("05ac\n")
        (usb_dev / "idProduct").write_text(pid + "\n")
        (usb_dev / "serial").write_text(serial + "\n")
        (usb_dev / "manufacturer").write_text("Apple Inc.\n")
        (usb_dev / "product").write_text("iPod\n")
        disk_dir = usb_dev / f"{port}:1.0" / "host0" / "target0" / "0:0:0:0" / "block" / disk
        part = disk_dir / f"{disk}2"
        part.mkdir(parents=True)
        (part / "partition").write_text("2\n")
        (disk_dir / "size").write_text(f"{sectors}\n")
        disks[devnum] = part
    # /sys/block/<disk> (with its "device" link) and /sys/bus/usb/devices/<port>
    (root / "block").mkdir(parents=True)
    (root / "bus" / "usb" / "devices").mkdir(parents=True)
    for devnum, part in disks.items():
        disk_dir = part.parent
        os.symlink(str(disk_dir.parent.parent), str(disk_dir / "device"))
        os.symlink(str(disk_dir), str(root / "block" / disk_dir.name))
        usb_dev = disk_dir.parents[5]
        os.symlink(str(usb_dev), str(root / "bus" / "usb" / "devices" / usb_dev.name))
    nvme = root / "devices" / "pci0" / "nvme0" / "block" / "nvme0n1" / "nvme0n1p2"
    nvme.mkdir(parents=True)
    (nvme / "partition").write_text("2\n")
    disks["259:2"] = nvme
    (root / "dev" / "block").mkdir(parents=True)
    for devnum, target in disks.items():
        os.symlink(str(target), str(root / "dev" / "block" / devnum))
    return root


# sysfs names such as 1-1:1.0 and 0:0:0:0 cannot be created on Windows
linux_sysfs = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake Linux sysfs tree needs ':' in file names"
)


@pytest.fixture
def fake_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(usb_mod, "_SYSFS_ROOT", str(_fake_sysfs(tmp_path / "sys")))
    monkeypatch.setattr(usb_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(usb_mod, "_read_scsi_serial", lambda dev: None)
    bus = [_usb(OTHER_GUID, "OTHERSERIAL"), _usb(GUID, "THISSERIAL")]
    monkeypatch.setattr(usb_mod, "detect_ipod_usb", lambda: bus)
    return monkeypatch


@linux_sysfs
@linux_sysfs
@pytest.mark.parametrize(
    "devnum, guid, pid, capacity",
    [((8, 2), GUID, 0x1261, 80.0), ((8, 18), "000A2700BBBBBBBB", 0x1205, 4.0)],
)
def test_linux_mountpoint_maps_to_its_own_ipod(fake_linux, devnum, guid, pid, capacity):
    fake_linux.setattr(usb_mod, "_block_devnum", lambda mp: devnum)
    dev = detect_ipod_usb_for_mountpoint("/media/ipod")
    assert dev is not None
    assert (dev.firewire_guid, dev.product_id, dev.capacity_gb) == (guid, pid, capacity)


@linux_sysfs
def test_linux_scsi_serial_read_from_the_mapped_disk(fake_linux):
    seen = []
    fake_linux.setattr(usb_mod, "_read_scsi_serial", lambda dev: seen.append(dev) or "8K74729BY5N")
    fake_linux.setattr(usb_mod, "_block_devnum", lambda mp: (8, 18))
    dev = detect_ipod_usb_for_mountpoint("/media/mini")
    assert seen == ["/dev/sdy"] and dev.serial == "8K74729BY5N"


@linux_sysfs
def test_linux_non_usb_mountpoint_two_ipods_no_guess(fake_linux):
    fake_linux.setattr(usb_mod, "_block_devnum", lambda mp: (259, 2))
    assert detect_ipod_usb_for_mountpoint("/home/me/ipod-copy") is None


@linux_sysfs
def test_linux_unknown_devnum_one_ipod_uses_it(fake_linux):
    fake_linux.setattr(usb_mod, "_block_devnum", lambda mp: (7, 0))
    fake_linux.setattr(usb_mod, "detect_ipod_usb", lambda: [_usb(GUID, "THISSERIAL")])
    assert detect_ipod_usb_for_mountpoint("/mnt/x").firewire_guid == GUID


@linux_sysfs
def test_device_from_mapped_mountpoint_gets_its_own_guid(fake_linux, tmp_path):
    """Device.from_mountpoint on a mapped mountpoint takes this iPod's GUID, not the first."""
    from pygpod.device.device import Device

    mp = _signed_classic_without_sysinfo(tmp_path)
    fake_linux.setattr(usb_mod, "_block_devnum", lambda path: (8, 2))
    dev = Device.from_mountpoint(str(mp))
    assert dev.firewire_guid == GUID


def test_read_serial_linux_no_first_ipod_fallback_with_two(monkeypatch):
    devices = [("/dev/sdx", CLASSIC_SERIAL), ("/dev/sdy", MINI_SERIAL)]
    monkeypatch.setattr(usb_mod, "_find_all_ipod_block_devices", lambda: devices)
    monkeypatch.setattr(usb_mod, "_read_scsi_serial", lambda dev: "SERIAL-" + dev)
    assert usb_mod._read_serial_linux("000A2700CCCCCCCC") is None
    assert usb_mod._read_serial_linux(MINI_SERIAL) == "SERIAL-/dev/sdy"
    monkeypatch.setattr(usb_mod, "_find_all_ipod_block_devices", lambda: devices[:1])
    assert usb_mod._read_serial_linux("000A2700CCCCCCCC") == "SERIAL-/dev/sdx"


# Recorded-style outputs (macOS 13 system_profiler -json, diskutil info -plist)
_SP_USB = {
    "SPUSBDataType": [
        {
            "_name": "USB31Bus",
            "_items": [
                {
                    "_name": "iPod",
                    "vendor_id": "0x05ac  (Apple Inc.)",
                    "product_id": "0x1205",
                    "serial_num": MINI_SERIAL,
                    "Media": [{"_name": "Apple iPod", "bsd_name": "disk4"}],
                },
                {
                    "_name": "iPod",
                    "vendor_id": "0x05ac  (Apple Inc.)",
                    "product_id": "0x1261",
                    "serial_num": CLASSIC_SERIAL,
                    "Media": [{"_name": "Apple iPod", "bsd_name": "disk5"}],
                },
                {
                    "_name": "USB Stick",
                    "vendor_id": "0x0781  (SanDisk)",
                    "product_id": "0x5581",
                    "serial_num": "4C530001",
                    "Media": [{"_name": "SanDisk", "bsd_name": "disk6"}],
                },
            ],
        }
    ]
}


def _fake_macos(monkeypatch, whole_disk):
    def check_output(cmd, **kwargs):
        if cmd[:3] == ["diskutil", "info", "-plist"]:
            return plistlib.dumps(
                {"DeviceIdentifier": whole_disk + "s2", "ParentWholeDisk": whole_disk}
            )
        if cmd[:2] == ["system_profiler", "SPUSBDataType"]:
            return json.dumps(_SP_USB).encode()
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(usb_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(usb_mod.subprocess, "check_output", check_output)
    monkeypatch.setattr(
        usb_mod, "detect_ipod_usb", lambda: [_usb(OTHER_GUID, "X"), _usb(GUID, "Y")]
    )


@pytest.mark.parametrize(
    "disk, guid, pid", [("disk5", GUID, 0x1261), ("disk4", "000A2700BBBBBBBB", 0x1205)]
)
def test_macos_mountpoint_maps_to_its_own_ipod(monkeypatch, disk, guid, pid):
    _fake_macos(monkeypatch, disk)
    dev = detect_ipod_usb_for_mountpoint("/Volumes/IPOD")
    assert (dev.firewire_guid, dev.product_id) == (guid, pid)


def test_macos_non_apple_disk_two_ipods_no_guess(monkeypatch):
    _fake_macos(monkeypatch, "disk6")
    assert detect_ipod_usb_for_mountpoint("/Volumes/STICK") is None


def test_macos_diskutil_fails_two_ipods_no_guess(monkeypatch):
    _fake_macos(monkeypatch, "disk5")
    monkeypatch.setattr(
        usb_mod.subprocess, "check_output", mock.Mock(side_effect=FileNotFoundError("diskutil"))
    )
    assert detect_ipod_usb_for_mountpoint("/Volumes/IPOD") is None


def _fake_windows(monkeypatch, disks):
    def run_powershell(command):
        letter = command.split("-DriveLetter ")[1][0]
        return [json.dumps(disks[letter])] if letter in disks else []

    monkeypatch.setattr(usb_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(usb_mod, "_run_powershell", run_powershell)
    bus = [
        _usb(OTHER_GUID, "X"),
        _usb(GUID, "Y")._replace(product_id=0x1261),
        _usb("000A2700BBBBBBBB", "Z")._replace(product_id=0x1205),
    ]
    monkeypatch.setattr(usb_mod, "detect_ipod_usb", lambda: bus)


_WIN_DISKS = {
    "E": {
        "Path": "\\\\?\\usbstor#disk&ven_apple&prod_ipod&rev_1.62#"
        + CLASSIC_SERIAL.lower()
        + "&0#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}",
        "SerialNumber": "8K74729BY5N",
        "Size": 80000000000,
    },
    "F": {
        "Path": "\\\\?\\usbstor#disk&ven_apple&prod_ipod&rev_1.62#"
        + MINI_SERIAL.lower()
        + "&0#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}",
        "SerialNumber": MINI_SERIAL,
        "Size": 4000000000,
    },
    "G": {
        "Path": "\\\\?\\usbstor#disk&ven_sandisk&prod_cruzer&rev_1.00#4c530001&0#{53f5}",
        "SerialNumber": "4C530001",
        "Size": 16000000000,
    },
}


@pytest.mark.parametrize(
    "drive, guid, pid, serial",
    [
        ("E:\\", GUID, 0x1261, "8K74729BY5N"),
        ("F:", "000A2700BBBBBBBB", 0x1205, MINI_SERIAL),
    ],
)
def test_windows_drive_maps_to_its_own_ipod(monkeypatch, drive, guid, pid, serial):
    _fake_windows(monkeypatch, _WIN_DISKS)
    dev = detect_ipod_usb_for_mountpoint(drive)
    assert (dev.firewire_guid, dev.product_id, dev.serial) == (guid, pid, serial)


@pytest.mark.parametrize("drive", ["G:\\", "H:\\"])
def test_windows_unmapped_drive_two_ipods_no_guess(monkeypatch, drive):
    _fake_windows(monkeypatch, _WIN_DISKS)
    assert detect_ipod_usb_for_mountpoint(drive) is None


@linux_sysfs
def test_linux_bus_scan_gives_each_ipod_its_own_capacity(fake_linux):
    """Without pyusb, the sysfs scan pairs every iPod with its own disk (capacity)."""
    fake_linux.setattr(usb_mod, "_detect_pyusb", lambda: None)
    fake_linux.setattr(usb_mod, "read_ipod_serial", lambda serial=None: None)
    devices = {d.firewire_guid: d for d in usb_mod._detect_linux_sysfs()}
    assert devices[GUID].capacity_gb == 80.0
    assert devices["000A2700BBBBBBBB"].capacity_gb == 4.0
    assert usb_mod._get_ipod_capacity_gb(MINI_SERIAL) == 4.0
    assert usb_mod._get_ipod_capacity_gb("000A2700CCCCCCCC00000000") is None


def test_windows_bus_scan_gives_each_ipod_its_own_serial(monkeypatch):
    instance_ids = [
        "USB\\VID_05AC&PID_1261\\" + CLASSIC_SERIAL,
        "USB\\VID_05AC&PID_1205\\" + MINI_SERIAL,
    ]
    disks = [_WIN_DISKS["E"], dict(_WIN_DISKS["F"], SerialNumber="9C7311XYZ")]

    def run_powershell(command):
        if "Get-PnpDevice" in command:
            return instance_ids
        if "Get-Disk" in command:
            return [json.dumps(disks)]
        return []

    monkeypatch.setattr(usb_mod, "_run_powershell", run_powershell)
    found = {d.firewire_guid: d.serial for d in usb_mod._detect_windows_wmi()}
    assert found == {GUID: "8K74729BY5N", "000A2700BBBBBBBB": "9C7311XYZ"}


def test_powershell_output_decoded_as_utf8(monkeypatch):
    seen = {}

    def check_output(cmd, **kwargs):
        seen["cmd"] = cmd
        raw = "USB\\VID_05AC&PID_1261\\Ґанок\r\n".encode("utf-8")
        return raw.decode(kwargs.get("encoding") or "cp1252", kwargs.get("errors") or "strict")

    monkeypatch.setattr(usb_mod.subprocess, "check_output", check_output)
    assert usb_mod._run_powershell("Get-PnpDevice") == ["USB\\VID_05AC&PID_1261\\Ґанок"]
    assert seen["cmd"][-1].startswith("[Console]::OutputEncoding = [Text.Encoding]::UTF8; ")
