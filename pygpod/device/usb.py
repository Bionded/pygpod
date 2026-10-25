"""USB device detection for iPod.

Cross-platform USB detection with optional pyusb support
and platform-native fallbacks.

Known issues:
- Real serial number detection (SCSI VPD) is tested only on Linux.
  macOS and Windows implementations use OS commands (ioreg, wmic/PowerShell)
  and are untested - they may not return the correct serial on all devices.
"""

from __future__ import annotations

import json
import logging
import ntpath
import os
import pathlib
import platform
import plistlib
import subprocess
from typing import Any, Iterator, List, NamedTuple, Optional, Tuple

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


def detect_ipod_usb_for_mountpoint(mountpoint: str) -> Optional[USBDeviceInfo]:
    """Find the USB iPod that backs a mountpoint.

    The mountpoint is first mapped to its USB device (Linux: sysfs; macOS: diskutil and
    system_profiler; Windows: Get-Partition/Get-Disk). When that is not possible, the bus
    scan of :func:`detect_ipod_usb` is used only if exactly one iPod is connected: with
    several iPods there is no way to tell which one is mounted here, so nothing is guessed
    (a wrong FirewireGuid would sign the database with another iPod's key).

    Args:
        mountpoint: Path to the iPod mount point.

    Returns:
        USB device information of the iPod at the mountpoint, or None.
    """
    mapped = None
    try:
        mapped = usb_device_for_mountpoint(mountpoint)
    except Exception:
        logger.debug("Mapping %s to a USB device failed", mountpoint, exc_info=True)

    if mapped is not None and mapped.product_id:
        return mapped

    try:
        devices = detect_ipod_usb()
    except Exception:
        logger.debug("USB device detection failed", exc_info=True)
        devices = []

    if mapped is not None:
        # The mapping knows the device but not its product id (Windows): take it from the bus
        for dev in devices:
            if mapped.firewire_guid and dev.firewire_guid == mapped.firewire_guid:
                return mapped._replace(product_id=dev.product_id)
        return mapped

    if len(devices) == 1:
        return devices[0]
    if len(devices) > 1:
        logger.warning(
            "%d iPods are connected and %s cannot be matched to one of them; "
            "not using USB information for it",
            len(devices),
            mountpoint,
        )
    return None


def usb_device_for_mountpoint(mountpoint: str) -> Optional[USBDeviceInfo]:
    """Map a mountpoint to the Apple USB device backing it (no bus-scan guessing).

    Returns:
        USB device information, or None when the mountpoint is not on an Apple USB device
        or the platform lookup is not possible.
    """
    system = platform.system()
    if system == "Linux":
        devnum = _block_devnum(mountpoint)
        return _usb_device_for_devnum_linux(*devnum) if devnum else None
    elif system == "Darwin":
        return _usb_device_for_mountpoint_macos(mountpoint)
    elif system == "Windows":
        return _usb_device_for_mountpoint_windows(mountpoint)
    return None


# sysfs root, a module attribute so tests can point it at a fake tree
_SYSFS_ROOT = "/sys"


def _block_devnum(mountpoint: str) -> Optional[Tuple[int, int]]:
    """(major, minor) of the block device holding mountpoint."""
    try:
        st_dev = os.stat(mountpoint).st_dev
    except OSError:
        return None
    return os.major(st_dev), os.minor(st_dev)


def _usb_device_for_devnum_linux(major: int, minor: int) -> Optional[USBDeviceInfo]:
    """Walk sysfs from a block device number up to its USB device (Linux)."""
    link = pathlib.Path(_SYSFS_ROOT) / "dev" / "block" / f"{major}:{minor}"
    if not link.exists():
        return None
    block = link.resolve()
    # A partition (sdb2) lives inside its disk's directory (sdb)
    disk = block.parent if (block / "partition").exists() else block
    for parent in [block] + list(block.parents):
        vendor_file = parent / "idVendor"
        if not vendor_file.exists():
            continue
        try:
            if int(vendor_file.read_text().strip(), 16) != APPLE_VENDOR_ID:
                return None
            product = int((parent / "idProduct").read_text().strip(), 16)
            usb_serial = _read_sysfs_attr(parent / "serial")
            capacity = None
            size = _read_sysfs_attr(disk / "size")
            if size:
                capacity = round(int(size) * 512 / (1000**3), 1)
            real_serial = _read_scsi_serial(f"/dev/{disk.name}") or ""
        except (ValueError, OSError):
            logger.debug("Failed to read sysfs USB device %s", parent, exc_info=True)
            return None
        logger.debug("Block device %d:%d is backed by USB device %s", major, minor, parent)
        return USBDeviceInfo(
            vendor_id=APPLE_VENDOR_ID,
            product_id=product,
            serial=real_serial or usb_serial,
            manufacturer=_read_sysfs_attr(parent / "manufacturer"),
            product=_read_sysfs_attr(parent / "product"),
            firewire_guid=_guid_from_serial(usb_serial),
            capacity_gb=capacity,
        )
    return None


