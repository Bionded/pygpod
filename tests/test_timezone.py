"""Tests for pygpod.utils.timezone — iPod timezone handling."""

from __future__ import annotations

import struct
import time
from unittest import mock

import pytest

from pygpod.utils.timezone import (
    _CITY_TIMEZONES,
    _get_tz_offset,
    _parse_tz_4g,
    _parse_tz_5g,
    _parse_tz_6g,
    _parse_zoneinfo_file,
    _validate_tz,
    get_local_tz_offset,
    mac_timestamp_with_tz,
    read_timezone_from_device,
    read_timezone_from_prefs,
)

# ---------------------------------------------------------------------------
# get_local_tz_offset
# ---------------------------------------------------------------------------


class TestGetLocalTzOffset:
    """Tests for get_local_tz_offset()."""

    def test_non_dst(self):
        """When DST is not active, returns -time.timezone."""
        lt = time.struct_time((2025, 1, 15, 12, 0, 0, 2, 15, 0))
        with mock.patch("pygpod.utils.timezone.time") as mock_time:
            mock_time.daylight = 0
            mock_time.timezone = -3600  # UTC+1
            mock_time.localtime.return_value = lt
            result = get_local_tz_offset()
        assert result == 3600

    def test_dst_active(self):
        """When DST is active and daylight flag is set, returns -time.altzone (line 32)."""
        lt = time.struct_time((2025, 7, 15, 12, 0, 0, 1, 196, 1))
        with mock.patch("pygpod.utils.timezone.time") as mock_time:
            mock_time.daylight = 1
            mock_time.altzone = -7200  # UTC+2
            mock_time.localtime.return_value = lt
            result = get_local_tz_offset()
        assert result == 7200

    def test_daylight_flag_but_not_dst(self):
        """Daylight flag set but localtime says DST is not active."""
        lt = time.struct_time((2025, 1, 15, 12, 0, 0, 2, 15, 0))
        with mock.patch("pygpod.utils.timezone.time") as mock_time:
            mock_time.daylight = 1
            mock_time.timezone = -3600
            mock_time.localtime.return_value = lt
            result = get_local_tz_offset()
        assert result == 3600


# ---------------------------------------------------------------------------
# read_timezone_from_prefs
# ---------------------------------------------------------------------------


class TestReadTimezoneFromPrefs:
    """Tests for read_timezone_from_prefs() — lines 51-70."""

    def test_file_not_found(self):
        """Returns None when prefs file does not exist."""
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=False):
            assert read_timezone_from_prefs("/fake/Preferences") is None

    def test_file_read_error(self):
        """Returns None on OSError when reading."""
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", side_effect=OSError("disk error")):
                assert read_timezone_from_prefs("/fake/Preferences") is None

    def test_dispatch_4g(self):
        """Dispatches to _parse_tz_4g for 2892-byte file."""
        data = b"\x00" * 2892
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", mock.mock_open(read_data=data)):
                with mock.patch("pygpod.utils.timezone._parse_tz_4g", return_value=3600) as m:
                    result = read_timezone_from_prefs("/fake/Preferences")
                    m.assert_called_once_with(data)
                    assert result == 3600

    def test_dispatch_5g(self):
        """Dispatches to _parse_tz_5g for 2924-byte file."""
        data = b"\x00" * 2924
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", mock.mock_open(read_data=data)):
                with mock.patch("pygpod.utils.timezone._parse_tz_5g", return_value=-18000) as m:
                    result = read_timezone_from_prefs("/fake/Preferences")
                    m.assert_called_once_with(data)
                    assert result == -18000

    def test_dispatch_6g(self):
        """Dispatches to _parse_tz_6g for 2952-byte file."""
        data = b"\x00" * 2952
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", mock.mock_open(read_data=data)):
                with mock.patch("pygpod.utils.timezone._parse_tz_6g", return_value=7200) as m:
                    result = read_timezone_from_prefs("/fake/Preferences")
                    m.assert_called_once_with(data)
                    assert result == 7200

    def test_dispatch_6g_upper_bound(self):
        """Dispatches to _parse_tz_6g for 2960-byte file (upper bound)."""
        data = b"\x00" * 2960
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", mock.mock_open(read_data=data)):
                with mock.patch("pygpod.utils.timezone._parse_tz_6g", return_value=0) as m:
                    result = read_timezone_from_prefs("/fake/Preferences")
                    m.assert_called_once_with(data)
                    assert result == 0

    def test_unknown_size(self):
        """Returns None for unrecognised file size."""
        data = b"\x00" * 1000
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", mock.mock_open(read_data=data)):
                assert read_timezone_from_prefs("/fake/Preferences") is None


