"""Tests for Track model - full field coverage including read-only defaults."""

import struct

from pygpod.db.constants import (
    MEDIATYPE_AUDIO,
    MEDIATYPE_AUDIOBOOK,
    MEDIATYPE_PODCAST,
    MEDIATYPE_VIDEO,
    MHOD_ID_ALBUMARTIST,
    MHOD_ID_CATEGORY,
    MHOD_ID_DESCRIPTION,
    MHOD_ID_GROUPING,
    MHOD_ID_KEYWORDS,
    MHOD_ID_SUBTITLE,
)
from pygpod.db.parser import Record
from pygpod.db.writer import make_string_mhod
from pygpod.model.track import Track


def _make_full_track():
    """Create a Track with all header fields populated."""
    hdr_len = 0x248  # 584 bytes (iPod Classic header)
    header = bytearray(hdr_len)
    header[0:4] = b"mhit"
    struct.pack_into("<I", header, 4, hdr_len)
    struct.pack_into("<I", header, 0x10, 42)  # track_id
    struct.pack_into("<I", header, 0x14, 1)  # visible
    header[0x18:0x1C] = b" 3PM"  # filetype_marker (MP3, LE)
    header[0x1C] = 0  # type1
    header[0x1D] = 0  # type2
    header[0x1E] = 1  # compilation
    header[0x1F] = 80  # rating
    struct.pack_into("<I", header, 0x24, 5000000)  # file_size
    struct.pack_into("<I", header, 0x28, 240000)  # tracklen (duration_ms)
    struct.pack_into("<I", header, 0x2C, 5)  # track_number
    struct.pack_into("<I", header, 0x30, 12)  # total_tracks
    struct.pack_into("<I", header, 0x34, 2023)  # year
    struct.pack_into("<I", header, 0x38, 320)  # bitrate
    struct.pack_into("<I", header, 0x3C, (44100 << 16))  # samplerate
    struct.pack_into("<i", header, 0x40, -30)  # volume (signed)
    struct.pack_into("<I", header, 0x44, 1000)  # start_time
    struct.pack_into("<I", header, 0x48, 230000)  # stop_time
    struct.pack_into("<I", header, 0x4C, 0)  # soundcheck
    struct.pack_into("<I", header, 0x50, 42)  # play_count
    struct.pack_into("<I", header, 0x54, 40)  # play_count2
    struct.pack_into("<I", header, 0x5C, 2)  # cd_number
    struct.pack_into("<I", header, 0x60, 3)  # total_cds
    struct.pack_into("<Q", header, 0x70, 9999)  # dbid
    header[0x78] = 0  # checked
    header[0x79] = 60  # app_rating
    struct.pack_into("<H", header, 0x7A, 128)  # bpm
    struct.pack_into("<H", header, 0x7C, 1)  # artwork_count
    struct.pack_into("<I", header, 0x80, 50000)  # artwork_size
    header[0xA4] = 1  # has_artwork
    header[0xA5] = 1  # skip_when_shuffling
    header[0xA6] = 1  # remember_position
    header[0xB2] = 2  # mark_unplayed
    struct.pack_into("<I", header, 0xD0, MEDIATYPE_AUDIO)  # media_type
    struct.pack_into("<I", header, 0xD4, 3)  # season_number
    struct.pack_into("<I", header, 0xD8, 7)  # episode_number

    rec = Record(b"mhit", hdr_len, 0)
    rec.raw_header = bytes(header)
    rec.fields = {
        "track_id": 42,
        "dbid": 9999,
        "visible": 1,
        "filetype_marker": b" 3PM",
        "type1": 0,
        "type2": 0,
        "compilation": 1,
        "rating": 80,
        "file_size": 5000000,
        "tracklen": 240000,
        "track_number": 5,
        "total_tracks": 12,
        "year": 2023,
        "bitrate": 320,
        "samplerate": 44100 << 16,
        "volume": -30,
        "start_time": 1000,
        "stop_time": 230000,
        "soundcheck": 0,
        "play_count": 42,
        "play_count2": 40,
        "cd_number": 2,
        "total_cds": 3,
        "checked": 0,
        "app_rating": 60,
        "bpm": 128,
        "artwork_count": 1,
        "artwork_size": 50000,
        "has_artwork": 1,
        "skip_when_shuffling": 1,
        "remember_position": 1,
        "mark_unplayed": 2,
        "media_type": MEDIATYPE_AUDIO,
        "season_number": 3,
        "episode_number": 7,
        "samplerate_float": 44100.0,
        "explicit_flag": 1,
        "lyrics_flag": 0,
        "movie_flag": 0,
        "pregap": 0,
        "postgap": 0,
        "sample_count": 0,
        "gapless_data": 0,
        "gapless_track_flag": 0,
        "gapless_album_flag": 0,
        "skip_count": 3,
        "drm_userid": 0,
        "bookmark_time": 5000,
        "mhii_link": 10,
        "dbid2": 9999,
    }

    # Add string MHODs
    rec.children.append(make_string_mhod(1, "Full Track Title"))
    rec.children.append(make_string_mhod(4, "Full Artist"))
    rec.children.append(make_string_mhod(3, "Full Album"))
    rec.children.append(make_string_mhod(5, "Rock"))
    rec.children.append(make_string_mhod(12, "Composer Name"))
    rec.children.append(make_string_mhod(8, "A comment"))
    rec.children.append(make_string_mhod(MHOD_ID_ALBUMARTIST, "Album Artist"))
    rec.children.append(make_string_mhod(MHOD_ID_GROUPING, "My Group"))
    rec.children.append(make_string_mhod(MHOD_ID_DESCRIPTION, "A description"))
    rec.children.append(make_string_mhod(MHOD_ID_SUBTITLE, "Subtitle"))
    rec.children.append(make_string_mhod(MHOD_ID_KEYWORDS, "rock classic"))
    rec.children.append(make_string_mhod(MHOD_ID_CATEGORY, "Music"))

    return Track.from_record(rec)


