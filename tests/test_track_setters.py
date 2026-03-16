"""Tests for Track and Playlist property setters."""

import struct

import pytest

from pygpod.db.constants import (
    MEDIATYPE_AUDIO,
    MEDIATYPE_AUDIOBOOK,
    MEDIATYPE_PODCAST,
    MEDIATYPE_VIDEO,
    MHOD_ID_ALBUM,
    MHOD_ID_ALBUMARTIST,
    MHOD_ID_ARTIST,
    MHOD_ID_CATEGORY,
    MHOD_ID_COMMENT,
    MHOD_ID_COMPOSER,
    MHOD_ID_DESCRIPTION,
    MHOD_ID_GENRE,
    MHOD_ID_GROUPING,
    MHOD_ID_KEYWORDS,
    MHOD_ID_SORT_ALBUM,
    MHOD_ID_SORT_ARTIST,
    MHOD_ID_SORT_TITLE,
    MHOD_ID_SUBTITLE,
    MHOD_ID_TITLE,
    MHOD_ID_TVEPISODE,
    MHOD_ID_TVNETWORK,
    MHOD_ID_TVSHOW,
    MHOD_MAGIC,
)
from pygpod.db.parser import Record
from pygpod.db.writer import make_string_mhod
from pygpod.model.playlist import Playlist
from pygpod.model.track import Track


def _make_track(header_len=0x248, **fields):
    """Helper: create a Track with a valid-sized MHIT header."""
    rec = Record(b"mhit", header_len, 0)
    rec.raw_header = bytearray(header_len)
    rec.raw_header[0:4] = b"mhit"
    struct.pack_into("<I", rec.raw_header, 4, header_len)
    defaults = {"track_id": 1, "dbid": 100, "media_type": MEDIATYPE_AUDIO}
    defaults.update(fields)
    rec.fields = defaults
    return Track.from_record(rec)


# =========================================================================
# String setter tests
# =========================================================================


class TestTrackStringSetters:
    """Test that string setters create/replace MHOD children."""

    @pytest.mark.parametrize(
        "prop,mhod_type",
        [
            ("title", MHOD_ID_TITLE),
            ("artist", MHOD_ID_ARTIST),
            ("album", MHOD_ID_ALBUM),
            ("genre", MHOD_ID_GENRE),
            ("composer", MHOD_ID_COMPOSER),
            ("comment", MHOD_ID_COMMENT),
            ("albumartist", MHOD_ID_ALBUMARTIST),
            ("grouping", MHOD_ID_GROUPING),
            ("description", MHOD_ID_DESCRIPTION),
            ("subtitle", MHOD_ID_SUBTITLE),
            ("keywords", MHOD_ID_KEYWORDS),
            ("category", MHOD_ID_CATEGORY),
            ("tvshow", MHOD_ID_TVSHOW),
            ("tvepisode", MHOD_ID_TVEPISODE),
            ("tvnetwork", MHOD_ID_TVNETWORK),
            ("sort_title", MHOD_ID_SORT_TITLE),
            ("sort_artist", MHOD_ID_SORT_ARTIST),
            ("sort_album", MHOD_ID_SORT_ALBUM),
        ],
    )
    def test_set_string_property(self, prop, mhod_type):
        track = _make_track()
        assert getattr(track, prop) == ""

        setattr(track, prop, "Hello World")
        assert getattr(track, prop) == "Hello World"

        # Verify MHOD child exists with correct type
        found = [
            c
            for c in track.record.children
            if c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == mhod_type
        ]
        assert len(found) == 1
        assert found[0].fields["string"] == "Hello World"

    def test_set_title_replaces_existing(self):
        track = _make_track()
        track.record.children.append(make_string_mhod(MHOD_ID_TITLE, "Old"))
        assert track.title == "Old"

        track.title = "New"
        assert track.title == "New"

        # Only one title MHOD
        titles = [
            c
            for c in track.record.children
            if c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_TITLE
        ]
        assert len(titles) == 1

    def test_set_empty_string_removes_mhod(self):
        track = _make_track()
        track.title = "Something"
        assert track.title == "Something"

        track.title = ""
        assert track.title == ""
        titles = [
            c
            for c in track.record.children
            if c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_TITLE
        ]
        assert len(titles) == 0

    def test_set_unicode_string(self):
        track = _make_track()
        track.artist = "Скрябін"
        assert track.artist == "Скрябін"

        track.album = "日本語アルバム"
        assert track.album == "日本語アルバム"

    def test_set_does_not_affect_other_mhods(self):
        track = _make_track()
        track.record.children.append(make_string_mhod(MHOD_ID_TITLE, "Title"))
        track.record.children.append(make_string_mhod(MHOD_ID_ARTIST, "Artist"))
        track.record.children.append(make_string_mhod(MHOD_ID_ALBUM, "Album"))

        track.artist = "New Artist"

        assert track.title == "Title"
        assert track.artist == "New Artist"
        assert track.album == "Album"

    def test_set_on_empty_track(self):
        track = Track()
        # Should not raise on track with no record
        track.title = "Test"
        assert track.title == ""  # still empty because no record


