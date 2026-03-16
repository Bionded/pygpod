"""Tests for chapter data parsing and writing."""

import struct

from pygpod.model.chapterdata import (
    Chapter,
    ChapterData,
    parse_chapter_data,
    write_chapter_data,
    write_chapter_data_atom,
)

# =========================================================================
# Chapter
# =========================================================================


class TestChapter:
    def test_create(self):
        ch = Chapter("Intro", 0)
        assert ch.title == "Intro"
        assert ch.startpos == 0

    def test_defaults(self):
        ch = Chapter()
        assert ch.title == ""
        assert ch.startpos == 0

    def test_duplicate(self):
        ch = Chapter("Part 1", 5000)
        dup = ch.duplicate()
        assert dup.title == "Part 1"
        assert dup.startpos == 5000
        # Independent copy
        dup.title = "Changed"
        assert ch.title == "Part 1"

    def test_repr(self):
        ch = Chapter("My Chapter", 125000)  # 2:05
        r = repr(ch)
        assert "My Chapter" in r
        assert "2:05" in r

    def test_repr_zero(self):
        ch = Chapter("Start", 0)
        r = repr(ch)
        assert "0:00" in r


# =========================================================================
# ChapterData
# =========================================================================


class TestChapterData:
    def test_create_empty(self):
        cd = ChapterData()
        assert cd.chapter_count == 0
        assert len(cd) == 0
        assert list(cd) == []

    def test_add_chapter(self):
        cd = ChapterData()
        ch = cd.add_chapter("Chapter 1", 1000)
        assert ch.title == "Chapter 1"
        assert ch.startpos == 1000
        assert cd.chapter_count == 1

    def test_add_first_chapter_zero_becomes_one(self):
        cd = ChapterData()
        ch = cd.add_chapter("Start", 0)
        assert ch.startpos == 1  # iPod requires >= 1

    def test_add_second_chapter_zero_stays_zero(self):
        cd = ChapterData()
        cd.add_chapter("First", 1)
        ch2 = cd.add_chapter("Second", 0)
        assert ch2.startpos == 0  # only first chapter is adjusted

    def test_remove_chapter(self):
        cd = ChapterData()
        ch1 = cd.add_chapter("A", 1)
        cd.add_chapter("B", 5000)
        assert cd.chapter_count == 2

        result = cd.remove_chapter(ch1)
        assert result is True
        assert cd.chapter_count == 1
        assert cd.chapters[0].title == "B"

    def test_remove_chapter_not_found(self):
        cd = ChapterData()
        cd.add_chapter("A", 1)
        other = Chapter("X", 999)
        result = cd.remove_chapter(other)
        assert result is False
        assert cd.chapter_count == 1

    def test_remove_all_chapters(self):
        cd = ChapterData()
        cd.add_chapter("A", 1)
        cd.add_chapter("B", 5000)
        cd.add_chapter("C", 10000)
        cd.remove_all_chapters()
        assert cd.chapter_count == 0

    def test_duplicate(self):
        cd = ChapterData()
        cd.unk024 = 0x0C000000
        cd.unk028 = 42
        cd.add_chapter("Ch1", 1)
        cd.add_chapter("Ch2", 60000)

        dup = cd.duplicate()
        assert dup.chapter_count == 2
        assert dup.unk024 == 0x0C000000
        assert dup.unk028 == 42
        assert dup.chapters[0].title == "Ch1"

        # Independent
        dup.chapters[0].title = "Modified"
        assert cd.chapters[0].title == "Ch1"

    def test_repr(self):
        cd = ChapterData()
        cd.add_chapter("A", 1)
        cd.add_chapter("B", 5000)
        r = repr(cd)
        assert "2 chapters" in r

    def test_iter(self):
        cd = ChapterData()
        cd.add_chapter("A", 1)
        cd.add_chapter("B", 5000)
        titles = [ch.title for ch in cd]
        assert titles == ["A", "B"]

    def test_len(self):
        cd = ChapterData()
        assert len(cd) == 0
        cd.add_chapter("X", 1)
        assert len(cd) == 1


