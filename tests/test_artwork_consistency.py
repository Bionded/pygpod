"""Artwork changes keep the ArtworkDB, the .ithmb files and the tracks consistent."""

import os

import pytest

from pygpod.model.database import Database

from .conftest import _make_minimal_mp3, _make_minimal_png

PIL = pytest.importorskip("PIL.Image")
UnidentifiedImageError = PIL.UnidentifiedImageError


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _png_bytes(tmp_path, name, color):
    path = str(tmp_path / f"{name}.png")
    _make_minimal_png(path, 64, 64, color)
    return _read(path)


def _art_files(mp):
    d = os.path.join(mp, "iPod_Control", "Artwork")
    if not os.path.isdir(d):
        return {}
    return {n: _read(os.path.join(d, n)) for n in sorted(os.listdir(d))}


@pytest.fixture
def ipod(writable_ipod):
    Database(writable_ipod).save()
    return writable_ipod


@pytest.fixture
def mp3(tmp_path):
    path = str(tmp_path / "Пісня.mp3")
    _make_minimal_mp3(path)
    return path


def _covered_track(ipod, mp3, tmp_path):
    db = Database(ipod)
    db.add_track(mp3, title="Червона рута", cover_art=_png_bytes(tmp_path, "red", (255, 0, 0)))
    db.save()
    return db


# ---- set_artwork with data that is not an image ----


def test_set_artwork_invalid_image_keeps_old_cover(ipod, mp3, tmp_path):
    db = _covered_track(ipod, mp3, tmp_path)
    db = Database(ipod)
    track = db.tracks[-1]
    link, size = track.mhii_link, track.artwork_size
    files = _art_files(ipod)
    assert track.has_artwork and link

    with pytest.raises(UnidentifiedImageError):
        db.set_artwork(track, b"invalid")
    assert track.has_artwork and track.mhii_link == link and track.artwork_size == size
    assert not db.modified
    assert _art_files(ipod) == files

    db.save()  # an explicit save afterwards must not persist a loss
    again = Database(ipod).tracks[-1]
    assert again.has_artwork and again.mhii_link == link
    report = Database(ipod).check()
    assert not report.by_kind("artwork_missing") and not report.by_kind("artwork_orphan")


def test_set_artwork_valid_image_still_replaces(ipod, mp3, tmp_path):
    db = _covered_track(ipod, mp3, tmp_path)
    db = Database(ipod)
    track = db.tracks[-1]
    old = track.mhii_link
    db.set_artwork(track, _png_bytes(tmp_path, "green", (0, 255, 0)))
    assert track.has_artwork and track.mhii_link != old
    db.save()
    again = Database(ipod)
    assert again.tracks[-1].mhii_link == track.mhii_link
    assert again.check().ok


# ---- an ArtworkDB that cannot be read ----


def _tree(mp):
    """Every file under iPod_Control with its content."""
    out = {}
    for d, _, names in os.walk(os.path.join(mp, "iPod_Control")):
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, mp)] = _read(p)
    return out


@pytest.fixture
def corrupt(ipod, mp3, tmp_path):
    """Two tracks with covers, then the ArtworkDB header is damaged."""
    db = Database(ipod)
    for i, color in enumerate([(255, 0, 0), (0, 0, 255)]):
        db.add_track(mp3, title=f"Пісня {i}", cover_art=_png_bytes(tmp_path, f"c{i}", color))
    db.save()
    adb = os.path.join(ipod, "iPod_Control", "Artwork", "ArtworkDB")
    data = bytearray(_read(adb))
    data[0:8] = b"\xff" * 8  # magic and header length
    with open(adb, "wb") as f:
        f.write(bytes(data))
    return ipod


def _ours(db):
    """The tracks the corrupt fixture added with covers."""
    return [t for t in db.tracks if t.title.startswith("Пісня")]


def test_unreadable_artworkdb_add_track_with_cover_raises_nothing_written(corrupt, mp3, tmp_path):
    from pygpod import ArtworkDBError

    before = _tree(corrupt)
    db = Database(corrupt)  # opening still works
    n = len(db.tracks)
    with pytest.raises(ArtworkDBError):
        db.add_track(mp3, title="Нова", cover_art=_png_bytes(tmp_path, "new", (0, 255, 0)))
    assert len(db.tracks) == n and not db.modified
    assert _tree(corrupt) == before  # no media file copied, no artwork file touched
    ours = _ours(db)
    with pytest.raises(ArtworkDBError):
        db.set_artwork(ours[0], _png_bytes(tmp_path, "new", (0, 255, 0)))
    with pytest.raises(ArtworkDBError):
        db.set_artwork(ours[0], None)
    assert len(ours) == 2 and all(t.has_artwork and t.mhii_link for t in ours)
    assert _tree(corrupt) == before


