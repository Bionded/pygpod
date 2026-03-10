"""iPod file path utilities.

Handles Fxx directory naming, random filename generation, and path conversion.
"""

from __future__ import annotations

import logging
import os
import pathlib
import random
import string
from typing import Optional

logger = logging.getLogger(__name__)


def pick_music_dir(
    mountpoint: str, num_dirs: int = 50, rng: Optional[random.Random] = None
) -> pathlib.Path:
    """Pick a random Fxx music directory for a new track.

    Distributes files somewhat evenly across directories.

    Args:
        mountpoint: iPod mount point path.
        num_dirs: Number of Fxx directories available.
        rng: Optional Random instance for deterministic output.

    Returns:
        Path to the selected Fxx directory.
    """
    r = rng or random
    music_root = pathlib.Path(mountpoint) / "iPod_Control" / "Music"
    idx = r.randint(0, num_dirs - 1)
    logger.debug("Picked music dir: F%02d", idx)
    return music_root / f"F{idx:02d}"


def generate_ipod_filename(
    extension: str = ".mp3",
    prefix: str = "pygpod",
    rand_len: int = 8,
    charset: str = "alnum",
    rng: Optional[random.Random] = None,
) -> str:
    """Generate a random iPod-style filename.

    Args:
        extension: File extension including the dot.
        prefix: Filename prefix (default "pygpod", libgpod uses "libgpod").
        rand_len: Number of random characters (default 8, libgpod uses 6).
        charset: "alnum" for A-Z0-9, "digits" for 0-9 only.
        rng: Optional Random instance for deterministic output.

    Returns:
        Random filename string.
    """
    r = rng or random
    if charset == "digits":
        chars = string.digits
    else:
        chars = string.ascii_uppercase + string.digits
    rand_part = "".join(r.choices(chars, k=rand_len))
    filename = f"{prefix}{rand_part}{extension}"
    logger.debug("Generated filename: %s", filename)
    return filename


def copy_track_to_ipod(
    source: str,
    mountpoint: str,
    num_dirs: int = 50,
    prefix: str = "pygpod",
    rand_len: int = 8,
    charset: str = "alnum",
    rng: Optional[random.Random] = None,
) -> str:
    """Copy an audio file to the iPod's music directory.

    Picks a random Fxx directory and generates a random filename.

    Args:
        source: Path to the source audio file.
        mountpoint: iPod mount point.
        num_dirs: Number of Fxx directories.
        prefix: Filename prefix.
        rand_len: Number of random characters in filename.
        charset: "alnum" or "digits".
        rng: Optional Random instance for deterministic output.

    Returns:
        The iPod-style colon-separated path (e.g., ':iPod_Control:Music:F05:ABCD1234.mp3').
    """
    import shutil

    src_path = pathlib.Path(source)
    ext = src_path.suffix.lower()

    dest_dir = pick_music_dir(mountpoint, num_dirs, rng=rng)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_name = generate_ipod_filename(
        ext, prefix=prefix, rand_len=rand_len, charset=charset, rng=rng
    )
    dest_path = dest_dir / dest_name

    # Ensure unique filename
    while dest_path.exists():
        dest_name = generate_ipod_filename(
            ext, prefix=prefix, rand_len=rand_len, charset=charset, rng=rng
        )
        dest_path = dest_dir / dest_name

    shutil.copy2(str(src_path), str(dest_path))

    # Convert to iPod path
    rel = dest_path.relative_to(pathlib.Path(mountpoint))
    return ":" + ":".join(rel.parts)


def get_file_extension(path: str) -> str:
    """Get file extension in lowercase."""
    return pathlib.Path(path).suffix.lower()


def file_size(path: str) -> int:
    """Get file size in bytes."""
    return os.path.getsize(path)
