"""CLI entry point for pygpod.

Provides command-line interface for iPod database operations.
Uses argparse (stdlib) to avoid extra dependencies.

Supports PYGPOD_MOUNTPOINT environment variable for default mount point.

Command structure:
  pygpod info
  pygpod track list               (aliases: tr ls)
  pygpod track add file1 file2    (aliases: tr add)
  pygpod track remove id          (aliases: tr rm)
  pygpod track export id dest
  pygpod track info id
  pygpod playlist list             (aliases: pl ls)
  pygpod playlist create name      (aliases: pl create)
  pygpod playlist add name id1 id2 (aliases: pl add)
  pygpod playlist remove name id   (aliases: pl rm)
  pygpod playlist delete name
  pygpod init mountpoint
  pygpod dump
  pygpod discover
  pygpod fix-checksums
  pygpod purge

Mount point resolution (in order):
  1. -m / --mountpoint flag (global or per-subcommand)
  2. PYGPOD_MOUNTPOINT environment variable
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

logger = logging.getLogger(__name__)

ENV_MOUNTPOINT = "PYGPOD_MOUNTPOINT"

# Media type name -> constant mapping
_MEDIA_TYPES = {
    "audio": 0x0001,
    "video": 0x0002,
    "podcast": 0x0004,
    "audiobook": 0x0008,
    "musicvideo": 0x0020,
    "tvshow": 0x0040,
}

# Reverse: constant -> display name
_MEDIA_TYPE_NAMES = {v: k for k, v in _MEDIA_TYPES.items()}


def _media_type_name(media_type: int) -> str:
    """Get human-readable media type name."""
    # Check exact match first, then check bitwise
    name = _MEDIA_TYPE_NAMES.get(media_type)
    if name:
        return name
    parts = []
    for val, n in _MEDIA_TYPE_NAMES.items():
        if media_type & val:
            parts.append(n)
    return "+".join(parts) if parts else "audio"


def _get_mountpoint(args: argparse.Namespace) -> str:
    """Resolve mountpoint from args or environment."""
    mp = getattr(args, "mountpoint", None)
    if mp:
        return mp
    mp = os.environ.get(ENV_MOUNTPOINT)
    if mp:
        return mp
    print(
        f"Error: mount point required. Use -m flag or set {ENV_MOUNTPOINT}.",
        file=sys.stderr,
    )
    sys.exit(1)


def _add_mountpoint_opt(parser: argparse.ArgumentParser) -> None:
    """Add -m/--mountpoint option with env var fallback."""
    parser.add_argument(
        "-m",
        "--mountpoint",
        default=None,
        help=f"iPod mount point (default: ${ENV_MOUNTPOINT})",
    )


def _add_tag_options(parser: argparse.ArgumentParser) -> None:
    """Add track metadata override options to a parser."""
    g = parser.add_argument_group("metadata overrides")
    g.add_argument("--title", help="Track title")
    g.add_argument("--artist", help="Artist name")
    g.add_argument("--album", help="Album name")
    g.add_argument("--genre", help="Genre")
    g.add_argument("--composer", help="Composer")
    g.add_argument("--comment", help="Comment")
    g.add_argument("--albumartist", help="Album artist")
    g.add_argument("--grouping", help="Grouping")
    g.add_argument("--year", type=int, help="Year")
    g.add_argument("--track-number", type=int, help="Track number")
    g.add_argument("--total-tracks", type=int, help="Total tracks in album")
    g.add_argument("--cd-number", type=int, help="Disc number")
    g.add_argument("--total-cds", type=int, help="Total discs")

    g = parser.add_argument_group("media type")
    g.add_argument(
        "--type",
        dest="media_type",
        choices=list(_MEDIA_TYPES.keys()),
        help="Media type (default: auto-detect or audio)",
    )

    g = parser.add_argument_group("podcast/video metadata")
    g.add_argument("--category", help="Category (e.g. News, Sports)")
    g.add_argument("--description", help="Episode description")
    g.add_argument("--subtitle", help="Subtitle")
    g.add_argument("--keywords", help="Keywords/tags")
    g.add_argument("--podcast-url", help="Podcast episode URL")
    g.add_argument("--podcast-rss", help="Podcast feed RSS URL")
    g.add_argument("--tvshow", help="TV show name")
    g.add_argument("--tvepisode", help="TV episode identifier")
    g.add_argument("--tvnetwork", help="TV network name")
    g.add_argument("--season-number", type=int, help="Season number")
    g.add_argument("--episode-number", type=int, help="Episode number")


def _collect_tag_overrides(args: argparse.Namespace) -> dict:
    """Collect non-None tag overrides from args into a dict for add_track()."""
    tag_keys = [
        "title",
        "artist",
        "album",
        "genre",
        "composer",
        "comment",
        "albumartist",
        "grouping",
        "year",
        "track_number",
        "total_tracks",
        "cd_number",
        "total_cds",
        "category",
        "description",
        "subtitle",
        "keywords",
        "podcast_url",
        "podcast_rss",
        "tvshow",
        "tvepisode",
        "tvnetwork",
        "season_number",
        "episode_number",
    ]
    overrides = {}
    for key in tag_keys:
        val = getattr(args, key, None)
        if val is not None:
            overrides[key] = val
    if args.media_type:
        overrides["media_type"] = _MEDIA_TYPES[args.media_type]
    return overrides


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="pygpod",
        description="pygpod - Python iPod database manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "environment:\n"
            f"  {ENV_MOUNTPOINT}    Default iPod mount point\n"
            "\n"
            "aliases:\n"
            "  i = info, tr = track, pl = playlist, ls = list, rm = remove\n"
            "\n"
            "examples:\n"
            "\n"
            "  Setup & info:\n"
            "    pygpod -m /mnt/ipod init                     Initialize a new iPod\n"
            "    pygpod -m /mnt/ipod init --music-dirs 10     Init with 10 music dirs\n"
            "    export PYGPOD_MOUNTPOINT=/mnt/ipod           Set default mountpoint\n"
            "    pygpod info                                  Show device & track summary\n"
            "\n"
            "  Adding music:\n"
            "    pygpod tr add song.mp3                       Add a track (tags auto-read)\n"
            "    pygpod tr add *.mp3                          Add multiple files at once\n"
            "    pygpod tr add song.mp3 --artist 'Beatles' --album 'Abbey Road'\n"
            "    pygpod tr add song.mp3 --title 'Hey Jude' --genre Rock --year 1968\n"
            "    pygpod tr add song.mp3 --track-number 1 --total-tracks 12\n"
            "\n"
            "  Podcasts (auto-creates playlist by category):\n"
            "    pygpod tr add ep01.mp3 --type podcast --category 'Tech News'\n"
            "      -> creates 'Tech News' podcast playlist, adds track to it\n"
            "    pygpod tr add ep02.mp3 --type podcast --category 'Tech News'\n"
            "      -> adds to existing 'Tech News' playlist automatically\n"
            "    pygpod tr add ep.mp3 --type podcast\n"
            "      -> adds to default 'Podcasts' playlist\n"
            "    pygpod tr add ep.mp3 --type podcast --description 'Episode about AI'\n"
            "\n"
            "  Video & TV shows:\n"
            "    pygpod tr add clip.m4v --type video\n"
            "    pygpod tr add episode.m4v --type tvshow --tvshow 'Breaking Bad' \\\n"
            "        --season-number 1 --episode-number 1\n"
            "    pygpod tr add clip.m4v --type musicvideo --artist 'Radiohead'\n"
            "\n"
            "  Audiobooks:\n"
            "    pygpod tr add chapter1.m4b --type audiobook --title 'Chapter 1'\n"
            "\n"
            "  Browsing & filtering tracks:\n"
            "    pygpod tr ls                                 List all tracks\n"
            "    pygpod tr ls --type podcast                  List podcasts only\n"
            "    pygpod tr ls --genre Jazz                    Filter by genre\n"
            "    pygpod tr ls --artist Beatles                Filter by artist\n"
            "    pygpod tr ls --category 'Tech News'          Filter by category\n"
            "    pygpod tr info 52                            Show full details for track 52\n"
            "\n"
            "  Managing tracks:\n"
            "    pygpod tr rm 52                              Remove track from database\n"
            "    pygpod tr rm 52 --delete-file                Also delete file from iPod\n"
            "    pygpod tr export 52 ~/Music/song.mp3         Copy track off iPod\n"
            "\n"
            "  Playlists:\n"
            "    pygpod pl ls                                 List all playlists\n"
            "    pygpod pl ls --podcast                       List podcast playlists only\n"
            "    pygpod pl create 'Road Trip'                 Create a normal playlist\n"
            "    pygpod pl create 'My Podcast' --podcast      Create a podcast playlist\n"
            "    pygpod pl add 'Road Trip' 52 53 54           Add tracks to playlist\n"
            "    pygpod pl rm 'Road Trip' 52                  Remove track from playlist\n"
            "    pygpod pl delete 'Road Trip'                 Delete entire playlist\n"
            "\n"
            "  Maintenance:\n"
            "    pygpod fix-checksums                         Recalculate DB checksums\n"
            "    pygpod dump                                  Dump raw DB structure\n"
            "    pygpod purge --yes                           Wipe all data from iPod\n"
            "    pygpod discover                              Auto-detect connected iPods"
        ),
    )
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug logging")
    _add_mountpoint_opt(parser)
    subs = parser.add_subparsers(dest="command", help="command")

    # === info ===
    subs.add_parser("info", aliases=["i"], help="Show iPod info and playlists")

    # === track ===
    p_track = subs.add_parser("track", aliases=["tr"], help="Track operations")
    track_subs = p_track.add_subparsers(dest="subcommand", help="track subcommand")

    p_tr_list = track_subs.add_parser("list", aliases=["ls"], help="List tracks")
    p_tr_list.add_argument(
        "--type",
        dest="filter_type",
        choices=list(_MEDIA_TYPES.keys()),
        help="Filter by media type",
    )
    p_tr_list.add_argument("--genre", dest="filter_genre", help="Filter by genre")
    p_tr_list.add_argument("--artist", dest="filter_artist", help="Filter by artist")
    p_tr_list.add_argument(
        "--category", dest="filter_category", help="Filter by category (podcasts)"
    )

    p_tr_add = track_subs.add_parser("add", help="Add track(s) to iPod")
    p_tr_add.add_argument("files", nargs="+", help="Audio/video files to add")
    _add_tag_options(p_tr_add)

    p_tr_rm = track_subs.add_parser("remove", aliases=["rm"], help="Remove track by ID")
    p_tr_rm.add_argument("track_id", type=int, help="Track ID to remove")
    p_tr_rm.add_argument("--delete-file", action="store_true", help="Also delete file from iPod")

    p_tr_export = track_subs.add_parser("export", help="Export track from iPod")
    p_tr_export.add_argument("track_id", type=int, help="Track ID to export")
    p_tr_export.add_argument("destination", help="Destination path")

    p_tr_info = track_subs.add_parser("info", help="Show full track details")
    p_tr_info.add_argument("track_id", type=int, help="Track ID")

    # === playlist ===
    p_pl = subs.add_parser("playlist", aliases=["pl"], help="Playlist operations")
    pl_subs = p_pl.add_subparsers(dest="subcommand", help="playlist subcommand")

    p_pl_list = pl_subs.add_parser("list", aliases=["ls"], help="List playlists")
    p_pl_list.add_argument("--podcast", action="store_true", help="Show only podcast playlists")

    p_pl_create = pl_subs.add_parser("create", help="Create a playlist")
    p_pl_create.add_argument("name", help="Playlist name")
    p_pl_create.add_argument("--podcast", action="store_true", help="Create as podcast playlist")

    p_pl_add = pl_subs.add_parser("add", help="Add tracks to playlist")
    p_pl_add.add_argument("playlist", help="Playlist name")
    p_pl_add.add_argument("track_ids", nargs="+", type=int, help="Track IDs")

    p_pl_rm = pl_subs.add_parser("remove", aliases=["rm"], help="Remove track from playlist")
    p_pl_rm.add_argument("playlist", help="Playlist name")
    p_pl_rm.add_argument("track_id", type=int, help="Track ID")

    p_pl_del = pl_subs.add_parser("delete", help="Delete a playlist")
    p_pl_del.add_argument("name", help="Playlist name to delete")

    # === dump ===
    subs.add_parser("dump", help="Dump raw iTunesDB structure")

    # === init ===
    p_init = subs.add_parser("init", help="Initialize iPod directory structure")
    p_init.add_argument("--model", default="classic_6g", help="iPod model (default: classic_6g)")
    p_init.add_argument(
        "--music-dirs", type=int, default=50, help="Number of Fxx music dirs (default: 50)"
    )

    # === fix-checksums ===
    subs.add_parser("fix-checksums", help="Recalculate database checksums")

    # === purge ===
    p_purge = subs.add_parser("purge", help="Delete all data from iPod")
    p_purge.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # === discover ===
    subs.add_parser("discover", help="Auto-detect connected iPods")

    args = parser.parse_args(argv)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        return _dispatch(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.debug("Command failed", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    """Dispatch to command handler."""
    cmd = args.command

    if cmd in ("info", "i"):
        return _cmd_info(args)
    elif cmd in ("track", "tr"):
        return _dispatch_track(args)
    elif cmd in ("playlist", "pl"):
        return _dispatch_playlist(args)
    elif cmd == "dump":
        return _cmd_dump(args)
    elif cmd == "init":
        return _cmd_init(args)
    elif cmd == "fix-checksums":
        return _cmd_fix_checksums(args)
    elif cmd == "purge":
        return _cmd_purge(args)
    elif cmd == "discover":
        return _cmd_discover(args)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


def _dispatch_track(args: argparse.Namespace) -> int:
    sub = getattr(args, "subcommand", None)
    if sub in ("list", "ls"):
        return _cmd_track_list(args)
    elif sub == "add":
        return _cmd_track_add(args)
    elif sub in ("remove", "rm"):
        return _cmd_track_remove(args)
    elif sub == "export":
        return _cmd_track_export(args)
    elif sub == "info":
        return _cmd_track_info(args)
    else:
        print("Usage: pygpod track {list|add|remove|export|info}", file=sys.stderr)
        return 1


def _dispatch_playlist(args: argparse.Namespace) -> int:
    sub = getattr(args, "subcommand", None)
    if sub in ("list", "ls"):
        return _cmd_playlist_list(args)
    elif sub == "create":
        return _cmd_playlist_create(args)
    elif sub == "add":
        return _cmd_playlist_add(args)
    elif sub in ("remove", "rm"):
        return _cmd_playlist_remove(args)
    elif sub == "delete":
        return _cmd_playlist_delete(args)
    else:
        print("Usage: pygpod playlist {list|create|add|remove|delete}", file=sys.stderr)
        return 1


# ============================================================================
# info
# ============================================================================
def _cmd_info(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)

    if db._device:
        print(f"Device:       {db._device.model}")
        if db._device.firewire_guid:
            print(f"GUID:         {db._device.firewire_guid}")
        print(f"Hash:         {db._device.checksum_type.name}")
    if db._root:
        print(f"DB version:   {db._root.fields.get('db_version', '?')}")

    # Count by media type
    type_counts = {}
    for t in db.tracks:
        name = _media_type_name(t.media_type)
        type_counts[name] = type_counts.get(name, 0) + 1

    print(f"Tracks:       {len(db.tracks)}")
    if len(type_counts) > 1 or (type_counts and "audio" not in type_counts):
        for name, count in sorted(type_counts.items()):
            print(f"  {name}: {count}")

    print(f"Playlists:    {len(db.playlists)}")

    if db.playlists:
        print()
        for pl in db.playlists:
            prefix = "*" if pl.is_master else "P" if pl.is_podcast else " "
            print(f"  {prefix} {pl.name:<30s}  {pl.track_count} tracks")

    import pathlib

    art_dir = pathlib.Path(mp) / "iPod_Control" / "Artwork"
    if art_dir.exists():
        art_size = sum(f.stat().st_size for f in art_dir.iterdir() if f.is_file())
        if art_size > 0:
            print(f"\nArtwork:      {art_size / 1024 / 1024:.1f} MB")

    music_dir = pathlib.Path(mp) / "iPod_Control" / "Music"
    if music_dir.exists():
        music_size = sum(f.stat().st_size for f in music_dir.rglob("*") if f.is_file())
        if music_size > 0:
            print(f"Music:        {music_size / 1024 / 1024:.1f} MB")

    return 0


# ============================================================================
# track commands
# ============================================================================
def _cmd_track_list(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)

    tracks = db.tracks

    # Apply filters
    filter_type = getattr(args, "filter_type", None)
    if filter_type:
        type_val = _MEDIA_TYPES[filter_type]
        tracks = [t for t in tracks if t.media_type & type_val]
    filter_genre = getattr(args, "filter_genre", None)
    if filter_genre:
        gl = filter_genre.lower()
        tracks = [t for t in tracks if gl in t.genre.lower()]
    filter_artist = getattr(args, "filter_artist", None)
    if filter_artist:
        al = filter_artist.lower()
        tracks = [t for t in tracks if al in t.artist.lower()]
    filter_category = getattr(args, "filter_category", None)
    if filter_category:
        cl = filter_category.lower()
        tracks = [t for t in tracks if cl in t.category.lower()]

    if not tracks:
        print("No tracks found.")
        return 0

    # Detect if mixed media types to show type column
    has_non_audio = any(t.media_type != 0x0001 for t in tracks)

    if has_non_audio:
        print(
            f"{'ID':>5}  {'Type':<10} {'Artist':<20} {'Title':<30} "
            f"{'Album':<20} {'Genre':<15} {'Time':>6}"
        )
        print("-" * 110)
        for t in tracks:
            mins = t.duration_ms // 60000
            secs = (t.duration_ms % 60000) // 1000
            mtype = _media_type_name(t.media_type)
            print(
                f"{t.track_id:>5}  {mtype:<10} {t.artist[:20]:<20} "
                f"{t.title[:30]:<30} {t.album[:20]:<20} "
                f"{t.genre[:15]:<15} {mins:>2}:{secs:02d}"
            )
    else:
        print(f"{'ID':>5}  {'Artist':<25} {'Title':<35} {'Album':<25} {'Time':>8}")
        print("-" * 100)
        for t in tracks:
            mins = t.duration_ms // 60000
            secs = (t.duration_ms % 60000) // 1000
            print(
                f"{t.track_id:>5}  {t.artist[:25]:<25} {t.title[:35]:<35} "
                f"{t.album[:25]:<25} {mins:>2}:{secs:02d}"
            )
    print(f"\nTotal: {len(tracks)} tracks")
    return 0


def _cmd_track_add(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    overrides = _collect_tag_overrides(args)
    for filepath in args.files:
        playlists_before = set(p.name for p in db.playlists)
        track = db.add_track(filepath, **overrides)
        mtype = _media_type_name(track.media_type)
        print(f"Added [{mtype}]: {track}")
        # Show podcast playlist auto-assignment
        if track.media_type & _MEDIA_TYPES["podcast"]:
            if "Podcasts" not in playlists_before:
                print("  -> Created Podcasts playlist")
                playlists_before.add("Podcasts")
            print("  -> Added to Podcasts playlist")
    db.save()
    print(f"Database saved. Total tracks: {len(db.tracks)}")
    return 0


def _cmd_track_remove(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    track = db.get_track(args.track_id)
    if not track:
        print(f"Track ID {args.track_id} not found.", file=sys.stderr)
        return 1
    print(f"Removing: {track}")
    db.remove_track(track, delete_file=args.delete_file)
    db.save()
    return 0


def _cmd_track_export(args: argparse.Namespace) -> int:
    import shutil

    from .model.database import Database
    from .utils.encoding import ipod_path_to_os

    mp = _get_mountpoint(args)
    db = Database(mp)
    track = db.get_track(args.track_id)
    if not track:
        print(f"Track ID {args.track_id} not found.", file=sys.stderr)
        return 1
    src = ipod_path_to_os(track.ipod_path, mp)
    shutil.copy2(src, args.destination)
    print(f"Exported: {track} -> {args.destination}")
    return 0


def _cmd_track_info(args: argparse.Namespace) -> int:
    """Show detailed info for a single track."""
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    t = db.get_track(args.track_id)
    if not t:
        print(f"Track ID {args.track_id} not found.", file=sys.stderr)
        return 1

    mins = t.duration_ms // 60000
    secs = (t.duration_ms % 60000) // 1000

    print(f"Track ID:       {t.track_id}")
    print(f"Title:          {t.title}")
    print(f"Artist:         {t.artist}")
    print(f"Album:          {t.album}")
    if t.albumartist:
        print(f"Album Artist:   {t.albumartist}")
    print(f"Genre:          {t.genre}")
    if t.composer:
        print(f"Composer:       {t.composer}")
    if t.comment:
        print(f"Comment:        {t.comment}")
    if t.grouping:
        print(f"Grouping:       {t.grouping}")
    print(f"Duration:       {mins}:{secs:02d}")
    print(f"Year:           {t.year}")
    if t.track_number:
        tn = f"{t.track_number}"
        if t.total_tracks:
            tn += f"/{t.total_tracks}"
        print(f"Track:          {tn}")
    if t.cd_number:
        cn = f"{t.cd_number}"
        if t.total_cds:
            cn += f"/{t.total_cds}"
        print(f"Disc:           {cn}")
    print(f"Bitrate:        {t.bitrate} kbps")
    print(f"Sample Rate:    {t.samplerate} Hz")
    print(f"File Size:      {t.file_size / 1024:.0f} KB")
    print(f"Media Type:     {_media_type_name(t.media_type)}")
    print(f"Rating:         {'*' * t.rating_stars} ({t.rating}/100)")
    print(f"Play Count:     {t.play_count}")

    # Podcast/video fields
    if t.category:
        print(f"Category:       {t.category}")
    if t.description:
        print(f"Description:    {t.description}")
    if t.subtitle:
        print(f"Subtitle:       {t.subtitle}")
    if t.keywords:
        print(f"Keywords:       {t.keywords}")
    if t.podcast_url:
        print(f"Podcast URL:    {t.podcast_url}")
    if t.podcast_rss:
        print(f"Podcast RSS:    {t.podcast_rss}")
    if t.tvshow:
        print(f"TV Show:        {t.tvshow}")
    if t.tvepisode:
        print(f"TV Episode:     {t.tvepisode}")
    if t.tvnetwork:
        print(f"TV Network:     {t.tvnetwork}")
    if t.season_number:
        print(f"Season:         {t.season_number}")
    if t.episode_number:
        print(f"Episode:        {t.episode_number}")

    # Playback flags
    if t.is_podcast or t.is_audiobook:
        print(f"Remember Pos:   {t.remember_position}")
        print(f"Skip Shuffle:   {t.skip_when_shuffling}")
        print(f"Bookmark:       {t.bookmark_time} ms")
    if t.compilation:
        print("Compilation:    yes")
    if t.has_artwork:
        print(f"Artwork:        yes ({t.artwork_count} image(s))")

    print(f"Added:          {t.time_added}")
    print(f"Path:           {t.ipod_path}")

    return 0


# ============================================================================
# playlist commands
# ============================================================================
def _cmd_playlist_list(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    playlists = db.playlists

    filter_podcast = getattr(args, "podcast", False)
    if filter_podcast:
        playlists = [p for p in playlists if p.is_podcast]

    if not playlists:
        print("No playlists found.")
        return 0
    for pl in playlists:
        prefix = "*" if pl.is_master else "P" if pl.is_podcast else " "
        smart = " [smart]" if pl.is_smart else ""
        print(f"  {prefix} {pl.name:<30s}  {pl.track_count} tracks{smart}")
    return 0


def _cmd_playlist_create(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    is_podcast = getattr(args, "podcast", False)
    pl = db.create_playlist(args.name, podcast=is_podcast)
    db.save()
    kind = "podcast playlist" if is_podcast else "playlist"
    print(f"Created {kind}: {pl.name}")
    return 0


def _find_playlist(db, name: str):
    """Find a playlist by name, or print error and return None."""
    for p in db.playlists:
        if p.name == name:
            return p
    print(f"Playlist '{name}' not found.", file=sys.stderr)
    return None


def _cmd_playlist_add(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    pl = _find_playlist(db, args.playlist)
    if pl is None:
        return 1
    for tid in args.track_ids:
        track = db.get_track(tid)
        if track:
            db.add_track_to_playlist(pl, track)
            print(f"  Added track {tid} to {pl.name}")
        else:
            print(f"  Track {tid} not found", file=sys.stderr)
    db.save()
    return 0


def _cmd_playlist_remove(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    pl = _find_playlist(db, args.playlist)
    if pl is None:
        return 1
    track = db.get_track(args.track_id)
    if not track:
        print(f"Track {args.track_id} not found.", file=sys.stderr)
        return 1
    db.remove_track_from_playlist(pl, track)
    db.save()
    print(f"Removed track {args.track_id} from {pl.name}")
    return 0


def _cmd_playlist_delete(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    pl = _find_playlist(db, args.name)
    if pl is None:
        return 1
    if pl.is_master:
        print("Cannot delete the master playlist.", file=sys.stderr)
        return 1
    db.delete_playlist(pl)
    db.save()
    print(f"Deleted playlist: {args.name}")
    return 0


# ============================================================================
# standalone commands
# ============================================================================
def _cmd_dump(args: argparse.Namespace) -> int:
    import os

    from .db.parser import parse_itunesdb
    from .device.mountpoint import find_itunesdb

    mp = _get_mountpoint(args)
    if os.path.isfile(mp):
        db_path = mp
    else:
        db_path = find_itunesdb(mp)
        if not db_path:
            print(f"iTunesDB not found at {mp}", file=sys.stderr)
            return 1
        db_path = str(db_path)

    with open(db_path, "rb") as f:
        data = f.read()

    root = parse_itunesdb(data)
    _dump_record(root, 0)
    return 0


def _dump_record(rec, depth: int) -> None:
    """Recursively dump a record tree."""
    indent = "  " * depth
    magic = rec.magic.decode("ascii", errors="replace")
    extra = ""
    if "mhsd_type" in rec.fields:
        extra = f" type={rec.fields['mhsd_type']}"
    elif "mhod_type" in rec.fields:
        from .db.constants import MHOD_TYPE_NAMES

        mtype = rec.fields["mhod_type"]
        name = MHOD_TYPE_NAMES.get(mtype, "?")
        val = rec.fields.get("string", "")
        if val:
            extra = f' type={mtype}({name}) = "{val}"'
        else:
            extra = f" type={mtype}({name})"
    elif "track_id" in rec.fields and rec.magic == b"mhit":
        extra = f" id={rec.fields['track_id']}"
    elif "playlist_type" in rec.fields:
        extra = f" master={rec.fields['playlist_type']}"
    elif "num_tracks" in rec.fields:
        extra = f" count={rec.fields['num_tracks']}"
    elif "num_playlists" in rec.fields:
        extra = f" count={rec.fields['num_playlists']}"

    print(f"{indent}{magic} header={rec.header_len} total={rec.total_len}{extra}")

    for child in rec.children:
        _dump_record(child, depth + 1)


def _cmd_init(args: argparse.Namespace) -> int:
    from .device.device import Device
    from .device.mountpoint import init_ipod

    mp = _get_mountpoint(args)
    init_ipod(mp, model=args.model, music_dirs=args.music_dirs)
    print(f"Initialized iPod at {mp}")
    print(f"  Directory structure created ({args.music_dirs} music dirs)")
    print("  Empty iTunesDB created with master playlist")

    try:
        device = Device.from_mountpoint(mp)
        if not device.is_unknown and not device.has_sysinfo:
            path = device.write_sysinfo()
            print(f"  SysInfo written: {path}")
            if device.firewire_guid:
                print(f"  FirewireGuid: {device.firewire_guid}")
            if device.requires_hash:
                print(f"  Checksum type: {device.checksum_type.name}")
    except Exception:
        pass

    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    import shutil

    from .device.mountpoint import validate_mountpoint

    mp = _get_mountpoint(args)

    if not validate_mountpoint(mp):
        print(f"Not a valid iPod mount point: {mp}", file=sys.stderr)
        return 1

    if not args.yes:
        print(f"WARNING: This will delete ALL data from the iPod at {mp}")
        print("  - All tracks and audio files")
        print("  - All playlists")
        print("  - All artwork and photos")
        print("  - The iTunesDB database")
        print()
        confirm = input("Type 'PURGE' to confirm: ")
        if confirm != "PURGE":
            print("Aborted.")
            return 1

    import pathlib

    ipod_control = pathlib.Path(mp) / "iPod_Control"

    for subdir in ["Music", "Artwork", "iTunes", "Photos"]:
        target = ipod_control / subdir
        if target.exists():
            shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)

    # Recreate F00-F49 music subdirectories
    music_dir = ipod_control / "Music"
    for i in range(50):
        (music_dir / f"F{i:02d}").mkdir(exist_ok=True)

    print(f"All data purged from {mp}")
    print("Run 'pygpod init' to re-initialize the iPod.")
    return 0


def _cmd_fix_checksums(args: argparse.Namespace) -> int:
    from .model.database import Database

    mp = _get_mountpoint(args)
    db = Database(mp)
    db.save()
    print("Checksums updated and database saved.")
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    from . import discover

    try:
        results = discover()
    except Exception as e:
        print(f"Error during discovery: {e}", file=sys.stderr)
        return 1

    if not results:
        print("No iPods found.")
        return 0

    mounted = [(mp, dev) for mp, dev in results if mp is not None]
    usb_only = [(mp, dev) for mp, dev in results if mp is None]

    if mounted:
        print(f"Found {len(mounted)} mounted iPod(s):")
        for mp, dev in mounted:
            guid = dev.firewire_guid or "N/A"
            serial = dev.sysinfo.serial_number or "N/A"
            print(f"  {mp}: {dev.model} (GUID: {guid})")
            if serial != "N/A":
                print(f"    Serial: {serial}")

    if usb_only:
        print(f"Found {len(usb_only)} USB-connected iPod(s) (not mounted):")
        for _, dev in usb_only:
            usb = dev.usb_info
            guid = dev.firewire_guid or "N/A"
            pid = f"0x{usb.product_id:04x}" if usb else "N/A"
            usb_serial = usb.serial if usb else ""
            print(f"  {dev.model} - Product ID: {pid}, GUID: {guid}")

            # The USB descriptor serial on iPod Classics is the FireWire
            # GUID, not the real serial.  Only show it if it differs.
            is_guid = guid != "N/A" and usb_serial.upper() == guid.upper()

            # Try reading SCSI serial for this specific device
            scsi_serial = None
            try:
                from .device.usb import read_ipod_serial

                scsi_serial = read_ipod_serial(guid)
            except Exception:
                pass

            if scsi_serial:
                print(f"    Serial: {scsi_serial}")
                from .device.models import GENERATION_NAMES, lookup_model_by_serial

                info = lookup_model_by_serial(scsi_serial)
                if info:
                    gen_name = GENERATION_NAMES.get(info.generation, "Unknown")
                    gb = info.capacity_gb
                    cap = int(gb) if gb == int(gb) else gb
                    print(f"    Model (from serial): {gen_name} ({cap}GB) [{info.model_number}]")
            elif usb_serial and not is_guid:
                print(f"    Serial: {usb_serial}")
            else:
                print(
                    "    Serial: unavailable (run as root or add your user"
                    " to the 'disk' group for SCSI serial detection)"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
