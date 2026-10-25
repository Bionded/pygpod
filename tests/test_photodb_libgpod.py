"""Photo Database interop with libgpod.

The same scenario - four photos (JPEG and PNG, landscape and portrait, large and small), three
albums with Ukrainian names, one photo removed - is written once by pygpod (PhotoDB) and once by
libgpod (itdb_photodb_*, `libgpod_tool build-photos`) on emulated Classic 7G, nano 3G, nano 5G,
5G video and iPod Photo volumes. libgpod (`libgpod_tool dump-photos-full`) is the judge: it must
see the same photos, albums and thumbnail formats in pygpod's database, find and decode the
thumbnails, and pygpod must read (and re-save) libgpod's database without losing anything.
"""

import hashlib
import os
import random
import shutil
import struct
import subprocess
import time

import pytest

from pygpod import PhotoDB
from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.db.artwork_writer import write_artworkdb
from pygpod.device.models import IpodGeneration
from pygpod.device.mountpoint import init_ipod

PIL = pytest.importorskip("PIL.Image")

pytestmark = pytest.mark.libgpod

GUID = "000A2700213749FF"
MODELS = {
    "classic7g": "C293",  # 1067 720x480 I420, 1024 320x240, 1066 64x64
    "nano3g": "A978",  # same photo formats as the Classic
    "nano5g": "C027",  # 1087 384x384, 1079 80x80, 1066 64x64
    "video5g": "A002",  # 1036 50x41, 1015 130x88, 1024 320x240, 1019 720x480 UYVY (TV out)
    "photo4g": "A079",  # 1009 42x30, 1015 130x88, 1013 220x176 RGB565 BE, 1019 720x480 UYVY
}
ALL = list(MODELS)

# (file name, size, colour): JPEG and PNG, landscape and portrait, large and small
IMAGES = [
    ("Говерла.jpg", (1600, 1200), (40, 120, 60)),  # landscape, large, 4:3
    ("Ґанок.png", (300, 500), (180, 90, 30)),  # portrait, small
    ("Їжак.jpg", (1200, 1800), (90, 70, 50)),  # portrait, large
    ("Смерека.png", (40, 30), (20, 160, 90)),  # landscape, tiny
]
ALBUMS = [("Карпати", [0, 1]), ("Ґанок", [3, 0]), ("Їжаки", [1, 2, 3])]
REMOVED = 1
# what both libraries must end up with (photo indexes into IMAGES)
KEPT = [0, 2, 3]
EXPECTED_ALBUMS = [
    ("Photo Library", 1, [0, 2, 3]),
    ("Карпати", 2, [0]),
    ("Ґанок", 2, [3, 0]),
    ("Їжаки", 2, [2, 3]),
]


# -------------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------------
def _volume(path, model):
    os.makedirs(path)
    sysinfo = f"ModelNumStr: x{MODELS[model]}\nFirewireGuid: 0x{GUID}\n".encode()
    init_ipod(path, name="pygpod", sysinfo_files={"SysInfo": sysinfo})
    return path


def _dump(tool, mp):
    """libgpod's view: {"gen", "photo_support", "photos": [...], "albums": [(name, type, ids)]}."""
    out = subprocess.run(
        [tool, "dump-photos-full", mp], capture_output=True, text=True, check=True
    ).stdout
    res = {"photos": [], "albums": []}
    by_id = {}
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "D":
            res["gen"], res["photo_support"] = int(f[1]), f[2] == "1"
        elif f[0] == "I":
            photo = {
                "id": int(f[1]),
                "thumb_type": int(f[2]),
                "n_items": int(f[3]),
                "n_decoded": int(f[4]),
                "dbid": int(f[5]),
                "artwork_size": int(f[6]),
                "creation_date": int(f[7]),
                "digitized_date": int(f[8]),
                "rating": int(f[9]),
                "thumbs": {},
            }
            res["photos"].append(photo)
            by_id[photo["id"]] = photo
        elif f[0] == "F":
            by_id[int(f[1])]["thumbs"][int(f[2])] = {
                "width": int(f[3]),
                "height": int(f[4]),
                "size": int(f[5]),
                "offset": int(f[6]),
                "filename": f[7],
                "hpad": int(f[8]),
                "vpad": int(f[9]),
            }
        elif f[0] == "A":
            ids = [int(i) for i in f[4].split(",")] if f[4] else []
            assert len(ids) == int(f[3])
            res["albums"].append((f[1], int(f[2]), ids))
    return res


