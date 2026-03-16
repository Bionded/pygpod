"""Tests for SysInfo and SysInfoExtended parsing."""

import plistlib

import pytest

from pygpod.model.sysinfo import (
    SysInfo,
    _find_case_insensitive,
    parse_sysinfo,
    parse_sysinfo_extended,
    read_sysinfo,
)

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def sysinfo_dir(tmp_path):
    """Create a minimal iPod structure with SysInfo."""
    ipod = tmp_path / "ipod"
    dev_dir = ipod / "iPod_Control" / "Device"
    dev_dir.mkdir(parents=True)
    sysinfo = dev_dir / "SysInfo"
    sysinfo.write_text(
        "ModelNumStr: xB029\n"
        "FirewireGuid: 0x000A2700131A2BFC\n"
        "SerialNumber: 8K74729BY5N\n"
        "visibleBuildID: 1.3.1\n"
    )
    return ipod


@pytest.fixture
def sysinfo_extended_dir(sysinfo_dir):
    """Add SysInfoExtended plist to the fixture."""
    dev_dir = sysinfo_dir / "iPod_Control" / "Device"
    ext_path = dev_dir / "SysInfoExtended"
    plist_data = {
        "FireWireGUID": 0x000A2700131A2BFC,
        "SerialNumber": "8K74729BY5N",
        "VisibleBuildID": "1.3.1",
        "ProductType": "iPod5,1",
        "FamilyID": 10001,
        "DatabaseVersion": 3,
        "ModelString": "B029",
    }
    with open(ext_path, "wb") as f:
        plistlib.dump(plist_data, f)
    return sysinfo_dir


@pytest.fixture
def sysinfo_extended_str_guid(sysinfo_dir):
    """SysInfoExtended with FireWireGUID as string (not int)."""
    dev_dir = sysinfo_dir / "iPod_Control" / "Device"
    ext_path = dev_dir / "SysInfoExtended"
    plist_data = {
        "FireWireGUID": "0x000A2700131A2BFC",
        "SerialNumber": "8K74729BY5N",
    }
    with open(ext_path, "wb") as f:
        plistlib.dump(plist_data, f)
    return sysinfo_dir


# =========================================================================
# parse_sysinfo
# =========================================================================


class TestParseSysInfo:
    def test_parse_basic(self, sysinfo_dir):
        path = str(sysinfo_dir / "iPod_Control" / "Device" / "SysInfo")
        info = parse_sysinfo(path)
        assert info.raw["ModelNumStr"] == "xB029"
        assert info.raw["FirewireGuid"] == "0x000A2700131A2BFC"
        assert info.raw["SerialNumber"] == "8K74729BY5N"

    def test_parse_nonexistent(self, tmp_path):
        info = parse_sysinfo(str(tmp_path / "nonexistent"))
        assert info.raw == {}

    def test_raw_lines_preserved(self, sysinfo_dir):
        path = str(sysinfo_dir / "iPod_Control" / "Device" / "SysInfo")
        info = parse_sysinfo(path)
        assert len(info._raw_lines) >= 3
        keys = [k for k, _ in info._raw_lines]
        assert "ModelNumStr" in keys

    def test_empty_lines_skipped(self, tmp_path):
        p = tmp_path / "SysInfo"
        p.write_text("\n\nModelNumStr: xA002\n\n")
        info = parse_sysinfo(str(p))
        assert info.raw["ModelNumStr"] == "xA002"

    def test_no_colon_lines_skipped(self, tmp_path):
        p = tmp_path / "SysInfo"
        p.write_text("garbage line\nModelNumStr: xA002\n")
        info = parse_sysinfo(str(p))
        assert "ModelNumStr" in info.raw
        assert len(info.raw) == 1


# =========================================================================
# parse_sysinfo_extended
# =========================================================================


class TestParseSysInfoExtended:
    def test_parse_plist(self, sysinfo_extended_dir):
        path = str(sysinfo_extended_dir / "iPod_Control" / "Device" / "SysInfoExtended")
        ext = parse_sysinfo_extended(path)
        assert ext["FireWireGUID"] == 0x000A2700131A2BFC
        assert ext["SerialNumber"] == "8K74729BY5N"
        assert ext["ProductType"] == "iPod5,1"
        assert ext["FamilyID"] == 10001
        assert ext["DatabaseVersion"] == 3

    def test_parse_nonexistent(self, tmp_path):
        ext = parse_sysinfo_extended(str(tmp_path / "nonexistent"))
        assert ext == {}

    def test_parse_corrupt_plist(self, tmp_path):
        p = tmp_path / "SysInfoExtended"
        p.write_bytes(b"this is not a valid plist")
        ext = parse_sysinfo_extended(str(p))
        assert ext == {}


# =========================================================================
# SysInfo properties
# =========================================================================


