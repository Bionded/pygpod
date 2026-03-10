"""Tests for DatabaseConfig."""

from pygpod.model.config import DatabaseConfig


def test_default_config():
    cfg = DatabaseConfig()
    assert cfg.string_encoding == 1
    assert cfg.track_id_start == 52
    assert cfg.master_playlist_name == "iPod"
    assert cfg.platform == 1
    assert cfg.generate_album_list is True
    assert cfg.generate_artist_list is True
    assert cfg.filename_prefix == "pygpod"
    assert cfg.filename_rand_len == 8
    assert cfg.filename_charset == "alnum"
    assert cfg.random_seed is None


def test_libgpod_compat():
    cfg = DatabaseConfig.libgpod_compat()
    assert cfg.filename_prefix == "libgpod"
    assert cfg.filename_rand_len == 6
    assert cfg.filename_charset == "digits"
    assert cfg.mhod_order is not None
    assert len(cfg.mhod_order) == 6
    # Order: title=1, artist=4, album=3, filetype=6, path=2, genre=5
    assert cfg.mhod_order[0] == 1
    assert cfg.mhod_order[1] == 4
    assert cfg.mhod_order[2] == 3


def test_deterministic_seed():
    cfg = DatabaseConfig(random_seed=42)
    assert cfg.random_seed == 42


def test_custom_config():
    cfg = DatabaseConfig(
        master_playlist_name="Custom",
        track_id_start=100,
        generate_album_list=False,
    )
    assert cfg.master_playlist_name == "Custom"
    assert cfg.track_id_start == 100
    assert cfg.generate_album_list is False


def test_config_mhit_unknowns():
    cfg = DatabaseConfig()
    assert cfg.unk_0x7e == 0xFFFF
    assert cfg.unk_0x84 == 0
    assert cfg.unk_0x90 == 12
    assert cfg.mark_unplayed == 1
    assert cfg.unk_0x168 == 1


def test_config_spl_prefs():
    cfg = DatabaseConfig()
    assert cfg.spl_prefs_padded_size == 72
    assert cfg.chapter_format == "atom"


def test_config_language():
    cfg = DatabaseConfig()
    assert cfg.language == 0x656E  # "en"
