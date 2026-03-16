"""Tests for play count merge into iTunesDB + file deletion on load."""

import os
import struct


def _write_play_counts_file(itunes_dir, entries):
    """Write a binary Play Counts file with mhdp header."""
    entry_size = 28
    header_size = 96
    buf = b"mhdp"
    buf += struct.pack("<III", header_size, entry_size, len(entries))
    buf += b"\x00" * (header_size - 16)
    for e in entries:
        entry = bytearray(entry_size)
        struct.pack_into("<I", entry, 0, e.get("play_count", 0))
        struct.pack_into("<I", entry, 4, e.get("time_played", 0))
        struct.pack_into("<I", entry, 8, e.get("bookmark_time", 0))
        struct.pack_into("<I", entry, 12, e.get("rating", 0))
        struct.pack_into("<I", entry, 20, e.get("skip_count", 0))
        struct.pack_into("<I", entry, 24, e.get("time_skipped", 0))
        buf += bytes(entry)
    pc_path = os.path.join(itunes_dir, "Play Counts")
    with open(pc_path, "wb") as f:
        f.write(buf)
    return pc_path


class TestPlayCountMergeOnLoad:
    def test_play_counts_merged_into_tracks(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        n_tracks = len(db.tracks)
        assert n_tracks > 0

        # Get initial play counts
        initial_counts = [t.play_count for t in db.tracks]
        db.save()

        # Write Play Counts file with increments
        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        entries = []
        for i in range(n_tracks):
            entries.append({"play_count": 3, "rating": 80})
        pc_path = _write_play_counts_file(itunes_dir, entries)
        assert os.path.isfile(pc_path)

        # Reload - should merge play counts
        db2 = Database(writable_ipod)
        for i, t in enumerate(db2.tracks):
            assert t.play_count == initial_counts[i] + 3
            assert t.record.fields["rating"] == 80

    def test_play_counts_file_deleted_after_merge(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        entries = [{"play_count": 1}] * len(db.tracks)
        pc_path = _write_play_counts_file(itunes_dir, entries)
        assert os.path.isfile(pc_path)

        # Reload - should merge and delete
        Database(writable_ipod)
        assert not os.path.isfile(pc_path), "Play Counts file should be deleted after merge"

    def test_database_marked_modified_after_merge(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        entries = [{"play_count": 5}] * len(db.tracks)
        _write_play_counts_file(itunes_dir, entries)

        db2 = Database(writable_ipod)
        assert db2._modified is True

    def test_no_play_counts_file_no_change(self, writable_ipod):
        from pygpod.model.database import Database

        # Remove any existing Play Counts files
        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        for fname in ("Play Counts", "iTunesStats", "PlayCounts.plist"):
            fpath = os.path.join(itunes_dir, fname)
            if os.path.isfile(fpath):
                os.unlink(fpath)

        db = Database(writable_ipod)
        assert db._modified is False

    def test_raw_header_updated(self, writable_ipod):
        """Verify raw_header bytes are updated, not just fields dict."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        entries = [{"play_count": 7, "rating": 60}] * len(db.tracks)
        _write_play_counts_file(itunes_dir, entries)

        db2 = Database(writable_ipod)
        t = db2.tracks[0]

        # Check raw_header has the updated values
        header = t.record.raw_header
        pc_in_header = struct.unpack_from("<I", header, 0x50)[0]
        rating_in_header = header[0x1F]
        assert pc_in_header == t.play_count
        assert rating_in_header == 60

    def test_play_counts_survive_save_reload(self, writable_ipod):
        """Merged play counts persist through save + reload cycle."""
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        entries = [{"play_count": 10, "rating": 100}] * len(db.tracks)
        _write_play_counts_file(itunes_dir, entries)

        # Merge
        db2 = Database(writable_ipod)
        expected_pc = db2.tracks[0].play_count
        db2.save()

        # Reload after save - no Play Counts file, values should persist
        db3 = Database(writable_ipod)
        assert db3.tracks[0].play_count == expected_pc
        assert db3.tracks[0].record.fields["rating"] == 100

    def test_skip_count_and_bookmark(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        entries = [{"skip_count": 2, "bookmark_time": 45000}] * len(db.tracks)
        _write_play_counts_file(itunes_dir, entries)

        db2 = Database(writable_ipod)
        t = db2.tracks[0]
        assert t.record.fields["skip_count"] == 2
        assert t.record.fields["bookmark_time"] == 45000

    def test_itunesstats_also_deleted(self, writable_ipod):
        from pygpod.model.database import Database

        db = Database(writable_ipod)
        db.save()

        itunes_dir = os.path.join(writable_ipod, "iPod_Control", "iTunes")
        # Write iTunesStats instead of Play Counts
        stats = f"{len(db.tracks)}\n"
        for _ in db.tracks:
            stats += "2\n3700000000\n0\n0\n0\n\n"
        stats_path = os.path.join(itunes_dir, "iTunesStats")
        with open(stats_path, "w") as f:
            f.write(stats)
        assert os.path.isfile(stats_path)

        Database(writable_ipod)
        assert not os.path.isfile(stats_path), "iTunesStats should be deleted after merge"
