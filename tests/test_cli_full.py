"""Comprehensive CLI tests - covers all commands, aliases, and edge cases."""

import os

import pytest


@pytest.fixture
def mp(writable_ipod):
    """Shorthand for writable iPod mountpoint."""
    return writable_ipod


def _main(argv):
    from pygpod.cli import main
    return main(argv)


# ============================================================================
# info command
# ============================================================================
class TestInfoCommand:

    def test_info_shows_device(self, mp, capsys):
        assert _main(["-m", mp, "info"]) == 0
        out = capsys.readouterr().out
        assert "Device:" in out

    def test_info_shows_tracks_count(self, mp, capsys):
        assert _main(["-m", mp, "info"]) == 0
        out = capsys.readouterr().out
        assert "Tracks:" in out

    def test_info_shows_playlists(self, mp, capsys):
        assert _main(["-m", mp, "info"]) == 0
        out = capsys.readouterr().out
        assert "Playlists:" in out

    def test_info_alias_i(self, mp, capsys):
        assert _main(["-m", mp, "i"]) == 0
        out = capsys.readouterr().out
        assert "Tracks:" in out

    def test_info_invalid_path(self):
        assert _main(["-m", "/nonexistent/path", "info"]) == 1

    def test_info_shows_master_playlist(self, mp, capsys):
        assert _main(["-m", mp, "info"]) == 0
        out = capsys.readouterr().out
        assert "*" in out  # master playlist indicator


# ============================================================================
# track commands
# ============================================================================
class TestTrackList:

    def test_track_list(self, mp, capsys):
        assert _main(["-m", mp, "track", "list"]) == 0
        out = capsys.readouterr().out
        assert "ID" in out
        assert "Total:" in out

    def test_track_list_alias_tr_ls(self, mp, capsys):
        assert _main(["-m", mp, "tr", "ls"]) == 0
        out = capsys.readouterr().out
        assert "Total:" in out

    def test_track_list_invalid_path(self):
        assert _main(["-m", "/nonexistent", "tr", "ls"]) == 1

    def test_track_list_no_subcommand(self, mp, capsys):
        result = _main(["-m", mp, "track"])
        assert result == 1
        err = capsys.readouterr().err
        assert "Usage:" in err


class TestTrackAdd:

    def test_track_add(self, mp, generated_mp3, capsys):
        assert _main(["-m", mp, "tr", "add", generated_mp3]) == 0
        out = capsys.readouterr().out
        assert "Added" in out
        assert "Database saved." in out

    def test_track_add_multiple(self, mp, generated_mp3, capsys):
        # Add same file twice (different copies on iPod)
        assert _main(["-m", mp, "tr", "add", generated_mp3, generated_mp3]) == 0
        out = capsys.readouterr().out
        assert out.count("Added") == 2

    def test_track_add_nonexistent_file(self, mp):
        assert _main(["-m", mp, "tr", "add", "/no/such/file.mp3"]) == 1


class TestTrackRemove:

    def test_track_remove_not_found(self, mp, capsys):
        assert _main(["-m", mp, "tr", "rm", "99999"]) == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_track_add_then_remove(self, mp, generated_mp3, capsys):
        # Add a track first
        _main(["-m", mp, "tr", "add", generated_mp3])
        capsys.readouterr()  # clear output

        # Get track ID from listing
        _main(["-m", mp, "tr", "ls"])
        out = capsys.readouterr().out

        # Find any track ID in the listing
        from pygpod.model.database import Database
        db = Database(mp)
        tid = db.tracks[0].track_id

        # Remove it
        assert _main(["-m", mp, "tr", "rm", str(tid)]) == 0
        out = capsys.readouterr().out
        assert "Removing:" in out


class TestTrackExport:

    def test_export_not_found(self, mp, tmp_path):
        dest = str(tmp_path / "out.mp3")
        assert _main(["-m", mp, "track", "export", "99999", dest]) == 1


