"""Full tests for artwork_parser.py - synthetic ArtworkDB fixtures."""

import struct

import pytest

from pygpod.db.artwork_parser import (
    MHBA_MAGIC,
    MHFD_MAGIC,
    MHIF_MAGIC,
    MHII_MAGIC,
    MHLA_MAGIC,
    MHLI_MAGIC,
    MHNI_MAGIC,
    MHOD_MAGIC,
    MHSD_MAGIC,
    parse_artworkdb,
)
from pygpod.exceptions import ParseError
from pygpod.utils.compat import put16lint, put32lint, put64lint


def _u32(val):
    return struct.pack("<I", val)


def _u16(val):
    return struct.pack("<H", val)


def _u64(val):
    return struct.pack("<Q", val)


# =========================================================================
# Helper: build synthetic ArtworkDB binary
# =========================================================================


def _make_mhod_filename(filename=":F1060_1.ithmb"):
    """Build MHOD type 3 (filename) for artwork."""
    name_bytes = filename.encode("utf-16-le")
    hdr_len = 24
    body = bytearray(12 + len(name_bytes))
    put32lint(body, 0, len(name_bytes))  # string length
    put32lint(body, 4, 2)  # encoding = UTF-16LE
    body[12 : 12 + len(name_bytes)] = name_bytes
    total = hdr_len + len(body)

    header = bytearray(hdr_len)
    header[0:4] = MHOD_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put32lint(header, 0x0C, 3)  # mhod_type = 3
    return bytes(header) + bytes(body)


def _make_mhni(format_id=1060, offset=0, size=204800, width=320, height=320):
    """Build MHNI record."""
    hdr_len = 76
    filename_mhod = _make_mhod_filename(f":F{format_id}_1.ithmb")
    total = hdr_len + len(filename_mhod)

    header = bytearray(hdr_len)
    header[0:4] = MHNI_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put32lint(header, 0x0C, 1)  # num_children = 1
    put32lint(header, 0x10, format_id)
    put32lint(header, 0x14, offset)
    put32lint(header, 0x18, size)
    put16lint(header, 0x20, height)
    put16lint(header, 0x22, width)
    return bytes(header) + filename_mhod


def _make_mhod_wrapper(mhni_data):
    """Build MHOD type 2 wrapping an MHNI."""
    hdr_len = 24
    total = hdr_len + len(mhni_data)
    header = bytearray(hdr_len)
    header[0:4] = MHOD_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put16lint(header, 0x0C, 2)  # mhod_type = 2
    return bytes(header) + mhni_data


def _make_mhii(image_id=1, song_id=100, mhni_list=None):
    """Build MHII record with MHOD-wrapped MHNIs."""
    if mhni_list is None:
        mhni_list = [_make_mhni()]
    wrapped = b"".join(_make_mhod_wrapper(m) for m in mhni_list)
    hdr_len = 152
    total = hdr_len + len(wrapped)

    header = bytearray(hdr_len)
    header[0:4] = MHII_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put32lint(header, 0x0C, len(mhni_list))
    put32lint(header, 0x10, image_id)
    put64lint(header, 0x14, song_id)
    return bytes(header) + wrapped


def _make_mhli(mhii_list):
    """Build MHLI record."""
    hdr_len = 92
    body = b"".join(mhii_list)
    header = bytearray(hdr_len)
    header[0:4] = MHLI_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, len(mhii_list))
    return bytes(header) + body


def _make_mhif(format_id=1060, image_size=204800):
    """Build MHIF record."""
    hdr_len = 124
    header = bytearray(hdr_len)
    header[0:4] = MHIF_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, hdr_len)
    put32lint(header, 0x10, format_id)
    put32lint(header, 0x14, image_size)
    return bytes(header)


def _make_mhlf(mhif_list):
    """Build MHLF record."""
    hdr_len = 92
    body = b"".join(mhif_list)
    header = bytearray(hdr_len)
    header[0:4] = b"mhlf"
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, len(mhif_list))
    return bytes(header) + body


