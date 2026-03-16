"""Shared pytest fixtures for pygpod tests.

Provides session-scoped fixtures for iPod filesystem, generated MP3 files,
and writable iPod directories. All fixtures are built in pure Python using
pygpod itself - no gcc, libgpod, or ffmpeg required.

If gcc + libgpod + ffmpeg ARE available, the C-based fixture builder is used
for integration tests (richer fixture with cover art, video tracks, etc.).
"""

import os
import shutil
import struct
import subprocess
import tempfile
import zlib

import pytest


def pytest_sessionfinish(session, exitstatus):
    """Clean up test_files/ directory after test session."""
    test_files = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "test_files",
    )
    if os.path.isdir(test_files):
        shutil.rmtree(test_files, ignore_errors=True)


# Root of the project
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_FILES_DIR = os.path.join(PROJECT_ROOT, "test_files")
IPOD_FS_DIR = os.path.join(TEST_FILES_DIR, "ipod_fs")
C_SOURCE = os.path.join(PROJECT_ROOT, "integration_tests", "build_ipod_mass.c")

# iPod Classic 1G (B029) params for fixture generation
FIXTURE_MODEL = "B029"
FIXTURE_GUID = "000A2700213749FF"
FIXTURE_MUSIC_DIRS = 50

# -------------------------------------------------------------------------
# Minimal MP3 generator (pure Python, no ffmpeg)
# -------------------------------------------------------------------------

# A valid MPEG Layer 3 frame header for 128kbps, 44100Hz, stereo, padded.
# This is followed by silence (zero bytes) to fill the frame.
# Frame header: 0xFFFB9004  (sync=FFF, ver=MPEG1, layer=III, no CRC,
#   bitrate=128k, samplerate=44100, padding=0, stereo)
_MP3_FRAME_HEADER = b"\xff\xfb\x90\x04"
# MPEG1 Layer3 128kbps 44100Hz frame size = 417 bytes (with header)
_MP3_FRAME_SIZE = 417


def _make_minimal_mp3(path, duration_frames=38):
    """Create a minimal valid MP3 file (~1 second) without ffmpeg.

    Just repeated silent MPEG frames. Enough for pygpod to detect as MP3
    and get basic metadata (duration, bitrate, samplerate).
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        for _ in range(duration_frames):
            f.write(_MP3_FRAME_HEADER)
            f.write(b"\x00" * (_MP3_FRAME_SIZE - len(_MP3_FRAME_HEADER)))


def _make_minimal_png(path, width=64, height=64, color=(0, 100, 200)):
    """Create a minimal PNG without Pillow."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    for _ in range(height):
        raw += b"\x00"
        for _ in range(width):
            raw += bytes(color)
    compressed = zlib.compress(raw)
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    with open(path, "wb") as f:
        f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


