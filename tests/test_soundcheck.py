"""Sound Check from iTunNORM / ReplayGain tags."""

import os
import shutil
import subprocess

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TXXX
from mutagen.mp4 import MP4, MP4FreeForm

from pygpod.db.parser import parse_itunesdb
from pygpod.db.writer import write_itunesdb
from pygpod.model.database import Database
from pygpod.tags import read_soundcheck, soundcheck_from_gain

from .conftest import _make_minimal_mp3
from .test_media_validation import _ipod

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
ITUNNORM = (
    " 00000A2C 00000B00 00002B3E 00002C10 00024CA8 00024CA8 00007FFF 00007FFF 00024CA8 00024CA8"
)
FROM_NORM = 0xB00  # the larger of the first two values


def _mp3(tmp_path, name, **frames):
    path = str(tmp_path / name)
    _make_minimal_mp3(path)
    tags = ID3()
    if "norm" in frames:
        tags.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=[frames["norm"]]))
    for key in ("REPLAYGAIN_TRACK_GAIN", "replaygain_album_gain"):
        if key.lower() in frames:
            tags.add(TXXX(encoding=3, desc=key, text=[frames[key.lower()]]))
    tags.save(path)
    return path


def _ffmpeg_audio(path, codec):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
         "-c:a", codec, str(path)],
        check=True,
        timeout=60,
    )  # fmt: skip
    return str(path)


def test_formula_matches_gtkpod():
    assert soundcheck_from_gain(0.0) == 1000
    assert soundcheck_from_gain(-6.5) == 4467  # round(1000 * 10^0.65)
    assert soundcheck_from_gain(2.0) == 631
    assert soundcheck_from_gain(-200.0) == 0xFFFFFFFF
    assert soundcheck_from_gain(200.0) == 1  # 0 would mean "no Sound Check"


def test_id3_itunnorm_track_gain_album_gain_and_none(tmp_path):
    assert read_soundcheck(_mp3(tmp_path, "norm.mp3", norm=ITUNNORM)) == FROM_NORM
    both = _mp3(tmp_path, "both.mp3", norm=ITUNNORM, replaygain_track_gain="-6.50 dB")
    assert read_soundcheck(both) == FROM_NORM  # iTunNORM wins
    track = _mp3(
        tmp_path, "rg.mp3", replaygain_track_gain="-6.50 dB", replaygain_album_gain="+2.00 dB"
    )
    assert read_soundcheck(track) == 4467  # track gain before album gain
    assert read_soundcheck(_mp3(tmp_path, "album.mp3", replaygain_album_gain="+2.00 dB")) == 631
    bad_norm = _mp3(tmp_path, "bad.mp3", norm="zz zz", replaygain_track_gain="0 dB")
    assert read_soundcheck(bad_norm) == 1000
    assert read_soundcheck(_mp3(tmp_path, "none.mp3")) is None
    assert read_soundcheck(str(tmp_path / "missing.mp3")) is None


@needs_ffmpeg
def test_mp4_freeform_and_flac_comments(tmp_path):
    m4a = _ffmpeg_audio(tmp_path / "norm.m4a", "aac")
    f = MP4(m4a)
    f["----:com.apple.iTunes:iTunNORM"] = [MP4FreeForm(ITUNNORM.encode())]
    f.save()
    assert read_soundcheck(m4a) == FROM_NORM
    rg = _ffmpeg_audio(tmp_path / "rg.m4a", "aac")
    f = MP4(rg)
    f["----:com.apple.iTunes:replaygain_track_gain"] = [MP4FreeForm(b"-6.50 dB")]
    f.save()
    assert read_soundcheck(rg) == 4467
    flac = _ffmpeg_audio(tmp_path / "rg.flac", "flac")
    f = FLAC(flac)
    f["REPLAYGAIN_ALBUM_GAIN"] = "+2.00 dB"
    f.save()
    assert read_soundcheck(flac) == 631


