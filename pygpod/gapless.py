"""Gapless playback data for the iTunesDB (pregap, sample count, postgap, gapless_data).

MP3 follows gtkpod (``mp3_read_lame_tag`` / ``mp3_get_track_gapless``): the encoder delay and
padding come from the LAME tag in the Xing/Info frame (its CRC must match), the sample count
from the number of frames, and ``gapless_data`` is the byte count from the Xing frame up to
the last eight frames. AAC/ALAC in MP4 use the ``iTunSMPB`` atom, else the audio track's edit
list; ALAC without either (e.g. from ffmpeg) has no encoder delay or padding, so its sample
count is the media duration. Files without such data get nothing (all fields stay 0).
"""

from __future__ import annotations

import contextlib
import logging
import mmap
import os
import struct
from typing import Any, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

# MHIT fields written by add_track: name -> (offset, size)
GAPLESS_FIELDS = {
    "pregap": (0xB8, 4),
    "sample_count": (0xBC, 8),
    "postgap": (0xC8, 4),
    "gapless_data": (0xF8, 4),
    "gapless_track_flag": (0x100, 2),
    "gapless_album_flag": (0x102, 2),
}

# Layer III only (iPods play no MP1/MP2)
_BITRATES = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),  # MPEG 1
    0: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG 2 / 2.5
}
_FREQS = {1: (44100, 48000, 32000), 0: (22050, 24000, 16000), 2: (11025, 12000, 8000)}
_MIN_FRAME = 21
_CONSECUTIVE = 4
_RESYNC_LIMIT = 64 * 1024  # bytes searched for the next frame after a damaged spot
# tags that may follow the audio frames (ID3v1, APEv2, Lyrics3, appended ID3v2)
_TRAILING_TAGS = (b"TAG", b"APETAGEX", b"LYRICS", b"ID3", b"3DI")
_LAME_CRC_SPAN = 190
_FRAMES_FLAG, _BYTES_FLAG, _TOC_FLAG, _SCALE_FLAG = 1, 2, 4, 8


@contextlib.contextmanager
def _mapped(filepath: str) -> Iterator[Any]:
    """The file's bytes as a read-only mmap (b"" for an empty file)."""
    with open(filepath, "rb") as f:
        if os.fstat(f.fileno()).st_size == 0:
            yield b""
            return
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            yield m
        finally:
            m.close()


