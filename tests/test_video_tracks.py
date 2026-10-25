"""Video tracks end to end: movies, TV shows and music videos from real .m4v files.

add_track -> save -> reopen keeps every video field, the iTunesDB round-trips byte-identically,
and libgpod (the reference) reads the same fields back. Needs ffmpeg for the H.264/AAC fixture.
"""

import os
import shutil
import subprocess

import pytest

from pygpod.db.constants import (
    MEDIATYPE_AUDIO,
    MEDIATYPE_MUSICVIDEO,
    MEDIATYPE_TVSHOW,
    MEDIATYPE_VIDEO,
)
from pygpod.db.parser import parse_itunesdb
from pygpod.db.writer import write_itunesdb
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database
from pygpod.tags import mp4_has_video, read_tags

from .conftest import _make_minimal_mp3, _make_minimal_png

GUID = "000A2700213749FF"
SYSINFO = f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n"  # Classic: hash58
M4V_MARKER = b" V4M"  # 'M4V ' as the little-endian u32 libgpod writes

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True, timeout=120)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """1 s 320x240 H.264 Baseline/AAC .m4v, the same with TV atoms + stik, audio-only .mp4."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("video_media")
    clip = str(d / "clip.m4v")
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "1", "-c:v", "libx264", "-profile:v", "baseline", "-level", "1.3",
        "-pix_fmt", "yuv420p", "-b:v", "200k", "-c:a", "aac", "-b:a", "64k", "-ac", "2",
        "-movflags", "+faststart", "-metadata", "title=Clip", clip,
    )  # fmt: skip
    from mutagen.mp4 import MP4

    def tagged(name, **atoms):
        path = str(d / name)
        shutil.copyfile(clip, path)
        f = MP4(path)
        for k, v in atoms.items():
            f[k] = v
        f.save()
        return path

    episode = tagged(
        "episode.m4v",
        stik=[10],
        tvsh=["Тіні"],
        tven=["S02E05"],
        tvnn=["UA:Перший"],
        tvsn=[2],
        tves=[5],
    )
    musicvideo = tagged("mv.m4v", stik=[6])
    audio_mp4 = str(d / "audio.mp4")
    _ffmpeg("-i", clip, "-vn", "-c:a", "copy", audio_mp4)
    return {"clip": clip, "episode": episode, "musicvideo": musicvideo, "audio_mp4": audio_mp4}


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp, name="pygpod", sysinfo_files={"SysInfo": SYSINFO.encode()})
    return mp


def _png_bytes(tmp_path):
    path = str(tmp_path / "poster.png")
    _make_minimal_png(path, 120, 120, (0, 120, 255))
    with open(path, "rb") as f:
        return f.read()


def _add_all(mp, media, cover=None):
    """A sync tool's usage: explicit metadata per media type."""
    db = Database(mp)
    db.add_track(
        media["clip"], media_type=MEDIATYPE_VIDEO, title="Фільм", year=2021, cover_art=cover
    )
    db.add_track(
        media["clip"],
        media_type=MEDIATYPE_TVSHOW,
        title="Episode 3",
        tvshow="Show",
        tvepisode="S01E03",
        season_number=1,
        episode_number=3,
        year=2020,
    )
    db.add_track(
        media["clip"], media_type=MEDIATYPE_MUSICVIDEO, title="Song", artist="Band", album="LP"
    )
    db.save()
    return Database(mp)


@needs_ffmpeg
def test_video_fields_survive_save_and_reopen(ipod, media, tmp_path):
    db = _add_all(ipod, media, cover=_png_bytes(tmp_path))
    t = {x.title: x for x in db.tracks}
    size = os.path.getsize(media["clip"])
    for x in t.values():
        assert x.movie_flag
        assert x.filetype_marker == M4V_MARKER
        assert x.filetype_str == "MPEG-4 video file"
        assert 900 <= x.duration_ms <= 1200
        assert x.file_size == size
        assert x.ipod_path.endswith(".m4v")
        assert os.path.isfile(os.path.join(ipod, *x.ipod_path.strip(":").split(":")))
    movie, ep, mv = t["Фільм"], t["Episode 3"], t["Song"]
    assert (movie.media_type, movie.year, movie.has_artwork) == (MEDIATYPE_VIDEO, 2021, True)
    assert (ep.media_type, ep.tvshow, ep.tvepisode) == (MEDIATYPE_TVSHOW, "Show", "S01E03")
    assert (ep.season_number, ep.episode_number, ep.year) == (1, 3, 2020)
    assert (mv.media_type, mv.artist, mv.album) == (MEDIATYPE_MUSICVIDEO, "Band", "LP")
    # videos are in the master playlist only: no podcast playlist, no extra playlists
    assert [p.name for p in db.playlists if not p.is_master] == []


