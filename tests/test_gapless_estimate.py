"""Estimated gapless data for MP3s without a LAME/Info tag (experimental, opt-in):
gapless='estimate' / 'auto', pygpod.gapless.estimate_mp3_gapless. Fixtures need ffmpeg."""

import os
import shutil
import subprocess

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import TrackError
from pygpod.gapless import estimate_mp3_gapless, read_mp3_gapless
from pygpod.model.database import Database

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SECONDS = 2.5


def _mp3(path, rate=44100, args=("-b:a", "128k"), xing=False):
    """A sine of SECONDS at `rate`, encoded by LAME (via ffmpeg) with or without the Xing/
    LAME info frame."""
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi"]
        + ["-i", f"sine=f=440:d={SECONDS},volume=-6dB", "-ar", str(rate), "-c:a", "libmp3lame"]
        + list(args)
        + ["-write_xing", "1" if xing else "0", path],
        check=True,
    )
    return path


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A270012345678\n"
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": sysinfo})
    return mp


@needs_ffmpeg
@pytest.mark.parametrize(
    "rate, args",
    [(44100, ("-b:a", "128k")), (44100, ("-q:a", "2")), (22050, ("-b:a", "64k"))],
    ids=["cbr", "vbr", "mpeg2"],
)
def test_estimate_within_one_frame(tmp_path, rate, args):
    untagged = _mp3(str(tmp_path / "Без тегу.mp3"), rate, args)
    tagged = _mp3(str(tmp_path / "З тегом.mp3"), rate, args, xing=True)
    exact = read_mp3_gapless(tagged)
    est = estimate_mp3_gapless(untagged)
    spf = 1152 if rate > 24000 else 576
    true_samples = int(SECONDS * rate)
    assert abs(est["sample_count"] - true_samples) <= spf
    assert est["pregap"] == exact["pregap"] == 576
    assert abs(est["postgap"] - exact["postgap"]) <= spf
    assert est["gapless_data"] > 0 and est["gapless_track_flag"] == 1


@needs_ffmpeg
def test_tagged_and_non_mp3_files_are_not_estimated(tmp_path):
    tagged = _mp3(str(tmp_path / "Обійми.mp3"), xing=True)
    assert read_mp3_gapless(tagged) and estimate_mp3_gapless(tagged) is None
    m4a = str(tmp_path / "Ґанок.m4a")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", m4a], check=True
    )
    assert estimate_mp3_gapless(m4a) is None


@needs_ffmpeg
def test_without_ffmpeg_postgap_zero_and_no_flag(tmp_path, monkeypatch):
    untagged = _mp3(str(tmp_path / "a.mp3"))
    complete = estimate_mp3_gapless(untagged)
    monkeypatch.setenv("PYGPOD_FFMPEG", str(tmp_path / "nonexistent"))
    est = estimate_mp3_gapless(untagged)
    assert est["postgap"] == 0 and "gapless_track_flag" not in est
    assert est["sample_count"] == complete["sample_count"] + complete["postgap"]
    assert est["gapless_data"] == complete["gapless_data"]
    assert estimate_mp3_gapless(untagged, use_ffmpeg=False) == est


@needs_ffmpeg
def test_add_track_modes(ipod, tmp_path):
    untagged = _mp3(str(tmp_path / "Без тегу.mp3"))
    tagged = _mp3(str(tmp_path / "З тегом.mp3"), xing=True)
    db = Database(ipod)
    default = db.add_track(untagged)
    assert (default.sample_count, default.pregap, default.gapless_track_flag) == (0, 0, False)
    est = estimate_mp3_gapless(untagged)
    for mode in ("estimate", "auto"):
        t = db.add_track(untagged, gapless=mode)
        assert (t.pregap, t.postgap, t.sample_count, t.gapless_data) == (
            est["pregap"],
            est["postgap"],
            est["sample_count"],
            est["gapless_data"],
        )
        assert t.gapless_track_flag
        exact = db.add_track(tagged, gapless=mode)  # a valid LAME tag always wins
        assert exact.sample_count == read_mp3_gapless(tagged)["sample_count"]
    explicit = db.add_track(untagged, gapless={"pregap": 1, "sample_count": 2})
    assert (explicit.pregap, explicit.sample_count) == (1, 2)
    db.save()
    reopened = Database(ipod).tracks
    assert reopened[1].sample_count == est["sample_count"]


def test_add_track_rejects_bad_values(ipod, tmp_path):
    from .conftest import _make_minimal_mp3

    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(ipod)
    with pytest.raises(TrackError, match="gapless"):
        db.add_track(mp3, gapless="guess")
    with pytest.raises(TrackError, match="gapless"):
        db.add_track(mp3, gapless={"pregapp": 1})
    assert not db.tracks