class TestTrackAllReadOnlyFields:
    """Verify every read-only property returns the correct value."""

    def test_identifiers(self):
        t = _make_full_track()
        assert t.track_id == 42
        assert t.dbid == 9999
        assert t.dbid2 == 9999

    def test_file_info(self):
        t = _make_full_track()
        assert t.file_size == 5000000
        assert t.bitrate == 320
        assert t.samplerate == 44100
        assert t.filetype_marker == b" 3PM"
        assert t.visible == 1

    def test_duration(self):
        t = _make_full_track()
        assert t.duration_ms == 240000
        assert t.duration == 240.0

    def test_playback(self):
        t = _make_full_track()
        assert t.soundcheck == 0
        assert t.play_count2 == 40
        assert t.skip_count == 3
        assert t.drm_userid == 0
        assert t.bookmark_time == 5000

    def test_artwork(self):
        t = _make_full_track()
        assert t.artwork_count == 1
        assert t.artwork_size == 50000
        assert t.has_artwork is True
        assert t.mhii_link == 10

    def test_flags(self):
        t = _make_full_track()
        assert t.checked is True  # checked=0 means checked=True
        assert t.app_rating == 60
        assert t.explicit_flag == 1
        assert t.lyrics_flag is False
        assert t.movie_flag is False

    def test_gapless(self):
        t = _make_full_track()
        assert t.pregap == 0
        assert t.postgap == 0
        assert t.sample_count == 0
        assert t.gapless_data == 0
        assert t.gapless_track_flag is False
        assert t.gapless_album_flag is False

    def test_samplerate_float(self):
        t = _make_full_track()
        assert t.samplerate_float == 44100.0


