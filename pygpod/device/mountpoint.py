"""Mount point operations for iPod filesystem structure."""

from __future__ import annotations

import logging
import pathlib
from typing import Optional

from ..exceptions import MountPointError
from ..utils.compat import put16lint, put32lint, put64lint

logger = logging.getLogger(__name__)


def validate_mountpoint(mountpoint: str) -> bool:
    """Check if a path looks like a valid iPod mount point.

    Args:
        mountpoint: Filesystem path to check.

    Returns:
        True if iPod_Control directory structure exists.
    """
    logger.debug("Validating mountpoint: %s", mountpoint)
    try:
        mp = pathlib.Path(mountpoint)
        ipod_control = _find_ipod_control(mp)
        if ipod_control is None:
            return False
        # Check for iTunes directory
        for child in ipod_control.iterdir():
            if child.name.lower() == "itunes" and child.is_dir():
                return True
    except (PermissionError, OSError):
        logger.debug("Permission denied checking %s", mountpoint)
    return False


def find_itunesdb(mountpoint: str) -> Optional[pathlib.Path]:
    """Find the iTunesDB file on an iPod mount point.

    Handles case-insensitive lookups for cross-platform support.

    Args:
        mountpoint: Path to the iPod mount point.

    Returns:
        Path to iTunesDB, or None if not found.
    """
    mp = pathlib.Path(mountpoint)
    ipod_control = _find_ipod_control(mp)
    if ipod_control is None:
        return None

    itunes_dir = _find_ci(ipod_control, "iTunes")
    if itunes_dir is None:
        return None

    itunesdb = _find_ci(itunes_dir, "iTunesDB")
    return itunesdb


def init_ipod(mountpoint: str, model: str = "classic_6g", music_dirs: int = 50) -> None:
    """Initialize a fresh iPod directory structure with an empty iTunesDB.

    Creates all necessary directories and writes a minimal iTunesDB
    containing only a master playlist, ready for adding tracks.

    Args:
        mountpoint: Path to the iPod mount root.
        model: iPod model identifier (used for directory count).
        music_dirs: Number of Fxx music directories to create.

    Raises:
        MountPointError: If the mountpoint doesn't exist.
    """
    mp = pathlib.Path(mountpoint)
    if not mp.exists():
        raise MountPointError(f"Mount point does not exist: {mountpoint}")

    # Create directory structure
    itunes_dir = mp / "iPod_Control" / "iTunes"
    dirs = [
        itunes_dir,
        mp / "iPod_Control" / "Device",
        mp / "iPod_Control" / "Artwork",
        mp / "Calendars",
        mp / "Contacts",
        mp / "Notes",
    ]

    # Create Fxx music directories
    for i in range(music_dirs):
        dirs.append(mp / "iPod_Control" / "Music" / f"F{i:02d}")

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Create empty iTunesDB
    db_path = itunes_dir / "iTunesDB"
    if not db_path.exists():
        data = _build_empty_itunesdb()
        db_path.write_bytes(data)
        logger.info("Created empty iTunesDB at %s", db_path)


