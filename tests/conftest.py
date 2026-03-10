"""Shared pytest fixtures for pygpod tests.

Provides session-scoped fixtures for iPod filesystem, generated MP3 files,
and writable iPod directories to avoid hardcoded paths in test modules.

The iPod fixture at test_files/ipod_fs/ is auto-generated using the
integration test C builder (build_ipod_mass.c) if it doesn't exist.
Requires gcc, libgpod-1.0, glib-2.0, and ffmpeg.
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
        f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed)
                + chunk(b"IEND", b""))


def _generate_ipod_fixture():
    """Auto-generate the iPod fixture using the integration C builder.

    Compiles build_ipod_mass.c, generates media files, and creates a
    complete iPod filesystem at test_files/ipod_fs/.

    Returns the path on success, or None on failure.
    """
    if not os.path.isfile(C_SOURCE):
        return None

    # Check for pkg-config, gcc, ffmpeg
    try:
        cflags = subprocess.check_output(
            ["pkg-config", "--cflags", "libgpod-1.0", "glib-2.0"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        libs = subprocess.check_output(
            ["pkg-config", "--libs", "libgpod-1.0", "glib-2.0"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    build_dir = tempfile.mkdtemp(prefix="pygpod_fixture_build_")
    try:
        # Compile
        binary = os.path.join(build_dir, "build_ipod_mass")
        cmd = f"gcc -o {binary} {C_SOURCE} {cflags} {libs}"
        subprocess.check_call(cmd, shell=True, stderr=subprocess.PIPE)

        # Generate media files
        media_dir = os.path.join(build_dir, "media")
        os.makedirs(media_dir)
        for i in range(21):
            freq = 220 + i * 40
            is_video = 10 <= i <= 13
            ext = "mp4" if is_video else "mp3"
            path = os.path.join(media_dir, f"media_{i + 1:02d}.{ext}")
            if is_video:
                subprocess.run(
                    ["ffmpeg", "-y",
                     "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d=1",
                     "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                     "-codec:v", "libx264", "-preset", "ultrafast",
                     "-pix_fmt", "yuv420p", "-codec:a", "aac", "-b:a", "128k",
                     "-shortest", path],
                    capture_output=True, check=True,
                )
            else:
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i",
                     f"sine=frequency={freq}:duration=1",
                     "-codec:a", "libmp3lame", "-b:a", "128k", "-ar", "44100",
                     path],
                    capture_output=True, check=True,
                )

        # Cover art
        cover_art = os.path.join(media_dir, "cover_art.png")
        _make_minimal_png(cover_art)

        # Build iPod filesystem
        os.makedirs(IPOD_FS_DIR, exist_ok=True)
        result = subprocess.run(
            [binary, IPOD_FS_DIR, FIXTURE_MODEL, media_dir,
             FIXTURE_GUID, str(FIXTURE_MUSIC_DIRS), cover_art],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(IPOD_FS_DIR, ignore_errors=True)
            return None

        # Generate iTunesSD for Shuffle tests
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
        if colon_path.startswith(":"):
            pass  # already colon-prefixed
        elif not colon_path:
            colon_path = ":iPod_Control:Music:F00:unknown.mp3"

        ft = 1  # mp3
        if colon_path.endswith((".m4a", ".aac")):
            ft = 2
        elif colon_path.endswith((".mp4", ".m4v")):
            ft = 2
        elif colon_path.endswith((".wav", ".aiff")):
            ft = 4

        sd.tracks.append(ITunesSDTrack(
            start_pos_ms=0, stop_pos_ms=0, volume=100,
            file_type=ft, filename=colon_path,
            shuffle_flag=0 if track.is_podcast or track.is_audiobook else 1,
            bookmark_flag=1 if track.is_podcast or track.is_audiobook else 0,
        ))

    sd_path = os.path.join(ipod_dir, "iPod_Control", "iTunes", "iTunesSD")
    with open(sd_path, "wb") as f:
        f.write(sd.write())


@pytest.fixture(scope="session")
def ipod_fs_path():
    """Path to the iPod filesystem test fixture.

    Auto-generates test_files/ipod_fs/ using the integration C builder
    if it doesn't exist. Skips if libgpod/gcc/ffmpeg are unavailable.
    """
    if not os.path.isdir(IPOD_FS_DIR):
        result = _generate_ipod_fixture()
        if result is None:
            pytest.skip(
                "iPod fixture not available (needs gcc, libgpod, ffmpeg)"
            )
    return IPOD_FS_DIR


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


def _generate_minimal_mp3(path):
    """Generate a minimal valid MP3 file using ffmpeg.

    Creates a 1-second 128kbps sine tone at 440Hz.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-ar",
            "44100",
            "-ab",
            "128k",
            "-f",
            "mp3",
            path,
        ],
        capture_output=True,
        check=True,
    )


@pytest.fixture(scope="session")
def generated_mp3(tmp_path_factory):
    """Generate a minimal MP3 file for testing track operations.

    Returns the path to a valid MP3 file (1 second, 440Hz sine tone).
    """
    mp3_dir = tmp_path_factory.mktemp("music")
    mp3_path = str(mp3_dir / "test_track.mp3")
    _generate_minimal_mp3(mp3_path)
    return mp3_path


@pytest.fixture
def writable_ipod(tmp_path, ipod_fs_path, generated_mp3):
    """A writable copy of the iPod filesystem for modification tests.

    Includes:
    - Full iPod_Control directory tree copied from test fixture
    - F00-F49 music directories created
    - The generated MP3 placed in the music directory for add operations
    """
    ipod = str(tmp_path / "ipod")
    shutil.copytree(ipod_fs_path, ipod)

    # Create Fxx music directories
    music_dir = os.path.join(ipod, "iPod_Control", "Music")
    os.makedirs(music_dir, exist_ok=True)
    for i in range(50):
        os.makedirs(os.path.join(music_dir, f"F{i:02d}"), exist_ok=True)

    return ipod
