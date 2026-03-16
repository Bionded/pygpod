"""iPod timezone handling - generation-specific timezone encoding.

Ported from libgpod's itdb_tzinfo.c. Different iPod generations store
timezone information differently in the iTunesDB:

- iPod 4G: Raw value at prefs offset 0xB10, encoded as shifted GMT offset
- iPod Video/5G: Value at prefs offset 0xB22, minutes from Tokyo timezone
- iPod Classic/Nano 3G+: City-based lookup at prefs offset 0xB70

The timezone offset is stored in the MHBD header at offset 0x6C as a
signed 32-bit integer representing seconds from UTC.
"""

from __future__ import annotations

import logging
import os
import struct
import time
from typing import Optional

logger = logging.getLogger(__name__)


def get_local_tz_offset() -> int:
    """Get the local timezone offset in seconds from UTC.

    Returns:
        Offset in seconds (positive = east of UTC, negative = west).
    """
    if time.daylight and time.localtime().tm_isdst:
        return -time.altzone
    return -time.timezone


def read_timezone_from_prefs(prefs_path: str) -> Optional[int]:
    """Read timezone offset from iPod preferences file.

    The preferences file is iPod_Control/Device/Preferences and its
    size determines which iPod generation format to use:
    - 2892 bytes: iPod 4G
    - 2924 bytes: iPod Video/5G
    - 2952-2960 bytes: iPod Classic/Nano 3G+

    Args:
        prefs_path: Path to the Preferences file.

    Returns:
        Timezone offset in seconds from UTC, or None if not determinable.
    """
    if not os.path.isfile(prefs_path):
        return None

    try:
        with open(prefs_path, "rb") as f:
            data = f.read()
    except (OSError, IOError):
        logger.debug("Cannot read timezone prefs file: %s", prefs_path, exc_info=True)
        return None

    size = len(data)

    if size == 2892:
        return _parse_tz_4g(data)
    elif size == 2924:
        return _parse_tz_5g(data)
    elif 2952 <= size <= 2960:
        return _parse_tz_6g(data)

    return None


def _parse_tz_4g(data: bytes) -> Optional[int]:
    """Parse timezone from iPod 4G preferences.

    Raw value at offset 0xB10, adjusted by subtracting GMT base (0x19),
    then shifted right by 1 for hours. LSB indicates DST.

    Args:
        data: Preferences file data.

    Returns:
        Timezone offset in seconds from UTC.
    """
    if len(data) < 0xB10 + 1:
        return None

    raw = data[0xB10]
    if raw == 0:
        return None

    # Subtract GMT base offset
    adjusted = raw - 0x19
    hours = adjusted >> 1
    dst = adjusted & 1

    offset = hours * 3600
    if dst:
        offset += 3600

    return _validate_tz(offset)


def _parse_tz_5g(data: bytes) -> Optional[int]:
    """Parse timezone from iPod Video/5G preferences.

    Value at offset 0xB22 stored as minutes offset from Tokyo timezone.

    Args:
        data: Preferences file data.

    Returns:
        Timezone offset in seconds from UTC.
    """
    if len(data) < 0xB22 + 2:
        return None

    raw = struct.unpack_from("<h", data, 0xB22)[0]
    if raw == 0:
        return None

    # Tokyo is UTC+9, so subtract 9 hours worth of minutes
    tokyo_offset = 9 * 60  # 540 minutes
    offset_minutes = raw - tokyo_offset
    offset_seconds = offset_minutes * 60

    return _validate_tz(offset_seconds)


def _parse_tz_6g(data: bytes) -> Optional[int]:
    """Parse timezone from iPod Classic/Nano 3G+ preferences.

    City index at offset 0xB70, maps to IANA timezone.

    Args:
        data: Preferences file data.

    Returns:
        Timezone offset in seconds from UTC.
    """
    if len(data) < 0xB70 + 2:
        return None

    city_idx = struct.unpack_from("<H", data, 0xB70)[0]
    if city_idx == 0 or city_idx == 0xFFFF:
        return None

    # Look up city timezone
    tz_name = _CITY_TIMEZONES.get(city_idx)
    if tz_name:
        offset = _get_tz_offset(tz_name)
        if offset is not None:
            return offset

    return None


def _get_tz_offset(tz_name: str) -> Optional[int]:
    """Get UTC offset for a timezone name.

    Tries to use the system zoneinfo, falls back to Python's zoneinfo module.

    Args:
        tz_name: IANA timezone name (e.g., 'America/New_York').

    Returns:
        Offset in seconds from UTC, or None.
    """
    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(tz_name)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        offset = now.astimezone(tz).utcoffset()
        if offset:
            return int(offset.total_seconds())
    except (ImportError, KeyError, AttributeError):
        logger.debug("zoneinfo lookup failed for %r", tz_name, exc_info=True)

    # Fallback: try reading from /usr/share/zoneinfo
    zi_path = f"/usr/share/zoneinfo/{tz_name}"
    if os.path.isfile(zi_path):
        offset = _parse_zoneinfo_file(zi_path)
        if offset is not None:
            return offset

    return None


