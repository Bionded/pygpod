"""Search and export tracks from an iPod to a local directory.

Supports filtering by artist, album, genre, or title substring.
Copies matching tracks from the iPod filesystem to a destination directory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

import pygpod
from pygpod.utils.encoding import ipod_path_to_os


def find_tracks(
    tracks: list[pygpod.Track],
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    title: str | None = None,
) -> list[pygpod.Track]:
    """Filter tracks by metadata substring (case-insensitive)."""
    result = tracks
    if artist:
        artist_lower = artist.lower()
        result = [t for t in result if artist_lower in t.artist.lower()]
    if album:
        album_lower = album.lower()
        result = [t for t in result if album_lower in t.album.lower()]
    if genre:
        genre_lower = genre.lower()
        result = [t for t in result if genre_lower in t.genre.lower()]
    if title:
        title_lower = title.lower()
        result = [t for t in result if title_lower in t.title.lower()]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export tracks from an iPod")
    parser.add_argument("mountpoint", help="iPod mount point")
    parser.add_argument("destination", help="Local directory to export to")
    parser.add_argument("--artist", help="Filter by artist")
    parser.add_argument("--album", help="Filter by album")
    parser.add_argument("--genre", help="Filter by genre")
    parser.add_argument("--title", help="Filter by title")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be exported")
    args = parser.parse_args(argv)

    db = pygpod.Database(args.mountpoint)
    matches = find_tracks(db.tracks, args.artist, args.album, args.genre, args.title)

    if not matches:
        print("No matching tracks found.")
        return 0

    print(f"Found {len(matches)} matching track(s):")
    for t in matches:
        print(f"  {t.track_id:>5}  {t.artist} - {t.title}")

    if args.dry_run:
        return 0

    os.makedirs(args.destination, exist_ok=True)
    exported = 0
    for track in matches:
        if not track.ipod_path:
            print(f"  Skipping track {track.track_id} (no iPod path)", file=sys.stderr)
            continue

        src = ipod_path_to_os(track.ipod_path, args.mountpoint)
        if not os.path.isfile(src):
            print(f"  File not found: {src}", file=sys.stderr)
            continue

        # Build a readable filename: "Artist - Title.ext"
        ext = os.path.splitext(src)[1]
        safe_artist = track.artist.replace("/", "_") or "Unknown"
        safe_title = track.title.replace("/", "_") or "Untitled"
        dest_name = f"{safe_artist} - {safe_title}{ext}"
        dest_path = os.path.join(args.destination, dest_name)

        shutil.copy2(src, dest_path)
        exported += 1
        print(f"  Exported: {dest_name}")

    print(f"\n{exported} track(s) exported to {args.destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