@needs_ffmpeg
def test_video_itunesdb_round_trip_byte_identical(ipod, media):
    _add_all(ipod, media)
    with open(os.path.join(ipod, "iPod_Control", "iTunes", "iTunesDB"), "rb") as f:
        data = f.read()
    assert write_itunesdb(parse_itunesdb(data)) == data


@needs_ffmpeg
def test_media_type_and_tv_atoms_from_the_file(ipod, media):
    db = Database(ipod)
    clip = db.add_track(media["clip"])
    ep = db.add_track(media["episode"])
    mv = db.add_track(media["musicvideo"])
    override = db.add_track(media["episode"], media_type=MEDIATYPE_VIDEO, tvshow="Other")
    db.save()
    t = {x.track_id: x for x in Database(ipod).tracks}
    assert t[clip.track_id].media_type == MEDIATYPE_VIDEO and t[clip.track_id].movie_flag
    e = t[ep.track_id]
    assert (e.media_type, e.tvshow, e.tvepisode, e.tvnetwork) == (
        MEDIATYPE_TVSHOW,
        "Тіні",
        "S02E05",
        "UA:Перший",
    )
    assert (e.season_number, e.episode_number) == (2, 5)
    assert t[mv.track_id].media_type == MEDIATYPE_MUSICVIDEO
    o = t[override.track_id]  # explicit arguments win over the file's atoms
    assert (o.media_type, o.tvshow, o.season_number) == (MEDIATYPE_VIDEO, "Other", 2)


@needs_ffmpeg
def test_audio_only_mp4_and_mp3_stay_audio(ipod, media, tmp_path):
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    assert not mp4_has_video(media["audio_mp4"])
    assert "media_type" not in read_tags(media["audio_mp4"])
    db = Database(ipod)
    a = db.add_track(media["audio_mp4"])
    b = db.add_track(mp3)
    db.save()
    for x in Database(ipod).tracks:
        assert x.track_id in (a.track_id, b.track_id)
        assert x.media_type == MEDIATYPE_AUDIO and not x.movie_flag


def test_mp4_has_video_on_broken_files(tmp_path):
    cases = {
        "empty.m4v": b"",
        "garbage.m4v": b"\xff" * 64,
        "short_atom.m4v": b"\x00\x00\x00\x04moov",
        "oversized.m4v": b"\x00\x00\x10\x00moov\x00\x00\x00\x08trak",
        "no_hdlr.m4v": b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak",
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert mp4_has_video(str(path)) is False, name
    assert mp4_has_video(str(tmp_path / "missing.m4v")) is False


@needs_ffmpeg
@pytest.mark.libgpod
def test_libgpod_reads_video_fields(tool, ipod, media, tmp_path):
    _add_all(ipod, media, cover=_png_bytes(tmp_path))
    out = subprocess.run(
        [tool, "dump-video", ipod], capture_output=True, text=True, check=True
    ).stdout
    rows = {}
    for line in out.splitlines():
        f = line.split("\t")
        assert f[0] == "V"
        rows[f[1]] = f[2:]
    size = str(os.path.getsize(media["clip"]))
    #           mediatype, movie_flag, tvshow, tvepisode, tvnetwork, season, episode
    assert rows["Фільм"][:7] == [str(MEDIATYPE_VIDEO), "1", "", "", "", "0", "0"]
    assert rows["Episode 3"][:7] == [str(MEDIATYPE_TVSHOW), "1", "Show", "S01E03", "", "1", "3"]
    assert rows["Song"][:7] == [str(MEDIATYPE_MUSICVIDEO), "1", "", "", "", "0", "0"]
    for title, r in rows.items():
        marker, filetype, tracklen, fsize, _bitrate, samplerate = r[7:13]
        assert (marker, filetype, fsize, samplerate) == (
            "4d345620",
            "MPEG-4 video file",
            size,
            "44100",
        ), title
        assert 900 <= int(tracklen) <= 1200
    assert rows["Фільм"][13] == "2021" and rows["Episode 3"][13] == "2020"
    # artwork: libgpod sees the poster's thumbnails on the movie only
    assert rows["Фільм"][15] == "1" and rows["Episode 3"][15] == "0"


@needs_ffmpeg
def test_audiobook_and_podcast_with_pictures_are_not_movies(tmp_path, ipod):
    from mutagen.mp4 import MP4

    book = str(tmp_path / "book.m4b")
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=duration=1", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=1",
        "-map", "0", "-map", "1", "-c:a", "aac", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p",
        "-f", "mp4", book,
    )  # fmt: skip
    pod = str(tmp_path / "episode.mp4")
    shutil.copyfile(book, pod)
    f = MP4(pod)
    f["stik"] = [21]  # podcast
    f.save()
    assert "media_type" not in read_tags(book)
    assert "media_type" not in read_tags(pod)
    t = Database(ipod).add_track(book)
    assert t.media_type == MEDIATYPE_AUDIO and not t.movie_flag


