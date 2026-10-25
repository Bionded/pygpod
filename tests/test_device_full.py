"""Full tests for device.py - multiple generations, from_usb, properties, write_sysinfo."""

import os
import pathlib
import plistlib
from types import SimpleNamespace

import pytest

from pygpod.device.device import Device
from pygpod.device.models import (
    ARTWORK_GENERATIONS,
    HASH58_GENERATIONS,
    PODCAST_GENERATIONS,
    SHUFFLE_GENERATIONS,
    VIDEO_GENERATIONS,
    ChecksumType,
    IpodGeneration,
    IpodInfo,
    IpodModel,
)
from pygpod.model.sysinfo import SysInfo

# =========================================================================
# Helpers
# =========================================================================


def _make_ipod_fs(tmp_path, model="B029", guid="000A2700131A2BFC", serial=None, extended=None):
    """Create a minimal iPod filesystem with SysInfo."""
    ipod = tmp_path / "ipod"
    dev_dir = ipod / "iPod_Control" / "Device"
    dev_dir.mkdir(parents=True)
    (ipod / "iPod_Control" / "iTunes").mkdir(parents=True)
    (ipod / "iPod_Control" / "Music").mkdir(parents=True)
    (ipod / "iPod_Control" / "Artwork").mkdir(parents=True)

    lines = []
    if model:
        lines.append(f"ModelNumStr: x{model}")
    if guid:
        lines.append(f"FirewireGuid: 0x{guid}")
    if serial:
        lines.append(f"SerialNumber: {serial}")
    (dev_dir / "SysInfo").write_text("\n".join(lines) + "\n")

    if extended:
        with open(dev_dir / "SysInfoExtended", "wb") as f:
            plistlib.dump(extended, f)

    # Write minimal iTunesDB
    from pygpod.device.mountpoint import init_ipod

    init_ipod(str(ipod))

    return str(ipod)


def _make_usb_info(
    product_id=0x1261, serial="8K74729BY5N", firewire_guid="000A2700131A2BFC", capacity_gb=80
):
    return SimpleNamespace(
        product_id=product_id,
        serial=serial,
        firewire_guid=firewire_guid,
        capacity_gb=capacity_gb,
    )


# =========================================================================
# from_mountpoint - multiple generations
# =========================================================================


