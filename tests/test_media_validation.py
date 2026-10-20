"""add_track validates the file's content for the iPod generation before copying anything."""

import os
import shutil
import struct
import subprocess
import wave

import pytest

from pygpod.device.models import IpodGeneration, supported_audio_codecs
from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import UnsupportedFormatError
from pygpod.model.database import Database
from pygpod.tags import probe_media, validate_media

from .conftest import _make_minimal_mp3


def _flac(path, sample_rate=44100):
    """A FLAC stream header (STREAMINFO only) - enough for mutagen to identify FLAC."""
    info = bytearray(34)
    struct.pack_into(">HH", info, 0, 4096, 4096)
    packed = (sample_rate << 44) | ((2 - 1) << 41) | ((16 - 1) << 36) | 1000
    info[10:18] = packed.to_bytes(8, "big")
    with open(path, "wb") as f:
        f.write(b"fLaC" + bytes([0x80, 0, 0, 34]) + bytes(info) + b"\x00" * 64)


def _wav(path, rate=44100, channels=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * 100)


def _ipod(tmp_path, model):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = f"ModelNumStr: x{model}\nFirewireGuid: 0x000A2700213749FF\n"
    init_ipod(mp, sysinfo_files={"SysInfo": sysinfo.encode()})
    return mp


def _state(mp):
    files = sorted(
        os.path.join(d, f)
        for d, _, fs in os.walk(os.path.join(mp, "iPod_Control", "Music"))
        for f in fs
    )
    return files, len(Database(mp).tracks)


def test_probe_identifies_content(tmp_path):
    mp3 = tmp_path / "a.mp3"
    _make_minimal_mp3(str(mp3))
    assert probe_media(str(mp3)).codec == "mp3"
    _flac(tmp_path / "b.flac")
    assert probe_media(str(tmp_path / "b.flac")).codec == "flac"
    _wav(tmp_path / "c.wav", 96000, 6)
    info = probe_media(str(tmp_path / "c.wav"))
    assert (info.codec, info.sample_rate, info.channels) == ("wav", 96000, 6)


@pytest.mark.parametrize(
    "make,name,match",
    [
        (lambda p: _flac(p), "disguised.mp3", "FLAC"),
        (lambda p: _wav(p, 96000), "hires.wav", "sample rate"),
        (lambda p: _wav(p, 44100, 6), "surround.wav", "channels"),
        (lambda p: p.write_bytes(os.urandom(4000)), "noise.mp3", "not a recognised"),
    ],
)
def test_rejected_before_copy_and_db(tmp_path, make, name, match):
    mp = _ipod(tmp_path, "B029")  # Classic
    src = tmp_path / name
    make(src)
    before = _state(mp)
    db = Database(mp)
    with pytest.raises(UnsupportedFormatError, match=match):
        db.add_track(str(src))
    assert db.tracks == [] and not db.modified
    db.save()
    assert _state(mp) == before


def test_alac_only_where_the_firmware_plays_it(tmp_path):
    assert "alac" in supported_audio_codecs(IpodGeneration.CLASSIC_3)
    assert "alac" in supported_audio_codecs(IpodGeneration.MINI_1)
    assert "alac" not in supported_audio_codecs(IpodGeneration.SECOND)
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to make an ALAC file")
    m4a = tmp_path / "lossless.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=0.2",
            "-c:a",
            "alac",
            str(m4a),
        ],
        check=True,
    )
    assert probe_media(str(m4a)).codec == "alac"
    validate_media(str(m4a), IpodGeneration.CLASSIC_3)
    with pytest.raises(UnsupportedFormatError, match="Apple Lossless"):
        validate_media(str(m4a), IpodGeneration.SECOND)


def test_video_only_on_video_ipods(tmp_path):
    clip = tmp_path / "clip.m4v"
    clip.write_bytes(os.urandom(100))
    validate_media(str(clip), IpodGeneration.CLASSIC_3)
    with pytest.raises(UnsupportedFormatError, match="video"):
        validate_media(str(clip), IpodGeneration.MINI_1)


def test_valid_file_added(tmp_path):
    mp = _ipod(tmp_path, "B029")
    src = tmp_path / "ok.wav"
    _wav(src)
    db = Database(mp)
    db.add_track(str(src), title="ok")
    db.save()
    assert [t.title for t in Database(mp).tracks] == ["ok"]
