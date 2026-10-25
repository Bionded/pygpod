"""Audio file tag reading with optional mutagen integration.

If mutagen is installed, tags are read automatically from audio files.
If not, metadata must be provided manually.
"""

from __future__ import annotations

import datetime
import logging
import math
import os
import re
import struct
from typing import Any, Dict, List, NamedTuple, Optional

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

    Besides title/artist/album/albumartist/genre/composer/comment/track/disc/year, the audio
    properties, Sound Check and gapless data, these keys are set when the file has them:

    =================  ===========================  ==================  ====================
    key                ID3 (MP3)                    MP4 (M4A/M4B)       Vorbis/FLAC, APE
    =================  ===========================  ==================  ====================
    compilation        TCMP                         cpil                COMPILATION
    sort_artist        TSOP                         soar                ARTISTSORT
    sort_album         TSOA                         soal                ALBUMSORT
    sort_title         TSOT                         sonm                TITLESORT
    sort_albumartist   TSO2                         soaa                ALBUMARTISTSORT
    sort_composer      TSOC                         soco                COMPOSERSORT
    sort_tvshow        -                            sosn                -
    rating (0-100)     POPM (any e-mail, 0-255 in   -                   FMPS_RATING (0-1),
                       star bands of 20)                                RATING (1-5 / 0-100)
    bpm                TBPM                         tmpo                BPM
    grouping           GRP1, else TIT1              (c)grp              GROUPING
    explicit_flag      TXXX:ITUNESADVISORY          rtng (1/4 -> 1      ITUNESADVISORY
                       (1 explicit, 2 clean)        explicit, 2 clean)
    description        TDES                         ldes, else desc     -
    podcast_url        WFED                         purl                -
    podcast_rss        TGID (episode GUID, as       egid                -
                       gtkpod stores it)
    time_released      TDRL, else TDRC (full date)  (c)day (full date)  -
    category           TCAT                         catg                -
    keywords           TKWD                         keyw                -
    =================  ===========================  ==================  ====================

    Keyword arguments of ``Database.add_track`` override every one of them.

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
            if _mp3_without_header(info):
                # the length is estimated from the first frame's bitrate: wrong for VBR files
                from .gapless import mp3_stream_info

                result.update(mp3_stream_info(filepath) or {})

        if type(audio).__name__ in ("MP4", "EasyMP4"):
            result.update(_read_mp4_video_tags(filepath))
        result.update(_read_extra_tags(filepath))
        soundcheck = read_soundcheck(filepath)
        if soundcheck is not None:
            result["soundcheck"] = soundcheck
        from .gapless import read_gapless

        result.update(read_gapless(filepath) or {})

    except Exception:
        logger.warning("Failed to read tags from %s", filepath, exc_info=True)

    return result


def _mp3_without_header(info: Any) -> bool:
    """True for mutagen MPEG info of an MP3 without a Xing/Info/VBRI header."""
    try:
        from mutagen.mp3 import BitrateMode, MPEGInfo
    except ImportError:  # pragma: no cover
        return False
    return isinstance(info, MPEGInfo) and info.bitrate_mode == BitrateMode.UNKNOWN


def soundcheck_from_gain(gain_db: float) -> int:
    """Sound Check value for a ReplayGain-style gain in dB (gtkpod/libgpod formula):
    round(1000 * 10^(-gain_dB / 10)), kept within 1 .. 2^32-1 (0 means "no Sound Check")."""
    value = math.floor(1000.0 * math.pow(10.0, -gain_db / 10.0) + 0.5)
    return max(1, min(0xFFFFFFFF, value))


def _tag_texts(audio: Any) -> Dict[str, str]:
    """Lower-case tag name -> first text value, for ID3 COMM/TXXX descriptions, MP4 freeform
    atoms ('----:com.apple.iTunes:NAME' -> 'name'), Vorbis comments and APEv2 items."""
    out: Dict[str, str] = {}
    tags = getattr(audio, "tags", None)
    if not tags:
        return out
    if hasattr(tags, "getall"):  # ID3
        for frame in tags.values():
            if frame.FrameID in ("COMM", "TXXX") and frame.text:
                out.setdefault(str(frame.desc).lower(), str(frame.text[0]))
        return out
    for key in list(tags.keys()):
        name = str(key)
        if name.startswith("----:"):
            name = name.rsplit(":", 1)[-1]
        value = tags[key]
        if isinstance(value, list):
            value = value[0] if value else ""
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value).decode("utf-8", "replace")
        out.setdefault(name.lower(), str(value))
    return out


