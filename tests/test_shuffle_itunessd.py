"""iPod shuffle 1G/2G: save() writes the iTunesSD exactly like libgpod; 3G/4G are refused."""

import os
import subprocess

import pytest

from pygpod.db.itunessd import ITunesSD
from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import DeviceError
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


def _sd(mp):
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesSD"), "rb") as f:
        return f.read()


def _shuffle(tmp_path, model):
    mp = str(tmp_path / "shuffle")
    os.makedirs(mp)
    sysinfo = f"ModelNumStr: {model}\nFirewireGuid: 0x000A2700213749FF\n"
    init_ipod(mp, sysinfo_files={"SysInfo": sysinfo.encode()})
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    return mp, mp3


def test_save_writes_itunessd_for_shuffle_2g(tmp_path):
    mp, mp3 = _shuffle(tmp_path, "xA546")
    db = Database(mp)
    a = db.add_track(mp3, title="Пісня")
    b = db.add_track(mp3, title="Випуск", media_type=4)  # podcast: bookmarkable, skipped in shuffle
    db.save()
    sd = ITunesSD.parse(_sd(mp))
    assert [t.filename for t in sd.tracks] == [x.ipod_path.replace(":", "/") for x in (a, b)]
    assert [(t.shuffle_flag, t.bookmark_flag) for t in sd.tracks] == [(0, 0), (1, 1)]
    assert all(t.volume == 100 and t.file_type == 1 for t in sd.tracks)

    db = Database(mp)
    db.remove_track(db.tracks[0], delete_file=True)
    db.save()
    assert [t.filename for t in ITunesSD.parse(_sd(mp)).tracks] == [b.ipod_path.replace(":", "/")]


def test_shuffle_3g_refused_before_writing(tmp_path):
    mp, mp3 = _shuffle(tmp_path, "xC306")  # shuffle 3G
    before = os.listdir(os.path.join(mp, "iPod_Control", "iTunes"))
    db = Database(mp)
    db.add_track(mp3, title="x")
    with pytest.raises(DeviceError, match="shuffle 3G/4G"):
        db.save()
    assert os.listdir(os.path.join(mp, "iPod_Control", "iTunes")) == before


def test_no_itunessd_for_other_ipods(tmp_path):
    mp, mp3 = _shuffle(tmp_path, "xB029")
    db = Database(mp)
    db.add_track(mp3, title="x")
    db.save()
    assert not os.path.exists(os.path.join(mp, "iPod_Control", "iTunes", "iTunesSD"))


@pytest.mark.libgpod
def test_itunessd_identical_to_libgpod(tool, tmp_path):
    mp = tmp_path / "lg"
    for d in ("iTunes", "Device", "Music/F00"):
        (mp / "iPod_Control" / d).mkdir(parents=True)
    (mp / "iPod_Control" / "Device" / "SysInfo").write_text("ModelNumStr: xA546\n")
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    spec = tmp_path / "spec.tsv"
    spec.write_text(
        "name\tShuffle\n"
        f"track\t{mp3}\tПісня\tОкеан Ельзи\tАльбом\tRock\t1\t1988\t1\t0\t0\n"
        f"track\t{mp3}\tВипуск\tРадіо-Т\tРадіо-Т\tPodcast\t1\t2024\t4\t0\t0\n"
    )
    subprocess.run([tool, "build", str(mp), str(spec)], check=True, capture_output=True)
    theirs = (mp / "iPod_Control" / "iTunes" / "iTunesSD").read_bytes()
    parsed = ITunesSD.parse(theirs)
    assert len(parsed.tracks) == 2 and parsed.tracks[1].bookmark_flag == 1

    db = Database(str(mp))
    db._mark_modified()
    db.save()
    assert _sd(str(mp)) == theirs  # byte-identical to libgpod's iTunesSD