# =========================================================================
# Numeric setter tests
# =========================================================================


class TestTrackNumericSetters:
    def test_set_rating(self):
        track = _make_track()
        track.rating = 80
        assert track.rating == 80
        assert track.record.fields["rating"] == 80
        # Check raw header at offset 0x1F (uint8)
        assert track.record.raw_header[0x1F] == 80

    def test_set_rating_stars(self):
        track = _make_track()
        track.rating_stars = 3
        assert track.rating == 60
        assert track.rating_stars == 3

    def test_set_rating_stars_clamped(self):
        track = _make_track()
        track.rating_stars = 10  # > 5
        assert track.rating == 100
        track.rating_stars = -1  # < 0
        assert track.rating == 0

    def test_set_play_count(self):
        track = _make_track()
        track.play_count = 42
        assert track.play_count == 42
        val = struct.unpack_from("<I", track.record.raw_header, 0x50)[0]
        assert val == 42

    def test_set_track_number(self):
        track = _make_track()
        track.track_number = 7
        assert track.track_number == 7
        val = struct.unpack_from("<I", track.record.raw_header, 0x2C)[0]
        assert val == 7

    def test_set_total_tracks(self):
        track = _make_track()
        track.total_tracks = 15
        assert track.total_tracks == 15

    def test_set_year(self):
        track = _make_track()
        track.year = 2024
        assert track.year == 2024
        val = struct.unpack_from("<I", track.record.raw_header, 0x34)[0]
        assert val == 2024

    def test_set_cd_number(self):
        track = _make_track()
        track.cd_number = 2
        track.total_cds = 3
        assert track.cd_number == 2
        assert track.total_cds == 3

    def test_set_bpm(self):
        track = _make_track()
        track.bpm = 140
        assert track.bpm == 140
        val = struct.unpack_from("<H", track.record.raw_header, 0x7A)[0]
        assert val == 140

    def test_set_volume(self):
        track = _make_track()
        track.volume = 100
        assert track.volume == 100

    def test_set_volume_clamped(self):
        track = _make_track()
        track.volume = 999
        assert track.volume == 255
        track.volume = -999
        assert track.volume == -255

    def test_set_start_stop_time(self):
        track = _make_track()
        track.start_time = 5000
        track.stop_time = 180000
        assert track.start_time == 5000
        assert track.stop_time == 180000

    def test_set_season_episode(self):
        track = _make_track()
        track.season_number = 2
        track.episode_number = 5
        assert track.season_number == 2
        assert track.episode_number == 5

    def test_set_compilation(self):
        track = _make_track()
        assert not track.compilation
        track.compilation = True
        assert track.compilation
        val = track.record.raw_header[0x1E]
        assert val == 1

    def test_set_skip_when_shuffling(self):
        track = _make_track()
        track.skip_when_shuffling = True
        assert track.skip_when_shuffling
        assert track.record.raw_header[0xA5] == 1

    def test_set_remember_position(self):
        track = _make_track()
        track.remember_position = True
        assert track.remember_position
        assert track.record.raw_header[0xA6] == 1

    def test_set_mark_unplayed(self):
        track = _make_track()
        track.mark_unplayed = True
        assert track.record.raw_header[0xB2] == 0x02
        track.mark_unplayed = False
        assert track.record.raw_header[0xB2] == 0x01


