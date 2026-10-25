"""add_track(convert=True) for video: out-of-profile video becomes iPod H.264 Baseline."""

import json
import os
import shutil
import subprocess

import pytest

from pygpod import convert
from pygpod.db.constants import MEDIATYPE_VIDEO
from pygpod.device.models import IpodGeneration
from pygpod.exceptions import UnsupportedFormatError
from pygpod.model.database import Database

from .test_media_validation import _ipod, _state

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe needed"
)


def _make(path, size="640x360", rate=25, vcodec=("libx264", "-profile:v", "high"), acodec="aac"):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1", "-c:v", *vcodec, "-pix_fmt", "yuv420p", "-c:a", acodec, "-ac", "2",
            "-metadata", "title=Кліп", str(path),
        ],
        check=True,
        timeout=120,
    )  # fmt: skip
    return str(path)


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    streams = json.loads(out)["streams"]
    v = [s for s in streams if s["codec_type"] == "video"]
    a = [s for s in streams if s["codec_type"] == "audio"]
    return v[0], a[0]


def _top_atoms(path):
    atoms, pos = [], 0
    with open(path, "rb") as f:
        data = f.read()
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos : pos + 4], "big")
        atoms.append(data[pos + 4 : pos + 8])
        if size < 8:
            break
        pos += size
    return atoms


def _copied(mp):
    (path,) = _state(mp)[0]
    return path


def test_high_profile_video_becomes_small_baseline_m4v(tmp_path, monkeypatch):
    monkeypatch.setattr(convert.tempfile, "tempdir", str(tmp_path))
    mp = _ipod(tmp_path, "B029")  # Classic
    src = _make(tmp_path / "clip.mp4", rate=60)
    db = Database(mp)
    track = db.add_track(src, convert=True)
    db.save()
    out = _copied(mp)
    assert out.endswith(".m4v")
    v, a = _streams(out)
    assert v["codec_name"] == "h264" and "Baseline" in v["profile"] and v["level"] <= 13
    assert (v["width"], v["height"]) == (320, 180)  # 16:9 kept, inside 320x240, even
    assert v["pix_fmt"] == "yuv420p" and v["avg_frame_rate"] == "30/1"
    assert (a["codec_name"], a["profile"], a["channels"]) == ("aac", "LC", 2)
    assert a["sample_rate"] == "48000"
    atoms = _top_atoms(out)
    assert atoms.index(b"moov") < atoms.index(b"mdat")  # +faststart
    assert convert.video_problems(convert.probe_video(out), convert.SMALL_VIDEO, out) == []
    assert (track.title, track.media_type, track.movie_flag) == ("Кліп", MEDIATYPE_VIDEO, True)
    assert track.file_size == os.path.getsize(out)
    assert 900 <= track.duration_ms <= 1200
    assert not [f for f in os.listdir(tmp_path) if f.startswith("pygpod-")]


def test_large_size_on_a_classic_and_small_on_a_5g(tmp_path):
    src = _make(tmp_path / "clip.mp4", size="1280x720")
    mp = _ipod(tmp_path / "classic", "B029")
    Database(mp).add_track(src, convert=True, video_size="large")
    v, _ = _streams(_copied(mp))
    assert (v["width"], v["height"], v["level"]) == (640, 360, 30)
    assert convert.video_profile(IpodGeneration.VIDEO_1) is convert.SMALL_VIDEO
    out = convert.convert_video_for_ipod(src, IpodGeneration.VIDEO_1, size="large")
    try:
        v, _ = _streams(out)
        assert (v["width"], v["height"], v["level"]) == (320, 180, 13)
    finally:
        os.unlink(out)


def test_video_in_profile_is_copied_byte_identical(tmp_path):
    mp = _ipod(tmp_path, "B029")
    small = _make(tmp_path / "ok.m4v", "320x240", 15, ("libx264", "-profile:v", "baseline"))
    db = Database(mp)
    db.add_track(small, convert=True)
    with open(small, "rb") as f, open(_copied(mp), "rb") as g:
        assert f.read() == g.read()


def test_no_upscaling_and_odd_sizes_become_even(tmp_path):
    src = _make(tmp_path / "tiny.mp4", "161x121", vcodec=("mpeg4",))
    out = convert.convert_video_for_ipod(src, IpodGeneration.CLASSIC_1)
    try:
        v, _ = _streams(out)
        assert (v["width"], v["height"]) == (160, 120)
    finally:
        os.unlink(out)


def test_mkv_with_opus_becomes_a_video_track(tmp_path):
    mp = _ipod(tmp_path, "B029")
    src = _make(tmp_path / "clip.mkv", acodec="libopus")
    track = Database(mp).add_track(src, convert=True)
    out = _copied(mp)
    assert out.endswith(".m4v")
    v, a = _streams(out)
    assert "Baseline" in v["profile"] and a["codec_name"] == "aac"
    assert track.media_type == MEDIATYPE_VIDEO and track.movie_flag


