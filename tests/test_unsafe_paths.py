"""Paths from the iTunesDB are untrusted: they must never leave the mount point."""

import os

import pytest

from pygpod.db.constants import MHOD_ID_PATH
from pygpod.exceptions import UnsafePathError
from pygpod.model.database import Database
from pygpod.utils.encoding import ipod_path_to_os


@pytest.mark.parametrize(
    "path",
    [
        ":iPod_Control:..:..:sentinel.txt",
        ":..:sentinel.txt",
        ":iPod_Control:Music:.:x.mp3",
        ":/etc/passwd",
        "/etc/passwd",
        ":C:\\Windows\\x.mp3",
        ":iPod_Control:Music:F00:a\\..\\..\\b.mp3",
        ":iPod_Control::x.mp3",
        ":iPod_Control:Music:x\x00.mp3",
        "",
    ],
)
def test_rejected(tmp_path, path):
    with pytest.raises(UnsafePathError):
        ipod_path_to_os(path, str(tmp_path / "ipod"))


def test_valid_path(tmp_path):
    mp = str(tmp_path)
    assert ipod_path_to_os(":iPod_Control:Music:F00:ABCD.mp3", mp) == os.path.join(
        mp, "iPod_Control", "Music", "F00", "ABCD.mp3"
    )
    # case differences are fine (FAT is case-insensitive), the path stays inside
    assert ipod_path_to_os(":IPOD_CONTROL:MUSIC:F00:ABCD.MP3", mp).startswith(mp)


def test_symlink_escape_rejected(tmp_path):
    mp = tmp_path / "ipod"
    (mp / "iPod_Control" / "Music").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (mp / "iPod_Control" / "Music" / "F00").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        ipod_path_to_os(":iPod_Control:Music:F00:x.mp3", str(mp))


@pytest.mark.parametrize(
    "bad", [":iPod_Control:..:..:sentinel.txt", ":iPod_Control:Music:F00:link.mp3"]
)
def test_remove_track_never_deletes_outside(writable_ipod, tmp_path, bad):
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep")
    # writable_ipod is tmp_path/ipod: two levels up from iPod_Control is tmp_path
    link = os.path.join(writable_ipod, "iPod_Control", "Music", "F00", "link.mp3")
    os.symlink(sentinel, link)

    db = Database(writable_ipod)
    track = db.tracks[0]
    n = len(db.tracks)
    track._set_mhod(MHOD_ID_PATH, bad)
    with pytest.raises(UnsafePathError):
        db.remove_track(track, delete_file=True)
    assert sentinel.read_text() == "keep"
    assert len(db.tracks) == n  # nothing changed


def test_remove_track_valid_path_deletes_file(writable_ipod_with_mp3):
    mp, mp3 = writable_ipod_with_mp3
    db = Database(mp)
    track = db.add_track(mp3, title="t")
    fpath = ipod_path_to_os(track.ipod_path, mp)
    assert os.path.isfile(fpath)
    db.remove_track(track, delete_file=True)
    assert os.path.isfile(fpath)  # deleted by the save, not before it
    db.save()
    assert not os.path.exists(fpath)


def _link_or_skip(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not available")


@pytest.mark.parametrize("linked", ["Music", "Fxx"])
def test_add_track_never_writes_through_a_link_leading_outside(
    writable_ipod_with_mp3, tmp_path, linked
):
    import shutil

    mp, mp3 = writable_ipod_with_mp3
    outside = tmp_path / "назовні"
    outside.mkdir()
    music = os.path.join(mp, "iPod_Control", "Music")
    if linked == "Music":
        shutil.rmtree(music)
        _link_or_skip(str(outside), music)
    else:
        for name in os.listdir(music):  # every Fxx folder leads outside
            shutil.rmtree(os.path.join(music, name))
        for i in range(100):
            _link_or_skip(str(outside), os.path.join(music, f"F{i:02d}"))
    db = Database(mp)
    n = len(db.tracks)
    with pytest.raises(UnsafePathError):
        db.add_track(mp3, title="Мандрівка")
    assert list(outside.iterdir()) == []
    assert len(db.tracks) == n


def test_add_track_still_copies_into_music(writable_ipod_with_mp3):
    mp, mp3 = writable_ipod_with_mp3
    db = Database(mp)
    track = db.add_track(mp3, title="Вдома")
    assert os.path.isfile(ipod_path_to_os(track.ipod_path, mp))
