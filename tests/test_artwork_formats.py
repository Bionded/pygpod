"""Tests for artwork format tables and ArtworkManager."""
import pytest


class TestArtworkFormats:
    """Test per-generation format tables."""

    def test_classic_formats(self):
        from pygpod.device.artwork_formats import _CLASSIC_COVER_ART

        assert len(_CLASSIC_COVER_ART) == 4
        ids = [f.format_id for f in _CLASSIC_COVER_ART]
        assert 1061 in ids
        assert 1055 in ids
        assert 1068 in ids
        assert 1060 in ids

    def test_classic_format_sizes(self):
        from pygpod.device.artwork_formats import _CLASSIC_COVER_ART, ThumbFormat

        for fmt in _CLASSIC_COVER_ART:
            assert fmt.pixel_format == ThumbFormat.RGB565_LE

    def test_nano_formats(self):
        from pygpod.device.artwork_formats import _NANO_COVER_ART

        assert len(_NANO_COVER_ART) == 2
        ids = [f.format_id for f in _NANO_COVER_ART]
        assert 1031 in ids
        assert 1027 in ids

    def test_photo_formats(self):
        from pygpod.device.artwork_formats import _PHOTO_COVER_ART

        assert len(_PHOTO_COVER_ART) == 2
        assert _PHOTO_COVER_ART[0].format_id == 1017
        assert _PHOTO_COVER_ART[1].format_id == 1016

    def test_video_formats(self):
        from pygpod.device.artwork_formats import _VIDEO_COVER_ART

        ids = [f.format_id for f in _VIDEO_COVER_ART]
        assert 1028 in ids
        assert 1029 in ids

    def test_nano4g_formats(self):
        from pygpod.device.artwork_formats import _NANO4G_COVER_ART

        assert len(_NANO4G_COVER_ART) == 6

    def test_nano5g_formats(self):
        from pygpod.device.artwork_formats import _NANO5G_COVER_ART

        assert len(_NANO5G_COVER_ART) == 4

    def test_get_cover_art_formats_classic(self):
        from pygpod.device.artwork_formats import get_cover_art_formats
        from pygpod.device.models import IpodGeneration

        fmts = get_cover_art_formats(IpodGeneration.CLASSIC_1)
        assert fmts is not None
        assert len(fmts) == 4

    def test_get_cover_art_formats_all_generations(self):
        from pygpod.device.artwork_formats import get_cover_art_formats
        from pygpod.device.models import IpodGeneration

        supported = [
            IpodGeneration.PHOTO,
            IpodGeneration.VIDEO_1,
            IpodGeneration.VIDEO_2,
            IpodGeneration.NANO_1,
            IpodGeneration.NANO_2,
            IpodGeneration.NANO_3,
            IpodGeneration.NANO_4,
            IpodGeneration.NANO_5,
            IpodGeneration.CLASSIC_1,
            IpodGeneration.CLASSIC_2,
            IpodGeneration.CLASSIC_3,
        ]
        for gen in supported:
            fmts = get_cover_art_formats(gen)
            assert fmts is not None, f"No formats for {gen}"
            assert len(fmts) > 0

    def test_get_cover_art_formats_unknown(self):
        from pygpod.device.artwork_formats import get_cover_art_formats
        from pygpod.device.models import IpodGeneration

        fmts = get_cover_art_formats(IpodGeneration.UNKNOWN)
        assert fmts is None

    def test_bytes_per_pixel(self):
        from pygpod.device.artwork_formats import ThumbFormat, bytes_per_pixel

        assert bytes_per_pixel(ThumbFormat.RGB565_LE) == 2.0
        assert bytes_per_pixel(ThumbFormat.RGB565_BE) == 2.0
        assert bytes_per_pixel(ThumbFormat.RGB555_LE) == 2.0
        assert bytes_per_pixel(ThumbFormat.UYVY_LE) == 2.0
        assert bytes_per_pixel(ThumbFormat.I420_LE) == 1.5

    def test_image_data_size(self):
        from pygpod.device.artwork_formats import ArtworkFormatInfo, ThumbFormat, image_data_size

        fmt = ArtworkFormatInfo(1061, 56, 56, ThumbFormat.RGB565_LE)
        assert image_data_size(fmt) == 56 * 56 * 2

    def test_image_data_size_with_padding(self):
        from pygpod.device.artwork_formats import ArtworkFormatInfo, ThumbFormat, image_data_size

        fmt = ArtworkFormatInfo(1061, 56, 56, ThumbFormat.RGB565_LE, padding=8192)
        assert image_data_size(fmt) == 8192

    def test_nano3g_shares_classic_formats(self):
        from pygpod.device.artwork_formats import get_cover_art_formats
        from pygpod.device.models import IpodGeneration

        classic = get_cover_art_formats(IpodGeneration.CLASSIC_1)
        nano3 = get_cover_art_formats(IpodGeneration.NANO_3)
        assert classic == nano3