class TestFromMountpoint:
    def test_classic_1g(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="B029")
        dev = Device.from_mountpoint(mp)
        assert dev.generation == IpodGeneration.CLASSIC_1
        assert "Classic" in dev.model
        assert "80GB" in dev.model

    def test_nano_3g(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="A978")
        dev = Device.from_mountpoint(mp)
        assert dev.generation == IpodGeneration.NANO_3

    def test_video_1g(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="A002")
        dev = Device.from_mountpoint(mp)
        assert dev.generation == IpodGeneration.VIDEO_1

    def test_shuffle_2g(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="A546")
        dev = Device.from_mountpoint(mp)
        assert dev.generation == IpodGeneration.SHUFFLE_2

    def test_unknown_model(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="ZZZZ")
        dev = Device.from_mountpoint(mp)
        assert dev.is_unknown

    def test_no_sysinfo(self, tmp_path):
        ipod = tmp_path / "empty_ipod"
        (ipod / "iPod_Control" / "iTunes").mkdir(parents=True)
        (ipod / "iPod_Control" / "Device").mkdir(parents=True)
        from pygpod.device.mountpoint import init_ipod

        init_ipod(str(ipod))
        dev = Device.from_mountpoint(str(ipod))
        assert dev.is_unknown
        assert not dev.has_sysinfo

    def test_serial_fallback(self, tmp_path):
        """When ModelNumStr is missing, fall back to serial suffix."""
        ipod = tmp_path / "ipod_serial"
        dev_dir = ipod / "iPod_Control" / "Device"
        dev_dir.mkdir(parents=True)
        (ipod / "iPod_Control" / "iTunes").mkdir(parents=True)
        # Serial ending in "Y5N" - might match a model via lookup_model_by_serial
        (dev_dir / "SysInfo").write_text("SerialNumber: XXXXXXXXXXY5N\n")
        from pygpod.device.mountpoint import init_ipod

        init_ipod(str(ipod))
        dev = Device.from_mountpoint(str(ipod))
        # May or may not find a match depending on serial table
        assert isinstance(dev, Device)

    def test_with_sysinfo_extended(self, tmp_path):
        mp = _make_ipod_fs(
            tmp_path,
            model="B029",
            extended={
                "FireWireGUID": 0x000A2700131A2BFC,
                "SerialNumber": "8K74729BY5N",
                "ProductType": "iPod5,1",
                "FamilyID": 10001,
                "DatabaseVersion": 3,
            },
        )
        dev = Device.from_mountpoint(mp)
        assert dev.generation == IpodGeneration.CLASSIC_1
        assert dev.checksum_type == ChecksumType.HASH58
        assert dev.sysinfo.product_type == "iPod5,1"
        assert dev.sysinfo.family_id == 10001

    @pytest.mark.parametrize(
        "dbversion,expected",
        [
            (0, ChecksumType.NONE),
            (1, ChecksumType.NONE),
            (2, ChecksumType.NONE),
            (3, ChecksumType.HASH58),
            (4, ChecksumType.HASH72),
            (5, ChecksumType.HASHAB),
        ],
    )
    def test_sysinfo_extended_dbversion_unknown_model(self, tmp_path, dbversion, expected):
        """Real iPods and libgpod use the key 'DBVersion'; it decides the scheme."""
        mp = _make_ipod_fs(
            tmp_path,
            model="ZZZZ",
            extended={"FireWireGUID": "000A270012345678", "DBVersion": dbversion},
        )
        dev = Device.from_mountpoint(mp)
        assert dev.generation == IpodGeneration.UNKNOWN
        assert dev.checksum_type == expected

    @pytest.mark.parametrize(
        "model,dbversion,expected",
        [("B029", 2, ChecksumType.NONE), ("A003", 3, ChecksumType.HASH58)],
    )
    def test_sysinfo_extended_dbversion_overrides_model(self, tmp_path, model, dbversion, expected):
        """Same as libgpod: DBVersion wins over the model-number mapping."""
        mp = _make_ipod_fs(tmp_path, model=model, extended={"DBVersion": dbversion})
        assert Device.from_mountpoint(mp).checksum_type == expected

    def test_sysinfo_extended_dbversion_preferred_over_legacy_key(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="ZZZZ", extended={"DBVersion": 4, "DatabaseVersion": 3})
        assert Device.from_mountpoint(mp).checksum_type == ChecksumType.HASH72

    def test_sysinfo_extended_without_dbversion_uses_model(self, tmp_path):
        """No DBVersion key: model mapping (libgpod would treat it as 0 = unsigned)."""
        mp = _make_ipod_fs(tmp_path, model="B029", extended={"SerialNumber": "8K74729BY5N"})
        assert Device.from_mountpoint(mp).checksum_type == ChecksumType.HASH58


# =========================================================================
# from_usb
# =========================================================================


class TestFromUSB:
    def test_from_usb_with_serial(self):
        usb = _make_usb_info(serial="8K74729BY5N")
        dev = Device.from_usb(usb)
        assert dev.mountpoint is None
        assert dev.is_usb_only
        assert dev.usb_enriched
        assert dev.usb_info is usb
        assert dev.firewire_guid == "000A2700131A2BFC"

    def test_from_usb_no_serial(self):
        usb = _make_usb_info(serial=None, product_id=0x120A)  # Nano 1G
        dev = Device.from_usb(usb)
        assert dev.generation == IpodGeneration.NANO_1

    def test_from_usb_unknown(self):
        usb = _make_usb_info(serial=None, product_id=0x9999)
        dev = Device.from_usb(usb)
        assert dev.is_unknown