# ---- Further tags: compilation, sort tags, rating, BPM, grouping, advisory, podcast ----

# MP4 atom -> read_tags key (text values)
_MP4_TEXT = {
    "soar": "sort_artist",
    "soal": "sort_album",
    "sonm": "sort_title",
    "soaa": "sort_albumartist",
    "soco": "sort_composer",
    "sosn": "sort_tvshow",
    "\xa9grp": "grouping",
    "purl": "podcast_url",
    "egid": "podcast_rss",
    "catg": "category",
    "keyw": "keywords",
}
# ID3 frame -> read_tags key (text values); GRP1 (iTunes >= 12.5) wins over TIT1
_ID3_TEXT = {
    "TSOP": "sort_artist",
    "TSOA": "sort_album",
    "TSOT": "sort_title",
    "TSO2": "sort_albumartist",
    "TSOC": "sort_composer",
    "TIT1": "grouping",
    "GRP1": "grouping",
    "TDES": "description",
    "TGID": "podcast_rss",
    "TCAT": "category",
    "TKWD": "keywords",
}
# Vorbis comment / APEv2 item (lower case) -> read_tags key
_VORBIS_TEXT = {
    "artistsort": "sort_artist",
    "albumsort": "sort_album",
    "titlesort": "sort_title",
    "albumartistsort": "sort_albumartist",
    "composersort": "sort_composer",
    "grouping": "grouping",
}
_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _truthy(value: Any) -> bool:
    value = _first(value)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _rating_from_popm(value: int) -> int:
    """POPM 0-255 -> 0-100 in steps of 20 (the Windows Media Player / foobar2000 star bands)."""
    if value <= 0:
        return 0
    for limit, rating in ((31, 20), (95, 40), (159, 60), (223, 80)):
        if value <= limit:
            return rating
    return 100


