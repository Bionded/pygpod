"""PhotoDB thumbnails: device formats, file references, content preserved across saves."""

import os

import pytest

from pygpod.db.photodb import PhotoDB
from pygpod.device.models import IpodGeneration
from pygpod.exceptions import ArtworkError

PIL = pytest.importorskip("PIL.Image")
CLASSIC = IpodGeneration.CLASSIC_1


def _image(path, color):
    PIL.new("RGB", (800, 600), color).save(path)
    return str(path)


def _thumbs_dir(mp):
    return os.path.join(mp, "Photos", "Thumbs")  # libgpod: <mountpoint>/Photos


def _content(db, mp):
    photos_dir = os.path.join(mp, "Photos")
    return {
        (p.photo_id, t.format_id): t.read_data(photos_dir) for p in db.photos for t in p.thumbnails
    }


def test_classic_formats_written_and_read_back(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "a.png", (200, 10, 10)))
    assert sorted(t.format_id for t in photo.thumbnails) == [1024, 1066, 1067]
    sizes = {t.format_id: t.image_size for t in photo.thumbnails}
    assert sizes == {1067: 720 * 480 * 2, 1024: 320 * 240 * 2, 1066: 64 * 64 * 2}
    db.save()
    again = PhotoDB(mp)
    [p] = again.photos
    # 4:3 letterboxed like libgpod: width/height include the left/top padding
    assert {
        t.format_id: (t.width, t.height, t.horizontal_padding, t.vertical_padding, t.ithmb_file)
        for t in p.thumbnails
    } == {
        1067: (680, 480, 40, 0, 1),
        1024: (320, 240, 0, 0, 1),
        1066: (64, 56, 0, 8, 1),
    }
    assert all(len(d) == sizes[f] for (_, f), d in _content(again, mp).items())
    assert again.master_album.photo_ids == [p.photo_id]


def test_existing_images_survive_add_delete_and_repeated_saves(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    db.save()
    first = _content(PhotoDB(mp), mp)

    db = PhotoDB(mp, generation=CLASSIC)
    b = db.add_photo(_image(tmp_path / "b.png", (0, 0, 255)))
    db.save()
    db = PhotoDB(mp, generation=CLASSIC)
    both = _content(db, mp)
    assert {k: v for k, v in both.items() if k[0] == a.photo_id} == first  # A not overwritten
    for fmt in (1024, 1066, 1067):
        assert both[(b.photo_id, fmt)] != both[(a.photo_id, fmt)]
    offsets = {(p.photo_id, t.format_id): t.image_offset for p in db.photos for t in p.thumbnails}
    assert offsets[(b.photo_id, 1024)] == 320 * 240 * 2  # appended after A
    # one file per format: the previous ones were replaced, not left behind
    assert sorted(os.listdir(_thumbs_dir(mp))) == sorted(
        f"F{f}_{db.photos[0].thumbnails[0].ithmb_file}.ithmb" for f in (1024, 1066, 1067)
    )

    for _ in range(3):  # saves without changes keep everything
        db = PhotoDB(mp, generation=CLASSIC)
        db.save()
        assert _content(PhotoDB(mp), mp) == both

    db = PhotoDB(mp, generation=CLASSIC)
    db.remove_photo(db.get_photo(a.photo_id))
    db.save()
    db = PhotoDB(mp)
    after = _content(db, mp)
    assert after == {k: v for k, v in both.items() if k[0] == b.photo_id}
    assert all(t.image_offset == 0 for p in db.photos for t in p.thumbnails)
    f1024 = [n for n in os.listdir(_thumbs_dir(mp)) if n.startswith("F1024_")]
    assert len(f1024) == 1
    assert os.path.getsize(os.path.join(_thumbs_dir(mp), f1024[0])) == 320 * 240 * 2


def test_unknown_generation_refuses_instead_of_empty_photo(tmp_path):
    db = PhotoDB(generation=IpodGeneration.SHUFFLE_1)
    with pytest.raises(ArtworkError, match="no photo thumbnail formats"):
        db.add_photo(_image(tmp_path / "a.png", (1, 2, 3)))
    assert db.photos == [] and db.albums == []


@pytest.mark.libgpod
def test_libgpod_reads_the_photo_database(tool, writable_ipod, tmp_path):
    import subprocess

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    b = db.add_photo(_image(tmp_path / "b.png", (0, 255, 0)))
    trip = db.create_album("Відпустка")
    db.add_photo_to_album(trip, b)
    db.save()
    out = subprocess.run([tool, "dump-photos", mp], capture_output=True, text=True, check=True)
    lines = out.stdout.splitlines()
    assert sorted(ln for ln in lines if ln.startswith("I")) == sorted(
        [f"I\t{a.photo_id}\t1", f"I\t{b.photo_id}\t1"]
    )
    albums = [ln.split("\t") for ln in lines if ln.startswith("A")]
    assert ["A", "Photo Library", "1", f"{a.photo_id},{b.photo_id}"] in albums
    assert ["A", "Відпустка", "2", f"{b.photo_id}"] in albums


def test_old_location_is_read_and_moved(writable_ipod, tmp_path):
    """pygpod used to write iPod_Control/Photos; libgpod and the iPod use <mountpoint>/Photos."""
    import shutil

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "a.png", (9, 9, 9)))
    db.save()
    before = _content(PhotoDB(mp), mp)
    shutil.rmtree(os.path.join(mp, "iPod_Control", "Photos"), ignore_errors=True)
    shutil.move(os.path.join(mp, "Photos"), os.path.join(mp, "iPod_Control", "Photos"))
    old = PhotoDB(mp, generation=CLASSIC)
    assert [p.photo_id for p in old.photos] == [a.photo_id]
    old.save()
    assert not os.path.exists(os.path.join(mp, "iPod_Control", "Photos"))
    assert _content(PhotoDB(mp), mp) == before