class TestSysInfoProperties:
    def test_model_num_str(self, sysinfo_dir):
        info = parse_sysinfo(str(sysinfo_dir / "iPod_Control" / "Device" / "SysInfo"))
        assert info.model_num_str == "xB029"

    def test_model_number_strips_x(self, sysinfo_dir):
        info = parse_sysinfo(str(sysinfo_dir / "iPod_Control" / "Device" / "SysInfo"))
        assert info.model_number == "B029"

    def test_model_number_strips_m(self):
        info = SysInfo()
        info.raw["ModelNumStr"] = "MA002"
        assert info.model_number == "A002"

    def test_model_number_no_prefix(self):
        info = SysInfo()
        info.raw["ModelNumStr"] = "B029"
        assert info.model_number == "B029"

    def test_model_number_fallback_to_serial(self):
        info = SysInfo()
        info.raw["SerialNumber"] = "8K74729BY5N"
        # No ModelNumStr, so falls back to last 3 chars of serial
        assert info.model_number == "Y5N"

    def test_model_number_none_when_no_data(self):
        info = SysInfo()
        assert info.model_number is None

    def test_firewire_guid_from_sysinfo(self, sysinfo_dir):
        info = parse_sysinfo(str(sysinfo_dir / "iPod_Control" / "Device" / "SysInfo"))
        assert info.firewire_guid == "000A2700131A2BFC"

    def test_firewire_guid_from_extended_int(self, sysinfo_extended_dir):
        info = read_sysinfo(str(sysinfo_extended_dir))
        guid = info.firewire_guid
        assert guid == "000A2700131A2BFC"

    def test_firewire_guid_from_extended_str(self, sysinfo_extended_str_guid):
        info = read_sysinfo(str(sysinfo_extended_str_guid))
        guid = info.firewire_guid
        assert guid == "000A2700131A2BFC"

    def test_firewire_guid_none(self):
        info = SysInfo()
        assert info.firewire_guid is None

    def test_serial_number_from_sysinfo(self, sysinfo_dir):
        info = parse_sysinfo(str(sysinfo_dir / "iPod_Control" / "Device" / "SysInfo"))
        assert info.serial_number == "8K74729BY5N"

    def test_serial_number_from_extended(self):
        info = SysInfo()
        info.extended = {"SerialNumber": "EXTENDED_SERIAL"}
        assert info.serial_number == "EXTENDED_SERIAL"

    def test_serial_number_extended_priority(self):
        info = SysInfo()
        info.raw["SerialNumber"] = "RAW"
        info.extended = {"SerialNumber": "EXTENDED"}
        # Extended takes priority
        assert info.serial_number == "EXTENDED"

    def test_firmware_version_from_extended(self):
        info = SysInfo()
        info.extended = {"VisibleBuildID": "2.0.5"}
        assert info.firmware_version == "2.0.5"

    def test_firmware_version_from_raw(self):
        info = SysInfo()
        info.raw["visibleBuildID"] = "1.3.1"
        assert info.firmware_version == "1.3.1"

    def test_firmware_version_none(self):
        info = SysInfo()
        assert info.firmware_version is None

    def test_product_type(self):
        info = SysInfo()
        info.extended = {"ProductType": "iPod5,1"}
        assert info.product_type == "iPod5,1"

    def test_product_type_none(self):
        info = SysInfo()
        assert info.product_type is None

    def test_family_id(self):
        info = SysInfo()
        info.extended = {"FamilyID": 10001}
        assert info.family_id == 10001

    def test_family_id_none(self):
        info = SysInfo()
        assert info.family_id is None


# =========================================================================
# read_sysinfo (combined)
# =========================================================================


class TestReadSysInfo:
    def test_read_basic(self, sysinfo_dir):
        info = read_sysinfo(str(sysinfo_dir))
        assert info.model_number == "B029"
        assert info.firewire_guid == "000A2700131A2BFC"

    def test_read_with_extended(self, sysinfo_extended_dir):
        info = read_sysinfo(str(sysinfo_extended_dir))
        assert info.model_number == "B029"
        assert info.firewire_guid == "000A2700131A2BFC"
        assert info.product_type == "iPod5,1"
        assert info.family_id == 10001
        assert info.firmware_version == "1.3.1"
        assert info.serial_number == "8K74729BY5N"

    def test_extended_guid_copied_to_raw(self, sysinfo_extended_dir):
        """FireWireGUID from extended should be available via raw too."""
        # Remove FirewireGuid from SysInfo so only extended has it
        si_path = sysinfo_extended_dir / "iPod_Control" / "Device" / "SysInfo"
        si_path.write_text("ModelNumStr: xB029\n")

        info = read_sysinfo(str(sysinfo_extended_dir))
        assert info.firewire_guid == "000A2700131A2BFC"
        assert "FirewireGuid" in info.raw

    def test_extended_str_guid_copied_to_raw(self, sysinfo_extended_str_guid):
        si_path = sysinfo_extended_str_guid / "iPod_Control" / "Device" / "SysInfo"
        si_path.write_text("ModelNumStr: xB029\n")

        info = read_sysinfo(str(sysinfo_extended_str_guid))
        assert info.firewire_guid == "000A2700131A2BFC"

    def test_read_no_device_dir(self, tmp_path):
        ipod = tmp_path / "ipod"
        (ipod / "iPod_Control").mkdir(parents=True)
        info = read_sysinfo(str(ipod))
        assert info.raw == {}

    def test_read_empty_mountpoint(self, tmp_path):
        info = read_sysinfo(str(tmp_path / "nonexistent"))
        assert info.raw == {}


# =========================================================================
# _find_case_insensitive
# =========================================================================


class TestCaseInsensitive:
    def test_exact_match(self, tmp_path):
        (tmp_path / "Device").mkdir()
        result = _find_case_insensitive(tmp_path, "Device")
        assert result is not None
        assert result.name == "Device"

    def test_lowercase_match(self, tmp_path):
        (tmp_path / "device").mkdir()
        result = _find_case_insensitive(tmp_path, "Device")
        assert result is not None

    def test_uppercase_match(self, tmp_path):
        (tmp_path / "DEVICE").mkdir()
        result = _find_case_insensitive(tmp_path, "Device")
        assert result is not None

    def test_no_match(self, tmp_path):
        (tmp_path / "Other").mkdir()
        result = _find_case_insensitive(tmp_path, "Device")
        assert result is None

    def test_nonexistent_parent(self, tmp_path):
        result = _find_case_insensitive(tmp_path / "nope", "Device")
        assert result is None

    def test_file_not_dir(self, tmp_path):
        (tmp_path / "Device").write_text("not a dir")
        result = _find_case_insensitive(tmp_path, "Device")
        assert result is None