def _albums_by_position(dump):
    """Albums with members as positions in the photo list (libgpod renumbers ids on write)."""
    pos = {p["id"]: i for i, p in enumerate(dump["photos"])}
    return [(name, kind, [pos[i] for i in ids]) for name, kind, ids in dump["albums"]]


def _expected_by_position():
    return [(n, k, [KEPT.index(i) for i in m]) for n, k, m in EXPECTED_ALBUMS]


def _files(mp):
    """{relative path: sha1} of everything under Photos/."""
    out = {}
    for root, _, names in os.walk(os.path.join(mp, "Photos")):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, mp)] = hashlib.sha1(fh.read()).hexdigest()
    return out


def _without_filenames(photos):
    return [
        dict(p, thumbs={f: dict(t, filename=None) for f, t in p["thumbs"].items()}) for p in photos
    ]


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# -------------------------------------------------------------------------
# fixtures: one pygpod-written and one libgpod-written volume per model
# -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def images(tmp_path_factory):
    d = tmp_path_factory.mktemp("images")
    paths = []
    for name, size, colour in IMAGES:
        img = PIL.new("RGB", size, colour)
        # a lighter frame so letterboxing/cropping changes the pixels
        img.paste((240, 240, 240), (0, 0, size[0], max(1, size[1] // 10)))
        path = str(d / name)
        img.save(path)
        paths.append(path)
    return paths


@pytest.fixture(scope="module")
def built(tool, images, tmp_path_factory):
    """{model: (pygpod volume, libgpod volume)}, built lazily; tests copy before changing them."""
    cache = {}

    def get(model):
        if model in cache:
            return cache[model]
        base = tmp_path_factory.mktemp(model)
        py = _volume(str(base / "py"), model)
        db = PhotoDB(py)
        photos = [db.add_photo(p) for p in images]
        for name, members in ALBUMS:
            album = db.create_album(name)
            for i in members:
                db.add_photo_to_album(album, photos[i])
        db.remove_photo(photos[REMOVED])
        db.save()

        lg = _volume(str(base / "lg"), model)
        spec = base / "photos.tsv"
        lines = [f"photo\t{p}" for p in images]
        lines += [f"album\t{n}\t{','.join(map(str, m))}" for n, m in ALBUMS]
        lines.append(f"remove\t{REMOVED}")
        spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run([tool, "build-photos", lg, str(spec)], check=True)
        cache[model] = (py, lg)
        return cache[model]

    return get


@pytest.fixture
def copy_of(built, tmp_path):
    def get(model, which):
        src = built(model)[0 if which == "py" else 1]
        dst = str(tmp_path / which)
        shutil.copytree(src, dst)
        return dst

    return get


# -------------------------------------------------------------------------
# pygpod writes -> libgpod reads
# -------------------------------------------------------------------------
@pytest.mark.parametrize("model", ALL)
def test_libgpod_reads_pygpod_photos_and_albums(tool, built, model):
    py, _ = built(model)
    lg_view = _dump(tool, py)
    assert lg_view["photo_support"]
    db = PhotoDB(py)
    assert [p["id"] for p in lg_view["photos"]] == [p.photo_id for p in db.photos]
    assert len(db.photos) == len(KEPT)
    assert lg_view["albums"] == [(a.name, a.album_type, a.photo_ids) for a in db.albums]
    assert _albums_by_position(lg_view) == _expected_by_position()
    for p in lg_view["photos"]:
        assert p["thumb_type"] == 4  # ITDB_THUMB_TYPE_IPOD
        assert p["n_items"] == len(p["thumbs"]) > 0
        assert -1 not in p["thumbs"]  # every format id is known to libgpod for this model


@pytest.mark.parametrize("model", ALL)
def test_thumbnail_formats_match_libgpod(tool, built, model):
    py, lg = built(model)
    ours, ref = _dump(tool, py), _dump(tool, lg)
    assert [sorted(p["thumbs"]) for p in ours["photos"]] == [
        sorted(p["thumbs"]) for p in ref["photos"]
    ]


@pytest.mark.parametrize("model", ALL)
def test_thumbnail_sizes_match_libgpod(tool, built, model):
    py, lg = built(model)
    ours, ref = _dump(tool, py), _dump(tool, lg)
    for o, r in zip(ours["photos"], ref["photos"]):
        common = set(o["thumbs"]) & set(r["thumbs"])
        assert common
        assert {f: o["thumbs"][f]["size"] for f in common} == {
            f: r["thumbs"][f]["size"] for f in common
        }


@pytest.mark.parametrize("model", ALL)
def test_thumbnail_geometry_matches_libgpod(tool, built, model):
    py, lg = built(model)
    ours, ref = _dump(tool, py), _dump(tool, lg)

    def geometry(p, fmts):
        return {
            f: {k: p["thumbs"][f][k] for k in ("width", "height", "hpad", "vpad")} for f in fmts
        }

    for o, r in zip(ours["photos"], ref["photos"]):
        common = set(o["thumbs"]) & set(r["thumbs"])
        assert geometry(o, common) == geometry(r, common)


@pytest.mark.parametrize("model", ALL)
def test_libgpod_finds_and_decodes_pygpod_thumbnails(tool, built, model):
    py, _ = built(model)
    for p in _dump(tool, py)["photos"]:
        assert {t["filename"].rsplit(":", 1)[0] for t in p["thumbs"].values()} == {":Thumbs"}
        assert p["n_decoded"] == p["n_items"]


@pytest.mark.parametrize("model", ALL)
def test_new_photo_artwork_size_matches_libgpod(tool, built, images, model):
    py, lg = built(model)
    want = [os.path.getsize(images[i]) for i in KEPT]
    assert [p["artwork_size"] for p in _dump(tool, lg)["photos"]] == want  # libgpod's reference
    assert [p["artwork_size"] for p in _dump(tool, py)["photos"]] == want


@pytest.mark.parametrize("which", ["py", "lg"])
@pytest.mark.parametrize("model", ALL)
def test_ithmb_offsets_and_sizes_inside_the_files(tool, built, model, which):
    """Every thumbnail lies inside its .ithmb file under Photos/Thumbs; none overlap."""
    mp = built(model)[0 if which == "py" else 1]
    thumbs_dir = os.path.join(mp, "Photos", "Thumbs")
    spans = {}
    for p in _dump(tool, mp)["photos"]:
        for fmt, t in p["thumbs"].items():
            name = t["filename"].rsplit(":", 1)[-1]
            assert name.startswith(f"F{fmt}_") and name.endswith(".ithmb")
            path = os.path.join(thumbs_dir, name)
            assert os.path.isfile(path), name
            assert t["size"] > 0
            assert t["offset"] + t["size"] <= os.path.getsize(path)
            spans.setdefault(name, []).append((t["offset"], t["offset"] + t["size"]))
    assert spans
    for name, ranges in spans.items():
        ranges.sort()
        assert all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), name


# -------------------------------------------------------------------------
# libgpod writes -> pygpod reads / changes
# -------------------------------------------------------------------------
@pytest.mark.parametrize("model", ALL)
def test_pygpod_reads_libgpod_photodb(tool, built, model):
    _, lg = built(model)
    ref = _dump(tool, lg)
    assert _albums_by_position(ref) == _expected_by_position()
    assert all(p["n_decoded"] == p["n_items"] for p in ref["photos"])  # libgpod reads its own DB

    db = PhotoDB(lg)
    assert [p.photo_id for p in db.photos] == [p["id"] for p in ref["photos"]]
    assert [(a.name, a.album_type, a.photo_ids) for a in db.albums] == ref["albums"]
    photos_dir = os.path.join(lg, "Photos")
    for photo, r in zip(db.photos, ref["photos"]):
        got = {
            t.format_id: {
                "width": t.width,
                "height": t.height,
                "size": t.image_size,
                "offset": t.image_offset,
                "file": f"F{t.format_id}_{t.ithmb_file}.ithmb",
            }
            for t in photo.thumbnails
        }
        want = {
            f: {
                "width": t["width"],
                "height": t["height"],
                "size": t["size"],
                "offset": t["offset"],
                "file": t["filename"].rsplit(":", 1)[-1],
            }
            for f, t in r["thumbs"].items()
        }
        assert got == want
        for t in photo.thumbnails:
            assert len(t.read_data(photos_dir)) == t.image_size


@pytest.mark.parametrize("model", ALL)
def test_pygpod_adds_and_removes_in_libgpod_photodb(tool, copy_of, images, model):
    mp = copy_of(model, "lg")
    db = PhotoDB(mp)
    before = {
        p.photo_id: {t.format_id: t.read_data(os.path.join(mp, "Photos")) for t in p.thumbnails}
        for p in db.photos
    }
    new = db.add_photo(images[REMOVED])
    db.add_photo_to_album(db.album_by_name("Карпати"), new)
    gone = db.photos[0]
    db.remove_photo(gone)
    db.remove_album(db.album_by_name("Ґанок"))
    db.save()

    view = _dump(tool, mp)
    ids = [p["id"] for p in view["photos"]]
    assert ids == [p for p in before if p != gone.photo_id] + [new.photo_id]
    assert view["albums"] == [
        ("Photo Library", 1, ids),
        ("Карпати", 2, [new.photo_id]),
        ("Їжаки", 2, ids[:2]),
    ]
    again = PhotoDB(mp)
    for photo in again.photos:  # the kept photos' pixels are unchanged
        if photo.photo_id in before:
            assert {
                t.format_id: t.read_data(os.path.join(mp, "Photos")) for t in photo.thumbnails
            } == before[photo.photo_id]


@pytest.mark.parametrize("model", ALL)
def test_resave_of_libgpod_photodb_keeps_libgpod_fields(tool, copy_of, model):
    mp = copy_of(model, "lg")
    before = _dump(tool, mp)
    PhotoDB(mp).save()
    after = _dump(tool, mp)

    def fields(view):  # file names / decoding: test_libgpod_finds_and_decodes_pygpod_thumbnails
        return [
            (
                p["id"],
                p["artwork_size"],
                p["creation_date"],
                p["digitized_date"],
                p["rating"],
                {
                    f: {k: v for k, v in t.items() if k != "filename"}
                    for f, t in p["thumbs"].items()
                },
            )
            for p in view["photos"]
        ]

    assert after["albums"] == before["albums"]
    assert fields(after) == fields(before)


# -------------------------------------------------------------------------
# binary fidelity
# -------------------------------------------------------------------------
@pytest.mark.parametrize("which", ["py", "lg"])
@pytest.mark.parametrize("model", ALL)
def test_photo_database_round_trip_is_byte_identical(built, model, which):
    mp = built(model)[0 if which == "py" else 1]
    raw = _read(os.path.join(mp, "Photos", "Photo Database"))
    assert write_artworkdb(parse_artworkdb(raw)) == raw


@pytest.mark.parametrize("model", ALL)
def test_unchanged_resaves_keep_the_thumbnails(tool, copy_of, model):
    """Thumbnail pixels and libgpod's view (except the .ithmb index) survive unchanged saves."""
    mp = copy_of(model, "py")
    view = _dump(tool, mp)
    pixels = {
        (p.photo_id, t.format_id): t.read_data(os.path.join(mp, "Photos"))
        for p in PhotoDB(mp).photos
        for t in p.thumbnails
    }
    for _ in range(2):
        PhotoDB(mp).save()
        db = PhotoDB(mp)
        assert {
            (p.photo_id, t.format_id): t.read_data(os.path.join(mp, "Photos"))
            for p in db.photos
            for t in p.thumbnails
        } == pixels
        again = _dump(tool, mp)
        assert again["albums"] == view["albums"]
        assert _without_filenames(again["photos"]) == _without_filenames(view["photos"])


@pytest.mark.parametrize("model", ALL)
def test_unchanged_resave_is_byte_identical(copy_of, model):
    mp = copy_of(model, "py")
    before = _files(mp)
    PhotoDB(mp).save()
    assert _files(mp) == before


# unusual sources: colour modes PIL must convert, extreme aspect ratios, a single pixel
ODD_IMAGES = [
    ("Прозорий.png", "RGBA", (400, 300), (255, 0, 0, 128)),
    ("Палітра.png", "P", (320, 240), 5),
    ("Сірий.jpg", "L", (640, 480), 128),
    ("Друк.jpg", "CMYK", (500, 500), (0, 50, 100, 0)),
    ("Смуга.png", "RGB", (3000, 5), (0, 200, 0)),
    ("Вежа.png", "RGB", (4, 2000), (0, 0, 200)),
    ("Точка.png", "RGB", (1, 1), (255, 255, 255)),
]


@pytest.mark.parametrize("model", ALL)
def test_odd_images_decoded_by_libgpod_with_libgpod_geometry(tool, tmp_path, model):
    paths = []
    for name, mode, size, colour in ODD_IMAGES:
        path = str(tmp_path / name)
        PIL.new(mode, size, colour).save(path)
        paths.append(path)
    py = _volume(str(tmp_path / "py"), model)
    db = PhotoDB(py)
    for p in paths:
        db.add_photo(p)
    db.save()
    lg = _volume(str(tmp_path / "lg"), model)
    spec = tmp_path / "photos.tsv"
    spec.write_text("".join(f"photo\t{p}\n" for p in paths), encoding="utf-8")
    subprocess.run([tool, "build-photos", lg, str(spec)], check=True)

    ours, ref = _dump(tool, py), _dump(tool, lg)
    assert len(ours["photos"]) == len(ref["photos"]) == len(paths)
    keys = ("width", "height", "hpad", "vpad")
    for o, r in zip(ours["photos"], ref["photos"]):
        assert o["n_decoded"] == o["n_items"] > 0
        for fmt in set(o["thumbs"]) & set(r["thumbs"]):
            got = {k: o["thumbs"][fmt][k] for k in keys}
            assert got == {k: r["thumbs"][fmt][k] for k in keys}
            assert got["width"] > 0 and got["height"] > 0


# full-size thumbnails are encoded byte for byte like libgpod (same YCbCr conversion and layout);
# a noise image of exactly the slot size is not scaled, so the encoder alone is compared
FULL_SIZE_FORMATS = [
    ("classic7g", 1067, (720, 480)),
    ("nano3g", 1067, (720, 480)),
    ("video5g", 1019, (720, 480)),
    ("photo4g", 1019, (720, 480)),
    ("photo4g", 1013, (220, 176)),
]


@pytest.mark.parametrize("model,fmt,size", FULL_SIZE_FORMATS)
def test_full_size_thumbnail_bytes_match_libgpod(tool, tmp_path, model, fmt, size):
    rnd = random.Random(1)
    path = str(tmp_path / "Шум.png")
    noise = bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3))
    PIL.frombytes("RGB", size, noise).save(path)
    py = _volume(str(tmp_path / "py"), model)
    db = PhotoDB(py)
    db.add_photo(path)
    db.save()
    lg = _volume(str(tmp_path / "lg"), model)
    spec = tmp_path / "photos.tsv"
    spec.write_text(f"photo\t{path}\n", encoding="utf-8")
    subprocess.run([tool, "build-photos", lg, str(spec)], check=True)

    def pixels(mp):
        (thumb,) = [t for t in PhotoDB(mp).photos[0].thumbnails if t.format_id == fmt]
        assert (thumb.width, thumb.height) == size
        return thumb.read_data(os.path.join(mp, "Photos"))

    assert pixels(py) == pixels(lg)