def test_photo_fields_and_padding_survive_saves(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "Смерека.png", (20, 160, 90)))
    photo.rating, photo.creation_date, photo.digitized_date = 80, 3875698414, 3875698000
    photo.artwork_size = 12345
    db.save()
    for _ in range(2):
        PhotoDB(mp).save()
    [again] = PhotoDB(mp).photos
    assert (again.rating, again.creation_date, again.digitized_date, again.artwork_size) == (
        80,
        3875698414,
        3875698000,
        12345,
    )
    assert {t.format_id: (t.horizontal_padding, t.vertical_padding) for t in again.thumbnails} == {
        1067: (40, 0),
        1024: (0, 0),
        1066: (0, 8),
    }


def test_new_photo_artwork_size_is_the_source_size(writable_ipod, tmp_path):
    path = _image(tmp_path / "Говерла.jpg", (40, 120, 60))
    with open(path, "rb") as fh:
        data = fh.read()
    db = PhotoDB(writable_ipod, generation=CLASSIC)
    from_path = db.add_photo(path)
    from_bytes = db.add_photo(data)
    assert from_path.artwork_size == from_bytes.artwork_size == os.path.getsize(path) == len(data)
    assert from_path.original_path == path and from_bytes.original_path == ""
    assert [(t.format_id, t.width, t.height, t._pixel_data) for t in from_bytes.thumbnails] == [
        (t.format_id, t.width, t.height, t._pixel_data) for t in from_path.thumbnails
    ]
    db.save()
    assert [p.artwork_size for p in PhotoDB(writable_ipod).photos] == [len(data)] * 2
    with pytest.raises(ArtworkError):
        db.add_photo(b"")