# ============================================================================
# playlist commands
# ============================================================================
class TestPlaylistList:

    def test_playlist_list(self, mp, capsys):
        assert _main(["-m", mp, "pl", "ls"]) == 0
        out = capsys.readouterr().out
        assert "tracks" in out

    def test_playlist_list_full_name(self, mp, capsys):
        assert _main(["-m", mp, "playlist", "list"]) == 0
        out = capsys.readouterr().out
        assert "tracks" in out

    def test_playlist_no_subcommand(self, mp, capsys):
        result = _main(["-m", mp, "playlist"])
        assert result == 1
        err = capsys.readouterr().err
        assert "Usage:" in err


class TestPlaylistCreate:

    def test_playlist_create(self, mp, capsys):
        assert _main(["-m", mp, "pl", "create", "Test PL"]) == 0
        out = capsys.readouterr().out
        assert "Created playlist: Test PL" in out

        # Verify it shows up in listing
        assert _main(["-m", mp, "pl", "ls"]) == 0
        out = capsys.readouterr().out
        assert "Test PL" in out


class TestPlaylistAdd:

    def test_playlist_add_not_found_playlist(self, mp, capsys):
        assert _main(["-m", mp, "pl", "add", "NoSuchPlaylist", "52"]) == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_playlist_add_not_found_track(self, mp, capsys):
        # Create a playlist first
        _main(["-m", mp, "pl", "create", "TestPL2"])
        capsys.readouterr()

        # Add nonexistent track
        assert _main(["-m", mp, "pl", "add", "TestPL2", "99999"]) == 0
        err = capsys.readouterr().err
        assert "not found" in err

    def test_playlist_create_and_add_tracks(self, mp, generated_mp3, capsys):
        # Add a track
        _main(["-m", mp, "tr", "add", generated_mp3])
        capsys.readouterr()

        # Get track ID
        from pygpod.model.database import Database
        db = Database(mp)
        tid = db.tracks[0].track_id

        # Create playlist
        _main(["-m", mp, "pl", "create", "AddTest"])
        capsys.readouterr()

        # Add track to playlist (playlist is empty, tests `if pl is None` fix)
        assert _main(["-m", mp, "pl", "add", "AddTest", str(tid)]) == 0
        out = capsys.readouterr().out
        assert "Added track" in out


class TestPlaylistRemove:

    def test_playlist_remove_not_found_playlist(self, mp, capsys):
        assert _main(["-m", mp, "pl", "rm", "NoSuch", "52"]) == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_playlist_remove_not_found_track(self, mp, capsys):
        _main(["-m", mp, "pl", "create", "RmTest"])
        capsys.readouterr()
        assert _main(["-m", mp, "pl", "rm", "RmTest", "99999"]) == 1
        err = capsys.readouterr().err
        assert "not found" in err


# ============================================================================
# dump command
# ============================================================================
class TestDumpCommand:

    def test_dump(self, mp, capsys):
        assert _main(["-m", mp, "dump"]) == 0
        out = capsys.readouterr().out
        assert "mhbd" in out
        assert "mhsd" in out

    def test_dump_direct_file(self, itunesdb_path, capsys):
        assert _main(["-m", itunesdb_path, "dump"]) == 0
        out = capsys.readouterr().out
        assert "mhbd" in out


# ============================================================================
# init command
# ============================================================================
class TestInitCommand:

    def test_init(self, tmp_path, capsys):
        new_ipod = str(tmp_path / "new_ipod")
        os.makedirs(new_ipod)
        assert _main(["-m", new_ipod, "init"]) == 0
        out = capsys.readouterr().out
        assert "Initialized iPod" in out
        # Verify directory structure was created
        assert os.path.isdir(os.path.join(new_ipod, "iPod_Control", "iTunes"))
        assert os.path.isdir(os.path.join(new_ipod, "iPod_Control", "Music"))

    def test_init_custom_music_dirs(self, tmp_path, capsys):
        new_ipod = str(tmp_path / "new_ipod2")
        os.makedirs(new_ipod)
        assert _main(["-m", new_ipod, "init", "--music-dirs", "10"]) == 0
        out = capsys.readouterr().out
        assert "10 music dirs" in out

    def test_init_with_env(self, tmp_path, monkeypatch, capsys):
        new_ipod = str(tmp_path / "env_ipod")
        os.makedirs(new_ipod)
        monkeypatch.setenv("PYGPOD_MOUNTPOINT", new_ipod)
        assert _main(["init"]) == 0
        out = capsys.readouterr().out
        assert "Initialized iPod" in out