def _read_sysfs_attr(path: pathlib.Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _usb_device_for_mountpoint_macos(mountpoint: str) -> Optional[USBDeviceInfo]:
    """Map a mountpoint to its USB iPod on macOS (diskutil + system_profiler JSON)."""
    try:
        out = subprocess.check_output(["diskutil", "info", "-plist", mountpoint], timeout=10)
        disk = plistlib.loads(out)
    except (subprocess.SubprocessError, OSError, ValueError, plistlib.InvalidFileException):
        logger.debug("diskutil info failed for %s", mountpoint, exc_info=True)
        return None
    bsd_name = disk.get("ParentWholeDisk") or disk.get("DeviceIdentifier")
    if not bsd_name:
        return None

    # SPUSBHostDataType replaces SPUSBDataType on newer macOS releases
    for data_type in ("SPUSBDataType", "SPUSBHostDataType"):
        try:
            out = subprocess.check_output(
                ["system_profiler", data_type, "-json"], timeout=30, stderr=subprocess.DEVNULL
            )
            tree = json.loads(out)
        except (subprocess.SubprocessError, OSError, ValueError):
            logger.debug("system_profiler %s failed", data_type, exc_info=True)
            continue
        for item in _walk_profiler_items(tree):
            media = item.get("Media") or []
            if not any(isinstance(m, dict) and m.get("bsd_name") == bsd_name for m in media):
                continue
            vendor = _profiler_int(item.get("vendor_id") or item.get("USBDeviceKeyVendorID"))
            if vendor != APPLE_VENDOR_ID:
                return None
            serial = str(item.get("serial_num") or item.get("USBDeviceKeySerialNumber") or "")
            return USBDeviceInfo(
                vendor_id=APPLE_VENDOR_ID,
                product_id=_profiler_int(
                    item.get("product_id") or item.get("USBDeviceKeyProductID")
                ),
                serial=serial,
                manufacturer="Apple Inc.",
                product=str(item.get("_name") or "iPod"),
                firewire_guid=_guid_from_serial(serial),
            )
    return None


def _walk_profiler_items(node: Any) -> Iterator[dict]:
    """Every dict in a system_profiler JSON tree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_profiler_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_profiler_items(value)


def _profiler_int(value: Any) -> int:
    """Parse '0x05ac  (Apple Inc.)', '0x1261' or 1452."""
    if isinstance(value, int):
        return value
    try:
        return int(str(value).split()[0], 0)
    except (ValueError, IndexError):
        return 0


def _usb_device_for_mountpoint_windows(mountpoint: str) -> Optional[USBDeviceInfo]:
    """Map a drive letter to its USB iPod on Windows (Get-Partition | Get-Disk).

    The disk Path holds the USBSTOR instance id, whose serial part is the iPod's USB
    serial (= FirewireGuid); the product id is not part of it (filled from the bus scan).
    """
    drive, _ = ntpath.splitdrive(mountpoint)
    letter = drive[:1].upper()
    if not (letter.isascii() and letter.isalpha()):
        return None
    lines = _run_powershell(
        f"Get-Partition -DriveLetter {letter} -ErrorAction Stop | Get-Disk"
        " | Select-Object Path,SerialNumber,Size | ConvertTo-Json -Compress"
    )
    try:
        disk = json.loads("".join(lines))
    except ValueError:
        return None
    if isinstance(disk, list):
        disk = disk[0] if disk else {}
    if not isinstance(disk, dict):
        return None
    usb_serial = _usbstor_serial(str(disk.get("Path") or ""))
    if usb_serial is None:
        return None
    real_serial = str(disk.get("SerialNumber") or "").strip()
    if all(c in "0123456789ABCDEFabcdef" for c in real_serial):
        real_serial = ""  # a GUID, not the iPod serial
    size = disk.get("Size")
    return USBDeviceInfo(
        vendor_id=APPLE_VENDOR_ID,
        product_id=0,
        serial=real_serial or usb_serial,
        manufacturer="Apple Inc.",
        product="iPod",
        firewire_guid=_guid_from_serial(usb_serial),
        capacity_gb=round(size / 1000**3, 1) if isinstance(size, int) and size > 0 else None,
    )


def _usbstor_serial(path: str) -> Optional[str]:
    r"""USB serial from an Apple USBSTOR disk path.

    E.g. ``\\?\usbstor#disk&ven_apple&prod_ipod&rev_1.62#000a27001234567800000000&0#{...}``.
    """
    parts = path.split("#")
    if len(parts) < 3 or not parts[0].lower().endswith("usbstor"):
        return None
    if "ven_apple" not in parts[1].lower():
        return None
    serial = parts[2].split("&")[0]
    return serial.upper() or None


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
        linux = platform.system() == "Linux"

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
            capacity = _get_ipod_capacity_gb(usb_serial or None) if linux else None

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
    sysfs = pathlib.Path(_SYSFS_ROOT) / "bus" / "usb" / "devices"
    if not sysfs.exists():
        return results

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
            capacity = _get_ipod_capacity_gb(usb_serial or None)

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
            ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"],
            encoding="utf-8",
            errors="replace",
            timeout=10,
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

    # Step 2: real serial per iPod from Get-Disk (its Path holds the USB serial); with one
    # iPod and no match, the Apple disk's serial as before
    disk_serials = _windows_disk_serials()
    fallback_serial = ""
    if not disk_serials and len(instance_ids) == 1:
        fallback_serial = _read_serial_windows() or ""

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
        real_serial = disk_serials.get(usb_serial.upper(), "") or fallback_serial

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


def _windows_disk_serials() -> dict:
    """USB serial (upper case) -> disk serial number of every Apple USB disk (Get-Disk)."""
    lines = _run_powershell(
        "Get-Disk | Where-Object {$_.BusType -eq 'USB'} | Select-Object Path,SerialNumber"
        " | ConvertTo-Json -Compress"
    )
    try:
        disks = json.loads("".join(lines)) if lines else []
    except ValueError:
        return {}
    if isinstance(disks, dict):
        disks = [disks]
    result = {}
    for disk in disks if isinstance(disks, list) else []:
        if not isinstance(disk, dict):
            continue
        usb_serial = _usbstor_serial(str(disk.get("Path") or ""))
        serial = str(disk.get("SerialNumber") or "").strip()
        if usb_serial and serial and not all(c in "0123456789ABCDEFabcdef" for c in serial):
            result[usb_serial] = serial
    return result


def _run_powershell(command: str) -> List[str]:
    """Run a PowerShell command and return output lines."""
    try:
        output = subprocess.check_output(
            # UTF-8 output instead of the console code page
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + command,
            ],
            encoding="utf-8",
            errors="replace",
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
    return [(dev, serial) for dev, serial, _ in _ipod_block_devices()]


def _ipod_block_devices() -> List[Tuple[str, str, Optional[int]]]:
    """(block device path, USB serial, size in 512-byte sectors) of every Apple USB disk."""
    results: List[Tuple[str, str, Optional[int]]] = []
    root = pathlib.Path(_SYSFS_ROOT) / "block"
    if not root.is_dir():
        return results
    for block in sorted(root.iterdir()):
        device_link = block / "device"
        if not device_link.exists():
            continue
        # Walk up to find the USB device with idVendor
        real = device_link.resolve()
        for parent in [real] + list(real.parents):
            vendor_file = parent / "idVendor"
            if vendor_file.exists():
                try:
                    if vendor_file.read_text().strip() == "05ac":
                        size = _read_sysfs_attr(block / "size")
                        results.append(
                            (
                                f"/dev/{block.name}",
                                _read_sysfs_attr(parent / "serial"),
                                int(size) if size.isdigit() else None,
                            )
                        )
                except OSError:
                    pass
                break
    return results


def _get_ipod_capacity_gb(usb_serial: Optional[str] = None) -> Optional[float]:
    """Read the disk capacity of a connected iPod from Linux sysfs.

    Reads /sys/block/<dev>/size which is world-readable (no root needed).
    Useful for narrowing down the iPod model when serial is unavailable.

    Args:
        usb_serial: USB serial of the iPod; with several iPods connected only that one's
            disk counts (None: the first iPod, or None when its disk is not found).

    Returns:
        Capacity in GB (base-10, as used in Apple marketing), or None.
    """
    for _, serial, sectors in _ipod_block_devices():
        if usb_serial and serial.upper() != usb_serial.upper():
            continue
        if sectors is None:
            return None
        gb = round(sectors * 512 / (1000**3), 1)
        logger.debug("iPod sysfs capacity: %.1f GB", gb)
        return gb
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
    # Fallback: the only iPod (with several, another iPod's serial would be returned)
    if len(all_devices) == 1:
        serial = _read_scsi_serial(all_devices[0][0])
        if serial:
            logger.info("Read SCSI serial from %s: %s", all_devices[0][0], serial)
            return serial
    return None


def _read_serial_macos() -> Optional[str]:
    """Read iPod serial on macOS via diskutil or system_profiler."""
    # Try diskutil first - it reads the SCSI serial for USB mass storage
    try:
        output = subprocess.check_output(["diskutil", "list", "-plist", "external"], timeout=10)
        import plistlib

        plist = plistlib.loads(output)
        for disk_name in plist.get("AllDisksAndPartitions", []):
            dev = disk_name.get("DeviceIdentifier", "")
            if not dev:
                continue
            try:
                info_out = subprocess.check_output(["diskutil", "info", "-plist", dev], timeout=10)
                info = plistlib.loads(info_out)
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
            ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l"],
            encoding="utf-8",
            errors="replace",
            timeout=10,
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
            errors="replace",  # console code page
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
