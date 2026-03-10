"""Tests for tags module - audio file metadata reading."""




class TestReadTags:

    def test_read_tags_mp3(self, generated_mp3):
        from pygpod.tags import read_tags
        tags = read_tags(generated_mp3)
        assert "title" in tags
        assert "artist" in tags
        assert "album" in tags
        assert "file_size" in tags
        assert tags["file_size"] > 0
        assert tags["filetype"] == "MPEG audio file"

    def test_read_tags_duration(self, generated_mp3):
        from pygpod.tags import read_tags
        tags = read_tags(generated_mp3)
        # Generated MP3 is ~1 second
        assert tags["duration_ms"] > 500
        assert tags["duration_ms"] < 3000

    def test_read_tags_default_title(self, generated_mp3):
        from pygpod.tags import read_tags
        tags = read_tags(generated_mp3)
        # Title defaults to filename without extension
        assert tags["title"] == "test_track" or len(tags["title"]) > 0


class TestExtractArtwork:

    def test_extract_artwork_no_art(self, generated_mp3):
        from pygpod.tags import extract_artwork
        result = extract_artwork(generated_mp3)
        # Generated MP3 has no embedded artwork
        assert result is None

    def test_extract_artwork_nonexistent(self):
        from pygpod.tags import extract_artwork
        result = extract_artwork("/no/such/file.mp3")
        assert result is None


class TestDetectFiletype:

    def test_mp3(self):
        from pygpod.tags import _detect_filetype
        assert _detect_filetype("song.mp3") == "MPEG audio file"

    def test_m4a(self):
        from pygpod.tags import _detect_filetype
        assert _detect_filetype("song.m4a") == "AAC audio file"

    def test_mp4(self):
        from pygpod.tags import _detect_filetype
        assert _detect_filetype("video.mp4") == "MPEG-4 video file"

    def test_wav(self):
        from pygpod.tags import _detect_filetype
        assert _detect_filetype("sound.wav") == "WAV audio file"

    def test_aiff(self):
        from pygpod.tags import _detect_filetype
        assert _detect_filetype("sound.aiff") == "AIFF audio file"

    def test_unknown_extension(self):
        from pygpod.tags import _detect_filetype
        assert _detect_filetype("file.xyz") == "MPEG audio file"


class TestFiletypeMarker:

    def test_mp3_marker(self):
        from pygpod.tags import filetype_marker
        assert filetype_marker("song.mp3") == b" 3PM"

    def test_m4a_marker(self):
        from pygpod.tags import filetype_marker
        assert filetype_marker("song.m4a") == b" A4M"

    def test_mp4_marker(self):
        from pygpod.tags import filetype_marker
        assert filetype_marker("video.mp4") == b" V4M"

    def test_wav_marker(self):
        from pygpod.tags import filetype_marker
        assert filetype_marker("song.wav") == b" VAW"

    def test_unknown_marker(self):
        from pygpod.tags import filetype_marker
        assert filetype_marker("file.xyz") == b" 3PM"


class TestSafeInt:

    def test_valid_int(self):
        from pygpod.tags import _safe_int
        assert _safe_int("42") == 42

    def test_invalid_int(self):
        from pygpod.tags import _safe_int
        assert _safe_int("abc") == 0

    def test_empty_string(self):
        from pygpod.tags import _safe_int
        assert _safe_int("") == 0

    def test_none(self):
        from pygpod.tags import _safe_int
        assert _safe_int(None) == 0
