"""iPod timezone handling, as in libgpod's itdb_tzinfo.c.

iTunesDB timestamps are the iPod's local wall time (Mac epoch, 1904). libgpod converts them with
the timezone the iPod's ``iPod_Control/Device/Preferences`` gives, else the host's current UTC
offset. The Preferences format depends on the generation (told apart by the file's size):

- iPod 4G: gint16 at 0xB10, 0x19 + 2 * hours east of UTC (+1 with DST)
- iPod Video: gint16 at 0xB22, minutes (libgpod: raw * 60 - 8 h)
- nano 3G/4G, Classic: gint16 city index at 0xB70 (libgpod's city table)
"""

from __future__ import annotations

import logging
import os
import struct
import time
from typing import Optional

logger = logging.getLogger(__name__)


def get_local_tz_offset() -> int:
    """The host's current UTC offset in seconds (positive = east of UTC), DST included, like
    libgpod's fallback when the iPod's Preferences give no timezone."""
    gmtoff = getattr(time.localtime(), "tm_gmtoff", None)
    if gmtoff is not None:
        return int(gmtoff)
    if time.daylight and time.localtime().tm_isdst:
        return -time.altzone
    return -time.timezone


# Preferences file size -> offset of the raw timezone value (gint16 LE) and its decoder, as in
# libgpod's itdb_tzinfo.c; other sizes: the iPod's timezone is unknown
_PREFS_FORMATS = {
    2892: (0xB10, "_parse_tz_4g"),  # iPod 4G
    2924: (0xB22, "_parse_tz_5g"),  # iPod Video
    2952: (0xB70, "_parse_tz_6g"),  # nano 3G, Classic
    2960: (0xB70, "_parse_tz_6g"),  # nano 4G
}


def read_timezone_from_prefs(prefs_path: str) -> Optional[int]:
    """Read the timezone (UTC offset in seconds) from an iPod's Preferences file.

    The file's size gives the format (2892 bytes: iPod 4G, 2924: iPod Video, 2952/2960:
    nano 3G/4G and Classic), decoded like libgpod. None if the file is missing, of another
    size, or the value is not a valid timezone (outside +-12 hours, unknown city).
    """
    if not os.path.isfile(prefs_path):
        return None

    try:
        with open(prefs_path, "rb") as f:
            data = f.read()
    except (OSError, IOError):
        logger.debug("Cannot read timezone prefs file: %s", prefs_path, exc_info=True)
        return None

    fmt = _PREFS_FORMATS.get(len(data))
    if fmt is None:
        return None
    offset = globals()[fmt[1]](data)
    if offset is None or abs(offset) > 12 * 3600:
        return None
    return offset


def _raw(data: bytes, offset: int) -> Optional[int]:
    if len(data) < offset + 2:
        return None
    return struct.unpack_from("<h", data, offset)[0]


def _parse_tz_4g(data: bytes) -> Optional[int]:
    """iPod 4G: gint16 at 0xB10 = 0x19 + 2 * hours east of UTC, + 1 when DST is on."""
    raw = _raw(data, 0xB10)
    if raw is None or raw < 0 or raw > (2 * 12) << 1:
        return None
    raw -= 0x19
    return (raw >> 1) * 3600 + (3600 if raw & 1 else 0)


def _parse_tz_5g(data: bytes) -> Optional[int]:
    """iPod Video: gint16 at 0xB22 = minutes; libgpod reads it as raw * 60 s - 8 h."""
    raw = _raw(data, 0xB22)
    if raw is None:
        return None
    return raw * 60 - 8 * 3600


def _parse_tz_6g(data: bytes) -> Optional[int]:
    """nano 3G/4G, Classic: gint16 at 0xB70 = the iPod's city (libgpod's table); the city's
    current UTC offset."""
    raw = _raw(data, 0xB70)
    tz_name = _CITY_TIMEZONES.get(raw) if raw is not None else None
    if tz_name is None:
        return None
    return _get_tz_offset(tz_name)


def _get_tz_offset(tz_name: str) -> Optional[int]:
    """Get UTC offset for a timezone name.

    Tries to use the system zoneinfo, falls back to Python's zoneinfo module.

    Args:
        tz_name: IANA timezone name (e.g., 'America/New_York').

    Returns:
        Offset in seconds from UTC, or None.
    """
    for name in dict.fromkeys((_LEGACY_ZONES.get(tz_name, tz_name), tz_name)):
        offset = _zone_offset(name)
        if offset is not None:
            return offset
    return None