def test_new_photo_creation_date_is_the_file_mtime(writable_ipod, tmp_path):
    from pygpod.utils.timezone import device_timezone_shift

    mac_epoch = 2082844800 + device_timezone_shift(writable_ipod)  # the iPod's local time
    path = _image(tmp_path / "Обійми.jpg", (40, 120, 60))
    os.utime(path, (1500000000, 1600000000))
    future = _image(tmp_path / "Ґанок.png", (180, 90, 30))
    os.utime(future, (0, 2**32))  # after 2040: no 32-bit Mac timestamp
    with open(path, "rb") as fh:
        data = fh.read()
    db = PhotoDB(writable_ipod, generation=CLASSIC)
    assert db.add_photo(path).creation_date == 1600000000 + mac_epoch
    assert db.add_photo(data).creation_date == 0  # no file, no date
    assert db.add_photo(future).creation_date == 0
    assert all(p.digitized_date == 0 for p in db.photos)
    db.save()
    assert [p.creation_date for p in PhotoDB(writable_ipod).photos] == [
        1600000000 + mac_epoch,
        0,
        0,
    ]


# -------------------------------------------------------------------------
# exactly one master album (Photo Library)
# -------------------------------------------------------------------------
def _empty_master(mp, tmp_path):
    """Photos and album written, then all removed: an empty master album stays."""
    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    album = db.create_album("Ґанок")
    db.add_photo_to_album(album, photo)
    db.save()
    db = PhotoDB(mp, generation=CLASSIC)
    for pid in list(db.album_by_name("Ґанок").photo_ids):
        db.remove_photo(db.get_photo(pid))
    db.remove_album(db.album_by_name("Ґанок"))
    db.save()
    return PhotoDB(mp, generation=CLASSIC)


def test_photo_added_to_an_empty_master_album(writable_ipod, tmp_path):
    mp = writable_ipod
    db = _empty_master(mp, tmp_path)
    [master] = db.albums
    assert master.is_master and master.photo_ids == []
    photo = db.add_photo(_image(tmp_path / "b.png", (0, 255, 0)))
    assert db.albums == [master] and master.photo_ids == [photo.photo_id]
    db.save()
    [master] = PhotoDB(mp).albums
    assert (master.is_master, master.photo_ids) == (True, [photo.photo_id])


@pytest.mark.libgpod
def test_libgpod_sees_one_master_album_after_refill(tool, writable_ipod, tmp_path):
    import subprocess

    mp = writable_ipod
    db = _empty_master(mp, tmp_path)
    photo = db.add_photo(_image(tmp_path / "b.png", (0, 255, 0)))
    db.save()
    out = subprocess.run([tool, "dump-photos", mp], capture_output=True, text=True, check=True)
    albums = [ln.split("\t") for ln in out.stdout.splitlines() if ln.startswith("A")]
    assert albums == [["A", "Photo Library", "1", f"{photo.photo_id}"]]


@pytest.fixture
def two_masters(writable_ipod, tmp_path):
    """A Photo Database with two master albums, as older versions wrote it."""
    from pygpod.db.photodb import ALBUM_TYPE_MASTER, PhotoAlbum

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    b = db.add_photo(_image(tmp_path / "b.png", (0, 255, 0)))
    c = db.add_photo(_image(tmp_path / "c.png", (0, 0, 255)))
    second = PhotoAlbum("Photo Library", ALBUM_TYPE_MASTER)
    second.album_id = 200
    second.photo_ids = [b.photo_id, c.photo_id]  # one shared, one only here
    db.master_album.photo_ids = [b.photo_id, a.photo_id]
    db._albums.append(second)
    db.save()
    return mp, [b.photo_id, a.photo_id, c.photo_id]


def test_several_master_albums_are_merged_on_load(two_masters, caplog):
    mp, order = two_masters
    with caplog.at_level("WARNING", logger="pygpod.db.photodb"):
        db = PhotoDB(mp)
    assert "2 master photo albums" in caplog.text
    [master] = db.albums
    assert master.album_id == db.master_album.album_id != 200
    assert master.photo_ids == order