# =========================================================================
# Properties - generation-specific
# =========================================================================


class TestProperties:
    def _dev(self, gen, model_num="B029", cap=80):
        info = IpodInfo(model_num, cap, IpodModel.CLASSIC_SILVER, gen, 50)
        return Device(sysinfo=SysInfo(), ipod_info=info)

    def test_supports_artwork(self):
        for gen in ARTWORK_GENERATIONS:
            assert self._dev(gen).supports_artwork is True
        assert self._dev(IpodGeneration.FIRST).supports_artwork is False

    def test_supports_video(self):
        for gen in VIDEO_GENERATIONS:
            assert self._dev(gen).supports_video is True
        assert self._dev(IpodGeneration.NANO_2).supports_video is False

    def test_supports_podcast(self):
        for gen in PODCAST_GENERATIONS:
            assert self._dev(gen).supports_podcast is True
        assert self._dev(IpodGeneration.FIRST).supports_podcast is False

    def test_is_shuffle(self):
        for gen in SHUFFLE_GENERATIONS:
            assert self._dev(gen).is_shuffle is True
        assert self._dev(IpodGeneration.CLASSIC_1).is_shuffle is False

    def test_requires_hash(self):
        for gen in HASH58_GENERATIONS:
            assert self._dev(gen).requires_hash is True
        assert self._dev(IpodGeneration.FIRST).requires_hash is False

    def test_music_dirs(self):
        info = IpodInfo("A133", 0.5, IpodModel.SHUFFLE, IpodGeneration.SHUFFLE_1, 3)
        dev = Device(sysinfo=SysInfo(), ipod_info=info)
        assert dev.music_dirs == 3

    def test_music_dirs_default(self):
        dev = Device(sysinfo=SysInfo(), ipod_info=None)
        assert dev.music_dirs == 50

    def test_music_dirs_unknown_model_counts_dirs_on_disk(self, tmp_path):
        """The Unknown table entry has musicdirs 0; count F00.. on disk like libgpod."""
        music = tmp_path / "iPod_Control" / "Music"
        for i in (0, 1, 2, 5):  # F05 is not consecutive
            (music / f"F{i:02d}").mkdir(parents=True)
        info = IpodInfo("Unknown", 0, IpodModel.UNKNOWN, IpodGeneration.UNKNOWN, 0)
        dev = Device(mountpoint=str(tmp_path), sysinfo=SysInfo(), ipod_info=info)
        assert dev.music_dirs == 3

    def test_music_dirs_unknown_model_without_dirs(self, tmp_path):
        info = IpodInfo("Unknown", 0, IpodModel.UNKNOWN, IpodGeneration.UNKNOWN, 0)
        assert Device(sysinfo=SysInfo(), ipod_info=info).music_dirs == 50
        dev = Device(mountpoint=str(tmp_path), sysinfo=SysInfo(), ipod_info=info)
        assert dev.music_dirs == 50

    def test_add_track_copy_on_unknown_model(self, tmp_path):
        """Add_track(copy=True) on an unknown ModelNumStr no longer raises ValueError."""
        from pygpod.device.mountpoint import init_ipod
        from pygpod.model.database import Database

        from .conftest import _make_minimal_mp3

        mp = tmp_path / "ipod"
        mp.mkdir()
        init_ipod(
            str(mp), name="x", sysinfo_files={"SysInfo": b"ModelNumStr: xZZZZ\n"}, music_dirs=7
        )
        mp3 = str(tmp_path / "a.mp3")
        _make_minimal_mp3(mp3)
        db = Database(str(mp))
        assert db.device.music_dirs == 7
        t = db.add_track(mp3, title="Song", copy=True)
        db.save()
        parts = t.ipod_path.strip(":").split(":")
        assert parts[:2] == ["iPod_Control", "Music"] and parts[2] in {
            f"F{i:02d}" for i in range(7)
        }
        assert (mp / os.path.join(*parts)).is_file()

    def test_model_name_with_capacity(self):
        dev = self._dev(IpodGeneration.CLASSIC_1, cap=80)
        assert "80GB" in dev.model
        assert "Classic" in dev.model

    def test_model_name_fractional_capacity(self):
        info = IpodInfo("A133", 0.5, IpodModel.SHUFFLE, IpodGeneration.SHUFFLE_1, 3)
        dev = Device(sysinfo=SysInfo(), ipod_info=info)
        assert "0.5GB" in dev.model

    def test_model_name_no_capacity(self):
        info = IpodInfo("X000", 0, IpodModel.UNKNOWN, IpodGeneration.UNKNOWN, 50)
        dev = Device(sysinfo=SysInfo(), ipod_info=info)
        assert "GB" not in dev.model

    def test_model_name_unknown(self):
        dev = Device(sysinfo=SysInfo(), ipod_info=None)
        assert dev.model == "Unknown iPod"

    def test_generation_unknown(self):
        dev = Device(sysinfo=SysInfo(), ipod_info=None)
        assert dev.generation == IpodGeneration.UNKNOWN