def _zone_offset(tz_name: str) -> Optional[int]:
    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(tz_name)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        offset = now.astimezone(tz).utcoffset()
        if offset is not None:
            return int(offset.total_seconds())
    except (ImportError, KeyError, AttributeError, ValueError):
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
    path = mountpoint
    for name in ("iPod_Control", "Device", "Preferences"):  # case-insensitive, like libgpod
        found = os.path.join(path, name)
        if not os.path.exists(found):
            try:
                found = next(
                    (os.path.join(path, n) for n in os.listdir(path) if n.lower() == name.lower()),
                    found,
                )
            except OSError:
                return None
        path = found
    return read_timezone_from_prefs(path)


def device_timezone_shift(mountpoint: Optional[str]) -> int:
    """The UTC offset (seconds east) the iPod at ``mountpoint`` keeps its timestamps in.

    Like libgpod: the timezone from ``iPod_Control/Device/Preferences`` when it can be read,
    else the host's current UTC offset (``get_local_tz_offset``).
    """
    tz = read_timezone_from_device(mountpoint) if mountpoint else None
    return get_local_tz_offset() if tz is None else tz


def mac_timestamp_with_tz(mac_timestamp: int, tz_offset: int = 0) -> int:
    """Adjust a Mac timestamp by timezone offset.

    Args:
        mac_timestamp: Mac timestamp (seconds since 1904-01-01).
        tz_offset: Timezone offset in seconds.

    Returns:
        Adjusted timestamp.
    """
    return mac_timestamp + tz_offset


