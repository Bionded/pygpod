"""Chapter markers of audiobooks and podcasts, read from the media file.

Sources, first one found wins:

- MP4/M4A/M4B: the QuickTime chapter text track (``tref/chap``, what iTunes and ffmpeg
  write), else the Nero ``moov/udta/chpl`` atom. Stdlib only.
- MP3: ID3v2 ``CHAP`` frames (title from the ``TIT2`` sub-frame) in the order of the
  top-level ``CTOC`` frame, else by start time. Needs mutagen.

The result is a list of ``(start_ms, title)`` tuples, which ``Database.add_track`` turns
into the track's chapter data (MHOD 17, like libgpod's itdb_chapterdata).
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Any, List, Optional, Tuple

from .gapless import _atoms, _child, _mapped

logger = logging.getLogger(__name__)

ChapterList = List[Tuple[int, str]]

_MP4_EXTS = {".m4a", ".m4b", ".m4p", ".mp4", ".m4v", ".mov", ".aac"}


def read_chapters(filepath: str) -> ChapterList:
    """Chapters of a media file as ``[(start_ms, title), ...]`` ([] when it has none).

    Files that cannot be read give [] (a warning is logged only for unexpected errors).
    """
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext in _MP4_EXTS:
            with _mapped(filepath) as data:
                return _mp4_chapters(data)
        if ext == ".mp3":
            return _id3_chapters(filepath)
    except (OSError, ValueError, struct.error, IndexError):
        logger.debug("No chapters read from %s", filepath, exc_info=True)
    except Exception:  # noqa: BLE001 - a broken tag must not stop add_track
        logger.warning("Failed to read chapters from %s", filepath, exc_info=True)
    return []


# ---- MP4 ----


def _mp4_chapters(data: Any) -> ChapterList:
    moov = _child(data, 0, len(data), b"moov")
    if moov is None:
        return []
    chapters = _quicktime_chapters(data, moov)
    if not chapters:
        udta = _child(data, *moov, b"udta")
        chpl = _child(data, *udta, b"chpl") if udta else None
        if chpl is not None:
            chapters = _nero_chapters(bytes(data[chpl[0] : chpl[1]]))
    return chapters


def _nero_chapters(payload: bytes) -> ChapterList:
    """Nero 'chpl': version, flags(3), [reserved(4) in version 1], count, then per chapter
    start (u64, 100 ns units) and a length-prefixed UTF-8 title."""
    pos = 8 if payload[0] >= 1 else 4
    count = payload[pos]
    pos += 1
    chapters = []
    for _ in range(count):
        start = struct.unpack_from(">Q", payload, pos)[0]
        length = payload[pos + 8]
        title = payload[pos + 9 : pos + 9 + length].decode("utf-8", "replace")
        pos += 9 + length
        chapters.append((start // 10000, title))
    return chapters


def _quicktime_chapters(data: Any, moov: Tuple[int, int]) -> ChapterList:
    traks = {}
    chapter_ids: List[int] = []
    for kind, start, end in _atoms(data, *moov):
        if kind != b"trak":
            continue
        tkhd = _child(data, start, end, b"tkhd")
        if tkhd is None:
            continue
        version = data[tkhd[0]]
        traks[struct.unpack_from(">I", data, tkhd[0] + (20 if version == 1 else 12))[0]] = (
            start,
            end,
        )
        tref = _child(data, start, end, b"tref")
        chap = _child(data, *tref, b"chap") if tref else None
        if chap is not None and not chapter_ids:
            chapter_ids = [
                struct.unpack_from(">I", data, p)[0] for p in range(chap[0], chap[1] - 3, 4)
            ]
    for track_id in chapter_ids:
        if track_id in traks:
            chapters = _text_track_samples(data, *traks[track_id])
            if chapters:
                return chapters
    return []


def _text_track_samples(data: Any, start: int, end: int) -> ChapterList:
    """(start_ms, text) of every sample of a QuickTime text track."""
    mdia = _child(data, start, end, b"mdia")
    mdhd = _child(data, *mdia, b"mdhd") if mdia else None
    minf = _child(data, *mdia, b"minf") if mdia else None
    stbl = _child(data, *minf, b"stbl") if minf else None
    if mdhd is None or stbl is None:
        return []
    version = data[mdhd[0]]
    timescale = struct.unpack_from(">I", data, mdhd[0] + (20 if version == 1 else 12))[0]
    if not timescale:
        return []

    starts = []
    stts = _child(data, *stbl, b"stts")
    if stts is None:
        return []
    time = 0
    (count,) = struct.unpack_from(">I", data, stts[0] + 4)
    for i in range(count):
        n, delta = struct.unpack_from(">II", data, stts[0] + 8 + 8 * i)
        for _ in range(n):
            starts.append(time)
            time += delta

    stsz = _child(data, *stbl, b"stsz")
    if stsz is None:
        return []
    fixed, n_samples = struct.unpack_from(">II", data, stsz[0] + 4)
    sizes = [
        fixed or struct.unpack_from(">I", data, stsz[0] + 12 + 4 * i)[0] for i in range(n_samples)
    ]

    offsets = _chunk_offsets(data, stbl)
    stsc = _child(data, *stbl, b"stsc")
    if stsc is None or not offsets:
        return []
    (n_entries,) = struct.unpack_from(">I", data, stsc[0] + 4)
    runs = [struct.unpack_from(">III", data, stsc[0] + 8 + 12 * i)[:2] for i in range(n_entries)]

    chapters = []
    sample = 0
    for chunk, offset in enumerate(offsets, 1):
        per_chunk = 0
        for first, samples in runs:
            if first <= chunk:
                per_chunk = samples
        for _ in range(per_chunk):
            if sample >= min(n_samples, len(starts)):
                break
            chapters.append(
                (starts[sample] * 1000 // timescale, _text_sample(data, offset, sizes[sample]))
            )
            offset += sizes[sample]
            sample += 1
    return chapters


def _chunk_offsets(data: Any, stbl: Tuple[int, int]) -> List[int]:
    stco = _child(data, *stbl, b"stco")
    if stco is not None:
        (count,) = struct.unpack_from(">I", data, stco[0] + 4)
        return [struct.unpack_from(">I", data, stco[0] + 8 + 4 * i)[0] for i in range(count)]
    co64 = _child(data, *stbl, b"co64")
    if co64 is not None:
        (count,) = struct.unpack_from(">I", data, co64[0] + 4)
        return [struct.unpack_from(">Q", data, co64[0] + 8 + 8 * i)[0] for i in range(count)]
    return []


def _text_sample(data: Any, offset: int, size: int) -> str:
    """A QuickTime text sample: u16 length + UTF-8 (or UTF-16 with a BOM) text."""
    if size < 2:
        return ""
    (length,) = struct.unpack_from(">H", data, offset)
    raw = bytes(data[offset + 2 : offset + 2 + min(length, size - 2)])
    if raw[:2] in (b"\xfe\xff", b"\xff\xfe"):
        return raw.decode("utf-16", "replace")
    return raw.decode("utf-8", "replace")


# ---- MP3 (ID3v2 CHAP / CTOC) ----


def _id3_chapters(filepath: str) -> ChapterList:
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
    except ImportError:
        return []
    try:
        tags = ID3(filepath)
    except ID3NoHeaderError:
        return []
    chaps = {frame.element_id: frame for frame in tags.getall("CHAP")}
    if not chaps:
        return []
    order: Optional[List[str]] = None
    for toc in tags.getall("CTOC"):
        if toc.flags & 0x02:  # top-level table of contents
            order = [cid for cid in toc.child_element_ids if cid in chaps]
            break
    if not order:
        order = sorted(chaps, key=lambda cid: chaps[cid].start_time)
    chapters = []
    for cid in order:
        frame = chaps[cid]
        title_frames = frame.sub_frames.getall("TIT2")
        title = str(title_frames[0].text[0]) if title_frames and title_frames[0].text else ""
        chapters.append((int(frame.start_time), title))
    return chapters