# =========================================================================
# Parse
# =========================================================================


class TestParseChapterData:
    def _build_binary(self, chapters, unk024=0x0C000000, unk028=0, unk032=0):
        """Build raw chapter data binary."""
        buf = struct.pack("<IIII", unk024, unk028, unk032, len(chapters))
        for title, startpos in chapters:
            encoded = title.encode("utf-8")
            buf += struct.pack("<II", startpos, len(encoded))
            buf += encoded
        return buf

    def test_parse_basic(self):
        data = self._build_binary(
            [
                ("Introduction", 1),
                ("Main Content", 60000),
                ("Conclusion", 180000),
            ]
        )
        cd = parse_chapter_data(data)
        assert cd is not None
        assert cd.chapter_count == 3
        assert cd.chapters[0].title == "Introduction"
        assert cd.chapters[0].startpos == 1
        assert cd.chapters[1].title == "Main Content"
        assert cd.chapters[1].startpos == 60000
        assert cd.chapters[2].title == "Conclusion"
        assert cd.chapters[2].startpos == 180000

    def test_parse_preserves_unknowns(self):
        data = self._build_binary([], unk024=0x0C000000, unk028=42, unk032=99)
        cd = parse_chapter_data(data)
        assert cd.unk024 == 0x0C000000
        assert cd.unk028 == 42
        assert cd.unk032 == 99

    def test_parse_empty_chapters(self):
        data = self._build_binary([])
        cd = parse_chapter_data(data)
        assert cd is not None
        assert cd.chapter_count == 0

    def test_parse_unicode_titles(self):
        data = self._build_binary(
            [
                ("Розділ 1", 1),
                ("日本語チャプター", 30000),
            ]
        )
        cd = parse_chapter_data(data)
        assert cd.chapters[0].title == "Розділ 1"
        assert cd.chapters[1].title == "日本語チャプター"

    def test_parse_too_short(self):
        cd = parse_chapter_data(b"\x00" * 10)
        assert cd is None

    def test_parse_truncated_chapter(self):
        # Header says 2 chapters but data only has 1
        data = struct.pack("<IIII", 0, 0, 0, 2)
        data += struct.pack("<II", 1000, 5)
        data += b"Hello"
        # Second chapter is missing
        cd = parse_chapter_data(data)
        assert cd is not None
        assert cd.chapter_count == 1
        assert cd.chapters[0].title == "Hello"

    def test_parse_invalid_utf8(self):
        # Build chapter with invalid UTF-8 bytes
        data = struct.pack("<IIII", 0, 0, 0, 1)
        data += struct.pack("<II", 1000, 4)
        data += b"\xff\xfe\x80\x81"  # invalid UTF-8
        cd = parse_chapter_data(data)
        assert cd is not None
        assert cd.chapter_count == 1
        assert len(cd.chapters[0].title) > 0  # replacement chars

    def test_parse_empty_title(self):
        data = self._build_binary([("", 5000)])
        cd = parse_chapter_data(data)
        assert cd.chapters[0].title == ""
        assert cd.chapters[0].startpos == 5000


# =========================================================================
# Write (compact format)
# =========================================================================


