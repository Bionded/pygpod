"""Tests for iPod file path utilities."""

import os
import random


def test_pick_music_dir(tmp_path):
    """Test random music directory selection."""
    from pygpod.device.fileutils import pick_music_dir

    # Create Fxx directories
    music_root = tmp_path / "iPod_Control" / "Music"
    for i in range(50):
        (music_root / f"F{i:02d}").mkdir(parents=True)

    result = pick_music_dir(str(tmp_path), num_dirs=50)
    assert "iPod_Control" in str(result)
    assert "Music" in str(result)
    assert result.name.startswith("F")


def test_pick_music_dir_deterministic(tmp_path):
    """Seeded RNG should give deterministic results."""
    from pygpod.device.fileutils import pick_music_dir

    rng = random.Random(42)
    d1 = pick_music_dir(str(tmp_path), num_dirs=50, rng=rng)

    rng2 = random.Random(42)
    d2 = pick_music_dir(str(tmp_path), num_dirs=50, rng=rng2)

    assert d1 == d2


def test_generate_ipod_filename():
    """Test random filename generation."""
    from pygpod.device.fileutils import generate_ipod_filename

    fname = generate_ipod_filename()
    assert fname.startswith("pygpod")
    assert fname.endswith(".mp3")
    # prefix (6) + rand (8) + ext (4) = 18
    assert len(fname) == 18


def test_generate_ipod_filename_custom():
    """Test filename generation with custom params."""
    from pygpod.device.fileutils import generate_ipod_filename

    fname = generate_ipod_filename(extension=".m4a", prefix="libgpod", rand_len=6, charset="digits")
    assert fname.startswith("libgpod")
    assert fname.endswith(".m4a")
    # Extract random part
    rand_part = fname[len("libgpod") : -len(".m4a")]
    assert len(rand_part) == 6
    assert rand_part.isdigit()


def test_generate_ipod_filename_deterministic():
    """Seeded RNG should produce deterministic filenames."""
    from pygpod.device.fileutils import generate_ipod_filename

    rng1 = random.Random(123)
    f1 = generate_ipod_filename(rng=rng1)

    rng2 = random.Random(123)
    f2 = generate_ipod_filename(rng=rng2)

    assert f1 == f2


def test_copy_track_to_ipod(tmp_path, generated_mp3):
    """Test copying a track to the iPod filesystem."""
    from pygpod.device.fileutils import copy_track_to_ipod

    # Create Fxx directories
    music_root = tmp_path / "iPod_Control" / "Music"
    for i in range(50):
        (music_root / f"F{i:02d}").mkdir(parents=True)

    ipod_path = copy_track_to_ipod(generated_mp3, str(tmp_path))

    # Should be colon-separated iPod path
    assert ipod_path.startswith(":iPod_Control:Music:F")
    assert ipod_path.endswith(".mp3")

    # File should actually exist on disk
    os_path = str(tmp_path / ipod_path.replace(":", os.sep).lstrip(os.sep))
    assert os.path.isfile(os_path)


def test_get_file_extension():
    """Test file extension extraction."""
    from pygpod.device.fileutils import get_file_extension

    assert get_file_extension("test.mp3") == ".mp3"
    assert get_file_extension("test.M4A") == ".m4a"
    assert get_file_extension("/path/to/file.wav") == ".wav"
    assert get_file_extension("noext") == ""


def test_file_size(generated_mp3):
    """Test file size measurement."""
    from pygpod.device.fileutils import file_size

    size = file_size(generated_mp3)
    assert size > 0
    assert isinstance(size, int)