def test_unreadable_artworkdb_other_changes_save_and_keep_it(corrupt, mp3):
    adb = os.path.join(corrupt, "iPod_Control", "Artwork", "ArtworkDB")
    art_before = _art_files(corrupt)
    db = Database(corrupt)
    first, second = _ours(db)
    first.title = "Пісня перейменована"
    db.add_track(mp3, title="Без обкладинки")  # the MP3 has no embedded cover
    db.remove_track(second)  # had a cover: its entry stays in the unreadable file
    db.save()
    assert _art_files(corrupt) == art_before and os.path.isfile(adb)
    again = Database(corrupt)
    titles = [t.title for t in again.tracks]
    assert "Пісня перейменована" in titles and "Без обкладинки" in titles
    assert second.title not in titles


def test_unreadable_artworkdb_reported_by_doctor(corrupt):
    report = Database(corrupt).check()
    assert "artwork_db_unreadable" in report.kinds()
    assert "reset_artwork" in report.by_kind("artwork_db_unreadable")[0].message


def test_reset_artwork_rebuilds_a_consistent_artworkdb(corrupt, tmp_path):
    from pygpod.db.artwork_parser import parse_artworkdb

    art_dir = os.path.join(corrupt, "iPod_Control", "Artwork")
    db = Database(corrupt)
    assert len(_ours(db)) == 2 and all(t.mhii_link for t in _ours(db))
    db.reset_artwork()
    assert db.modified
    assert all(not t.has_artwork and not t.mhii_link and not t.artwork_size for t in db.tracks)
    assert _art_files(corrupt)["ArtworkDB"][:4] != b"mhfd"  # written with the save only
    db.save()

    root = parse_artworkdb(_read(os.path.join(art_dir, "ArtworkDB")))
    mhli = next(c for c in root.children if c.fields.get("mhsd_type") == 1).children[0]
    assert mhli.children == []
    assert [n for n in os.listdir(art_dir) if n.endswith(".ithmb")] == []
    db = Database(corrupt)
    report = db.check()
    assert not {k for k in report.kinds() if k.startswith("artwork")}, report.summary()
    assert all(not t.has_artwork and not t.mhii_link for t in db.tracks)

    # covers can be set again, and nothing points at a stale image
    db.set_artwork(_ours(db)[1], _png_bytes(tmp_path, "back", (10, 200, 10)))
    db.save()
    db = Database(corrupt)
    assert [t.title for t in db.tracks if t.has_artwork] == [_ours(db)[1].title]
    assert db.check().ok, db.check().summary()


# ---- thumbnails are written with the save, after its conflict check ----


def _db_files(mp):
    """iTunes and Artwork files (the media folders change with add_track's copy)."""
    return {k: v for k, v in _tree(mp).items() if "Music" not in k.split(os.sep)}


def _covers(db):
    """{image id: {format id: thumbnail bytes}} of every track with a cover."""
    from .test_crash_safe_save import _thumbs

    mgr = db._get_artwork_manager()
    return {t.mhii_link: _thumbs(mgr, t.mhii_link) for t in db.tracks if t.has_artwork}


def test_new_cover_writes_nothing_before_the_conflict_check(ipod, mp3, tmp_path):
    from pygpod.exceptions import ConflictError

    db_b = Database(ipod)  # B opens first
    db_a = Database(ipod)
    track_a = db_a.add_track(mp3, title="Від A", cover_art=_png_bytes(tmp_path, "a", (255, 0, 0)))
    db_a.save()
    saved = _db_files(ipod)
    covers_a = _covers(Database(ipod))
    assert track_a.mhii_link in covers_a and all(covers_a[track_a.mhii_link].values())

    db_b.add_track(mp3, title="Від B", cover_art=_png_bytes(tmp_path, "b", (0, 0, 255)))
    db_b.set_artwork(db_b.tracks[0], _png_bytes(tmp_path, "b2", (0, 255, 0)))
    assert _db_files(ipod) == saved  # nothing written by add_track / set_artwork
    with pytest.raises(ConflictError):
        db_b.save()
    assert _db_files(ipod) == saved
    assert _covers(Database(ipod)) == covers_a


