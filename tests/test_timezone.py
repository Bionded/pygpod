"""Tests for pygpod.utils.timezone - iPod timezone handling."""

from __future__ import annotations

import struct
import time
from unittest import mock

import pytest

from pygpod.utils import timezone as timezone_mod
from pygpod.utils.timezone import (
    _CITY_TIMEZONES,
    _get_tz_offset,
    _parse_tz_4g,
    _parse_tz_5g,
    _parse_tz_6g,
    _parse_zoneinfo_file,
    _validate_tz,
    device_timezone_shift,
    get_local_tz_offset,
    mac_timestamp_with_tz,
    read_timezone_from_device,
    read_timezone_from_prefs,
)

from .conftest import require_tz_database

# ---------------------------------------------------------------------------
# get_local_tz_offset
# ---------------------------------------------------------------------------


class TestGetLocalTzOffset:
    """Tests for get_local_tz_offset(): the host's current offset (tm_gmtoff), like libgpod."""

    def test_uses_tm_gmtoff(self):
        lt = mock.Mock(tm_gmtoff=7200)
        with mock.patch("pygpod.utils.timezone.time.localtime", return_value=lt):
            assert get_local_tz_offset() == 7200

    def test_matches_the_host(self):
        assert get_local_tz_offset() == time.localtime().tm_gmtoff

    def test_without_tm_gmtoff_dst_active(self):
        lt = time.struct_time((2025, 7, 15, 12, 0, 0, 1, 196, 1))
        with mock.patch("pygpod.utils.timezone.time") as mock_time:
            mock_time.daylight = 1
            mock_time.altzone = -7200  # UTC+2
            mock_time.localtime.return_value = lt
            assert get_local_tz_offset() == 7200

    def test_without_tm_gmtoff_no_dst(self):
        lt = time.struct_time((2025, 1, 15, 12, 0, 0, 2, 15, 0))
        with mock.patch("pygpod.utils.timezone.time") as mock_time:
            mock_time.daylight = 1
            mock_time.timezone = -3600
            mock_time.localtime.return_value = lt
            assert get_local_tz_offset() == 3600


# ---------------------------------------------------------------------------
# read_timezone_from_prefs
# ---------------------------------------------------------------------------


def _prefs(size, offset, value):
    data = bytearray(size)
    struct.pack_into("<h", data, offset, value)
    return bytes(data)


def _write(tmp_path, data):
    path = tmp_path / "Preferences"
    path.write_bytes(data)
    return str(path)


class TestReadTimezoneFromPrefs:
    """Tests for read_timezone_from_prefs(): format by file size, as libgpod."""

    def test_file_not_found(self, tmp_path):
        assert read_timezone_from_prefs(str(tmp_path / "Preferences")) is None

    def test_file_read_error(self):
        with mock.patch("pygpod.utils.timezone.os.path.isfile", return_value=True):
            with mock.patch("builtins.open", side_effect=OSError("disk error")):
                assert read_timezone_from_prefs("/fake/Preferences") is None

    @pytest.mark.parametrize(
        "size,offset,value,expected",
        [
            (2892, 0xB10, 0x19 + 4, 7200),  # iPod 4G: UTC+2
            (2924, 0xB22, 660, 10800),  # iPod Video: 660 min - 8 h = UTC+3
            (2952, 0xB70, 0xC2, 32400),  # Classic: Tokyo
            (2960, 0xB70, 0xC2, 32400),  # nano 4G: Tokyo
        ],
    )
    def test_formats(self, tmp_path, size, offset, value, expected):
        if offset == 0xB70:  # a city
            require_tz_database()
        assert read_timezone_from_prefs(_write(tmp_path, _prefs(size, offset, value))) == expected

    @pytest.mark.parametrize("size", [1000, 2956, 2893])
    def test_unknown_size(self, tmp_path, size):
        assert read_timezone_from_prefs(_write(tmp_path, _prefs(size, 0, 0xC2))) is None

    def test_beyond_12_hours_is_ignored(self, tmp_path):
        # iPod Video: 1300 min - 8 h = +13 h 40 min
        assert read_timezone_from_prefs(_write(tmp_path, _prefs(2924, 0xB22, 1300))) is None


class TestParseTz4g:
    def test_data_too_short(self):
        assert _parse_tz_4g(b"\x00" * 0xB11) is None

    def test_utc(self):
        assert _parse_tz_4g(_prefs(2892, 0xB10, 0x19)) == 0

    def test_positive_offset_no_dst(self):
        assert _parse_tz_4g(_prefs(2892, 0xB10, 0x19 + 10)) == 5 * 3600

    def test_positive_offset_with_dst(self):
        assert _parse_tz_4g(_prefs(2892, 0xB10, 0x19 + 5)) == 3 * 3600

    def test_west_of_utc(self):
        assert _parse_tz_4g(_prefs(2892, 0xB10, 0x19 - 10)) == -5 * 3600

    @pytest.mark.parametrize("raw", [-1, 49, 0x7FFF])
    def test_invalid(self, raw):
        assert _parse_tz_4g(_prefs(2892, 0xB10, raw)) is None