@pytest.mark.parametrize("zone", ["UTC", "Asia/Tokyo", "America/New_York"])
def test_new_photo_dates_match_libgpod(tool, tmp_path, host_tz, zone):
    """creation_date is the file's modification time in the iPod's local time (libgpod: the
    host's UTC offset when the iPod has no timezone in its Preferences), digitized_date 0."""
    host_tz(zone)
    paths = []
    for name, mtime in (("Обійми.jpg", 1600000000), ("Ґанок.png", 1234567890)):
        path = str(tmp_path / name)
        PIL.new("RGB", (64, 48), (1, 2, 3)).save(path)
        os.utime(path, (1500000000, mtime))
        paths.append(path)
    py = _volume(str(tmp_path / "py"), "classic7g")
    db = PhotoDB(py)
    for p in paths:
        db.add_photo(p)
    db.save()
    lg = _volume(str(tmp_path / "lg"), "classic7g")
    spec = tmp_path / "photos.tsv"
    spec.write_text("".join(f"photo\t{p}\n" for p in paths), encoding="utf-8")
    subprocess.run([tool, "build-photos", lg, str(spec)], check=True, env=dict(os.environ))

    def dates(mp):
        return [(p.creation_date, p.digitized_date) for p in PhotoDB(mp).photos]

    shift = time.localtime().tm_gmtoff
    assert dates(lg) == [(m + 2082844800 + shift, 0) for m in (1600000000, 1234567890)]
    assert dates(py) == dates(lg)
    utc = _volume(str(tmp_path / "utc"), "classic7g")
    db = PhotoDB(utc, timestamps="utc")
    db.add_photo(paths[0])
    db.save()
    assert dates(utc) == [(1600000000 + 2082844800, 0)]


