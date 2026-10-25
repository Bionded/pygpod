"""Chapter data support for audiobooks and podcasts.

Ported from libgpod's itdb_chapterdata.c. Chapters are stored as MHOD type 17
(CHAPTERDATA) attached to track MHIT records. Each chapter has a title and
a start position in milliseconds.

Binary format of MHOD type 17 body:
  - 4 bytes: unknown (usually 0x0C000000)
  - 4 bytes: unknown
  - 4 bytes: unknown
  - 4 bytes: number of chapters
  Then for each chapter:
  - 4 bytes: start position in ms (LE)
  - 4 bytes: string length in bytes (LE)
  - N bytes: chapter title (UTF-8)
"""

from __future__ import annotations

import logging
import struct
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class Chapter:
    """A single chapter marker in an audiobook or podcast."""

    __slots__ = ("title", "startpos")

    def __init__(self, title: str = "", startpos: int = 0) -> None:
        """Create a chapter.

        Args:
            title: Chapter title.
            startpos: Start position in milliseconds.
                      First chapter should have startpos=1 (not 0).
        """
        self.title = title
        self.startpos = startpos

    def duplicate(self) -> "Chapter":
        """Create a deep copy of this chapter."""
        return Chapter(title=self.title, startpos=self.startpos)

    def __repr__(self) -> str:
        ms = self.startpos
        secs = ms // 1000
        mins = secs // 60
        return f"<Chapter '{self.title}' at {mins}:{secs % 60:02d}>"


class ChapterData:
    """Container for chapter data associated with a track."""

    def __init__(self) -> None:
        self.chapters: List[Chapter] = []
        self.unk024: int = 0
        self.unk028: int = 0
        self.unk032: int = 0

    def add_chapter(self, title: str, startpos: int) -> Chapter:
        """Add a chapter.

        The first chapter must have startpos >= 1. If this is the first
        chapter and startpos is 0, it will be set to 1.

        Args:
            title: Chapter title.
            startpos: Start position in milliseconds.

        Returns:
            The new Chapter.
        """
        if not self.chapters and startpos == 0:
            startpos = 1  # iPod requires first chapter startpos >= 1

        ch = Chapter(title=title, startpos=startpos)
        self.chapters.append(ch)
        return ch

    def remove_chapter(self, chapter: Chapter) -> bool:
        """Remove a chapter.

        Args:
            chapter: Chapter to remove.

        Returns:
            True if the chapter was found and removed.
        """
        try:
            self.chapters.remove(chapter)
            return True
        except ValueError:
            return False

    def remove_all_chapters(self) -> None:
        """Remove all chapters."""
        self.chapters.clear()

    def duplicate(self) -> "ChapterData":
        """Create a deep copy of this chapter data."""
        cd = ChapterData()
        cd.unk024 = self.unk024
        cd.unk028 = self.unk028
        cd.unk032 = self.unk032
        cd.chapters = [ch.duplicate() for ch in self.chapters]
        return cd

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    def __repr__(self) -> str:
        return f"<ChapterData {self.chapter_count} chapters>"

    def __len__(self) -> int:
        return self.chapter_count

    def __iter__(self):
        return iter(self.chapters)

    def to_list(self) -> List[tuple]:
        """The chapters as ``[(startpos_ms, title), ...]``."""
        return [(ch.startpos, ch.title) for ch in self.chapters]


def chapter_data_from(value: Any) -> Optional[ChapterData]:
    """A ChapterData from a ChapterData (copied), an iterable of ``(start_ms, title)``
    tuples or Chapter objects, or None (-> None).

    Chapters are kept in the given order; a first chapter at 0 ms moves to 1 ms.
    """
    if value is None:
        return None
    if isinstance(value, ChapterData):
        cd = value.duplicate()
        if cd.chapters and cd.chapters[0].startpos == 0:
            cd.chapters[0].startpos = 1
        return cd
    cd = ChapterData()
    for item in value:
        if isinstance(item, Chapter):
            cd.add_chapter(item.title, item.startpos)
        else:
            start, title = item
            cd.add_chapter(str(title), int(start))
    return cd


def parse_chapter_data(data: bytes) -> Optional[ChapterData]:
    """Parse chapter data from MHOD type 17 body.

    Args:
        data: Raw body bytes of the MHOD type 17 record.

    Returns:
        ChapterData, or None if data is invalid.
    """
    if len(data) < 16:
        return None

    cd = ChapterData()
    cd.unk024 = struct.unpack_from("<I", data, 0)[0]
    cd.unk028 = struct.unpack_from("<I", data, 4)[0]
    cd.unk032 = struct.unpack_from("<I", data, 8)[0]
    num_chapters = struct.unpack_from("<I", data, 12)[0]

    offset = 16
    for _ in range(num_chapters):
        if offset + 8 > len(data):
            break

        startpos = struct.unpack_from("<I", data, offset)[0]
        str_len = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8

        title = ""
        if str_len > 0 and offset + str_len <= len(data):
            try:
                title = data[offset : offset + str_len].decode("utf-8")
            except UnicodeDecodeError:
                logger.debug("Chapter title decode failed at offset %d, using replacement", offset)
                title = data[offset : offset + str_len].decode("utf-8", errors="replace")
            offset += str_len

        cd.chapters.append(Chapter(title=title, startpos=startpos))

    return cd


