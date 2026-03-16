"""Audio file tag reading with optional mutagen integration.

If mutagen is installed, tags are read automatically from audio files.
If not, metadata must be provided manually.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import mutagen  # type: ignore  # noqa: F401
    from mutagen.easyid3 import EasyID3  # type: ignore  # noqa: F401
    from mutagen.mp3 import MP3  # type: ignore  # noqa: F401
    from mutagen.mp4 import MP4  # type: ignore  # noqa: F401

    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


def read_tags(filepath: str) -> Dict[str, Any]:
    """Read audio file tags.

    If mutagen is installed, reads tags automatically. Otherwise returns
    only basic file info.

    Args:
        filepath: Path to the audio file.

    Returns:
        Dictionary with tag information.
    """
    logger.debug("Reading tags from %s", filepath)
    result: Dict[str, Any] = {
        "title": os.path.splitext(os.path.basename(filepath))[0],
        "artist": "",
        "album": "",
        "genre": "",
        "composer": "",
        "comment": "",
        "track_number": 0,
        "total_tracks": 0,
        "cd_number": 0,
        "total_cds": 0,
        "year": 0,
        "bitrate": 0,
        "samplerate": 0,
        "duration_ms": 0,
        "file_size": os.path.getsize(filepath),
        "filetype": _detect_filetype(filepath),
    }

    if not HAS_MUTAGEN:
        return result

    try:
        audio = mutagen.File(filepath, easy=True)
        if audio is None:
            return result

        result["title"] = _get_tag(audio, "title", result["title"])
        result["artist"] = _get_tag(audio, "artist", "")
        result["album"] = _get_tag(audio, "album", "")
        result["genre"] = _get_tag(audio, "genre", "")
        result["composer"] = _get_tag(audio, "composer", "")
        result["comment"] = _get_tag(audio, "comment", "")
        result["albumartist"] = _get_tag(audio, "albumartist", "")

        # Track number (may be "3/12" format)
        tn = _get_tag(audio, "tracknumber", "0")
        if "/" in tn:
            parts = tn.split("/")
            result["track_number"] = _safe_int(parts[0])
            result["total_tracks"] = _safe_int(parts[1])
        else:
            result["track_number"] = _safe_int(tn)

        # Disc number
        dn = _get_tag(audio, "discnumber", "0")
        if "/" in dn:
            parts = dn.split("/")
            result["cd_number"] = _safe_int(parts[0])
            result["total_cds"] = _safe_int(parts[1])
        else:
            result["cd_number"] = _safe_int(dn)

        # Year
        date = _get_tag(audio, "date", "")
        if date:
            result["year"] = _safe_int(date[:4])

        # Audio info
        info = audio.info
        if info:
            result["duration_ms"] = int(info.length * 1000) if info.length else 0
            br = getattr(info, "bitrate", 0)
            result["bitrate"] = br // 1000 if br else 0
            result["samplerate"] = getattr(info, "sample_rate", 0)

    except Exception:
        logger.warning("Failed to read tags from %s", filepath, exc_info=True)

    return result


def extract_artwork(filepath: str) -> Optional[bytes]:
    """Extract embedded cover art from an audio file.

    Args:
        filepath: Path to the audio file.

    Returns:
        Raw image bytes (JPEG/PNG), or None if no artwork found.
    """
    if not HAS_MUTAGEN:
        return None

    try:
        audio = mutagen.File(filepath)
        if audio is None:
            return None

        # MP3 (ID3 tags) - APIC frames
        if hasattr(audio, "tags") and audio.tags:
            for key in audio.tags:
                if key.startswith("APIC"):
                    frame = audio.tags[key]
                    if hasattr(frame, "data") and frame.data:
                        return bytes(frame.data)

        # MP4/M4A - covr atom
        if hasattr(audio, "tags") and audio.tags and "covr" in audio.tags:
            covers = audio.tags["covr"]
            if covers:
                return bytes(covers[0])

        # FLAC - pictures
        if hasattr(audio, "pictures") and audio.pictures:
            return bytes(audio.pictures[0].data)

    except Exception:
        logger.debug("Failed to extract artwork from %s", filepath, exc_info=True)

    return None


def _get_tag(audio: Any, key: str, default: str = "") -> str:
    """Get a tag value, handling list values."""
    try:
        val = audio.get(key)
        if val:
            if isinstance(val, list):
                return str(val[0])
            return str(val)
    except Exception:
        logger.debug("Failed to read tag %r from file", key, exc_info=True)
    return default


def _safe_int(s: str) -> int:
    """Convert string to int, returning 0 on failure."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