def _make_mhla_empty():
    """Build empty MHLA."""
    hdr_len = 92
    header = bytearray(hdr_len)
    header[0:4] = MHLA_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, 0)  # 0 albums
    return bytes(header)


def _make_mhba_mhod(name="Album Name"):
    """Build MHOD inside MHBA (album name, UTF-8)."""
    encoded = name.encode("utf-8")
    hdr_len = 24
    body = bytearray(12 + len(encoded))
    put32lint(body, 0, len(encoded))
    body[12 : 12 + len(encoded)] = encoded
    total = hdr_len + len(body)
    header = bytearray(hdr_len)
    header[0:4] = MHOD_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    header[0x0C] = 1  # mhod_type = 1
    return bytes(header) + bytes(body)


def _make_mhia(image_id=1):
    """Build MHIA (photo album item ref)."""
    hdr_len = 40
    header = bytearray(hdr_len)
    header[0:4] = b"mhia"
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, hdr_len)
    put32lint(header, 0x10, image_id)
    return bytes(header)


def _make_mhba(album_id=1, name="Test Album", image_ids=None):
    """Build MHBA (photo album)."""
    if image_ids is None:
        image_ids = [1, 2]
    name_mhod = _make_mhba_mhod(name)
    mhias = b"".join(_make_mhia(iid) for iid in image_ids)
    hdr_len = 94
    body = name_mhod + mhias
    total = hdr_len + len(body)

    header = bytearray(hdr_len)
    header[0:4] = MHBA_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put32lint(header, 0x0C, 1)  # num_mhods
    put32lint(header, 0x10, len(image_ids))  # num_mhias
    put32lint(header, 0x14, album_id)
    header[0x1E] = 1  # album_type
    return bytes(header) + body


def _make_mhla(mhba_list):
    """Build MHLA with MHBA children."""
    hdr_len = 92
    body = b"".join(mhba_list)
    header = bytearray(hdr_len)
    header[0:4] = MHLA_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, len(mhba_list))
    return bytes(header) + body


def _make_mhsd(mhsd_type, child_data):
    """Build MHSD."""
    hdr_len = 96
    total = hdr_len + len(child_data)
    header = bytearray(hdr_len)
    header[0:4] = MHSD_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put32lint(header, 0x0C, mhsd_type)
    return bytes(header) + child_data


def _make_artworkdb(mhsd_list):
    """Build full ArtworkDB."""
    hdr_len = 132
    body = b"".join(mhsd_list)
    total = hdr_len + len(body)
    header = bytearray(hdr_len)
    header[0:4] = MHFD_MAGIC
    put32lint(header, 4, hdr_len)
    put32lint(header, 8, total)
    put32lint(header, 0x10, 2)  # unknown2
    put32lint(header, 0x14, len(mhsd_list))
    return bytes(header) + body


# =========================================================================
# Tests
# =========================================================================


