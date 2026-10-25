"""Length and bitrate of MP3s without a Xing/Info/VBRI header (a frame scan instead of the
first frame's bitrate), and doctor's check of lengths against the gapless sample count."""

import os
import shutil
import subprocess

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.gapless import mp3_stream_info
from pygpod.model.database import Database
from pygpod.tags import read_tags

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
mutagen_mp3 = pytest.importorskip("mutagen.mp3")


def _mp3(path, *args, xing=False):
    """4 s of silence, then 4 s of noise: a VBR encoder's first frames are far below the
    average bitrate."""
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
        + ["aevalsrc=random(0)*0.4*gt(t\\,4)|random(1)*0.4*gt(t\\,4):d=8:s=44100"]
        + ["-c:a", "libmp3lame", *args, "-write_xing", "1" if xing else "0", str(path)],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("mp3len")
    return {
        "vbr": _mp3(d / "Ґанок.mp3", "-q:a", "2"),
        "vbr_xing": _mp3(d / "Ґанок з тегом.mp3", "-q:a", "2", xing=True),
        "cbr": _mp3(d / "Обійми.mp3", "-b:a", "128k"),
    }


def _mutagen(path):
    info = mutagen_mp3.MP3(path).info
    return int(info.length * 1000), info.bitrate // 1000


def test_vbr_without_xing_length_from_the_frames(files):
    tagged_ms, tagged_kbps = _mutagen(files["vbr_xing"])
    assert abs(_mutagen(files["vbr"])[0] - tagged_ms) > 1000  # the first-frame estimate
    tags = read_tags(files["vbr"])
    assert abs(tags["duration_ms"] - tagged_ms) <= 30
    assert abs(tags["bitrate"] - tagged_kbps) <= tagged_kbps * 0.05
    assert tags["samplerate"] == 44100


def test_files_with_xing_and_cbr_unchanged(files):
    tags = read_tags(files["vbr_xing"])
    assert (tags["duration_ms"], tags["bitrate"]) == _mutagen(files["vbr_xing"])
    ms, kbps = _mutagen(files["cbr"])
    tags = read_tags(files["cbr"])
    assert abs(tags["duration_ms"] - ms) <= 1 and tags["bitrate"] == kbps == 128


def test_stream_info_skips_the_xing_frame(files):
    plain, tagged = mp3_stream_info(files["vbr"]), mp3_stream_info(files["vbr_xing"])
    assert plain["duration_ms"] == tagged["duration_ms"]
    assert mp3_stream_info(__file__) is None


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A270012345678\n"
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": sysinfo})
    return mp


def test_estimated_gapless_track_length_agrees_with_samples(ipod, files):
    db = Database(ipod)
    t = db.add_track(files["vbr"], gapless="estimate")
    db.save()
    t = Database(ipod).tracks[0]
    assert abs(t.duration_ms - t.sample_count * 1000 / 44100) < 100
    assert abs(t.duration_ms - _mutagen(files["vbr_xing"])[0]) <= 30


@pytest.mark.libgpod
def test_libgpod_reads_the_length(tool, ipod, files):
    db = Database(ipod)
    db.add_track(files["vbr"])
    db.save()
    out = subprocess.run([tool, "dump-video", ipod], capture_output=True, text=True, check=True)
    [row] = [ln.split("\t") for ln in out.stdout.splitlines() if ln.startswith("V")]
    tracklen, bitrate = int(row[11]), int(row[13])
    assert abs(tracklen - _mutagen(files["vbr_xing"])[0]) <= 30
    assert 50 < bitrate < 300


def test_doctor_reports_and_repairs_a_wrong_length(ipod, files):
    db = Database(ipod)
    t = db.add_track(files["vbr"], gapless="estimate")
    good = t.duration_ms
    t._set_field_and_header_u32("tracklen", 0x28, 1173)  # what the first frame suggested
    db.save()
    report = Database(ipod, recover=False).check()
    (issue,) = report.by_kind("length_mismatch")
    assert (issue.severity, issue.fixable) == ("warning", True)
    assert "length 1173 ms" in issue.message
    db = Database(ipod)
    actions = db.repair(report)
    assert [a.done for a in actions if a.kind == "length_mismatch"] == [True]
    assert Database(ipod).tracks[0].duration_ms == good
    assert not Database(ipod, recover=False).check().by_kind("length_mismatch")


def _decoded_ms(path):
    """Length of what ffmpeg decodes (it skips damaged frames)."""
    pcm = subprocess.run(
        ["ffmpeg", "-loglevel", "quiet", "-i", path, "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return len(pcm) // 2 * 1000 / 44100


def _variant(tmp_path, src, name, change):
    with open(src, "rb") as f:
        data = f.read()
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(change(data))
    return path


def test_damaged_spot_in_the_middle_is_skipped(files, tmp_path):
    def garbage(data):
        mid = len(data) // 2
        return data[:mid] + b"\0" * 333 + data[mid:]

    path = _variant(tmp_path, files["vbr"], "Карпати.mp3", garbage)
    assert abs(mp3_stream_info(path)["duration_ms"] - _decoded_ms(path)) <= 50
    assert abs(read_tags(path)["duration_ms"] - _decoded_ms(files["vbr"])) <= 50


@pytest.mark.parametrize(
    "name, change",
    [
        ("truncated", lambda d: d[:-100]),
        ("id3v1", lambda d: d + b"TAG" + "Обійми".encode("latin-1", "replace").ljust(125, b"\0")),
        ("ape", lambda d: d + b"APETAGEX" + b"\xff\xfb\x90\x64" * 20 + b"\0" * 24),
        ("garbage_end", lambda d: d + b"\xff\xfb" + b"\0" * 300),
    ],
)
def test_end_of_stream_unchanged(files, tmp_path, name, change):
    plain = mp3_stream_info(files["vbr"])
    info = mp3_stream_info(_variant(tmp_path, files["vbr"], name + ".mp3", change))
    if name == "truncated":  # the cut frame still counts, as before
        assert info["duration_ms"] == plain["duration_ms"]
    else:
        assert info == plain


def test_gapless_sample_count_skips_a_damaged_spot(tmp_path):
    from pygpod.gapless import read_mp3_gapless

    src = _mp3(tmp_path / "Ґанок.mp3", "-q:a", "2", xing=True)
    exact = read_mp3_gapless(src)

    def garbage(data):
        mid = len(data) // 2
        return data[:mid] + b"\0" * 333 + data[mid:]

    damaged = read_mp3_gapless(_variant(tmp_path, src, "Обійми.mp3", garbage))
    assert abs(damaged["sample_count"] - exact["sample_count"]) <= 1152