def test_add_track_writes_soundcheck_and_explicit_value_wins(tmp_path):
    mp = _ipod(tmp_path, "B029")
    db = Database(mp)
    a = db.add_track(_mp3(tmp_path, "a.mp3", replaygain_track_gain="-6.50 dB"))
    b = db.add_track(_mp3(tmp_path, "b.mp3", norm=ITUNNORM), soundcheck=1234)
    c = db.add_track(_mp3(tmp_path, "c.mp3"))
    assert (a.soundcheck, b.soundcheck, c.soundcheck) == (4467, 1234, 0)
    db.save()
    db = Database(mp)
    got = {t.track_id: t.soundcheck for t in db.tracks}
    assert got == {a.track_id: 4467, b.track_id: 1234, c.track_id: 0}
    t = db.get_track(c.track_id)
    t.soundcheck = 999
    with pytest.raises(ValueError):
        t.soundcheck = -1
    db.save()
    assert Database(mp).get_track(c.track_id).soundcheck == 999
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        data = f.read()
    assert write_itunesdb(parse_itunesdb(data)) == data


@needs_ffmpeg
def test_converted_flac_keeps_the_gain(tmp_path):
    mp = _ipod(tmp_path, "B029")
    flac = _ffmpeg_audio(tmp_path / "song.flac", "flac")
    f = FLAC(flac)
    f["REPLAYGAIN_TRACK_GAIN"] = "-6.50 dB"
    f.save()
    track = Database(mp).add_track(flac, convert=True)
    assert track.soundcheck == 4467


@pytest.mark.libgpod
def test_libgpod_reads_the_soundcheck(tool, tmp_path):
    mp = _ipod(tmp_path, "B029")
    db = Database(mp)
    db.add_track(_mp3(tmp_path, "a.mp3", replaygain_track_gain="-6.50 dB"), title="rg")
    db.add_track(_mp3(tmp_path, "b.mp3", norm=ITUNNORM), title="norm")
    db.add_track(_mp3(tmp_path, "c.mp3"), title="none")
    db.save()
    out = subprocess.run([tool, "dump-video", mp], capture_output=True, text=True, check=True)
    got = {f[1]: int(f[18]) for f in (line.split("\t") for line in out.stdout.splitlines())}
    assert got == {"rg": 4467, "norm": FROM_NORM, "none": 0}


@pytest.mark.parametrize("desc", ["replaygain_track_gain", "REPLAYGAIN_TRACK_GAIN"])
def test_sync_tool_usage_copy_false_and_update(tmp_path, desc):
    """How a sync tool drives it: the file is already on the iPod (copy=False) with a
    ReplayGain tag in any case; an explicit value from its own analysis wins; a track's value
    is fixed later through Track.soundcheck without re-adding it."""
    mp = _ipod(tmp_path, "B029")
    music = os.path.join(mp, "iPod_Control", "Music", "F00")
    os.makedirs(music, exist_ok=True)
    dest = os.path.join(music, "Обійми.mp3")
    _make_minimal_mp3(dest)
    tags = ID3()
    tags.add(TXXX(encoding=3, desc=desc, text=["-6.18 dB"]))
    tags.save(dest)

    db = Database(mp)
    from_tag = db.add_track(dest, copy=False, title="Обійми", artist="Океан Ельзи")
    assert from_tag.soundcheck == soundcheck_from_gain(-6.18)
    sc = soundcheck_from_gain(-3.5)
    explicit = db.add_track(dest, copy=False, title="Ґанок", **{"soundcheck": sc})
    assert explicit.soundcheck == sc
    db.save()

    db = Database(mp)
    track = [t for t in db.tracks if t.title == "Обійми"][0]
    track.soundcheck = soundcheck_from_gain(1.25)
    db.save()
    reopened = {t.title: t.soundcheck for t in Database(mp).tracks}
    assert reopened == {"Обійми": soundcheck_from_gain(1.25), "Ґанок": sc}


def test_mp4_lowercase_freeform_gain(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = _ffmpeg_audio(tmp_path / "Ґанок.m4a", "aac")
    audio = MP4(path)
    audio["----:com.apple.iTunes:replaygain_track_gain"] = [MP4FreeForm(b"-6.18 dB")]
    audio.save()
    assert read_soundcheck(path) == soundcheck_from_gain(-6.18)
