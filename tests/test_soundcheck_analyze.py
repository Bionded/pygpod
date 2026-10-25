"""Sound Check measured by ffmpeg's replaygain filter: soundcheck='analyze' / 'auto',
Database.analyze_soundcheck() and 'pygpod track add --soundcheck'. Needs ffmpeg (fixtures
and the analysis); without it only the error paths run."""

import math
import os
import shutil
import subprocess

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import TrackError, UnsupportedFormatError
from pygpod.model.database import Database
from pygpod.tags import analyze_gain_db, analyze_soundcheck, soundcheck_from_gain

from .conftest import _make_minimal_mp3

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(path, volume_db, codec="libmp3lame", seconds=3):
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi"]
        + ["-i", f"sine=f=440:d={seconds},volume={volume_db}dB", "-c:a", codec, path],
        check=True,
    )
    return path


def _gain(soundcheck):
    """dB gain back from a Sound Check value (inverse of soundcheck_from_gain)."""
    return -10 * math.log10(soundcheck / 1000)


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A270012345678\n"
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": sysinfo})
    return mp


@pytest.fixture
def tones(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    return {
        "loud": _tone(str(tmp_path / "Гучна.mp3"), -6),
        "quiet": _tone(str(tmp_path / "Тиха.mp3"), -20),
    }


@needs_ffmpeg
def test_analyze_gives_ffmpeg_gain(ipod, tones):
    db = Database(ipod)
    loud = db.add_track(tones["loud"], title="Гучна", soundcheck="analyze")
    quiet = db.add_track(tones["quiet"], title="Тиха", soundcheck="analyze")
    assert abs(_gain(loud.soundcheck) - analyze_gain_db(tones["loud"])) < 0.5
    assert abs(_gain(quiet.soundcheck) - analyze_gain_db(tones["quiet"])) < 0.5
    # the louder file needs less gain: a higher Sound Check value
    assert loud.soundcheck > quiet.soundcheck
    assert abs((_gain(quiet.soundcheck) - _gain(loud.soundcheck)) - 14) < 1
    db.save()
    assert [t.soundcheck for t in Database(ipod).tracks] == [loud.soundcheck, quiet.soundcheck]


@needs_ffmpeg
def test_analyze_converted_flac_measures_the_alac(ipod, tmp_path):
    flac = _tone(str(tmp_path / "Ґанок.flac"), -10, codec="flac")
    db = Database(ipod)
    track = db.add_track(flac, convert=True, soundcheck="analyze")
    assert track.ipod_path.lower().endswith(".m4a")  # converted (ALAC on the Classic)
    assert abs(_gain(track.soundcheck) - analyze_gain_db(flac)) < 0.5


@needs_ffmpeg
def test_auto_prefers_gain_tags(ipod, tones):
    from mutagen.id3 import ID3, TXXX

    tagged = tones["quiet"]
    tags = ID3(tagged)
    tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-3.00 dB"]))
    tags.save(tagged)
    db = Database(ipod)
    track = db.add_track(tagged, soundcheck="auto")
    assert track.soundcheck == soundcheck_from_gain(-3.0)
    untagged = db.add_track(tones["loud"], soundcheck="auto")
    assert untagged.soundcheck == analyze_soundcheck(tones["loud"])
    measured = db.add_track(tagged, soundcheck="analyze")  # analyze ignores the tag
    assert measured.soundcheck == analyze_soundcheck(tagged) != track.soundcheck


def test_default_does_not_analyze(ipod, tmp_path, monkeypatch):
    """Without the kwarg nothing is measured and the database is as before (no gain tags -> 0)."""
    import pygpod.tags

    def boom(*a, **k):
        raise AssertionError("analysed without being asked")

    monkeypatch.setattr(pygpod.tags, "analyze_soundcheck", boom)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(ipod)
    assert db.add_track(mp3).soundcheck == 0
    assert db.add_track(mp3, soundcheck=1234).soundcheck == 1234


def test_no_ffmpeg(ipod, tmp_path, monkeypatch):
    """No ffmpeg: 'analyze' raises before anything is copied, 'auto' warns and writes 0."""
    monkeypatch.setenv("PYGPOD_FFMPEG", str(tmp_path / "nonexistent" / "ffmpeg"))
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(ipod)
    with pytest.raises(UnsupportedFormatError, match="ffmpeg"):
        db.add_track(mp3, soundcheck="analyze")
    assert not db.tracks
    music = os.path.join(ipod, "iPod_Control", "Music")
    assert not [f for _, _, files in os.walk(music) for f in files]
    assert db.add_track(mp3, soundcheck="auto").soundcheck == 0
    with pytest.raises(UnsupportedFormatError):
        db.analyze_soundcheck()


def test_bad_mode_rejected(ipod, tmp_path):
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    with pytest.raises(TrackError, match="soundcheck"):
        Database(ipod).add_track(mp3, soundcheck="loud")


@needs_ffmpeg
def test_database_analyze_soundcheck_only_missing(ipod, tones):
    db = Database(ipod)
    a = db.add_track(tones["loud"], title="Обійми")
    b = db.add_track(tones["quiet"], title="Без бою", soundcheck=777)
    db.save()
    db = Database(ipod)
    seen = []
    changed = db.analyze_soundcheck(progress=lambda done, total, t: seen.append((done, total)))
    assert changed == 1 and seen == [(1, 1)]
    by_title = {t.title: t for t in db.tracks}
    assert by_title["Обійми"].soundcheck == analyze_soundcheck(tones["loud"])
    assert by_title["Без бою"].soundcheck == 777
    assert db.analyze_soundcheck(only_missing=False) == 1  # 777 replaced by the measurement
    db.save()
    assert Database(ipod).tracks[1].soundcheck == analyze_soundcheck(tones["quiet"])
    assert a.title and b.title


@needs_ffmpeg
def test_cli_track_add_soundcheck(ipod, tones, capsys):
    from pygpod.cli import main

    assert main(["-m", ipod, "track", "add", "--soundcheck", "analyze", tones["loud"]]) == 0
    assert Database(ipod).tracks[0].soundcheck == analyze_soundcheck(tones["loud"])
    with pytest.raises(SystemExit):
        main(["-m", ipod, "track", "add", "--soundcheck", "loud", tones["loud"]])