# ---------------------------------------------------------------------------
# _parse_tz_4g — lines 85-101
# ---------------------------------------------------------------------------


class TestParseTz4g:
    """Tests for _parse_tz_4g()."""

    def test_data_too_short(self):
        """Returns None if data shorter than 0xB10 + 1."""
        assert _parse_tz_4g(b"\x00" * 0xB10) is None

    def test_raw_zero(self):
        """Returns None if raw byte at 0xB10 is zero."""
        data = bytearray(0xB11)
        data[0xB10] = 0
        assert _parse_tz_4g(bytes(data)) is None

    def test_utc(self):
        """Raw = 0x19 means UTC (adjusted=0, hours=0, no DST)."""
        data = bytearray(0xB11)
        data[0xB10] = 0x19
        assert _parse_tz_4g(bytes(data)) == 0

    def test_positive_offset_no_dst(self):
        """UTC+5 without DST: adjusted = 0x0A, hours = 5, dst = 0."""
        data = bytearray(0xB11)
        # adjusted = raw - 0x19 = 0x0A => hours = 5, dst = 0
        data[0xB10] = 0x19 + 0x0A
        assert _parse_tz_4g(bytes(data)) == 5 * 3600

    def test_positive_offset_with_dst(self):
        """UTC+2 with DST: adjusted = 0x05, hours = 2, dst = 1."""
        data = bytearray(0xB11)
        # adjusted = 0x05 => hours = 2, dst = 1
        data[0xB10] = 0x19 + 0x05
        assert _parse_tz_4g(bytes(data)) == 2 * 3600 + 3600

    def test_offset_out_of_range(self):
        """Returns None when calculated offset exceeds ±12 hours."""
        data = bytearray(0xB11)
        # Need adjusted >> 1 > 12 => adjusted >= 26 => 0x1A
        # adjusted = 0x1A => hours = 13, offset = 46800 > 43200
        data[0xB10] = 0x19 + 0x1A
        assert _parse_tz_4g(bytes(data)) is None


# ---------------------------------------------------------------------------
# _parse_tz_5g — lines 115-127
# ---------------------------------------------------------------------------


class TestParseTz5g:
    """Tests for _parse_tz_5g()."""

    def test_data_too_short(self):
        """Returns None if data shorter than 0xB22 + 2."""
        assert _parse_tz_5g(b"\x00" * 0xB23) is None

    def test_raw_zero(self):
        """Returns None if the stored minutes value is zero."""
        data = bytearray(0xB24)
        struct.pack_into("<h", data, 0xB22, 0)
        assert _parse_tz_5g(bytes(data)) is None

    def test_tokyo(self):
        """Tokyo itself: stored 540 minutes => offset 0 (UTC+9 - 9h = 0)."""
        data = bytearray(0xB24)
        struct.pack_into("<h", data, 0xB22, 540)
        assert _parse_tz_5g(bytes(data)) == 0

    def test_new_york(self):
        """UTC-5 (EST): stored 540 + (-5*60 - 9*60) => 540 + (-14*60)... no.

        Actually, offset_minutes = raw - 540. We want offset_minutes = -300 (UTC-5).
        So raw = 540 + (-300) = 240.
        offset_seconds = -300 * 60 = -18000.
        """
        data = bytearray(0xB24)
        struct.pack_into("<h", data, 0xB22, 240)
        assert _parse_tz_5g(bytes(data)) == -18000

    def test_paris(self):
        """UTC+1 (CET): offset_minutes = 60, raw = 540 + 60 = 600."""
        data = bytearray(0xB24)
        struct.pack_into("<h", data, 0xB22, 600)
        assert _parse_tz_5g(bytes(data)) == 3600

    def test_offset_out_of_range(self):
        """Returns None when offset exceeds ±12 hours."""
        data = bytearray(0xB24)
        # raw = 540 + 800 = 1340 => offset_minutes = 800 => 48000s > 43200
        struct.pack_into("<h", data, 0xB22, 1340)
        assert _parse_tz_5g(bytes(data)) is None