def _build_empty_itunesdb() -> bytes:
    """Build a minimal valid iTunesDB with an empty master playlist.

    Uses header sizes matching libgpod / iPod firmware expectations:
    - MHBD: 244 bytes (0xF4), required for db_version >= 0x28
    - MHLT/MHLP: 92 bytes, required for newer firmware
    - MHSD: 96 bytes (0x60)
    - MHYP: 108 bytes (0x6C)

    Structure: MHBD -> MHSD(type=1) -> MHLT (0 tracks)
                    -> MHSD(type=3) -> MHLP -> MHYP (master, podcast mirror)
                    -> MHSD(type=2) -> MHLP -> MHYP (master playlist)
    """
    import os
    import struct

    db_id = struct.unpack("<Q", os.urandom(8))[0]
    playlist_id = struct.unpack("<Q", os.urandom(8))[0]

    def _make_name_mhod(name: str) -> bytes:
        """Create a name MHOD (type 1) with UTF-16LE encoding."""
        name_bytes = name.encode("utf-16-le")
        mhod_header_len = 24
        mhod_body = bytearray(16 + len(name_bytes))
        put32lint(mhod_body, 0, 1)  # encoding = 1 (UTF-16LE)
        put32lint(mhod_body, 4, len(name_bytes))  # string length
        put32lint(mhod_body, 8, 1)  # unk1
        put32lint(mhod_body, 12, 0)  # unk2
        mhod_body[16 : 16 + len(name_bytes)] = name_bytes
        mhod_total = mhod_header_len + len(mhod_body)

        mhod = bytearray(mhod_header_len)
        mhod[0:4] = b"mhod"
        put32lint(mhod, 4, mhod_header_len)
        put32lint(mhod, 8, mhod_total)
        put32lint(mhod, 0x0C, 1)  # mhod_type = title
        return bytes(mhod) + bytes(mhod_body)

    def _make_mhyp(pid: int, is_master: bool, mhod_data: bytes, num_mhods: int) -> bytes:
        """Create MHYP record."""
        import time

        # Mac epoch timestamp (seconds since 1904-01-01)
        mac_epoch_offset = 2082844800
        timestamp = int(time.time()) + mac_epoch_offset

        mhyp_header_len = 0x6C  # 108 bytes
        mhyp_total = mhyp_header_len + len(mhod_data)
        mhyp = bytearray(mhyp_header_len)
        mhyp[0:4] = b"mhyp"
        put32lint(mhyp, 4, mhyp_header_len)
        put32lint(mhyp, 8, mhyp_total)
        put32lint(mhyp, 0x0C, num_mhods)
        put32lint(mhyp, 0x10, 0)  # num_tracks = 0
        put32lint(mhyp, 0x14, 1 if is_master else 0)
        put32lint(mhyp, 0x18, timestamp)
        put64lint(mhyp, 0x1C, pid)
        # unk_0x28 = 1, sort_order = 1 (libgpod defaults)
        put32lint(mhyp, 0x28, 1)
        put32lint(mhyp, 0x2C, 1)
        return bytes(mhyp) + mhod_data

    name_mhod_data = _make_name_mhod("iPod")

    # --- Master playlist for type 2 and type 3 ---
    # Both share the same playlist_id (libgpod behavior)
    mhyp_data = _make_mhyp(playlist_id, True, name_mhod_data, 1)

    def _make_list_header(magic: bytes, count: int, header_len: int = 92) -> bytes:
        """Create a list header (MHLT or MHLP)."""
        hdr = bytearray(header_len)
        hdr[0:4] = magic
        put32lint(hdr, 4, header_len)
        put32lint(hdr, 8, count)
        return bytes(hdr)

    mhlt = _make_list_header(b"mhlt", 0)
    mhlp = _make_list_header(b"mhlp", 1)
    mhlp_pod = _make_list_header(b"mhlp", 1)

    def _make_mhsd(mhsd_type: int, children_bytes: bytes) -> bytes:
        """Create MHSD wrapper."""
        mhsd_header_len = 0x60  # 96 bytes
        mhsd_total = mhsd_header_len + len(children_bytes)
        mhsd = bytearray(mhsd_header_len)
        mhsd[0:4] = b"mhsd"
        put32lint(mhsd, 4, mhsd_header_len)
        put32lint(mhsd, 8, mhsd_total)
        put32lint(mhsd, 0x0C, mhsd_type)
        return bytes(mhsd) + children_bytes

    # MHSD type 1 (tracks)
    mhsd1_data = _make_mhsd(1, mhlt)
    # MHSD type 3 (podcast mirror - same master playlist, libgpod puts this before type 2)
    mhsd3_data = _make_mhsd(3, mhlp_pod + mhyp_data)
    # MHSD type 2 (playlists)
    mhsd2_data = _make_mhsd(2, mhlp + mhyp_data)

    # --- MHBD (database header) - 244 bytes (0xF4) for db_version 48 ---
    mhbd_header_len = 0xF4  # 244 bytes
    children_data = mhsd1_data + mhsd3_data + mhsd2_data
    mhbd_total = mhbd_header_len + len(children_data)

    mhbd = bytearray(mhbd_header_len)
    mhbd[0:4] = b"mhbd"
    put32lint(mhbd, 4, mhbd_header_len)
    put32lint(mhbd, 8, mhbd_total)
    put32lint(mhbd, 0x0C, 1)  # unknown1
    put32lint(mhbd, 0x10, 0x19)  # db_version (25, upgraded by save() if needed)
    put32lint(mhbd, 0x14, 3)  # num_children (3 MHSDs)
    put64lint(mhbd, 0x18, db_id)  # db_id
    put16lint(mhbd, 0x20, 1)  # platform = Mac
    put16lint(mhbd, 0x46, 0x656E)  # language = "en"

    return bytes(mhbd) + children_data


def _find_ipod_control(mountpoint: pathlib.Path) -> Optional[pathlib.Path]:
    """Find iPod_Control directory case-insensitively."""
    return _find_ci(mountpoint, "iPod_Control")


def _find_ci(parent: pathlib.Path, name: str) -> Optional[pathlib.Path]:
    """Case-insensitive file/directory lookup."""
    try:
        # Try exact match first (fast path)
        exact = parent / name
        if exact.exists():
            return exact

        # Fall back to case-insensitive search
        if not parent.exists():
            return None
        name_lower = name.lower()
        for child in parent.iterdir():
            if child.name.lower() == name_lower:
                return child
    except (PermissionError, OSError):
        return None
    return None