def _rating_from_text(text: str) -> Optional[int]:
    """FMPS_RATING (0.0-1.0) or RATING (1-5 stars or 0-100) -> 0-100 in steps of 20."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    if value <= 1.0 and "." in str(text):
        stars = value * 5
    elif value <= 5:
        stars = value
    else:
        stars = min(value, 100) / 20
    return int(round(stars)) * 20


def _advisory(value: Any) -> Optional[int]:
    """MP4 'rtng' / ITUNESADVISORY -> explicit_flag (1 explicit, 2 clean)."""
    try:
        value = int(str(_first(value)).strip())
    except (TypeError, ValueError):
        return None
    if value in (1, 4):
        return 1
    if value == 2:
        return 2
    return None


def _release_date(text: Any) -> Optional[datetime.datetime]:
    """A full date (YYYY-MM-DD...) as a local datetime; a year alone is not a release date."""
    m = _DATE_RE.match(str(_first(text) or ""))
    if not m:
        return None
    try:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _bpm(value: Any) -> Optional[int]:
    try:
        bpm = int(round(float(str(_first(value)).strip())))
    except (TypeError, ValueError):
        return None
    return bpm if 0 < bpm < 0x10000 else None


def _read_extra_tags(filepath: str) -> Dict[str, Any]:
    """Compilation, sort tags, rating, BPM, grouping, content advisory and podcast fields of
    a file (only the keys it has a value for). See read_tags for the mapping."""
    audio = mutagen.File(filepath)
    tags = getattr(audio, "tags", None) if audio is not None else None
    if not tags:
        return {}
    result: Dict[str, Any] = {}
    texts = _tag_texts(audio)

    if hasattr(tags, "getall"):  # ID3
        for frame_id, key in _ID3_TEXT.items():
            frame = tags.get(frame_id)
            if frame is not None and frame.text and str(frame.text[0]).strip():
                result[key] = str(frame.text[0])
        if "TCMP" in tags and tags["TCMP"].text:
            result["compilation"] = _truthy(str(tags["TCMP"].text[0]))
        for popm in tags.getall("POPM"):
            if getattr(popm, "rating", 0):
                result["rating"] = _rating_from_popm(popm.rating)
                break
        if "TBPM" in tags and tags["TBPM"].text:
            bpm = _bpm(tags["TBPM"].text[0])
            if bpm:
                result["bpm"] = bpm
        wfed = tags.getall("WFED")
        if wfed and getattr(wfed[0], "url", ""):
            result["podcast_url"] = str(wfed[0].url)
        for frame_id in ("TDRL", "TDRC"):
            frame = tags.get(frame_id)
            released = _release_date(frame.text[0]) if frame is not None and frame.text else None
            if released:
                result["time_released"] = released
                break
        advisory = _advisory(texts["itunesadvisory"]) if "itunesadvisory" in texts else None
        if advisory:
            result["explicit_flag"] = advisory
    elif type(audio).__name__ == "MP4":
        for atom, key in _MP4_TEXT.items():
            value = _first(tags.get(atom))
            if value is not None and str(value).strip():
                result[key] = str(value)
        description = _first(tags.get("ldes")) or _first(tags.get("desc"))
        if description:
            result["description"] = str(description)
        if "cpil" in tags:
            result["compilation"] = _truthy(tags["cpil"])
        bpm = _bpm(tags.get("tmpo")) if "tmpo" in tags else None
        if bpm:
            result["bpm"] = bpm
        advisory = _advisory(tags.get("rtng")) if "rtng" in tags else None
        if advisory:
            result["explicit_flag"] = advisory
        released = _release_date(tags.get("\xa9day"))
        if released:
            result["time_released"] = released
    else:  # Vorbis comments (FLAC, Ogg), APEv2
        for name, key in _VORBIS_TEXT.items():
            if texts.get(name, "").strip():
                result[key] = texts[name]
        if "compilation" in texts:
            result["compilation"] = _truthy(texts["compilation"])
        for name in ("fmps_rating", "rating"):
            rating = _rating_from_text(texts[name]) if name in texts else None
            if rating is not None:
                result["rating"] = rating
                break
        bpm = _bpm(texts["bpm"]) if "bpm" in texts else None
        if bpm:
            result["bpm"] = bpm
        advisory = _advisory(texts["itunesadvisory"]) if "itunesadvisory" in texts else None
        if advisory:
            result["explicit_flag"] = advisory
    return result


_GAIN_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)")


def read_soundcheck(filepath: str) -> Optional[int]:
    """Sound Check value from a file's tags, None if it has none (or mutagen is missing).

    iTunNORM (ID3 COMM / MP4 freeform; the larger of its first two hex values, as iTunes
    and gtkpod read it) wins, then REPLAYGAIN_TRACK_GAIN, then REPLAYGAIN_ALBUM_GAIN (ID3
    TXXX, MP4 freeform, Vorbis/FLAC comments, APEv2).
    """
    if not HAS_MUTAGEN:
        return None
    try:
        audio = mutagen.File(filepath)
    except Exception:  # noqa: BLE001 - unreadable = no tags
        logger.debug("mutagen could not read %s", filepath, exc_info=True)
        return None
    if audio is None:
        return None
    try:
        texts = _tag_texts(audio)
    except Exception:  # noqa: BLE001 - odd tag objects = no gain
        logger.debug("could not read gain tags of %s", filepath, exc_info=True)
        return None
    norm = texts.get("itunnorm", "").split()
    if len(norm) >= 2:
        try:
            value = max(int(norm[0], 16), int(norm[1], 16))
            if 0 < value <= 0xFFFFFFFF:
                return value
        except ValueError:
            logger.debug("bad iTunNORM in %s: %r", filepath, texts["itunnorm"])
    for key in ("replaygain_track_gain", "replaygain_album_gain"):
        m = _GAIN_RE.match(texts.get(key, ""))
        if m:
            return soundcheck_from_gain(float(m.group(1)))
    return None


_TRACK_GAIN_RE = re.compile(r"track_gain\s*=\s*([-+]?\d+(?:\.\d+)?)\s*dB")


def analyze_gain_db(filepath: str, timeout: Optional[float] = None) -> Optional[float]:
    """ReplayGain track gain (dB) of a file's first audio stream, measured by ffmpeg's
    ``replaygain`` filter (the same scale as REPLAYGAIN_TRACK_GAIN tags).

    Args:
        filepath: Audio file.
        timeout: Seconds before ffmpeg is stopped; default: 60 + the file's duration / 20
            (ffmpeg scans about 150x realtime on a Raspberry Pi 5).

    Returns:
        The gain in dB, or None if ffmpeg could not measure the file.

    Raises:
        UnsupportedFormatError: ffmpeg is not installed (``PYGPOD_FFMPEG`` overrides the
            search in PATH).
    """
    import subprocess

    from .convert import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if timeout is None:
        duration_s = 0.0
        if HAS_MUTAGEN:
            try:
                audio = mutagen.File(filepath)
                duration_s = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
            except Exception:  # noqa: BLE001 - unknown length: default timeout
                duration_s = 0.0
        timeout = 60 + duration_s / 20
    cmd = [ffmpeg, "-nostats", "-hide_banner", "-nostdin", "-i", filepath]
    cmd += ["-map", "0:a:0", "-af", "replaygain", "-f", "null", "-"]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Sound Check analysis of %s timed out after %.0f s", filepath, timeout)
        return None
    except OSError as e:
        logger.warning("Sound Check analysis of %s failed: %s", filepath, e)
        return None
    m = _TRACK_GAIN_RE.search(proc.stderr.decode("utf-8", "replace"))
    if proc.returncode != 0 or not m:
        logger.warning("ffmpeg could not measure the loudness of %s", filepath)
        return None
    return float(m.group(1))


def analyze_soundcheck(filepath: str, timeout: Optional[float] = None) -> Optional[int]:
    """Sound Check value measured from the audio (see analyze_gain_db), None if it could
    not be measured. Raises UnsupportedFormatError without ffmpeg."""
    gain = analyze_gain_db(filepath, timeout)
    return None if gain is None else soundcheck_from_gain(gain)


# iTunes media kind (MP4 'stik' atom) of video files -> iTunesDB media type
_STIK_VIDEO_MEDIA_TYPES = {6: 0x0020, 9: 0x0002, 10: 0x0040}  # music video, movie, TV show
_STIK_AUDIO_KINDS = {1, 2, 21}  # music, audiobook, podcast: never treated as video


def _read_mp4_video_tags(filepath: str) -> Dict[str, Any]:
    """TV show atoms and, for files with a video track, the media type (MEDIATYPE_VIDEO
    unless the 'stik' atom says music video or TV show). Audio-only files, .m4a/.m4b/.m4p
    files and files whose media kind is music/audiobook/podcast get no media_type."""
    result: Dict[str, Any] = {}
    tags = MP4(filepath).tags or {}
    for atom, key in (("tvsh", "tvshow"), ("tven", "tvepisode"), ("tvnn", "tvnetwork")):
        if tags.get(atom):
            result[key] = str(tags[atom][0])
    for atom, key in (("tvsn", "season_number"), ("tves", "episode_number")):
        if tags.get(atom):
            result[key] = _safe_int(tags[atom][0])
    stik = tags.get("stik")
    stik = _safe_int(stik[0]) if stik else 0
    audio_container = os.path.splitext(filepath)[1].lower() in (".m4a", ".m4b", ".m4p")
    if not audio_container and stik not in _STIK_AUDIO_KINDS and mp4_has_video(filepath):
        result["media_type"] = _STIK_VIDEO_MEDIA_TYPES.get(stik, 0x0002)
    return result


def _mp4_atoms(f: Any, start: int, end: int):
    """(type, payload offset, payload end) of the MP4 atoms in f[start:end]."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        payload = pos + 8
        if size == 1:  # 64-bit size follows
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            payload += 8
        elif size == 0:  # atom extends to the end
            size = end - pos
        if size < payload - pos or pos + size > end:
            return
        yield kind, payload, pos + size
        pos += size