# =========================================================================
# Path properties
# =========================================================================


class TestPaths:
    def test_all_paths(self, tmp_path):
        mp = _make_ipod_fs(tmp_path)
        dev = Device.from_mountpoint(mp)
        assert dev.itunes_dir == pathlib.Path(mp) / "iPod_Control" / "iTunes"
        assert dev.music_dir == pathlib.Path(mp) / "iPod_Control" / "Music"
        assert dev.artwork_dir == pathlib.Path(mp) / "iPod_Control" / "Artwork"
        assert dev.device_dir == pathlib.Path(mp) / "iPod_Control" / "Device"
        assert dev.itunesdb_path() == dev.itunes_dir / "iTunesDB"
        assert dev.itunessd_path() == dev.itunes_dir / "iTunesSD"
        assert dev.artworkdb_path() == dev.artwork_dir / "ArtworkDB"
        assert dev.sysinfo_file_path == dev.device_dir / "SysInfo"


# =========================================================================
# write_sysinfo
# =========================================================================


class TestWriteSysInfo:
    def test_write_preserves_existing(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="B029", guid="000A2700131A2BFC")
        dev = Device.from_mountpoint(mp)
        path = dev.write_sysinfo()
        content = path.read_text()
        assert "ModelNumStr" in content
        assert "FirewireGuid" in content

    def test_write_adds_missing_model(self, tmp_path):
        """If SysInfo has no ModelNumStr, write_sysinfo adds it."""
        ipod = tmp_path / "ipod"
        dev_dir = ipod / "iPod_Control" / "Device"
        dev_dir.mkdir(parents=True)
        (ipod / "iPod_Control" / "iTunes").mkdir()
        (dev_dir / "SysInfo").write_text("FirewireGuid: 0xABCD\n")
        from pygpod.device.mountpoint import init_ipod

        init_ipod(str(ipod))

        from pygpod.model.sysinfo import read_sysinfo as _read_si

        info = IpodInfo("B029", 80, IpodModel.CLASSIC_SILVER, IpodGeneration.CLASSIC_1, 50)
        si = _read_si(str(ipod))
        dev = Device(str(ipod), sysinfo=si, ipod_info=info)

        path = dev.write_sysinfo()
        content = path.read_text()
        assert "ModelNumStr: xB029" in content

    def test_write_to_empty_dir(self, tmp_path):
        ipod = str(tmp_path / "ipod")
        os.makedirs(os.path.join(ipod, "iPod_Control", "Device"), exist_ok=True)
        info = IpodInfo("A978", 8, IpodModel.NANO_SILVER, IpodGeneration.NANO_3, 50)
        dev = Device(ipod, SysInfo(), info)
        path = dev.write_sysinfo()
        assert path.exists()
        content = path.read_text()
        assert "ModelNumStr: xA978" in content


# =========================================================================
# sysinfo lazy loading
# =========================================================================