# -------------------------------------------------------------------------
# header fields the firmware checks but libgpod's reader ignores
# -------------------------------------------------------------------------
def _records(mp, magic):
    """(offset, header bytes, total length) of every record with this magic in the database."""
    with open(os.path.join(mp, "Photos", "Photo Database"), "rb") as fh:
        data = fh.read()
    pos, out = 0, []
    while True:
        pos = data.find(magic, pos)
        if pos < 0:
            return data, out
        hlen, total = struct.unpack_from("<II", data, pos + 4)
        out.append((pos, data[pos : pos + hlen], total))
        pos += 4


@pytest.mark.parametrize("model", ALL)
def test_mhod_padding_length_like_libgpod(tool, built, model):
    """A wrong padding length in any MHOD makes the iPod show no photos at all."""
    py, lg = built(model)
    _, ours = _records(py, b"mhod")
    _, ref = _records(lg, b"mhod")
    assert [h[0x0C] for _, h, _ in ours] == [h[0x0C] for _, h, _ in ref]
    assert [h[0x0F] for _, h, _ in ours] == [h[0x0F] for _, h, _ in ref]
    data, _ = _records(py, b"mhod")
    for pos, h, total in ours:
        if h[0x0C] in (1, 3):  # strings: header, length/encoding/unknown, string, padding
            (length,) = struct.unpack_from("<I", data, pos + len(h))
            assert h[0x0F] == total - len(h) - 12 - length < 4