class TestParseArtworkDB:
    def test_parse_basic(self):
        mhii = _make_mhii(image_id=1, song_id=100)
        mhli = _make_mhli([mhii])
        mhsd1 = _make_mhsd(1, mhli)
        mhsd2 = _make_mhsd(2, _make_mhla_empty())
        mhsd3 = _make_mhsd(3, _make_mhlf([_make_mhif(1060, 204800)]))
        data = _make_artworkdb([mhsd1, mhsd2, mhsd3])

        root = parse_artworkdb(data)
        assert root.magic == MHFD_MAGIC
        assert len(root.children) == 3

    def test_mhsd_types(self):
        mhsd1 = _make_mhsd(1, _make_mhli([]))
        mhsd2 = _make_mhsd(2, _make_mhla_empty())
        mhsd3 = _make_mhsd(3, _make_mhlf([]))
        root = parse_artworkdb(_make_artworkdb([mhsd1, mhsd2, mhsd3]))

        types = [c.fields["mhsd_type"] for c in root.children]
        assert types == [1, 2, 3]

    def test_mhii_fields(self):
        mhii = _make_mhii(image_id=42, song_id=9999)
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, _make_mhli([mhii]))]))

        mhli = root.children[0].children[0]
        assert len(mhli.children) == 1
        img = mhli.children[0]
        assert img.fields["image_id"] == 42
        assert img.fields["song_id"] == 9999

    def test_mhni_fields(self):
        mhni = _make_mhni(format_id=1060, offset=204800, size=204800, width=320, height=320)
        _make_mhod_wrapper(mhni)
        mhii_data = _make_mhii(image_id=1, song_id=1, mhni_list=[_make_mhni()])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, _make_mhli([mhii_data]))]))

        mhii = root.children[0].children[0].children[0]
        # MHII has MHOD wrapper children
        mhod = mhii.children[0]
        assert mhod.fields["mhod_type"] == 2
        # MHOD wraps MHNI
        mhni_rec = mhod.children[0]
        assert mhni_rec.fields["format_id"] == 1060
        assert mhni_rec.fields["image_width"] == 320
        assert mhni_rec.fields["image_height"] == 320

    def test_multi_format(self):
        """MHII with multiple thumbnail formats."""
        mhni_list = [
            _make_mhni(format_id=1061, width=56, height=56, size=6272),
            _make_mhni(format_id=1055, width=128, height=128, size=32768),
            _make_mhni(format_id=1060, width=320, height=320, size=204800),
        ]
        mhii = _make_mhii(image_id=1, song_id=100, mhni_list=mhni_list)
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, _make_mhli([mhii]))]))

        img = root.children[0].children[0].children[0]
        assert img.fields["num_children"] == 3
        # Each MHOD wrapper contains an MHNI
        formats = []
        for mhod in img.children:
            if mhod.children:
                formats.append(mhod.children[0].fields["format_id"])
        assert formats == [1061, 1055, 1060]

    def test_multiple_images(self):
        mhii1 = _make_mhii(image_id=1, song_id=100)
        mhii2 = _make_mhii(image_id=2, song_id=200)
        mhii3 = _make_mhii(image_id=3, song_id=300)
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, _make_mhli([mhii1, mhii2, mhii3]))]))

        mhli = root.children[0].children[0]
        assert len(mhli.children) == 3
        ids = [c.fields["image_id"] for c in mhli.children]
        assert ids == [1, 2, 3]

    def test_mhlf_formats(self):
        mhif1 = _make_mhif(format_id=1060, image_size=204800)
        mhif2 = _make_mhif(format_id=1055, image_size=32768)
        mhlf = _make_mhlf([mhif1, mhif2])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(3, mhlf)]))

        mhlf_rec = root.children[0].children[0]
        assert len(mhlf_rec.children) == 2
        assert mhlf_rec.children[0].fields["format_id"] == 1060
        assert mhlf_rec.children[1].fields["format_id"] == 1055


class TestParseMHLA:
    def test_empty_mhla(self):
        mhla = _make_mhla_empty()
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(2, mhla)]))
        mhla_rec = root.children[0].children[0]
        assert mhla_rec.magic == MHLA_MAGIC
        assert len(mhla_rec.children) == 0

    def test_mhla_with_albums(self):
        mhba1 = _make_mhba(album_id=1, name="Album One", image_ids=[1, 2])
        mhba2 = _make_mhba(album_id=2, name="Album Two", image_ids=[3])
        mhla = _make_mhla([mhba1, mhba2])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(2, mhla)]))

        mhla_rec = root.children[0].children[0]
        assert len(mhla_rec.children) == 2

    def test_mhba_fields(self):
        mhba = _make_mhba(album_id=42, name="My Album", image_ids=[10, 20])
        mhla = _make_mhla([mhba])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(2, mhla)]))

        album = root.children[0].children[0].children[0]
        assert album.magic == MHBA_MAGIC
        assert album.fields["album_id"] == 42
        assert album.fields["num_mhods"] == 1
        assert album.fields["num_mhias"] == 2
        assert album.fields["album_type"] == 1

    def test_mhba_name_mhod(self):
        mhba = _make_mhba(album_id=1, name="Фотоальбом")
        mhla = _make_mhla([mhba])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(2, mhla)]))

        album = root.children[0].children[0].children[0]
        # First child should be the name MHOD
        name_mhod = album.children[0]
        assert name_mhod.magic == MHOD_MAGIC
        assert name_mhod.fields.get("string") == "Фотоальбом"

    def test_mhia_fields(self):
        mhba = _make_mhba(album_id=1, name="Test", image_ids=[99])
        mhla = _make_mhla([mhba])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(2, mhla)]))

        album = root.children[0].children[0].children[0]
        # MHOD (name) + MHIA (photo ref)
        mhia = [c for c in album.children if c.magic == b"mhia"]
        assert len(mhia) == 1
        assert mhia[0].fields["image_id"] == 99


