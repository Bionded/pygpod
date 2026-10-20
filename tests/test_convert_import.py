"""add_track(convert=True): unplayable audio is converted with ffmpeg (syncDirector task 58)."""

import os
import shutil
import subprocess

import pytest

from pygpod import convert
from pygpod.device.models import IpodGeneration
from pygpod.exceptions import TrackError, UnsupportedFormatError
from pygpod.model.database import Database
from pygpod.tags import probe_media

from .test_media_validation import _ipod, _state

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def _make(path, codec, rate=44100, channels=2, title="Пісня"):
    layout = {1: "mono", 2: "stereo", 6: "5.1"}[channels]
    subprocess.run(
        [
            "ffmpeg", "-v", "quiet", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration=1:sample_rate={rate}",
            "-af", f"aformat=channel_layouts={layout}",
            "-c:a", codec,
            "-metadata", f"title={title}", "-metadata", "artist=Океан Ельзи",
            "-metadata", "album=Зоря", "-metadata", "track=3/9",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return str(path)


def _music_files(mp):
    return _state(mp)[0]


def test_flac_becomes_alac_on_a_classic_with_the_original_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(convert.tempfile, "tempdir", str(tmp_path))
    mp = _ipod(tmp_path, "B029")  # Classic
    src = _make(tmp_path / "song.flac", "flac")
    db = Database(mp)
    with pytest.raises(UnsupportedFormatError, match="convert=True"):
        db.add_track(src)
    track = db.add_track(src, convert=True)
    db.save()
    (copied,) = _music_files(mp)
    assert copied.endswith(".m4a")
    assert probe_media(copied).codec == "alac"
    assert (track.title, track.artist, track.album, track.track_number) == (
        "Пісня",
        "Океан Ельзи",
        "Зоря",
        3,
    )
    assert 900 <= track.duration_ms <= 1100  # from the converted file
    assert track.file_size == os.path.getsize(copied)
    assert not [f for f in os.listdir(tmp_path) if f.startswith("pygpod-")]
    reread = Database(mp).tracks
    assert [t.title for t in reread] == ["Пісня"]


def test_lossy_and_unplayable_alac_become_aac(tmp_path):
    mp = _ipod(tmp_path, "8737")  # 2nd generation: no ALAC
    ogg = _make(tmp_path / "a.ogg", "libvorbis", title="Vorbis")
    flac = _make(tmp_path / "b.flac", "flac", title="Lossless")
    db = Database(mp)
    db.add_track(ogg, convert=True)
    db.add_track(flac, convert=True)
    db.save()
    assert sorted(probe_media(f).codec for f in _music_files(mp)) == ["aac", "aac"]


def test_hires_and_surround_are_reduced(tmp_path):
    mp = _ipod(tmp_path, "B029")
    hires = _make(tmp_path / "hires.flac", "flac", rate=96000)
    surround = _make(tmp_path / "surround.flac", "flac", channels=6)
    db = Database(mp)
    db.add_track(hires, convert=True)
    db.add_track(surround, convert=True)
    db.save()
    infos = [probe_media(f) for f in _music_files(mp)]
    assert all(i.codec == "alac" and i.sample_rate <= 48000 and i.channels == 2 for i in infos)


def test_playable_files_are_never_converted(tmp_path):
    mp = _ipod(tmp_path, "B029")
    src = _make(tmp_path / "ok.m4a", "aac")
    db = Database(mp)
    db.add_track(src, convert=True)
    db.save()
    (copied,) = _music_files(mp)
    with open(src, "rb") as a, open(copied, "rb") as b:
        assert a.read() == b.read()


def test_convert_needs_copy_and_ffmpeg(tmp_path, monkeypatch):
    mp = _ipod(tmp_path, "B029")
    src = _make(tmp_path / "song.flac", "flac")
    db = Database(mp)
    with pytest.raises(TrackError, match="copy=True"):
        db.add_track(src, copy=False, convert=True)
    monkeypatch.setenv("PYGPOD_FFMPEG", "")
    monkeypatch.setattr(convert.shutil, "which", lambda _: None)
    with pytest.raises(UnsupportedFormatError, match="needs ffmpeg"):
        db.add_track(src, convert=True)
    assert db.tracks == [] and not db.modified


def test_failed_conversion_leaves_nothing(tmp_path, monkeypatch):
    mp = _ipod(tmp_path, "B029")
    src = tmp_path / "broken.flac"
    src.write_bytes(b"fLaC" + os.urandom(2000))
    monkeypatch.setattr(convert.tempfile, "tempdir", str(tmp_path))
    db = Database(mp)
    with pytest.raises(UnsupportedFormatError, match="conversion failed"):
        db.add_track(str(src), convert=True)
    assert db.tracks == [] and not db.modified
    assert not [f for f in os.listdir(tmp_path) if f.startswith("pygpod-")]


def test_target_codec():
    assert convert.target_codec("flac", IpodGeneration.CLASSIC_3) == "alac"
    assert convert.target_codec("flac", IpodGeneration.SECOND) == "aac"
    assert convert.target_codec("vorbis", IpodGeneration.CLASSIC_3) == "aac"
    assert convert.target_codec("flac", None) == convert.target_codec(
        "flac", IpodGeneration.UNKNOWN
    )
