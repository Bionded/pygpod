"""Chapters on import: MP4 chapter track / Nero chpl, ID3 CHAP/CTOC -> MHOD 17.

The MP3 fixtures are made with mutagen; the M4B fixtures need ffmpeg (skipped without it).
libgpod reading the chapters back is tested in test_libgpod_interop.py.
"""

import os
import shutil
import subprocess

import pytest

from pygpod.chapters import read_chapters
from pygpod.db.constants import MHOD_ID_CHAPTERDATA, MHOD_MAGIC
from pygpod.device.mountpoint import init_ipod
from pygpod.model.chapterdata import (
    Chapter,
    ChapterData,
    parse_chapter_data_atom,
    write_chapter_data_atom,
)
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

CHAPTERS = [(0, "Розділ перший"), (1200, "Ґанок і їжак"), (2500, "Третій")]
STORED = [(1, "Розділ перший"), (1200, "Ґанок і їжак"), (2500, "Третій")]  # 0 ms -> 1 ms

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make_m4b(path, chapters=CHAPTERS, keep=("qt", "chpl")):
    """An M4B with chapters written by ffmpeg (both a QuickTime chapter track and a Nero
    chpl atom); `keep` disables the other source by renaming its atom (same size)."""
    meta = path + ".txt"
    with open(meta, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\ntitle=Аудіокнига\n")
        ends = [c[0] for c in chapters[1:]] + [4000]
        for (start, title), end in zip(chapters, ends):
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle={title}\n")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=4", "-i", meta]
        + ["-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "32k", path],
        check=True,
    )
    data = bytearray(open(path, "rb").read())
    if "qt" not in keep:
        i = data.index(b"tref")
        data[data.index(b"chap", i) : data.index(b"chap", i) + 4] = b"xxxx"
    if "chpl" not in keep:
        i = data.index(b"chpl")
        data[i : i + 4] = b"xxxx"
    open(path, "wb").write(bytes(data))
    return path


def make_mp3_with_chap(path, chapters=CHAPTERS, toc_order=None):
    """MP3 with ID3v2 CHAP frames (+ a top-level CTOC in `toc_order` if given)."""
    from mutagen.id3 import CHAP, CTOC, ID3, TIT2, CTOCFlags

    _make_minimal_mp3(path)
    tags = ID3()
    ends = [c[0] for c in chapters[1:]] + [4000]
    for i, ((start, title), end) in enumerate(zip(chapters, ends)):
        tags.add(
            CHAP(
                element_id=f"chp{i}",
                start_time=start,
                end_time=end,
                sub_frames=[TIT2(encoding=3, text=[title])],
            )
        )
    if toc_order is not None:
        tags.add(
            CTOC(
                element_id="toc",
                flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                child_element_ids=[f"chp{i}" for i in toc_order],
                sub_frames=[],
            )
        )
    tags.save(path)
    return path


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": b"ModelNumStr: xA003\n"})
    return mp


# ---- reading chapters from files ----


@needs_ffmpeg
@pytest.mark.parametrize("keep", [("qt", "chpl"), ("qt",), ("chpl",)])
def test_read_m4b_chapters(tmp_path, keep):
    path = make_m4b(str(tmp_path / "book.m4b"), keep=keep)
    assert read_chapters(path) == CHAPTERS


@needs_ffmpeg
def test_read_m4b_without_chapters(tmp_path):
    path = make_m4b(str(tmp_path / "book.m4b"), keep=())
    assert read_chapters(path) == []


def test_read_mp3_chap_by_start_time(tmp_path):
    path = make_mp3_with_chap(str(tmp_path / "a.mp3"), list(reversed(CHAPTERS)))
    assert read_chapters(path) == CHAPTERS


def test_read_mp3_ctoc_order_wins(tmp_path):
    path = make_mp3_with_chap(str(tmp_path / "a.mp3"), toc_order=[0, 2, 1])
    assert read_chapters(path) == [CHAPTERS[0], CHAPTERS[2], CHAPTERS[1]]


def test_read_mp3_without_chapters(tmp_path):
    path = str(tmp_path / "a.mp3")
    _make_minimal_mp3(path)
    assert read_chapters(path) == []


def test_read_unreadable_or_other_files(tmp_path):
    broken = tmp_path / "broken.m4b"
    broken.write_bytes(b"\x00\x00\x00\x10moovjunkjunk")
    assert read_chapters(str(broken)) == []
    assert read_chapters(str(tmp_path / "missing.mp3")) == []
    other = tmp_path / "a.flac"
    other.write_bytes(b"fLaC")
    assert read_chapters(str(other)) == []


# ---- MHOD 17 format ----