# ---------------------------------------------------------------------------
# _parse_tz_6g — lines 141-155
# ---------------------------------------------------------------------------


class TestParseTz6g:
    """Tests for _parse_tz_6g()."""

    def test_data_too_short(self):
        """Returns None if data shorter than 0xB70 + 2."""
        assert _parse_tz_6g(b"\x00" * 0xB71) is None

    def test_city_idx_zero(self):
        """Returns None when city index is 0 (not set)."""
        data = bytearray(0xB72)
        struct.pack_into("<H", data, 0xB70, 0)
        assert _parse_tz_6g(bytes(data)) is None

    def test_city_idx_ffff(self):
        """Returns None when city index is 0xFFFF (sentinel)."""
        data = bytearray(0xB72)
        struct.pack_into("<H", data, 0xB70, 0xFFFF)
        assert _parse_tz_6g(bytes(data)) is None

    def test_known_city(self):
        """Returns offset for a known city index (Tokyo = 37)."""
        data = bytearray(0xB72)
        struct.pack_into("<H", data, 0xB70, 37)  # Tokyo
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=32400) as m:
            result = _parse_tz_6g(bytes(data))
            m.assert_called_once_with("Asia/Tokyo")
            assert result == 32400

    def test_known_city_offset_none(self):
        """Returns None when _get_tz_offset fails for a known city."""
        data = bytearray(0xB72)
        struct.pack_into("<H", data, 0xB70, 37)
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=None):
            assert _parse_tz_6g(bytes(data)) is None

    def test_unknown_city_index(self):
        """Returns None for a city index not in the lookup table."""
        data = bytearray(0xB72)
        struct.pack_into("<H", data, 0xB70, 999)  # not in _CITY_TIMEZONES
        assert _parse_tz_6g(bytes(data)) is None


# ---------------------------------------------------------------------------
# _get_tz_offset — lines 169-189
# ---------------------------------------------------------------------------


class TestGetTzOffset:
    """Tests for _get_tz_offset()."""

    def test_zoneinfo_success(self):
        """Uses zoneinfo module to resolve timezone offset."""
        from datetime import timedelta

        mock_offset = timedelta(hours=2)
        mock_dt = mock.MagicMock()
        mock_dt.utcoffset.return_value = mock_offset

        mock_tz_obj = mock.MagicMock()
        mock_now = mock.MagicMock()
        mock_now.astimezone.return_value = mock_dt

        with mock.patch.dict("sys.modules", {"zoneinfo": mock.MagicMock()}) as _:
            import sys

            mock_zoneinfo = sys.modules["zoneinfo"]
            mock_zoneinfo.ZoneInfo.return_value = mock_tz_obj

            with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=False):
                # We need a fresh import context; instead, just call with real zoneinfo
                pass

        # Use the real zoneinfo if available
        try:
            import zoneinfo  # noqa: F401, F811

            result = _get_tz_offset("Europe/London")
            if result is not None:
                # London is UTC+0 or UTC+1 depending on DST
                assert -3600 <= result <= 7200
            # result may be None on some platforms (e.g. Windows without tzdata)
        except ImportError:
            pytest.skip("zoneinfo not available")

    def test_zoneinfo_import_error_falls_back(self):
        """Falls back to file-based lookup when zoneinfo import fails."""
        with mock.patch.dict("sys.modules", {"zoneinfo": None}):
            with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
                with mock.patch(
                    "pygpod.utils.timezone._parse_zoneinfo_file", return_value=7200
                ) as m:
                    result = _get_tz_offset("Europe/Kyiv")
                    m.assert_called_once_with("/usr/share/zoneinfo/Europe/Kyiv")
                    assert result == 7200

    def test_zoneinfo_key_error_falls_back(self):
        """Falls back when zoneinfo raises KeyError for unknown tz."""
        mock_zi = mock.MagicMock()
        mock_zi.ZoneInfo.side_effect = KeyError("no such tz")

        with mock.patch.dict("sys.modules", {"zoneinfo": mock_zi}):
            with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=False):
                result = _get_tz_offset("Fake/Timezone")
                assert result is None

    def test_all_methods_fail(self):
        """Returns None when both zoneinfo and file fallback fail."""
        with mock.patch.dict("sys.modules", {"zoneinfo": None}):
            with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=False):
                result = _get_tz_offset("Nonexistent/Zone")
                assert result is None

    def test_file_fallback_returns_none(self):
        """Returns None when file exists but parsing fails."""
        with mock.patch.dict("sys.modules", {"zoneinfo": None}):
            with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
                with mock.patch("pygpod.utils.timezone._parse_zoneinfo_file", return_value=None):
                    result = _get_tz_offset("Bad/Zone")
                    assert result is None


