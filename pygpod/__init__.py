"""pygpod - Pure Python iPod database library.

A complete Python rewrite of libgpod for managing iPods that mount as
USB mass storage devices.

Basic usage:
    import pygpod

    # Open existing iPod
    db = pygpod.Database("/mnt/ipod")

    # List tracks
    for track in db.tracks:
        print(f"{track.artist} - {track.title}")

    # Add a track
    db.add_track("/path/to/song.mp3")

    # Save
    db.save()
"""

from __future__ import annotations

import logging

__version__ = "0.0.5"

logger = logging.getLogger(__name__)

from .db.photodb import Photo, PhotoAlbum, PhotoDB
from .db.playcounts import read_otg_playlists, read_play_counts
from .device.device import Device
from .device.mountpoint import init_ipod, validate_mountpoint
from .exceptions import (
    ArtworkError,
    DatabaseError,
    DependencyError,
    DeviceError,
    HashError,
    MountPointError,
    ParseError,
    PlaylistError,
    PyGpodError,
    TrackError,
    UnsupportedFormatError,
    WriteError,
)
from .model.artwork import Artwork
from .model.chapterdata import Chapter, ChapterData
from .model.config import DatabaseConfig
from .model.database import Database
from .model.playlist import Playlist
from .model.smartplaylist import (
    SPLAction,
    SPLField,
    SPLLimitSort,
    SPLLimitType,
    SPLMatch,
    SPLPrefs,
    SPLRule,
    apply_limit,
    evaluate_smart_playlist,
)
from .model.track import Track


def discover():
    """Auto-discover connected iPods.

    Scans mounted volumes for iPods, then always attempts USB detection
    to find iPods at non-standard mount points or not yet mounted.

    Returns:
        List of (mountpoint, Device) tuples. Mountpoint is None for
        USB-detected devices that are not mounted.
    """
    import pathlib
    import platform

    results = []
    found_mountpoints: set[str] = set()

    # Scan common mount points
    system = platform.system()
    mounts = []

    try:
        if system == "Linux":
            for base in [pathlib.Path("/media"), pathlib.Path("/run/media")]:
                try:
                    if base.exists():
                        for p in base.glob("*/*"):
                            try:
                                if p.is_dir():
                                    mounts.append(p)
                            except (PermissionError, OSError):
                                pass
                except (PermissionError, OSError):
                    pass
            mnt = pathlib.Path("/mnt")
            try:
                if mnt.exists():
                    for p in mnt.iterdir():
                        try:
                            if p.is_dir():
                                mounts.append(p)
                        except (PermissionError, OSError):
                            pass
            except (PermissionError, OSError):
                pass
        elif system == "Darwin":
            volumes = pathlib.Path("/Volumes")
            try:
                if volumes.exists():
                    for p in volumes.iterdir():
                        try:
                            if p.is_dir():
                                mounts.append(p)
                        except (PermissionError, OSError):
                            pass
            except (PermissionError, OSError):
                pass
        elif system == "Windows":
            import string

            for letter in string.ascii_uppercase:
                drive = pathlib.Path(f"{letter}:\\")
                try:
                    if drive.exists():
                        mounts.append(drive)
                except (PermissionError, OSError):
                    pass
    except Exception:
        logger.debug("Mount point scanning failed", exc_info=True)

    for mp in mounts:
        if validate_mountpoint(str(mp)):
            try:
                dev = Device.from_mountpoint(str(mp))
                results.append((str(mp), dev))
                found_mountpoints.add(str(mp))
            except Exception:
                logger.debug("Failed to read device at %s", mp, exc_info=True)

    # Always try USB detection - find iPods at non-standard mounts or unmounted
    try:
        from .device.usb import detect_ipod_usb

        usb_devices = detect_ipod_usb()
        if usb_devices:
            # Try to resolve mount points for USB devices
            usb_mounts = _resolve_usb_mount_points(system)
            for usb_mp in usb_mounts:
                if usb_mp not in found_mountpoints and validate_mountpoint(usb_mp):
                    try:
                        dev = Device.from_mountpoint(usb_mp)
                        results.append((usb_mp, dev))
                        found_mountpoints.add(usb_mp)
                    except Exception:
                        logger.debug("Failed to read device at %s", usb_mp, exc_info=True)

            # If no mounted iPods found, include USB-only devices
            if not results:
                for usb_dev in usb_devices:
                    try:
                        dev = Device.from_usb(usb_dev)
                        results.append((None, dev))
                    except Exception:
                        logger.debug("Failed to create device from USB info", exc_info=True)
    except Exception:
        logger.debug("USB-based iPod discovery failed", exc_info=True)

    return results


