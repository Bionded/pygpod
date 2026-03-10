"""Tests for mountpoint utilities and init_ipod."""

import os


class TestValidateMountpoint:

    def test_valid(self, ipod_fs_path):
        from pygpod.device.mountpoint import validate_mountpoint
        assert validate_mountpoint(ipod_fs_path)

    def test_invalid_nonexistent(self):
        from pygpod.device.mountpoint import validate_mountpoint
        assert not validate_mountpoint("/nonexistent/path")

    def test_invalid_no_ipod_control(self, tmp_path):
        from pygpod.device.mountpoint import validate_mountpoint
        assert not validate_mountpoint(str(tmp_path))


class TestFindItunesdb:

    def test_find(self, ipod_fs_path):
        from pygpod.device.mountpoint import find_itunesdb
        path = find_itunesdb(ipod_fs_path)
        assert path is not None
        assert os.path.isfile(path)

    def test_not_found(self, tmp_path):
        from pygpod.device.mountpoint import find_itunesdb
        assert find_itunesdb(str(tmp_path)) is None


class TestInitIpod:

    def test_init_creates_structure(self, tmp_path):
        from pygpod.device.mountpoint import init_ipod
        mp = str(tmp_path / "ipod")
        os.makedirs(mp)
        init_ipod(mp)
        assert os.path.isdir(os.path.join(mp, "iPod_Control", "iTunes"))
        assert os.path.isdir(os.path.join(mp, "iPod_Control", "Music"))
        assert os.path.isfile(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"))

    def test_init_creates_music_dirs(self, tmp_path):
        from pygpod.device.mountpoint import init_ipod
        mp = str(tmp_path / "ipod2")
        os.makedirs(mp)
        init_ipod(mp, music_dirs=10)
        for i in range(10):
            assert os.path.isdir(os.path.join(mp, "iPod_Control", "Music", f"F{i:02d}"))

    def test_init_db_is_parseable(self, tmp_path):
        from pygpod.device.mountpoint import init_ipod
        from pygpod.model.database import Database
        mp = str(tmp_path / "ipod3")
        os.makedirs(mp)
        init_ipod(mp)
        # Should be loadable
        db = Database(mp)
        assert db.master_playlist is not None
        assert len(db.tracks) == 0

    def test_init_custom_music_dirs_count(self, tmp_path):
        from pygpod.device.mountpoint import init_ipod
        mp = str(tmp_path / "ipod4")
        os.makedirs(mp)
        init_ipod(mp, music_dirs=5)
        music_dir = os.path.join(mp, "iPod_Control", "Music")
        dirs = [d for d in os.listdir(music_dir) if d.startswith("F")]
        assert len(dirs) == 5