class TestParseEdgeCases:
    def test_too_short(self):
        with pytest.raises(ParseError):
            parse_artworkdb(b"\x00" * 5)

    def test_bad_magic(self):
        with pytest.raises(ParseError, match="Invalid"):
            parse_artworkdb(b"XXXX" + b"\x00" * 128)

    def test_empty_mhli(self):
        mhli = _make_mhli([])
        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, mhli)]))
        assert len(root.children[0].children[0].children) == 0

    def test_bare_mhni_in_mhii(self):
        """MHII with bare MHNI (no MHOD wrapper)."""
        mhni = _make_mhni()
        hdr_len = 152
        total = hdr_len + len(mhni)
        header = bytearray(hdr_len)
        header[0:4] = MHII_MAGIC
        put32lint(header, 4, hdr_len)
        put32lint(header, 8, total)
        put32lint(header, 0x0C, 1)
        put32lint(header, 0x10, 1)
        put64lint(header, 0x14, 100)
        mhii_data = bytes(header) + mhni

        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, _make_mhli([mhii_data]))]))
        img = root.children[0].children[0].children[0]
        # Bare MHNI parsed directly
        assert len(img.children) == 1
        assert img.children[0].magic == MHNI_MAGIC

    def test_mhni_without_mhod_child(self):
        """MHNI with no filename MHOD child."""
        hdr_len = 76
        header = bytearray(hdr_len)
        header[0:4] = MHNI_MAGIC
        put32lint(header, 4, hdr_len)
        put32lint(header, 8, hdr_len)  # total = header only
        put32lint(header, 0x0C, 0)  # 0 children
        put32lint(header, 0x10, 1060)
        mhni_data = bytes(header)

        _make_mhii(image_id=1, song_id=1, mhni_list=[])
        # Manually replace MHII body with bare MHNI
        mhii_hdr = bytearray(152)
        mhii_hdr[0:4] = MHII_MAGIC
        put32lint(mhii_hdr, 4, 152)
        put32lint(mhii_hdr, 8, 152 + len(mhni_data))
        put32lint(mhii_hdr, 0x0C, 1)
        put32lint(mhii_hdr, 0x10, 1)
        full_mhii = bytes(mhii_hdr) + mhni_data

        root = parse_artworkdb(_make_artworkdb([_make_mhsd(1, _make_mhli([full_mhii]))]))
        mhni_rec = root.children[0].children[0].children[0].children[0]
        assert mhni_rec.fields["format_id"] == 1060
        assert len(mhni_rec.children) == 0  # no filename MHOD

    def test_non_mhsd_child_stops_parsing(self):
        """Garbage after valid MHSD stops parsing."""
        mhsd = _make_mhsd(1, _make_mhli([]))
        data = _make_artworkdb([mhsd])
        # Append garbage
        data += b"XXXX" + b"\x00" * 100
        # Fix total_len to include garbage
        data = bytearray(data)
        put32lint(data, 8, len(data))
        root = parse_artworkdb(bytes(data))
        assert len(root.children) == 1  # stopped at garbage