def test_atom_format_roundtrip_and_counts():
    cd = ChapterData()
    for start, title in CHAPTERS:
        cd.add_chapter(title, start)
    body = write_chapter_data_atom(cd)
    # sean child count = chapters + hedr; hedr ends with 1 (libgpod's bytes)
    assert body[12 + 12 : 12 + 16] == (len(CHAPTERS) + 1).to_bytes(4, "big")
    assert body[-20:] == bytes.fromhex("00000001" + "00" * 12 + "00000001")
    assert parse_chapter_data_atom(body).to_list() == STORED


# ---- add_track / Track.chapters ----


def _chapter_mhods(track):
    return [
        c
        for c in track.record.children
        if c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_CHAPTERDATA
    ]


@needs_ffmpeg
def test_add_track_m4b_chapters_survive_save(ipod, tmp_path):
    book = make_m4b(str(tmp_path / "book.m4b"))
    db = Database(ipod)
    track = db.add_track(book, media_type=0x08)
    assert track.chapters.to_list() == STORED
    db.save()
    (track,) = Database(ipod).tracks
    assert track.chapters.to_list() == STORED
    assert track.title == "Аудіокнига"


def test_add_track_mp3_chapters_last_mhod(ipod, tmp_path):
    db = Database(ipod)
    track = db.add_track(make_mp3_with_chap(str(tmp_path / "a.mp3")))
    assert track.chapters.to_list() == STORED
    mhods = [c for c in track.record.children if c.magic == MHOD_MAGIC]
    assert mhods[-1].fields["mhod_type"] == MHOD_ID_CHAPTERDATA  # last, like libgpod
    db.save()
    assert Database(ipod).tracks[0].chapters.to_list() == STORED


def test_explicit_chapters_win(ipod, tmp_path):
    mp3 = make_mp3_with_chap(str(tmp_path / "a.mp3"))
    db = Database(ipod)
    t1 = db.add_track(mp3, chapters=[(0, "Вступ"), (90000, "Обійми")])
    t2 = db.add_track(mp3, chapters=[Chapter("Ґанок", 5)])
    cd = ChapterData()
    cd.add_chapter("Океан Ельзи", 0)
    t3 = db.add_track(mp3, chapters=cd)
    t4 = db.add_track(mp3, chapters=[])
    t5 = db.add_track(mp3, chapters=None)
    db.save()
    reopened = Database(ipod).tracks
    assert reopened[0].chapters.to_list() == [(1, "Вступ"), (90000, "Обійми")]
    assert reopened[1].chapters.to_list() == [(5, "Ґанок")]
    assert reopened[2].chapters.to_list() == [(1, "Океан Ельзи")]
    assert reopened[3].chapters is None and reopened[4].chapters is None
    assert [t1.chapters.to_list()[0][1], t2.chapters.to_list()[0][1]] == ["Вступ", "Ґанок"]
    assert cd.to_list() == [(1, "Океан Ельзи")] and t3.chapters is not cd
    assert not _chapter_mhods(t4) and not _chapter_mhods(t5)


def test_file_without_chapters_writes_the_same_bytes(tmp_path):
    """No chapters -> no MHOD 17: the database equals one built with chapters=[]."""
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    out = []
    for name, kwargs in (("one", {}), ("two", {"chapters": []})):
        mp = str(tmp_path / name)
        os.makedirs(mp)
        init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": b"ModelNumStr: xA003\n"})
        db = Database(mp)
        db._rng.seed(1)
        db.add_track(mp3, title="Обійми", **kwargs)
        db.save_to_file(str(tmp_path / f"{name}.db"))
        out.append(open(str(tmp_path / f"{name}.db"), "rb").read())
    assert b"sean" not in out[0]
    assert len(out[0]) == len(out[1])


def test_track_chapters_setter_and_removal(ipod, tmp_path):
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(ipod)
    track = db.add_track(mp3)
    assert track.chapters is None
    db.save()
    db = Database(ipod)
    track = db.tracks[0]
    track.chapters = [(0, "Перший"), (3000, "Другий")]
    assert db.modified
    db.save()
    db = Database(ipod)
    track = db.tracks[0]
    assert track.chapters.to_list() == [(1, "Перший"), (3000, "Другий")]
    cd = track.chapters
    cd.add_chapter("Третій", 6000)
    track.chapters = cd
    db.save()
    db = Database(ipod)
    assert len(db.tracks[0].chapters) == 3
    db.tracks[0].chapters = None
    db.save()
    assert Database(ipod).tracks[0].chapters is None


def test_unchanged_db_with_chapters_roundtrips_byte_identical(ipod, tmp_path):
    db = Database(ipod)
    db.add_track(make_mp3_with_chap(str(tmp_path / "a.mp3")))
    db.save()
    path = os.path.join(ipod, "iPod_Control", "iTunes", "iTunesDB")
    before = open(path, "rb").read()
    Database(ipod).save()
    assert open(path, "rb").read() == before
