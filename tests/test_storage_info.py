"""Tests for Device.storage_info() and StorageInfo."""

import pytest

from pygpod.device.device import Device, StorageInfo, _dir_size, _file_size, _fmt_size


class TestFmtSize:
    def test_bytes(self):
        assert _fmt_size(500) == "500 B"

    def test_kb(self):
        assert "KB" in _fmt_size(2048)

    def test_mb(self):
        assert "MB" in _fmt_size(5 * 1024 * 1024)

    def test_gb(self):
        assert "GB" in _fmt_size(2 * 1024**3)

    def test_zero(self):
        assert _fmt_size(0) == "0 B"


class TestFileSize:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00" * 1024)
        assert _file_size(f) == 1024

    def test_nonexistent(self, tmp_path):
        assert _file_size(tmp_path / "nope.bin") == 0


class TestDirSize:
    def test_dir_with_files(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"\x00" * 100)
        (tmp_path / "b.bin").write_bytes(b"\x00" * 200)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.bin").write_bytes(b"\x00" * 300)
        assert _dir_size(tmp_path) == 600

    def test_empty_dir(self, tmp_path):
        assert _dir_size(tmp_path) == 0

    def test_nonexistent_dir(self, tmp_path):
        assert _dir_size(tmp_path / "nope") == 0


class TestStorageInfo:
    def test_repr(self):
        si = StorageInfo(total=80 * 1024**3, used=40 * 1024**3, free=40 * 1024**3)
        r = repr(si)
        assert "80.0 GB" in r
        assert "40.0 GB" in r

    def test_defaults_zero(self):
        si = StorageInfo()
        assert si.total == 0
        assert si.free == 0
        assert si.music == 0
        assert si.is_scanned is False

    def test_str_not_scanned(self):
        si = StorageInfo(total=100, used=60, free=40)
        s = str(si)
        assert "Total:" in s
        assert "Used:" in s
        assert "Free:" in s
        # No breakdown when not scanned
        assert "Music files:" not in s

    def test_str_scanned(self):
        si = StorageInfo(total=100, used=60, free=40)
        si.music = 30
        si.artwork_thumbnails = 10
        si.artwork_db = 1
        si.itunes_db = 2
        si.other = 17
        si._scanned = True
        s = str(si)
        assert "Music files:" in s
        assert "Artwork (.ithmb):" in s
        assert "ArtworkDB:" in s
        assert "iTunesDB:" in s
        assert "Other/System:" in s
        # No iTunesSD or Photos when zero
        assert "iTunesSD" not in s
        assert "Photos" not in s

    def test_str_with_optional(self):
        si = StorageInfo(total=1, used=1, free=0)
        si.itunes_sd = 1024
        si.photos = 5000
        si._scanned = True
        s = str(si)
        assert "iTunesSD:" in s
        assert "Photos:" in s


class TestStorageInfoScan:
    def test_scan_populates_fields(self, ipod_fs_path):
        si = StorageInfo(total=999999, used=888888, free=111111)
        assert si.is_scanned is False

        si.scan(ipod_fs_path)
        assert si.is_scanned is True
        assert si.itunes_db > 0
        assert si.music >= 0
        assert si.artwork_db >= 0

    def test_scan_can_be_called_twice(self, ipod_fs_path):
        si = StorageInfo(total=999, used=999, free=0)
        si.scan(ipod_fs_path)
        first_db = si.itunes_db
        si.scan(ipod_fs_path)
        assert si.itunes_db == first_db  # same data

    def test_scan_empty_mountpoint(self, tmp_path):
        si = StorageInfo(total=100, used=0, free=100)
        si.scan(str(tmp_path))
        assert si.is_scanned is True
        assert si.music == 0
        assert si.itunes_db == 0
        assert si.other == 0


class TestDeviceStorageInfo:
    def test_full_true(self, ipod_fs_path):
        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.storage_info(full=True)

        assert si.total > 0
        assert si.free >= 0
        assert si.used >= 0
        assert si.total == si.used + si.free
        assert si.is_scanned is True
        assert si.itunes_db > 0

    def test_full_false(self, ipod_fs_path):
        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.storage_info(full=False)

        assert si.total > 0
        assert si.free >= 0
        assert si.is_scanned is False
        assert si.music == 0  # not scanned
        assert si.itunes_db == 0

    def test_default_is_full(self, ipod_fs_path):
        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.storage_info()
        assert si.is_scanned is True

    def test_no_mountpoint_raises(self):
        from pygpod.exceptions import DeviceError

        dev = Device(mountpoint=None)
        with pytest.raises(DeviceError):
            dev.storage_info()

    def test_full_print(self, ipod_fs_path):
        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.storage_info(full=True)
        output = str(si)
        assert "Total:" in output
        assert "Music files:" in output
        assert "iTunesDB:" in output

    def test_brief_print(self, ipod_fs_path):
        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.storage_info(full=False)
        output = str(si)
        assert "Total:" in output
        assert "Music files:" not in output

    def test_scan_after_brief(self, ipod_fs_path):
        """Can get brief first, then scan for details later."""
        dev = Device.from_mountpoint(ipod_fs_path)
        si = dev.storage_info(full=False)
        assert not si.is_scanned
        assert si.itunes_db == 0

        si.scan(ipod_fs_path)
        assert si.is_scanned
        assert si.itunes_db > 0
