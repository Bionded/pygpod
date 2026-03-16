"""Additional CLI tests for coverage gaps in cli.py."""

from unittest.mock import patch

import pytest


@pytest.fixture
def mp(writable_ipod):
    return writable_ipod


def _main(argv):
    from pygpod.cli import main

    return main(argv)


class TestTrackInfoAllOptionalFields:
    def test_track_info_with_all_optional_fields(self, mp, generated_mp3, capsys):
        """Track info displays all optional metadata fields when present."""
        # Add a track with every optional field populated
        _main(
            [
                "-m",
                mp,
                "tr",
                "add",
                generated_mp3,
                "--title",
                "Full Track",
                "--artist",
                "Test Artist",
                "--album",
                "Test Album",
                "--albumartist",
                "Album Artist Name",
                "--composer",
                "Composer Name",
                "--comment",
                "A test comment",
                "--grouping",
                "Grouping Value",
                "--genre",
                "TestGenre",
                "--year",
                "2024",
                "--track-number",
                "3",
                "--total-tracks",
                "12",
                "--cd-number",
                "2",
                "--total-cds",
                "3",
                "--type",
                "podcast",
                "--category",
                "Tech",
                "--description",
                "Episode description here",
                "--subtitle",
                "Episode subtitle",
                "--keywords",
                "python,ipod,test",
                "--podcast-url",
                "https://example.com/ep1.mp3",
                "--podcast-rss",
                "https://example.com/feed.xml",
            ]
        )
        capsys.readouterr()

        from pygpod.model.database import Database

        db = Database(mp)
        found = [t for t in db.tracks if t.title == "Full Track"]
        assert len(found) == 1
        tid = found[0].track_id

        ret = _main(["-m", mp, "track", "info", str(tid)])
        assert ret == 0
        out = capsys.readouterr().out

        # Verify all optional fields appear in output
        assert "Album Artist:" in out
        assert "Album Artist Name" in out
        assert "Composer:" in out
        assert "Composer Name" in out
        assert "Comment:" in out
        assert "A test comment" in out
        assert "Grouping:" in out
        assert "Grouping Value" in out
        assert "Category:" in out
        assert "Tech" in out
        assert "Description:" in out
        assert "Episode description here" in out
        assert "Subtitle:" in out
        assert "Episode subtitle" in out
        assert "Keywords:" in out
        assert "python,ipod,test" in out
        assert "Podcast URL:" in out
        assert "https://example.com/ep1.mp3" in out
        assert "Podcast RSS:" in out
        assert "https://example.com/feed.xml" in out
        # Track/disc numbering
        assert "Track:" in out
        assert "3/12" in out
        assert "Disc:" in out
        assert "2/3" in out
        # Podcast-specific playback flags
        assert "Remember Pos:" in out
        assert "Skip Shuffle:" in out

    def test_track_info_tvshow_fields(self, mp, generated_mp3, capsys):
        """Track info displays TV show-specific fields."""
        _main(
            [
                "-m",
                mp,
                "tr",
                "add",
                generated_mp3,
                "--title",
                "TV Episode",
                "--type",
                "tvshow",
                "--tvshow",
                "Breaking Bad",
                "--tvepisode",
                "S01E01",
                "--tvnetwork",
                "AMC",
                "--season-number",
                "1",
                "--episode-number",
                "1",
            ]
        )
        capsys.readouterr()

        from pygpod.model.database import Database

        db = Database(mp)
        found = [t for t in db.tracks if t.title == "TV Episode"]
        assert len(found) == 1
        tid = found[0].track_id

        ret = _main(["-m", mp, "track", "info", str(tid)])
        assert ret == 0
        out = capsys.readouterr().out

        assert "TV Show:" in out
        assert "Breaking Bad" in out
        assert "TV Episode:" in out
        assert "S01E01" in out
        assert "TV Network:" in out
        assert "AMC" in out
        assert "Season:" in out
        assert "Episode:" in out


class TestPurgeWithoutYes:
    def test_purge_confirm_with_purge(self, mp, capsys):
        """Purge without --yes prompts user and proceeds when input is 'PURGE'."""
        with patch("builtins.input", return_value="PURGE"):
            ret = _main(["-m", mp, "purge"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "purged" in out.lower()

    def test_purge_abort_wrong_input(self, mp, capsys):
        """Purge without --yes aborts when user types anything other than 'PURGE'."""
        with patch("builtins.input", return_value="no"):
            ret = _main(["-m", mp, "purge"])
        assert ret == 1
        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_purge_abort_empty_input(self, mp, capsys):
        """Purge without --yes aborts on empty input."""
        with patch("builtins.input", return_value=""):
            ret = _main(["-m", mp, "purge"])
        assert ret == 1
        out = capsys.readouterr().out
        assert "Aborted" in out


class TestDiscoverOutput:
    def test_discover_no_crash(self, capsys):
        """Discover command runs without crashing regardless of connected devices."""
        ret = _main(["-m", "/nonexistent", "discover"])
        # Just verify it completes without error
        assert ret == 0