# =========================================================================
# media_type setter tests
# =========================================================================


class TestTrackMediaTypeSetter:
    def test_set_media_type_video(self):
        track = _make_track()
        track.media_type = MEDIATYPE_VIDEO
        assert track.media_type == MEDIATYPE_VIDEO
        assert track.is_video
        assert not track.is_podcast
        # type2 should be 1 for video
        assert track.record.raw_header[0x1D] == 1
        assert track.record.raw_header[0x1C] == 0

    def test_set_media_type_podcast(self):
        track = _make_track()
        track.media_type = MEDIATYPE_PODCAST
        assert track.is_podcast
        assert not track.is_video
        # Podcast flags set automatically
        assert track.record.raw_header[0xA5] == 1  # skip_when_shuffling
        assert track.record.raw_header[0xA6] == 1  # remember_position
        assert track.record.raw_header[0xA7] == 1  # flag4

    def test_set_media_type_audiobook(self):
        track = _make_track()
        track.media_type = MEDIATYPE_AUDIOBOOK
        assert track.is_audiobook
        # type1 should be 1 for audiobook
        assert track.record.raw_header[0x1C] == 1
        assert track.record.raw_header[0xA5] == 1  # skip_when_shuffling

    def test_set_media_type_audio(self):
        track = _make_track()
        track.media_type = MEDIATYPE_PODCAST
        assert track.is_podcast
        track.media_type = MEDIATYPE_AUDIO
        assert not track.is_podcast
        assert not track.is_video
        assert track.record.raw_header[0x1C] == 0
        assert track.record.raw_header[0x1D] == 0

    def test_media_type_updates_header(self):
        track = _make_track()
        track.media_type = MEDIATYPE_VIDEO
        val = struct.unpack_from("<I", track.record.raw_header, 0xD0)[0]
        assert val == MEDIATYPE_VIDEO


# =========================================================================
# Read-only property tests
# =========================================================================


class TestTrackReadOnly:
    def test_track_id_no_setter(self):
        track = _make_track(track_id=42)
        assert track.track_id == 42
        with pytest.raises(AttributeError):
            track.track_id = 99

    def test_dbid_no_setter(self):
        track = _make_track(dbid=123)
        assert track.dbid == 123
        with pytest.raises(AttributeError):
            track.dbid = 999

    def test_ipod_path_no_setter(self):
        track = _make_track()
        with pytest.raises(AttributeError):
            track.ipod_path = "/test"

    def test_file_size_no_setter(self):
        track = _make_track(file_size=1024)
        with pytest.raises(AttributeError):
            track.file_size = 2048

    def test_bitrate_no_setter(self):
        track = _make_track(bitrate=320)
        with pytest.raises(AttributeError):
            track.bitrate = 128

    def test_filetype_str_no_setter(self):
        track = _make_track()
        with pytest.raises(AttributeError):
            track.filetype_str = "WAV"


# =========================================================================
# Playlist setter tests
# =========================================================================