def _parse_zoneinfo_file(path: str) -> Optional[int]:
    """Parse a binary zoneinfo file to get current UTC offset.

    Reads the TZif format file and finds the last transition before now.

    Args:
        path: Path to zoneinfo file.

    Returns:
        UTC offset in seconds, or None.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except (OSError, IOError):
        logger.debug("Cannot read zoneinfo file: %s", path, exc_info=True)
        return None

    if len(data) < 44 or data[:4] != b"TZif":
        return None

    # Parse TZif header
    is_v2 = data[4:5] in (b"2", b"3")

    # V1 header
    tzh_ttisutcnt = struct.unpack_from(">I", data, 20)[0]
    tzh_ttisstdcnt = struct.unpack_from(">I", data, 24)[0]
    tzh_leapcnt = struct.unpack_from(">I", data, 28)[0]
    tzh_timecnt = struct.unpack_from(">I", data, 32)[0]
    tzh_typecnt = struct.unpack_from(">I", data, 36)[0]
    tzh_charcnt = struct.unpack_from(">I", data, 40)[0]

    if is_v2:
        # Skip V1 data to get to V2
        v1_size = (
            44
            + tzh_timecnt * 5
            + tzh_typecnt * 6
            + tzh_charcnt
            + tzh_leapcnt * 8
            + tzh_ttisstdcnt
            + tzh_ttisutcnt
        )
        if v1_size + 44 > len(data):
            return None

        data = data[v1_size:]
        if len(data) < 44 or data[:4] != b"TZif":
            return None

        tzh_timecnt = struct.unpack_from(">I", data, 32)[0]
        tzh_typecnt = struct.unpack_from(">I", data, 36)[0]

        offset = 44
        now = int(time.time())

        # Find last transition before now
        type_idx = 0
        for i in range(tzh_timecnt):
            trans_time = struct.unpack_from(">q", data, offset + i * 8)[0]
            if trans_time <= now:
                type_offset = offset + tzh_timecnt * 8
                type_idx = data[type_offset + i]
            else:
                break

        # Read ttinfo
        ttinfo_offset = offset + tzh_timecnt * 9
        if ttinfo_offset + type_idx * 6 + 4 <= len(data):
            utoff = struct.unpack_from(">i", data, ttinfo_offset + type_idx * 6)[0]
            return utoff

    return None


def _validate_tz(offset: int) -> Optional[int]:
    """Validate timezone offset is within reasonable range.

    Args:
        offset: Timezone offset in seconds.

    Returns:
        Validated offset, or None if out of range (±12 hours).
    """
    if abs(offset) > 50400:  # ±14 hours
        return None
    return offset


def read_timezone_from_device(mountpoint: str) -> Optional[int]:
    """Read timezone from iPod device.

    Args:
        mountpoint: iPod mount point path.

    Returns:
        Timezone offset in seconds from UTC, or None.
    """
    prefs_path = os.path.join(mountpoint, "iPod_Control", "Device", "Preferences")
    return read_timezone_from_prefs(prefs_path)


def mac_timestamp_with_tz(mac_timestamp: int, tz_offset: int = 0) -> int:
    """Adjust a Mac timestamp by timezone offset.

    Args:
        mac_timestamp: Mac timestamp (seconds since 1904-01-01).
        tz_offset: Timezone offset in seconds.

    Returns:
        Adjusted timestamp.
    """
    return mac_timestamp + tz_offset


# City index → IANA timezone mapping (common cities from iPod firmware)
_CITY_TIMEZONES = {
    1: "Pacific/Midway",
    2: "Pacific/Honolulu",
    3: "America/Anchorage",
    4: "America/Los_Angeles",
    5: "America/Phoenix",
    6: "America/Denver",
    7: "America/Chicago",
    8: "America/Mexico_City",
    9: "America/New_York",
    10: "America/Caracas",
    11: "America/Halifax",
    12: "America/St_Johns",
    13: "America/Argentina/Buenos_Aires",
    14: "America/Sao_Paulo",
    15: "Atlantic/South_Georgia",
    16: "Atlantic/Azores",
    17: "Atlantic/Cape_Verde",
    18: "Europe/London",
    19: "Europe/Paris",
    20: "Europe/Berlin",
    21: "Europe/Athens",
    22: "Europe/Helsinki",
    23: "Africa/Cairo",
    24: "Europe/Moscow",
    25: "Asia/Baghdad",
    26: "Asia/Tehran",
    27: "Asia/Dubai",
    28: "Asia/Kabul",
    29: "Asia/Karachi",
    30: "Asia/Kolkata",
    31: "Asia/Kathmandu",
    32: "Asia/Dhaka",
    33: "Asia/Rangoon",
    34: "Asia/Bangkok",
    35: "Asia/Shanghai",
    36: "Asia/Hong_Kong",
    37: "Asia/Tokyo",
    38: "Asia/Seoul",
    39: "Australia/Adelaide",
    40: "Australia/Sydney",
    41: "Pacific/Guam",
    42: "Pacific/Noumea",
    43: "Pacific/Auckland",
    44: "Pacific/Tongatapu",
}
