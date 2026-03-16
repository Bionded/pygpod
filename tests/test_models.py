"""Tests for iPod model database — lookup functions, checksums, db versions."""

import pytest

from pygpod.device.models import (
    ARTWORK_GENERATIONS,
    HASH58_GENERATIONS,
    HASH72_GENERATIONS,
    HASHAB_GENERATIONS,
    IPOD_INFO_TABLE,
    SHUFFLE_GENERATIONS,
    VIDEO_GENERATIONS,
    ChecksumType,
    IpodGeneration,
    get_checksum_type,
    get_db_version,
    get_generation_name,
    lookup_by_usb_product_id,
    lookup_model,
    lookup_model_by_serial,
)

# =========================================================================
# lookup_model
# =========================================================================


class TestLookupModel:
    @pytest.mark.parametrize(
        "model_num,expected_gen",
        [
            ("B029", IpodGeneration.CLASSIC_1),
            ("B147", IpodGeneration.CLASSIC_1),
            ("B562", IpodGeneration.CLASSIC_2),
            ("C293", IpodGeneration.CLASSIC_3),
            ("A350", IpodGeneration.NANO_1),
            ("A477", IpodGeneration.NANO_2),
            ("A978", IpodGeneration.NANO_3),
            ("B480", IpodGeneration.NANO_4),
            ("C027", IpodGeneration.NANO_5),
            ("C525", IpodGeneration.NANO_6),
            ("A002", IpodGeneration.VIDEO_1),
            ("A444", IpodGeneration.VIDEO_2),
            ("8513", IpodGeneration.FIRST),
            ("8737", IpodGeneration.SECOND),
            ("8976", IpodGeneration.THIRD),
            ("9282", IpodGeneration.FOURTH),
            ("A079", IpodGeneration.PHOTO),
            ("9160", IpodGeneration.MINI_1),
            ("9800", IpodGeneration.MINI_2),
            ("9724", IpodGeneration.SHUFFLE_1),
            ("A546", IpodGeneration.SHUFFLE_2),
            ("C306", IpodGeneration.SHUFFLE_3),
            ("C584", IpodGeneration.SHUFFLE_4),
        ],
    )
    def test_known_models(self, model_num, expected_gen):
        info = lookup_model(model_num)
        assert info.generation == expected_gen

    def test_unknown_model(self):
        info = lookup_model("ZZZZ")
        assert info.generation == IpodGeneration.UNKNOWN

    def test_case_insensitive(self):
        info = lookup_model("b029")
        assert info.generation == IpodGeneration.CLASSIC_1

    def test_strips_x_prefix(self):
        info = lookup_model("xB029")
        assert info.generation == IpodGeneration.CLASSIC_1

    def test_strips_m_prefix(self):
        info = lookup_model("MA002")
        assert info.generation != IpodGeneration.UNKNOWN


# =========================================================================
# lookup_model_by_serial
# =========================================================================


class TestLookupBySerial:
    def test_known_serial_suffix(self):
        # "LG6" maps to model 8541 (iPod 1st Gen)
        info = lookup_model_by_serial("XXXXXXXXXLG6")
        assert info is not None
        assert info.generation == IpodGeneration.FIRST

    def test_unknown_serial(self):
        info = lookup_model_by_serial("XXXXXXXXXXZZZ")
        assert info is None

    def test_short_serial(self):
        assert lookup_model_by_serial("AB") is None
        assert lookup_model_by_serial("") is None
        assert lookup_model_by_serial(None) is None


# =========================================================================
# get_checksum_type
# =========================================================================