class TestPlaylistSetters:
    def _make_playlist(self, name="Test Playlist"):
        rec = Record(b"mhyp", 108, 0)
        rec.raw_header = bytearray(108)
        rec.raw_header[0:4] = b"mhyp"
        rec.fields = {"playlist_type": 0, "podcast_flag": 0, "playlist_id": 42}
        rec.children.append(make_string_mhod(MHOD_ID_TITLE, name))
        return Playlist.from_record(rec)

    def test_rename_playlist(self):
        pl = self._make_playlist("Old Name")
        assert pl.name == "Old Name"

        pl.name = "New Name"
        assert pl.name == "New Name"

    def test_rename_preserves_other_children(self):
        pl = self._make_playlist("Test")
        # Add a non-title MHOD
        pl.record.children.append(make_string_mhod(MHOD_ID_GENRE, "dummy"))
        assert len(pl.record.children) == 2

        pl.name = "Renamed"
        assert pl.name == "Renamed"
        # Title replaced, genre kept
        assert len(pl.record.children) == 2

    def test_rename_unicode(self):
        pl = self._make_playlist("Test")
        pl.name = "Депрессивная рефлексия"
        assert pl.name == "Депрессивная рефлексия"

    def test_name_inserted_at_front(self):
        pl = self._make_playlist("Test")
        pl.record.children.append(make_string_mhod(MHOD_ID_GENRE, "extra"))
        pl.name = "Front"
        # Title MHOD should be first child
        first = pl.record.children[0]
        assert first.fields.get("mhod_type") == MHOD_ID_TITLE
        assert first.fields.get("string") == "Front"

    def test_playlist_id_no_setter(self):
        pl = self._make_playlist()
        assert pl.playlist_id == 42
        with pytest.raises(AttributeError):
            pl.playlist_id = 99

    def test_is_master_no_setter(self):
        pl = self._make_playlist()
        with pytest.raises(AttributeError):
            pl.is_master = True

    def test_is_podcast_no_setter(self):
        pl = self._make_playlist()
        with pytest.raises(AttributeError):
            pl.is_podcast = True


# =========================================================================
# Round-trip: set properties then save/reload
# =========================================================================


class TestSetterRoundTrip:
    def test_set_and_write(self):
        """Verify setters produce valid records that the writer can serialize."""
        from pygpod.db.writer import _write_item_record

        track = _make_track()
        track.title = "Round Trip Title"
        track.artist = "Round Trip Artist"
        track.rating = 100
        track.year = 2025
        track.track_number = 5

        data = _write_item_record(track.record)
        assert len(data) > 0
        assert data[0:4] == b"mhit"

    def test_modify_and_save_database(self, writable_ipod):
        """Test modifying track properties and saving the database."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        if not db.tracks:
            pytest.skip("No tracks in fixture")

        track = db.tracks[0]

        track.title = "Modified Title"
        track.artist = "Modified Artist"
        track.rating = 60
        track.year = 1999

        assert track.title == "Modified Title"
        assert track.artist == "Modified Artist"
        assert track.rating == 60
        assert track.year == 1999

        db.save()

        # Reload and verify
        db2 = Database(writable_ipod)
        track2 = db2.tracks[0]
        assert track2.title == "Modified Title"
        assert track2.artist == "Modified Artist"
        assert track2.rating == 60
        assert track2.year == 1999

    def test_rename_playlist_and_save(self, writable_ipod):
        """Test renaming a playlist and verifying after reload."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        pl = db.create_playlist("Original Name")
        assert pl.name == "Original Name"

        pl.name = "Renamed Playlist"
        db.save()

        db2 = Database(writable_ipod)
        found = [p for p in db2.playlists if p.name == "Renamed Playlist"]
        assert len(found) == 1

    def test_change_media_type_and_save(self, writable_ipod):
        """Test changing media_type and verifying round-trip."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        if not db.tracks:
            pytest.skip("No tracks in fixture")

        track = db.tracks[0]
        track.media_type = MEDIATYPE_PODCAST
        assert track.is_podcast
        db.save()

        db2 = Database(writable_ipod)
        track2 = db2.tracks[0]
        assert track2.is_podcast
        assert track2.media_type == MEDIATYPE_PODCAST
