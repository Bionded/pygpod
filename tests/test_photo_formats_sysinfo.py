"""Photo thumbnail formats from the device's SysInfoExtended, as libgpod reads them.

A Classic 7G's SysInfoExtended (serial number and FireWire GUID replaced) lists its photo
formats under ImageSpecifications: 1067 is 640x480 there (the static table says 720x480, which
the firmware does not show), 1066 is cropped to fill 64x64. libgpod uses these
specifications whenever the file has them; pygpod must write the same formats, geometry and
order, which libgpod (`libgpod_tool build-photos` / `dump-photos-full`) checks.
"""

import os
import pathlib
import subprocess

import pytest

from pygpod import PhotoDB
from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.device.artwork_formats import (
    SPEC_KEY_CHAPTER_IMAGES,
    SPEC_KEY_COVER_ART,
    SPEC_KEY_PHOTOS,
    ArtworkFormatInfo,
    ThumbFormat,
    aligned_width,
    formats_from_sysinfo_extended,
    get_photo_formats,
    image_data_size,
)
from pygpod.device.device import Device
from pygpod.device.models import IpodGeneration
from pygpod.device.mountpoint import init_ipod
from pygpod.model.sysinfo import parse_sysinfo_extended

PIL = pytest.importorskip("PIL.Image")

FIXTURE = pathlib.Path(__file__).parent / "data" / "classic7g_SysInfoExtended"
GUID = "000A27001A2B3C4D"

L565, I420 = ThumbFormat.RGB565_LE, ThumbFormat.I420_LE
WHITE = (255, 255, 255)


def _ext():
    return parse_sysinfo_extended(str(FIXTURE))


def _volume(path, ext=True):
    os.makedirs(path)
    files = {"SysInfo": f"FirewireGuid: 0x{GUID}\n".encode()}
    if ext:
        files["SysInfoExtended"] = FIXTURE.read_bytes()
    init_ipod(path, name="pygpod", sysinfo_files=files)
    return path