# Audio formats supported by all iPods
_BASE_AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".wav", ".aiff", ".aif"}

# Video extensions (not validated here - resolution/codec limits vary too much)
VIDEO_EXTS = {".m4v", ".mp4", ".mov"}

# Audio formats that require conversion (never supported by any iPod)
UNSUPPORTED_AUDIO_EXTS = {
    ".flac": "FLAC",
    ".ogg": "Ogg Vorbis",
    ".opus": "Opus",
    ".wma": "Windows Media Audio",
    ".ape": "Monkey's Audio",
    ".webm": "WebM",
    ".mka": "Matroska Audio",
}

# Per-generation supported audio formats
# All generations get _BASE_AUDIO_EXTS. Some add ALAC (.m4a container).
# Shuffles are more limited (no Audible, no ALAC on older models).

# Legacy alias for backwards compat
IPOD_SUPPORTED_EXTS = _BASE_AUDIO_EXTS | VIDEO_EXTS


def get_supported_audio_exts(generation=None) -> set:
    """Get supported audio file extensions for an iPod generation.

    Args:
        generation: IpodGeneration enum value, or None for base set.

    Returns:
        Set of lowercase extensions (e.g. {'.mp3', '.m4a', ...}).
    """
    return set(_BASE_AUDIO_EXTS)  # all generations support the same audio exts


_video_warning_shown = False


def check_format_supported(filepath: str, generation=None) -> None:
    """Raise UnsupportedFormatError if the audio format is not iPod-compatible.

    Video files (.mp4, .m4v, .mov) are allowed through with a one-time warning
    since they have complex codec/resolution requirements that can't be
    validated by extension alone.

    Args:
        filepath: Path to the audio/video file.
        generation: IpodGeneration enum value (for future per-gen validation).

    Raises:
        UnsupportedFormatError: If the audio format needs conversion.
    """
    from .exceptions import UnsupportedFormatError

    ext = os.path.splitext(filepath)[1].lower()

    # Video files: allow but warn once
    if ext in VIDEO_EXTS:
        global _video_warning_shown
        if not _video_warning_shown:
            logger.warning(
                "Video file detected (%s). iPod video support depends on "
                "codec (H.264 Baseline), resolution (max 640x480), and "
                "container (.m4v/.mp4). Ensure the file is properly encoded.",
                ext,
            )
            _video_warning_shown = True
        return

    # Known unsupported audio formats
    if ext in UNSUPPORTED_AUDIO_EXTS:
        fmt_name = UNSUPPORTED_AUDIO_EXTS[ext]
        raise UnsupportedFormatError(
            f"{fmt_name} ({ext}) is not supported by iPod. "
            f"Convert to MP3 or AAC first. "
            f"Auto-conversion is not yet implemented."
        )


def _detect_filetype(filepath: str) -> str:
    """Detect audio file type from extension.

    Returns:
        Filetype string for MHOD type 6 and MHIT filetype marker.
    """
    ext = os.path.splitext(filepath)[1].lower()
    filetypes = {
        ".mp3": "MPEG audio file",
        ".m4a": "AAC audio file",
        ".m4b": "Protected AAC audio file",
        ".m4p": "Protected AAC audio file",
        ".aac": "AAC audio file",
        ".wav": "WAV audio file",
        ".aiff": "AIFF audio file",
        ".aif": "AIFF audio file",
        ".alac": "Apple Lossless audio file",
        ".m4v": "MPEG-4 video file",
        ".mp4": "MPEG-4 video file",
    }
    return filetypes.get(ext, "MPEG audio file")


def filetype_marker(filepath: str) -> bytes:
    """Get the 4-byte MHIT filetype marker for a file.

    Args:
        filepath: Path to the audio file.

    Returns:
        4-byte little-endian filetype marker.
    """
    ext = os.path.splitext(filepath)[1].lower()
    markers = {
        ".mp3": b" 3PM",  # 'MP3 ' reversed
        ".m4a": b" A4M",  # 'M4A ' reversed
        ".m4b": b" A4M",
        ".m4p": b" A4M",
        ".aac": b" A4M",
        ".wav": b" VAW",  # 'WAV ' reversed
        ".aiff": b"FFIA",  # 'AIFF' reversed
        ".aif": b"FFIA",
        ".m4v": b" V4M",  # 'M4V ' reversed
        ".mp4": b" V4M",
    }
    return markers.get(ext, b" 3PM")
