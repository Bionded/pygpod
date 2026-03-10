"""Basic pygpod usage - open a database and inspect tracks.

Opens an iPod database from a mount point, prints a summary,
and lists all tracks with their metadata.
"""

from __future__ import annotations

import argparse
import sys

import pygpod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List tracks on an iPod")
    parser.add_argument("mountpoint", help="iPod mount point (e.g. /mnt/ipod)")
    args = parser.parse_args(argv)

    db = pygpod.Database(args.mountpoint)

    # Print database summary
    print(db.summary())
    print()

    # List all tracks
    if not db.tracks:
        print("No tracks on this iPod.")
        return 0

    print(f"{'ID':>5}  {'Artist':<25} {'Title':<30} {'Album':<25} {'Duration':>8}")
    print("-" * 97)

    for track in db.tracks:
        mins, secs = divmod(track.duration_ms // 1000, 60)
        print(
            f"{track.track_id:>5}  "
            f"{track.artist[:25]:<25} "
            f"{track.title[:30]:<30} "
            f"{track.album[:25]:<25} "
            f"{mins:>2}:{secs:02d}"
        )

    print(f"\nTotal: {len(db.tracks)} tracks")

    # Show track details for the first track
    if db.tracks:
        t = db.tracks[0]
        print(f"\n--- Details for track {t.track_id} ---")
        print(f"  Title:       {t.title}")
        print(f"  Artist:      {t.artist}")
        print(f"  Album:       {t.album}")
        print(f"  Genre:       {t.genre}")
        print(f"  Year:        {t.year}")
        print(f"  Duration:    {t.duration:.1f}s")
        print(f"  Bitrate:     {t.bitrate} kbps")
        print(f"  Sample rate: {t.samplerate} Hz")
        print(f"  File size:   {t.file_size} bytes")
        print(f"  Play count:  {t.play_count}")
        print(f"  Rating:      {'*' * t.rating_stars} ({t.rating}/100)")
        print(f"  iPod path:   {t.ipod_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