# The iPod's city index (Preferences 0xB70) -> timezone, from libgpod's itdb_tzinfo_data.h (the
# first entry wins where libgpod lists an index twice)
_CITY_TIMEZONES = {
    0x01: "PST8PDT",  # California
    0x02: "America/Chicago",  # Chicago
    0x03: "Pacific/Honolulu",  # Honolulu
    0x04: "America/Anchorage",  # Anchorage
    0x05: "PST8PDT",  # Cupertino
    0x06: "America/Los_Angeles",  # LosAngeles
    0x07: "PST8PDT",  # Portland
    0x08: "PST8PDT",  # SanDiego
    0x09: "PST8PDT",  # SanFrancisco
    0x0A: "PST8PDT",  # Seattle
    0x0B: "America/Vancouver",  # Vancouver
    0x0C: "MST7MDT",  # Calgary
    0x0D: "America/Denver",  # Denver
    0x0E: "America/Phoenix",  # Phoenix
    0x0F: "MST7MDT",  # SaltLakeCity
    0x10: "CST6CDT",  # Austin
    0x11: "CST6CDT",  # Dallas
    0x12: "America/Guatemala",  # Guatemala
    0x13: "America/Managua",  # Managua
    0x14: "CST6CDT",  # Memphis
    0x15: "America/Mexico_City",  # MexicoCity
    0x16: "CST6CDT",  # Minneapolis
    0x17: "America/Regina",  # Regina
    0x18: "PST8PDT",  # SanJose
    0x19: "America/El_Salvador",  # SanSalvador
    0x1A: "CST6CDT",  # StLouis
    0x1B: "America/Tegucigalpa",  # Tegucigalpa
    0x1C: "America/Winnipeg",  # Winnipeg
    0x1D: "EST5EDT",  # Atlanta
    0x1E: "America/Bogota",  # Bogota
    0x1F: "EST5EDT",  # Boston
    0x20: "EST5EDT",  # Columbus
    0x21: "America/Detroit",  # Detroit
    0x22: "America/Havana",  # Havana
    0x23: "America/Indiana/Indianapolis",  # Indianapolis
    0x24: "EST5EDT",  # KnoxVille
    0x25: "America/Lima",  # Lima
    0x26: "Europe/London",  # Manchester
    0x27: "EST5EDT",  # Miami
    0x28: "America/Montreal",  # Montreal
    0x29: "America/New_York",  # NewYork
    0x2A: "EST5EDT",  # Ottawa
    0x2B: "America/Panama",  # Panama
    0x2C: "EST5EDT",  # Philadelphia
    0x2D: "America/Port-au-Prince",  # Port-au-Prince
    0x2E: "America/Guayaquil",  # Quito
    0x2F: "America/Toronto",  # Toronto
    0x30: "EST5EDT",  # WashingtonDC
    0x31: "America/Asuncion",  # Asuncion
    0x32: "America/Caracas",  # Caracas
    0x33: "America/Guyana",  # GeorgeTown
    0x34: "America/Halifax",  # Halifax
    0x35: "America/La_Paz",  # LaPaz
    0x36: "America/Argentina/San_Juan",  # SanJuan
    0x37: "America/Santiago",  # Santiago
    0x38: "America/Santo_Domingo",  # SantoDomingo
    0x39: "America/St_Johns",  # StJohns
    0x3A: "America/Sao_Paulo",  # Brasilia
    0x3B: "America/Argentina/Buenos_Aires",  # BuenosAires
    0x3C: "America/Cayenne",  # Cayenne
    0x3D: "America/Montevideo",  # Montevideo
    0x3E: "America/Godthab",  # Nuuk
    0x3F: "America/Paramaribo",  # Paramaribo
    0x40: "America/Recife",  # Recife
    0x41: "Africa/Casablanca",  # RioDeJaneiro
    0x42: "America/Sao_Paulo",  # SaoPaulo
    0x43: "Atlantic/South_Georgia",  # Grytviken
    0x44: "Atlantic/Azores",  # Ponta Delgada
    0x45: "Europe/Dublin",  # Cork
    0x46: "Africa/Accra",  # Accra
    0x47: "Africa/Bamako",  # Bamako
    0x48: "Europe/London",  # Cardiff
    0x49: "Africa/Conakry",  # Conakry
    0x4A: "Africa/Dakar",  # Dakar
    0x4B: "Europe/Dublin",  # Dublin
    0x4C: "Europe/London",  # Edinburgh
    0x4D: "Africa/Freetown",  # Freetown
    0x4E: "Europe/Lisbon",  # Lisbon
    0x4F: "Europe/London",  # London
    0x50: "Africa/Monrovia",  # Monrovia
    0x51: "Africa/Nouakchott",  # Nouakchott
    0x52: "Africa/Ouagadougou",  # Ouagadougou
    0x53: "Atlantic/Reykjavik",  # Reykjavik
    0x54: "Africa/Algiers",  # Algiers
    0x55: "Europe/Amsterdam",  # Amsterdam
    0x56: "Africa/Bangui",  # Bangui
    0x57: "Europe/Belgrade",  # Belgrade
    0x58: "Europe/Berlin",  # Berlin
    0x59: "Europe/Brussels",  # Brussels
    0x5A: "Europe/Budapest",  # Budapest
    0x5B: "Europe/Copenhagen",  # Copenhagen
    0x5C: "Africa/Douala",  # Douala
    0x5D: "Europe/Paris",  # Geneva
    0x5E: "Africa/Kinshasa",  # Kinshasa
    0x5F: "Africa/Lagos",  # Lagos
    0x60: "Europe/Paris",  # Ljubljana
    0x61: "Africa/Luanda",  # Luanda
    0x62: "Europe/Madrid",  # Madrid
    0x63: "Europe/Berlin",  # Munich
    0x64: "Africa/Ndjamena",  # Ndjamena
    0x65: "Europe/Oslo",  # Oslo
    0x66: "Europe/Paris",  # Paris
    0x67: "Europe/Prague",  # Prague
    0x68: "Africa/Casablanca",  # Rabat
    0x69: "Europe/Rome",  # Rome
    0x6A: "Europe/Stockholm",  # Stockholm
    0x6B: "Africa/Tripoli",  # Tripoli
    0x6C: "Africa/Tunis",  # Tunis
    0x6D: "Europe/Vienna",  # Vienna
    0x6E: "Europe/Warsaw",  # Warsaw
    0x6F: "Europe/Paris",  # Zagreb
    0x70: "Europe/Zurich",  # Zurich
    0x71: "Asia/Amman",  # Amman
    0x72: "EET",  # Ankara
    0x73: "Asia/Beirut",  # Beirut
    0x74: "Europe/Bucharest",  # Bucharest
    0x75: "Africa/Cairo",  # Cairo
    0x76: "Africa/Johannesburg",  # CapeTown
    0x77: "Africa/Harare",  # Harare
    0x78: "Europe/Helsinki",  # Helsinki
    0x79: "Europe/Istanbul",  # Istanbul
    0x7A: "Asia/Jerusalem",  # Jerusalem
    0x7B: "Africa/Khartoum",  # Khartoum
    0x7C: "Europe/Kiev",  # Kiev
    0x7D: "Africa/Lusaka",  # Lusaka
    0x7E: "Africa/Maputo",  # Maputo
    0x7F: "Europe/Sofia",  # Sofia
    0x80: "Africa/Addis_Ababa",  # AddisAbaba
    0x81: "Indian/Antananarivo",  # Antananarivo
    0x82: "Africa/Asmara",  # Asmara
    0x83: "Asia/Baghdad",  # Baghdad
    0x84: "Asia/Damascus",  # Damascus
    0x85: "Africa/Dar_es_Salaam",  # DaresSalaam
    0x86: "Africa/Djibouti",  # Djibouti
    0x87: "Asia/Qatar",  # Doha
    0x88: "Africa/Kampala",  # Kampala
    0x89: "Asia/Bahrain",  # Manama
    0x8A: "Asia/Riyadh",  # Mekka
    0x8B: "Africa/Mogadishu",  # Mogadishu
    0x8C: "Europe/Moscow",  # Moscow
    0x8D: "Africa/Nairobi",  # Nairobi
    0x8E: "Asia/Riyadh",  # Riyadh
    0x8F: "Asia/Aden",  # Sanaa
    0x90: "Europe/Moscow",  # SaintPetersburg
    0x91: "Europe/Volgograd",  # Volgograd
    0x92: "Asia/Dubai",  # AbuDhabi
    0x93: "Asia/Muscat",  # Muscat
    0x94: "Indian/Mauritius",  # PortLouis
    0x96: "Asia/Karachi",  # Islamabad
    0x97: "Indian/Maldives",  # Male
    0x98: "Asia/Samarkand",  # Tachkent
    0x99: "Asia/Yekaterinburg",  # Yekaterinburg
    0x9A: "Asia/Omsk",  # Omsk
    0x9B: "Asia/Dhaka",  # Dhaka
    0x9C: "Asia/Novosibirsk",  # Novosibirsk
    0x9D: "Asia/Shanghai",  # Beijing
    0x9E: "Asia/Shanghai",  # Canton
    0x9F: "Asia/Hong_Kong",  # HongKong
    0xA0: "Asia/Kuala_Lumpur",  # KualaLumpur
    0xA1: "Asia/Manila",  # Manila
    0xA2: "Australia/Perth",  # Perth
    0xA3: "Asia/Shanghai",  # Shanghai
    0xA4: "Asia/Singapore",  # Singapore
    0xA5: "Asia/Taipei",  # Taipei
    0xA6: "Asia/Shanghai",  # TienTsin
    0xA7: "Asia/Ulaanbaatar",  # Ulaanbaatar
    0xA8: "Australia/Darwin",  # Darwin
    0xA9: "Australia/Adelaide",  # Adelaide
    0xAA: "Australia/Brisbane",  # Brisbane
    0xAB: "Australia/Melbourne",  # Canberra
    0xAC: "Pacific/Guam",  # Guam
    0xAD: "Australia/Hobart",  # Hobart
    0xAE: "Australia/Melbourne",  # Melbourne
    0xAF: "Australia/Melbourne",  # Sidney
    0xB0: "Asia/Vladivostok",  # Vladivostok
    0xB1: "Asia/Magadan",  # Magadan
    0xB2: "Pacific/Noumea",  # Noumea
    0xB3: "Asia/Anadyr",  # Anadyr
    0xB4: "Pacific/Auckland",  # Wellington
    0xB5: "America/Adak",  # Adak
    0xB6: "Pacific/Pago_Pago",  # PagoPago
    0xB7: "Asia/Tehran",  # Tehran
    0xB8: "Asia/Kabul",  # Kabul
    0xB9: "Asia/Kolkata",  # ChenaiMadras
    0xBA: "Asia/Colombo",  # Colombo
    0xBB: "Asia/Kolkata",  # Kolkata
    0xBC: "Asia/Kolkata",  # MumbaiBombay
    0xBD: "Asia/Kolkata",  # NewDelhi
    0xBE: "Asia/Kathmandu",  # Kathmandu
    0xBF: "Asia/Tokyo",  # Osaka
    0xC0: "Asia/Pyongyang",  # Pyongyang
    0xC1: "Asia/Seoul",  # Seoul
    0xC2: "Asia/Tokyo",  # Tokyo
    0xC3: "Asia/Yakutsk",  # Yakutsk
    0xC4: "Europe/Athens",  # Athens
    0xC5: "Asia/Rangoon",  # Rangoon
    0xC6: "Asia/Ho_Chi_Minh",  # HoChiMinh
    0xC7: "CST6CDT",  # DesMoines
    0xC8: "Asia/Bangkok",  # Bangkok
    0xC9: "Asia/Ho_Chi_Minh",  # Hanoi
    0xCA: "Asia/Jakarta",  # Jakarta
    0xCB: "Asia/Krasnoyarsk",  # Krasnoyarsk
    0xCC: "Asia/Kuwait",  # Kuwait
    0xCD: "Asia/Phnom_Penh",  # PhnomPenh
}

# zone names of that table that newer tz databases only keep as links (or drop)
_LEGACY_ZONES = {
    "America/Godthab": "America/Nuuk",
    "Asia/Rangoon": "Asia/Yangon",
    "Europe/Kiev": "Europe/Kyiv",
    "CST6CDT": "America/Chicago",
    "EET": "Europe/Athens",
    "EST5EDT": "America/New_York",
    "MST7MDT": "America/Denver",
    "PST8PDT": "America/Los_Angeles",
}