def test_failed_save_keeps_new_covers_pending_and_files_consistent(
    ipod, mp3, tmp_path, monkeypatch
):
    from pygpod.db.transaction import Transaction

    _covered_track(ipod, mp3, tmp_path)  # .ithmb files exist: new thumbnails go in place
    before = _db_files(ipod)
    db = Database(ipod)
    image = _png_bytes(tmp_path, "blue", (0, 0, 255))
    track = db.add_track(mp3, title="Синя", cover_art=image)
    expected = {f.format_id: data for f, data, _ in db._get_artwork_manager()._render(image)}

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr(Transaction, "commit", fail)
    with pytest.raises(OSError):
        db.save()
    monkeypatch.undo()
    assert _db_files(ipod) == before  # appended thumbnail data was cut off again
    kinds = Database(ipod).check().kinds()
    assert not {k for k in kinds if k.startswith("artwork")}, kinds

    db.save()  # the retry places the still pending thumbnails
    again = Database(ipod)
    assert _covers(again)[track.mhii_link] == expected
    assert again.check().ok, again.check().summary()


def test_many_pending_covers_spill_to_a_temporary_file(ipod, mp3, tmp_path, monkeypatch):
    from pygpod.model.artwork_manager import ArtworkManager

    monkeypatch.setattr(ArtworkManager, "SPOOL_MEMORY", 1024)  # spill after the first cover
    db = Database(ipod)
    colors = [(40 * i, 255 - 40 * i, 90) for i in range(4)]
    tracks = [
        db.add_track(mp3, title=f"Пісня {i}", cover_art=_png_bytes(tmp_path, f"s{i}", c))
        for i, c in enumerate(colors)
    ]
    mgr = db._get_artwork_manager()
    expected = {
        t.mhii_link: {f.format_id: d for f, d, _ in mgr._render(_png_bytes(tmp_path, f"s{i}", c))}
        for i, (t, c) in enumerate(zip(tracks, colors))
    }
    db.save()
    covers = _covers(Database(ipod))
    assert {k: covers[k] for k in expected} == expected


def test_has_artwork_value_two_means_no_cover(writable_ipod, tmp_path):
    """libgpod and iTunes store 1 for a track with cover art and 2 for one without."""
    from pygpod.utils.compat import put8int

    from .conftest import _make_minimal_mp3

    mp3 = str(tmp_path / "без обкладинки.mp3")
    _make_minimal_mp3(mp3)
    db = Database(writable_ipod)
    track = db.add_track(mp3, title="Без обкладинки")
    header = bytearray(track.record.raw_header)
    put8int(header, 0xA4, 2)
    track.record.raw_header = bytes(header)
    track.record.fields["has_artwork"] = 2
    assert track.has_artwork is False
    db.save()
    again = next(t for t in Database(writable_ipod).tracks if t.title == "Без обкладинки")
    assert again.has_artwork is False and again.record.fields["has_artwork"] == 2
    assert "artwork_missing" not in Database(writable_ipod).check().kinds()


# ---- a missing ArtworkDB ----


def _lose_artworkdb_then_add_cover(ipod, mp3, tmp_path):
    db = Database(ipod)
    db.add_track(mp3, title="Червона рута", cover_art=_png_bytes(tmp_path, "red", (255, 0, 0)))
    db.save()
    os.unlink(os.path.join(ipod, "iPod_Control", "Artwork", "ArtworkDB"))
    db = Database(ipod)
    assert "artwork_missing" in db.check().kinds()
    blue = _png_bytes(tmp_path, "blue", (0, 0, 255))
    db.add_track(mp3, title="Водограй", cover_art=blue)
    db.save()
    return Database(ipod)


def test_missing_artworkdb_new_cover_is_not_shown_on_old_tracks(ipod, mp3, tmp_path):
    db = _lose_artworkdb_then_add_cover(ipod, mp3, tmp_path)
    old = next(t for t in db.tracks if t.title == "Червона рута")
    new = next(t for t in db.tracks if t.title == "Водограй")
    # the old track's image went with the ArtworkDB: no reference to the new image id
    assert not old.has_artwork and not old.mhii_link and not old.artwork_size
    assert new.has_artwork and new.mhii_link
    mgr = db._get_artwork_manager()
    songs = {m.fields["image_id"]: m.fields["song_id"] for m in mgr._get_or_create_mhli().children}
    assert songs == {new.mhii_link: new.dbid}
    report = db.check()
    assert not {k for k in report.kinds() if k.startswith("artwork")}, report.summary()


@pytest.mark.libgpod
def test_missing_artworkdb_libgpod_shows_the_new_cover_only_on_its_track(tool, ipod, mp3, tmp_path):
    import subprocess

    _lose_artworkdb_then_add_cover(ipod, mp3, tmp_path)
    out = subprocess.run(
        [tool, "dump-artwork", ipod], capture_output=True, text=True, check=True
    ).stdout
    titles = {line.split("\t")[1] for line in out.splitlines() if line[:2] in ("K\t", "P\t")}
    assert titles == {"Водограй"}
    k = next(line.split("\t") for line in out.splitlines() if line.startswith("K\t"))
    assert int(k[2]) == int(k[3]) > 0  # libgpod decodes its thumbnails
