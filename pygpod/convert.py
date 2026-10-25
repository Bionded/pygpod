"""Optional conversion on import: audio an iPod cannot play becomes Apple Lossless or AAC,
video becomes the iPod's H.264 Baseline profile.

Used by ``Database.add_track(..., convert=True)``. Lossless sources (FLAC, Monkey's Audio,
WavPack, ...) become ALAC when the generation plays ALAC, everything else AAC. Sample rates
above 48 kHz and more than two channels are reduced to what iPods play. Video (on iPods that
play video) becomes an .m4v with H.264 Baseline video and AAC-LC stereo audio, fitted inside
320x240 (``size="small"``, every video iPod) or 640x480 (``size="large"``, 5.5G, nano 3G-5G,
Classic). Needs the ``ffmpeg`` program (and ``ffprobe`` to check video); nothing is converted
unless asked for.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, NamedTuple, Optional

from .exceptions import UnsupportedFormatError

logger = logging.getLogger(__name__)

# codecs (as named by tags.probe_media) whose conversion keeps every bit when the iPod plays ALAC
LOSSLESS_CODECS = {"flac", "ape", "wavpack", "tta", "optimfrog", "alac", "wav", "aiff"}
DEFAULT_AAC_BITRATE = "256k"
TIMEOUT_S = 600
# ffmpeg and ffprobe write UTF-8 (tags, paths) whatever the locale; decoding with the locale
# default (e.g. cp1252 on Windows) fails on the first non-ASCII byte
_UTF8 = {"encoding": "utf-8", "errors": "replace"}


def find_ffmpeg() -> str:
    """Path of the ffmpeg program (``PYGPOD_FFMPEG`` overrides the search in PATH)."""
    path = os.environ.get("PYGPOD_FFMPEG") or shutil.which("ffmpeg")
    if not path or not (os.path.isfile(path) or shutil.which(path)):
        raise UnsupportedFormatError(
            "converting needs ffmpeg: install it or set PYGPOD_FFMPEG to its path"
            + (f" ({path} not found)" if path else "")
        )
    return path


def find_ffprobe() -> Optional[str]:
    """Path of ffprobe (``PYGPOD_FFPROBE``, next to ffmpeg, or in PATH), None if missing."""
    path = os.environ.get("PYGPOD_FFPROBE")
    if path:
        return path
    ffmpeg = os.environ.get("PYGPOD_FFMPEG")
    if ffmpeg:
        sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        if os.path.isfile(sibling):
            return sibling
    return shutil.which("ffprobe")


class VideoProfile(NamedTuple):
    """Limits of an iPod H.264 video profile (Baseline, yuv420p, AAC-LC stereo audio)."""

    name: str
    max_width: int
    max_height: int
    level: int  # H.264 level x 10 (13 = 1.3)
    max_video_kbps: int
    max_fps: int = 30


_STILL_IMAGE_CODECS = {"mjpeg", "png", "bmp", "gif", "tiff", "webp"}
SMALL_VIDEO = VideoProfile("small", 320, 240, 13, 768)
LARGE_VIDEO = VideoProfile("large", 640, 480, 30, 1500)
VIDEO_AUDIO_BITRATE = "128k"


def video_profile(generation=None) -> VideoProfile:
    """Largest profile the generation plays: LARGE except the 5G (VIDEO_1) and unknown models."""
    from .device.models import VIDEO_GENERATIONS, IpodGeneration

    if generation in VIDEO_GENERATIONS and generation != IpodGeneration.VIDEO_1:
        return LARGE_VIDEO
    return SMALL_VIDEO


def probe_video(filepath: str) -> Optional[Dict[str, Any]]:
    """ffprobe facts about a media file, None when ffprobe is missing or fails.

    Returns ``{"format": names, "duration": s, "video": stream or None, "audio": stream or
    None}``; cover pictures (attached_pic streams) do not count as video.
    """
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    cmd = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", filepath]
    try:
        proc = subprocess.run(cmd, capture_output=True, **_UTF8, timeout=60)
        data = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout else None
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        logger.debug("ffprobe failed on %s", filepath, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    result: Dict[str, Any] = {"video": None, "audio": None}
    for st in data.get("streams") or []:
        kind = st.get("codec_type")
        if kind == "video" and (
            (st.get("disposition") or {}).get("attached_pic")
            or st.get("codec_name") in _STILL_IMAGE_CODECS
        ):
            continue  # cover or chapter pictures, not video
        if kind in ("video", "audio") and result[kind] is None:
            result[kind] = st
    fmt = data.get("format") or {}
    result["format"] = fmt.get("format_name") or ""
    result["duration"] = _num(fmt.get("duration"))
    return result


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fps(stream: Dict[str, Any]) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = str(stream.get(key) or "0/0").partition("/")
        if _num(den or 1) > 0 and _num(num) > 0:
            return _num(num) / _num(den or 1)
    return 0.0


def video_problems(
    probe: Dict[str, Any], profile: VideoProfile, filepath: Optional[str] = None
) -> List[str]:
    """Why a probed file does not play as is on an iPod with ``profile`` (empty: it plays)."""
    from .device.models import MAX_CHANNELS, MAX_SAMPLE_RATE
    from .tags import VIDEO_EXTS

    problems = []
    if filepath and os.path.splitext(filepath)[1].lower() not in VIDEO_EXTS:
        problems.append("not an .m4v/.mp4/.mov file")
    if "mp4" not in probe.get("format", "") and "mov" not in probe.get("format", ""):
        problems.append(f"container {probe.get('format') or '?'}")
    v = probe.get("video")
    if v:
        if v.get("codec_name") != "h264":
            problems.append(f"video codec {v.get('codec_name')}")
        elif "baseline" not in str(v.get("profile", "")).lower():
            problems.append(f"H.264 profile {v.get('profile')}")
        elif int(_num(v.get("level"))) > profile.level:
            problems.append(f"H.264 level {_num(v.get('level')) / 10:g}")
        width, height = int(_num(v.get("width"))), int(_num(v.get("height")))
        if width > profile.max_width or height > profile.max_height:
            problems.append(f"{width}x{height} > {profile.max_width}x{profile.max_height}")
        if v.get("pix_fmt") not in (None, "yuv420p", "yuvj420p"):
            problems.append(f"pixel format {v.get('pix_fmt')}")
        if _fps(v) > profile.max_fps + 0.01:
            problems.append(f"{_fps(v):g} fps")
        if _num(v.get("bit_rate")) > profile.max_video_kbps * 1000:
            problems.append(f"video bitrate {_num(v.get('bit_rate')) / 1000:.0f} kbps")
    a = probe.get("audio")
    if a:
        if a.get("codec_name") != "aac" or str(a.get("profile", "LC")) != "LC":
            problems.append(f"audio {a.get('codec_name')} {a.get('profile') or ''}".rstrip())
        if int(_num(a.get("channels"))) > MAX_CHANNELS:
            problems.append(f"{a.get('channels')} audio channels")
        if _num(a.get("sample_rate")) > MAX_SAMPLE_RATE:
            problems.append(f"audio sample rate {a.get('sample_rate')} Hz")
    return problems


def convert_video_for_ipod(
    filepath: str,
    generation=None,
    size: str = "small",
    workdir: Optional[str] = None,
    probe: Optional[Dict[str, Any]] = None,
) -> str:
    """Convert a video to an .m4v the iPod plays (H.264 Baseline + AAC-LC stereo).

    ``size="small"``: inside 320x240, Level 1.3, <= 768 kbps (every video iPod).
    ``size="large"``: inside 640x480, Level 3.0, <= 1.5 Mbps where the generation plays it
    (5.5G, nano 3G-5G, Classic), else small. Keeps the aspect ratio, never upscales, caps
    the frame rate at 30 fps. Returns a new temporary file (the caller deletes it). Raises
    UnsupportedFormatError when the iPod plays no video, ffmpeg is missing or fails.
    """
    from .device.models import VIDEO_GENERATIONS, IpodGeneration

    if size not in ("small", "large"):
        raise ValueError(f"size must be 'small' or 'large', not {size!r}")
    known = generation is not None and generation != IpodGeneration.UNKNOWN
    if known and generation not in VIDEO_GENERATIONS:
        raise UnsupportedFormatError(f"{filepath}: this iPod does not play video")
    profile = video_profile(generation) if size == "large" else SMALL_VIDEO
    ffmpeg = find_ffmpeg()
    if probe is None:
        probe = probe_video(filepath) or {}
    v = probe.get("video") or {}
    w, h = profile.max_width, profile.max_height
    # square pixels first, then fit inside the box (never larger than the source), even sizes
    vf = (
        "scale='trunc(iw*sar/2)*2':'trunc(ih/2)*2',setsar=1,"
        f"scale='min({w},iw)':'min({h},ih)':force_original_aspect_ratio=decrease"
        ":force_divisible_by=2,format=yuv420p"
    )
    if _fps(v) > profile.max_fps + 0.01:
        vf += f",fps={profile.max_fps}"
    kbps = profile.max_video_kbps
    level = f"{profile.level // 10}.{profile.level % 10}"
    fd, dest = tempfile.mkstemp(prefix="pygpod-", suffix=".m4v", dir=workdir)
    os.close(fd)
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", filepath]
    cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "0", "-sn", "-dn"]
    cmd += ["-vf", vf, "-c:v", "libx264", "-profile:v", "baseline", "-level", level]
    cmd += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k"]
    a = probe.get("audio") or {}
    rate = "48000" if _num(a.get("sample_rate")) == 48000 else "44100"
    cmd += ["-c:a", "aac", "-b:a", VIDEO_AUDIO_BITRATE, "-ac", "2", "-ar", rate]
    cmd += ["-movflags", "+faststart", "-f", "ipod", dest]
    timeout = max(TIMEOUT_S, 2 * _num(probe.get("duration")))
    try:
        proc = subprocess.run(cmd, capture_output=True, **_UTF8, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        os.unlink(dest)
        raise UnsupportedFormatError(f"{filepath}: video conversion failed: {e}") from e
    if proc.returncode != 0:
        os.unlink(dest)
        detail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise UnsupportedFormatError(f"{filepath}: video conversion failed: {detail}")
    logger.info("Converted video %s to H.264 %s (%s)", filepath, level, profile.name)
    return dest


def video_needs_conversion(filepath: str, generation=None) -> bool:
    """True if an MP4/M4V/MOV video does not play as is on the generation (checked with
    ffprobe against video_profile()). Without ffprobe: False (the file is copied unchecked)."""
    probe = probe_video(filepath)
    if probe is None:
        logger.warning("ffprobe not found: %s is copied without checking its video", filepath)
        return False
    problems = video_problems(probe, video_profile(generation), filepath)
    if problems:
        logger.info("%s needs conversion: %s", filepath, ", ".join(problems))
    return bool(problems)


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
    video_size: str = "small",
) -> str:
    """Convert ``filepath`` to an .m4a (or, for video, .m4v) the iPod plays.

    Returns the path of a new temporary file (the caller deletes it). Tags are copied as far as
    ffmpeg maps them; embedded pictures are not (add_track reads tags and artwork from the
    original). A file with a video stream (found by ffprobe; cover pictures do not count)
    goes to convert_video_for_ipod(size=video_size) when the iPod plays video or the
    generation is unknown. Raises UnsupportedFormatError when ffmpeg is missing or fails.
    """
    from .device.models import MAX_CHANNELS, MAX_SAMPLE_RATE, VIDEO_GENERATIONS, IpodGeneration
    from .tags import probe_media

    if generation in VIDEO_GENERATIONS or generation in (None, IpodGeneration.UNKNOWN):
        probe = probe_video(filepath)
        if probe is not None and probe.get("video"):
            return convert_video_for_ipod(filepath, generation, video_size, workdir, probe)
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
        proc = subprocess.run(cmd, capture_output=True, **_UTF8, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        os.unlink(dest)
        raise UnsupportedFormatError(f"{filepath}: conversion failed: {e}") from e
    if proc.returncode != 0:
        os.unlink(dest)
        detail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise UnsupportedFormatError(f"{filepath}: conversion failed: {detail}")
    logger.info("Converted %s (%s) to %s", filepath, info.codec, codec.upper())
    return dest