class TestArtworkManager:
    """Test ArtworkManager initialization and structure."""

    def test_init_no_artworkdb(self, writable_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        assert mgr.formats is not None
        assert len(mgr.formats) > 0

    def test_formats_fallback_unknown(self, writable_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.UNKNOWN)
        # Falls back to classic formats
        assert len(mgr.formats) > 0

    def test_reset(self, writable_ipod):
        import os

        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        art_dir = os.path.join(writable_ipod, "iPod_Control", "Artwork")
        os.makedirs(art_dir, exist_ok=True)
        # Create a dummy file
        with open(os.path.join(art_dir, "ArtworkDB"), "wb") as f:
            f.write(b"\x00" * 100)
        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        mgr.reset()
        assert not os.path.exists(os.path.join(art_dir, "ArtworkDB"))

    def test_create_empty_artworkdb(self, writable_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        root = mgr._create_empty_artworkdb()
        assert root.magic == b"mhfd"
        assert len(root.children) == 3  # MHSD type 1, 2, 3
        # Check MHSD types
        types = [c.fields.get("mhsd_type") for c in root.children]
        assert 1 in types
        assert 2 in types
        assert 3 in types

    def test_mhfd_header_fields(self, writable_ipod):
        import struct

        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        root = mgr._create_empty_artworkdb()
        hdr = root.raw_header
        # unknown2 = 2 at offset 0x10
        assert struct.unpack_from("<I", hdr, 0x10)[0] == 2
        # unknown_flag1 = 2 at offset 0x30
        assert hdr[0x30] == 2

    def _make_png(self, path, w=64, h=64):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        Image.new("RGB", (w, h), color=(0, 128, 255)).save(path, "PNG")

    def test_remove_artwork_missing_id_returns_false(self, writable_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        assert mgr.remove_artwork(999) is False

    def test_remove_artwork_clears_mhii(self, tmp_path, writable_ipod):
        import os

        from pygpod.db.artwork_parser import parse_artworkdb
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        png = str(tmp_path / "cover.png")
        self._make_png(png)
        with open(png, "rb") as f:
            art_data = f.read()

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        image_id = mgr.add_artwork_data(dbid=42, art_data=art_data, save=False)
        assert image_id is not None

        removed = mgr.remove_artwork(image_id)
        assert removed is True

        mgr.save()

        artdb_path = os.path.join(writable_ipod, "iPod_Control", "Artwork", "ArtworkDB")
        with open(artdb_path, "rb") as f:
            root = parse_artworkdb(f.read())

        mhli = [c for c in root.children if c.fields.get("mhsd_type") == 1][0].children[0]
        ids = [c.fields.get("image_id") for c in mhli.children]
        assert image_id not in ids

    def test_remove_artwork_compacts_ithmb(self, tmp_path, writable_ipod):
        import os

        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        png = str(tmp_path / "cover.png")
        self._make_png(png)
        with open(png, "rb") as f:
            art_data = f.read()

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        # Start from a clean slate so fixture artwork doesn't skew sizes
        mgr.reset()
        id1 = mgr.add_artwork_data(dbid=1, art_data=art_data, save=False)
        id2 = mgr.add_artwork_data(dbid=2, art_data=art_data, save=False)
        assert id1 is not None and id2 is not None

        art_dir = os.path.join(writable_ipod, "iPod_Control", "Artwork")
        # Record sizes with both entries present
        sizes_two = {
            f: os.path.getsize(os.path.join(art_dir, f))
            for f in os.listdir(art_dir) if f.endswith(".ithmb")
        }

        mgr.remove_artwork(id1)
        mgr.save()

        # Each .ithmb must be exactly half its two-entry size (both entries are same source)
        for fname, size_two in sizes_two.items():
            fpath = os.path.join(art_dir, fname)
            assert os.path.isfile(fpath), f"{fname} was deleted but still referenced"
            assert os.path.getsize(fpath) == size_two // 2

    def test_remove_artwork_sets_needs_rebuild(self, tmp_path, writable_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        png = str(tmp_path / "cover.png")
        self._make_png(png)
        with open(png, "rb") as f:
            art_data = f.read()

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        image_id = mgr.add_artwork_data(dbid=10, art_data=art_data, save=False)
        assert not getattr(mgr, "_needs_rebuild", False)
        mgr.remove_artwork(image_id)
        assert mgr._needs_rebuild is True
        # Flag cleared after save
        mgr.save()
        assert mgr._needs_rebuild is False

    def test_remove_all_artwork_deletes_ithmb_files(self, tmp_path, writable_ipod):
        import os

        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        png = str(tmp_path / "cover.png")
        self._make_png(png)
        with open(png, "rb") as f:
            art_data = f.read()

        mgr = ArtworkManager(writable_ipod, generation=IpodGeneration.CLASSIC_1)
        # Start from clean slate so no fixture .ithmb files survive the removal
        mgr.reset()
        image_id = mgr.add_artwork_data(dbid=7, art_data=art_data, save=False)
        assert image_id is not None

        mgr.remove_artwork(image_id)
        mgr.save()

        art_dir = os.path.join(writable_ipod, "iPod_Control", "Artwork")
        ithmb_files = [f for f in os.listdir(art_dir) if f.endswith(".ithmb")]
        assert ithmb_files == [], f"Expected no .ithmb files, found: {ithmb_files}"