def _crc16(data: bytes) -> int:
    """CRC-16/ARC (reflected 0x8005, init 0), as LAME uses for its info tag."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class _Frame:
    __slots__ = ("version", "bitrate_index", "freq_index", "mode", "length", "constants")

    def __init__(self, head: bytes):
        b1, b2, b3 = head[1], head[2], head[3]
        self.version = (b1 >> 3) & 1 if b1 & 0x10 else 2  # 1 MPEG1, 0 MPEG2, 2 MPEG2.5
        self.bitrate_index = b2 >> 4
        self.freq_index = (b2 >> 2) & 3
        self.mode = b3 >> 6
        self.length = 0
        if head[0] != 0xFF or (b1 & 0xE0) != 0xE0 or ((b1 >> 1) & 3) != 1:
            return  # no sync or not Layer III
        if self.bitrate_index in (0, 15) or self.freq_index == 3:
            return
        kbps = _BITRATES[self.version & 1][self.bitrate_index]
        freq = _FREQS[self.version][self.freq_index]
        scale = 144000 if self.version == 1 else 72000
        length = int(scale * kbps / freq) + ((b2 >> 1) & 1)
        self.length = length if length >= _MIN_FRAME else 0
        # what stays the same from frame to frame: version, layer, sample rate, mono or not
        # (ffmpeg's Xing frame differs from the music frames in the "original" bit)
        self.constants = (b1 & 0xFE, self.freq_index, self.mode == 3)

    @property
    def samples(self) -> int:
        return 1152 if self.version == 1 else 576


def _frame_at(data: bytes, pos: int) -> Optional[_Frame]:
    if pos + 4 > len(data):
        return None
    f = _Frame(data[pos : pos + 4])
    return f if f.length else None


def _first_frame(data: bytes) -> int:
    """Offset of the first frame followed by 3 more with the same constants, or -1."""
    pos = 0
    if data[:3] == b"ID3" and len(data) >= 10:  # skip an ID3v2 tag (syncsafe size)
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        pos = 10 + size + (10 if data[5] & 0x10 else 0)
    while True:
        pos = data.find(b"\xff", pos)
        if pos < 0:
            return -1
        first = _frame_at(data, pos)
        if first is not None:
            nxt, k = pos + first.length, 1
            while k < _CONSECUTIVE:
                f = _frame_at(data, nxt)
                if f is None or f.constants != first.constants:
                    break
                nxt += f.length
                k += 1
            if k == _CONSECUTIVE:
                return pos
        pos += 1


def _resync(data: Any, pos: int, constants: tuple) -> int:
    """Offset of the next frame with ``constants`` after a damaged spot at ``pos``, like a
    decoder resyncs: it must be followed by more such frames (or end the file). -1 if none is
    found within _RESYNC_LIMIT bytes."""
    end = min(len(data), pos + _RESYNC_LIMIT)
    while True:
        pos = data.find(b"\xff", pos, end)
        if pos < 0:
            return -1
        nxt, k = pos, 0
        while k < _CONSECUTIVE:
            f = _frame_at(data, nxt)
            if f is None or f.constants != constants:
                break
            nxt += f.length
            k += 1
        if k == _CONSECUTIVE or (k and nxt == len(data)):
            return pos
        pos += 1


def _frames(data: Any, pos: int, constants: tuple) -> Iterator[Tuple[int, int]]:
    """(offset, length) of the frames with ``constants`` from ``pos`` to the end of the audio.
    A damaged spot (bytes that are no such frame) is skipped up to the next valid frame; the
    walk ends at the end of the file, at a trailing tag, or where no frame follows."""
    while pos < len(data):
        f = _frame_at(data, pos)
        if f is not None and f.constants == constants:
            yield pos, f.length
            pos += f.length
            continue
        if any(data[pos : pos + len(t)] == t for t in _TRAILING_TAGS):
            return
        nxt = _resync(data, pos + 1, constants)
        if nxt < 0:
            return
        logger.debug("MP3: %d damaged bytes at offset %d skipped", nxt - pos, pos)
        pos = nxt


def read_mp3_gapless(filepath: str) -> Optional[Dict[str, int]]:
    """Gapless fields of a LAME-encoded MP3 (gtkpod's method), None without valid LAME data."""
    try:
        with _mapped(filepath) as data:
            return _mp3_gapless(filepath, data)
    except (OSError, ValueError):
        return None


def _mp3_gapless(filepath: str, data: Any) -> Optional[Dict[str, int]]:
    start = _first_frame(data)
    if start < 0:
        return None
    xing = _frame_at(data, start)
    side = (17 if xing.mode == 3 else 32) if xing.version == 1 else (9 if xing.mode == 3 else 17)
    pos = start + 4 + side
    if data[pos : pos + 4] not in (b"Xing", b"Info") or pos + 8 > len(data):
        return None
    flags = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
    pos += 8
    pos += 4 * bool(flags & _FRAMES_FLAG) + 4 * bool(flags & _BYTES_FLAG)
    pos += 100 * bool(flags & _TOC_FLAG) + 4 * bool(flags & _SCALE_FLAG)
    lame = data[pos : pos + 0x24]
    # LAME, or ffmpeg's libmp3lame muxer: same info tag layout with "Lavc"/"Lavf"
    if len(lame) < 0x24 or lame[:4] not in (b"LAME", b"Lavc", b"Lavf"):
        return None
    # tag CRC: LAME covers the frame up to the CRC field, gtkpod/ffmpeg a fixed 190 bytes
    # (the same for MPEG-1 stereo). ffmpeg (mp3enc.c) computes it over its frame buffer before
    # it stores it: for mono MPEG-1 and MPEG-2/2.5 the field lies inside those 190 bytes and
    # counts as zero, and a frame shorter than 190 bytes is padded with zeros.
    stored = struct.unpack(">H", lame[0x22:0x24])[0]
    crc_at = pos + 0x22 - start
    candidates = [data[start : start + crc_at], data[start : start + _LAME_CRC_SPAN]]
    if crc_at + 2 <= _LAME_CRC_SPAN:
        frame = bytes(data[start : start + min(xing.length, _LAME_CRC_SPAN)])
        frame = frame[:crc_at] + b"\0\0" + frame[crc_at + 2 :]
        candidates.append(frame.ljust(_LAME_CRC_SPAN, b"\0"))
    if all(_crc16(c) != stored for c in candidates):
        logger.debug("%s: LAME tag CRC mismatch", filepath)
        return None
    pregap = (lame[0x15] << 4) | (lame[0x16] >> 4)
    postgap = ((lame[0x16] & 0x0F) << 8) | lame[0x17]

    # walk the frames after the Xing frame
    total = xing.length
    frames = 0
    last = [0] * 8
    bitrates = {xing.bitrate_index}
    for pos, length in _frames(data, start + xing.length, xing.constants):
        last[frames % 8] = length
        total += length
        frames += 1
        bitrates.add(_Frame(data[pos : pos + 4]).bitrate_index)
    if len(bitrates) == 1:
        frames += 1  # CBR: iTunes counts one frame more (gtkpod, 2007)
    result = {
        "pregap": pregap,
        "postgap": postgap,
        "sample_count": frames * xing.samples - pregap - postgap,
        "gapless_data": total - sum(last),
    }
    if not all(v > 0 for v in result.values()):
        return None
    result["gapless_track_flag"] = 1
    return result


def _atoms(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        head = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            return
        yield kind, pos + head, pos + size
        pos += size


def _child(data: bytes, start: int, end: int, kind: bytes):
    for k, s, e in _atoms(data, start, end):
        if k == kind:
            return s, e
    return None


def _mp4_audio(data: bytes) -> Optional[Dict[str, Any]]:
    """The first audio track of an MP4: codec (sample entry type), sample rate, timescales,
    stts (count, delta) runs and the edit list's (segment duration, media time) entries."""
    moov = _child(data, 0, len(data), b"moov")
    if moov is None:
        return None
    mvhd = _child(data, *moov, b"mvhd")
    if mvhd is None:
        return None
    v = data[mvhd[0]]
    movie_scale = struct.unpack(">I", data[mvhd[0] + (20 if v else 12) :][:4])[0]
    for kind, ts, te in _atoms(data, *moov):
        if kind != b"trak":
            continue
        mdia = _child(data, ts, te, b"mdia")
        hdlr = _child(data, *mdia, b"hdlr") if mdia else None
        if not hdlr or data[hdlr[0] + 8 : hdlr[0] + 12] != b"soun":
            continue
        mdhd = _child(data, *mdia, b"mdhd")
        minf = _child(data, *mdia, b"minf")
        stbl = _child(data, *minf, b"stbl") if minf else None
        stts = _child(data, *stbl, b"stts") if stbl else None
        stsd = _child(data, *stbl, b"stsd") if stbl else None
        if not mdhd or not stts or not stsd or stsd[0] + 44 > stsd[1]:
            return None
        v = data[mdhd[0]]
        media_scale = struct.unpack(">I", data[mdhd[0] + (20 if v else 12) :][:4])[0]
        entry = stsd[0] + 8  # first sample entry (audio): type at 4, rate (16.16) at 32
        codec = bytes(data[entry + 4 : entry + 8])
        rate = struct.unpack(">I", data[entry + 32 : entry + 36])[0] >> 16
        n = struct.unpack(">I", data[stts[0] + 4 : stts[0] + 8])[0]
        runs = []
        for i in range(n):
            pos = stts[0] + 8 + 8 * i
            if pos + 8 > stts[1]:
                return None
            runs.append(struct.unpack(">II", data[pos : pos + 8]))
        edits = []
        edts = _child(data, ts, te, b"edts")
        elst = _child(data, *edts, b"elst") if edts else None
        if elst:
            v = data[elst[0]]
            count = struct.unpack(">I", data[elst[0] + 4 : elst[0] + 8])[0]
            fmt, size = (">QqI", 20) if v else (">IiI", 12)
            pos = elst[0] + 8
            for _ in range(count):
                if pos + size > elst[1]:
                    return None
                seg, media_time, _rate = struct.unpack(fmt, data[pos : pos + size])
                edits.append((seg, media_time))
                pos += size
        return {
            "codec": codec,
            "rate": rate,
            "movie_scale": movie_scale,
            "media_scale": media_scale,
            "stts": runs,
            "edits": edits,
        }
    return None


def _mp4_edit_list_gapless(data: bytes, audio=None) -> Optional[Dict[str, int]]:
    """pregap/samples/postgap from the first audio track's edit list and media duration."""
    audio = audio or _mp4_audio(data)
    if audio is None or not audio["edits"] or not audio["movie_scale"]:
        return None
    # decoded length = packets x frame length (ffmpeg shortens the last packet's stts
    # duration to the real end, so the media duration does not include the padding)
    packets = sum(cnt for cnt, _ in audio["stts"])
    frame_len = max((delta for _, delta in audio["stts"]), default=0)
    duration = packets * frame_len
    media = sum(cnt * delta for cnt, delta in audio["stts"])
    tick = -(-audio["media_scale"] // audio["movie_scale"])  # one movie time unit in samples
    for seg, media_time in audio["edits"]:
        if media_time < 0:
            continue  # empty edit
        samples = (seg * audio["media_scale"] + audio["movie_scale"] // 2) // audio["movie_scale"]
        # the segment is rounded to the movie timescale (often ms); the media duration ends at
        # the real last sample when the muxer shortened the last packet: exact if it agrees
        if 0 < media - media_time and abs(media - media_time - samples) <= tick:
            samples = media - media_time
        postgap = duration - media_time - samples
        if media_time > 0 and samples > 0 and postgap >= 0:
            return {"pregap": media_time, "sample_count": samples, "postgap": postgap}
        return None
    return None


def _alac_gapless(audio) -> Optional[Dict[str, int]]:
    """ALAC is lossless without encoder delay or padding: the sample count is the media
    duration (sum of the stts runs, in samples)."""
    if audio is None or audio["codec"] != b"alac":
        return None
    samples = sum(cnt * delta for cnt, delta in audio["stts"])
    if audio["rate"] and audio["media_scale"] and audio["rate"] != audio["media_scale"]:
        samples = samples * audio["rate"] // audio["media_scale"]
    if samples <= 0:
        return None
    return {"pregap": 0, "sample_count": samples, "postgap": 0}


def read_mp4_gapless(filepath: str) -> Optional[Dict[str, int]]:
    """Gapless fields of an AAC/ALAC MP4 (iTunSMPB, else the edit list; ALAC without them:
    no delay or padding, the media duration as sample count) and the pgap atom."""
    from mutagen.mp4 import MP4

    try:
        tags = MP4(filepath).tags or {}
    except Exception:  # noqa: BLE001 - not a readable MP4
        return None
    result: Dict[str, int] = {}
    smpb = tags.get("----:com.apple.iTunes:iTunSMPB")
    values = bytes(smpb[0]).decode("ascii", "replace").split() if smpb else []
    try:
        if len(values) >= 4:
            pregap, postgap, samples = int(values[1], 16), int(values[2], 16), int(values[3], 16)
            if samples > 0:
                result = {"pregap": pregap, "postgap": postgap, "sample_count": samples}
    except ValueError:
        logger.debug("bad iTunSMPB in %s: %r", filepath, values)
    if not result:
        try:
            with _mapped(filepath) as data:
                audio = _mp4_audio(data)
                result = _mp4_edit_list_gapless(data, audio) or _alac_gapless(audio) or {}
        except (OSError, ValueError, struct.error, IndexError, TypeError):
            logger.debug("no edit list in %s", filepath, exc_info=True)
            result = {}
    if result:
        result["gapless_data"] = 0  # libgpod: may be zero for AAC
        result["gapless_track_flag"] = 1
    if tags.get("pgap"):
        result["gapless_album_flag"] = 1
    return result or None


# ---- Estimate for MP3s without a LAME/Info tag (experimental) ----

# LAME's encoder delay as its info tag stores it (and gtkpod/iTunes use it as the pregap)
_ENCODER_DELAY = 576
# MP3 decoder delay: decoded output starts this many samples late, so the decoded trailing
# silence is the encoder padding minus this
_DECODER_DELAY = 529
_SILENCE = 1  # |sample| <= 1 of 32767: below -90 dBFS
_TAIL_FRAMES = 3  # frames whose decoded PCM is searched for the trailing padding
# decoded before them: at least this many frames and bytes (bit reservoir up to 511 bytes)
_LEAD_FRAMES, _LEAD_BYTES = 8, 2048


def estimate_mp3_gapless(filepath: str, use_ffmpeg: bool = True) -> Optional[Dict[str, int]]:
    """Estimated gapless fields of an MP3 without a valid LAME/Info tag (experimental).

    The frames are counted (sample total and ``gapless_data`` as for tagged files), the
    encoder delay is assumed to be LAME's (576 samples, the value its info tag stores; other
    encoders publish no delay) and the padding at the end is measured by decoding only the
    last frames with ffmpeg: the run of samples below -90 dBFS (at most two frames) plus
    the 529-sample decoder delay.
    Without ffmpeg (or ``use_ffmpeg=False``) the padding is taken as 0 and
    ``gapless_track_flag`` is not set, because the estimate is incomplete.

    Returns:
        The gapless fields, or None for non-MP3 files, files with a valid LAME/Info tag
        (use read_mp3_gapless) and files without MPEG audio frames.
    """
    if os.path.splitext(filepath)[1].lower() != ".mp3" or read_mp3_gapless(filepath):
        return None
    try:
        with _mapped(filepath) as data:
            scan = _scan_frames(data)
            if scan is None:
                return None
            frames, spf, total, tail_start = scan
            tail = bytes(data[tail_start:])
    except (OSError, ValueError):
        return None
    silence = _measure_padding(tail, spf) if use_ffmpeg else None
    postgap = None if silence is None else silence + _DECODER_DELAY
    result = {
        "pregap": _ENCODER_DELAY,
        "postgap": postgap or 0,
        "sample_count": frames * spf - _ENCODER_DELAY - (postgap or 0),
        "gapless_data": total,
    }
    if result["sample_count"] <= 0:
        return None
    if postgap is not None:
        result["gapless_track_flag"] = 1
    logger.info("Gapless data of %s estimated (no LAME tag): %s", filepath, result)
    return result


# AAC encoder delay when the file does not tell: ffmpeg's own AAC encoder primes 1024 samples,
# Apple's (and most others) 2112
_AAC_DELAY_FFMPEG, _AAC_DELAY = 1024, 2112


def estimate_mp4_gapless(filepath: str) -> Optional[Dict[str, int]]:
    """Estimated gapless fields of an AAC MP4 without iTunSMPB or edit list (experimental).

    The encoder delay is assumed: 1024 samples for files written by ffmpeg (``\xa9too``
    "Lavf...", its native AAC encoder), else 2112 (Apple's). The padding is what the last
    packet's stts duration leaves out of a whole frame (ffmpeg shortens it to the real end);
    only then is ``gapless_track_flag`` set, else the padding is unknown and taken as 0.

    Returns:
        The gapless fields, or None for other files, files with exact data (read_mp4_gapless)
        and ALAC.
    """
    if os.path.splitext(filepath)[1].lower() not in (".m4a", ".m4b", ".mp4", ".aac"):
        return None
    if read_mp4_gapless(filepath):
        return None
    try:
        from mutagen.mp4 import MP4

        tool = str((MP4(filepath).tags or {}).get("\xa9too", [""])[0])
        with _mapped(filepath) as data:
            audio = _mp4_audio(data)
    except Exception:  # noqa: BLE001 - not a readable MP4
        return None
    if audio is None or audio["codec"] != b"mp4a" or not audio["stts"]:
        return None
    frame_len = max(delta for _, delta in audio["stts"])
    packets = sum(cnt for cnt, _ in audio["stts"])
    media = sum(cnt * delta for cnt, delta in audio["stts"])
    pregap = _AAC_DELAY_FFMPEG if tool.startswith("Lavf") else _AAC_DELAY
    postgap = packets * frame_len - media
    result = {"pregap": pregap, "postgap": postgap, "sample_count": media - pregap}
    if result["sample_count"] <= 0:
        return None
    result["gapless_data"] = 0
    if postgap > 0:
        result["gapless_track_flag"] = 1
    logger.info("Gapless data of %s estimated (no iTunSMPB/edit list): %s", filepath, result)
    return result


def estimate_gapless(filepath: str, use_ffmpeg: bool = True) -> Optional[Dict[str, int]]:
    """estimate_mp3_gapless or estimate_mp4_gapless, by the file's extension."""
    if os.path.splitext(filepath)[1].lower() == ".mp3":
        return estimate_mp3_gapless(filepath, use_ffmpeg)
    try:
        import mutagen  # noqa: F401
    except ImportError:
        return None
    return estimate_mp4_gapless(filepath)


def _scan_frames(data: Any):
    """(audio frames, samples per frame, gapless_data, byte offset of the decode tail)."""
    start = _first_frame(data)
    if start < 0:
        return None
    first = _frame_at(data, start)
    spf = first.samples
    side = (17 if first.mode == 3 else 32) if first.version == 1 else (9 if first.mode == 3 else 17)
    xing = data[start + 4 + side : start + 8 + side] in (b"Xing", b"Info")
    offsets = list(_frames(data, start, first.constants))
    if xing:
        offsets = offsets[1:]  # the tag frame decodes to nothing
    if not offsets:
        return None
    total = sum(length for _, length in offsets)
    gapless_data = total - sum(length for _, length in offsets[-8:])
    tail_index = len(offsets) - _TAIL_FRAMES
    lead_bytes = 0
    while tail_index > 0 and (
        len(offsets) - _TAIL_FRAMES - tail_index < _LEAD_FRAMES or lead_bytes < _LEAD_BYTES
    ):
        tail_index -= 1
        lead_bytes += offsets[tail_index][1]
    return len(offsets), spf, gapless_data, offsets[max(0, tail_index)][0]


def mp3_stream_info(filepath: str) -> Optional[Dict[str, int]]:
    """Duration and average bitrate of an MP3 from a scan of all its frames.

    For MP3s without a Xing/Info/VBRI header, where a length estimated from the first frame's
    bitrate is wrong for VBR files. A Xing/Info frame, if any, is not counted.

    Returns:
        ``{"duration_ms": ..., "bitrate": kbps, "samplerate": Hz}``, or None for files without
        MPEG audio frames.
    """
    try:
        with _mapped(filepath) as data:
            start = _first_frame(data)
            if start < 0:
                return None
            first = _frame_at(data, start)
            side = (
                (17 if first.mode == 3 else 32)
                if first.version == 1
                else (9 if first.mode == 3 else 17)
            )
            pos = start
            if data[start + 4 + side : start + 8 + side] in (b"Xing", b"Info"):
                pos += first.length  # the tag frame decodes to nothing
            frames = total = 0
            for _, length in _frames(data, pos, first.constants):
                frames += 1
                total += length
    except (OSError, ValueError):
        return None
    rate = _FREQS[first.version][first.freq_index]
    duration_ms = frames * first.samples * 1000 // rate
    if duration_ms <= 0:
        return None
    return {
        "duration_ms": duration_ms,
        "bitrate": round(total * 8 / duration_ms),
        "samplerate": rate,
    }


def _measure_padding(tail: bytes, spf: int) -> Optional[int]:
    """Trailing silent samples in the decoded end of the stream (capped at 2 frames), None
    without ffmpeg or when decoding fails."""
    import subprocess

    from .convert import find_ffmpeg
    from .exceptions import UnsupportedFormatError

    try:
        ffmpeg = find_ffmpeg()
    except UnsupportedFormatError:
        return None
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-f", "mp3", "-i", "pipe:0"]
    cmd += ["-map", "0:a:0", "-ac", "1", "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=tail, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        logger.debug("ffmpeg could not decode the end of the stream", exc_info=True)
        return None
    pcm = proc.stdout
    if proc.returncode != 0 or len(pcm) < 2 * spf:
        return None
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm[: len(pcm) // 2 * 2])
    limit = min(2 * spf, len(samples))
    run = 0
    while run < limit and abs(samples[-1 - run]) <= _SILENCE:
        run += 1
    return run


def read_gapless(filepath: str) -> Optional[Dict[str, int]]:
    """Gapless MHIT fields of an audio file (keys of GAPLESS_FIELDS), None without data."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".mp3":
        return read_mp3_gapless(filepath)
    if ext in (".m4a", ".m4b", ".m4p", ".mp4", ".aac"):
        try:
            import mutagen  # noqa: F401
        except ImportError:
            return None
        return read_mp4_gapless(filepath)
    return None


def apply_gapless_fields(header: bytearray, fields: Dict[str, Any]) -> None:
    """Write the gapless values present in ``fields`` into an MHIT header."""
    for name, (offset, size) in GAPLESS_FIELDS.items():
        if name in fields and len(header) >= offset + size:
            header[offset : offset + size] = int(fields[name]).to_bytes(size, "little")