# ============================================================================
# fix-checksums command
# ============================================================================
class TestFixChecksums:

    def test_fix_checksums(self, mp, capsys):
        assert _main(["-m", mp, "fix-checksums"]) == 0
        out = capsys.readouterr().out
        assert "Checksums updated" in out


# ============================================================================
# purge command
# ============================================================================
class TestPurgeCommand:

    def test_purge_invalid_mountpoint(self, capsys):
        assert _main(["-m", "/nonexistent", "purge", "--yes"]) == 1

    def test_purge_with_yes(self, mp, capsys):
        # Ensure Music dir exists before purge
        music_dir = os.path.join(mp, "iPod_Control", "Music")
        os.makedirs(music_dir, exist_ok=True)
        assert _main(["-m", mp, "purge", "--yes"]) == 0
        out = capsys.readouterr().out
        assert "purged" in out
        # Music dir should be recreated (empty)
        assert os.path.isdir(music_dir)


# ============================================================================
# discover command
# ============================================================================
class TestDiscoverCommand:

    def test_discover(self, capsys):
        # Just verify it runs without error
        result = _main(["discover"])
        assert result == 0


# ============================================================================
# mountpoint resolution
# ============================================================================
class TestMountpoint:

    def test_env_var_fallback(self, mp, monkeypatch, capsys):
        monkeypatch.setenv("PYGPOD_MOUNTPOINT", mp)
        assert _main(["info"]) == 0

    def test_m_flag_takes_precedence(self, mp, monkeypatch, capsys):
        monkeypatch.setenv("PYGPOD_MOUNTPOINT", "/wrong/path")
        assert _main(["-m", mp, "info"]) == 0

    def test_no_mountpoint_error(self, monkeypatch, capsys):
        monkeypatch.delenv("PYGPOD_MOUNTPOINT", raising=False)
        result = _main(["info"])
        assert result == 1

    def test_debug_flag(self, mp, capsys):
        assert _main(["--debug", "-m", mp, "info"]) == 0


# ============================================================================
# track info command
# ============================================================================
class TestTrackInfo:

    def test_track_info(self, mp, capsys):
        from pygpod.model.database import Database
        db = Database(mp)
        tid = db.tracks[0].track_id
        assert _main(["-m", mp, "tr", "info", str(tid)]) == 0
        out = capsys.readouterr().out
        assert "Track ID:" in out
        assert "Title:" in out
        assert "Artist:" in out
        assert "Media Type:" in out
        assert "Path:" in out

    def test_track_info_not_found(self, mp, capsys):
        assert _main(["-m", mp, "tr", "info", "99999"]) == 1
        err = capsys.readouterr().err
        assert "not found" in err


# ============================================================================
# track add with metadata overrides
# ============================================================================
class TestTrackAddOverrides:

    def test_track_add_with_tags(self, mp, generated_mp3, capsys):
        assert _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--title", "My Title",
            "--artist", "My Artist",
            "--album", "My Album",
            "--genre", "Rock",
            "--year", "2025",
        ]) == 0
        out = capsys.readouterr().out
        assert "Added" in out

        # Verify tags were applied
        from pygpod.model.database import Database
        db = Database(mp)
        track = db.tracks[-1]
        assert track.title == "My Title"
        assert track.artist == "My Artist"
        assert track.album == "My Album"
        assert track.genre == "Rock"
        assert track.year == 2025

    def test_track_add_as_podcast(self, mp, generated_mp3, capsys):
        assert _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--type", "podcast",
            "--title", "Episode 1",
            "--category", "News",
            "--description", "A test episode",
        ]) == 0
        out = capsys.readouterr().out
        assert "[podcast]" in out

        from pygpod.model.database import Database
        db = Database(mp)
        track = db.tracks[-1]
        assert track.is_podcast
        assert track.title == "Episode 1"
        assert track.category == "News"
        assert track.description == "A test episode"

    def test_track_add_as_video(self, mp, generated_mp3, capsys):
        assert _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--type", "tvshow",
            "--tvshow", "My Show",
            "--season-number", "2",
            "--episode-number", "5",
        ]) == 0
        out = capsys.readouterr().out
        assert "[tvshow]" in out

        from pygpod.model.database import Database
        db = Database(mp)
        track = db.tracks[-1]
        assert track.tvshow == "My Show"
        assert track.season_number == 2
        assert track.episode_number == 5