def _resolve_usb_mount_points(system: str) -> list[str]:
    """Find mount points for USB mass storage devices.

    Uses platform-specific methods to discover mount points that may
    not be in the standard locations checked by discover().

    Args:
        system: Platform name from platform.system().

    Returns:
        List of mount point paths for USB storage devices.
    """
    mount_points: list[str] = []

    if system == "Linux":
        mount_points.extend(_resolve_linux_usb_mounts())
    elif system == "Darwin":
        mount_points.extend(_resolve_macos_usb_mounts())
    elif system == "Windows":
        mount_points.extend(_resolve_windows_usb_mounts())

    return mount_points


def _resolve_linux_usb_mounts() -> list[str]:
    """Find USB storage mount points on Linux via /proc/mounts."""
    import pathlib

    mount_points: list[str] = []

    # Check /dev/disk/by-id for Apple iPod devices and resolve to mount points
    by_id = pathlib.Path("/dev/disk/by-id")
    if by_id.exists():
        apple_devs: set[str] = set()
        for link in by_id.iterdir():
            name_lower = link.name.lower()
            if "apple" in name_lower or "ipod" in name_lower:
                try:
                    real = str(link.resolve())
                    apple_devs.add(real)
                    # Also add partition variants (e.g. /dev/sdb -> /dev/sdb1)
                    parent = pathlib.Path(real)
                    if parent.parent == pathlib.Path("/dev"):
                        for part in pathlib.Path("/dev").glob(parent.name + "[0-9]*"):
                            apple_devs.add(str(part))
                except OSError:
                    logger.debug("Cannot resolve symlink %s", link, exc_info=True)

        if apple_devs:
            proc_mounts = pathlib.Path("/proc/mounts")
            if proc_mounts.exists():
                try:
                    for line in proc_mounts.read_text().splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0] in apple_devs:
                            mount_points.append(parts[1])
                except OSError:
                    logger.debug("Failed to read /proc/mounts", exc_info=True)

    return mount_points


def _resolve_macos_usb_mounts() -> list[str]:
    """Find USB storage mount points on macOS via diskutil."""
    import subprocess

    mount_points: list[str] = []
    try:
        import plistlib

        output = subprocess.check_output(
            ["diskutil", "list", "-plist", "external"],
            timeout=10,
        )
        plist = plistlib.loads(output)
        disk_ids = plist.get("AllDisksAndPartitions", [])
        for disk in disk_ids:
            mp = disk.get("MountPoint")
            if mp:
                mount_points.append(mp)
            for part in disk.get("Partitions", []):
                mp = part.get("MountPoint")
                if mp:
                    mount_points.append(mp)
    except Exception:
        logger.debug("macOS diskutil mount point discovery failed", exc_info=True)

    return mount_points


def _resolve_windows_usb_mounts() -> list[str]:
    """Find USB storage mount points on Windows via WMI."""
    mount_points: list[str] = []
    try:
        import wmi  # type: ignore

        c = wmi.WMI()
        for disk in c.Win32_DiskDrive(InterfaceType="USB"):
            for partition in disk.associators("Win32_DiskDriveToDiskPartition"):
                for logical in partition.associators("Win32_LogicalDiskToPartition"):
                    mp = logical.DeviceID  # e.g. "E:"
                    if mp:
                        mount_points.append(mp + "\\")
    except Exception:
        logger.debug("Windows WMI mount point discovery failed", exc_info=True)

    return mount_points


__all__ = [
    "DatabaseConfig",
    "Database",
    "Track",
    "Playlist",
    "Artwork",
    "ChapterData",
    "Chapter",
    "SPLRule",
    "SPLPrefs",
    "SPLField",
    "SPLAction",
    "SPLMatch",
    "SPLLimitSort",
    "SPLLimitType",
    "evaluate_smart_playlist",
    "apply_limit",
    "Device",
    "PhotoDB",
    "Photo",
    "PhotoAlbum",
    "read_play_counts",
    "read_otg_playlists",
    "init_ipod",
    "validate_mountpoint",
    "discover",
    "PyGpodError",
    "DatabaseError",
    "ParseError",
    "WriteError",
    "DeviceError",
    "MountPointError",
    "HashError",
    "TrackError",
    "PlaylistError",
    "ArtworkError",
    "DependencyError",
    "UnsupportedFormatError",
    "__version__",
]


# Check for optional dependencies and warn about missing ones
def _check_optional_deps() -> None:
    import warnings

    missing = []
    try:
        import mutagen  # noqa: F401
    except ImportError:
        missing.append("mutagen (tag reading: pip install pygpod[tags])")
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow (artwork: pip install pygpod[artwork])")
    try:
        import usb  # noqa: F401
    except ImportError:
        missing.append("pyusb (USB detection: pip install pygpod[usb])")
    if missing:
        warnings.warn(
            "pygpod: optional dependencies not installed: " + ", ".join(missing),
            stacklevel=3,
        )


_check_optional_deps()
del _check_optional_deps