# sample entry types of still images (chapter pictures of enhanced podcasts / audiobooks)
_STILL_IMAGE_TYPES = {b"jpeg", b"png ", b"mjpa", b"mjpb", b"gif ", b"tiff", b"bmp "}


def mp4_has_video(filepath: str) -> bool:
    """True if the MP4/M4V/MOV file has a real video track: an enabled 'vide' track that is
    not the chapter-picture track of another track ('chap' track reference, as in enhanced
    podcasts and audiobooks) and, if its samples are still images (JPEG, PNG, ...), has
    more than one of them (a Motion-JPEG video is video, a single picture is not)."""
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            for kind, start, end in _mp4_atoms(f, 0, size):
                if kind != b"moov":
                    continue
                traks = [(s, e) for k, s, e in _mp4_atoms(f, start, end) if k == b"trak"]
                chapters = set()
                for tstart, tend in traks:
                    chapters.update(_chapter_track_ids(f, tstart, tend))
                for tstart, tend in traks:
                    if _track_id(f, tstart, tend) not in chapters and _is_video_trak(
                        f, tstart, tend
                    ):
                        return True
    except OSError:
        logger.debug("Could not read %s", filepath, exc_info=True)
    return False


def _child_atom(f: Any, start: int, end: int, kind: bytes):
    for k, s, e in _mp4_atoms(f, start, end):
        if k == kind:
            return s, e
    return None


