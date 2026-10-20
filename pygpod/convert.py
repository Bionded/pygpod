"""Optional conversion on import: audio an iPod cannot play becomes Apple Lossless or AAC.

Used by ``Database.add_track(..., convert=True)``. Lossless sources (FLAC, Monkey's Audio,
WavPack, ...) become ALAC when the generation plays ALAC, everything else AAC. Sample rates
above 48 kHz and more than two channels are reduced to what iPods play. Needs the ``ffmpeg``
program; nothing is converted unless asked for.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from .exceptions import UnsupportedFormatError

logger = logging.getLogger(__name__)

# codecs (as named by tags.probe_media) whose conversion keeps every bit when the iPod plays ALAC
LOSSLESS_CODECS = {"flac", "ape", "wavpack", "tta", "optimfrog", "alac", "wav", "aiff"}
DEFAULT_AAC_BITRATE = "256k"
TIMEOUT_S = 600


def find_ffmpeg() -> str:
    """Path of the ffmpeg program (``PYGPOD_FFMPEG`` overrides the search in PATH)."""
    path = os.environ.get("PYGPOD_FFMPEG") or shutil.which("ffmpeg")
    if not path:
        raise UnsupportedFormatError(
            "converting needs ffmpeg: install it or set PYGPOD_FFMPEG to its path"
        )
    return path


def target_codec(source_codec: Optional[str], generation=None) -> str:
    """'alac' for a lossless source on an iPod that plays ALAC, else 'aac'."""
    from .device.models import IpodGeneration, supported_audio_codecs

    gen = generation if generation is not None else IpodGeneration.UNKNOWN
    if source_codec in LOSSLESS_CODECS and "alac" in supported_audio_codecs(gen):
        return "alac"
    return "aac"


def convert_for_ipod(
    filepath: str,
    generation=None,
    aac_bitrate: str = DEFAULT_AAC_BITRATE,
    workdir: Optional[str] = None,
) -> str:
    """Convert ``filepath`` to an .m4a the iPod plays.

    Returns the path of a new temporary file (the caller deletes it). Tags are copied as far as
    ffmpeg maps them; embedded pictures are not (add_track reads tags and artwork from the
    original). Raises UnsupportedFormatError when ffmpeg is missing or fails.
    """
    from .device.models import MAX_CHANNELS, MAX_SAMPLE_RATE
    from .tags import probe_media

    info = probe_media(filepath)
    if info.is_video:
        raise UnsupportedFormatError(f"{filepath}: video is not converted")
    codec = target_codec(info.codec, generation)
    fd, dest = tempfile.mkstemp(prefix="pygpod-", suffix=".m4a", dir=workdir)
    os.close(fd)
    cmd = [find_ffmpeg(), "-nostdin", "-v", "error", "-y", "-i", filepath]
    cmd += ["-map", "0:a:0", "-map_metadata", "0", "-vn"]
    if codec == "alac":
        cmd += ["-c:a", "alac", "-sample_fmt", "s16p"]  # 16-bit: every ALAC-capable iPod plays it
    else:
        cmd += ["-c:a", "aac", "-b:a", aac_bitrate]
    if info.sample_rate > MAX_SAMPLE_RATE or info.sample_rate == 0:
        cmd += ["-ar", "44100"]  # unknown rate (no mutagen info): CD rate is always safe
    if info.channels > MAX_CHANNELS or info.channels == 0:
        cmd += ["-ac", "2"]
    cmd += ["-f", "ipod", dest]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        os.unlink(dest)
        raise UnsupportedFormatError(f"{filepath}: conversion failed: {e}") from e
    if proc.returncode != 0:
        os.unlink(dest)
        detail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise UnsupportedFormatError(f"{filepath}: conversion failed: {detail}")
    logger.info("Converted %s (%s) to %s", filepath, info.codec, codec.upper())
    return dest
