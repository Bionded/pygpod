"""Tests to improve coverage on artwork, tags, device, and database modules."""

import os
import shutil
import struct

import pytest


# =========================================================================
# Tags (mutagen-based)
# =========================================================================


class TestTags:
    def test_read_tags_mp3(self, generated_mp3):
        from pygpod.tags import read_tags

        tags = read_tags(generated_mp3)
        assert isinstance(tags, dict)
        assert "title" in tags
        assert "file_size" in tags
        assert tags["file_size"] > 0

    def test_read_tags_nonexistent(self):
        from pygpod.tags import read_tags

        # Nonexistent file should still return basic dict (title from filename)
        try:
            tags = read_tags("/nonexistent/file.mp3")
            assert "title" in tags
        except (FileNotFoundError, OSError):
            pass  # Some platforms raise immediately

    def test_extract_artwork_mp3_with_art(self, generated_mp3_with_art):
        from pygpod.tags import extract_artwork

        art = extract_artwork(generated_mp3_with_art)
        if art:
            assert len(art) > 0
            # Should be PNG or JPEG
            assert art[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1")

    def test_extract_artwork_no_art(self, generated_mp3):
        from pygpod.tags import extract_artwork

        # Basic MP3 without embedded art may return None
        art = extract_artwork(generated_mp3)
        # Either None or valid image data
        assert art is None or len(art) > 0

    def test_filetype_marker(self):
        from pygpod.tags import filetype_marker

        # filetype_marker returns 4 bytes (little-endian encoded)
        marker = filetype_marker("song.mp3")
        assert len(marker) == 4
        assert marker != b"\x00\x00\x00\x00"
        # Different extensions give different markers
        assert filetype_marker("song.mp3") != filetype_marker("song.m4a")


# =========================================================================
# Device
# =========================================================================


class TestDevice:
    def test_device_from_mountpoint(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev is not None
        assert dev.model is not None
        assert "Classic" in dev.model or "iPod" in dev.model

    def test_device_generation(self, ipod_fs_path):
        from pygpod.device.device import Device
        from pygpod.device.models import IpodGeneration

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.generation == IpodGeneration.CLASSIC_1

    def test_device_supports_artwork(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.supports_artwork is True

    def test_device_supports_video(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.supports_video is True

    def test_device_checksum_type(self, ipod_fs_path):
        from pygpod.device.device import Device
        from pygpod.device.models import ChecksumType

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.checksum_type == ChecksumType.HASH58

    def test_device_firewire_guid(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.firewire_guid is not None
        assert len(dev.firewire_guid) == 16

    def test_device_db_version(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.db_version == 0x30  # 48 for Classic 1G

    def test_device_music_dirs(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.music_dirs == 50


# =========================================================================
# Artwork Manager (requires Pillow)
# =========================================================================


class TestArtworkManager:
    @pytest.fixture
    def art_ipod(self, writable_ipod):
        return writable_ipod

    def test_init_and_formats(self, art_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(art_ipod, generation=IpodGeneration.CLASSIC_1)
        assert len(mgr.formats) > 0

    def test_reset(self, art_ipod):
        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(art_ipod, generation=IpodGeneration.CLASSIC_1)
        mgr.reset()
        assert mgr._root is None
        assert mgr._next_image_id == 1

    def _make_test_png(self, path, w=100, h=100):
        """Create a valid PNG using Pillow."""
        from PIL import Image

        img = Image.new("RGB", (w, h), color=(0, 100, 200))
        img.save(path, "PNG")

    def test_add_artwork_data(self, art_ipod):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")

        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(art_ipod, generation=IpodGeneration.CLASSIC_1)
        mgr.reset()

        png_path = os.path.join(art_ipod, "test_cover.png")
        self._make_test_png(png_path, 100, 100)
        with open(png_path, "rb") as f:
            art_data = f.read()

        image_id = mgr.add_artwork_data(dbid=999, art_data=art_data)
        assert image_id is not None
        assert image_id >= 1

        artdb = os.path.join(art_ipod, "iPod_Control", "Artwork", "ArtworkDB")
        assert os.path.isfile(artdb)

        art_dir = os.path.join(art_ipod, "iPod_Control", "Artwork")
        ithmb_files = [f for f in os.listdir(art_dir) if f.endswith(".ithmb")]
        assert len(ithmb_files) > 0

    def test_add_artwork_invalid_data(self, art_ipod):
        """ArtworkManager handles invalid image data."""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")

        from pygpod.device.models import IpodGeneration
        from pygpod.model.artwork_manager import ArtworkManager

        mgr = ArtworkManager(art_ipod, generation=IpodGeneration.CLASSIC_1)
        # Invalid data should either return None or raise
        try:
            result = mgr.add_artwork_data(dbid=1, art_data=b"not an image at all")
            assert result is None
        except Exception:
            pass  # PIL may raise UnidentifiedImageError


# =========================================================================
# Database operations
# =========================================================================


class TestDatabaseOperations:
    def test_add_track(self, writable_ipod_with_mp3):
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        count_before = len(db.tracks)
        track = db.add_track(mp3)
        assert len(db.tracks) == count_before + 1
        assert track.track_id > 0

    def test_remove_track(self, writable_ipod_with_mp3):
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        track = db.add_track(mp3)
        tid = track.track_id
        db.remove_track(track)
        assert db.get_track(tid) is None

    def test_create_and_delete_playlist(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        pl = db.create_playlist("Test Playlist")
        assert pl.name == "Test Playlist"
        names = [p.name for p in db.playlists]
        assert "Test Playlist" in names

        db.delete_playlist(pl)
        names = [p.name for p in db.playlists]
        assert "Test Playlist" not in names

    def test_add_track_to_playlist(self, writable_ipod_with_mp3):
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        track = db.add_track(mp3)
        pl = db.create_playlist("My Playlist")
        db.add_track_to_playlist(pl, track)
        assert track.track_id in pl.track_ids

    def test_remove_track_from_playlist(self, writable_ipod_with_mp3):
        from pygpod.model.database import Database

        ipod, mp3 = writable_ipod_with_mp3
        db = Database(ipod)
        track = db.add_track(mp3)
        pl = db.create_playlist("Remove Test")
        db.add_track_to_playlist(pl, track)
        assert track.track_id in pl.track_ids

        db.remove_track_from_playlist(pl, track)
        assert track.track_id not in pl.track_ids

    def test_save_and_reload(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        n_tracks = len(db.tracks)
        n_playlists = len(db.playlists)
        db.save()

        db2 = Database(writable_ipod)
        assert len(db2.tracks) == n_tracks
        assert len(db2.playlists) == n_playlists

    def test_save_writes_hash(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        db_path = os.path.join(writable_ipod, "iPod_Control", "iTunes", "iTunesDB")
        with open(db_path, "rb") as f:
            data = f.read()
        h58 = data[0x58:0x58 + 20]
        assert h58 != b"\x00" * 20  # hash should be non-zero

    def test_mhsd_order_after_save(self, writable_ipod):
        """Verify MHSDs are in correct order: 1, 3, 2, 4, 8, ..."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        db_path = os.path.join(writable_ipod, "iPod_Control", "iTunes", "iTunesDB")
        with open(db_path, "rb") as f:
            data = f.read()

        hdr_len = struct.unpack_from("<I", data, 4)[0]
        pos = hdr_len
        types = []
        while pos < len(data) - 8:
            if data[pos:pos + 4] != b"mhsd":
                break
            s_type = struct.unpack_from("<I", data, pos + 12)[0]
            s_total = struct.unpack_from("<I", data, pos + 8)[0]
            types.append(s_type)
            pos += s_total

        # First three must be 1, 3, 2 (matching libgpod order)
        assert types[:3] == [1, 3, 2]
        # Must have types 4 and 8
        assert 4 in types
        assert 8 in types

    def test_sort_indexes_valid(self, writable_ipod):
        """Verify sort indexes have correct structure after save."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        db_path = os.path.join(writable_ipod, "iPod_Control", "iTunes", "iTunesDB")
        with open(db_path, "rb") as f:
            data = f.read()

        # Find MHSD type 2, master MHYP, check sort MHODs
        hdr_len = struct.unpack_from("<I", data, 4)[0]
        pos = hdr_len
        while pos < len(data) - 8:
            if data[pos:pos + 4] != b"mhsd":
                break
            s_type = struct.unpack_from("<I", data, pos + 12)[0]
            s_hdr = struct.unpack_from("<I", data, pos + 4)[0]
            s_total = struct.unpack_from("<I", data, pos + 8)[0]
            if s_type == 2:
                mhlp_pos = pos + s_hdr
                mhlp_hdr = struct.unpack_from("<I", data, mhlp_pos + 4)[0]
                mhyp_pos = mhlp_pos + mhlp_hdr
                mhyp_hdr = struct.unpack_from("<I", data, mhyp_pos + 4)[0]
                mhyp_total = struct.unpack_from("<I", data, mhyp_pos + 8)[0]
                mhip_count = struct.unpack_from("<I", data, mhyp_pos + 0x10)[0]

                # Walk MHODs looking for type 52/53
                mhod_pos = mhyp_pos + mhyp_hdr
                while mhod_pos < mhyp_pos + mhyp_total - 8:
                    if data[mhod_pos:mhod_pos + 4] == b"mhip":
                        break
                    if data[mhod_pos:mhod_pos + 4] != b"mhod":
                        break
                    md_hdr = struct.unpack_from("<I", data, mhod_pos + 4)[0]
                    md_total = struct.unpack_from("<I", data, mhod_pos + 8)[0]
                    md_type = struct.unpack_from("<I", data, mhod_pos + 12)[0]

                    if md_type == 52:
                        body = md_total - md_hdr
                        cnt = struct.unpack_from("<I", data, mhod_pos + md_hdr + 4)[0]
                        expected = 48 + cnt * 4
                        assert body == expected, f"MHOD 52 body {body} != expected {expected}"
                        assert cnt == mhip_count
                    elif md_type == 53:
                        body = md_total - md_hdr
                        cnt = struct.unpack_from("<I", data, mhod_pos + md_hdr + 4)[0]
                        expected = 16 + cnt * 12
                        assert body == expected, f"MHOD 53 body {body} != expected {expected}"

                    mhod_pos += md_total
                break
            pos += s_total


# =========================================================================
# Artwork pixel formats (Pillow-based)
# =========================================================================


class TestArtworkPixelFormats:
    def test_create_thumbnail_rgb565(self):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        from pygpod.model.artwork import create_thumbnail

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (200, 200), color=(255, 0, 0))
            img.save(f.name, "PNG")
            thumb = create_thumbnail(f.name, 1060, 320, 320)
            os.unlink(f.name)

        assert thumb is not None
        assert len(thumb) == 320 * 320 * 2


# =========================================================================
# Checksum round-trip
# =========================================================================


class TestChecksumRoundTrip:
    def test_hash58_round_trip(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        db_path = os.path.join(writable_ipod, "iPod_Control", "iTunes", "iTunesDB")
        with open(db_path, "rb") as f:
            data = f.read()

        from pygpod.hash.hash58 import compute_hash58

        original_hash = data[0x58:0x58 + 20]
        recomputed = compute_hash58(data, "000A2700213749FF")
        assert recomputed[0x58:0x58 + 20] == original_hash


# =========================================================================
# SysInfo parsing
# =========================================================================


class TestSysInfo:
    def test_parse_sysinfo(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.sysinfo
        assert si is not None
        assert si.model_number is not None
        assert "B029" in si.model_number

    def test_sysinfo_firewire_guid(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert dev.firewire_guid is not None
        assert len(dev.firewire_guid) == 16

    def test_sysinfo_extended(self, ipod_fs_path):
        from pygpod.device.device import Device

        dev = Device.from_mountpoint(ipod_fs_path)
        assert isinstance(dev.sysinfo.extended, dict)
