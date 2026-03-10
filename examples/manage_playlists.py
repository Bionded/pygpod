"""Manage playlists on an iPod.

Demonstrates creating playlists, adding/removing tracks, and
listing all playlists with their contents.
"""

from __future__ import annotations

import argparse
import sys

import pygpod


def cmd_list(db: pygpod.Database) -> int:
    """List all playlists and their tracks."""
    for pl in db.playlists:
        marker = " [Master]" if pl.is_master else ""
        print(f"\n{pl.name}{marker} ({pl.track_count} tracks)")
        for track in pl.tracks:
            print(f"    {track.track_id:>5}  {track.artist} - {track.title}")
    return 0


def cmd_create(db: pygpod.Database, name: str) -> int:
    """Create a new playlist."""
    pl = db.create_playlist(name)
    db.save()
    print(f"Created playlist: {pl.name}")
    return 0


def cmd_add(db: pygpod.Database, playlist_name: str, track_ids: list[int]) -> int:
    """Add tracks to an existing playlist."""
    pl = None
    for p in db.playlists:
        if p.name == playlist_name:
            pl = p
            break
    if pl is None:
        print(f"Playlist '{playlist_name}' not found.", file=sys.stderr)
        return 1

    for tid in track_ids:
        track = db.get_track(tid)
        if track:
            db.add_track_to_playlist(pl, track)
            print(f"  Added track {tid}: {track.title}")
        else:
            print(f"  Track {tid} not found.", file=sys.stderr)

    db.save()
    return 0


def cmd_remove(db: pygpod.Database, playlist_name: str, track_ids: list[int]) -> int:
    """Remove tracks from a playlist."""
    pl = None
    for p in db.playlists:
        if p.name == playlist_name:
            pl = p
            break
    if pl is None:
        print(f"Playlist '{playlist_name}' not found.", file=sys.stderr)
        return 1

    for tid in track_ids:
        track = db.get_track(tid)
        if track:
            db.remove_track_from_playlist(pl, track)
            print(f"  Removed track {tid} from {pl.name}")
        else:
            print(f"  Track {tid} not found.", file=sys.stderr)

    db.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage iPod playlists")
    parser.add_argument("mountpoint", help="iPod mount point")
    sub = parser.add_subparsers(dest="action")

    sub.add_parser("list", help="List all playlists")

    p_create = sub.add_parser("create", help="Create a playlist")
    p_create.add_argument("name", help="Playlist name")

    p_add = sub.add_parser("add", help="Add tracks to a playlist")
    p_add.add_argument("playlist", help="Playlist name")
    p_add.add_argument("track_ids", nargs="+", type=int, help="Track IDs")

    p_rm = sub.add_parser("remove", help="Remove tracks from a playlist")
    p_rm.add_argument("playlist", help="Playlist name")
    p_rm.add_argument("track_ids", nargs="+", type=int, help="Track IDs")

    args = parser.parse_args(argv)

    if args.action is None:
        parser.print_help()
        return 0

    db = pygpod.Database(args.mountpoint)

    if args.action == "list":
        return cmd_list(db)
    elif args.action == "create":
        return cmd_create(db, args.name)
    elif args.action == "add":
        return cmd_add(db, args.playlist, args.track_ids)
    elif args.action == "remove":
        return cmd_remove(db, args.playlist, args.track_ids)

    return 0


if __name__ == "__main__":
    sys.exit(main())