def test_doctor_reports_and_merges_several_master_albums(two_masters):
    from pygpod import Database

    mp, order = two_masters
    report = Database(mp, recover=False).check()
    (issue,) = report.by_kind("photo_master_albums")
    assert (issue.severity, issue.fixable) == ("error", True)
    assert "2 master photo albums" in issue.message
    db = Database(mp)
    actions = db.repair(report)
    assert [a.done for a in actions if a.kind == "photo_master_albums"] == [True]
    assert not Database(mp, recover=False).check().by_kind("photo_master_albums")
    albums = [a for a in PhotoDB(mp).albums if a.is_master]
    assert [a.photo_ids for a in albums] == [order]


def test_save_repairs_mhod_padding_of_older_databases(writable_ipod, tmp_path):
    """Older versions left the MHOD padding length at 0: the iPod showed no photos."""
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    db.add_photo_to_album(db.create_album("Обійми"), photo)
    db.save()
    path = os.path.join(mp, "Photos", "Photo Database")
    with open(path, "rb") as f:
        good = f.read()
    mhods = [i for i in range(len(good)) if good[i : i + 4] == b"mhod"]
    broken = bytearray(good)
    for pos in mhods:
        broken[pos + 0x0F] = 0
    assert broken != good
    with open(path, "wb") as f:
        f.write(broken)
    again = PhotoDB(mp)
    assert [t.ithmb_file for t in again.photos[0].thumbnails] == [1, 1, 1]
    again.save()  # rewritten (thumbnails into new .ithmb files), padding as before
    with open(path, "rb") as f:
        fixed = f.read()
    assert [fixed[p + 0x0F] for p in mhods] == [good[p + 0x0F] for p in mhods]
    assert any(good[p + 0x0F] for p in mhods)


