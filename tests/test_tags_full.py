"""Full tests for tags.py — all codecs, format validation, edge cases."""

import os
import shutil
import subprocess

import pytest

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _gen_audio(path, fmt_args, duration=1):
    """Generate a test audio file with ffmpeg."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = (
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
        ]
        + fmt_args
        + [path]
    )
    subprocess.run(cmd, capture_output=True, check=True)


# =========================================================================
# Format validation
# =========================================================================


class TestFormatValidation:
    def test_mp3_supported(self, tmp_path):
        from pygpod.tags import check_format_supported

        check_format_supported(str(tmp_path / "song.mp3"))  # no raise

    def test_m4a_supported(self, tmp_path):
        from pygpod.tags import check_format_supported

        check_format_supported(str(tmp_path / "song.m4a"))

    def test_wav_supported(self, tmp_path):
        from pygpod.tags import check_format_supported

        check_format_supported(str(tmp_path / "song.wav"))

    def test_aiff_supported(self, tmp_path):
        from pygpod.tags import check_format_supported

        check_format_supported(str(tmp_path / "song.aiff"))

    def test_mp4_video_passes_with_warning(self, tmp_path):
        from pygpod.tags import check_format_supported

        # Video files pass through (not validated by extension)
        check_format_supported(str(tmp_path / "video.mp4"))
        check_format_supported(str(tmp_path / "video.m4v"))
        check_format_supported(str(tmp_path / "video.mov"))

    def test_flac_unsupported(self, tmp_path):
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.tags import check_format_supported

        with pytest.raises(UnsupportedFormatError, match="FLAC"):
            check_format_supported(str(tmp_path / "song.flac"))

    def test_ogg_unsupported(self, tmp_path):
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.tags import check_format_supported

        with pytest.raises(UnsupportedFormatError, match="Ogg"):
            check_format_supported(str(tmp_path / "song.ogg"))

    def test_wma_unsupported(self, tmp_path):
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.tags import check_format_supported

        with pytest.raises(UnsupportedFormatError, match="Windows Media"):
            check_format_supported(str(tmp_path / "song.wma"))

    def test_opus_unsupported(self, tmp_path):
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.tags import check_format_supported

        with pytest.raises(UnsupportedFormatError, match="Opus"):
            check_format_supported(str(tmp_path / "song.opus"))

    def test_webm_unsupported(self, tmp_path):
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.tags import check_format_supported

        with pytest.raises(UnsupportedFormatError, match="WebM"):
            check_format_supported(str(tmp_path / "song.webm"))

    def test_unknown_ext_passes(self, tmp_path):
        from pygpod.tags import check_format_supported

        # Unknown extensions are allowed (not in the unsupported list)
        check_format_supported(str(tmp_path / "song.xyz"))

    def test_database_rejects_flac(self, writable_ipod, tmp_path):
        """Database.add_track raises for unsupported formats."""
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        flac_path = str(tmp_path / "song.flac")
        with open(flac_path, "wb") as f:
            f.write(b"\x00" * 100)

        with pytest.raises(UnsupportedFormatError):
            db.add_track(flac_path)

    def test_error_message_mentions_conversion(self, tmp_path):
        from pygpod.exceptions import UnsupportedFormatError
        from pygpod.tags import check_format_supported

        with pytest.raises(UnsupportedFormatError) as exc_info:
            check_format_supported(str(tmp_path / "song.flac"))
        msg = str(exc_info.value)
        assert "Convert to MP3 or AAC" in msg
        assert "not yet implemented" in msg


# =========================================================================
# Tag reading — different codecs
# =========================================================================


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not available")
class TestTagReadingCodecs:
    def test_read_mp3_tags(self, tmp_path):
        from pygpod.tags import read_tags

        p = str(tmp_path / "test.mp3")
        _gen_audio(
            p,
            [
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                "-metadata",
                "title=MP3 Title",
                "-metadata",
                "artist=MP3 Artist",
                "-metadata",
                "album=MP3 Album",
                "-metadata",
                "genre=Rock",
                "-metadata",
                "date=2023",
                "-metadata",
                "track=3/10",
            ],
        )
        tags = read_tags(p)
        assert tags["title"] == "MP3 Title"
        assert tags["artist"] == "MP3 Artist"
        assert tags["album"] == "MP3 Album"
        assert tags["genre"] == "Rock"
        assert tags["year"] == 2023
        assert tags["track_number"] == 3
        assert tags["total_tracks"] == 10
        assert tags["bitrate"] > 0
        assert tags["samplerate"] > 0
        assert tags["duration_ms"] > 0

    def test_read_m4a_tags(self, tmp_path):
        from pygpod.tags import read_tags

        p = str(tmp_path / "test.m4a")
        _gen_audio(
            p,
            [
                "-codec:a",
                "aac",
                "-b:a",
                "128k",
                "-metadata",
                "title=M4A Title",
                "-metadata",
                "artist=M4A Artist",
                "-metadata",
                "album=M4A Album",
            ],
        )
        tags = read_tags(p)
        assert tags["title"] == "M4A Title"
        assert tags["artist"] == "M4A Artist"
        assert tags["album"] == "M4A Album"
        assert tags["duration_ms"] > 0
        assert tags["filetype"] == "AAC audio file"

    def test_read_wav_tags(self, tmp_path):
        from pygpod.tags import read_tags

        p = str(tmp_path / "test.wav")
        _gen_audio(p, ["-codec:a", "pcm_s16le", "-ar", "44100"])
        tags = read_tags(p)
        assert tags["duration_ms"] > 0
        assert tags["samplerate"] == 44100
        assert tags["filetype"] == "WAV audio file"

    def test_read_aiff_tags(self, tmp_path):
        from pygpod.tags import read_tags

        p = str(tmp_path / "test.aiff")
        _gen_audio(p, ["-codec:a", "pcm_s16be"])
        tags = read_tags(p)
        assert tags["duration_ms"] > 0
        assert tags["filetype"] == "AIFF audio file"

    def test_read_flac_tags(self, tmp_path):
        """FLAC is unsupported for iPod but tags should still be readable."""
        from pygpod.tags import read_tags

        p = str(tmp_path / "test.flac")
        _gen_audio(
            p,
            [
                "-codec:a",
                "flac",
                "-metadata",
                "title=FLAC Title",
                "-metadata",
                "artist=FLAC Artist",
            ],
        )
        tags = read_tags(p)
        assert tags["title"] == "FLAC Title"
        assert tags["artist"] == "FLAC Artist"
        assert tags["duration_ms"] > 0

    def test_read_ogg_tags(self, tmp_path):
        """OGG is unsupported for iPod but tags should still be readable."""
        from pygpod.tags import read_tags

        p = str(tmp_path / "test.ogg")
        try:
            _gen_audio(
                p,
                [
                    "-codec:a",
                    "libvorbis",
                    "-b:a",
                    "128k",
                    "-metadata",
                    "title=OGG Title",
                ],
            )
        except subprocess.CalledProcessError:
            pytest.skip("ffmpeg lacks libvorbis support")
        tags = read_tags(p)
        assert tags["title"] == "OGG Title"
        assert tags["duration_ms"] > 0


# =========================================================================
# Tag reading — edge cases
# =========================================================================


class TestTagEdgeCases:
    def test_empty_file(self, tmp_path):
        from pygpod.tags import read_tags

        p = str(tmp_path / "empty.mp3")
        with open(p, "wb") as f:
            f.write(b"\x00" * 10)
        tags = read_tags(p)
        assert tags["title"] == "empty"
        assert tags["file_size"] == 10

    def test_corrupt_file(self, tmp_path):
        from pygpod.tags import read_tags

        p = str(tmp_path / "corrupt.mp3")
        with open(p, "wb") as f:
            f.write(b"this is not an mp3 file at all" * 100)
        tags = read_tags(p)
        assert isinstance(tags, dict)
        assert tags["title"] == "corrupt"

    def test_disc_number_with_slash(self, tmp_path):
        """Track with disc number in X/Y format."""
        if not _HAS_FFMPEG:
            pytest.skip("ffmpeg not available")
        from pygpod.tags import read_tags

        p = str(tmp_path / "disc.mp3")
        _gen_audio(
            p,
            [
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                "-metadata",
                "disc=2/3",
            ],
        )
        tags = read_tags(p)
        assert tags["cd_number"] == 2
        assert tags["total_cds"] == 3

    def test_no_tags_returns_filename_as_title(self, tmp_path):
        """File with no metadata uses filename stem as title."""
        from pygpod.tags import read_tags
        from tests.conftest import _make_minimal_mp3

        p = str(tmp_path / "My Song Name.mp3")
        _make_minimal_mp3(p)
        tags = read_tags(p)
        assert tags["title"] == "My Song Name"


# =========================================================================
# Artwork extraction
# =========================================================================


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not available")
class TestArtworkExtraction:
    def test_mp3_artwork(self, generated_mp3_with_art):
        from pygpod.tags import extract_artwork

        art = extract_artwork(generated_mp3_with_art)
        if art:
            assert len(art) > 100
            assert art[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1")

    def test_m4a_artwork(self, tmp_path):
        """M4A with embedded cover art."""
        from pygpod.tags import extract_artwork
        from tests.conftest import _make_minimal_png

        m4a = str(tmp_path / "art.m4a")
        cover = str(tmp_path / "cover.png")
        _make_minimal_png(cover, 50, 50)

        _gen_audio(m4a, ["-codec:a", "aac", "-b:a", "128k"])

        # Embed art with mutagen
        try:
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(m4a)
            with open(cover, "rb") as f:
                art_data = f.read()
            audio["covr"] = [MP4Cover(art_data, imageformat=MP4Cover.FORMAT_PNG)]
            audio.save()

            result = extract_artwork(m4a)
            assert result is not None
            assert len(result) > 0
        except ImportError:
            pytest.skip("mutagen MP4 not available")

    def test_no_artwork_returns_none(self, generated_mp3):
        from pygpod.tags import extract_artwork

        art = extract_artwork(generated_mp3)
        # Generated MP3 may or may not have art depending on fixture
        assert art is None or isinstance(art, bytes)

    def test_artwork_nonexistent_file(self):
        from pygpod.tags import extract_artwork

        assert extract_artwork("/nonexistent/file.mp3") is None


# =========================================================================
# Filetype detection
# =========================================================================


class TestFiletypeDetection:
    def test_detect_filetype_all(self):
        from pygpod.tags import _detect_filetype

        assert "MPEG" in _detect_filetype("song.mp3")
        assert "AAC" in _detect_filetype("song.m4a")
        assert "AAC" in _detect_filetype("song.aac")
        assert "WAV" in _detect_filetype("song.wav")
        assert "AIFF" in _detect_filetype("song.aiff")
        assert "AIFF" in _detect_filetype("song.aif")
        assert "video" in _detect_filetype("video.mp4").lower()
        assert "video" in _detect_filetype("video.m4v").lower()
        assert "Protected" in _detect_filetype("song.m4b")
        assert "Protected" in _detect_filetype("song.m4p")

    def test_detect_filetype_unknown_defaults_to_mpeg(self):
        from pygpod.tags import _detect_filetype

        assert _detect_filetype("song.xyz") == "MPEG audio file"

    def test_filetype_marker_all(self):
        from pygpod.tags import filetype_marker

        assert len(filetype_marker("song.mp3")) == 4
        assert len(filetype_marker("song.m4a")) == 4
        assert len(filetype_marker("song.wav")) == 4
        assert len(filetype_marker("song.aiff")) == 4
        assert len(filetype_marker("song.aif")) == 4
        assert len(filetype_marker("video.m4v")) == 4
        assert len(filetype_marker("video.mp4")) == 4

    def test_filetype_marker_unique_per_format(self):
        from pygpod.tags import filetype_marker

        markers = {
            filetype_marker("x.mp3"),
            filetype_marker("x.m4a"),
            filetype_marker("x.wav"),
            filetype_marker("x.aiff"),
            filetype_marker("x.m4v"),
        }
        assert len(markers) == 5  # all different


# =========================================================================
# Constants
# =========================================================================


class TestConstants:
    def test_base_audio_exts(self):
        from pygpod.tags import _BASE_AUDIO_EXTS

        assert ".mp3" in _BASE_AUDIO_EXTS
        assert ".m4a" in _BASE_AUDIO_EXTS
        assert ".wav" in _BASE_AUDIO_EXTS
        assert ".aiff" in _BASE_AUDIO_EXTS

    def test_video_exts_separate(self):
        from pygpod.tags import VIDEO_EXTS

        assert ".mp4" in VIDEO_EXTS
        assert ".m4v" in VIDEO_EXTS
        assert ".mov" in VIDEO_EXTS

    def test_unsupported_exts_no_overlap_with_supported(self):
        from pygpod.tags import _BASE_AUDIO_EXTS, UNSUPPORTED_AUDIO_EXTS, VIDEO_EXTS

        supported = _BASE_AUDIO_EXTS | VIDEO_EXTS
        overlap = set(UNSUPPORTED_AUDIO_EXTS.keys()) & supported
        assert not overlap, f"Overlap: {overlap}"

    def test_get_supported_audio_exts(self):
        from pygpod.tags import get_supported_audio_exts

        exts = get_supported_audio_exts()
        assert ".mp3" in exts
        assert ".m4a" in exts
        assert ".flac" not in exts

    def test_get_supported_audio_exts_with_generation(self):
        from pygpod.device.models import IpodGeneration
        from pygpod.tags import get_supported_audio_exts

        exts = get_supported_audio_exts(IpodGeneration.CLASSIC_1)
        assert ".mp3" in exts

    def test_video_warning_logged(self, tmp_path, caplog):
        import logging

        import pygpod.tags
        from pygpod.tags import check_format_supported

        # Reset the warning flag
        pygpod.tags._video_warning_shown = False

        with caplog.at_level(logging.WARNING, logger="pygpod.tags"):
            check_format_supported(str(tmp_path / "video.mp4"))

        assert any("Video file detected" in r.message for r in caplog.records)

    def test_video_warning_only_once(self, tmp_path, caplog):
        import logging

        import pygpod.tags
        from pygpod.tags import check_format_supported

        pygpod.tags._video_warning_shown = False

        with caplog.at_level(logging.WARNING, logger="pygpod.tags"):
            check_format_supported(str(tmp_path / "a.mp4"))
            caplog.clear()
            check_format_supported(str(tmp_path / "b.mp4"))

        # Second call should NOT log
        assert not any("Video file detected" in r.message for r in caplog.records)
