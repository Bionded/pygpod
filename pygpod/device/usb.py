"""USB device detection for iPod.

Cross-platform USB detection with optional pyusb support
and platform-native fallbacks.

Known issues:
- Real serial number detection (SCSI VPD) is tested only on Linux.
  macOS and Windows implementations use OS commands (ioreg, wmic/PowerShell)
  and are untested - they may not return the correct serial on all devices.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import List, NamedTuple, Optional

from .models import APPLE_VENDOR_ID

logger = logging.getLogger(__name__)


class USBDeviceInfo(NamedTuple):
    """USB device information for an iPod."""

    vendor_id: int
    product_id: int
    serial: str
    manufacturer: str
    product: str
    firewire_guid: Optional[str]
    capacity_gb: Optional[float] = None


def detect_ipod_usb() -> List[USBDeviceInfo]:
    """Detect connected iPods via USB. Cross-platform.

    Tries pyusb first, then falls back to platform-specific methods.

    Returns:
        List of detected iPod USB devices.
    """
    logger.debug("Scanning for Apple USB devices")
    # Try pyusb first
    devices = _detect_pyusb()
    if devices:
        return devices

    # Platform-specific fallback (also used when pyusb finds nothing)
    system = platform.system()
    if system == "Linux":
        return _detect_linux_sysfs()
    elif system == "Darwin":
        return _detect_macos_ioreg()
    elif system == "Windows":
        return _detect_windows_wmi()

    return []


def _detect_pyusb() -> Optional[List[USBDeviceInfo]]:
    """Try detection via pyusb."""
    try:
        import usb.core  # type: ignore
    except ImportError:
        logger.debug("pyusb not installed, skipping USB detection")
        return None

    results = []
    try:
        devices = usb.core.find(find_all=True, idVendor=APPLE_VENDOR_ID)
        capacity = None
        if platform.system() == "Linux":
            capacity = _get_ipod_capacity_gb()

        for dev in devices:
            usb_serial = ""
            manufacturer = ""
            product_name = ""
            try:
                usb_serial = usb.util.get_string(dev, dev.iSerialNumber) or ""
                manufacturer = usb.util.get_string(dev, dev.iManufacturer) or ""
                product_name = usb.util.get_string(dev, dev.iProduct) or ""
            except Exception:
                logger.debug(
                    "Failed to read USB descriptors for device %04x:%04x",
                    dev.idVendor,
                    dev.idProduct,
                    exc_info=True,
                )

            guid = _guid_from_serial(usb_serial)

            # USB serial on iPod Classics is the FireWire GUID, not the
            # real serial. Try SCSI VPD to get the actual device serial.
            real_serial = read_ipod_serial(usb_serial) or ""

            results.append(
                USBDeviceInfo(
                    vendor_id=dev.idVendor,
                    product_id=dev.idProduct,
                    serial=real_serial or usb_serial,
                    manufacturer=manufacturer,
                    product=product_name,
                    firewire_guid=guid,
                    capacity_gb=capacity,
                )
            )
    except Exception as e:
        logger.debug("PyUSB detection skipped (no backend): %s", e)
        return None

    return results


def _detect_linux_sysfs() -> List[USBDeviceInfo]:
    """Detect iPods via Linux sysfs."""
    import pathlib

    results = []
    sysfs = pathlib.Path("/sys/bus/usb/devices")
    if not sysfs.exists():
        return results

    capacity = _get_ipod_capacity_gb()

    for dev_path in sysfs.iterdir():
        vendor_file = dev_path / "idVendor"
        if not vendor_file.exists():
            continue
        try:
            vendor = int(vendor_file.read_text().strip(), 16)
            if vendor != APPLE_VENDOR_ID:
                continue
            product = int((dev_path / "idProduct").read_text().strip(), 16)
            serial_path = dev_path / "serial"
            usb_serial = serial_path.read_text().strip() if serial_path.exists() else ""
            mfr_path = dev_path / "manufacturer"
            manufacturer = mfr_path.read_text().strip() if mfr_path.exists() else ""
            prod_path = dev_path / "product"
            product_name = prod_path.read_text().strip() if prod_path.exists() else ""

            guid = _guid_from_serial(usb_serial)
            real_serial = read_ipod_serial(usb_serial) or ""

            results.append(
                USBDeviceInfo(
                    vendor_id=vendor,
                    product_id=product,
                    serial=real_serial or usb_serial,
                    manufacturer=manufacturer,
                    product=product_name,
                    firewire_guid=guid,
                    capacity_gb=capacity,
                )
            )
        except (ValueError, OSError):
            logger.debug("Failed to read sysfs USB device %s", dev_path, exc_info=True)
            continue

    return results


def _detect_macos_ioreg() -> List[USBDeviceInfo]:
    """Detect iPods via macOS ioreg."""
    results = []
    try:
        output = subprocess.check_output(
            ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"], text=True, timeout=10
        )
        # Basic parsing of system_profiler output
        # This is a simplified parser
        current_vendor = 0
        current_product = 0
        current_serial = ""
        in_apple = False

        for line in output.splitlines():
            line = line.strip()
            if "Vendor ID:" in line and "0x05ac" in line.lower():
                in_apple = True
                current_vendor = APPLE_VENDOR_ID
            elif "Product ID:" in line and in_apple:
                try:
                    pid = line.split(":")[-1].strip()
                    current_product = int(pid, 0)
                except ValueError:
                    logger.debug("Cannot parse macOS USB product ID: %s", line)
            elif "Serial Number:" in line and in_apple:
                current_serial = line.split(":")[-1].strip()
                guid = _guid_from_serial(current_serial)
                results.append(
                    USBDeviceInfo(
                        vendor_id=current_vendor,
                        product_id=current_product,
                        serial=current_serial,
                        manufacturer="Apple Inc.",
                        product="iPod",
                        firewire_guid=guid,
                    )
                )
                in_apple = False
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.debug("macOS system_profiler USB detection failed", exc_info=True)

    return results


def _detect_windows_wmi() -> List[USBDeviceInfo]:
    """Detect iPods via Windows PowerShell/PnP.

    Queries PnP devices for Apple USB devices (VID_05AC) and reads the
    real device serial from Get-Disk.  No external Python modules needed.
    """
    import re

    results = []

    # Step 1: Find Apple USB root devices via Get-PnpDevice.
    # We look for USBDevice class entries with VID_05AC - these are the
    # root composite devices whose InstanceId contains the FireWire GUID.
    # Filter: Status OK = currently connected.
    pnp_cmd = (
        "Get-PnpDevice -Class USBDevice -Status OK -ErrorAction SilentlyContinue"
        " | Where-Object { $_.InstanceId -like '*VID_05AC*' }"
        " | Select-Object -ExpandProperty InstanceId"
    )
    instance_ids = _run_powershell(pnp_cmd)
    if not instance_ids:
        # Broader fallback: any USB class, root devices only (no MI_)
        pnp_cmd2 = (
            "Get-PnpDevice -Class USB -Status OK -ErrorAction SilentlyContinue"
            " | Where-Object { $_.InstanceId -like '*VID_05AC*'"
            " -and $_.InstanceId -notlike '*MI_*' }"
            " | Select-Object -ExpandProperty InstanceId"
        )
        instance_ids = _run_powershell(pnp_cmd2)

    if not instance_ids:
        return results

    # Step 2: Get real serial from Get-Disk (Apple iPod disk)
    real_serial = _read_serial_windows() or ""

    # Step 3: Parse each instance ID
    # Format: USB\VID_05AC&PID_XXXX\<usb_serial>
    pid_re = re.compile(r"PID_([0-9A-Fa-f]{4})", re.IGNORECASE)

    for line in instance_ids:
        line = line.strip()
        if not line:
            continue

        # Extract product ID
        pid_match = pid_re.search(line)
        product_id = int(pid_match.group(1), 16) if pid_match else 0

        # Extract USB serial (last segment after \)
        parts = line.split("\\")
        usb_serial = parts[-1] if len(parts) >= 3 else ""

        guid = _guid_from_serial(usb_serial)

        results.append(
            USBDeviceInfo(
                vendor_id=APPLE_VENDOR_ID,
                product_id=product_id,
                serial=real_serial or usb_serial,
                manufacturer="Apple Inc.",
                product="iPod",
                firewire_guid=guid,
            )
        )

    return results


def _run_powershell(command: str) -> List[str]:
    """Run a PowerShell command and return output lines."""
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", command],
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
        return [ln for ln in output.splitlines() if ln.strip()]
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.debug("PowerShell command failed: %s", command, exc_info=True)
        return []


def _read_scsi_serial(block_device: str) -> Optional[str]:
    """Read the real device serial from SCSI VPD page 0x80.

    The USB serial descriptor on iPods contains the FireWire GUID,
    not the actual device serial number. The real serial is only
    available via SCSI INQUIRY VPD page 0x80 (Unit Serial Number).

    Requires read access to the block device (typically needs root).

    Args:
        block_device: Path to block device (e.g., '/dev/sdb').

    Returns:
        Device serial string, or None if unavailable.
    """
    try:
        import ctypes
        import fcntl
    except ImportError:
        return None

    SG_IO = 0x2285

    class SgIoHdr(ctypes.Structure):
        _fields_ = [
            ("interface_id", ctypes.c_int),
            ("dxfer_direction", ctypes.c_int),
            ("cmd_len", ctypes.c_ubyte),
            ("mx_sb_len", ctypes.c_ubyte),
            ("iovec_count", ctypes.c_ushort),
            ("dxfer_len", ctypes.c_uint),
            ("dxferp", ctypes.c_void_p),
            ("cmdp", ctypes.c_void_p),
            ("sbp", ctypes.c_void_p),
            ("timeout", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("pack_id", ctypes.c_int),
            ("usr_ptr", ctypes.c_void_p),
            ("status", ctypes.c_ubyte),
            ("masked_status", ctypes.c_ubyte),
            ("msg_status", ctypes.c_ubyte),
            ("sb_len_wr", ctypes.c_ubyte),
            ("host_status", ctypes.c_ushort),
            ("driver_status", ctypes.c_ushort),
            ("resid", ctypes.c_int),
            ("duration", ctypes.c_uint),
            ("info", ctypes.c_uint),
        ]

    try:
        with open(block_device, "rb") as f:
            cdb = (ctypes.c_ubyte * 6)(0x12, 0x01, 0x80, 0, 0xFF, 0)
            data = (ctypes.c_ubyte * 255)()
            sense = (ctypes.c_ubyte * 32)()

            hdr = SgIoHdr()
            hdr.interface_id = ord("S")
            hdr.dxfer_direction = -3  # SG_DXFER_FROM_DEV
            hdr.cmd_len = 6
            hdr.mx_sb_len = 32
            hdr.dxfer_len = 255
            hdr.dxferp = ctypes.addressof(data)
            hdr.cmdp = ctypes.addressof(cdb)
            hdr.sbp = ctypes.addressof(sense)
            hdr.timeout = 5000

            fcntl.ioctl(f.fileno(), SG_IO, hdr)

            if hdr.status != 0:
                return None

            result = bytes(data)
            page_len = result[3]
            serial = result[4 : 4 + page_len].decode("ascii", errors="replace")
            serial = serial.strip(" \t\r\n\x00")
            return serial if serial else None
    except (OSError, IOError):
        logger.debug("Failed to read SCSI serial from %s", block_device, exc_info=True)
        return None


def _find_ipod_block_device() -> Optional[str]:
    """Find the block device path for a connected iPod on Linux.

    Scans /sys/block/ for USB mass storage devices with Apple vendor ID.

    Returns:
        Block device path (e.g., '/dev/sdb'), or None.
    """
    devices = _find_all_ipod_block_devices()
    return devices[0][0] if devices else None


def _find_all_ipod_block_devices() -> List[tuple]:
    """Find all iPod block devices on Linux with their USB serial.

    Returns:
        List of (block_device_path, usb_serial) tuples.
    """
    import pathlib

    results = []
    for block in pathlib.Path("/sys/block").iterdir():
        device_link = block / "device"
        if not device_link.exists():
            continue
        # Walk up to find the USB device with idVendor
        real = device_link.resolve()
        for parent in [real] + list(real.parents):
            vendor_file = parent / "idVendor"
            if vendor_file.exists():
                try:
                    vendor = vendor_file.read_text().strip()
                    if vendor == "05ac":
                        serial_file = parent / "serial"
                        usb_serial = ""
                        if serial_file.exists():
                            usb_serial = serial_file.read_text().strip()
                        results.append((f"/dev/{block.name}", usb_serial))
                except OSError:
                    pass
                break
    return results


def _get_ipod_capacity_gb() -> Optional[float]:
    """Read the disk capacity of a connected iPod from Linux sysfs.

    Reads /sys/block/<dev>/size which is world-readable (no root needed).
    Useful for narrowing down the iPod model when serial is unavailable.

    Returns:
        Capacity in GB (base-10, as used in Apple marketing), or None.
    """
    import pathlib

    for block in pathlib.Path("/sys/block").iterdir():
        device_link = block / "device"
        if not device_link.exists():
            continue
        real = device_link.resolve()
        for parent in [real] + list(real.parents):
            vendor_file = parent / "idVendor"
            if vendor_file.exists():
                try:
                    vendor = vendor_file.read_text().strip()
                    if vendor == "05ac":
                        size_file = block / "size"
                        if size_file.exists():
                            sectors = int(size_file.read_text().strip())
                            gb = round(sectors * 512 / (1000**3), 1)
                            logger.debug("iPod sysfs capacity: %.1f GB", gb)
                            return gb
                except (OSError, ValueError):
                    pass
                break
    return None


def read_ipod_serial(usb_serial: Optional[str] = None) -> Optional[str]:
    """Read the real serial number from a connected iPod.

    The USB descriptor serial on iPod Classics is the FireWire GUID,
    not the actual device serial. This function reads the real serial
    via platform-specific methods:
      - Linux: SCSI INQUIRY VPD page 0x80 via SG_IO ioctl (tested, works)
      - macOS: diskutil info or ioreg (untested)
      - Windows: wmic or PowerShell Get-Disk (untested)

    Args:
        usb_serial: USB descriptor serial to match a specific device.
            When multiple iPods are connected, this is used to find the
            correct block device. If None, returns the first iPod found.

    Returns:
        Device serial string, or None if unavailable.
    """
    system = platform.system()
    if system == "Linux":
        return _read_serial_linux(usb_serial)
    elif system == "Darwin":
        return _read_serial_macos()
    elif system == "Windows":
        return _read_serial_windows()
    return None


def _read_serial_linux(usb_serial: Optional[str] = None) -> Optional[str]:
    """Read iPod serial on Linux via SCSI VPD."""
    all_devices = _find_all_ipod_block_devices()
    if usb_serial:
        # Match specific device by USB serial or GUID prefix
        needle = usb_serial.upper()
        for block_dev, dev_usb_serial in all_devices:
            haystack = dev_usb_serial.upper()
            if haystack == needle or haystack.startswith(needle) or needle.startswith(haystack):
                serial = _read_scsi_serial(block_dev)
                if serial:
                    logger.info("Read SCSI serial from %s: %s", block_dev, serial)
                    return serial
    # Fallback: return first iPod found
    if all_devices:
        serial = _read_scsi_serial(all_devices[0][0])
        if serial:
            logger.info("Read SCSI serial from %s: %s", all_devices[0][0], serial)
            return serial
    return None


def _read_serial_macos() -> Optional[str]:
    """Read iPod serial on macOS via diskutil or system_profiler."""
    # Try diskutil first - it reads the SCSI serial for USB mass storage
    try:
        output = subprocess.check_output(
            ["diskutil", "list", "-plist", "external"], text=True, timeout=10
        )
        import plistlib

        plist = plistlib.loads(output.encode())
        for disk_name in plist.get("AllDisksAndPartitions", []):
            dev = disk_name.get("DeviceIdentifier", "")
            if not dev:
                continue
            try:
                info_out = subprocess.check_output(
                    ["diskutil", "info", "-plist", dev], text=True, timeout=10
                )
                info = plistlib.loads(info_out.encode())
                vendor = info.get("MediaName", "") or info.get("IORegistryEntryName", "")
                if "iPod" in vendor or "Apple" in vendor:
                    # Get serial from IOKit - try system_profiler
                    serial = _macos_ioreg_serial()
                    if serial:
                        return serial
            except (subprocess.SubprocessError, Exception):
                continue
    except (subprocess.SubprocessError, FileNotFoundError, Exception):
        logger.debug("macOS diskutil detection failed", exc_info=True)

    # Fallback: system_profiler
    return _macos_ioreg_serial()


def _macos_ioreg_serial() -> Optional[str]:
    """Read iPod serial from macOS ioreg (IOKit registry)."""
    try:
        output = subprocess.check_output(
            ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l"], text=True, timeout=10
        )
        in_apple = False
        for line in output.splitlines():
            stripped = line.strip()
            if '"idVendor"' in stripped and "1452" in stripped:  # 0x05AC = 1452
                in_apple = True
            elif in_apple and '"USB Serial Number"' in stripped:
                # This gives FireWire GUID - skip it
                pass
            elif in_apple and '"Serial Number"' in stripped:
                val = stripped.split("=")[-1].strip().strip('"')
                # Verify it's not just hex (GUID)
                if val and not all(c in "0123456789ABCDEFabcdef" for c in val):
                    logger.info("macOS ioreg serial: %s", val)
                    return val
                in_apple = False
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.debug("macOS ioreg serial detection failed", exc_info=True)
    return None


def _read_serial_windows() -> Optional[str]:
    """Read iPod serial on Windows via PowerShell Get-Disk.

    The disk serial for Apple iPod USB mass storage contains the real
    device serial (not the FireWire GUID).
    """
    # PowerShell Get-Disk - searches for 'Apple' or 'iPod' in FriendlyName
    ps_cmd = (
        "Get-Disk | Where-Object {$_.BusType -eq 'USB'"
        " -and ($_.FriendlyName -like '*iPod*' -or $_.FriendlyName -like '*Apple*')}"
        " | Select-Object -ExpandProperty SerialNumber"
    )
    lines = _run_powershell(ps_cmd)
    for line in lines:
        serial = line.strip()
        if serial and not all(c in "0123456789ABCDEFabcdef" for c in serial):
            logger.info("Windows Get-Disk serial: %s", serial)
            return serial

    # Fallback: wmic (older Windows)
    try:
        output = subprocess.check_output(
            [
                "wmic",
                "diskdrive",
                "where",
                "InterfaceType='USB' and (Model like '%iPod%' or Model like '%Apple%')",
                "get",
                "SerialNumber",
                "/value",
            ],
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
        for wline in output.splitlines():
            if wline.startswith("SerialNumber="):
                serial = wline.split("=", 1)[1].strip()
                if serial and not all(c in "0123456789ABCDEFabcdef" for c in serial):
                    logger.info("Windows wmic serial: %s", serial)
                    return serial
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.debug("Windows wmic serial detection failed", exc_info=True)

    return None


def _guid_from_serial(serial: str) -> Optional[str]:
    """Derive FirewireGuid from USB serial number.

    On many iPod models, the first 16 hex characters of the USB serial
    number correspond to the FirewireGuid.
    """
    if not serial:
        return None
    # Remove any non-hex characters
    hex_chars = "".join(c for c in serial if c in "0123456789ABCDEFabcdef")
    if len(hex_chars) >= 16:
        return hex_chars[:16].upper()
    return None
