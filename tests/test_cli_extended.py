"""Extended CLI tests - discover, init, track info, filters, error paths."""

import os

import pytest


@pytest.fixture
def mp(writable_ipod):
    return writable_ipod


def _main(argv):
    from pygpod.cli import main

    return main(argv)


# =========================================================================
# discover command
# =========================================================================


class TestDiscover:
    def test_discover_runs(self, capsys):
        # discover scans real system - just verify it doesn't crash
        ret = _main(["-m", "/nonexistent", "discover"])
        out = capsys.readouterr().out
        # Either finds iPods or says none found
        assert ret == 0 or "No iPods" in out or ret == 0


# =========================================================================
# init command
# =========================================================================


class TestInit:
    def test_init_basic(self, tmp_path, capsys):
        ipod = str(tmp_path / "new_ipod")
        os.makedirs(ipod)
        ret = _main(["-m", ipod, "init"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Initialized" in out
        assert os.path.isfile(os.path.join(ipod, "iPod_Control", "iTunes", "iTunesDB"))

    def test_init_with_model(self, tmp_path, capsys):
        ipod = str(tmp_path / "new_ipod2")
        os.makedirs(ipod)
        ret = _main(["-m", ipod, "init", "--model", "classic_6g"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Initialized" in out

    def test_init_custom_music_dirs(self, tmp_path, capsys):
        ipod = str(tmp_path / "new_ipod3")
        os.makedirs(ipod)
        ret = _main(["-m", ipod, "init", "--music-dirs", "20"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "20 music dirs" in out


# =========================================================================
# track info command
# =========================================================================


class TestTrackInfo:
    def test_track_info(self, mp, capsys):
        from pygpod.model.database import Database

        db = Database(mp)
        if not db.tracks:
            pytest.skip("No tracks")
        tid = db.tracks[0].track_id
        ret = _main(["-m", mp, "track", "info", str(tid)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Track ID:" in out
        assert "Title:" in out
        assert "Artist:" in out
        assert "Duration:" in out
        assert "Bitrate:" in out
        assert "Path:" in out

    def test_track_info_not_found(self, mp, capsys):
        ret = _main(["-m", mp, "track", "info", "999999"])
        assert ret == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_track_info_podcast_fields(self, mp, generated_mp3, capsys):
        """Track info shows podcast-specific fields."""
        _main(["-m", mp, "tr", "add", generated_mp3, "--type", "podcast", "--category", "Tech"])
        capsys.readouterr()

        from pygpod.model.database import Database

        db = Database(mp)
        pod = [t for t in db.tracks if t.is_podcast]
        if not pod:
            pytest.skip("No podcast track")
        ret = _main(["-m", mp, "track", "info", str(pod[0].track_id)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Category:" in out


# =========================================================================
# track list filters
# =========================================================================


class TestTrackListFilters:
    def test_filter_by_genre(self, mp, capsys):
        ret = _main(["-m", mp, "track", "list", "--genre", "Rock"])
        assert ret == 0
        out = capsys.readouterr().out
        # Either finds tracks or says "No tracks found"
        assert "Rock" in out or "No tracks" in out

    def test_filter_by_artist(self, mp, capsys):
        ret = _main(["-m", mp, "track", "list", "--artist", "Rockers"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Rockers" in out or "No tracks" in out

    def test_filter_by_type_podcast(self, mp, capsys):
        ret = _main(["-m", mp, "track", "list", "--type", "podcast"])
        assert ret == 0

    def test_filter_by_category(self, mp, capsys):
        ret = _main(["-m", mp, "track", "list", "--category", "Music"])
        assert ret == 0

    def test_no_tracks_found(self, mp, capsys):
        ret = _main(["-m", mp, "track", "list", "--genre", "ZZZZNONEXISTENT"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "No tracks" in out


# =========================================================================
# track export
# =========================================================================


class TestTrackExport:
    def test_export(self, mp, tmp_path, capsys):
        from pygpod.model.database import Database

        db = Database(mp)
        if not db.tracks:
            pytest.skip("No tracks")
        t = db.tracks[0]
        dest = str(tmp_path / "exported")
        os.makedirs(dest)
        ret = _main(["-m", mp, "track", "export", str(t.track_id), dest])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Exported" in out

    def test_export_not_found(self, mp, tmp_path, capsys):
        ret = _main(["-m", mp, "track", "export", "999999", str(tmp_path)])
        assert ret == 1


# =========================================================================
# playlist commands
# =========================================================================


class TestPlaylistCommands:
    def test_playlist_remove_track(self, mp, capsys):
        from pygpod.model.database import Database

        db = Database(mp)
        pls = [p for p in db.playlists if not p.is_master and p.track_count > 0]
        if not pls:
            pytest.skip("No playlist with tracks")
        pl = pls[0]
        tid = pl.track_ids[0]
        ret = _main(["-m", mp, "playlist", "remove", pl.name, str(tid)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Removed" in out

    def test_playlist_remove_not_found(self, mp, capsys):
        ret = _main(["-m", mp, "playlist", "remove", "ZZZZZ", "999"])
        assert ret == 1

    def test_playlist_delete(self, mp, capsys):
        _main(["-m", mp, "playlist", "create", "ToDelete"])
        capsys.readouterr()
        ret = _main(["-m", mp, "playlist", "delete", "ToDelete"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Deleted" in out

    def test_playlist_delete_master(self, mp, capsys):
        ret = _main(["-m", mp, "playlist", "delete", "iPod"])
        assert ret == 1
        err = capsys.readouterr().err
        assert "master" in err.lower() or "Cannot" in err


# =========================================================================
# dump command
# =========================================================================


class TestDump:
    def test_dump_from_file(self, itunesdb_path, capsys):
        ret = _main(["-m", itunesdb_path, "dump"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "mhbd" in out

    def test_dump_not_found(self, tmp_path, capsys):
        ret = _main(["-m", str(tmp_path / "nonexistent"), "dump"])
        assert ret == 1


# =========================================================================
# fix-checksums
# =========================================================================


class TestFixChecksums:
    def test_fix_checksums(self, mp, capsys):
        ret = _main(["-m", mp, "fix-checksums"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Checksums updated" in out


# =========================================================================
# purge
# =========================================================================


class TestPurge:
    def test_purge_with_yes(self, mp, capsys):
        ret = _main(["-m", mp, "purge", "--yes"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "purged" in out.lower()
        # iTunes dir should be empty
        itunes = os.path.join(mp, "iPod_Control", "iTunes")
        assert os.path.isdir(itunes)

    def test_purge_invalid_mountpoint(self, tmp_path, capsys):
        ret = _main(["-m", str(tmp_path), "purge", "--yes"])
        assert ret == 1


# =========================================================================
# unknown command
# =========================================================================


class TestUnknownCommand:
    def test_unknown_command_exits(self, mp, capsys):
        # argparse exits with code 2 for invalid choices
        with pytest.raises(SystemExit) as exc_info:
            _main(["-m", mp, "nonexistent_command"])
        assert exc_info.value.code == 2

    def test_track_no_subcommand(self, mp, capsys):
        ret = _main(["-m", mp, "track"])
        assert ret == 1

    def test_playlist_no_subcommand(self, mp, capsys):
        ret = _main(["-m", mp, "playlist"])
        assert ret == 1


# =========================================================================
# media_type_name
# =========================================================================


class TestMediaTypeName:
    def test_known_types(self):
        from pygpod.cli import _media_type_name

        assert _media_type_name(0x0001) == "audio"
        assert _media_type_name(0x0002) == "video"
        assert _media_type_name(0x0004) == "podcast"

    def test_combined_type(self):
        from pygpod.cli import _media_type_name

        name = _media_type_name(0x0006)  # VIDEO | PODCAST
        assert "video" in name
        assert "podcast" in name

    def test_unknown_type(self):
        from pygpod.cli import _media_type_name

        assert _media_type_name(0) == "audio"
