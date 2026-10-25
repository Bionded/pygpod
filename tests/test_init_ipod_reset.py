"""init_ipod from zero: device name, SysInfo preservation, reset and dry-run."""

import os

import pytest

from pygpod.db.artwork_parser import parse_artworkdb
from pygpod.device.mountpoint import init_ipod, read_sysinfo_files
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

SYSINFO = b"FirewireGuid: 0x000A2700213749FF\nModelNumStr: xB029\n"
SYSINFO_EXT = (
    b'<?xml version="1.0"?><plist><dict>'
    b"<key>FireWireGUID</key><string>000A27</string></dict></plist>"
)


def _snapshot(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            out.append((p, os.path.getsize(p) if os.path.isfile(p) else None))
    return sorted(out)


def test_init_empty_dir_with_name(tmp_path):
    mp = str(tmp_path)
    actions = init_ipod(mp, name="Юрин iPod")
    for i in range(50):
        assert os.path.isdir(os.path.join(mp, "iPod_Control", "Music", f"F{i:02d}"))
    assert ("write", os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB")) in actions
    db = Database(mp)
    assert db.tracks == []
    assert db.master_playlist.name == "Юрин iPod"
    with open(os.path.join(mp, "iPod_Control", "Artwork", "ArtworkDB"), "rb") as f:
        art = parse_artworkdb(f.read())
    assert [c.fields.get("mhsd_type") for c in art.children] == [1, 2, 3]


def test_dry_run_touches_nothing(tmp_path):
    mp = str(tmp_path)
    before = _snapshot(mp)
    actions = init_ipod(mp, name="X", dry_run=True)
    assert _snapshot(mp) == before
    ops = {a.op for a in actions}
    assert ops == {"mkdir", "write"}
    assert any(a.path.endswith("F49") for a in actions)


def test_sysinfo_written_after_format(tmp_path):
    """Format flow: read SysInfo files, (caller formats), init with them."""
    old = tmp_path / "old"
    old.mkdir()
    init_ipod(str(old), sysinfo_files={"SysInfo": SYSINFO, "SysInfoExtended": SYSINFO_EXT})
    saved = read_sysinfo_files(str(old))
    assert saved == {"SysInfo": SYSINFO, "SysInfoExtended": SYSINFO_EXT}

    fresh = tmp_path / "fresh"  # a freshly formatted, empty filesystem
    fresh.mkdir()
    init_ipod(str(fresh), name="New", sysinfo_files=saved)
    dev = fresh / "iPod_Control" / "Device"
    assert (dev / "SysInfo").read_bytes() == SYSINFO
    assert (dev / "SysInfoExtended").read_bytes() == SYSINFO_EXT
    assert Database(str(fresh)).master_playlist.name == "New"


def test_reset_removes_tracks_keeps_sysinfo(tmp_path):
    mp = str(tmp_path)
    init_ipod(mp, name="Old", sysinfo_files={"SysInfo": SYSINFO, "SysInfoExtended": SYSINFO_EXT})
    mp3 = str(tmp_path.parent / f"{tmp_path.name}.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    t = db.add_track(mp3, title="gone")
    db.save()
    track_file = os.path.join(mp, *t.ipod_path.lstrip(":").split(":"))
    assert os.path.isfile(track_file)
    with open(os.path.join(mp, "iPod_Control", "iTunes", "Play Counts"), "wb") as f:
        f.write(b"stale")
    other = os.path.join(mp, "Notes", "keep.txt")
    with open(other, "w") as f:
        f.write("user note")

    plan = init_ipod(mp, name="Fresh", reset=True, dry_run=True)
    assert os.path.isfile(track_file)  # dry-run did nothing
    assert ("delete", track_file) in plan

    init_ipod(mp, name="Fresh", reset=True)
    assert not os.path.exists(track_file)
    assert not os.path.exists(os.path.join(mp, "iPod_Control", "iTunes", "Play Counts"))
    dev = os.path.join(mp, "iPod_Control", "Device")
    assert open(os.path.join(dev, "SysInfo"), "rb").read() == SYSINFO
    assert open(os.path.join(dev, "SysInfoExtended"), "rb").read() == SYSINFO_EXT
    assert open(other).read() == "user note"
    db = Database(mp)
    assert db.tracks == [] and db.master_playlist.name == "Fresh"
    assert os.path.isdir(os.path.join(mp, "iPod_Control", "Music", "F00"))


def test_reset_without_keep_sysinfo(tmp_path):
    mp = str(tmp_path)
    init_ipod(mp, sysinfo_files={"SysInfo": SYSINFO})
    init_ipod(mp, reset=True, keep_sysinfo=False)
    assert not os.path.exists(os.path.join(mp, "iPod_Control", "Device", "SysInfo"))


def test_existing_db_kept_without_reset(tmp_path):
    mp = str(tmp_path)
    init_ipod(mp, name="First")
    init_ipod(mp, name="Second")
    assert Database(mp).master_playlist.name == "First"


def test_bad_sysinfo_name_refused(tmp_path):
    with pytest.raises(ValueError):
        init_ipod(str(tmp_path), sysinfo_files={"../evil": b"x"}, dry_run=True)


@pytest.mark.parametrize("model", ["xB029", "xC297", "xA978"])
def test_init_signs_hash58_db(tmp_path, model):
    """Like libgpod's itdb_init_ipod, the new iTunesDB of a hash58 device is signed."""
    from pygpod.doctor import check
    from pygpod.hash.hash58 import compute_hash58

    mp = str(tmp_path)
    guid = "000A270012345678"
    init_ipod(
        mp,
        name="x",
        sysinfo_files={"SysInfo": f"ModelNumStr: {model}\nFirewireGuid: 0x{guid}\n".encode()},
    )
    data = open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    assert data[0x58:0x6C] != b"\x00" * 20
    assert compute_hash58(data, guid) == data
    assert check(Database(mp)).kinds() == set()


def test_init_without_guid_stays_unsigned(tmp_path):
    mp = str(tmp_path)
    init_ipod(mp, name="x", sysinfo_files={"SysInfo": b"ModelNumStr: xB029\n"})
    data = open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    assert data[0x58:0x6C] == b"\x00" * 20
    assert Database(mp).master_playlist.name == "x"


def test_init_unsigned_device_and_dry_run(tmp_path):
    mp = str(tmp_path / "a")
    os.makedirs(mp)
    init_ipod(
        mp, sysinfo_files={"SysInfo": b"ModelNumStr: xA003\nFirewireGuid: 0x000A270012345678\n"}
    )
    data = open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb").read()
    assert data[0x58:0x6C] == b"\x00" * 20  # 5G: no hash
    dry = str(tmp_path / "b")
    os.makedirs(dry)
    init_ipod(dry, sysinfo_files={"SysInfo": SYSINFO}, dry_run=True)
    assert os.listdir(dry) == []


def test_init_signs_hash72_with_hashinfo(tmp_path):
    """Nano 5G: signed with the device's HashInfo; without HashInfo it stays unsigned."""
    import shutil

    from pygpod.hash.hash72 import compute_hash72

    from .conftest import PROJECT_ROOT

    guid = "000A270012345678"
    ext = os.path.join(PROJECT_ROOT, "tests", "data", "hash72_libgpod", "SysInfoExtended")
    sysinfo = f"ModelNumStr: xC027\nFirewireGuid: 0x{guid}\n".encode()
    for with_info in (True, False):
        mp = tmp_path / str(with_info)
        dev = mp / "iPod_Control" / "Device"
        dev.mkdir(parents=True)
        shutil.copy(ext, dev / "SysInfoExtended")
        iv, rnd = os.urandom(16), os.urandom(12)
        if with_info:
            uuid = bytes.fromhex(guid).ljust(20, b"\x00")
            (dev / "HashInfo").write_bytes(b"HASHv0" + uuid + rnd + iv)
        init_ipod(str(mp), name="Nano", sysinfo_files={"SysInfo": sysinfo})
        data = (mp / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes()
        if with_info:
            assert data[0x72] == 0x01 and compute_hash72(data, iv=iv, random_bytes=rnd) == data
        else:
            assert data[0x72 : 0x72 + 46] == b"\x00" * 46