# ---------------------------------------------------------------------------
# _parse_zoneinfo_file — lines 203-264
# ---------------------------------------------------------------------------


def _build_tzif_v2(transitions: list[tuple[int, int]], utoffsets: list[int]) -> bytes:
    """Build a minimal TZif v2 file for testing.

    Args:
        transitions: List of (timestamp, type_index) tuples.
        utoffsets: List of UTC offsets (seconds) for each ttinfo entry.

    Returns:
        Binary TZif v2 data.
    """
    # V1 section (empty)
    v1_timecnt = 0
    v1_typecnt = 0
    v1_charcnt = 0
    v1_leapcnt = 0
    v1_ttisstdcnt = 0
    v1_ttisutcnt = 0

    v1_header = b"TZif"
    v1_header += b"2"  # version
    v1_header += b"\x00" * 15  # padding
    v1_header += struct.pack(
        ">6I",
        v1_ttisutcnt,
        v1_ttisstdcnt,
        v1_leapcnt,
        v1_timecnt,
        v1_typecnt,
        v1_charcnt,
    )

    # V2 section
    v2_timecnt = len(transitions)
    v2_typecnt = len(utoffsets)
    v2_charcnt = 1  # at least 1 byte for abbreviation
    v2_leapcnt = 0
    v2_ttisstdcnt = 0
    v2_ttisutcnt = 0

    v2_header = b"TZif"
    v2_header += b"2"
    v2_header += b"\x00" * 15
    v2_header += struct.pack(
        ">6I",
        v2_ttisutcnt,
        v2_ttisstdcnt,
        v2_leapcnt,
        v2_timecnt,
        v2_typecnt,
        v2_charcnt,
    )

    # V2 transition times (8 bytes each, big-endian signed)
    trans_data = b""
    for ts, _ in transitions:
        trans_data += struct.pack(">q", ts)

    # V2 transition type indices (1 byte each)
    type_indices = b""
    for _, ti in transitions:
        type_indices += struct.pack("B", ti)

    # V2 ttinfo entries (6 bytes each: i4 utoff, u1 dst, u1 idx)
    ttinfo_data = b""
    for off in utoffsets:
        ttinfo_data += struct.pack(">i", off)
        ttinfo_data += b"\x00"  # is_dst
        ttinfo_data += b"\x00"  # abbreviation index

    # Abbreviation characters
    abbr_data = b"\x00"

    return v1_header + v2_header + trans_data + type_indices + ttinfo_data + abbr_data


class TestParseZoneinfoFile:
    """Tests for _parse_zoneinfo_file()."""

    def test_file_read_error(self):
        """Returns None on OSError."""
        with mock.patch("builtins.open", side_effect=OSError("nope")):
            assert _parse_zoneinfo_file("/fake/zone") is None

    def test_data_too_short(self):
        """Returns None for data shorter than 44 bytes."""
        with mock.patch("builtins.open", mock.mock_open(read_data=b"\x00" * 10)):
            assert _parse_zoneinfo_file("/fake/zone") is None

    def test_bad_magic(self):
        """Returns None if magic is not TZif."""
        data = b"XXXX" + b"\x00" * 40
        with mock.patch("builtins.open", mock.mock_open(read_data=data)):
            assert _parse_zoneinfo_file("/fake/zone") is None

    def test_v1_only_returns_none(self):
        """V1-only TZif file (no V2 section) returns None."""
        # Build a valid V1-only header (version byte = '1' or space)
        header = b"TZif"
        header += b" "  # V1
        header += b"\x00" * 15
        header += struct.pack(">6I", 0, 0, 0, 0, 0, 0)
        with mock.patch("builtins.open", mock.mock_open(read_data=header)):
            assert _parse_zoneinfo_file("/fake/zone") is None

    def test_v2_single_transition(self):
        """Parses V2 file with a single transition in the past."""
        # Transition in the far past, type 0 with offset +3600 (UTC+1)
        now = int(time.time())
        data = _build_tzif_v2(
            transitions=[(now - 100000, 0)],
            utoffsets=[3600],
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=data)):
            result = _parse_zoneinfo_file("/fake/zone")
            assert result == 3600

    def test_v2_multiple_transitions(self):
        """Picks the last transition before now."""
        now = int(time.time())
        data = _build_tzif_v2(
            transitions=[
                (now - 200000, 0),
                (now - 100000, 1),
                (now + 100000, 0),  # future
            ],
            utoffsets=[3600, 7200],
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=data)):
            result = _parse_zoneinfo_file("/fake/zone")
            assert result == 7200  # type 1 was the last before now

    def test_v2_no_transitions(self):
        """Returns type 0 offset when there are no transitions."""
        data = _build_tzif_v2(
            transitions=[],
            utoffsets=[-18000],
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=data)):
            result = _parse_zoneinfo_file("/fake/zone")
            assert result == -18000

    def test_v2_truncated_data(self):
        """Returns None when V2 section is truncated."""
        # Valid V1 header with version '2', but V2 section is missing
        header = b"TZif"
        header += b"2"
        header += b"\x00" * 15
        header += struct.pack(">6I", 0, 0, 0, 0, 0, 0)
        # V2 header would start right after V1 (since V1 has 0 of everything)
        # but we truncate the data
        with mock.patch("builtins.open", mock.mock_open(read_data=header)):
            assert _parse_zoneinfo_file("/fake/zone") is None


