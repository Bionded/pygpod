"""Tests for CLI entry point."""


def test_cli_no_args():
    """Running with no args should print help and return 0."""
    from pygpod.cli import main

    result = main([])
    assert result == 0


def test_cli_help():
    """--help should exit with SystemExit(0)."""
    from pygpod.cli import main

    try:
        main(["--help"])
        assert False, "Should have raised SystemExit"
    except SystemExit as e:
        assert e.code == 0


def test_cli_debug_flag():
    """--debug flag should be accepted."""
    from pygpod.cli import main

    # With no command, should just print help
    result = main(["--debug"])
    assert result == 0


def test_cli_info_invalid():
    """info with invalid mountpoint should return error."""
    from pygpod.cli import main

    result = main(["-m", "/nonexistent/path", "info"])
    assert result == 1


def test_cli_list_invalid():
    """track list with invalid mountpoint should return error."""
    from pygpod.cli import main

    result = main(["-m", "/nonexistent/path", "track", "list"])
    assert result == 1


def test_cli_info_valid(ipod_fs_path):
    """info with valid mountpoint should return 0."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "info"])
    assert result == 0


def test_cli_list_valid(ipod_fs_path):
    """track list with valid mountpoint should return 0."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "tr", "ls"])
    assert result == 0


def test_cli_list_playlists(ipod_fs_path):
    """playlist list with valid mountpoint should return 0."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "pl", "ls"])
    assert result == 0


def test_cli_dump(ipod_fs_path):
    """dump with valid mountpoint should return 0."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "dump"])
    assert result == 0


def test_cli_dump_file(itunesdb_path):
    """dump with direct iTunesDB path should return 0."""
    from pygpod.cli import main

    result = main(["-m", itunesdb_path, "dump"])
    assert result == 0


def test_cli_remove_missing(ipod_fs_path):
    """track remove with non-existent track ID should return 1."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "tr", "rm", "99999"])
    assert result == 1


def test_cli_export_missing(ipod_fs_path, tmp_path):
    """track export with non-existent track ID should return 1."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "track", "export", "99999", str(tmp_path / "out.mp3")])
    assert result == 1


def test_cli_info_alias(ipod_fs_path):
    """'i' alias should work for info."""
    from pygpod.cli import main

    result = main(["-m", ipod_fs_path, "i"])
    assert result == 0


def test_cli_env_mountpoint(ipod_fs_path, monkeypatch):
    """PYGPOD_MOUNTPOINT env var should be used as default."""
    from pygpod.cli import main

    monkeypatch.setenv("PYGPOD_MOUNTPOINT", ipod_fs_path)
    result = main(["info"])
    assert result == 0