# landscape, portrait, square, very wide - Ukrainian names
IMAGES = [
    ("Говерла.jpg", (1200, 800), (40, 120, 60)),
    ("Ґанок.png", (600, 900), (180, 90, 30)),
    ("Їжак.jpg", (500, 500), (20, 20, 200)),
    ("Смерека.png", (1000, 200), (200, 200, 30)),
]


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    d = tmp_path_factory.mktemp("images")
    paths = []
    for name, size, colour in IMAGES:
        img = PIL.new("RGB", size, colour)
        img.paste((240, 240, 240), (0, 0, size[0], size[1] // 10))
        path = str(d / name)
        img.save(path)
        paths.append(path)
    return paths


# -------------------------------------------------------------------------
# parsing the specifications
# -------------------------------------------------------------------------
def test_photo_specifications_of_the_classic():
    assert formats_from_sysinfo_extended(_ext(), SPEC_KEY_PHOTOS) == [
        ArtworkFormatInfo(1067, 640, 480, I420),
        ArtworkFormatInfo(1024, 320, 240, L565, row_bytes_alignment=4),
        ArtworkFormatInfo(1066, 64, 64, L565, crop=True, row_bytes_alignment=4),
    ]


def test_cover_art_and_chapter_specifications_of_the_classic():
    assert formats_from_sysinfo_extended(_ext(), SPEC_KEY_COVER_ART) == [
        ArtworkFormatInfo(1069, 142, 142, L565, row_bytes_alignment=4),
        ArtworkFormatInfo(1055, 128, 128, L565, crop=True, row_bytes_alignment=4),
        ArtworkFormatInfo(1068, 128, 128, L565, back_color=WHITE, row_bytes_alignment=4),
        ArtworkFormatInfo(1060, 320, 320, L565, crop=True, row_bytes_alignment=4),
        ArtworkFormatInfo(1061, 55, 55, L565, row_bytes_alignment=4),
    ]
    assert [
        (f.format_id, f.back_color)
        for f in formats_from_sysinfo_extended(_ext(), SPEC_KEY_CHAPTER_IMAGES)
    ] == [(1055, WHITE), (1029, WHITE)]


def _spec(**kw):
    spec = {"FormatId": 2000, "RenderWidth": 10, "RenderHeight": 8, "PixelFormat": "4C353635"}
    spec.update(kw)
    return {k: v for k, v in spec.items() if v is not None}


@pytest.mark.parametrize(
    "code, fmt",
    [
        ("32767579", ThumbFormat.UYVY_BE),
        ("42353635", ThumbFormat.RGB565_BE),
        ("4C353635", ThumbFormat.RGB565_LE),
        ("4c353635", ThumbFormat.RGB565_LE),
        ("79343230", ThumbFormat.I420_LE),
        ("4C353535", ThumbFormat.RGB555_LE),
    ],
)
def test_pixel_format_codes(code, fmt):
    (got,) = formats_from_sysinfo_extended({"X": [_spec(PixelFormat=code)]}, "X")
    assert got.pixel_format == fmt


def test_unusable_entries_are_skipped_like_libgpod():
    specs = [
        "a string, as the iPhone has them",
        _spec(PixelFormat="4A504547"),  # JPEG: unknown
        _spec(PixelFormat=None),
        _spec(PixelFormat="4C353535", PixelOrder="x"),  # recursive RGB555
        _spec(RenderWidth=0),
        _spec(FormatId=2001),
    ]
    assert [f.format_id for f in formats_from_sysinfo_extended({"X": specs}, "X")] == [2001]
    assert formats_from_sysinfo_extended({"X": specs[:5]}, "X") is None
    assert formats_from_sysinfo_extended({}, "X") is None
    assert formats_from_sysinfo_extended({"X": "nonsense"}, "X") is None


def test_back_color_and_row_alignment():
    (f,) = formats_from_sysinfo_extended({"X": [_spec(BackColor="FF800010")]}, "X")
    assert f.back_color == (255, 128, 0) and f.row_bytes_alignment == 0 and not f.crop
    (f,) = formats_from_sysinfo_extended({"X": [_spec(BackColor="zz", AlignRowBytes=True)]}, "X")
    assert f.back_color == (0, 0, 0) and f.row_bytes_alignment == 4
    spec = _spec(AlignRowBytes=True, RowBytesAlignment=16)
    (f,) = formats_from_sysinfo_extended({"X": [spec]}, "X")
    assert f.row_bytes_alignment == 16


def test_aligned_width_and_slot_size():
    f = ArtworkFormatInfo(1061, 55, 55, L565, row_bytes_alignment=4)
    assert aligned_width(f) == 56 and image_data_size(f) == 56 * 55 * 2
    assert aligned_width(f._replace(row_bytes_alignment=0)) == 55
    assert aligned_width(ArtworkFormatInfo(1, 55, 54, I420, row_bytes_alignment=4)) == 55
    assert aligned_width(f._replace(width=64)) == 64


# -------------------------------------------------------------------------
# Device / PhotoDB pick the specifications
# -------------------------------------------------------------------------
def test_device_photo_formats_prefer_sysinfo_extended(tmp_path):
    with_ext = Device.from_mountpoint(_volume(str(tmp_path / "a")))
    assert with_ext.get_photo_formats() == formats_from_sysinfo_extended(_ext(), SPEC_KEY_PHOTOS)


def test_without_image_specifications_the_table_is_used(tmp_path):
    mp = _volume(str(tmp_path / "b"), ext=False)
    (pathlib.Path(mp) / "iPod_Control/Device/SysInfo").write_text(
        f"ModelNumStr: xC293\nFirewireGuid: 0x{GUID}\n"
    )
    table = get_photo_formats(IpodGeneration.CLASSIC_3)
    assert Device.from_mountpoint(mp).get_photo_formats() == table
    # a SysInfoExtended without ImageSpecifications (hand-made) keeps the table too
    (pathlib.Path(mp) / "iPod_Control/Device/SysInfoExtended").write_bytes(
        b'<?xml version="1.0"?><plist version="1.0"><dict><key>DBVersion</key>'
        b"<integer>3</integer></dict></plist>"
    )
    assert Device.from_mountpoint(mp).get_photo_formats() == table


def _on_disk(mp):
    """[(MHNI format ids per photo)], [MHIF format ids], {format id: MHNI sizes}."""
    root = parse_artworkdb((pathlib.Path(mp) / "Photos" / "Photo Database").read_bytes())
    photos, files = [], []
    for mhsd in root.children:
        kind = mhsd.fields.get("mhsd_type")
        if kind == 1:
            for mhii in mhsd.children[0].children:
                ids = []
                for child in mhii.children:
                    for rec in [child] + list(child.children or []):
                        if rec.magic == b"mhni":
                            ids.append(rec.fields["format_id"])
                photos.append(ids)
        elif kind == 3:
            files = [m.fields["format_id"] for m in mhsd.children[0].children]
    return photos, files


def test_photodb_writes_the_device_formats_in_libgpod_order(tmp_path, images):
    mp = _volume(str(tmp_path / "v"))
    db = PhotoDB(mp)
    photo = db.add_photo(images[0])
    db.add_photo(images[1])
    db.save()
    sizes = {t.format_id: (t.image_size, t.width, t.height) for t in photo.thumbnails}
    # 1200x800 in 640x480: 640x427 (+26 padding on top); 1066 cropped to fill 64x64
    assert sizes == {1067: (614400, 640, 453), 1024: (153600, 320, 227), 1066: (8192, 64, 64)}
    assert _on_disk(mp) == ([[1066, 1024, 1067]] * 2, [1066, 1024, 1067])
    thumbs = pathlib.Path(mp) / "Photos" / "Thumbs"
    assert (thumbs / "F1067_1.ithmb").stat().st_size == 2 * 640 * 480 * 2

    before = (pathlib.Path(mp) / "Photos" / "Photo Database").read_bytes()
    PhotoDB(mp).save()  # an unchanged re-save keeps the order
    assert (pathlib.Path(mp) / "Photos" / "Photo Database").read_bytes() == before


def test_cropped_format_fills_the_slot(tmp_path, images):
    mp = _volume(str(tmp_path / "v"))
    photo = PhotoDB(mp).add_photo(images[3])  # 5:1
    t = {t.format_id: t for t in photo.thumbnails}
    assert (t[1066].horizontal_padding, t[1066].vertical_padding) == (0, 0)
    assert (t[1024].width, t[1024].height, t[1024].vertical_padding) == (320, 152, 88)


def test_explicit_generation_keeps_the_static_table(tmp_path, images):
    mp = _volume(str(tmp_path / "v"))
    photo = PhotoDB(mp, generation=IpodGeneration.CLASSIC_3).add_photo(images[0])
    assert {t.format_id: t.image_size for t in photo.thumbnails}[1067] == 720 * 480 * 2


# -------------------------------------------------------------------------
# rendering: crop, back colour, row alignment
# -------------------------------------------------------------------------
def test_render_back_color_and_row_width(tmp_path):
    from pygpod.model.artwork import PixelFormat, _unpack_rgb565, render_fitted_thumbnail

    path = str(tmp_path / "червоний.png")
    PIL.new("RGB", (40, 20), (255, 0, 0)).save(path)
    fitted = render_fitted_thumbnail(
        path, 10, 10, PixelFormat.RGB565_LE, back_color=WHITE, row_width=12
    )
    assert (fitted.width, fitted.height, fitted.vertical_padding) == (10, 7, 2)
    assert len(fitted.data) == 12 * 10 * 2
    rgb = _unpack_rgb565(fitted.data, 12, 10)

    def px(x, y):
        i = (y * 12 + x) * 3
        return tuple(rgb[i : i + 3])

    assert px(0, 0) == px(11, 5) == px(10, 9) == (248, 252, 248)  # white bars and row padding
    assert px(5, 5) == (248, 0, 0)  # red in RGB565

    cropped = render_fitted_thumbnail(path, 10, 10, PixelFormat.RGB565_LE, crop=True)
    assert (cropped.width, cropped.height, cropped.horizontal_padding) == (10, 10, 0)
    assert set(_unpack_rgb565(cropped.data, 10, 10)[0::3]) == {248}  # all red, no bars


# -------------------------------------------------------------------------
# libgpod is the judge
# -------------------------------------------------------------------------
def _dump(tool, mp):
    run = subprocess.run([tool, "dump-photos-full", mp], capture_output=True, text=True, check=True)
    assert "CRITICAL" not in run.stderr, run.stderr
    lines = [line.split("\t") for line in run.stdout.splitlines()]
    photos = [f[2:7] for f in lines if f[0] == "I"]  # thumb type, items, decoded, dbid, size
    thumbs = [f[2:] for f in lines if f[0] == "F"]
    albums = [f[1:] for f in lines if f[0] == "A"]
    return photos, thumbs, albums


@pytest.fixture(scope="module")
def both(tool, images, tmp_path_factory):
    base = tmp_path_factory.mktemp("classic7g_ext")
    py = _volume(str(base / "py"))
    db = PhotoDB(py)
    photos = [db.add_photo(p) for p in images]
    album = db.create_album("Карпати")
    for p in photos:
        db.add_photo_to_album(album, p)
    db.save()

    lg = _volume(str(base / "lg"))
    spec = base / "photos.tsv"
    lines = [f"photo\t{p}" for p in images] + ["album\tКарпати\t0,1,2,3"]
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([tool, "build-photos", lg, str(spec)], check=True)
    return py, lg


@pytest.mark.libgpod
def test_libgpod_sees_the_same_photos_as_its_own(tool, both):
    py, lg = both
    ours, ref = _dump(tool, py), _dump(tool, lg)
    assert ours == ref
    photos, thumbs, _ = ours
    assert all(p[1] == p[2] == "3" for p in photos)  # every thumbnail decoded
    assert {(t[0], t[3]) for t in thumbs} == {
        ("1067", "614400"),
        ("1024", "153600"),
        ("1066", "8192"),
    }


@pytest.mark.libgpod
def test_on_disk_order_and_ithmb_sizes_match_libgpod(both):
    py, lg = both
    assert _on_disk(py) == _on_disk(lg)

    def sizes(mp):
        d = pathlib.Path(mp) / "Photos" / "Thumbs"
        return {f.name: f.stat().st_size for f in d.iterdir()}

    assert sizes(py) == sizes(lg)


# -------------------------------------------------------------------------
# a Photo Database with thumbnails of the static table (720x480) gets new photos
# -------------------------------------------------------------------------
def _mhif_sizes(mp):
    root = parse_artworkdb((pathlib.Path(mp) / "Photos" / "Photo Database").read_bytes())
    for mhsd in root.children:
        if mhsd.fields.get("mhsd_type") == 3:
            return {
                m.fields["format_id"]: m.fields["image_size"] for m in mhsd.children[0].children
            }
    return {}


@pytest.fixture
def mixed(tmp_path, images):
    """A photo written with the static table, then one with the device's formats."""
    mp = _volume(str(tmp_path / "v"))
    db = PhotoDB(mp, generation=IpodGeneration.CLASSIC_3)
    old = db.add_photo(images[0])
    db.save()
    db = PhotoDB(mp)
    new = db.add_photo(images[1])
    db.save()
    return mp, old.photo_id, new.photo_id


def test_mixed_sizes_mhif_has_the_device_format_size(mixed):
    mp, _, _ = mixed
    assert _mhif_sizes(mp) == {1067: 640 * 480 * 2, 1024: 320 * 240 * 2, 1066: 64 * 64 * 2}


def test_mixed_sizes_round_trip_byte_identical(mixed):
    mp, _, _ = mixed
    path = pathlib.Path(mp) / "Photos" / "Photo Database"
    before = path.read_bytes()
    PhotoDB(mp).save()
    assert _mhif_sizes(mp)[1067] == 640 * 480 * 2
    assert path.read_bytes() == before


def test_mixed_sizes_mhif_kept_by_saves_without_new_photos(mixed):
    mp, old_id, _ = mixed
    db = PhotoDB(mp)
    db.create_album("Обійми")
    db.save()
    assert _mhif_sizes(mp)[1067] == 640 * 480 * 2
    db = PhotoDB(mp)
    db.remove_photo(db.get_photo(old_id))
    db.save()
    assert _mhif_sizes(mp)[1067] == 640 * 480 * 2


def test_doctor_reports_thumbnails_of_another_size(mixed):
    from pygpod import Database

    mp, old_id, _ = mixed
    report = Database(mp, recover=False).check()
    (issue,) = report.by_kind("photo_thumbnail_size")
    assert issue.image_id == old_id
    assert (issue.severity, issue.fixable) == ("warning", False)
    assert "1067: 691200 bytes" in issue.message and "640x480" in issue.message
    assert "1024" not in issue.message  # 320x240 in both tables


def test_doctor_quiet_for_photos_in_the_device_formats(tmp_path, images):
    from pygpod import Database

    mp = _volume(str(tmp_path / "v"))
    db = PhotoDB(mp)
    for path in images:
        db.add_photo(path)
    db.save()
    report = Database(mp, recover=False).check()
    assert not report.by_kind("photo_thumbnail_size")
    assert not report.by_kind("photo_db_unreadable")


def test_doctor_reports_an_unreadable_photo_database(tmp_path):
    from pygpod import Database

    mp = _volume(str(tmp_path / "v"))
    photos = pathlib.Path(mp) / "Photos"
    photos.mkdir(exist_ok=True)
    (photos / "Photo Database").write_bytes(b"mhfd" + b"\xff" * 12)
    report = Database(mp, recover=False).check()
    assert report.by_kind("photo_db_unreadable")


@pytest.mark.libgpod
def test_libgpod_decodes_the_new_photo_of_a_mixed_database(tool, mixed):
    mp, old_id, new_id = mixed
    run = subprocess.run([tool, "dump-photos-full", mp], capture_output=True, text=True, check=True)
    # I <id> <thumb type> <n thumbnail items> <n decoded> ...
    lines = [line.split("\t") for line in run.stdout.splitlines()]
    photos = {int(f[1]): f[3:5] for f in lines if f[0] == "I"}
    assert photos[new_id] == ["3", "3"]  # every thumbnail of the new photo decoded
    assert photos[old_id] == ["3", "2"]  # the 720x480 I420 thumbnail cannot be unpacked
