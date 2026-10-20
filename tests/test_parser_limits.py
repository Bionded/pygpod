"""Malformed record lengths must raise ParseError quickly, never hang (task: parser limits)."""

import faulthandler
import os
import signal
import struct

import pytest

from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.db.parser import parse_itunesdb
from pygpod.db.playcounts import parse_itunesstats, parse_otg_playlists, parse_play_counts
from pygpod.exceptions import ParseError


@pytest.fixture(autouse=True)
def deadline():
    """Fail a test that runs longer than 5 s (no pytest-timeout dependency)."""
    if not hasattr(signal, "SIGALRM"):  # Windows: no alarm, abort the run with tracebacks
        faulthandler.dump_traceback_later(5, exit=True)
        yield
        faulthandler.cancel_dump_traceback_later()
        return

    def expired(signum, frame):
        raise TimeoutError("parser did not terminate")

    old = signal.signal(signal.SIGALRM, expired)
    signal.alarm(5)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


@pytest.fixture
def itunesdb(ipod_fs_path):
    with open(os.path.join(ipod_fs_path, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        return f.read()


def _find(data, magic, start=0):
    pos = data.find(magic, start)
    assert pos >= 0, magic
    return pos


def _patch32(data, offset, value):
    buf = bytearray(data)
    struct.pack_into("<I", buf, offset, value)
    return bytes(buf)


def test_fixture_parses(itunesdb):
    assert parse_itunesdb(itunesdb).children


@pytest.mark.parametrize("magic", [b"mhsd", b"mhit", b"mhod", b"mhyp", b"mhip"])
def test_zero_total_len(itunesdb, magic):
    pos = _find(itunesdb, magic, 4)
    with pytest.raises(ParseError):
        parse_itunesdb(_patch32(itunesdb, pos + 8, 0))


@pytest.mark.parametrize("magic", [b"mhsd", b"mhit", b"mhod", b"mhyp"])
def test_undersized_header(itunesdb, magic):
    pos = _find(itunesdb, magic, 4)
    with pytest.raises(ParseError):
        parse_itunesdb(_patch32(itunesdb, pos + 4, 4))


@pytest.mark.parametrize("magic", [b"mhsd", b"mhit", b"mhod"])
def test_child_beyond_parent(itunesdb, magic):
    pos = _find(itunesdb, magic, 4)
    with pytest.raises(ParseError):
        parse_itunesdb(_patch32(itunesdb, pos + 8, len(itunesdb) * 2))


def test_mhbd_undersized_and_truncated(itunesdb):
    with pytest.raises(ParseError):
        parse_itunesdb(_patch32(itunesdb, 4, 8))
    with pytest.raises(ParseError):
        parse_itunesdb(itunesdb[: len(itunesdb) // 2])
    with pytest.raises(ParseError):
        parse_itunesdb(itunesdb[:20])


def test_huge_child_count_with_zero_length_records(itunesdb):
    # mhlt claims 2**32-1 tracks and the first mhit has length 0
    pos = _find(itunesdb, b"mhlt")
    data = _patch32(itunesdb, pos + 8, 0xFFFFFFFF)
    data = _patch32(data, _find(data, b"mhit") + 8, 0)
    with pytest.raises(ParseError):
        parse_itunesdb(data)


def _artworkdb():
    def rec(magic, header_len, total_or_count, extra=b""):
        return (
            magic
            + struct.pack("<II", header_len, total_or_count)
            + b"\x00" * (header_len - 12)
            + extra
        )

    mhli = rec(b"mhli", 92, 0)
    mhsd = rec(b"mhsd", 96, 96 + len(mhli), mhli)
    mhsd_buf = bytearray(mhsd)
    struct.pack_into("<H", mhsd_buf, 12, 1)
    body = bytes(mhsd_buf)
    head = bytearray(rec(b"mhfd", 132, 132 + len(body)))
    struct.pack_into("<I", head, 0x14, 1)
    return bytes(head) + body


def test_artworkdb_limits():
    data = _artworkdb()
    assert parse_artworkdb(data).children
    mhsd = _find(data, b"mhsd")
    for bad in (
        _patch32(data, mhsd + 8, 0),  # zero length
        _patch32(data, mhsd + 4, 4),  # undersized header
        _patch32(data, mhsd + 8, 10_000),  # beyond parent
        _patch32(data, 4, 8),  # mhfd header too small
        data[:-10],  # truncated
    ):
        with pytest.raises(ParseError):
            parse_artworkdb(bad)


def test_play_count_files_terminate():
    # Play Counts: huge entry count, tiny file
    pc = b"mhdp" + struct.pack("<III", 96, 28, 0xFFFFFFFF) + b"\x00" * 80
    assert parse_play_counts(pc) == []
    # zero entry size
    assert parse_play_counts(b"mhdp" + struct.pack("<III", 96, 0, 10) + b"\x00" * 200) == []
    # iTunesStats claiming 2**31 entries
    assert len(parse_itunesstats("2147483647\n1\n0\n0\n0\n0\n")) == 1
    # OTG: huge count
    otg = b"mhpo" + struct.pack("<III", 0x14, 4, 0xFFFFFFFF) + b"\x00" * 8
    assert parse_otg_playlists(otg) == [[0]]