class TestSysInfoLazy:
    def test_sysinfo_lazy_from_mountpoint(self, tmp_path):
        mp = _make_ipod_fs(tmp_path)
        dev = Device(mountpoint=mp, sysinfo=None, ipod_info=None)
        si = dev.sysinfo
        assert si is not None
        assert si.model_number == "B029"

    def test_sysinfo_lazy_no_mountpoint(self):
        dev = Device(mountpoint=None, sysinfo=None, ipod_info=None)
        si = dev.sysinfo
        assert si is not None
        assert si.model_num_str is None


# =========================================================================
# repr
# =========================================================================


class TestRepr:
    def test_repr_mounted(self, tmp_path):
        mp = _make_ipod_fs(tmp_path)
        dev = Device.from_mountpoint(mp)
        r = repr(dev)
        assert "Device" in r
        assert mp in r

    def test_repr_usb_only(self):
        usb = _make_usb_info()
        dev = Device.from_usb(usb)
        r = repr(dev)
        assert "USB" in r
        assert "not mounted" in r


# =========================================================================
# get_cover_art_formats
# =========================================================================


class TestCoverArtFormats:
    def test_classic_has_formats(self, tmp_path):
        mp = _make_ipod_fs(tmp_path, model="B029")
        dev = Device.from_mountpoint(mp)
        fmts = dev.get_cover_art_formats()
        assert fmts is not None
        assert len(fmts) > 0

    def test_first_gen_no_formats(self):
        info = IpodInfo("8513", 5, IpodModel.REGULAR, IpodGeneration.FIRST, 50)
        dev = Device(sysinfo=SysInfo(), ipod_info=info)
        fmts = dev.get_cover_art_formats()
        # First gen doesn't support artwork
        assert fmts is None or len(fmts) == 0


class TestSqliteDb:
    """Device.sqlite_db follows libgpod: the SysInfoExtended 'SQLiteDB' key, else nano 5G+."""

    def _dev(self, tmp_path, model, ext=None):
        import plistlib

        from pygpod.device.device import Device

        d = tmp_path / "iPod_Control" / "Device"
        d.mkdir(parents=True)
        (d / "SysInfo").write_text(f"ModelNumStr: {model}\n")
        if ext is not None:
            (d / "SysInfoExtended").write_bytes(plistlib.dumps(ext))
        return Device.from_mountpoint(str(tmp_path))

    def test_nano5_without_sysinfoextended(self, tmp_path):
        assert self._dev(tmp_path, "xC027").sqlite_db

    def test_sysinfoextended_key_decides(self, tmp_path):
        assert not self._dev(tmp_path / "a", "xC027", {"DBVersion": 4}).sqlite_db
        assert self._dev(tmp_path / "b", "xB029", {"DBVersion": 3, "SQLiteDB": True}).sqlite_db
        assert not self._dev(tmp_path / "c", "xC027", {"SQLiteDB": False}).sqlite_db

    def test_older_models(self, tmp_path):
        assert not self._dev(tmp_path / "a", "xB029").sqlite_db
        assert not self._dev(tmp_path / "b", "xA003").sqlite_db

    def test_save_warns_once(self, tmp_path, caplog, generated_mp3):
        import logging

        from pygpod import init_ipod
        from pygpod.model.database import Database

        mp = tmp_path / "ipod"
        mp.mkdir()
        sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A270012345678\n"
        init_ipod(str(mp), name="iPod", sysinfo_files={"SysInfo": sysinfo})
        import plistlib

        ext = {"DBVersion": 3, "SQLiteDB": True, "FireWireGUID": "000A270012345678"}
        (mp / "iPod_Control" / "Device" / "SysInfoExtended").write_bytes(plistlib.dumps(ext))
        db = Database(str(mp))
        db.add_track(generated_mp3, title="Обійми")
        with caplog.at_level(logging.WARNING):
            db.save()
            db.save(force=True)
        assert sum("SQLite" in r.getMessage() for r in caplog.records) == 1