def _is_video_trak(f: Any, start: int, end: int) -> bool:
    tkhd = _child_atom(f, start, end, b"tkhd")
    if tkhd is not None:
        f.seek(tkhd[0])
        flags = f.read(4)
        if len(flags) == 4 and not flags[3] & 1:
            return False  # disabled track (e.g. chapter pictures)
    mdia = _child_atom(f, start, end, b"mdia")
    if mdia is None:
        return False
    hdlr = _child_atom(f, *mdia, b"hdlr")
    if hdlr is None or hdlr[1] - hdlr[0] < 12:
        return False
    f.seek(hdlr[0] + 8)  # version/flags, pre_defined
    if f.read(4) != b"vide":
        return False
    minf = _child_atom(f, *mdia, b"minf")
    stbl = _child_atom(f, *minf, b"stbl") if minf else None
    stsd = _child_atom(f, *stbl, b"stsd") if stbl else None
    if stsd is not None and stsd[1] - stsd[0] >= 16:
        f.seek(stsd[0] + 12)  # version/flags, entry count, first entry size
        if f.read(4) in _STILL_IMAGE_TYPES:
            return _sample_count(f, stbl) > 1  # one picture is no video, Motion-JPEG is
    return True


def _track_id(f: Any, start: int, end: int) -> Optional[int]:
    """Track ID from the 'tkhd' atom of a 'trak' (None if missing or short)."""
    tkhd = _child_atom(f, start, end, b"tkhd")
    if tkhd is None:
        return None
    f.seek(tkhd[0])
    version = f.read(1)
    offset = 20 if version == b"\x01" else 12  # after version/flags and two 8/4-byte dates
    if tkhd[1] - tkhd[0] < offset + 4:
        return None
    f.seek(tkhd[0] + offset)
    return struct.unpack(">I", f.read(4))[0]


def _chapter_track_ids(f: Any, start: int, end: int) -> List[int]:
    """IDs of the tracks a 'trak' names as its chapter track ('tref'/'chap')."""
    tref = _child_atom(f, start, end, b"tref")
    chap = _child_atom(f, *tref, b"chap") if tref else None
    if chap is None:
        return []
    f.seek(chap[0])
    data = f.read(chap[1] - chap[0])
    return [struct.unpack(">I", data[i : i + 4])[0] for i in range(0, len(data) - 3, 4)]


def _sample_count(f: Any, stbl) -> int:
    """Number of samples in an 'stbl' (its 'stsz'/'stz2' atom; 0 if unknown)."""
    for kind in (b"stsz", b"stz2"):
        atom = _child_atom(f, *stbl, kind)
        if atom is not None and atom[1] - atom[0] >= 12:
            f.seek(atom[0] + 8)  # version/flags, sample size (stz2: reserved, field size)
            return struct.unpack(">I", f.read(4))[0]
    return 0


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
            f"Convert to MP3 or AAC first, "
            f"or pass convert=True to add_track (needs ffmpeg)."
        )


class MediaInfo(NamedTuple):
    """What a media file really contains (from its content, not its extension)."""

    codec: Optional[str]  # mp3 | aac | alac | wav | aiff | flac | vorbis | opus | wma | ... | None
    sample_rate: int
    channels: int
    is_video: bool