# ============================================================================
# track list filtering
# ============================================================================
class TestTrackListFilter:

    def test_track_list_filter_genre(self, mp, generated_mp3, capsys):
        # Add a track with specific genre
        _main(["-m", mp, "tr", "add", generated_mp3, "--genre", "Jazz"])
        capsys.readouterr()

        assert _main(["-m", mp, "tr", "ls", "--genre", "Jazz"]) == 0
        out = capsys.readouterr().out
        assert "Total:" in out

    def test_track_list_filter_type(self, mp, capsys):
        # Filtered list should have fewer tracks than unfiltered
        assert _main(["-m", mp, "tr", "ls"]) == 0
        all_out = capsys.readouterr().out

        assert _main(["-m", mp, "tr", "ls", "--type", "podcast"]) == 0
        filtered_out = capsys.readouterr().out
        # Filtered output should be different from (shorter than) full list
        assert len(filtered_out) <= len(all_out)


# ============================================================================
# podcast playlist
# ============================================================================
class TestPodcastPlaylist:

    def test_create_podcast_playlist(self, mp, capsys):
        assert _main(["-m", mp, "pl", "create", "My Podcast", "--podcast"]) == 0
        out = capsys.readouterr().out
        assert "podcast playlist" in out

        # Verify it shows with P prefix in listing
        assert _main(["-m", mp, "pl", "ls"]) == 0
        out = capsys.readouterr().out
        assert "My Podcast" in out

    def test_playlist_list_podcast_filter(self, mp, capsys):
        _main(["-m", mp, "pl", "create", "Podcast1", "--podcast"])
        capsys.readouterr()

        assert _main(["-m", mp, "pl", "ls", "--podcast"]) == 0
        out = capsys.readouterr().out
        assert "Podcast1" in out


# ============================================================================
# playlist delete
# ============================================================================
class TestPlaylistDelete:

    def test_playlist_delete(self, mp, capsys):
        _main(["-m", mp, "pl", "create", "ToDelete"])
        capsys.readouterr()

        assert _main(["-m", mp, "pl", "delete", "ToDelete"]) == 0
        out = capsys.readouterr().out
        assert "Deleted" in out

    def test_playlist_delete_not_found(self, mp, capsys):
        assert _main(["-m", mp, "pl", "delete", "NoSuch"]) == 1
        err = capsys.readouterr().err
        assert "not found" in err


# ============================================================================
# auto podcast playlist
# ============================================================================
class TestAutoPodcastPlaylist:

    def test_podcast_auto_creates_playlist(self, mp, generated_mp3, capsys):
        """Adding a podcast track auto-creates the Podcasts playlist."""
        assert _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--type", "podcast", "--category", "Tech News",
        ]) == 0
        out = capsys.readouterr().out
        assert "Podcasts playlist" in out

        # Verify Podcasts playlist exists and has the track
        from pygpod.model.database import Database
        db = Database(mp)
        pl = None
        for p in db.playlists:
            if p.is_podcast:
                pl = p
                break
        assert pl is not None
        assert pl.track_count >= 1

    def test_podcast_auto_adds_to_existing(self, mp, generated_mp3, capsys):
        """Second podcast track reuses existing Podcasts playlist."""
        # First track creates the playlist
        _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--type", "podcast", "--category", "Science",
        ])
        capsys.readouterr()

        # Second track should add to existing Podcasts playlist
        assert _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--type", "podcast", "--category", "Science",
        ]) == 0
        out = capsys.readouterr().out
        assert "Added to Podcasts playlist" in out
        assert "Created Podcasts playlist" not in out

    def test_podcast_default_playlist_name(self, mp, generated_mp3, capsys):
        """Podcast without category uses 'Podcasts' as default playlist name."""
        assert _main([
            "-m", mp, "tr", "add", generated_mp3,
            "--type", "podcast",
        ]) == 0
        out = capsys.readouterr().out
        assert "Added to Podcasts playlist" in out