def write_chapter_data(cd: ChapterData) -> bytes:
    """Write chapter data to compact binary format for MHOD type 17 body.

    Args:
        cd: ChapterData to serialize.

    Returns:
        Raw bytes for the MHOD type 17 body.
    """
    buf = bytearray()

    # Header
    buf.extend(struct.pack("<I", cd.unk024))
    buf.extend(struct.pack("<I", cd.unk028))
    buf.extend(struct.pack("<I", cd.unk032))
    buf.extend(struct.pack("<I", len(cd.chapters)))

    # Chapters
    for ch in cd.chapters:
        encoded = ch.title.encode("utf-8")
        buf.extend(struct.pack("<I", ch.startpos))
        buf.extend(struct.pack("<I", len(encoded)))
        buf.extend(encoded)

    return bytes(buf)


def write_chapter_data_atom(cd: ChapterData) -> bytes:
    """Write chapter data in QuickTime atom-box format for MHOD type 17 body.

    This matches libgpod's exact format: a 12-byte LE header followed by nested
    QuickTime atoms (sean -> chap -> name + hedr). Each name atom contains the
    chapter title as UTF-16BE with version/length fields.

    Atom layout:
        sean: size(4BE) + "sean" + version(4) + child count(4) + pad(4)
          chap: size(4BE) + "chap" + startpos(4BE) + version(4) + pad(4)
            name: size(4BE) + "name" + version(4) + pad(4) + pad(4) + strlen(2BE) + UTF-16BE
          ...
          hedr: size(4BE) + "hedr" + version(4) + 0(4) + 0(4) + 0(4) + 1(4)

    Args:
        cd: ChapterData to serialize.

    Returns:
        Raw bytes for the MHOD type 17 body.
    """
    # Build all chap atoms first
    chap_atoms = bytearray()
    for ch in cd.chapters:
        title_bytes = ch.title.encode("utf-16-be")
        str_len = len(ch.title)  # character count
        # name atom: size(4BE) + "name" + version(4) + unk(4) + unk(4) + strlen(2BE) + utf16be
        name_payload = (
            struct.pack(">I", 1)  # version = 1
            + struct.pack(">I", 0)  # unk = 0
            + struct.pack(">I", 0)  # unk = 0
            + struct.pack(">H", str_len)  # string length in chars
            + title_bytes
        )
        name_atom = struct.pack(">I", 8 + len(name_payload)) + b"name" + name_payload
        # chap atom: size(4BE) + "chap" + startpos(4BE) + version(4) + pad(4) + name_atom
        chap_payload = (
            struct.pack(">I", ch.startpos)
            + struct.pack(">I", 1)  # version = 1
            + struct.pack(">I", 0)  # pad = 0
            + name_atom
        )
        chap_atom = struct.pack(">I", 8 + len(chap_payload)) + b"chap" + chap_payload
        chap_atoms.extend(chap_atom)

    # hedr atom: size(4BE) + "hedr" + version(4) + 3 zero words + 1 (as libgpod writes it)
    hedr_payload = struct.pack(">IIIII", 1, 0, 0, 0, 1)
    hedr_atom = struct.pack(">I", 8 + len(hedr_payload)) + b"hedr" + hedr_payload

    # sean atom: size(4BE) + "sean" + version(4) + children(4) + pad(4) + chaps + hedr
    sean_inner = (
        struct.pack(">I", 1)  # version = 1
        + struct.pack(">I", len(cd.chapters) + 1)  # child atoms: chapters + hedr
        + struct.pack(">I", 0)  # pad = 0
        + bytes(chap_atoms)
        + hedr_atom
    )
    sean_atom = struct.pack(">I", 8 + len(sean_inner)) + b"sean" + sean_inner

    # 12-byte LE header: unk024, unk028, unk032
    header = struct.pack("<III", cd.unk024, cd.unk028, cd.unk032)

    return header + sean_atom


def parse_chapter_data_atom(data: bytes) -> Optional[ChapterData]:
    """Parse the QuickTime atom format of an MHOD type 17 body (libgpod/iTunes).

    Layout as written by :func:`write_chapter_data_atom`; chapter titles are UTF-16BE.

    Returns:
        ChapterData, or None if the body is not in the atom format.
    """
    if len(data) < 32 or data[16:20] != b"sean":
        return None
    cd = ChapterData()
    cd.unk024, cd.unk028, cd.unk032 = struct.unpack_from("<III", data, 0)
    sean_end = min(len(data), 12 + struct.unpack_from(">I", data, 12)[0])
    pos = 12 + 20  # sean header: size, type, version, children, pad
    while pos + 8 <= sean_end:
        size = struct.unpack_from(">I", data, pos)[0]
        if size < 8 or pos + size > sean_end:
            break
        if data[pos + 4 : pos + 8] == b"chap" and size >= 20:
            startpos = struct.unpack_from(">I", data, pos + 8)[0]
            title = ""
            name = pos + 20
            if pos + size >= name + 22 and data[name + 4 : name + 8] == b"name":
                chars = struct.unpack_from(">H", data, name + 20)[0]
                raw = data[name + 22 : min(name + 22 + 2 * chars, pos + size)]
                title = raw.decode("utf-16-be", errors="replace")
            cd.chapters.append(Chapter(title=title, startpos=startpos))
        pos += size
    return cd


def parse_chapter_data_any(data: bytes) -> Optional[ChapterData]:
    """Parse an MHOD type 17 body in either format (atom format first, else compact)."""
    return parse_chapter_data_atom(data) or parse_chapter_data(data)
