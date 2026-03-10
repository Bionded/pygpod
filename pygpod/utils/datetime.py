"""Mac/iPod timestamp conversion utilities.

iTunesDB timestamps are seconds since January 1, 1904 00:00:00 UTC (Mac epoch).
Unix timestamps are seconds since January 1, 1970 00:00:00 UTC.
"""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

# Offset between Mac epoch (1904-01-01) and Unix epoch (1970-01-01) in seconds
MAC_EPOCH_OFFSET = 2082844800


def mac_to_datetime(mac_timestamp: int) -> datetime.datetime:
    """Convert a Mac timestamp to a Python datetime (UTC).

    Args:
        mac_timestamp: Seconds since 1904-01-01 00:00:00 UTC.

    Returns:
        Python datetime object in UTC, or datetime.min if timestamp is 0.
    """
    if mac_timestamp == 0:
        return datetime.datetime.min
    unix_ts = mac_timestamp - MAC_EPOCH_OFFSET
    try:
        return datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc)
    except (OSError, OverflowError, ValueError):
        logger.debug("Invalid Mac timestamp: %d", mac_timestamp, exc_info=True)
        return datetime.datetime.min


def datetime_to_mac(dt: datetime.datetime) -> int:
    """Convert a Python datetime to a Mac timestamp.

    Args:
        dt: Python datetime object.

    Returns:
        Mac timestamp (seconds since 1904-01-01).
    """
    if dt == datetime.datetime.min:
        return 0
    unix_ts = int(dt.timestamp())
    return unix_ts + MAC_EPOCH_OFFSET


def now_mac() -> int:
    """Get the current time as a Mac timestamp."""
    return datetime_to_mac(datetime.datetime.now(tz=datetime.timezone.utc))
