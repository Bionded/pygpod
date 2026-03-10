"""Add audio files to an iPod.

Copies audio files to the iPod, reads tags automatically (if mutagen
is installed), and allows metadata overrides via command-line flags.
"""

from __future__ import annotations

import argparse
import sys

import pygpod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add audio files to an iPod")
    parser.add_argument("mountpoint", help="iPod mount point")
    parser.add_argument("files", nargs="+", help="Audio files to add")
    parser.add_argument("--artist", help="Override artist for all files")
    parser.add_argument("--album", help="Override album for all files")
    parser.add_argument("--genre", help="Override genre for all files")
    args = parser.parse_args(argv)

    db = pygpod.Database(args.mountpoint)
    print(f"Opened iPod: {len(db.tracks)} existing tracks")

    # Build metadata overrides from CLI flags
    overrides = {}
    if args.artist:
        overrides["artist"] = args.artist
    if args.album:
        overrides["album"] = args.album
    if args.genre:
        overrides["genre"] = args.genre

    added = []
    for filepath in args.files:
        try:
            track = db.add_track(filepath, **overrides)
            added.append(track)
            print(f"  Added: {track}")
        except pygpod.TrackError as e:
            print(f"  Error adding {filepath}: {e}", file=sys.stderr)

    if added:
        db.save()
        print(f"\nSaved. {len(added)} track(s) added, {len(db.tracks)} total.")
    else:
        print("\nNo tracks were added.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