def test_album_member_without_photo_not_written(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    album = db.create_album("Карпати")
    db.add_photo_to_album(album, db.add_photo(_image(tmp_path / "a.png", (255, 0, 0))))
    db.save()
    # a photo of another PhotoDB instance of the same iPod, never added to this one
    foreign = PhotoDB(mp).add_photo(_image(tmp_path / "b.png", (0, 255, 0)))
    db = PhotoDB(mp)
    try:
        db.add_photo_to_album(db.album_by_name("Карпати"), foreign)
    except ValueError:
        pass
    db.save()
    again = PhotoDB(mp)
    ids = {p.photo_id for p in again.photos}
    assert all(set(a.photo_ids) <= ids for a in again.albums)


def test_add_photo_to_album_rejects_a_photo_of_another_database(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    album = db.create_album("Карпати")
    own = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    foreign = PhotoDB(mp, generation=CLASSIC).add_photo(_image(tmp_path / "b.png", (0, 9, 0)))
    foreign.photo_id = own.photo_id + 50
    with pytest.raises(ValueError, match="not in this Photo Database"):
        db.add_photo_to_album(album, foreign)
    assert album.photo_ids == []
    db.add_photo_to_album(album, own)
    assert album.photo_ids == [own.photo_id]


def _write_dangling(mp, tmp_path, missing=9999):
    """A Photo Database whose album "Ґанок" also lists a photo id that is not in it."""
    from pygpod.db.photodb import write_artworkdb

    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    album = db.create_album("Ґанок")
    db.add_photo_to_album(album, photo)
    db.save()
    album.photo_ids.append(missing)
    db.master_album.photo_ids.append(missing)
    with open(os.path.join(mp, "Photos", "Photo Database"), "wb") as f:
        f.write(write_artworkdb(db._build_root()))
    return photo.photo_id


def test_members_without_photo_dropped_on_load_and_save(writable_ipod, tmp_path, caplog):
    mp = writable_ipod
    pid = _write_dangling(mp, tmp_path)
    with caplog.at_level("WARNING", logger="pygpod.db.photodb"):
        db = PhotoDB(mp)
    assert "members without a photo dropped (9999)" in caplog.text
    assert db._dropped_members == 2
    assert [a.photo_ids for a in db.albums] == [[pid], [pid]]
    db.save()  # differs from the disk: rewritten without the dangling members
    again = PhotoDB(mp)
    assert again._dropped_members == 0
    assert [a.photo_ids for a in again.albums] == [[pid], [pid]]


def test_save_drops_ids_appended_directly(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    album = db.create_album("Обійми")
    album.photo_ids += [photo.photo_id, 9999]
    db.save()
    assert album.photo_ids == [photo.photo_id]
    assert PhotoDB(mp).album_by_name("Обійми").photo_ids == [photo.photo_id]


def test_master_album_merge_drops_ids_without_photo(two_masters):
    from pygpod.db.photodb import write_artworkdb

    mp, order = two_masters
    db = PhotoDB(mp)  # merged; write two masters again, the second with a missing id
    from pygpod.db.photodb import ALBUM_TYPE_MASTER, PhotoAlbum

    second = PhotoAlbum("Photo Library", ALBUM_TYPE_MASTER)
    second.photo_ids = [order[0], 9999]
    db._albums.append(second)
    with open(os.path.join(mp, "Photos", "Photo Database"), "wb") as f:
        f.write(write_artworkdb(db._build_root()))
    again = PhotoDB(mp)
    assert (again._merged_masters, again._dropped_members) == (1, 1)
    assert [a.photo_ids for a in again.albums] == [order]


def test_doctor_reports_and_repairs_members_without_photo(writable_ipod, tmp_path):
    from pygpod import Database

    mp = writable_ipod
    pid = _write_dangling(mp, tmp_path)
    report = Database(mp, recover=False).check()
    (issue,) = report.by_kind("photo_album_missing_photo")
    assert (issue.severity, issue.fixable) == ("error", True)
    assert "2 photo album member(s) reference no photo" in issue.message
    actions = Database(mp).repair(report)
    assert [a.done for a in actions if a.kind == "photo_album_missing_photo"] == [True]
    assert not Database(mp, recover=False).check().by_kind("photo_album_missing_photo")
    assert [a.photo_ids for a in PhotoDB(mp).albums] == [[pid], [pid]]


def _albums_on_disk(mp):
    return [(a.name, a.album_type, a.photo_ids) for a in PhotoDB(mp).albums]


def _db_bytes(mp):
    with open(os.path.join(mp, "Photos", "Photo Database"), "rb") as fh:
        return fh.read()


def test_albums_without_photos_get_a_master_album_first(writable_ipod):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.create_album("Порожній")
    db.save()
    assert _albums_on_disk(mp) == [("Photo Library", 1, []), ("Порожній", 2, [])]
    before = _db_bytes(mp)
    PhotoDB(mp).save()  # a database with its master album stays byte-identical
    assert _db_bytes(mp) == before


def test_libgpod_sees_the_master_album_of_a_database_without_photos(tool, writable_ipod, tmp_path):
    import shutil
    import subprocess

    def albums(mp):
        out = subprocess.run([tool, "dump-photos", mp], capture_output=True, text=True, check=True)
        return [ln for ln in out.stdout.splitlines() if ln.startswith("A")]

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.create_album("Порожній")
    db.save()

    lg = str(tmp_path / "lg")
    shutil.copytree(mp, lg, ignore=shutil.ignore_patterns("Photos"))
    spec = tmp_path / "photos.tsv"
    spec.write_text("album\tПорожній\t\n", encoding="utf-8")
    subprocess.run([tool, "build-photos", lg, str(spec)], check=True)
    assert albums(mp) == albums(lg) == ["A\tPhoto Library\t1\t", "A\tПорожній\t2\t"]


@pytest.fixture
def no_master(writable_ipod, monkeypatch):
    """A Photo Database with one user album and no master album, as older versions wrote it."""
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.create_album("Порожній")
    with monkeypatch.context() as m:
        m.setattr(PhotoDB, "_ensure_master_album", lambda self: None)
        db.save()
    assert _albums_on_disk(mp) == [("Порожній", 2, [])]
    return mp


def test_save_adds_the_missing_master_album(no_master):
    db = PhotoDB(no_master)
    assert db.master_album is None
    db.save()
    assert _albums_on_disk(no_master) == [("Photo Library", 1, []), ("Порожній", 2, [])]


def test_doctor_reports_and_repairs_a_missing_master_album(no_master):
    from pygpod import Database

    report = Database(no_master, recover=False).check()
    (issue,) = report.by_kind("photo_master_album_missing")
    assert (issue.severity, issue.fixable) == ("error", True)
    actions = Database(no_master).repair(report)
    assert [a.done for a in actions if a.kind == "photo_master_album_missing"] == [True]
    assert not Database(no_master, recover=False).check().by_kind("photo_master_album_missing")
    assert _albums_on_disk(no_master)[0] == ("Photo Library", 1, [])


def test_recreated_master_album_lists_every_photo(writable_ipod, tmp_path, monkeypatch):
    from pygpod import Database

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    photo = db.add_photo(_image(tmp_path / "Літо.png", (20, 160, 40)))
    album = db.create_album("Літо")
    db.add_photo_to_album(album, photo)
    db._albums.remove(db.master_album)
    with monkeypatch.context() as m:
        m.setattr(PhotoDB, "_ensure_master_album", lambda self: None)
        db.save()
    assert _albums_on_disk(mp) == [("Літо", 2, [photo.photo_id])]
    Database(mp).repair(Database(mp, recover=False).check())
    assert _albums_on_disk(mp)[0] == ("Photo Library", 1, [photo.photo_id])


def test_recreated_master_album_seen_by_libgpod(tool, writable_ipod, tmp_path, monkeypatch):
    import subprocess

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    photos = [
        db.add_photo(_image(tmp_path / f"{n}.png", c))
        for n, c in (("а", (9, 9, 9)), ("б", (200, 1, 1)))
    ]
    album = db.create_album("Літо")
    db.add_photo_to_album(album, photos[1])
    db._albums.remove(db.master_album)
    with monkeypatch.context() as m:
        m.setattr(PhotoDB, "_ensure_master_album", lambda self: None)
        db.save()
    PhotoDB(mp).save()
    out = subprocess.run([tool, "dump-photos", mp], capture_output=True, text=True, check=True)
    albums = [ln for ln in out.stdout.splitlines() if ln.startswith("A")]
    ids = ",".join(str(p.photo_id) for p in photos)
    assert albums == [f"A\tPhoto Library\t1\t{ids}", f"A\tЛіто\t2\t{photos[1].photo_id}"]


@pytest.fixture
def photo_outside_master(writable_ipod, tmp_path, monkeypatch):
    """A Photo Database whose master album lacks one of its two photos."""
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    first = db.add_photo(_image(tmp_path / "перше.png", (20, 160, 40)))
    second = db.add_photo(_image(tmp_path / "друге.png", (160, 20, 40)))
    db.remove_photo_from_album(db.master_album, second)
    db.save()
    with monkeypatch.context() as m:  # as written, without the fix on load
        m.setattr(PhotoDB, "_add_missing_to_master", lambda self: 0)
        assert _albums_on_disk(mp) == [("Photo Library", 1, [first.photo_id])]
    return mp, [first.photo_id, second.photo_id]


def test_photos_outside_the_master_album_are_added_on_load(photo_outside_master):
    mp, ids = photo_outside_master
    db = PhotoDB(mp)
    assert db.master_album.photo_ids == ids
    db.save()
    assert _albums_on_disk(mp) == [("Photo Library", 1, ids)]


def test_doctor_reports_and_repairs_photos_outside_the_master_album(photo_outside_master):
    from pygpod import Database

    mp, ids = photo_outside_master
    report = Database(mp, recover=False).check()
    (issue,) = report.by_kind("photo_not_in_master")
    assert (issue.severity, issue.fixable) == ("error", True) and "1 photo" in issue.message
    actions = Database(mp).repair(report)
    assert [a.done for a in actions if a.kind == "photo_not_in_master"] == [True]
    assert _albums_on_disk(mp) == [("Photo Library", 1, ids)]
    assert not Database(mp, recover=False).check().by_kind("photo_not_in_master")


def test_complete_master_album_resave_is_byte_identical(writable_ipod, tmp_path):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.add_photo(_image(tmp_path / "ціле.png", (1, 2, 3)))
    db.save()
    before = _db_bytes(mp)
    loaded = PhotoDB(mp)
    assert loaded._not_in_master == 0
    loaded.save()
    assert _db_bytes(mp) == before


# ---- failed saves: the database on the iPod and its thumbnails stay intact ----


def _photo_files(mp):
    """Every file below Photos/ (relative path -> bytes)."""
    root = os.path.join(mp, "Photos")
    out = {}
    for d, _, names in os.walk(root):
        for name in names:
            path = os.path.join(d, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = fh.read()
    return out


def _refs(db):
    return [
        (p.photo_id, t.format_id, t.ithmb_file, t.image_offset, t.image_size)
        for p in db.photos
        for t in p.thumbnails
    ]


def _fail_database_write(monkeypatch):
    """Make writing the Photo Database file fail (e.g. the iPod is full or unplugged)."""
    from pygpod.db.transaction import Transaction

    real = Transaction.stage_bytes

    def stage_bytes(self, target, data):
        if os.path.basename(target) == "Photo Database":
            raise OSError(28, "No space left on device")
        return real(self, target, data)

    monkeypatch.setattr(Transaction, "stage_bytes", stage_bytes)


def test_failed_database_writes_keep_old_thumbnails_and_retry_is_safe(
    writable_ipod, tmp_path, monkeypatch
):
    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "червоне.png", (255, 0, 0)))
    db.save()
    on_disk = _photo_files(mp)
    first = _content(PhotoDB(mp), mp)

    db = PhotoDB(mp, generation=CLASSIC)
    b = db.add_photo(_image(tmp_path / "синє.png", (0, 0, 255)))
    new_pixels = {t.format_id: t._pixel_data for t in b.thumbnails}
    refs = _refs(db)
    with monkeypatch.context() as m:
        _fail_database_write(m)
        for _ in range(2):  # the second attempt must not reuse the first one's addresses
            with pytest.raises(OSError):
                db.save()
            assert _photo_files(mp) == on_disk  # byte-identical, no staging files left
            assert _refs(db) == refs
            assert {t.format_id: t._pixel_data for t in b.thumbnails} == new_pixels
            assert _content(PhotoDB(mp), mp) == first

    db.save()
    content = _content(PhotoDB(mp), mp)
    assert {k: v for k, v in content.items() if k[0] == a.photo_id} == first
    assert {f: v for (pid, f), v in content.items() if pid == b.photo_id} == new_pixels
    names = sorted(os.listdir(_thumbs_dir(mp)))
    assert len(names) == 3 and all(n.endswith(".ithmb") for n in names)  # old files removed


def test_failed_commit_keeps_old_thumbnails(writable_ipod, tmp_path, monkeypatch):
    from pygpod.db.transaction import Transaction

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.add_photo(_image(tmp_path / "a.png", (10, 200, 10)))
    db.save()
    on_disk = _photo_files(mp)

    db = PhotoDB(mp, generation=CLASSIC)
    db.remove_photo(db.photos[0])
    db.add_photo(_image(tmp_path / "b.png", (200, 10, 200)))
    refs = _refs(db)

    def commit(self):
        raise OSError(5, "Input/output error")

    with monkeypatch.context() as m:
        m.setattr(Transaction, "commit", commit)
        with pytest.raises(OSError):
            db.save()
    assert _photo_files(mp) == on_disk
    assert _refs(db) == refs
    db.save()
    [p] = PhotoDB(mp).photos
    assert all(d for d in _content(PhotoDB(mp), mp).values())
    assert len(os.listdir(_thumbs_dir(mp))) == len(p.thumbnails)


def test_save_refuses_while_an_interrupted_commit_is_pending(writable_ipod, tmp_path):
    from pygpod.db.transaction import JOURNAL_NAME

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.add_photo(_image(tmp_path / "a.png", (1, 2, 3)))
    journal = os.path.join(mp, "iPod_Control", "iTunes", JOURNAL_NAME)
    with open(journal, "w") as fh:
        fh.write('{"replace": [], "delete": []}')
    with pytest.raises(ArtworkError, match="interrupted save"):
        db.save()  # writing its own journal would lose the pending one
    assert not os.path.exists(os.path.join(mp, "Photos", "Photo Database"))


@pytest.mark.parametrize("photos, thumbs", [("Photos", "Thumbs"), ("PHOTOS", "THUMBS")])
def test_recover_removes_staged_photo_files(writable_ipod, photos, thumbs):
    from pygpod.db import transaction

    mp = writable_ipod
    thumbs_dir = os.path.join(mp, photos, thumbs)
    os.makedirs(thumbs_dir)
    staged = [
        os.path.join(mp, photos, "Photo Database" + transaction.STAGING_SUFFIX),
        os.path.join(thumbs_dir, "F1024_2.ithmb" + transaction.STAGING_SUFFIX),
    ]
    for path in staged:
        with open(path, "wb") as fh:
            fh.write(b"x")
    assert sorted(transaction.leftover_files(mp)) == sorted(staged)
    assert transaction.recover(mp) == "rolled-back"
    assert not any(os.path.exists(p) for p in staged)


@pytest.mark.libgpod
def test_libgpod_reads_the_database_saved_after_a_failed_save(
    tool, writable_ipod, tmp_path, monkeypatch
):
    import subprocess

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    db.save()
    db = PhotoDB(mp, generation=CLASSIC)
    b = db.add_photo(_image(tmp_path / "b.png", (0, 255, 0)))
    with monkeypatch.context() as m:
        _fail_database_write(m)
        with pytest.raises(OSError):
            db.save()
    db.save()
    out = subprocess.run([tool, "dump-photos", mp], capture_output=True, text=True, check=True)
    assert sorted(ln for ln in out.stdout.splitlines() if ln.startswith("I")) == sorted(
        [f"I\t{a.photo_id}\t1", f"I\t{b.photo_id}\t1"]
    )


def test_crash_after_the_commit_point_rolls_forward(writable_ipod, tmp_path, monkeypatch):
    from pygpod.db import transaction

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    a = db.add_photo(_image(tmp_path / "a.png", (255, 0, 0)))
    db.save()
    first = _content(PhotoDB(mp), mp)
    db = PhotoDB(mp, generation=CLASSIC)
    b = db.add_photo(_image(tmp_path / "b.png", (0, 0, 255)))
    new_pixels = {t.format_id: t._pixel_data for t in b.thumbnails}

    def crash(mountpoint):
        raise OSError(5, "Input/output error")

    with monkeypatch.context() as m:
        m.setattr(transaction, "_apply_journal", crash)
        with pytest.raises(OSError):
            db.save()
    assert transaction.recover(mp) == "rolled-forward"
    assert _refs(PhotoDB(mp)) == _refs(db)  # the journal took effect: the new addresses hold
    content = _content(PhotoDB(mp), mp)
    assert {k: v for k, v in content.items() if k[0] == a.photo_id} == first
    assert {f: v for (pid, f), v in content.items() if pid == b.photo_id} == new_pixels


def test_save_waits_for_no_other_writer(writable_ipod, tmp_path):
    from pygpod.db import writelock
    from pygpod.exceptions import ConflictError

    mp = writable_ipod
    db = PhotoDB(mp, generation=CLASSIC)
    db.add_photo(_image(tmp_path / "зелене.png", (0, 255, 0)))
    before = _photo_files(mp)
    with writelock.writer_lock(os.path.join(mp, "iPod_Control", "iTunes")):
        with pytest.raises(ConflictError):  # e.g. a Database.save() of another process
            db.save()
    assert _photo_files(mp) == before
    db.save()
    assert len(PhotoDB(mp).photos) == 1
