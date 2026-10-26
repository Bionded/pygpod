"""iPod device detection and capabilities.

Detects iPod model, generation, and capabilities from mount point.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Optional

from ..model.sysinfo import SysInfo, read_sysinfo
from .models import (
    ARTWORK_GENERATIONS,
    GENERATION_NAMES,
    PODCAST_GENERATIONS,
    SHUFFLE_GENERATIONS,
    VIDEO_GENERATIONS,
    ChecksumType,
    IpodGeneration,
    IpodInfo,
    get_checksum_type,
    get_db_version,
    lookup_by_usb_product_id,
    lookup_model,
    lookup_model_by_serial,
)

logger = logging.getLogger(__name__)


class Device:
    """Represents a connected iPod device with its capabilities."""

    def __init__(
        self,
        mountpoint: Optional[str] = None,
        sysinfo: Optional[SysInfo] = None,
        ipod_info: Optional[IpodInfo] = None,
    ) -> None:
        self.mountpoint = str(mountpoint) if mountpoint else None
        self._sysinfo = sysinfo
        self._info = ipod_info
        self._usb_enriched = False
        self._usb_info = None  # USBDeviceInfo when detected via USB

    @classmethod
    def from_mountpoint(cls, mountpoint: str) -> "Device":
        """Create a Device by reading info from an iPod mount point.

        Autodetection priority:
        1. ModelNumStr from SysInfo → direct model number lookup
        2. Serial number from SysInfoExtended → 3-char suffix lookup
           (libgpod checks serial first, but model number is more precise
           when present; both yield the same result for valid iPods)
        3. USB device detection (serial lookup, then product ID lookup)
        4. Unknown device (still usable with limited capabilities)

        Args:
            mountpoint: Path to the iPod mount point.

        Returns:
            Device instance with detected model and capabilities.
        """
        sysinfo = read_sysinfo(mountpoint)

        # Try model number from SysInfo
        model_num = sysinfo.model_number
        ipod_info = lookup_model(model_num) if model_num else None

        # Fallback: try serial number lookup
        if ipod_info is None or ipod_info.model_number == "Unknown":
            serial = sysinfo.serial_number
            if serial:
                serial_info = lookup_model_by_serial(serial)
                if serial_info:
                    ipod_info = serial_info

        # Fallback: try USB detection
        usb_enriched = False
        if ipod_info is None or ipod_info.model_number == "Unknown":
            usb_result = cls._try_usb_fallback(sysinfo, mountpoint)
            if usb_result is not None:
                ipod_info, updates = usb_result
                for key, val in updates.items():
                    if not sysinfo.raw.get(key):
                        sysinfo.raw[key] = val
                usb_enriched = True

        dev = cls(mountpoint, sysinfo, ipod_info)
        dev._usb_enriched = usb_enriched
        logger.debug("Detected device: %s", dev.model)
        logger.info("Device: %s, hash type: %s", dev.model, dev.checksum_type)
        return dev

    @classmethod
    def from_usb(cls, usb_info) -> "Device":
        """Create a Device from USB detection info (no mountpoint).

        Used when an iPod is connected via USB but not mounted.

        Args:
            usb_info: USBDeviceInfo from detect_ipod_usb().

        Returns:
            Device instance with USB-derived model and capabilities.
        """
        ipod_info = None
        if usb_info.serial:
            ipod_info = lookup_model_by_serial(usb_info.serial)
        if ipod_info is None:
            cap = getattr(usb_info, "capacity_gb", None)
            ipod_info = lookup_by_usb_product_id(usb_info.product_id, cap)

        sysinfo = SysInfo()
        if usb_info.firewire_guid:
            sysinfo.raw["FirewireGuid"] = usb_info.firewire_guid
        if usb_info.serial:
            sysinfo.extended["SerialNumber"] = usb_info.serial

        dev = cls(None, sysinfo, ipod_info)
        dev._usb_enriched = True
        dev._usb_info = usb_info
        logger.debug("USB device detected: %s (product_id=0x%04x)", dev.model, usb_info.product_id)
        return dev

    @staticmethod
    def _try_usb_fallback(sysinfo: SysInfo, mountpoint: Optional[str] = None):
        """Try USB detection as fallback for device identification.

        Only the iPod backing ``mountpoint`` is used; when it cannot be told apart from
        other connected iPods, there is no fallback (see detect_ipod_usb_for_mountpoint).

        Returns:
            Tuple of (IpodInfo, dict of sysinfo key-value updates) or None.
        """
        try:
            from .usb import detect_ipod_usb, detect_ipod_usb_for_mountpoint
        except ImportError:
            logger.debug("USB detection unavailable (pyusb not installed)")
            return None

        try:
            if mountpoint:
                usb_dev = detect_ipod_usb_for_mountpoint(mountpoint)
                usb_devices = [usb_dev] if usb_dev else []
            else:
                usb_devices = detect_ipod_usb()
                if len(usb_devices) > 1:
                    logger.debug("%d iPods connected, no USB fallback", len(usb_devices))
                    return None
        except Exception:
            logger.debug("USB device detection failed", exc_info=True)
            return None

        for usb_dev in usb_devices:
            updates = {}
            info = None

            # Try serial-based lookup first (most precise)
            if usb_dev.serial:
                info = lookup_model_by_serial(usb_dev.serial)

            # Fall back to USB product ID + capacity
            if info is None:
                cap = getattr(usb_dev, "capacity_gb", None)
                info = lookup_by_usb_product_id(usb_dev.product_id, cap)

            if info and info.model_number not in ("Invalid", "Unknown"):
                if usb_dev.firewire_guid:
                    updates["FirewireGuid"] = usb_dev.firewire_guid
                if usb_dev.serial:
                    updates["SerialNumber"] = usb_dev.serial
                return (info, updates)

        return None

    @property
    def sysinfo(self) -> SysInfo:
        """Device SysInfo data."""
        if self._sysinfo is None:
            if self.mountpoint:
                self._sysinfo = read_sysinfo(self.mountpoint)
            else:
                self._sysinfo = SysInfo()
        return self._sysinfo

    @property
    def info(self) -> Optional[IpodInfo]:
        """iPod model info from the model database."""
        return self._info

    @property
    def model(self) -> str:
        """Human-readable model name."""
        if self._info:
            gen_name = GENERATION_NAMES.get(self._info.generation, "Unknown")
            cap = self._info.capacity_gb
            if cap > 0:
                if cap == int(cap):
                    return f"{gen_name} ({int(cap)}GB)"
                return f"{gen_name} ({cap}GB)"
            return gen_name
        return "Unknown iPod"

    @property
    def generation(self) -> IpodGeneration:
        """iPod generation."""
        if self._info:
            return self._info.generation
        return IpodGeneration.UNKNOWN

    @property
    def firewire_guid(self) -> Optional[str]:
        """FirewireGuid - primary device identifier and hash key."""
        return self.sysinfo.firewire_guid

    @property
    def requires_hash(self) -> bool:
        """Whether this iPod requires database hashing (``checksum_type`` is not NONE)."""
        return self.checksum_type is not ChecksumType.NONE

    def _sysinfo_db_version(self) -> Optional[int]:
        """The SysInfoExtended ``DBVersion`` (or ``DatabaseVersion``) as int, if usable.

        Only 0-5 name a checksum scheme; other values (an iPod Classic 6G has been seen
        reporting 48) are ignored like a missing key.
        """
        ext = self.sysinfo.extended
        for key in ("DBVersion", "DatabaseVersion"):
            value = ext.get(key)
            if value is None:
                continue
            try:
                version = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= version <= 5:
                return version
        return None

    @property
    def checksum_type(self) -> ChecksumType:
        """Determine the checksum type for this device.

        Uses the SysInfoExtended ``DBVersion`` if available and 0-5 (the key real iPods
        and libgpod use; ``DatabaseVersion`` is still accepted), falls back to the
        generation-based mapping from libgpod.
        """
        return get_checksum_type(self.generation, self._sysinfo_db_version())

    @property
    def sqlite_db(self) -> bool:
        """Whether the iPod reads its library from the SQLite databases (``iTunesCDB`` and
        ``iTunes Library.itlp``) as libgpod decides it: the SysInfoExtended ``SQLiteDB`` key,
        else the model (nano 5G and newer). pygpod writes only the iTunesDB, so tracks it
        adds may not show up on such an iPod.
        """
        value = self.sysinfo.extended.get("SQLiteDB")
        if value is not None:
            return value is True or str(value).strip().lower() in ("1", "true", "yes")
        if self.sysinfo.extended:
            return False
        return self.generation in (IpodGeneration.NANO_5, IpodGeneration.NANO_6)

    @property
    def checksum_known(self) -> bool:
        """Whether ``checksum_type`` is known (model identified or SysInfoExtended DBVersion).

        False means the model could not be identified (SysInfo missing or incomplete) and
        ``checksum_type`` is only a NONE default; ``save()`` then signs like the database on
        the iPod (hash58/hash72) and ``check()`` reports ``model_unidentified``.
        """
        return (
            self.generation is not IpodGeneration.UNKNOWN or self._sysinfo_db_version() is not None
        )

    @property
    def db_version(self) -> int:
        """Get the iTunesDB version number for this device.

        This is the value written at offset 0x10 in the MHBD header.
        """
        return get_db_version(self.generation)

    @property
    def supports_artwork(self) -> bool:
        """Whether this iPod supports cover artwork."""
        return self.generation in ARTWORK_GENERATIONS

    def get_cover_art_formats(self):
        """Cover art thumbnail formats for this device (list of ArtworkFormatInfo, or None if
        unsupported).

        Like libgpod: the ``AlbumArt`` specifications of the device's SysInfoExtended when it
        has them (the firmware's own list, e.g. a Classic's 1069 142x142 and 55x55 1061), else
        the generation's table.
        """
        from .artwork_formats import get_cover_art_formats

        return self.get_sysinfo_cover_art_formats() or get_cover_art_formats(self.generation)

    def get_sysinfo_cover_art_formats(self):
        """The cover art formats the device's SysInfoExtended lists (``AlbumArt``), or None."""
        from .artwork_formats import SPEC_KEY_COVER_ART, formats_from_sysinfo_extended

        return formats_from_sysinfo_extended(self.sysinfo.extended, SPEC_KEY_COVER_ART)

    def get_photo_formats(self):
        """Photo thumbnail formats for this device (list of ArtworkFormatInfo, or None).

        Like libgpod: the ``ImageSpecifications`` of the device's SysInfoExtended when it has
        them (the firmware shows only photos in those sizes), else the generation's table.
        """
        from .artwork_formats import (
            SPEC_KEY_PHOTOS,
            formats_from_sysinfo_extended,
            get_photo_formats,
        )

        formats = formats_from_sysinfo_extended(self.sysinfo.extended, SPEC_KEY_PHOTOS)
        return formats or get_photo_formats(self.generation)

    @property
    def supports_video(self) -> bool:
        """Whether this iPod supports video playback."""
        return self.generation in VIDEO_GENERATIONS

    @property
    def supports_podcast(self) -> bool:
        """Whether this iPod supports podcasts."""
        return self.generation in PODCAST_GENERATIONS

    @property
    def is_shuffle(self) -> bool:
        """Whether this is an iPod Shuffle (uses iTunesSD)."""
        return self.generation in SHUFFLE_GENERATIONS

    @property
    def is_unknown(self) -> bool:
        """Whether the device model could not be determined."""
        return self._info is None or self._info.model_number == "Unknown"

    @property
    def has_sysinfo(self) -> bool:
        """Whether a SysInfo file with model info was found on disk."""
        return bool(self._sysinfo and self._sysinfo.model_num_str)

    @property
    def usb_enriched(self) -> bool:
        """Whether device info was obtained via USB detection fallback."""
        return self._usb_enriched

    @property
    def is_usb_only(self) -> bool:
        """Whether this device was detected via USB only (no mountpoint)."""
        return self.mountpoint is None

    @property
    def usb_info(self):
        """USB device info if detected via USB, else None."""
        return self._usb_info

    @property
    def music_dirs(self) -> int:
        """Number of Fxx music directories this iPod uses.

        From the model table; for an unknown model (no table entry) the consecutive
        ``F00``, ``F01``, ... directories on disk are counted like libgpod does, and
        50 is the default when there are none.
        """
        if self._info and self._info.musicdirs > 0:
            return self._info.musicdirs
        if self.mountpoint:
            n = 0
            while (self.music_dir / f"F{n:02d}").is_dir():
                n += 1
            if n:
                return n
        return 50  # Default

    @property
    def itunes_dir(self) -> pathlib.Path:
        """Path to iPod_Control/iTunes/ directory."""
        return pathlib.Path(self.mountpoint) / "iPod_Control" / "iTunes"

    @property
    def music_dir(self) -> pathlib.Path:
        """Path to iPod_Control/Music/ directory."""
        return pathlib.Path(self.mountpoint) / "iPod_Control" / "Music"

    @property
    def artwork_dir(self) -> pathlib.Path:
        """Path to iPod_Control/Artwork/ directory."""
        return pathlib.Path(self.mountpoint) / "iPod_Control" / "Artwork"

    @property
    def device_dir(self) -> pathlib.Path:
        """Path to iPod_Control/Device/ directory."""
        return pathlib.Path(self.mountpoint) / "iPod_Control" / "Device"

    def itunesdb_path(self) -> pathlib.Path:
        """Path to the iTunesDB file."""
        return self.itunes_dir / "iTunesDB"

    def itunessd_path(self) -> pathlib.Path:
        """Path to the iTunesSD file."""
        return self.itunes_dir / "iTunesSD"

    def artworkdb_path(self) -> pathlib.Path:
        """Path to the ArtworkDB file."""
        return self.artwork_dir / "ArtworkDB"

    @property
    def sysinfo_file_path(self) -> pathlib.Path:
        """Path to the SysInfo file on this iPod."""
        return pathlib.Path(self.mountpoint) / "iPod_Control" / "Device" / "SysInfo"

    def storage_info(self, full: bool = True) -> "StorageInfo":
        """Get filesystem storage usage.

        Args:
            full: If True (default), scan the filesystem to get per-category
                  breakdown (music, artwork, databases, photos). This can be
                  slow on large libraries. If False, return only total/used/free.

        Returns:
            StorageInfo with space data.

        Raises:
            DeviceError: If mountpoint is not set.
        """
        if not self.mountpoint:
            from ..exceptions import DeviceError

            raise DeviceError("Cannot get storage info without a mount point")

        total, free = _get_disk_space(self.mountpoint)
        used = total - free

        si = StorageInfo(total=total, free=free, used=used)
        if full:
            si.scan(self.mountpoint)
        return si

    def write_sysinfo(self) -> pathlib.Path:
        """Write current device info to SysInfo file.

        Preserves the original file format when re-saving an existing
        SysInfo.  Only adds missing keys (ModelNumStr, FirewireGuid).

        Returns:
            Path to the written SysInfo file.
        """
        path = self.sysinfo_file_path
        path.parent.mkdir(parents=True, exist_ok=True)

        # Start with original lines if available, track which keys exist
        seen_keys = set()
        lines = []
        if self._sysinfo and self._sysinfo._raw_lines:
            for key, raw_line in self._sysinfo._raw_lines:
                lines.append(raw_line)
                seen_keys.add(key)

        # Add missing essential keys
        if "ModelNumStr" not in seen_keys:
            if self._info and self._info.model_number not in ("Invalid", "Unknown"):
                lines.append("ModelNumStr: x%s" % self._info.model_number)
        if "FirewireGuid" not in seen_keys:
            guid = self._sysinfo.firewire_guid if self._sysinfo else None
            if guid:
                lines.append("FirewireGuid: 0x%s" % guid)
        if "SerialNumber" not in seen_keys:
            # Only write the real device serial (from SCSI VPD), never the
            # USB descriptor serial which is the FireWire GUID on Classics.
            serial = self._try_read_real_serial()
            if serial:
                lines.append("SerialNumber: %s" % serial)

        if lines:
            path.write_text("\n".join(lines) + "\n")
            logger.info("SysInfo written to %s", path)

        return path

    def _try_read_real_serial(self) -> Optional[str]:
        """Try to read the real device serial via SCSI or platform APIs.

        Returns the serial only if it looks like a real device serial
        (not the FireWire GUID that USB descriptors expose on Classics).
        """
        try:
            from .usb import read_ipod_serial

            serial = read_ipod_serial()
            if serial:
                # Verify it's not just the GUID repeated
                guid = self.firewire_guid
                if guid and serial.upper() == guid.upper():
                    return None
                return serial
        except Exception:
            logger.debug("Failed to read real serial", exc_info=True)
        return None

    def __repr__(self) -> str:
        if self.mountpoint:
            return f"<Device {self.model} at {self.mountpoint}>"
        return f"<Device {self.model} (USB, not mounted)>"


