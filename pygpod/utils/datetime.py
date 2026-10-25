"""Mac/iPod timestamp conversion utilities.

iTunesDB timestamps are seconds since January 1, 1904 (Mac epoch) in the iPod's local wall
time: like libgpod, ``tz_shift`` is the iPod's UTC offset in seconds east
(``pygpod.utils.timezone.device_timezone_shift``); 0 treats the timestamps as UTC.
Unix timestamps are seconds since January 1, 1970 00:00:00 UTC.
"""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

# Offset between Mac epoch (1904-01-01) and Unix epoch (1970-01-01) in seconds
MAC_EPOCH_OFFSET = 2082844800

# Range of a stored (unsigned 32-bit) timestamp; 0 means "not set"
MAC_MIN = 1
MAC_MAX = 0xFFFFFFFF


def mac_to_datetime(mac_timestamp: int, tz_shift: int = 0) -> datetime.datetime:
    """Convert a Mac timestamp to a Python datetime (UTC).

    Args:
        mac_timestamp: Seconds since 1904-01-01 00:00:00 in a zone ``tz_shift`` seconds east
            of UTC (0: UTC).

    Returns:
        Python datetime object in UTC, or datetime.min if timestamp is 0.
    """
    if mac_timestamp == 0:
        return datetime.datetime.min
    unix_ts = mac_timestamp - MAC_EPOCH_OFFSET - tz_shift
    try:
        return datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc)
    except (OSError, OverflowError, ValueError):
        logger.debug("Invalid Mac timestamp: %d", mac_timestamp, exc_info=True)
        return datetime.datetime.min


def datetime_to_mac(dt: datetime.datetime, tz_shift: int = 0) -> int:
    """Convert a Python datetime to a Mac timestamp.

    Args:
        dt: Python datetime object (a naive one is local time of the host).
        tz_shift: The iPod's UTC offset in seconds east (0: UTC).

    Returns:
        Mac timestamp (seconds since 1904-01-01), 0 for ``datetime.min``. Dates outside
        the unsigned 32-bit range (before 1904-01-01 00:00:01 or after 2040-02-06 06:28:15
        iPod-local time) are clamped to its ends with a warning instead of wrapping around.
    """
    if dt == datetime.datetime.min:
        return 0
    mac = int(dt.timestamp()) + MAC_EPOCH_OFFSET + tz_shift
    if mac < MAC_MIN or mac > MAC_MAX:
        clamped = MAC_MIN if mac < MAC_MIN else MAC_MAX
        logger.warning(
            "Date %s is outside the iPod's timestamp range (1904-2040); stored as %s",
            dt.isoformat(),
            "1904-01-01 00:00:01" if clamped == MAC_MIN else "2040-02-06 06:28:15",
        )
        return clamped
    return mac


def now_mac(tz_shift: int = 0) -> int:
    """Get the current time as a Mac timestamp (``tz_shift``: see datetime_to_mac)."""
    return datetime_to_mac(datetime.datetime.now(tz=datetime.timezone.utc), tz_shift)
