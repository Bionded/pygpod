"""Gapless playback data from LAME tags and iTunSMPB / edit lists."""

import os
import shutil
import subprocess

import pytest
from mutagen.mp4 import MP4, MP4FreeForm

from pygpod.db.parser import parse_itunesdb
from pygpod.db.writer import write_itunesdb
from pygpod.gapless import _crc16, estimate_gapless, read_gapless
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3
from .test_media_validation import _ipod

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
SECONDS, RATE = 2, 44100
FIELDS = ("pregap", "sample_count", "postgap", "gapless_data", "gapless_track_flag")


def _encode(path, *codec):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={SECONDS}:sample_rate={RATE}",
         "-ac", "2", *codec, str(path)],
        check=True,
        timeout=60,
    )  # fmt: skip
    return str(path)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("gapless")
    out = {
        "vbr": _encode(d / "vbr.mp3", "-c:a", "libmp3lame", "-q:a", "4"),
        "cbr": _encode(d / "cbr.mp3", "-c:a", "libmp3lame", "-b:a", "128k"),
        "noxing": _encode(d / "noxing.mp3", "-c:a", "libmp3lame", "-write_xing", "0"),
        "aac": _encode(d / "aac.m4a", "-c:a", "aac"),
        "wav": _encode(d / "a.wav", "-c:a", "pcm_s16le"),
        "flac": _encode(d / "a.flac", "-c:a", "flac"),
        "alac": _encode(d / "alac.m4a", "-c:a", "alac"),  # edit list from 0: no priming
        "alac_noelst": _encode(d / "alac2.m4a", "-c:a", "alac", "-use_editlist", "0"),
        "aac_noelst": _encode(d / "aac2.m4a", "-c:a", "aac", "-use_editlist", "0"),
    }
    smpb = str(d / "smpb.m4a")
    shutil.copyfile(out["aac"], smpb)
    f = MP4(smpb)
    f["----:com.apple.iTunes:iTunSMPB"] = [
        MP4FreeForm(b" 00000000 00000840 000001CA 0000000000015888 00000000 00000000")
    ]
    f["pgap"] = True
    f.save()
    out["smpb"] = smpb
    return out


def _mp3_frames(path):
    """(Xing frame offset, [frame lengths]) by an independent, simple walk."""
    data = open(path, "rb").read()
    size = data[6] << 21 | data[7] << 14 | data[8] << 7 | data[9]
    pos = 10 + size
    while data[pos] != 0xFF:
        pos += 1
    start, lengths = pos, []
    rates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    while pos + 4 <= len(data) and data[pos] == 0xFF and data[pos + 1] & 0xE0 == 0xE0:
        n = 144000 * rates[data[pos + 2] >> 4] // RATE + (data[pos + 2] >> 1 & 1)
        lengths.append(n)
        pos += n
    return start, lengths


def test_vbr_mp3_matches_the_lame_tag_and_frame_count(files):
    g = read_gapless(files["vbr"])
    start, lengths = _mp3_frames(files["vbr"])
    music = len(lengths) - 1  # without the Xing frame
    assert (g["pregap"], g["postgap"]) == (576, 1080)  # ffmpeg/libmp3lame delay and padding
    assert g["sample_count"] == music * 1152 - 576 - 1080 == SECONDS * RATE
    assert g["gapless_data"] == sum(lengths) - sum(lengths[-8:])
    assert g["gapless_track_flag"] == 1 and "gapless_album_flag" not in g


def test_cbr_mp3_counts_one_frame_more_like_itunes(files):
    g = read_gapless(files["cbr"])
    assert g["sample_count"] == SECONDS * RATE + 1152