# ============================================================================
# Storage info
# ============================================================================


def _get_disk_space(path: str) -> tuple:
    """Get total and free disk space in bytes (cross-platform).

    Returns:
        (total_bytes, free_bytes)
    """
    try:
        # Python 3.3+ shutil.disk_usage works on all platforms
        import shutil

        usage = shutil.disk_usage(path)
        return usage.total, usage.free
    except (OSError, AttributeError):
        pass

    # Fallback for older Python or edge cases
    try:
        stat = os.statvfs(path)
        return stat.f_frsize * stat.f_blocks, stat.f_frsize * stat.f_bavail
    except (OSError, AttributeError):
        pass

    return 0, 0


def _file_size(path: pathlib.Path) -> int:
    """Get file size in bytes, 0 if doesn't exist."""
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _dir_size(path: pathlib.Path) -> int:
    """Get total size of all files in a directory tree, 0 if doesn't exist."""
    total = 0
    try:
        if not path.is_dir():
            return 0
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _fmt_size(b: int) -> str:
    """Format byte count as human-readable string."""
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB"
    if b >= 1 << 10:
        return f"{b / (1 << 10):.1f} KB"
    return f"{b} B"


class StorageInfo:
    """iPod filesystem storage usage breakdown.

    Always has total/free/used. Per-category fields (music, artwork, etc.)
    are populated by ``scan()`` or by ``Device.storage_info(full=True)``.
    """

    __slots__ = (
        "total",
        "free",
        "used",
        "music",
        "artwork_thumbnails",
        "artwork_db",
        "itunes_db",
        "itunes_sd",
        "photos",
        "other",
        "_scanned",
    )

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k, 0))
        self._scanned = False

    @property
    def is_scanned(self) -> bool:
        """Whether per-category breakdown has been computed."""
        return self._scanned

    def scan(self, mountpoint: str) -> None:
        """Scan the iPod filesystem to compute per-category sizes.

        Can be called multiple times to refresh. Updates all category
        fields (music, artwork, databases, photos, other).

        Args:
            mountpoint: iPod mount point path.
        """
        mp = pathlib.Path(mountpoint)
        ipod_ctrl = mp / "iPod_Control"

        music_dir = ipod_ctrl / "Music"
        artwork_dir = ipod_ctrl / "Artwork"
        itunes_dir = ipod_ctrl / "iTunes"
        photos_dirs = (mp / "Photos", ipod_ctrl / "Photos")  # libgpod's place, pygpod's old one

        self.music = _dir_size(music_dir)

        artwork_total = _dir_size(artwork_dir)
        artworkdb = _file_size(artwork_dir / "ArtworkDB")
        self.artwork_db = artworkdb
        self.artwork_thumbnails = max(0, artwork_total - artworkdb)

        self.itunes_db = _file_size(itunes_dir / "iTunesDB")
        self.itunes_sd = _file_size(itunes_dir / "iTunesSD")
        self.photos = sum(_dir_size(d) for d in photos_dirs)
        self.other = max(0, self.used - self.music - artwork_total - self.photos)
        self._scanned = True

    def __repr__(self) -> str:
        return (
            f"<StorageInfo total={_fmt_size(self.total)} "
            f"used={_fmt_size(self.used)} free={_fmt_size(self.free)}>"
        )

    def __str__(self) -> str:
        lines = [
            f"Total:              {_fmt_size(self.total)}",
            f"Used:               {_fmt_size(self.used)}",
            f"Free:               {_fmt_size(self.free)}",
        ]
        if self._scanned:
            lines.append("")
            lines.append(f"  Music files:      {_fmt_size(self.music)}")
            lines.append(f"  Artwork (.ithmb): {_fmt_size(self.artwork_thumbnails)}")
            lines.append(f"  ArtworkDB:        {_fmt_size(self.artwork_db)}")
            lines.append(f"  iTunesDB:         {_fmt_size(self.itunes_db)}")
            if self.itunes_sd:
                lines.append(f"  iTunesSD:         {_fmt_size(self.itunes_sd)}")
            if self.photos:
                lines.append(f"  Photos:           {_fmt_size(self.photos)}")
            lines.append(f"  Other/System:     {_fmt_size(self.other)}")
        return "\n".join(lines)
