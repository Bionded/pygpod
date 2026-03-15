"""Tests for datetime conversion edge cases."""

import datetime

import pytest

from pygpod.utils.datetime import MAC_EPOCH_OFFSET, datetime_to_mac, mac_to_datetime, now_mac


class TestMacToDatetime:
    def test_zero_returns_min(self):
        assert mac_to_datetime(0) == datetime.datetime.min

    def test_known_date(self):
        # 2020-01-01 00:00:00 UTC
        # Unix: 1577836800, Mac: 1577836800 + 2082844800 = 3660681600
        dt = mac_to_datetime(3660681600)
        assert dt.year == 2020
        assert dt.month == 1
        assert dt.day == 1

    def test_mac_epoch_start(self):
        # Mac timestamp = MAC_EPOCH_OFFSET corresponds to Unix epoch (1970-01-01)
        dt = mac_to_datetime(MAC_EPOCH_OFFSET)
        assert dt.year == 1970
        assert dt.month == 1
        assert dt.day == 1

    def test_overflow_returns_min(self):
        # Huge timestamp that would overflow
        dt = mac_to_datetime(2**63)
        assert dt == datetime.datetime.min

    def test_negative_handled(self):
        # Negative timestamp — may return min or a pre-1904 date depending on platform
        dt = mac_to_datetime(-1)
        assert isinstance(dt, datetime.datetime)

    def test_small_value_pre_1970(self):
        # Small positive value (before 1970) — should be valid pre-1970 date
        dt = mac_to_datetime(1)
        # 1 second after 1904-01-01, Unix epoch offset makes this very negative
        # Should return min due to OSError on platforms that don't support pre-1970
        assert dt == datetime.datetime.min or dt.year < 1970


class TestDatetimeToMac:
    def test_min_returns_zero(self):
        assert datetime_to_mac(datetime.datetime.min) == 0

    def test_known_date(self):
        dt = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        mac = datetime_to_mac(dt)
        assert mac == 3660681600

    def test_round_trip(self):
        original = datetime.datetime(2024, 6, 15, 12, 30, 0, tzinfo=datetime.timezone.utc)
        mac = datetime_to_mac(original)
        result = mac_to_datetime(mac)
        assert result.year == original.year
        assert result.month == original.month
        assert result.day == original.day
        assert result.hour == original.hour


class TestNowMac:
    def test_now_is_recent(self):
        mac = now_mac()
        # Should be after 2024-01-01 (Mac timestamp ~3786912000)
        assert mac > 3786912000
        # Should be a positive integer
        assert isinstance(mac, int)
        assert mac > 0

    def test_now_round_trips(self):
        mac = now_mac()
        dt = mac_to_datetime(mac)
        assert dt.year >= 2024
