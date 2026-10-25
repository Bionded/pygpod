"""A format's thumbnails spread over several .ithmb files (F<format>_1.ithmb, _2 ...).

iTunes and libgpod start a new file once one reaches 256 MB; here the limit is lowered so a
few covers already span several files. Removing covers and saving must keep every other track
on its own image: each thumbnail is read, compacted and deleted through the file its MHNI
names, not F<format>_1.ithmb.
"""

import os
import subprocess

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.model.artwork_manager import ArtworkManager
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3, _make_minimal_png
from .test_crash_safe_save import _thumbs

pytest.importorskip("PIL.Image")

COLORS = [
    (230, 30, 30),
    (30, 200, 30),
    (30, 30, 230),
    (220, 220, 20),
    (20, 210, 210),
    (200, 20, 200),
]


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _art_dir(mp):
    return os.path.join(mp, "iPod_Control", "Artwork")


def _ithmbs(mp):
    d = _art_dir(mp)
    return {n: _read(os.path.join(d, n)) for n in sorted(os.listdir(d)) if n.endswith(".ithmb")}


def _covers(mp):
    """{title: {format id: thumbnail bytes}} read through each MHNI's own file and offset."""
    db = Database(mp)
    mgr = db._get_artwork_manager()
    return {t.title: _thumbs(mgr, t.mhii_link) for t in db.tracks if t.has_artwork}


def _files_of(mp, title):
    """The .ithmb file names the cover of ``title`` uses."""
    db = Database(mp)
    mgr = db._get_artwork_manager()
    link = next(t.mhii_link for t in db.tracks if t.title == title)
    mhii = next(m for m in mgr._get_or_create_mhli().children if m.fields["image_id"] == link)
    return {ArtworkManager._mhni_file(mhni) for mhni in mgr._mhnis(mhii)}


def _build(tmp_path, monkeypatch, max_size):
    """A Classic 7G volume with six covers, files limited to ``max_size`` bytes."""
    monkeypatch.setattr(ArtworkManager, "ITHMB_MAX_SIZE", max_size)
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xC293\nFirewireGuid: 0x000A2700213749FF\n"
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": sysinfo})
    mp3 = str(tmp_path / "Пісня.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    for i, color in enumerate(COLORS):
        png = str(tmp_path / f"обкладинка{i}.png")
        _make_minimal_png(png, 64, 64, color)
        db.add_track(mp3, title=f"Пісня {i}", artist="Гурт", cover_art=png)
    db.save()
    return mp, tmp_path


@pytest.fixture
def split(tmp_path, monkeypatch):
    """Every large-format cover in a file of its own, the small ones shared."""
    return _build(tmp_path, monkeypatch, 50_000)


@pytest.fixture
def full_first(tmp_path, monkeypatch):
    """Like a big library: F1060_1.ithmb full with five covers, the sixth in F1060_2.ithmb."""
    return _build(tmp_path, monkeypatch, 5 * 320 * 320 * 2)


def test_compacting_a_full_first_file_keeps_the_second_files_covers(full_first):
    mp, _ = full_first
    assert "F1060_2.ithmb" in _ithmbs(mp) and _files_of(mp, "Пісня 5") >= {"F1060_2.ithmb"}
    before = _covers(mp)
    files = _ithmbs(mp)
    removed = {f"Пісня {i}" for i in range(4)}
    db = Database(mp)
    for t in db.tracks:
        if t.title in removed:
            db.set_artwork(t, None)
    db.save()  # most of the _1 files is unused now: compacted
    now = _ithmbs(mp)
    assert len(now["F1060_1.ithmb"]) < len(files["F1060_1.ithmb"])
    assert now["F1060_2.ithmb"] == files["F1060_2.ithmb"]
    assert _covers(mp) == {t: c for t, c in before.items() if t not in removed}
    assert Database(mp).check().ok


def test_thumbnails_spread_over_several_files(split):
    mp, _ = split
    names = set(_ithmbs(mp))
    assert {n for n in names if n.endswith("_2.ithmb")}, names
    covers = _covers(mp)
    assert len(covers) == len(COLORS)
    # every cover distinct in every format: nothing points at another track's image
    for fmt in covers["Пісня 0"]:
        assert len({c[fmt] for c in covers.values()}) == len(COLORS)
    assert Database(mp).check().ok


def test_removing_covers_keeps_the_others_on_their_own_images(split):
    mp, tmp_path = split
    before = _covers(mp)
    files = _ithmbs(mp)
    removed = ["Пісня 1", "Пісня 3"]
    touched = set().union(*(_files_of(mp, t) for t in removed))

    db = Database(mp)
    for t in db.tracks:
        if t.title in removed:
            db.set_artwork(t, None)
    db.save()

    after = _covers(mp)
    assert after == {t: c for t, c in before.items() if t not in removed}
    now = _ithmbs(mp)
    referenced = set().union(*(_files_of(mp, t) for t in after))
    assert set(now) == referenced  # files only the removed covers used are gone
    for name in set(files) - touched:
        assert now[name] == files[name]  # files without a removed cover stay untouched
    assert any(len(now[n]) < len(files[n]) for n in touched & set(now))  # some compacted
    assert Database(mp).check().ok

    # a new cover reuses free slots or opens the next file; all covers stay intact
    png = str(tmp_path / "нова.png")
    _make_minimal_png(png, 64, 64, (120, 60, 10))
    db = Database(mp)
    track = next(t for t in db.tracks if t.title == "Пісня 0")
    with open(png, "rb") as f:
        db.set_artwork(track, f.read())
    db.save()
    final = _covers(mp)
    assert {t: c for t, c in final.items() if t != "Пісня 0"} == {
        t: c for t, c in after.items() if t != "Пісня 0"
    }
    assert final["Пісня 0"] != after["Пісня 0"]
    assert Database(mp).check().ok


def test_compaction_keeps_each_file_and_its_name(split, monkeypatch):
    mp, _ = split
    before = _covers(mp)
    monkeypatch.setattr(ArtworkManager, "COMPACT_WASTE_RATIO", 0.0)  # compact any waste
    db = Database(mp)
    for t in db.tracks:
        if t.title in ("Пісня 0", "Пісня 4"):
            db.set_artwork(t, None)
    db.save()
    after = _covers(mp)
    assert after == {t: c for t, c in before.items() if t not in ("Пісня 0", "Пісня 4")}
    assert {n for n in _ithmbs(mp) if not n.endswith("_1.ithmb")}  # still several files
    assert Database(mp).check().ok


def _dump(tool, mp):
    """{title: sorted K/F/P lines of libgpod's view of its cover}."""
    out = subprocess.run(
        [tool, "dump-artwork", mp], capture_output=True, text=True, check=True
    ).stdout
    by_title = {}
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] in ("K", "F", "P"):
            by_title.setdefault(f[1], []).append(line)
    return {t: sorted(lines) for t, lines in by_title.items()}


@pytest.mark.libgpod
def test_libgpod_reads_the_untouched_covers_unchanged(tool, split):
    mp, _ = split
    before = _dump(tool, mp)
    assert len(before) == len(COLORS)
    for lines in before.values():
        k = next(x.split("\t") for x in lines if x.startswith("K"))
        assert int(k[2]) == int(k[3]) > 0  # libgpod decodes every thumbnail
    db = Database(mp)
    for t in db.tracks:
        if t.title in ("Пісня 2", "Пісня 5"):
            db.set_artwork(t, None)
    db.save()
    after = _dump(tool, mp)
    assert after == {t: v for t, v in before.items() if t not in ("Пісня 2", "Пісня 5")}