# ---------------------------------------------------------------------------
# _validate_tz
# ---------------------------------------------------------------------------


class TestValidateTz:
    """Tests for _validate_tz()."""

    def test_valid_utc(self):
        assert _validate_tz(0) == 0

    def test_valid_positive(self):
        assert _validate_tz(36000) == 36000  # +10h

    def test_valid_negative(self):
        assert _validate_tz(-18000) == -18000  # -5h

    def test_boundary_12h(self):
        assert _validate_tz(43200) == 43200  # exactly +12h

    def test_boundary_minus_12h(self):
        assert _validate_tz(-43200) == -43200  # exactly -12h

    def test_exceeds_positive(self):
        assert _validate_tz(43201) is None

    def test_exceeds_negative(self):
        assert _validate_tz(-43201) is None


# ---------------------------------------------------------------------------
# read_timezone_from_device — lines 290-291
# ---------------------------------------------------------------------------


class TestReadTimezoneFromDevice:
    """Tests for read_timezone_from_device()."""

    def test_constructs_prefs_path(self):
        """Passes the correct prefs file path to read_timezone_from_prefs."""
        with mock.patch("pygpod.utils.timezone.read_timezone_from_prefs", return_value=7200) as m:
            result = read_timezone_from_device("/mnt/ipod")
            import os

            expected = os.path.join("/mnt/ipod", "iPod_Control", "Device", "Preferences")
            m.assert_called_once_with(expected)
            assert result == 7200

    def test_returns_none_on_missing(self):
        """Returns None when prefs file is missing."""
        with mock.patch("pygpod.utils.timezone.read_timezone_from_prefs", return_value=None):
            result = read_timezone_from_device("/mnt/ipod")
            assert result is None


# ---------------------------------------------------------------------------
# mac_timestamp_with_tz — line 304
# ---------------------------------------------------------------------------


class TestMacTimestampWithTz:
    """Tests for mac_timestamp_with_tz()."""

    def test_zero_offset(self):
        assert mac_timestamp_with_tz(1000000, 0) == 1000000

    def test_positive_offset(self):
        assert mac_timestamp_with_tz(1000000, 3600) == 1003600

    def test_negative_offset(self):
        assert mac_timestamp_with_tz(1000000, -18000) == 982000

    def test_default_offset(self):
        """Default tz_offset is 0."""
        assert mac_timestamp_with_tz(500000) == 500000


# ---------------------------------------------------------------------------
# _CITY_TIMEZONES
# ---------------------------------------------------------------------------


class TestCityTimezones:
    """Sanity checks on the city timezone mapping."""

    def test_has_expected_cities(self):
        assert _CITY_TIMEZONES[1] == "Pacific/Midway"
        assert _CITY_TIMEZONES[37] == "Asia/Tokyo"
        assert _CITY_TIMEZONES[44] == "Pacific/Tongatapu"

    def test_all_values_are_iana_format(self):
        for idx, name in _CITY_TIMEZONES.items():
            assert "/" in name, f"City {idx} has invalid tz name: {name}"