def probe_media(filepath: str) -> MediaInfo:
    """Identify codec, sample rate and channels with mutagen (codec None: unknown / no mutagen)."""
    ext = os.path.splitext(filepath)[1].lower()
    if not HAS_MUTAGEN:
        return MediaInfo(None, 0, 0, ext in VIDEO_EXTS)
    import mutagen

    try:
        audio = mutagen.File(filepath)
    except Exception:  # noqa: BLE001 - unreadable = unknown
        logger.debug("mutagen could not read %s", filepath, exc_info=True)
        audio = None
    if audio is None:
        # mutagen ranks by extension first: FLAC data named .mp3 fails as MP3. Sniff the magic.
        return MediaInfo(_sniff_codec(filepath), 0, 0, ext in VIDEO_EXTS)
    name = type(audio).__name__
    info = audio.info
    codec: Optional[str] = {
        "MP3": "mp3",
        "EasyMP3": "mp3",
        "WAVE": "wav",
        "AIFF": "aiff",
        "FLAC": "flac",
        "OggVorbis": "vorbis",
        "OggOpus": "opus",
        "OggFLAC": "flac",
        "OggSpeex": "speex",
        "OggTheora": "theora",
        "ASF": "wma",
        "MonkeysAudio": "ape",
        "WavPack": "wavpack",
        "Musepack": "musepack",
        "TrueAudio": "tta",
        "OptimFROG": "optimfrog",
        "AAC": "aac",
        "AC3": "ac3",
        "DSF": "dsd",
        "DSDIFF": "dsd",
    }.get(name)
    if name in ("MP4", "EasyMP4"):
        c = (getattr(info, "codec", "") or "").lower()
        codec = "alac" if c == "alac" else "aac" if c.startswith("mp4a") else c or None
    return MediaInfo(
        codec,
        int(getattr(info, "sample_rate", 0) or 0),
        int(getattr(info, "channels", 0) or 0),
        ext in VIDEO_EXTS,
    )


_MAGIC = (
    (0, b"fLaC", "flac"),
    (0, b"OggS", "ogg"),
    (0, b"MAC ", "ape"),
    (0, b"wvpk", "wavpack"),
    (0, b"MPCK", "musepack"),
    (0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "wma"),
    (0, b"\x1a\x45\xdf\xa3", "matroska"),
)


def _sniff_codec(filepath: str) -> Optional[str]:
    try:
        with open(filepath, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for offset, magic, codec in _MAGIC:
        if head[offset : offset + len(magic)] == magic:
            return codec
    return None


def validate_media(filepath: str, generation=None) -> MediaInfo:
    """Raise UnsupportedFormatError unless the file plays on the iPod generation.

    Checks the content (codec, sample rate, channels) rather than the extension,
    so e.g. FLAC data in an ".mp3" file is refused. Unknown generation: codec
    rules for all iPods. Without mutagen only the extension check is done.
    """
    from .device.models import (
        MAX_CHANNELS,
        MAX_SAMPLE_RATE,
        VIDEO_GENERATIONS,
        IpodGeneration,
        supported_audio_codecs,
    )
    from .exceptions import UnsupportedFormatError

    check_format_supported(filepath, generation)
    info = probe_media(filepath)
    known = generation is not None and generation != IpodGeneration.UNKNOWN
    if info.is_video:
        if known and generation not in VIDEO_GENERATIONS:
            raise UnsupportedFormatError(f"{filepath}: this iPod does not play video")
        return info
    if info.codec is None:
        if HAS_MUTAGEN:
            raise UnsupportedFormatError(f"{filepath}: not a recognised audio file")
        return info
    allowed = supported_audio_codecs(generation if known else IpodGeneration.UNKNOWN)
    if info.codec not in allowed:
        what = "Apple Lossless" if info.codec == "alac" else info.codec.upper()
        plays = ", ".join(sorted(allowed))
        raise UnsupportedFormatError(
            f"{filepath}: {what} audio is not supported by this iPod (plays {plays})"
        )
    if info.sample_rate > MAX_SAMPLE_RATE:
        raise UnsupportedFormatError(
            f"{filepath}: sample rate {info.sample_rate} Hz > {MAX_SAMPLE_RATE} Hz"
        )
    if info.channels > MAX_CHANNELS:
        raise UnsupportedFormatError(f"{filepath}: {info.channels} channels (iPods play stereo)")
    return info


def _detect_filetype(filepath: str) -> str:
    """Detect audio file type from extension.

    Returns:
        Filetype string for MHOD type 6 and MHIT filetype marker.
    """
    # By extension, like gtkpod: an Apple Lossless .m4a is an 'AAC audio file' too (iTunes
    # says 'Apple Lossless audio file'); libgpod derives the shuffle's iTunesSD file type
    # (AAC) from this string, which the iTunes wording would turn into MP3.
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