@pytest.mark.parametrize("model", ALL)
def test_ids_and_previous_album_ids(built, model):
    py, _ = built(model)
    data, mhfd = _records(py, b"mhfd")
    photo_ids = [struct.unpack_from("<I", h, 0x10)[0] for _, h, _ in _records(py, b"mhii")[1]]
    mhbas = _records(py, b"mhba")[1]
    album_ids = [struct.unpack_from("<I", h, 0x14)[0] for _, h, _ in mhbas]
    assert min(album_ids) > max(photo_ids)  # one id space, albums after the photos
    assert album_ids == list(range(album_ids[0], album_ids[0] + len(album_ids)))
    assert struct.unpack_from("<I", mhfd[0][1], 0x1C)[0] == max(album_ids) + 1
    counts = [struct.unpack_from("<I", h, 0x10)[0] for _, h, _ in mhbas]
    prev = [struct.unpack_from("<I", h, 0x3C)[0] for _, h, _ in mhbas]
    # as iTunes writes them (libgpod too, at another offset on 64-bit builds)
    expected = [100, 101]
    for count in counts[1:-1]:
        expected.append(expected[-1] + count + 1)
    assert prev == expected[: len(prev)]


def test_many_photos_album_ids_do_not_collide(writable_ipod, images):
    db = PhotoDB(writable_ipod, generation=IpodGeneration.CLASSIC_1)
    photos = [db.add_photo(images[i % len(images)]) for i in range(40)]
    db.add_photo_to_album(db.create_album("Карпати"), photos[-1])
    db.save()
    again = PhotoDB(writable_ipod)
    ids = {p.photo_id for p in again.photos}
    assert not ids & {a.album_id for a in again.albums}
    assert again.album_by_name("Карпати").photo_ids == [photos[-1].photo_id]


def test_libgpod_reads_database_after_members_without_photo_dropped(tool, copy_of):
    """libgpod crashes on an album member whose photo is missing: pygpod drops such members on
    load, so re-saving a broken database makes it readable again."""
    from pygpod.db.photodb import write_artworkdb

    mp = copy_of("classic7g", "py")
    db = PhotoDB(mp)
    db.album_by_name("Карпати").photo_ids.append(9999)
    with open(os.path.join(mp, "Photos", "Photo Database"), "wb") as f:
        f.write(write_artworkdb(db._build_root()))
    PhotoDB(mp).save()
    assert _albums_by_position(_dump(tool, mp)) == _expected_by_position()