class TestChecksumType:
    @pytest.mark.parametrize(
        "gen,expected",
        [
            (IpodGeneration.CLASSIC_1, ChecksumType.HASH58),
            (IpodGeneration.CLASSIC_2, ChecksumType.HASH58),
            (IpodGeneration.CLASSIC_3, ChecksumType.HASH58),
            (IpodGeneration.VIDEO_1, ChecksumType.HASH58),
            (IpodGeneration.NANO_1, ChecksumType.HASH58),
            (IpodGeneration.NANO_4, ChecksumType.HASH58),
            (IpodGeneration.NANO_5, ChecksumType.HASH72),
            (IpodGeneration.NANO_6, ChecksumType.HASHAB),
            (IpodGeneration.FIRST, ChecksumType.NONE),
            (IpodGeneration.SECOND, ChecksumType.NONE),
            (IpodGeneration.PHOTO, ChecksumType.NONE),
            (IpodGeneration.SHUFFLE_1, ChecksumType.NONE),
            (IpodGeneration.UNKNOWN, ChecksumType.NONE),
        ],
    )
    def test_generation_based(self, gen, expected):
        assert get_checksum_type(gen) == expected

    def test_sysinfo_db_version_none(self):
        ct = get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=None)
        assert ct == ChecksumType.HASH58

    def test_sysinfo_db_version_0(self):
        ct = get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=0)
        assert ct == ChecksumType.NONE

    def test_sysinfo_db_version_2(self):
        ct = get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=2)
        assert ct == ChecksumType.NONE

    def test_sysinfo_db_version_3(self):
        ct = get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=3)
        assert ct == ChecksumType.HASH58

    def test_sysinfo_db_version_4(self):
        ct = get_checksum_type(IpodGeneration.CLASSIC_1, sysinfo_db_version=4)
        assert ct == ChecksumType.HASH72

    def test_sysinfo_db_version_5(self):
        assert get_checksum_type(IpodGeneration.FIRST, sysinfo_db_version=5) == ChecksumType.HASH72


# =========================================================================
# get_db_version
# =========================================================================


class TestDBVersion:
    @pytest.mark.parametrize(
        "gen,expected",
        [
            (IpodGeneration.FIRST, 0x09),
            (IpodGeneration.SECOND, 0x09),
            (IpodGeneration.THIRD, 0x09),
            (IpodGeneration.FOURTH, 0x09),
            (IpodGeneration.UNKNOWN, 0x09),
            (IpodGeneration.PHOTO, 0x0D),
            (IpodGeneration.MINI_1, 0x0C),
            (IpodGeneration.MINI_2, 0x0C),
            (IpodGeneration.SHUFFLE_1, 0x0C),
            (IpodGeneration.VIDEO_1, 0x19),
            (IpodGeneration.VIDEO_2, 0x19),
            (IpodGeneration.NANO_1, 0x19),
            (IpodGeneration.NANO_2, 0x19),
            (IpodGeneration.SHUFFLE_2, 0x19),
            (IpodGeneration.CLASSIC_1, 0x30),
            (IpodGeneration.CLASSIC_2, 0x30),
            (IpodGeneration.CLASSIC_3, 0x30),
            (IpodGeneration.NANO_3, 0x30),
            (IpodGeneration.NANO_4, 0x30),
            (IpodGeneration.NANO_5, 0x30),
            (IpodGeneration.NANO_6, 0x30),
            (IpodGeneration.SHUFFLE_3, 0x30),
            (IpodGeneration.SHUFFLE_4, 0x30),
        ],
    )
    def test_all_generations(self, gen, expected):
        assert get_db_version(gen) == expected


# =========================================================================
# get_generation_name
# =========================================================================


class TestGenerationName:
    def test_known(self):
        assert "Classic" in get_generation_name(IpodGeneration.CLASSIC_1)
        assert "Nano" in get_generation_name(IpodGeneration.NANO_3)
        assert "Shuffle" in get_generation_name(IpodGeneration.SHUFFLE_2)
        assert "Video" in get_generation_name(IpodGeneration.VIDEO_1)

    def test_unknown(self):
        assert get_generation_name(IpodGeneration.UNKNOWN) == "Unknown"

    def test_all_named(self):
        for gen in IpodGeneration:
            name = get_generation_name(gen)
            assert isinstance(name, str)
            assert len(name) > 0


# =========================================================================
# lookup_by_usb_product_id
# =========================================================================