def test_without_convert_video_is_copied_as_before(tmp_path):
    mp = _ipod(tmp_path, "B029")
    src = _make(tmp_path / "clip.mp4")
    Database(mp).add_track(src)
    with open(src, "rb") as f, open(_copied(mp), "rb") as g:
        assert f.read() == g.read()


def test_non_video_ipod_refuses_video(tmp_path):
    src = _make(tmp_path / "clip.mp4")
    with pytest.raises(UnsupportedFormatError, match="does not play video"):
        convert.convert_video_for_ipod(src, IpodGeneration.NANO_2)
    with pytest.raises(ValueError):
        convert.convert_video_for_ipod(src, IpodGeneration.CLASSIC_1, size="huge")


def test_missing_ffmpeg_raises_a_clear_error(tmp_path, monkeypatch):
    src = _make(tmp_path / "clip.mp4")
    monkeypatch.setenv("PYGPOD_FFMPEG", "")
    monkeypatch.setattr(convert.shutil, "which", lambda name: None)
    with pytest.raises(UnsupportedFormatError, match="needs ffmpeg"):
        convert.convert_video_for_ipod(src, IpodGeneration.CLASSIC_1)


def test_missing_ffprobe_copies_unchecked(tmp_path, monkeypatch):
    mp = _ipod(tmp_path, "B029")
    src = _make(tmp_path / "clip.mp4")
    monkeypatch.setattr(convert, "find_ffprobe", lambda: None)
    assert convert.probe_video(src) is None
    Database(mp).add_track(src, convert=True)
    with open(src, "rb") as f, open(_copied(mp), "rb") as g:
        assert f.read() == g.read()


def test_video_problems_lists_every_reason():
    probe = {
        "format": "matroska,webm",
        "video": {
            "codec_name": "h264",
            "profile": "High",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p10le",
            "avg_frame_rate": "60000/1001",
            "bit_rate": "5000000",
        },
        "audio": {"codec_name": "opus", "channels": 6, "sample_rate": "96000"},
    }
    problems = convert.video_problems(probe, convert.SMALL_VIDEO, "x.mkv")
    assert len(problems) == 10, problems
    ok = {
        "format": "mov,mp4,m4a,3gp,3g2,mj2",
        "video": {
            "codec_name": "h264",
            "profile": "Constrained Baseline",
            "level": 30,
            "width": 640,
            "height": 480,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "30/1",
            "bit_rate": "1400000",
        },
        "audio": {"codec_name": "aac", "profile": "LC", "channels": 2, "sample_rate": "48000"},
    }
    assert convert.video_problems(ok, convert.LARGE_VIDEO, "x.m4v") == []
    assert convert.video_problems(ok, convert.SMALL_VIDEO, "x.m4v") == [
        "H.264 level 3",
        "640x480 > 320x240",
        "video bitrate 1400 kbps",
    ]


def test_flac_with_cover_picture_stays_audio(tmp_path):
    from .conftest import _make_minimal_png

    png = str(tmp_path / "cover.png")
    _make_minimal_png(png, 64, 64)
    src = str(tmp_path / "song.flac")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-i", png,
            "-map", "0:a", "-map", "1:v", "-c:a", "flac", "-c:v", "png",
            "-disposition:v", "attached_pic", src,
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    assert convert.probe_video(src)["video"] is None
    mp = _ipod(tmp_path, "B029")
    track = Database(mp).add_track(src, convert=True)
    assert _copied(mp).endswith(".m4a") and not track.movie_flag


def _cp1252_locale(monkeypatch):
    """subprocess text mode decodes with cp1252, the locale default of a Western Windows."""
    real_run = subprocess.run

    def run(cmd, **kwargs):
        if kwargs.get("text") or kwargs.get("universal_newlines"):
            kwargs.setdefault("encoding", "cp1252")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(convert.subprocess, "run", run)


def test_probe_video_decodes_utf8_output_whatever_the_locale(tmp_path, monkeypatch):
    # 'Ґ' is D2 90 in UTF-8; 0x90 has no cp1252 character, so a locale decode fails
    src = str(tmp_path / "Ґанок.mp4")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-metadata", "title=Ґанок", src,
        ],
        check=True,
        timeout=120,
    )  # fmt: skip
    _cp1252_locale(monkeypatch)
    probe = convert.probe_video(src)
    assert probe["video"]["codec_name"] == "h264"
    mp = _ipod(tmp_path, "B029")
    track = Database(mp).add_track(src, convert=True)
    assert track.title == "Ґанок" and track.movie_flag


def test_probe_video_without_output_returns_none(tmp_path, monkeypatch):
    src = _make(tmp_path / "clip.mp4")
    done = subprocess.CompletedProcess([], 0, stdout=None, stderr=None)
    monkeypatch.setattr(convert.subprocess, "run", lambda cmd, **kwargs: done)
    assert convert.probe_video(src) is None