class TestWriteChapterData:
    def test_write_basic(self):
        cd = ChapterData()
        cd.add_chapter("Part 1", 1)
        cd.add_chapter("Part 2", 120000)

        data = write_chapter_data(cd)
        assert len(data) > 16

        # Verify header
        unk024, unk028, unk032, count = struct.unpack_from("<IIII", data, 0)
        assert count == 2

    def test_write_empty(self):
        cd = ChapterData()
        data = write_chapter_data(cd)
        count = struct.unpack_from("<I", data, 12)[0]
        assert count == 0

    def test_write_preserves_unknowns(self):
        cd = ChapterData()
        cd.unk024 = 0x0C000000
        cd.unk028 = 7
        cd.unk032 = 13
        data = write_chapter_data(cd)
        assert struct.unpack_from("<I", data, 0)[0] == 0x0C000000
        assert struct.unpack_from("<I", data, 4)[0] == 7
        assert struct.unpack_from("<I", data, 8)[0] == 13

    def test_round_trip(self):
        cd = ChapterData()
        cd.unk024 = 0x0C000000
        cd.add_chapter("Вступ", 1)
        cd.add_chapter("Основна частина", 60000)
        cd.add_chapter("Висновок", 300000)

        data = write_chapter_data(cd)
        cd2 = parse_chapter_data(data)

        assert cd2.chapter_count == 3
        assert cd2.unk024 == cd.unk024
        assert cd2.chapters[0].title == "Вступ"
        assert cd2.chapters[0].startpos == 1
        assert cd2.chapters[1].title == "Основна частина"
        assert cd2.chapters[1].startpos == 60000
        assert cd2.chapters[2].title == "Висновок"
        assert cd2.chapters[2].startpos == 300000

    def test_unicode_round_trip(self):
        cd = ChapterData()
        cd.add_chapter("日本語", 1)
        cd.add_chapter("Ελληνικά", 5000)
        cd.add_chapter("العربية", 10000)

        data = write_chapter_data(cd)
        cd2 = parse_chapter_data(data)

        assert cd2.chapters[0].title == "日本語"
        assert cd2.chapters[1].title == "Ελληνικά"
        assert cd2.chapters[2].title == "العربية"


# =========================================================================
# Write (QuickTime atom format)
# =========================================================================


class TestWriteChapterDataAtom:
    def test_write_atom_basic(self):
        cd = ChapterData()
        cd.add_chapter("Chapter 1", 1)
        cd.add_chapter("Chapter 2", 60000)

        data = write_chapter_data_atom(cd)
        assert len(data) > 20

        # First 12 bytes: LE header (unk024, unk028, unk032)
        unk024 = struct.unpack_from("<I", data, 0)[0]
        assert unk024 == cd.unk024

        # After 12-byte header: sean atom
        struct.unpack_from(">I", data, 12)[0]
        sean_magic = data[16:20]
        assert sean_magic == b"sean"

    def test_atom_contains_chap_atoms(self):
        cd = ChapterData()
        cd.add_chapter("A", 1)
        cd.add_chapter("B", 5000)

        data = write_chapter_data_atom(cd)

        # Find "chap" atoms
        chap_count = data.count(b"chap")
        assert chap_count == 2

    def test_atom_contains_name_atoms(self):
        cd = ChapterData()
        cd.add_chapter("Test Name", 1)

        data = write_chapter_data_atom(cd)
        assert b"name" in data

    def test_atom_contains_hedr(self):
        cd = ChapterData()
        cd.add_chapter("X", 1)

        data = write_chapter_data_atom(cd)
        assert b"hedr" in data

    def test_atom_empty_chapters(self):
        cd = ChapterData()
        data = write_chapter_data_atom(cd)
        # Should still have sean + hedr, no chap
        assert b"sean" in data
        assert b"hedr" in data
        assert b"chap" not in data

    def test_atom_unicode(self):
        cd = ChapterData()
        cd.add_chapter("Розділ один", 1)

        data = write_chapter_data_atom(cd)
        # Title should be UTF-16BE encoded inside name atom
        title_utf16 = "Розділ один".encode("utf-16-be")
        assert title_utf16 in data

    def test_atom_startpos_big_endian(self):
        cd = ChapterData()
        cd.add_chapter("Test", 0x00010000)  # 65536 ms

        data = write_chapter_data_atom(cd)
        # Find the chap atom and check startpos is BE
        chap_idx = data.index(b"chap")
        startpos = struct.unpack_from(">I", data, chap_idx + 4)[0]
        assert startpos == 0x00010000

    def test_atom_preserves_header_unknowns(self):
        cd = ChapterData()
        cd.unk024 = 0xAA
        cd.unk028 = 0xBB
        cd.unk032 = 0xCC
        cd.add_chapter("X", 1)

        data = write_chapter_data_atom(cd)
        assert struct.unpack_from("<I", data, 0)[0] == 0xAA
        assert struct.unpack_from("<I", data, 4)[0] == 0xBB
        assert struct.unpack_from("<I", data, 8)[0] == 0xCC
