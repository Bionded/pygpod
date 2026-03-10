"""Tests for device detection and SysInfo parsing."""

from pygpod.device.models import ChecksumType


def test_parse_sysinfo(sysinfo_path):
    """Parse the test SysInfo file."""
    from pygpod.model.sysinfo import parse_sysinfo

    info = parse_sysinfo(sysinfo_path)

    assert info.model_num_str == "xB029"
    assert info.model_number == "B029"
    assert info.firewire_guid == "000A2700213749FF"


def test_lookup_model():
    """Test model number lookup."""
    from pygpod.device.models import IpodGeneration, lookup_model

    info = lookup_model("B029")
    assert info.generation == IpodGeneration.CLASSIC_1
    assert info.capacity_gb == 80
    assert info.musicdirs == 50

    # Unknown model
    unknown = lookup_model("ZZZZ")
    assert unknown.generation == IpodGeneration.UNKNOWN


def test_device_from_mountpoint(ipod_fs_path):
    """Test Device creation from mount point."""
    from pygpod.device.device import Device

    dev = Device.from_mountpoint(ipod_fs_path)
    assert "Classic" in dev.model
    assert dev.requires_hash
    assert dev.music_dirs == 50


def test_validate_mountpoint(ipod_fs_path):
    """Test mount point validation."""
    from pygpod.device.mountpoint import validate_mountpoint

    assert validate_mountpoint(ipod_fs_path)
    assert not validate_mountpoint("/tmp")


def test_hash_generations():
    """Verify which generations require which hash type."""
    from pygpod.device.models import (
        HASH58_GENERATIONS,
        HASH72_GENERATIONS,
        ChecksumType,
        IpodGeneration,
        get_checksum_type,
    )

    # Classics use hash58 (per libgpod itdb_device.c)
    assert IpodGeneration.CLASSIC_1 in HASH58_GENERATIONS
    assert IpodGeneration.CLASSIC_2 in HASH58_GENERATIONS
    assert IpodGeneration.CLASSIC_3 in HASH58_GENERATIONS
    assert IpodGeneration.NANO_3 in HASH58_GENERATIONS
    assert IpodGeneration.NANO_4 in HASH58_GENERATIONS

    # Nano 5 uses hash72
    assert IpodGeneration.NANO_5 in HASH72_GENERATIONS

    # Old iPods and shuffles don't need hashing
    assert IpodGeneration.FIRST not in HASH58_GENERATIONS
    assert IpodGeneration.SHUFFLE_1 not in HASH58_GENERATIONS

    # Checksum type detection
    assert get_checksum_type(IpodGeneration.CLASSIC_3) == ChecksumType.HASH58
    assert get_checksum_type(IpodGeneration.NANO_5) == ChecksumType.HASH72
    assert get_checksum_type(IpodGeneration.FIRST) == ChecksumType.NONE

    # SysInfoExtended db_version overrides generation-based mapping
    assert get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=4) == ChecksumType.HASH72
    assert get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=3) == ChecksumType.HASH58


def test_serial_to_model():
    """Test serial number to model lookup."""
    from pygpod.device.models import IpodGeneration, lookup_model_by_serial

    # Classic 1G
    info = lookup_model_by_serial("ABCDEFGHIJ_Y5N")
    assert info is not None
    assert info.generation == IpodGeneration.CLASSIC_1

    # Classic 3G
    info = lookup_model_by_serial("SERIAL_9ZS")
    assert info is not None
    assert info.generation == IpodGeneration.CLASSIC_3

    # Unknown serial
    assert lookup_model_by_serial("XXY") is None
    assert lookup_model_by_serial("") is None
    assert lookup_model_by_serial(None) is None


def test_device_capabilities(ipod_fs_path):
    """Test device capability detection."""
    from pygpod.device.device import Device

    dev = Device.from_mountpoint(ipod_fs_path)
    assert dev.supports_artwork
    assert dev.supports_video
    assert dev.supports_podcast
    assert not dev.is_shuffle
    assert dev.db_version == 0x30  # 48
    assert dev.checksum_type == ChecksumType.HASH58


def test_db_version():
    """Test DB version detection for different generations."""
    from pygpod.device.models import IpodGeneration, get_db_version

    assert get_db_version(IpodGeneration.FIRST) == 0x09
    assert get_db_version(IpodGeneration.PHOTO) == 0x0D
    assert get_db_version(IpodGeneration.VIDEO_1) == 0x19
    assert get_db_version(IpodGeneration.CLASSIC_1) == 0x30
    assert get_db_version(IpodGeneration.CLASSIC_3) == 0x30
    assert get_db_version(IpodGeneration.NANO_5) == 0x30