@pytest.mark.parametrize(
    "rate,channels,codec,extra_frame",
    [
        (44100, 1, ("-b:a", "64k"), True),  # MPEG-1 mono
        (22050, 2, ("-b:a", "64k"), True),  # MPEG-2
        # 104-byte frames: ffmpeg writes the Info frame at a higher bitrate, so the file does not
        # count as CBR (gtkpod's rule) and gets no extra frame
        (22050, 1, ("-b:a", "32k"), False),
        (22050, 1, ("-q:a", "4"), False),  # MPEG-2 mono VBR
        (11025, 2, ("-b:a", "32k"), True),  # MPEG-2.5
    ],
)
def test_ffmpeg_mono_and_mpeg2_info_tags(tmp_path, rate, channels, codec, extra_frame):
    """Ffmpeg computes the tag CRC with the CRC field zero; when that field lies inside
    the 190-byte span (all but MPEG-1 stereo) the tag is accepted too."""
    path = tmp_path / "a.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={SECONDS}:sample_rate={rate}",
         "-ac", str(channels), "-c:a", "libmp3lame", *codec, str(path)],
        check=True,
        timeout=60,
    )  # fmt: skip
    g = read_gapless(str(path))
    assert g is not None
    per_frame = 1152 if rate == 44100 else 576
    assert g["pregap"] == 576 and g["postgap"] > 0
    assert g["sample_count"] == SECONDS * rate + per_frame * extra_frame  # CBR: +1 frame (iTunes)
    data = bytearray(path.read_bytes())
    i = data.find(b"Lavc")
    data[i + 0x15] ^= 0x10  # change the delay, keep the stored CRC
    path.write_bytes(bytes(data))
    assert read_gapless(str(path)) is None


def test_bad_lame_crc_and_files_without_data_give_nothing(files, tmp_path):
    data = bytearray(open(files["vbr"], "rb").read())
    i = data.find(b"Lavc")
    data[i + 0x15] ^= 0x10  # change the delay, keep the stored CRC
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(bytes(data))
    assert read_gapless(str(bad)) is None
    for name in ("noxing", "wav", "flac"):
        assert read_gapless(files[name]) is None, name
    plain = str(tmp_path / "plain.mp3")
    _make_minimal_mp3(plain)
    assert read_gapless(plain) is None
    (tmp_path / "empty.mp3").write_bytes(b"")
    assert read_gapless(str(tmp_path / "empty.mp3")) is None


def test_aac_edit_list_and_itunsmpb(files):
    g = read_gapless(files["aac"])
    packets = -(-(SECONDS * RATE + 1024) // 1024)
    assert (g["pregap"], g["sample_count"]) == (1024, SECONDS * RATE)
    assert g["postgap"] == packets * 1024 - 1024 - SECONDS * RATE
    assert (g["gapless_data"], g["gapless_track_flag"]) == (0, 1)
    s = read_gapless(files["smpb"])
    assert (s["pregap"], s["postgap"], s["sample_count"]) == (0x840, 0x1CA, 0x15888)
    assert s["gapless_album_flag"] == 1


@pytest.mark.parametrize("name", ["alac", "alac_noelst"])
def test_alac_without_itunsmpb_has_no_delay_and_its_full_length(files, name):
    g = read_gapless(files[name])
    assert (g["pregap"], g["sample_count"], g["postgap"]) == (0, SECONDS * RATE, 0)
    assert (g["gapless_data"], g["gapless_track_flag"]) == (0, 1)
    assert estimate_gapless(files[name]) is None  # exact data: nothing to estimate


def test_aac_without_itunsmpb_or_edit_list_only_estimated(files, tmp_path):
    assert read_gapless(files["aac_noelst"]) is None
    g = estimate_gapless(files["aac_noelst"])
    packets = -(-(SECONDS * RATE + 1024) // 1024)
    assert (g["pregap"], g["sample_count"]) == (1024, SECONDS * RATE)  # ffmpeg's AAC encoder
    assert g["postgap"] == packets * 1024 - 1024 - SECONDS * RATE and g["gapless_track_flag"]
    other = str(tmp_path / "other.m4a")
    shutil.copyfile(files["aac_noelst"], other)
    f = MP4(other)
    del f["\xa9too"]  # not written by ffmpeg: Apple's 2112 samples assumed
    f.save()
    assert estimate_gapless(other)["pregap"] == 2112
    assert estimate_gapless(files["aac"]) is None and estimate_gapless(files["wav"]) is None


def _aac_of_length(path, rate, samples):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:sample_rate={rate}",
         "-af", f"atrim=end_sample={samples}", "-ac", "2", "-c:a", "aac", str(path)],
        check=True,
        timeout=60,
    )  # fmt: skip
    return str(path)