def test_disabled_video_track_does_not_count(tmp_path):
    import struct as st

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    clip = str(tmp_path / "c.m4v")
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x48:rate=5", "-t", "1", "-c:v", "libx264", clip)
    assert mp4_has_video(clip)
    data = bytearray(open(clip, "rb").read())
    i = data.find(b"tkhd")
    data[i + 4 + 3] &= 0xFE  # clear "track enabled"
    assert st.unpack(">I", data[i - 4 : i])[0] > 0
    off = tmp_path / "off.m4v"
    off.write_bytes(bytes(data))
    assert not mp4_has_video(str(off))


def _add_chapter_reference(src, dest, chapter_track_id=2):
    """Copy an MP4 whose moov follows mdat, with a 'tref'/'chap' atom in its first track
    pointing at `chapter_track_id` (how enhanced podcasts and audiobooks link their chapter
    pictures). Atoms after mdat move, so no chunk offset changes."""
    import struct as st

    data = bytearray(open(src, "rb").read())

    def atoms(start, end):
        pos = start
        while pos + 8 <= end:
            size = st.unpack(">I", data[pos : pos + 4])[0]
            yield bytes(data[pos + 4 : pos + 8]), pos, size
            pos += size

    kinds = [k for k, _, _ in atoms(0, len(data))]
    assert kinds.index(b"mdat") < kinds.index(b"moov")
    moov, moov_size = next((p, s) for k, p, s in atoms(0, len(data)) if k == b"moov")
    trak, trak_size = next((p, s) for k, p, s in atoms(moov + 8, moov + moov_size) if k == b"trak")
    tref = st.pack(">I4sI4sI", 20, b"tref", 12, b"chap", chapter_track_id)
    data[trak + 8 : trak + 8] = tref
    st.pack_into(">I", data, trak, trak_size + len(tref))
    st.pack_into(">I", data, moov, moov_size + len(tref))
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _mjpeg_with_audio(path, rate, fmt):
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=duration=1", "-f", "lavfi",
        "-i", f"color=c=red:s=64x48:d=1:r={rate}", "-map", "0", "-map", "1",
        "-c:a", "aac", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-f", fmt, str(path),
    )  # fmt: skip
    return str(path)


@needs_ffmpeg
def test_motion_jpeg_mov_is_video(tmp_path):
    # QuickTime stores Motion-JPEG with the same 'jpeg' sample entry as a chapter picture
    mov = _mjpeg_with_audio(tmp_path / "camera.mov", 5, "mov")
    assert b"jpeg" in open(mov, "rb").read()
    assert mp4_has_video(mov)
    assert read_tags(mov)["media_type"] == MEDIATYPE_VIDEO
    still = _mjpeg_with_audio(tmp_path / "still.mov", 1, "mov")  # one frame: a picture
    assert not mp4_has_video(still)
    assert "media_type" not in read_tags(still)


@needs_ffmpeg
def test_chapter_picture_track_is_not_video(tmp_path):
    plain = _mjpeg_with_audio(tmp_path / "plain.mp4", 3, "mp4")
    assert mp4_has_video(plain)  # three enabled MJPEG frames without a reference: video
    pod = _add_chapter_reference(plain, str(tmp_path / "episode.mp4"))
    assert not mp4_has_video(pod)
    assert "media_type" not in read_tags(pod)
    mov = _mjpeg_with_audio(tmp_path / "plain.mov", 3, "mov")
    assert not mp4_has_video(_add_chapter_reference(mov, str(tmp_path / "chapters.mov")))
