"""A damaged iTunesDB is refused instead of silently truncated by the next save."""

import os

import pytest

from pygpod.cli import main
from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import DatabaseError, ParseError
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


@pytest.fixture
def damaged(tmp_path):
    """Tracks Арфа, Бубон, Ваган; Бубон's record header is overwritten."""
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    mp3 = str(tmp_path / "пісня.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    for title in ("Арфа", "Бубон", "Ваган"):
        db.add_track(mp3, title=title)
    db.save()
    path = os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB")
    with open(path, "rb") as f:
        data = bytearray(f.read())
    first = data.index(b"mhit")
    second = data.index(b"mhit", first + 4)
    data[second : second + 4] = b"zzzz"
    with open(path, "wb") as f:
        f.write(data)
    return mp, bytes(data)


def test_damaged_track_list_raises_by_default(damaged):
    mp, _ = damaged
    with pytest.raises(ParseError, match="damaged track list"):
        Database(mp)


def test_partial_mode_loads_what_is_readable_and_refuses_a_normal_save(damaged):
    mp, data = damaged
    db = Database(mp, partial=True)
    assert [t.title for t in db.tracks] == ["Арфа"]
    assert db.damage and "track list" in db.damage[0]
    with pytest.raises(DatabaseError, match="damaged"):
        db.save()
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        assert f.read() == data  # unchanged
    db.save(force=True)  # explicitly keep only the readable part
    assert [t.title for t in Database(mp).tracks] == ["Арфа"]


def test_doctor_reports_the_damage_and_does_not_repair_it(damaged, capsys):
    mp, data = damaged
    report = Database(mp, partial=True, recover=False).check()
    assert "database_damaged" in report.kinds()
    assert main(["-m", mp, "doctor"]) == 1
    assert "database_damaged" in capsys.readouterr().out
    assert main(["-m", mp, "doctor", "--fix"]) == 1
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        assert f.read() == data


def _music_files(mp):
    music = os.path.join(mp, "iPod_Control", "Music")
    return sorted(os.path.join(d, n) for d, _, names in os.walk(music) for n in names)


def test_doctor_offers_no_fix_for_a_damaged_database(damaged, capsys):
    mp, data = damaged
    files = _music_files(mp)
    report = Database(mp, partial=True, recover=False).check()
    # the unread tracks' files look like orphans, but they are not
    assert report.by_kind("orphan_file")
    assert not any(i.fixable for i in report)
    assert all("damage" in i.message for i in report.by_kind("orphan_file"))
    assert report.summary().endswith("(0 fixable)")
    assert main(["-m", mp, "doctor"]) == 1
    out = capsys.readouterr().out
    assert "(fixable)" not in out and "(0 fixable)" in out
    assert main(["-m", mp, "doctor", "--fix"]) == 1
    assert "After repair:" in capsys.readouterr().out
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        assert f.read() == data
    assert _music_files(mp) == files and len(files) == 3


def test_intact_database_has_no_damage(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    assert Database(mp, partial=True).damage == []