# -------------------------------------------------------------------------
# MP3 generation helpers
# -------------------------------------------------------------------------

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _generate_mp3_ffmpeg(
    path, freq=440, title="", artist="", album="", genre="", year=0, track_num=0, cover_png=None
):
    """Generate a real MP3 with ID3 tags using ffmpeg."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration=1",
        "-ar",
        "44100",
        "-ab",
        "128k",
    ]
    meta = {}
    if title:
        meta["title"] = title
    if artist:
        meta["artist"] = artist
    if album:
        meta["album"] = album
    if genre:
        meta["genre"] = genre
    if year:
        meta["date"] = str(year)
    if track_num:
        meta["track"] = str(track_num)
    for k, v in meta.items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd.append(path)
    subprocess.run(cmd, capture_output=True, check=True)

    # Embed cover art if provided
    if cover_png and os.path.isfile(cover_png):
        try:
            from mutagen.id3 import APIC
            from mutagen.mp3 import MP3

            audio = MP3(path)
            if audio.tags is None:
                audio.add_tags()
            with open(cover_png, "rb") as f:
                art_data = f.read()
            audio.tags.add(
                APIC(
                    encoding=0,
                    mime="image/png",
                    type=3,
                    desc="Cover",
                    data=art_data,
                )
            )
            audio.save()
        except ImportError:
            pass


# -------------------------------------------------------------------------
# Pure-Python iPod fixture (always available)
# -------------------------------------------------------------------------

TRACKS_META = [
    # (title, artist, album, genre, year, track_num, freq)
    ("Highway Ride", "The Rockers", "Road Trip", "Rock", 2018, 1, 220),
    ("Desert Wind", "The Rockers", "Road Trip", "Rock", 2018, 2, 260),
    ("Mountain Echo", "The Rockers", "Road Trip", "Rock", 2018, 3, 300),
    ("City Lights", "The Rockers", "Road Trip", "Rock", 2018, 4, 340),
    ("Open Road", "The Rockers", "Road Trip", "Rock", 2018, 5, 380),
    ("Neon Dreams", "Synthwave", "Retrowave", "Electronic", 2020, 1, 420),
    ("Midnight Run", "Synthwave", "Retrowave", "Electronic", 2020, 2, 460),
    ("Laser Grid", "Synthwave", "Retrowave", "Electronic", 2020, 3, 500),
    ("Chrome Horizon", "Synthwave", "Retrowave", "Electronic", 2020, 4, 540),
    ("Digital Sunset", "Synthwave", "Retrowave", "Electronic", 2020, 5, 580),
    ("Acoustic Morning", "Folk Band", "Campfire", "Folk", 2019, 1, 620),
    ("River Song", "Folk Band", "Campfire", "Folk", 2019, 2, 660),
    ("Timber Trail", "Folk Band", "Campfire", "Folk", 2019, 3, 700),
    ("Starlight", "Folk Band", "Campfire", "Folk", 2019, 4, 740),
    ("Embers", "Folk Band", "Campfire", "Folk", 2019, 5, 780),
    ("Podcast Ep 1", "Host", "My Podcast", "Podcast", 2021, 1, 330),
    ("Podcast Ep 2", "Host", "My Podcast", "Podcast", 2021, 2, 370),
    ("Podcast Ep 3", "Host", "My Podcast", "Podcast", 2021, 3, 410),
    ("Bass Drop", "DJ Mix", "Club Anthems", "Dance", 2022, 1, 440),
    ("Peak Hour", "DJ Mix", "Club Anthems", "Dance", 2022, 2, 480),
    ("After Party", "DJ Mix", "Club Anthems", "Dance", 2022, 3, 520),
]


def _generate_pure_python_fixture(dest_dir):
    """Build an iPod filesystem fixture using pygpod.

    Uses ffmpeg + mutagen for real MP3s with tags and cover art when
    available, falls back to silent MP3 frames with manual metadata.
    """
    from pygpod.device.mountpoint import init_ipod
    from pygpod.model.database import Database

    os.makedirs(dest_dir, exist_ok=True)

    # Create directory structure
    ipod_ctrl = os.path.join(dest_dir, "iPod_Control")
    for sub in ["iTunes", "Artwork", "Device"]:
        os.makedirs(os.path.join(ipod_ctrl, sub), exist_ok=True)
    for i in range(FIXTURE_MUSIC_DIRS):
        os.makedirs(os.path.join(ipod_ctrl, "Music", f"F{i:02d}"), exist_ok=True)

    # Write SysInfo
    sysinfo_path = os.path.join(ipod_ctrl, "Device", "SysInfo")
    with open(sysinfo_path, "w") as f:
        f.write(f"ModelNumStr: x{FIXTURE_MODEL}\n")
        f.write(f"FirewireGuid: 0x{FIXTURE_GUID}\n")

    # Initialize database
    init_ipod(dest_dir)

    # Generate cover art for embedding
    media_dir = os.path.join(dest_dir, "_media_src")
    os.makedirs(media_dir, exist_ok=True)
    cover_png = os.path.join(media_dir, "cover.png")
    _make_minimal_png(cover_png, 128, 128)

    # Generate MP3 files
    use_ffmpeg = _HAS_FFMPEG
    mp3_paths = []
    for i, (title, artist, album, genre, year, track_num, freq) in enumerate(TRACKS_META):
        mp3_path = os.path.join(media_dir, f"track_{i + 1:02d}.mp3")
        if use_ffmpeg:
            try:
                _generate_mp3_ffmpeg(
                    mp3_path,
                    freq=freq,
                    title=title,
                    artist=artist,
                    album=album,
                    genre=genre,
                    year=year,
                    track_num=track_num,
                    cover_png=cover_png if i < 15 else None,  # first 15 get art
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                use_ffmpeg = False
                _make_minimal_mp3(mp3_path)
        else:
            _make_minimal_mp3(mp3_path)
        mp3_paths.append(mp3_path)

    # Add tracks to database
    db = Database(dest_dir)
    added_tracks = []
    for i, (title, artist, album, genre, year, track_num, _freq) in enumerate(TRACKS_META):
        kwargs = {}
        if not use_ffmpeg:
            # Manual metadata when ffmpeg isn't available
            kwargs = {
                "title": title,
                "artist": artist,
                "album": album,
                "genre": genre,
                "year": year,
                "track_number": track_num,
                "total_tracks": 5 if track_num <= 5 else 3,
            }
        if genre == "Podcast":
            kwargs["media_type"] = 0x00000004
            kwargs["category"] = album

        track = db.add_track(mp3_paths[i], **kwargs)
        added_tracks.append(track)

    # Create playlists
    rock_pl = db.create_playlist("Rock Favorites")
    for t in added_tracks[:5]:
        db.add_track_to_playlist(rock_pl, t)

    electro_pl = db.create_playlist("Electronic Mix")
    for t in added_tracks[5:10]:
        db.add_track_to_playlist(electro_pl, t)

    db.save()

    # Generate iTunesSD
    _generate_itunessd(dest_dir)

    # Clean up source media files
    shutil.rmtree(media_dir, ignore_errors=True)

    return dest_dir


# -------------------------------------------------------------------------
# C-based fixture (richer, for integration tests)
# -------------------------------------------------------------------------


def _generate_c_fixture():
    """Auto-generate the iPod fixture using the integration C builder.

    Compiles build_ipod_mass.c, generates media files, and creates a
    complete iPod filesystem at test_files/ipod_fs/.

    Returns the path on success, or None on failure.
    """
    if not os.path.isfile(C_SOURCE):
        return None

    try:
        cflags = (
            subprocess.check_output(
                ["pkg-config", "--cflags", "libgpod-1.0", "glib-2.0"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        libs = (
            subprocess.check_output(
                ["pkg-config", "--libs", "libgpod-1.0", "glib-2.0"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    build_dir = tempfile.mkdtemp(prefix="pygpod_fixture_build_")
    try:
        binary = os.path.join(build_dir, "build_ipod_mass")
        cmd = f"gcc -o {binary} {C_SOURCE} {cflags} {libs}"
        subprocess.check_call(cmd, shell=True, stderr=subprocess.PIPE)

        media_dir = os.path.join(build_dir, "media")
        os.makedirs(media_dir)
        for i in range(21):
            freq = 220 + i * 40
            is_video = 10 <= i <= 13
            ext = "mp4" if is_video else "mp3"
            path = os.path.join(media_dir, f"media_{i + 1:02d}.{ext}")
            if is_video:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=c=blue:s=320x240:d=1",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq}:duration=1",
                        "-codec:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        "-codec:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-shortest",
                        path,
                    ],
                    capture_output=True,
                    check=True,
                )
            else:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq}:duration=1",
                        "-codec:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        "-ar",
                        "44100",
                        path,
                    ],
                    capture_output=True,
                    check=True,
                )

        cover_art = os.path.join(media_dir, "cover_art.png")
        _make_minimal_png(cover_art)

        os.makedirs(IPOD_FS_DIR, exist_ok=True)
        result = subprocess.run(
            [
                binary,
                IPOD_FS_DIR,
                FIXTURE_MODEL,
                media_dir,
                FIXTURE_GUID,
                str(FIXTURE_MUSIC_DIRS),
                cover_art,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(IPOD_FS_DIR, ignore_errors=True)
            return None

        _generate_itunessd(IPOD_FS_DIR)
        return IPOD_FS_DIR
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        shutil.rmtree(IPOD_FS_DIR, ignore_errors=True)
        return None
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def _generate_itunessd(ipod_dir):
    """Generate a minimal iTunesSD file from the iTunesDB tracks."""
    from pygpod.db.itunessd import ITunesSD, ITunesSDTrack
    from pygpod.model.database import Database

    db_path = os.path.join(ipod_dir, "iPod_Control", "iTunes", "iTunesDB")
    if not os.path.isfile(db_path):
        return

    db = Database.from_file(db_path)
    sd = ITunesSD()
    sd.db_version = 0x010800

    for track in db.tracks:
        ipod_path = track.ipod_path or ""
        colon_path = ipod_path.replace("/", ":")
        if not colon_path:
            colon_path = ":iPod_Control:Music:F00:unknown.mp3"

        ft = 1  # mp3
        if colon_path.endswith((".m4a", ".aac", ".mp4", ".m4v")):
            ft = 2
        elif colon_path.endswith((".wav", ".aiff")):
            ft = 4

        sd.tracks.append(
            ITunesSDTrack(
                start_pos_ms=0,
                stop_pos_ms=0,
                volume=100,
                file_type=ft,
                filename=colon_path,
                shuffle_flag=0 if track.is_podcast or track.is_audiobook else 1,
                bookmark_flag=1 if track.is_podcast or track.is_audiobook else 0,
            )
        )

    sd_path = os.path.join(ipod_dir, "iPod_Control", "iTunes", "iTunesSD")
    with open(sd_path, "wb") as f:
        f.write(sd.write())


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture(scope="session")
def ipod_fs_path(tmp_path_factory):
    """Path to the iPod filesystem test fixture.

    Tries the C-based builder first (richer fixture). Falls back to
    pure-Python builder (always works, no external dependencies).
    """
    if os.path.isdir(IPOD_FS_DIR):
        return IPOD_FS_DIR

    # Try C builder
    result = _generate_c_fixture()
    if result is not None:
        return result

    # Fall back to pure Python
    dest = str(tmp_path_factory.mktemp("ipod_fixture"))
    return _generate_pure_python_fixture(dest)


@pytest.fixture(scope="session")
def itunesdb_path(ipod_fs_path):
    """Path to the test iTunesDB file."""
    path = os.path.join(ipod_fs_path, "iPod_Control", "iTunes", "iTunesDB")
    assert os.path.isfile(path), f"iTunesDB missing: {path}"
    return path


@pytest.fixture(scope="session")
def itunessd_path(ipod_fs_path):
    """Path to the test iTunesSD file."""
    path = os.path.join(ipod_fs_path, "iPod_Control", "iTunes", "iTunesSD")
    if not os.path.isfile(path):
        pytest.skip("iTunesSD not available in fixture")
    return path


@pytest.fixture(scope="session")
def artworkdb_path(ipod_fs_path):
    """Path to the test ArtworkDB file."""
    path = os.path.join(ipod_fs_path, "iPod_Control", "Artwork", "ArtworkDB")
    if not os.path.isfile(path):
        pytest.skip("ArtworkDB not available in fixture")
    return path


@pytest.fixture(scope="session")
def sysinfo_path(ipod_fs_path):
    """Path to the test SysInfo file."""
    path = os.path.join(ipod_fs_path, "iPod_Control", "Device", "SysInfo")
    assert os.path.isfile(path), f"SysInfo missing: {path}"
    return path


@pytest.fixture(scope="session")
def generated_mp3(tmp_path_factory):
    """Generate an MP3 file for testing.

    Uses ffmpeg with ID3 tags when available, falls back to silent frames.
    """
    mp3_dir = tmp_path_factory.mktemp("music")
    mp3_path = str(mp3_dir / "test_track.mp3")
    if _HAS_FFMPEG:
        try:
            _generate_mp3_ffmpeg(
                mp3_path,
                freq=440,
                title="Test Track",
                artist="Test Artist",
                album="Test Album",
                genre="Test",
            )
            return mp3_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    _make_minimal_mp3(mp3_path)
    return mp3_path


@pytest.fixture(scope="session")
def generated_mp3_with_art(tmp_path_factory):
    """Generate an MP3 file with embedded cover art."""
    mp3_dir = tmp_path_factory.mktemp("music_art")
    mp3_path = str(mp3_dir / "track_with_art.mp3")
    cover_path = str(mp3_dir / "cover.png")
    _make_minimal_png(cover_path, 200, 200, (255, 0, 0))
    if _HAS_FFMPEG:
        try:
            _generate_mp3_ffmpeg(
                mp3_path,
                freq=440,
                title="Art Track",
                artist="Art Artist",
                album="Art Album",
                cover_png=cover_path,
            )
            return mp3_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    _make_minimal_mp3(mp3_path)
    return mp3_path


@pytest.fixture
def writable_ipod(tmp_path, ipod_fs_path):
    """A writable copy of the iPod filesystem for modification tests.

    Includes:
    - Full iPod_Control directory tree copied from test fixture
    - F00-F49 music directories created
    """
    ipod = str(tmp_path / "ipod")
    shutil.copytree(ipod_fs_path, ipod)

    # Ensure Fxx music directories exist
    music_dir = os.path.join(ipod, "iPod_Control", "Music")
    os.makedirs(music_dir, exist_ok=True)
    for i in range(50):
        os.makedirs(os.path.join(music_dir, f"F{i:02d}"), exist_ok=True)

    return ipod


@pytest.fixture
def writable_ipod_with_mp3(writable_ipod, tmp_path):
    """Writable iPod + a fresh MP3 file ready to be added."""
    mp3_path = str(tmp_path / "new_track.mp3")
    _make_minimal_mp3(mp3_path)
    return writable_ipod, mp3_path
