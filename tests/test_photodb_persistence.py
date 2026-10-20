"""PhotoDB thumbnails: device formats, file references, content preserved across saves (syncDirector
task 53)."""

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
    assert sizes == {1067: 720 * 480 * 3 // 2, 1024: 320 * 240 * 2, 1066: 64 * 64 * 2}
    db.save()
    again = PhotoDB(mp)
    [p] = again.photos
    assert {t.format_id: (t.width, t.height, t.ithmb_file) for t in p.thumbnails} == {
        1067: (720, 480, 1),
        1024: (320, 240, 1),
        1066: (64, 64, 1),
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