def _mjpeg_avi(path, audio=True):
    """1 s 64x48 Motion-JPEG AVI (5 frames), with PCM audio: a video, not cover art."""
    args = ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=5"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-c:a", "pcm_s16le"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args, "-t", "1", "-c:v", "mjpeg",
         "-pix_fmt", "yuvj420p", str(path)],
        check=True,
        timeout=60,
    )  # fmt: skip
    return str(path)


def test_motion_jpeg_avi_becomes_a_video_track(tmp_path):
    src = _mjpeg_avi(tmp_path / "camera.avi")
    probe = convert.probe_video(src)
    assert probe["video"]["codec_name"] == "mjpeg"
    assert probe["video"]["disposition"]["attached_pic"] == 0
    mp = _ipod(tmp_path, "B029")
    track = Database(mp).add_track(src, convert=True)
    out = _copied(mp)
    assert out.endswith(".m4v")
    v, a = _streams(out)
    assert v["codec_name"] == "h264" and "Baseline" in v["profile"]
    assert (v["width"], v["height"]) == (64, 48)
    assert a["codec_name"] == "aac"
    assert track.media_type == MEDIATYPE_VIDEO and track.movie_flag


def test_motion_jpeg_video_on_an_ipod_without_video_keeps_its_audio_with_a_warning(
    tmp_path, caplog
):
    # like any other non-MP4 video there: the soundtrack is converted, but not silently
    src = _mjpeg_avi(tmp_path / "camera.avi")
    mp = _ipod(tmp_path, "8737")
    with caplog.at_level("WARNING", logger="pygpod.convert"):
        track = Database(mp).add_track(src, convert=True)
    assert "does not play video" in caplog.text
    assert _copied(mp).endswith(".m4a") and not track.movie_flag
    silent = _mjpeg_avi(tmp_path / "silent.avi", audio=False)
    with pytest.raises(UnsupportedFormatError, match="conversion failed"):
        convert.convert_for_ipod(silent, IpodGeneration.NANO_2)


def _streams_all(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)["streams"]


def _with_cover(dest, audio_args, extra=()):
    from .conftest import _make_minimal_png

    png = os.path.join(os.path.dirname(dest), "cover.png")
    _make_minimal_png(png, 64, 64)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-i", png, "-map", "0:a", "-map", "1:v", *audio_args, "-c:v", "mjpeg",
         "-disposition:v", "attached_pic", *extra, dest],
        check=True,
        timeout=60,
    )  # fmt: skip
    return dest


def test_mp3_and_m4a_with_a_jpeg_cover_still_convert_as_audio(tmp_path):
    mp3 = _with_cover(str(tmp_path / "song.mp3"), ["-c:a", "libmp3lame"], ["-id3v2_version", "3"])
    m4a = _with_cover(str(tmp_path / "song.m4a"), ["-c:a", "aac"])
    for src in (mp3, m4a):
        cover = [s for s in _streams_all(src) if s["codec_type"] == "video"]
        assert [s["codec_name"] for s in cover] == ["mjpeg"], src
        probe = convert.probe_video(src)
        assert probe["video"] is None and probe["audio"] is not None, src
        out = convert.convert_for_ipod(src, IpodGeneration.CLASSIC_1)
        try:
            assert out.endswith(".m4a")
            assert [s["codec_type"] for s in _streams_all(out)] == ["audio"]
        finally:
            os.unlink(out)
    mp = _ipod(tmp_path, "B029")
    track = Database(mp).add_track(mp3, convert=True)
    assert _copied(mp).endswith(".mp3") and not track.movie_flag


def test_chapter_pictures_of_an_audiobook_are_not_video(tmp_path):
    from .test_video_tracks import _add_chapter_reference, _mjpeg_with_audio

    plain = _mjpeg_with_audio(tmp_path / "plain.m4b", 3, "mp4")
    book = _add_chapter_reference(plain, str(tmp_path / "book.m4b"))
    probe = convert.probe_video(book)
    assert probe["video"] is None and probe["audio"]["codec_name"] == "aac"
    single = _mjpeg_with_audio(tmp_path / "single.m4b", 1, "mp4")  # one picture, no reference
    assert convert.probe_video(single)["video"] is None
    out = convert.convert_for_ipod(book, IpodGeneration.CLASSIC_1)
    try:
        assert out.endswith(".m4a")
    finally:
        os.unlink(out)


def test_which_video_streams_are_pictures():
    assert convert._is_picture({"codec_name": "h264", "disposition": {"attached_pic": 1}})
    assert convert._is_picture({"codec_name": "mjpeg", "disposition": {"timed_thumbnails": 1}})
    assert convert._is_picture({"codec_name": "png", "nb_frames": "1"})
    assert convert._is_picture({"codec_name": "mjpeg", "nb_frames": "0"})
    assert not convert._is_picture({"codec_name": "mjpeg", "nb_frames": "25"})
    assert not convert._is_picture({"codec_name": "mjpeg"})  # frame count unknown: video
    assert not convert._is_picture({"codec_name": "h264", "nb_frames": "1"})
