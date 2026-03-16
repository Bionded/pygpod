"""SysInfo and SysInfoExtended parsing.

Reads iPod device information from the SysInfo files in iPod_Control/Device/.
"""

from __future__ import annotations

import logging
import pathlib
import plistlib
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SysInfo:
    """Parsed iPod SysInfo data.

    Combines data from both SysInfo (plain text) and SysInfoExtended (plist).
    """

    def __init__(self) -> None:
        self.raw: Dict[str, str] = {}
        self.extended: Dict[str, Any] = {}
        # Ordered list of (key, raw_line) for round-trip fidelity
        self._raw_lines: list[tuple[str, str]] = []

    @property
    def model_num_str(self) -> Optional[str]:
        """Model number string (e.g., 'xB029')."""
        return self.raw.get("ModelNumStr")

    @property
    def model_number(self) -> Optional[str]:
        """Cleaned model number (e.g., 'B029')."""
        mns = self.model_num_str
        if mns:
            # Strip leading 'x' or 'M' prefix
            if mns and mns[0].lower() in ("x", "m"):
                return mns[1:]
            return mns
        # Try from SysInfoExtended serial number
        serial = self.serial_number
        if serial and len(serial) >= 3:
            return serial[-3:]
        return None

    @property
    def firewire_guid(self) -> Optional[str]:
        """FirewireGuid for hash computation.

        Returns the hex string without '0x' prefix, uppercase.
        """
        # Try SysInfoExtended first
        guid = self.extended.get("FireWireGUID")
        if guid:
            if isinstance(guid, str):
                return guid.replace("0x", "").replace("0X", "").upper()
            return format(guid, "016X")

        # Fall back to SysInfo
        guid = self.raw.get("FirewireGuid")
        if guid:
            return guid.replace("0x", "").replace("0X", "").upper()
        return None

    @property
    def serial_number(self) -> Optional[str]:
        """Device serial number (from SysInfoExtended or USB).

        Used internally for model detection fallback only.
        The primary device identifier is firewire_guid.
        """
        serial = self.extended.get("SerialNumber")
        if serial:
            return serial
        return self.raw.get("SerialNumber")

    @property
    def firmware_version(self) -> Optional[str]:
        """Visible firmware version."""
        return self.extended.get("VisibleBuildID") or self.raw.get("visibleBuildID")

    @property
    def product_type(self) -> Optional[str]:
        """Product type string."""
        return self.extended.get("ProductType")

    @property
    def family_id(self) -> Optional[int]:
        """Device family ID."""
        return self.extended.get("FamilyID")


def parse_sysinfo(path: str) -> SysInfo:
    """Parse a SysInfo plain text file.

    Format is simple key-value: 'Key: Value' per line.
    Preserves original lines for round-trip write fidelity.

    Args:
        path: Path to the SysInfo file.

    Returns:
        SysInfo object with parsed data.
    """
    logger.debug("Parsing SysInfo: %s", path)
    info = SysInfo()
    p = pathlib.Path(path)
    if not p.exists():
        return info

    text = p.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if key:
                info.raw[key] = val
                info._raw_lines.append((key, stripped))

    return info


def parse_sysinfo_extended(path: str) -> Dict[str, Any]:
    """Parse a SysInfoExtended plist file.

    Args:
        path: Path to the SysInfoExtended file.

    Returns:
        Dictionary of parsed plist data.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return {}

    try:
        with open(p, "rb") as f:
            return plistlib.load(f)
    except Exception:
        logger.warning("Failed to parse SysInfoExtended plist: %s", path, exc_info=True)
        return {}


def read_sysinfo(mountpoint: str) -> SysInfo:
    """Read SysInfo from an iPod mount point.

    Reads both SysInfo and SysInfoExtended if available.

    Args:
        mountpoint: Path to the iPod mount point.

    Returns:
        SysInfo object with combined data.
    """
    mp = pathlib.Path(mountpoint)

    # Case-insensitive search for Device directory
    device_dir = _find_case_insensitive(mp / "iPod_Control", "Device")
    if device_dir is None:
        device_dir = mp / "iPod_Control" / "Device"

    sysinfo_path = device_dir / "SysInfo"
    info = parse_sysinfo(str(sysinfo_path))

    # Try SysInfoExtended
    ext_path = device_dir / "SysInfoExtended"
    if ext_path.exists():
        info.extended = parse_sysinfo_extended(str(ext_path))
        # Copy FireWireGUID to raw if not present
        if "FireWireGUID" in info.extended and "FirewireGuid" not in info.raw:
            guid = info.extended["FireWireGUID"]
            if isinstance(guid, str):
                info.raw["FirewireGuid"] = guid
            else:
                info.raw["FirewireGuid"] = format(guid, "016X")

    return info


def _find_case_insensitive(parent: pathlib.Path, name: str) -> Optional[pathlib.Path]:
    """Find a directory with case-insensitive name matching."""
    if not parent.exists():
        return None
    name_lower = name.lower()
    for child in parent.iterdir():
        if child.name.lower() == name_lower and child.is_dir():
            return child
    return None
