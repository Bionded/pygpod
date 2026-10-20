"""Binary iTunesStats (iPod shuffle), both layouts libgpod 0.8.3 reads."""

import os
import struct
import subprocess

import pytest

from pygpod.db.playcounts import parse_itunesstats

from .conftest import _make_minimal_mp3


def _u24(v):
    return struct.pack("<I", v)[:3]


def old_format(entries, entry_len=18):
    """shuffle 2G: u24 count, entries at 6: len, bookmark, unk, unk, playcount, skips (u24 each)."""
    out = _u24(len(entries)) + b"\x00" * 3
    for pc, bm, skips in entries:
        e = _u24(entry_len) + _u24(bm) + _u24(0) + _u24(0) + _u24(pc) + _u24(skips)
        out += e + b"\x00" * (entry_len - 18)
    return out


def new_format(entries, entry_len=32):
    """u32 count, entries at 8: len, bookmark, playcount, played, skips, skipped, 8 unknown."""
    out = struct.pack("<II", len(entries), 0)
    for pc, bm, played, skips, skipped in entries:
        e = struct.pack("<IIIIII", entry_len, bm, pc, played, skips, skipped) + b"\x00" * 8
        out += e + b"\x00" * (entry_len - 32)
    return out


def test_old_format():
    entries = parse_itunesstats(old_format([(3, 1500, 1), (7, 0, 2)]), old_format=True)
    assert [(e.play_count, e.bookmark_time, e.skip_count) for e in entries] == [
        (3, 1500, 1),
        (7, 0, 2),
    ]
    assert all(e.rating == -1 and e.time_played == 0 for e in entries)


def test_new_format_entries_at_consecutive_offsets():
    data = new_format(
        [(3, 1500, 3800000000, 1, 3800000100), (7, 0, 3800000200, 2, 0)], entry_len=40
    )
    entries = parse_itunesstats(data, old_format=False)
    assert [
        (e.play_count, e.bookmark_time, e.time_played, e.skip_count, e.time_skipped)
        for e in entries
    ] == [
        (3, 1500, 3800000000, 1, 3800000100),
        (7, 0, 3800000200, 2, 0),
    ]


def test_layout_detected():
    assert parse_itunesstats(new_format([(5, 0, 0, 0, 0)]))[0].play_count == 5
    assert parse_itunesstats(old_format([(4, 0, 0)]))[0].play_count == 4


def test_short_entry_length_stops():
    assert parse_itunesstats(old_format([(1, 0, 0)], entry_len=18)[:-5], old_format=True) == []
    bad = struct.pack("<II", 1, 0) + struct.pack("<I", 16) + b"\x00" * 28
    assert parse_itunesstats(bad, old_format=False) == []


@pytest.mark.libgpod
@pytest.mark.parametrize("layout", ["new", "old"])
def test_libgpod_reads_the_same(tool, tmp_path, layout):
    """libgpod applies the file when it parses the database: same counts as pygpod (one track, since
    libgpod 0.8.3 reads every new-format entry at the first offset)."""
    from pygpod.model.database import Database

    model = "xA546" if layout == "old" else "xB029"  # shuffle 2G uses the old layout
    mp = tmp_path / "ipod"
    for d in ("iTunes", "Device", "Music/F00"):
        (mp / "iPod_Control" / d).mkdir(parents=True)
    (mp / "iPod_Control" / "Device" / "SysInfo").write_text(
        f"ModelNumStr: {model}\nFirewireGuid: 0x000A2700213749FF\n"
    )
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(f"name\tS\ntrack\t{mp3}\tOne\tA\tAl\tPop\t1\t2008\t1\t0\t0\n")
    subprocess.run([tool, "build", str(mp), str(spec)], check=True, capture_output=True)
    stats = old_format([(6, 2500, 1)]) if layout == "old" else new_format([(6, 2500, 0, 1, 0)])
    (mp / "iPod_Control" / "iTunes" / "iTunesStats").write_bytes(stats)

    out = subprocess.run([tool, "dump", str(mp)], capture_output=True, text=True, check=True).stdout
    t = next(line.split("\t") for line in out.splitlines() if line.startswith("T\t"))
    libgpod_playcount = int(t[11])
    ours = Database(str(mp)).tracks[0]
    assert ours.play_count == libgpod_playcount == 6
    assert ours.skip_count == 1 and ours.bookmark_time == 2500
    assert os.path.isfile(
        mp / "iPod_Control" / "iTunes" / "iTunesStats"
    )  # not consumed without save