class TestParseTz5g:
    def test_data_too_short(self):
        assert _parse_tz_5g(b"\x00" * 0xB23) is None

    @pytest.mark.parametrize(
        "raw,expected", [(480, 0), (660, 3 * 3600), (180, -5 * 3600), (0, -8 * 3600)]
    )
    def test_minutes_minus_8_hours(self, raw, expected):
        assert _parse_tz_5g(_prefs(2924, 0xB22, raw)) == expected


class TestParseTz6g:
    def test_data_too_short(self):
        assert _parse_tz_6g(b"\x00" * 0xB71) is None

    def test_known_city(self):
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=32400) as m:
            assert _parse_tz_6g(_prefs(2952, 0xB70, 0xC2)) == 32400
            m.assert_called_once_with("Asia/Tokyo")

    def test_known_city_offset_none(self):
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=None):
            assert _parse_tz_6g(_prefs(2952, 0xB70, 0xC2)) is None

    @pytest.mark.parametrize("raw", [0, 0x100, -1, 999])
    def test_unknown_city_index(self, raw):
        assert _parse_tz_6g(_prefs(2952, 0xB70, raw)) is None

    def test_legacy_zone_names_resolve(self):
        # libgpod's table names e.g. "PST8PDT", which newer tz databases may not ship
        require_tz_database()
        assert _get_tz_offset("PST8PDT") in (-8 * 3600, -7 * 3600)


# ---------------------------------------------------------------------------
# _get_tz_offset - lines 169-189
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
# _parse_zoneinfo_file - lines 203-264
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

    def test_boundary_14h(self):
        assert _validate_tz(50400) == 50400  # exactly +14h

    def test_boundary_minus_14h(self):
        assert _validate_tz(-50400) == -50400  # exactly -14h

    def test_exceeds_positive(self):
        assert _validate_tz(50401) is None

    def test_exceeds_negative(self):
        assert _validate_tz(-50401) is None


# ---------------------------------------------------------------------------
# read_timezone_from_device - lines 290-291
# ---------------------------------------------------------------------------


class TestReadTimezoneFromDevice:
    """Tests for read_timezone_from_device() and device_timezone_shift()."""

    def _ipod(self, tmp_path, dirs=("iPod_Control", "Device"), name="Preferences"):
        d = tmp_path.joinpath(*dirs)
        d.mkdir(parents=True)
        (d / name).write_bytes(_prefs(2952, 0xB70, 0xC2))
        return str(tmp_path)

    def test_reads_the_preferences(self, tmp_path):
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=32400):
            assert read_timezone_from_device(self._ipod(tmp_path)) == 32400

    def test_case_insensitive(self, tmp_path):
        mp = self._ipod(tmp_path, ("IPOD_CONTROL", "device"), "preferences")
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=32400):
            assert read_timezone_from_device(mp) == 32400

    def test_returns_none_on_missing(self, tmp_path):
        assert read_timezone_from_device(str(tmp_path)) is None

    def test_shift_falls_back_to_the_host(self, tmp_path):
        with mock.patch("pygpod.utils.timezone.get_local_tz_offset", return_value=-14400):
            assert device_timezone_shift(str(tmp_path)) == -14400
            assert device_timezone_shift(None) == -14400
        with mock.patch("pygpod.utils.timezone._get_tz_offset", return_value=32400):
            assert device_timezone_shift(self._ipod(tmp_path)) == 32400


# ---------------------------------------------------------------------------
# mac_timestamp_with_tz - line 304
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
    """Sanity checks on the city table (libgpod's)."""

    def test_has_expected_cities(self):
        assert _CITY_TIMEZONES[0xC2] == "Asia/Tokyo"
        assert _CITY_TIMEZONES[0x4F] == "Europe/London"
        assert _CITY_TIMEZONES[0x87] == "Asia/Qatar"
        assert len(_CITY_TIMEZONES) == 204

    def test_every_zone_resolves(self):
        require_tz_database()
        for idx, name in _CITY_TIMEZONES.items():
            assert _get_tz_offset(name) is not None, f"city 0x{idx:02x}: {name}"


def test_tz_tests_skip_without_a_tz_database(monkeypatch):
    monkeypatch.setattr(timezone_mod, "_zone_offset", lambda name: None)
    with pytest.raises(pytest.skip.Exception, match="tzdata"):
        require_tz_database()