@pytest.mark.parametrize("rate,samples", [(44100, 88323), (48000, 96011)])
def test_aac_edit_list_sample_count_is_exact_for_odd_lengths(tmp_path, rate, samples):
    # the edit list's segment is in ms: 88323 samples are 2002.8 ms, rounded to 2003 ms
    g = read_gapless(_aac_of_length(tmp_path / "odd.m4a", rate, samples))
    packets = -(-(samples + 1024) // 1024)
    assert (g["pregap"], g["sample_count"]) == (1024, samples)
    assert g["postgap"] == packets * 1024 - 1024 - samples


def test_crc16_is_crc16_arc():
    assert _crc16(b"123456789") == 0xBB3D


def _db(tmp_path):
    return _ipod(tmp_path, "B029")


def test_add_track_save_reopen_and_explicit_values_win(files, tmp_path):
    mp = _db(tmp_path)
    db = Database(mp)
    vbr = db.add_track(files["vbr"], title="vbr")
    aac = db.add_track(files["smpb"], title="aac")
    wav = db.add_track(files["wav"], title="wav")
    mine = db.add_track(files["vbr"], title="mine", pregap=1, gapless_album_flag=True)
    db.save()
    db = Database(mp)
    t = {x.title: x for x in db.tracks}
    expect = read_gapless(files["vbr"])
    assert tuple(getattr(t["vbr"], k) for k in FIELDS) == tuple(expect[k] for k in FIELDS)
    assert t["aac"].gapless_album_flag and t["aac"].pregap == 0x840
    assert all(not getattr(t["wav"], k) for k in FIELDS + ("gapless_album_flag",))
    assert t["mine"].pregap == 1 and t["mine"].gapless_album_flag
    assert t["mine"].sample_count == expect["sample_count"]
    w = t["wav"]
    w.pregap, w.postgap, w.sample_count = 10, 20, 1 << 40
    w.gapless_data, w.gapless_track_flag, w.gapless_album_flag = 30, True, True
    with pytest.raises(ValueError):
        w.pregap = -1
    db.save()
    w = Database(mp).get_track(wav.track_id)
    assert (w.pregap, w.postgap, w.sample_count, w.gapless_data) == (10, 20, 1 << 40, 30)
    assert w.gapless_track_flag and w.gapless_album_flag
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        data = f.read()
    assert write_itunesdb(parse_itunesdb(data)) == data
    assert {vbr.track_id, aac.track_id, mine.track_id} <= {x.track_id for x in db.tracks}


def test_converted_file_gets_its_own_gapless_data(files, tmp_path):
    mp = _ipod(tmp_path, "8737")  # 2nd generation: no ALAC -> AAC
    t = Database(mp).add_track(files["flac"], convert=True)
    assert t.gapless_track_flag and t.pregap > 0
    assert abs(t.sample_count - SECONDS * RATE) <= 1


def test_flac_converted_to_alac_gets_its_sample_count(files, tmp_path):
    mp = _db(tmp_path)  # Classic: plays ALAC
    db = Database(mp)
    t = db.add_track(files["flac"], title="Зимородок", convert=True)
    assert t.ipod_path.lower().endswith(".m4a")
    assert (t.pregap, t.sample_count, t.postgap, t.gapless_track_flag) == (0, SECONDS * RATE, 0, 1)
    e = db.add_track(files["aac_noelst"], title="Обійми", gapless="estimate")
    assert (e.pregap, e.sample_count) == (1024, SECONDS * RATE)
    assert db.add_track(files["aac_noelst"], title="Ґанок").sample_count == 0  # default: none
    db.save()
    with open(os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        data = f.read()
    assert write_itunesdb(parse_itunesdb(data)) == data
    assert Database(mp).get_track(t.track_id).sample_count == SECONDS * RATE


@pytest.mark.libgpod
def test_libgpod_reads_the_gapless_fields(tool, files, tmp_path):
    mp = _db(tmp_path)
    db = Database(mp)
    db.add_track(files["vbr"], title="vbr")
    db.add_track(files["smpb"], title="aac")
    db.add_track(files["wav"], title="wav")
    db.add_track(files["alac"], title="alac")
    db.add_track(_aac_of_length(tmp_path / "odd.m4a", RATE, 88323), title="odd")
    db.save()
    out = subprocess.run([tool, "dump-video", mp], capture_output=True, text=True, check=True)
    rows = {
        f[1]: [int(x) for x in f[19:25]] for f in (ln.split("\t") for ln in out.stdout.splitlines())
    }
    g = read_gapless(files["vbr"])
    assert rows["vbr"] == [g["pregap"], g["sample_count"], g["postgap"], g["gapless_data"], 1, 0]
    assert rows["aac"] == [0x840, 0x15888, 0x1CA, 0, 1, 1]
    assert rows["wav"] == [0, 0, 0, 0, 0, 0]
    assert rows["alac"] == [0, SECONDS * RATE, 0, 0, 1, 0]
    assert rows["odd"][:3] == [1024, 88323, 88 * 1024 - 1024 - 88323]  # 88 packets
