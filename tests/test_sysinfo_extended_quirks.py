"""SysInfoExtended as real iPods write it: <key> elements inside <array> (libgpod skips them)."""

from pygpod.device.device import Device
from pygpod.model.sysinfo import drop_keys_in_arrays, parse_sysinfo_extended

PLIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
<key>SerialNumber</key>
<string>8K1483YF9ZU</string>
<key>FireWireGUID</key>
<string>000A27001301297E</string>
<key>DBVersion</key>
<integer>3</integer>
<key>ImageSpecifications</key>
<array>
<key>1067</key>
<dict>
<key>FormatId</key>
<integer>1067</integer>
<key>Empty</key><array/>
</dict>
<key>1068</key>
<dict>
<key>FormatId</key>
<integer>1068</integer>
</dict>
</array>
<key>After</key>
<string>kept</string>
</dict>
</plist>
"""


def test_keys_inside_arrays_are_dropped():
    fixed = drop_keys_in_arrays(PLIST)
    assert b"<key>1067</key>" not in fixed and b"<key>FormatId</key>" in fixed
    assert drop_keys_in_arrays(fixed) == fixed  # valid plists pass unchanged


def test_quirky_plist_parses_and_identifies_the_model(tmp_path):
    dev_dir = tmp_path / "iPod_Control" / "Device"
    dev_dir.mkdir(parents=True)
    (dev_dir / "SysInfo").write_bytes(b"")  # as left by a stock restore
    (dev_dir / "SysInfoExtended").write_bytes(PLIST)
    data = parse_sysinfo_extended(str(dev_dir / "SysInfoExtended"))
    assert [s["FormatId"] for s in data["ImageSpecifications"]] == [1067, 1068]
    assert data["After"] == "kept"
    dev = Device.from_mountpoint(str(tmp_path))
    assert dev.requires_hash and dev.checksum_type.name == "HASH58"
    assert dev.firewire_guid == "000A27001301297E"