class TestUSBProductLookup:
    def test_nano_1g(self):
        info = lookup_by_usb_product_id(0x120A)
        assert info is not None
        assert info.generation == IpodGeneration.NANO_1

    def test_nano_2g(self):
        info = lookup_by_usb_product_id(0x1260)
        assert info is not None
        assert info.generation == IpodGeneration.NANO_2

    def test_nano_3g(self):
        info = lookup_by_usb_product_id(0x1262)
        assert info is not None
        assert info.generation == IpodGeneration.NANO_3

    def test_nano_4g(self):
        info = lookup_by_usb_product_id(0x1263)
        assert info is not None
        assert info.generation == IpodGeneration.NANO_4

    def test_nano_5g(self):
        info = lookup_by_usb_product_id(0x1265)
        assert info is not None
        assert info.generation == IpodGeneration.NANO_5

    def test_classic_ambiguous_without_capacity(self):
        # 0x1261 maps to Classic 1G, 2G, and 3G — ambiguous
        info = lookup_by_usb_product_id(0x1261)
        assert info is None  # ambiguous

    def test_classic_disambiguated_by_capacity(self):
        # 80 GB = Classic 1G
        info = lookup_by_usb_product_id(0x1261, capacity_gb=80)
        assert info is not None
        assert info.generation == IpodGeneration.CLASSIC_1

    def test_classic_120gb(self):
        # 120 GB = Classic 2G
        info = lookup_by_usb_product_id(0x1261, capacity_gb=120)
        assert info is not None
        assert info.generation == IpodGeneration.CLASSIC_2

    def test_classic_160gb_ambiguous(self):
        # 160 GB exists in both Classic 1G and 3G
        info = lookup_by_usb_product_id(0x1261, capacity_gb=160)
        # Could match either — may be ambiguous or pick first
        # Just verify it doesn't crash
        assert info is None or info.generation in (
            IpodGeneration.CLASSIC_1,
            IpodGeneration.CLASSIC_3,
        )

    def test_unknown_product_id(self):
        info = lookup_by_usb_product_id(0x9999)
        assert info is None

    def test_video_ambiguous(self):
        # 0x1209 maps to both VIDEO_1 and VIDEO_2 — ambiguous without capacity
        info = lookup_by_usb_product_id(0x1209)
        assert info is None

    def test_video_with_capacity_still_ambiguous(self):
        # VIDEO_1 and VIDEO_2 share same capacities (30/60/80 GB)
        info = lookup_by_usb_product_id(0x1209, capacity_gb=30)
        # Both generations have 30GB models, so still ambiguous
        assert info is None or info.generation in (IpodGeneration.VIDEO_1, IpodGeneration.VIDEO_2)

    def test_shuffle_usb(self):
        info = lookup_by_usb_product_id(0x1300)
        assert info is not None
        assert info.generation == IpodGeneration.SHUFFLE_1


# =========================================================================
# Generation sets consistency
# =========================================================================


class TestGenerationSets:
    def test_hash_sets_disjoint(self):
        """Hash type sets should not overlap."""
        assert not (HASH58_GENERATIONS & HASH72_GENERATIONS)
        assert not (HASH58_GENERATIONS & HASHAB_GENERATIONS)
        assert not (HASH72_GENERATIONS & HASHAB_GENERATIONS)

    def test_all_generations_have_db_version(self):
        for gen in IpodGeneration:
            ver = get_db_version(gen)
            assert isinstance(ver, int)
            assert ver > 0

    def test_info_table_not_empty(self):
        assert len(IPOD_INFO_TABLE) > 30

    def test_info_table_has_all_generations(self):
        gens_in_table = {info.generation for info in IPOD_INFO_TABLE}
        for gen in IpodGeneration:
            assert gen in gens_in_table, f"Missing generation in table: {gen}"

    def test_shuffles_are_shuffles(self):
        for gen in SHUFFLE_GENERATIONS:
            name = get_generation_name(gen)
            assert "Shuffle" in name

    def test_video_generations_support_video(self):
        for gen in VIDEO_GENERATIONS:
            assert gen in ARTWORK_GENERATIONS