class TestTrackAllEditableFields:
    """Verify every editable property can be set and read back."""

    def test_all_strings(self):
        t = _make_full_track()
        assert t.title == "Full Track Title"
        assert t.artist == "Full Artist"
        assert t.album == "Full Album"
        assert t.genre == "Rock"
        assert t.composer == "Composer Name"
        assert t.comment == "A comment"
        assert t.albumartist == "Album Artist"
        assert t.grouping == "My Group"
        assert t.description == "A description"
        assert t.subtitle == "Subtitle"
        assert t.keywords == "rock classic"
        assert t.category == "Music"

    def test_all_numerics(self):
        t = _make_full_track()
        assert t.rating == 80
        assert t.rating_stars == 4
        assert t.play_count == 42
        assert t.track_number == 5
        assert t.total_tracks == 12
        assert t.year == 2023
        assert t.cd_number == 2
        assert t.total_cds == 3
        assert t.bpm == 128
        assert t.volume == -30
        assert t.start_time == 1000
        assert t.stop_time == 230000
        assert t.season_number == 3
        assert t.episode_number == 7
        assert t.compilation is True
        assert t.skip_when_shuffling is True
        assert t.remember_position is True
        assert t.mark_unplayed is True

    def test_set_all_strings_and_readback(self):
        t = _make_full_track()
        t.title = "New Title"
        t.artist = "New Artist"
        t.album = "New Album"
        t.genre = "Pop"
        t.composer = "New Composer"
        t.comment = "New Comment"
        t.albumartist = "New AA"
        t.grouping = "New Group"
        t.description = "New Desc"
        t.subtitle = "New Sub"
        t.keywords = "pop new"
        t.category = "New Cat"

        assert t.title == "New Title"
        assert t.artist == "New Artist"
        assert t.album == "New Album"
        assert t.genre == "Pop"
        assert t.composer == "New Composer"
        assert t.comment == "New Comment"
        assert t.albumartist == "New AA"
        assert t.grouping == "New Group"
        assert t.description == "New Desc"
        assert t.subtitle == "New Sub"
        assert t.keywords == "pop new"
        assert t.category == "New Cat"

    def test_set_all_numerics_and_readback(self):
        t = _make_full_track()
        t.rating = 40
        t.play_count = 100
        t.track_number = 1
        t.total_tracks = 20
        t.year = 1999
        t.cd_number = 1
        t.total_cds = 1
        t.bpm = 90
        t.volume = 200
        t.start_time = 0
        t.stop_time = 300000
        t.season_number = 1
        t.episode_number = 1
        t.compilation = False
        t.skip_when_shuffling = False
        t.remember_position = False
        t.mark_unplayed = False

        assert t.rating == 40
        assert t.play_count == 100
        assert t.track_number == 1
        assert t.total_tracks == 20
        assert t.year == 1999
        assert t.cd_number == 1
        assert t.total_cds == 1
        assert t.bpm == 90
        assert t.volume == 200
        assert t.start_time == 0
        assert t.stop_time == 300000
        assert t.season_number == 1
        assert t.episode_number == 1
        assert t.compilation is False
        assert t.skip_when_shuffling is False
        assert t.remember_position is False
        # mark_unplayed=False sets 0x01 (played); getter checks raw byte
        assert t.record.raw_header[0xB2] == 0x01


class TestTrackConvenienceProperties:
    def test_is_podcast(self):
        t = _make_full_track()
        assert not t.is_podcast
        t.media_type = MEDIATYPE_PODCAST
        assert t.is_podcast

    def test_is_audiobook(self):
        t = _make_full_track()
        assert not t.is_audiobook
        t.media_type = MEDIATYPE_AUDIOBOOK
        assert t.is_audiobook

    def test_is_video(self):
        t = _make_full_track()
        assert not t.is_video
        t.media_type = MEDIATYPE_VIDEO
        assert t.is_video

    def test_str_repr(self):
        t = _make_full_track()
        s = str(t)
        assert "Full Artist" in s
        assert "Full Track Title" in s
        assert "Full Album" in s

        r = repr(t)
        assert "42" in r
        assert "Full Artist" in r
