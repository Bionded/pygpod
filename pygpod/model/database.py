"""Main Database class - high-level iPod database API.

This is the primary entry point for pygpod, equivalent to Itdb_iTunesDB in libgpod.
"""

from __future__ import annotations

import logging
import os
import pathlib
import random
import struct
from typing import Any, Dict, List, Optional

from ..db.constants import (
    DEFAULT_MHBD_HEADER_LEN,
    DEFAULT_MHII_HEADER_LEN,
    DEFAULT_MHIP_HEADER_LEN,
    DEFAULT_MHIT_HEADER_LEN,
    DEFAULT_MHLA_HEADER_LEN,
    DEFAULT_MHLI_HEADER_LEN,
    DEFAULT_MHLP_HEADER_LEN,
    DEFAULT_MHLT_HEADER_LEN,
    DEFAULT_MHOD_HEADER_LEN,
    DEFAULT_MHSD_HEADER_LEN,
    DEFAULT_MHYP_HEADER_LEN,
    MEDIATYPE_AUDIO,
    MEDIATYPE_PODCAST,
    MHIA_MAGIC,
    MHII_MAGIC,
    MHIP_MAGIC,
    MHIT_MAGIC,
    MHLA_MAGIC,
    MHLI_MAGIC,
    MHLP_MAGIC,
    MHLT_MAGIC,
    MHOD_ID_ALBUM,
    MHOD_ID_ALBUM_ALBUM,
    MHOD_ID_ALBUM_ARTIST,
    MHOD_ID_ALBUM_ARTIST_MHII,
    MHOD_ID_ALBUMARTIST,
    MHOD_ID_ARTIST,
    MHOD_ID_CATEGORY,
    MHOD_ID_COMMENT,
    MHOD_ID_COMPOSER,
    MHOD_ID_DESCRIPTION,
    MHOD_ID_FILETYPE,
    MHOD_ID_GENRE,
    MHOD_ID_GROUPING,
    MHOD_ID_KEYWORDS,
    MHOD_ID_LIBPLAYLISTINDEX,
    MHOD_ID_LIBPLAYLISTJUMPTABLE,
    MHOD_ID_PATH,
    MHOD_ID_PLAYLIST,
    MHOD_ID_PODCASTRSS,
    MHOD_ID_PODCASTURL,
    MHOD_ID_SUBTITLE,
    MHOD_ID_TITLE,
    MHOD_ID_TVEPISODE,
    MHOD_ID_TVNETWORK,
    MHOD_ID_TVSHOW,
    MHOD_MAGIC,
    MHSD_MAGIC,
    MHYP_MAGIC,
)
from ..db.parser import Record, parse_itunesdb
from ..db.writer import make_string_mhod, write_itunesdb
from ..device.device import Device
from ..device.fileutils import copy_track_to_ipod
from ..device.mountpoint import find_itunesdb, validate_mountpoint
from ..exceptions import DatabaseError, HashError, MountPointError, TrackError
from ..hash.checksum import update_checksums
from ..tags import extract_artwork, filetype_marker, read_tags
from ..utils.compat import put8int, put16lint, put32lint, put64lint, put_float
from ..utils.datetime import now_mac
from .config import DatabaseConfig
from .playlist import Playlist
from .track import Track

logger = logging.getLogger(__name__)


class Database:
    """High-level iPod database API.

    Provides Pythonic access to tracks, playlists, and device info.

    Usage:
        db = Database("/mnt/ipod")
        for track in db.tracks:
            print(f"{track.artist} - {track.title}")

        db.add_track("/path/to/song.mp3")
        db.save()
    """

    def __init__(
        self, mountpoint: Optional[str] = None, config: Optional[DatabaseConfig] = None
    ) -> None:
        self._mountpoint = mountpoint
        self._config = config or DatabaseConfig()
        self._root: Optional[Record] = None
        self._device: Optional[Device] = None
        self._tracks: List[Track] = []
        self._playlists: List[Playlist] = []
        self._track_lookup: Dict[int, Track] = {}  # track_id -> Track
        self._raw_data: Optional[bytes] = None
        self._modified = False
        self._rng = random.Random(self._config.random_seed)

        if mountpoint:
            logger.info("Opening database at %s", mountpoint)
            self._load_from_mountpoint(mountpoint)

    @classmethod
    def from_file(cls, path: str, config: Optional[DatabaseConfig] = None) -> "Database":
        """Parse a standalone iTunesDB file.

        Args:
            path: Path to the iTunesDB file.
            config: Optional DatabaseConfig for controlling output behavior.

        Returns:
            Database instance.
        """
        db = cls.__new__(cls)
        db._mountpoint = None
        db._config = config or DatabaseConfig()
        db._device = None
        db._raw_data = None
        db._modified = False
        db._rng = random.Random(db._config.random_seed)

        with open(path, "rb") as f:
            data = f.read()

        db._raw_data = data
        db._root = parse_itunesdb(data)
        db._build_model()
        return db

    def _load_from_mountpoint(self, mountpoint: str) -> None:
        """Load database from iPod mount point."""
        if not validate_mountpoint(mountpoint):
            raise MountPointError(
                f"Not a valid iPod mount point: {mountpoint}\n"
                f"Expected to find iPod_Control/iTunes/ directory."
            )

        self._device = Device.from_mountpoint(mountpoint)
        db_path = find_itunesdb(mountpoint)
        if db_path is None:
            raise DatabaseError(f"iTunesDB not found at {mountpoint}/iPod_Control/iTunes/iTunesDB")

        with open(db_path, "rb") as f:
            data = f.read()

        self._raw_data = data
        self._root = parse_itunesdb(data)
        self._build_model()
        logger.info("Loaded %d tracks, %d playlists", len(self._tracks), len(self._playlists))

        # Apply play counts from device
        if mountpoint:
            self._apply_play_counts(mountpoint)

    def _apply_play_counts(self, mountpoint: str) -> None:
        """Read, apply, and delete play count data from device.

        The iPod writes play counts to separate files (Play Counts,
        iTunesStats, PlayCounts.plist) between syncs. We merge them
        into the iTunesDB and delete the files so they aren't applied
        again on next load.
        """
        try:
            from ..db.playcounts import apply_play_counts, read_play_counts

            entries = read_play_counts(mountpoint)
            if not entries:
                return

            updated = apply_play_counts(self._tracks, entries)
            if updated > 0:
                self._modified = True
                logger.info(
                    "Merged play counts: %d tracks updated from %d entries",
                    updated,
                    len(entries),
                )

                # Delete the play count files so they aren't re-applied
                itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")
                for fname in ("Play Counts", "iTunesStats", "PlayCounts.plist"):
                    fpath = os.path.join(itunes_dir, fname)
                    if os.path.isfile(fpath):
                        try:
                            os.unlink(fpath)
                            logger.debug("Deleted %s", fpath)
                        except OSError:
                            logger.debug("Failed to delete %s", fpath, exc_info=True)
        except Exception:
            logger.debug("Failed to apply play counts", exc_info=True)

    def _build_model(self) -> None:
        """Build high-level model objects from parsed records."""
        self._tracks = []
        self._playlists = []
        self._track_lookup = {}

        if not self._root:
            return

        # Find tracks (MHSD type 1)
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type == 1 and mhsd.children:
                mhlt = mhsd.children[0]
                for mhit_rec in mhlt.children:
                    track = Track.from_record(mhit_rec)
                    self._tracks.append(track)
                    self._track_lookup[track.track_id] = track

        # Find playlists (MHSD type 2 - primary playlists)
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type == 2 and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp_rec in mhlp.children:
                    pl = Playlist.from_record(mhyp_rec, self._track_lookup)
                    self._playlists.append(pl)

    # ---- Public Properties ----

    @property
    def config(self) -> DatabaseConfig:
        """Database generation configuration."""
        return self._config

    @property
    def mountpoint(self) -> Optional[str]:
        """iPod mount point path."""
        return self._mountpoint

    @property
    def device(self) -> Optional[Device]:
        """Device info (model, capabilities, etc.)."""
        return self._device

    @property
    def tracks(self) -> List[Track]:
        """All tracks in the database."""
        return list(self._tracks)

    @property
    def playlists(self) -> List[Playlist]:
        """All playlists in the database."""
        return list(self._playlists)

    @property
    def master_playlist(self) -> Optional[Playlist]:
        """The master (library) playlist."""
        for pl in self._playlists:
            if pl.is_master:
                return pl
        return None

    @property
    def db_version(self) -> int:
        """Database version number."""
        if self._root:
            return self._root.fields.get("db_version", 0)
        return 0

    @property
    def db_id(self) -> int:
        """Database ID."""
        if self._root:
            return self._root.fields.get("db_id", 0)
        return 0

    # ---- Track Operations ----

    def get_track(self, track_id: int) -> Optional[Track]:
        """Get a track by its ID."""
        return self._track_lookup.get(track_id)

    def add_track(self, filepath: str, **kwargs: Any) -> Track:
        """Add a track to the iPod.

        If mutagen is installed, tags are read automatically from the file.
        Additional/override metadata can be passed as keyword arguments.

        Args:
            filepath: Path to the audio file to add.
            **kwargs: Optional metadata overrides (title, artist, album, etc.)

        Returns:
            The new Track object.

        Raises:
            TrackError: If the file doesn't exist or can't be processed.
        """
        if not os.path.isfile(filepath):
            raise TrackError(f"File not found: {filepath}")

        if not self._mountpoint:
            raise TrackError("Cannot add tracks without a mount point")

        # Check format is iPod-compatible
        from ..tags import check_format_supported

        check_format_supported(filepath)

        # Read tags
        tags = read_tags(filepath)
        tags.update(kwargs)  # User overrides

        # Copy file to iPod
        num_dirs = self._device.music_dirs if self._device else 50
        cfg = self._config
        ipod_path = copy_track_to_ipod(
            filepath,
            self._mountpoint,
            num_dirs,
            prefix=cfg.filename_prefix,
            rand_len=cfg.filename_rand_len,
            charset=cfg.filename_charset,
            rng=self._rng,
        )

        # Generate track ID and DBID
        track_id = self._next_track_id()
        dbid = self._next_dbid()

        # Create MHIT record
        mhit = self._create_mhit(track_id, dbid, tags, ipod_path, filepath)

        # Add to track list MHSD
        mhlt = self._get_or_create_mhlt()
        mhlt.children.append(mhit)

        # Create Track model object
        track = Track.from_record(mhit)
        self._tracks.append(track)
        self._track_lookup[track_id] = track

        # Add to master playlist (in all playlist MHSDs)
        self._add_to_master_playlists(track_id)

        # Auto-add to podcast playlist if applicable
        media_type = tags.get("media_type", MEDIATYPE_AUDIO)
        if media_type & MEDIATYPE_PODCAST:
            pl_name = tags.get("category") or "Podcasts"
            self._auto_add_to_podcast_playlist(track, pl_name)

        # Add artwork if available
        self._add_artwork_for_track(mhit, dbid, filepath, tags.get("cover_art"))

        self._modified = True
        logger.info("Track added: %s", track)
        return track

    def _get_artwork_manager(self):
        """Lazily create and return the ArtworkManager instance."""
        if not hasattr(self, "_artwork_manager") or self._artwork_manager is None:
            from ..device.models import IpodGeneration
            from .artwork_manager import ArtworkManager

            generation = self._device.generation if self._device else None
            if generation is None:
                generation = IpodGeneration.UNKNOWN

            self._artwork_manager = ArtworkManager(self._mountpoint, generation=generation)
        return self._artwork_manager

    def _add_artwork_for_track(
        self, mhit: Record, dbid: int, filepath: str, cover_art: str = None
    ) -> None:
        """Extract artwork from audio file and process immediately.

        Creates thumbnails and writes .ithmb data right away during track
        addition, so save() doesn't have to do all the heavy lifting.
        ArtworkDB is written once at save time.

        Args:
            mhit: The MHIT record for the track.
            dbid: Database ID of the track.
            filepath: Path to the audio file.
            cover_art: Optional fallback image path if no embedded artwork.
        """
        if not self._mountpoint:
            return

        try:
            art_data = extract_artwork(filepath)
            if not art_data and cover_art and os.path.isfile(cover_art):
                with open(cover_art, "rb") as f:
                    art_data = f.read()
            if not art_data:
                return

            mgr = self._get_artwork_manager()
            image_id = mgr.add_artwork_data(dbid, art_data, save=False)
            if image_id is not None:
                # Update MHIT header bytes so iPod knows artwork exists
                header = bytearray(mhit.raw_header)
                if len(header) > 0x7D:  # u16 at 0x7C needs bytes 0x7C-0x7D
                    put16lint(header, 0x7C, 1)  # artwork_count
                if len(header) > 0x83:  # u32 at 0x80 needs bytes 0x80-0x83
                    put32lint(header, 0x80, len(art_data))  # artwork_size
                if len(header) > 0xA4:  # u8 at 0xA4 needs byte 0xA4
                    put8int(header, 0xA4, 1)  # has_artwork
                if len(header) > 0x163:  # u32 at 0x160 needs bytes 0x160-0x163
                    put32lint(header, 0x160, image_id)  # mhii_link
                mhit.raw_header = bytes(header)
                # Mirror into fields so Track properties reflect the new state
                # without requiring a reload (needed for same-session remove_track)
                mhit.fields["artwork_count"] = 1
                mhit.fields["artwork_size"] = len(art_data)
                mhit.fields["has_artwork"] = 1
                mhit.fields["mhii_link"] = image_id

            logger.info("Artwork processed for track dbid=%d (%d bytes)", dbid, len(art_data))
        except Exception:
            logger.debug("Failed to process artwork", exc_info=True)

    def remove_track(self, track: Track, delete_file: bool = False) -> None:
        """Remove a track from the database.

        Args:
            track: Track to remove.
            delete_file: If True, also delete the file from the iPod.
        """
        track_id = track.track_id

        # Remove from playlists
        self._remove_from_all_playlists(track_id)

        # Remove from track list
        mhlt = self._get_or_create_mhlt()
        mhlt.children = [
            c
            for c in mhlt.children
            if not (c.magic == MHIT_MAGIC and c.fields.get("track_id") == track_id)
        ]

        # Remove from model
        self._tracks = [t for t in self._tracks if t.track_id != track_id]
        self._track_lookup.pop(track_id, None)

        # Remove artwork from ArtworkDB
        if self._mountpoint and track.has_artwork and track.mhii_link:
            self._get_artwork_manager().remove_artwork(track.mhii_link)

        # Delete file if requested
        if delete_file and self._mountpoint and track.ipod_path:
            from ..utils.encoding import ipod_path_to_os

            fpath = ipod_path_to_os(track.ipod_path, self._mountpoint)
            try:
                os.unlink(fpath)
            except OSError:
                logger.warning("Failed to delete track file %s", fpath, exc_info=True)

        self._modified = True

    # ---- Playlist Operations ----

    def create_playlist(self, name: str, podcast: bool = False) -> Playlist:
        """Create a new playlist.

        Args:
            name: Playlist name.
            podcast: If True, mark as a podcast playlist.

        Returns:
            The new Playlist object.
        """
        mhlp = self._get_or_create_mhlp(mhsd_type=2)
        playlist_id = self._rng.getrandbits(64)

        mhyp = self._create_mhyp(name, playlist_id, is_master=False, podcast=podcast)
        mhlp.children.append(mhyp)

        # Also add to MHSD type 3 (podcast playlists mirror)
        mhlp3 = self._get_or_create_mhlp(mhsd_type=3)
        mhyp3 = self._create_mhyp(name, playlist_id, is_master=False, podcast=podcast)
        mhlp3.children.append(mhyp3)

        pl = Playlist.from_record(mhyp, self._track_lookup)
        self._playlists.append(pl)
        self._modified = True
        return pl

    def delete_playlist(self, playlist: Playlist) -> None:
        """Delete a playlist."""
        if playlist.is_master:
            raise DatabaseError("Cannot delete the master playlist")

        pid = playlist.playlist_id
        # Remove from MHSD type 2
        mhlp = self._get_or_create_mhlp(mhsd_type=2)
        mhlp.children = [c for c in mhlp.children if c.fields.get("playlist_id") != pid]
        # Remove from MHSD type 3
        mhlp3 = self._get_or_create_mhlp(mhsd_type=3)
        mhlp3.children = [c for c in mhlp3.children if c.fields.get("playlist_id") != pid]

        self._playlists = [p for p in self._playlists if p.playlist_id != pid]
        self._modified = True

    def add_track_to_playlist(self, playlist: Playlist, track: Track) -> None:
        """Add a track to a playlist."""
        if not playlist.record:
            return
        pos = sum(1 for c in playlist.record.children if c.magic == MHIP_MAGIC)
        mhip = self._create_mhip(track.track_id, position=pos)
        playlist.record.children.append(mhip)
        # Update MHIP count
        playlist.record.fields["num_mhips"] = playlist.record.fields.get("num_mhips", 0) + 1
        # Mirror to MHSD type 3
        self._mirror_to_type3(playlist.playlist_id, track.track_id)
        self._modified = True

    def _auto_add_to_podcast_playlist(self, track: Track, playlist_name: str) -> None:
        """Add a podcast track to the single "Podcasts" playlist.

        iPod firmware expects exactly one playlist with podcastflag=1
        containing all podcast tracks. The type 3 hierarchical grouping
        is done by album name at save time.

        Args:
            track: The podcast track to add.
            playlist_name: Ignored (kept for API compat). All podcasts go
                into one "Podcasts" playlist.
        """
        # Find the existing podcast playlist (there should be at most one)
        pl = None
        for p in self._playlists:
            if p.is_podcast:
                pl = p
                break
        # Create if not found
        if pl is None:
            pl = self.create_playlist("Podcasts", podcast=True)
            logger.info("Auto-created Podcasts playlist")
        self.add_track_to_playlist(pl, track)

    def remove_track_from_playlist(self, playlist: Playlist, track: Track) -> None:
        """Remove a track from a playlist."""
        if not playlist.record:
            return
        playlist.record.children = [
            c
            for c in playlist.record.children
            if not (c.magic == MHIP_MAGIC and c.fields.get("track_id") == track.track_id)
        ]
        # Mirror removal to MHSD type 3
        self._unmirror_from_type3(playlist.playlist_id, track.track_id)
        self._modified = True

    # ---- Pre-save Fixups ----

    @staticmethod
    def _expand_header(rec: Record, target_len: int) -> None:
        """Expand a record's header to target_len if currently smaller."""
        if len(rec.raw_header) >= target_len:
            return
        old_len = len(rec.raw_header)
        new_header = bytearray(target_len)
        new_header[:old_len] = rec.raw_header
        put32lint(new_header, 4, target_len)
        rec.raw_header = bytes(new_header)
        logger.debug("Upgraded %s header %d -> %d", rec.magic, old_len, target_len)

    def _upgrade_header_sizes(self) -> None:
        """Upgrade record header sizes to match libgpod/firmware expectations."""
        if not self._root:
            return

        self._expand_header(self._root, DEFAULT_MHBD_HEADER_LEN)

        # Ensure MHBD has required fields
        header = bytearray(self._root.raw_header)
        changed = False
        if len(header) > 0x34 and header[0x30] == 0:
            put32lint(header, 0x30, 1)
            changed = True
        if len(header) > 0x50:
            pid = struct.unpack_from("<Q", header, 0x48)[0]
            if pid == 0:
                put64lint(header, 0x48, self._rng.getrandbits(64))
                changed = True
        if changed:
            self._root.raw_header = bytes(header)

        # Upgrade MHLT and MHLP headers in all MHSDs
        _CHILD_HEADER_SIZES = {
            MHLT_MAGIC: DEFAULT_MHLT_HEADER_LEN,
            MHLP_MAGIC: DEFAULT_MHLP_HEADER_LEN,
        }
        for mhsd in self._root.children:
            for child in mhsd.children:
                target = _CHILD_HEADER_SIZES.get(child.magic)
                if target:
                    self._expand_header(child, target)

    def _ensure_playlist_prefs(self) -> None:
        """Ensure all playlists have a prefs MHOD (type 100).

        The iPod firmware requires this MHOD for proper playlist display.
        Init DBs created before this fix may be missing it.
        """
        if not self._root:
            return

        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type not in (2, 3) or not mhsd.children:
                continue
            mhlp = mhsd.children[0]
            for mhyp in mhlp.children:
                if mhyp.magic != MHYP_MAGIC:
                    continue
                # Check if prefs MHOD already exists
                has_prefs = any(
                    c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_PLAYLIST
                    for c in mhyp.children
                )
                if not has_prefs:
                    prefs = self._make_playlist_prefs_mhod()
                    # Insert after the last MHOD, before the first MHIP
                    insert_idx = 0
                    for ci, c in enumerate(mhyp.children):
                        if c.magic == MHIP_MAGIC:
                            insert_idx = ci
                            break
                    else:
                        insert_idx = len(mhyp.children)
                    mhyp.children.insert(insert_idx, prefs)
                    logger.debug("Added prefs MHOD to playlist %s", mhyp.fields.get("playlist_id"))

    def _ensure_type3_mirror(self) -> None:
        """Ensure MHSD type 3 has a master playlist mirroring type 2.

        libgpod creates identical master playlists in both MHSD type 2 and type 3.
        If type 3 has an empty MHLP, copy the master playlist structure from type 2.
        """
        if not self._root:
            return

        # Find type 2 master playlist
        master_mhyp = None
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 2 and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    if mhyp.fields.get("playlist_type") == 1:
                        master_mhyp = mhyp
                        break

        if not master_mhyp:
            return

        # Check type 3
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 3 and mhsd.children:
                mhlp = mhsd.children[0]
                # If no master playlist in type 3, create one
                has_master = any(
                    c.fields.get("playlist_type") == 1
                    for c in mhlp.children
                    if c.magic == MHYP_MAGIC
                )
                if not has_master:
                    # Create a new MHYP mirroring the master from type 2
                    pid = master_mhyp.fields.get("playlist_id", 0)
                    mirror = self._create_mhyp(
                        self._config.master_playlist_name, pid, is_master=True
                    )
                    # Copy MHIPs from master
                    for child in master_mhyp.children:
                        if child.magic == MHIP_MAGIC:
                            mhip = self._create_mhip(
                                child.fields.get("track_id", 0),
                                position=sum(1 for c in mirror.children if c.magic == MHIP_MAGIC),
                            )
                            mirror.children.append(mhip)
                    mhlp.children.append(mirror)
                    logger.debug("Mirrored master playlist to MHSD type 3")
                return

    def _sync_type3_names(self) -> None:
        """Sync playlist names from MHSD type 2 to type 3 mirrors.

        When a playlist is renamed via playlist.name setter, only the type 2
        MHYP is updated. This fixup copies the name MHOD from type 2 to the
        matching type 3 MHYP at save time.
        """
        if not self._root:
            return

        from ..db.writer import make_string_mhod

        # Build type 2 playlist_id -> name map
        type2_names: dict = {}
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 2 and mhsd.children:
                for mhyp in mhsd.children[0].children:
                    if mhyp.magic == MHYP_MAGIC:
                        pid = mhyp.fields.get("playlist_id")
                        name = mhyp.get_mhod(MHOD_ID_TITLE)
                        if pid and name:
                            type2_names[pid] = name

        # Update type 3 names to match
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 3 and mhsd.children:
                for mhyp in mhsd.children[0].children:
                    if mhyp.magic != MHYP_MAGIC:
                        continue
                    pid = mhyp.fields.get("playlist_id")
                    expected_name = type2_names.get(pid)
                    if not expected_name:
                        continue
                    current_name = mhyp.get_mhod(MHOD_ID_TITLE)
                    if current_name != expected_name:
                        # Replace name MHOD
                        mhyp.children = [
                            c
                            for c in mhyp.children
                            if not (
                                c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_TITLE
                            )
                        ]
                        mhyp.children.insert(0, make_string_mhod(MHOD_ID_TITLE, expected_name))

    def _rebuild_type3_podcasts(self) -> None:
        """Rebuild podcast playlists in MHSD type 3 with hierarchical grouping.

        iPod firmware expects podcast playlists in type 3 to have a
        two-level MHIP structure: group MHIPs (per album/show) containing
        member MHIPs (per track). This matches libgpod's write_podcast_mhips().
        """
        if not self._root:
            return

        # Find type 3 MHLP
        mhlp3 = None
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 3 and mhsd.children:
                mhlp3 = mhsd.children[0]
                break
        if mhlp3 is None:
            return

        # Find type 2 podcast playlists to get track lists
        type2_podcasts = {}  # playlist_id -> mhyp record
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 2 and mhsd.children:
                for mhyp in mhsd.children[0].children:
                    if (
                        mhyp.magic == MHYP_MAGIC
                        and mhyp.fields.get("podcast_flag") == 1
                        and mhyp.fields.get("playlist_type") != 1
                    ):
                        type2_podcasts[mhyp.fields.get("playlist_id")] = mhyp

        if not type2_podcasts:
            return

        # Track lookup for album names
        mhlt = self._get_or_create_mhlt()
        track_albums = {}  # track_id -> album name
        for mhit in mhlt.children:
            if mhit.magic == MHIT_MAGIC:
                tid = mhit.fields.get("track_id", 0)
                album = mhit.get_mhod(MHOD_ID_ALBUM) or ""
                track_albums[tid] = album

        # Counter for unique MHIP IDs
        next_id = 1
        for mhyp in mhlp3.children:
            if mhyp.magic == MHYP_MAGIC:
                for c in mhyp.children:
                    if c.magic == MHIP_MAGIC:
                        gid = c.fields.get("podcastgroupid", 0)
                        if gid >= next_id:
                            next_id = gid + 1

        # Rebuild each podcast playlist in type 3
        for pid, mhyp2 in type2_podcasts.items():
            # Find matching type 3 MHYP
            mhyp3 = None
            for mhyp in mhlp3.children:
                if mhyp.magic == MHYP_MAGIC and mhyp.fields.get("playlist_id") == pid:
                    mhyp3 = mhyp
                    break
            if mhyp3 is None:
                continue

            # Collect track IDs from type 2
            track_ids = [
                c.fields.get("track_id", 0) for c in mhyp2.children if c.magic == MHIP_MAGIC
            ]
            if not track_ids:
                continue

            # Group tracks by album
            album_groups = {}  # album -> [track_id, ...]
            for tid in track_ids:
                album = track_albums.get(tid, "")
                album_groups.setdefault(album, []).append(tid)

            # Remove old MHIPs from type 3 MHYP
            mhyp3.children = [c for c in mhyp3.children if c.magic != MHIP_MAGIC]

            # Create hierarchical MHIPs
            for album_name, tids in sorted(album_groups.items()):
                group_id = next_id
                next_id += 1
                group_mhip = self._create_podcast_group_mhip(group_id, album_name)
                mhyp3.children.append(group_mhip)

                for tid in tids:
                    member_id = next_id
                    next_id += 1
                    member_mhip = self._create_podcast_member_mhip(member_id, tid, group_id)
                    mhyp3.children.append(member_mhip)

            logger.debug(
                "Rebuilt type 3 podcast playlist %s with %d groups",
                pid,
                len(album_groups),
            )

    # ---- Sort Index Generation ----

    def _rebuild_sort_indexes(self) -> None:
        """Rebuild sort index MHODs (type 52+53) on master playlists.

        Generates sorted track index and letter jump table for each sort key
        (artist, album, genre, composer, title). These are required by iPod
        firmware for browsing by category.
        """
        sort_keys = [
            (3, "title"),
            (5, "artist"),
            (4, "album"),
            (7, "genre"),
            (18, "composer"),
        ]

        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type in (2, 3) and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    if mhyp.fields.get("playlist_type") != 1:
                        continue

                    # Remove existing sort MHODs
                    mhyp.children = [
                        c
                        for c in mhyp.children
                        if not (
                            c.magic == MHOD_MAGIC
                            and c.fields.get("mhod_type")
                            in (MHOD_ID_LIBPLAYLISTINDEX, MHOD_ID_LIBPLAYLISTJUMPTABLE)
                        )
                    ]

                    # Collect tracks in playlist order
                    track_ids = []
                    for child in mhyp.children:
                        if child.magic == MHIP_MAGIC:
                            tid = child.fields.get("track_id")
                            if tid is not None:
                                track_ids.append(tid)

                    if not track_ids:
                        continue

                    # Build metadata list
                    _empty = {"artist": "", "album": "", "genre": "", "composer": "", "title": ""}
                    tracks_meta = []
                    for i, tid in enumerate(track_ids):
                        track = self._track_lookup.get(tid)
                        if track:
                            meta = {
                                "index": i,
                                "artist": track.artist or "",
                                "album": track.album or "",
                                "genre": track.genre or "",
                                "composer": getattr(track, "composer", "") or "",
                                "title": track.title or "",
                            }
                        else:
                            meta = {"index": i, **_empty}
                        tracks_meta.append(meta)

                    # Insert sort MHODs BEFORE the first MHIP
                    insert_idx = next(
                        (i for i, c in enumerate(mhyp.children) if c.magic == MHIP_MAGIC),
                        len(mhyp.children),
                    )

                    for sort_key_num, field_name in sort_keys:
                        sort_mhods = self._build_sort_pair(sort_key_num, field_name, tracks_meta)
                        for mhod in sort_mhods:
                            mhyp.children.insert(insert_idx, mhod)
                            insert_idx += 1

    def _build_sort_pair(self, sort_key: int, field_name: str, tracks_meta: list) -> list:
        """Build a type 52 (sort index) + type 53 (jump table) MHOD pair."""

        def sort_val(meta):
            val = meta[field_name].upper()
            if val.startswith("THE "):
                val = val[4:]
            return val

        sorted_meta = sorted(tracks_meta, key=sort_val)
        sorted_indices = [m["index"] for m in sorted_meta]

        # Type 52: sort index
        body52 = bytearray(48 + len(sorted_indices) * 4)
        struct.pack_into("<I", body52, 0, sort_key)
        struct.pack_into("<I", body52, 4, len(sorted_indices))
        for j, idx in enumerate(sorted_indices):
            struct.pack_into("<I", body52, 48 + j * 4, idx)
        mhod52 = self._make_raw_mhod(MHOD_ID_LIBPLAYLISTINDEX, bytes(body52))

        # Type 53: letter jump table
        groups = []
        current_letter = None
        current_start = 0
        current_count = 0
        for i, m in enumerate(sorted_meta):
            val = sort_val(m)
            letter = ord(val[0]) if val else 0
            if letter != current_letter:
                if current_letter is not None:
                    groups.append((current_letter, current_start, current_count))
                current_letter = letter
                current_start = i
                current_count = 1
            else:
                current_count += 1
        if current_letter is not None:
            groups.append((current_letter, current_start, current_count))

        body53 = bytearray(16 + len(groups) * 12)
        struct.pack_into("<I", body53, 0, sort_key)
        struct.pack_into("<I", body53, 4, len(groups))
        for j, (letter, start, count) in enumerate(groups):
            off = 16 + j * 12
            struct.pack_into("<I", body53, off, letter)
            struct.pack_into("<I", body53, off + 4, start)
            struct.pack_into("<I", body53, off + 8, count)
        mhod53 = self._make_raw_mhod(MHOD_ID_LIBPLAYLISTJUMPTABLE, bytes(body53))

        return [mhod52, mhod53]

    # ---- Album / Artist List Generation ----

    def _rebuild_album_list(self) -> None:
        """Rebuild MHSD type 4 album list from track metadata.

        Creates MHIA records with MHOD types 200 (album name) and
        201 (album artist) for each unique (album, artist) pair.
        Also updates MHIT offset 0x120 with album_id links.
        """
        enc = self._config.string_encoding

        # Collect unique albums: (album, artist) -> album_id
        albums = {}  # (album, artist) -> int
        for track in self._tracks:
            album = track.album or ""
            artist = track.artist or ""
            key = (album, artist)
            if key not in albums:
                albums[key] = len(albums) + 1  # 1-based IDs

        mhsd4 = self._get_or_create_mhsd(4)

        # Create MHLA with MHIA children
        mhla = Record(MHLA_MAGIC, DEFAULT_MHLA_HEADER_LEN, 0)
        mhla.raw_header = self._make_basic_header(MHLA_MAGIC, DEFAULT_MHLA_HEADER_LEN)
        mhla.fields["num_albums"] = len(albums)

        # libgpod uses 88-byte MHIA headers for album list records
        ALBUM_MHIA_HEADER_LEN = 88

        for (album, artist), album_id in albums.items():
            mhia = Record(MHIA_MAGIC, ALBUM_MHIA_HEADER_LEN, 0)
            header = bytearray(ALBUM_MHIA_HEADER_LEN)
            header[0:4] = MHIA_MAGIC
            put32lint(header, 4, ALBUM_MHIA_HEADER_LEN)
            put32lint(header, 0x10, album_id)
            mhia.raw_header = bytes(header)
            mhia.fields["album_id"] = album_id

            # MHOD type 200: album name
            if album:
                mhod200 = make_string_mhod(MHOD_ID_ALBUM_ALBUM, album, encoding=enc)
                mhia.children.append(mhod200)
            # MHOD type 201: album artist
            if artist:
                mhod201 = make_string_mhod(MHOD_ID_ALBUM_ARTIST, artist, encoding=enc)
                mhia.children.append(mhod201)

            mhla.children.append(mhia)

        mhsd4.children = [mhla]

        # Update MHIT headers with album_id at offset 0x120
        mhlt = self._get_or_create_mhlt()
        for mhit in mhlt.children:
            if mhit.magic != MHIT_MAGIC:
                continue
            album_name = mhit.get_mhod(MHOD_ID_ALBUM) or ""
            artist_name = mhit.get_mhod(MHOD_ID_ARTIST) or ""
            album_id = albums.get((album_name, artist_name), 0)
            if album_id and len(mhit.raw_header) > 0x124:
                header = bytearray(mhit.raw_header)
                put32lint(header, 0x120, album_id)
                mhit.raw_header = bytes(header)

    def _rebuild_artist_list(self) -> None:
        """Rebuild MHSD type 8 artist list from track metadata.

        Creates MHII records with MHOD type 300 (artist name) for each
        unique artist. Also updates MHIT offset 0x160 with artist_id links.
        """
        enc = self._config.string_encoding

        # Collect unique artists: artist -> artist_id
        artists = {}  # artist_name -> int
        for track in self._tracks:
            artist = track.artist or ""
            if artist and artist not in artists:
                artists[artist] = len(artists) + 1  # 1-based IDs

        mhsd8 = self._get_or_create_mhsd(8)

        # Create MHLI with MHII children
        mhli = Record(MHLI_MAGIC, DEFAULT_MHLI_HEADER_LEN, 0)
        mhli.raw_header = self._make_basic_header(MHLI_MAGIC, DEFAULT_MHLI_HEADER_LEN)
        mhli.fields["num_artists"] = len(artists)

        for artist_name, artist_id in artists.items():
            mhii = Record(MHII_MAGIC, DEFAULT_MHII_HEADER_LEN, 0)
            header = bytearray(DEFAULT_MHII_HEADER_LEN)
            header[0:4] = MHII_MAGIC
            put32lint(header, 4, DEFAULT_MHII_HEADER_LEN)
            put32lint(header, 0x10, artist_id)
            mhii.raw_header = bytes(header)
            mhii.fields["artist_id"] = artist_id

            # MHOD type 300: artist name
            mhod300 = make_string_mhod(MHOD_ID_ALBUM_ARTIST_MHII, artist_name, encoding=enc)
            mhii.children.append(mhod300)

            mhli.children.append(mhii)

        mhsd8.children = [mhli]

        # Update MHIT headers with artist_id at offset 0x1E0
        # Note: 0x160 is mhii_link (artwork), NOT artist_id
        mhlt = self._get_or_create_mhlt()
        for mhit in mhlt.children:
            if mhit.magic != MHIT_MAGIC:
                continue
            artist_name = mhit.get_mhod(MHOD_ID_ARTIST) or ""
            artist_id = artists.get(artist_name, 0)
            if artist_id:
                header = bytearray(mhit.raw_header)
                if len(header) > 0x1E4:
                    put32lint(header, 0x1E0, artist_id)
                mhit.raw_header = bytes(header)

    # ---- Save ----

    def _prepare_for_save(self) -> bytes:
        """Run all pre-save fixups and serialize to bytes."""
        if not self._root:
            raise DatabaseError("No database loaded")

        self._save_artwork_db()

        self._upgrade_header_sizes()
        self._ensure_playlist_prefs()
        self._ensure_type3_mirror()
        self._sync_type3_names()
        self._rebuild_type3_podcasts()
        self._rebuild_sort_indexes()
        if self._config.generate_album_list:
            self._rebuild_album_list()
        if self._config.generate_artist_list:
            self._rebuild_artist_list()

        # Update db_version to match device requirements
        if self._device and self._root:
            expected_ver = self._device.db_version
            current_ver = self._root.fields.get("db_version", 0)
            if expected_ver > current_ver:
                header = bytearray(self._root.raw_header)
                put32lint(header, 0x10, expected_ver)
                self._root.raw_header = bytes(header)
                self._root.fields["db_version"] = expected_ver

        return write_itunesdb(self._root)

    def save(self) -> None:
        """Save the database back to the iPod."""
        logger.info("Saving database")
        if not self._mountpoint:
            raise DatabaseError("No mount point - use save_to_file() instead")

        data = self._prepare_for_save()

        # Update checksums if needed
        if self._device and self._device.requires_hash:
            guid = self._device.firewire_guid
            if not guid:
                guid = self._try_resolve_guid()
            if not guid:
                raise HashError(
                    "This iPod requires database checksums but FirewireGuid is not available.\n"
                    "Without a valid checksum the iPod will not read the database.\n"
                    "\n"
                    "To fix this, create a SysInfo file:\n"
                    f"  {self._device.sysinfo_file_path}\n"
                    "with content like:\n"
                    "  FirewireGuid: 0x000A27001301297E\n"
                    "\n"
                    "Find the GUID from USB serial:\n"
                    "  Linux:  cat /sys/bus/usb/devices/*/serial\n"
                    "  The first 16 hex characters = FirewireGuid\n"
                    "\n"
                    "Or install pyusb for automatic detection:\n"
                    "  pip install pygpod[usb]"
                )
            data = update_checksums(data, guid)

        db_path = (
            self._device.itunesdb_path()
            if self._device
            else pathlib.Path(self._mountpoint) / "iPod_Control" / "iTunes" / "iTunesDB"
        )

        with open(db_path, "wb") as f:
            f.write(data)

        self._raw_data = data
        self._modified = False
        logger.debug("Database saved to %s", db_path)

    def _save_artwork_db(self) -> None:
        """Write ArtworkDB to disk once (artwork was already processed per-track)."""
        if hasattr(self, "_artwork_manager") and self._artwork_manager is not None:
            try:
                self._artwork_manager.save()
                logger.info("ArtworkDB saved")
            except Exception:
                logger.warning("ArtworkDB save failed", exc_info=True)

    def _try_resolve_guid(self) -> Optional[str]:
        """Try to get FirewireGuid from USB and write SysInfo for future use."""
        if not self._device:
            return None
        try:
            from ..device.usb import detect_ipod_usb

            usb_devices = detect_ipod_usb()
            for usb_dev in usb_devices:
                if usb_dev.firewire_guid:
                    # Store in sysinfo for future use
                    self._device._sysinfo.raw["FirewireGuid"] = usb_dev.firewire_guid
                    try:
                        self._device.write_sysinfo()
                        logger.info("Auto-wrote SysInfo with GUID %s", usb_dev.firewire_guid)
                    except Exception:
                        logger.debug("Failed to write SysInfo", exc_info=True)
                    return usb_dev.firewire_guid
        except ImportError:
            logger.debug("pyusb not available for GUID resolution")
        except Exception:
            logger.debug("USB GUID resolution failed", exc_info=True)
        return None

    def save_to_file(self, path: str) -> None:
        """Save the database to a specific file path."""
        data = self._prepare_for_save()
        with open(path, "wb") as f:
            f.write(data)

    # ---- Context Manager ----

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._modified and exc_type is None:
            self.save()

    # ---- Internal Helpers ----

    def _next_track_id(self) -> int:
        """Generate the next unique track ID."""
        existing = {t.track_id for t in self._tracks}
        tid = max(existing, default=self._config.track_id_start - 1) + 1
        if tid < self._config.track_id_start:
            tid = self._config.track_id_start
        while tid in existing:
            tid += 1
        return tid

    def _next_dbid(self) -> int:
        """Generate a unique 64-bit DBID."""
        existing = {t.dbid for t in self._tracks}
        while True:
            dbid = self._rng.getrandbits(64)
            if dbid not in existing and dbid != 0:
                return dbid

    def _get_or_create_mhlt(self) -> Record:
        """Get the track list record, creating if needed."""
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 1 and mhsd.children:
                return mhsd.children[0]

        # Create new MHSD type 1 + MHLT
        mhsd = self._create_mhsd(1)
        mhlt = Record(MHLT_MAGIC, DEFAULT_MHLT_HEADER_LEN, 0)
        mhlt.raw_header = self._make_basic_header(MHLT_MAGIC, DEFAULT_MHLT_HEADER_LEN)
        mhlt.fields["num_tracks"] = 0
        mhsd.children.append(mhlt)
        self._root.children.insert(0, mhsd)
        return mhlt

    def _get_or_create_mhlp(self, mhsd_type: int = 2) -> Record:
        """Get a playlist list record, creating if needed."""
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == mhsd_type and mhsd.children:
                return mhsd.children[0]

        mhsd = self._create_mhsd(mhsd_type)
        mhlp = Record(MHLP_MAGIC, DEFAULT_MHLP_HEADER_LEN, 0)
        mhlp.raw_header = self._make_basic_header(MHLP_MAGIC, DEFAULT_MHLP_HEADER_LEN)
        mhlp.fields["num_playlists"] = 0
        mhsd.children.append(mhlp)
        self._root.children.append(mhsd)
        return mhlp

    def _get_or_create_mhsd(self, mhsd_type: int) -> Record:
        """Get an MHSD of the given type, creating if needed."""
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == mhsd_type:
                return mhsd
        mhsd = self._create_mhsd(mhsd_type)
        self._root.children.append(mhsd)
        return mhsd

    def _create_mhsd(self, mhsd_type: int) -> Record:
        """Create a new MHSD record."""
        header_len = DEFAULT_MHSD_HEADER_LEN
        rec = Record(MHSD_MAGIC, header_len, 0)
        header = bytearray(header_len)
        header[0:4] = MHSD_MAGIC
        put32lint(header, 4, header_len)
        put32lint(header, 0x0C, mhsd_type)
        rec.raw_header = bytes(header)
        rec.fields["mhsd_type"] = mhsd_type
        rec.raw_bytes = bytes(header)
        return rec

    def _create_mhit(
        self, track_id: int, dbid: int, tags: Dict[str, Any], ipod_path: str, filepath: str
    ) -> Record:
        """Create a new MHIT record with MHODs."""
        cfg = self._config
        header_len = DEFAULT_MHIT_HEADER_LEN
        header = bytearray(header_len)
        header[0:4] = MHIT_MAGIC
        put32lint(header, 4, header_len)
        # total_len updated by writer

        put32lint(header, 0x10, track_id)
        put32lint(header, 0x14, 1)  # visible

        # Filetype marker
        marker = filetype_marker(filepath)
        header[0x18:0x1C] = marker

        # type1: 0=audio, 1=audiobook.  type2: 0=audio, 1=video
        media_type = tags.get("media_type", MEDIATYPE_AUDIO)
        from ..db.constants import (
            MEDIATYPE_AUDIOBOOK,
            MEDIATYPE_MUSICVIDEO,
            MEDIATYPE_PODCAST,
            MEDIATYPE_TVSHOW,
            MEDIATYPE_VIDEO,
        )

        type1 = 1 if (media_type & MEDIATYPE_AUDIOBOOK) else 0
        _video_types = MEDIATYPE_VIDEO | MEDIATYPE_MUSICVIDEO | MEDIATYPE_TVSHOW
        type2 = 1 if (media_type & _video_types) else 0
        put8int(header, 0x1C, type1)
        put8int(header, 0x1D, type2)

        put8int(header, 0x1E, 1 if tags.get("compilation") else 0)

        file_size = tags.get("file_size", 0)

        if cfg.set_time_fields_zero:
            now = 0
        else:
            now = now_mac()

        put32lint(header, 0x20, now)  # time_modified
        put32lint(header, 0x24, file_size)
        put32lint(header, 0x28, tags.get("duration_ms", 0))
        put32lint(header, 0x2C, tags.get("track_number", 0))
        put32lint(header, 0x30, tags.get("total_tracks", 0))
        put32lint(header, 0x34, tags.get("year", 0))
        put32lint(header, 0x38, tags.get("bitrate", 0))

        # Pack sample rate: high 16 bits = Hz
        sr = tags.get("samplerate", 44100)
        put32lint(header, 0x3C, (sr << 16) & 0xFFFFFFFF)

        put32lint(header, 0x5C, tags.get("cd_number", 0))
        put32lint(header, 0x60, tags.get("total_cds", 0))
        put32lint(header, 0x68, now)  # time_added
        put64lint(header, 0x70, dbid)

        # Config-driven unknown fields
        if header_len > 0x7E:
            put16lint(header, 0x7E, cfg.unk_0x7e)
        put32lint(header, 0x84, cfg.unk_0x84)

        # samplerate float
        put_float(header, 0x88, float(sr))

        if header_len > 0x90:
            put16lint(header, 0x90, cfg.unk_0x90)

        # DBID2 = same as DBID
        if header_len >= 0xB0:
            put64lint(header, 0xA8, dbid)

        # Podcast/audiobook flags
        is_podcast_or_audiobook = media_type & (MEDIATYPE_PODCAST | MEDIATYPE_AUDIOBOOK)
        if header_len > 0xA8:
            # skip_when_shuffling (0xA5), remember_position (0xA6), flag4 (0xA7)
            if is_podcast_or_audiobook:
                put8int(header, 0xA5, 1)  # skip when shuffling
                put8int(header, 0xA6, 1)  # remember playback position
                put8int(header, 0xA7, 1)  # flag4: podcast display format

        # mark_unplayed at offset 0xB2
        if header_len > 0xB2:
            if is_podcast_or_audiobook:
                put8int(header, 0xB2, 0x02)  # mark as unplayed
            else:
                put8int(header, 0xB2, cfg.mark_unplayed)

        # Media type
        put32lint(header, 0xD0, media_type)

        # Season/episode numbers for TV shows
        if header_len > 0xDC:
            put32lint(header, 0xD4, tags.get("season_number", 0))
            put32lint(header, 0xD8, tags.get("episode_number", 0))

        # unk_0x12c (optionally copy file_size)
        if header_len > 0x130:
            if cfg.unk_0x12c_from_filesize:
                put32lint(header, 0x12C, file_size)

        # unk_0x134 fill (6 bytes)
        if header_len > 0x13A:
            for i in range(6):
                put8int(header, 0x134 + i, cfg.unk_0x134_fill)

        # unk_0x168
        if header_len > 0x16C:
            put32lint(header, 0x168, cfg.unk_0x168)

        rec = Record(MHIT_MAGIC, header_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["track_id"] = track_id
        rec.fields["dbid"] = dbid
        rec.fields["num_mhods"] = 0

        # Copy known fields into rec.fields for the Track wrapper
        rec.fields["visible"] = 1
        rec.fields["filetype_marker"] = marker
        rec.fields["file_size"] = file_size
        rec.fields["tracklen"] = tags.get("duration_ms", 0)
        rec.fields["track_number"] = tags.get("track_number", 0)
        rec.fields["total_tracks"] = tags.get("total_tracks", 0)
        rec.fields["year"] = tags.get("year", 0)
        rec.fields["bitrate"] = tags.get("bitrate", 0)
        rec.fields["samplerate"] = (sr << 16) & 0xFFFFFFFF
        rec.fields["time_added"] = now
        rec.fields["time_modified"] = now
        rec.fields["media_type"] = media_type
        rec.fields["play_count"] = 0
        rec.fields["rating"] = 0
        rec.fields["season_number"] = tags.get("season_number", 0)
        rec.fields["episode_number"] = tags.get("episode_number", 0)
        rec.fields["cd_number"] = tags.get("cd_number", 0)
        rec.fields["total_cds"] = tags.get("total_cds", 0)

        # Create MHODs for string metadata
        enc = cfg.string_encoding
        mhod_data = [
            (MHOD_ID_TITLE, tags.get("title", "")),
            (MHOD_ID_ARTIST, tags.get("artist", "")),
            (MHOD_ID_ALBUM, tags.get("album", "")),
            (MHOD_ID_GENRE, tags.get("genre", "")),
            (MHOD_ID_FILETYPE, tags.get("filetype", "")),
            (MHOD_ID_COMMENT, tags.get("comment", "")),
            (MHOD_ID_COMPOSER, tags.get("composer", "")),
            (MHOD_ID_PATH, ipod_path),
        ]
        # Optional string MHODs - only created when tag value is present
        _optional_mhods = [
            ("albumartist", MHOD_ID_ALBUMARTIST),
            ("grouping", MHOD_ID_GROUPING),
            ("category", MHOD_ID_CATEGORY),
            ("description", MHOD_ID_DESCRIPTION),
            ("subtitle", MHOD_ID_SUBTITLE),
            ("keywords", MHOD_ID_KEYWORDS),
            ("podcast_url", MHOD_ID_PODCASTURL),
            ("podcast_rss", MHOD_ID_PODCASTRSS),
            ("tvshow", MHOD_ID_TVSHOW),
            ("tvepisode", MHOD_ID_TVEPISODE),
            ("tvnetwork", MHOD_ID_TVNETWORK),
        ]
        for tag_key, mhod_id in _optional_mhods:
            val = tags.get(tag_key)
            if val:
                mhod_data.append((mhod_id, val))

        # Build MHODs (unordered first)
        mhod_records = {}  # type -> Record
        for mtype, text in mhod_data:
            if text:
                mhod = make_string_mhod(mtype, text, encoding=enc)
                mhod_records[mtype] = mhod

        # Apply MHOD ordering if configured
        if cfg.mhod_order:
            for mtype in cfg.mhod_order:
                if mtype in mhod_records:
                    rec.children.append(mhod_records.pop(mtype))
            # Append any remaining MHODs not in the order list
            for mhod in mhod_records.values():
                rec.children.append(mhod)
        else:
            for mhod in mhod_records.values():
                rec.children.append(mhod)

        return rec

    def _make_raw_mhod(self, mhod_type: int, body: bytes) -> Record:
        """Create a raw MHOD record with arbitrary body data."""
        header_len = DEFAULT_MHOD_HEADER_LEN
        total_len = header_len + len(body)
        header = bytearray(header_len)
        header[0:4] = MHOD_MAGIC
        put32lint(header, 4, header_len)
        put32lint(header, 8, total_len)
        put32lint(header, 0x0C, mhod_type)
        rec = Record(MHOD_MAGIC, header_len, total_len)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header) + body
        rec.fields["mhod_type"] = mhod_type
        return rec

    def _make_playlist_prefs_mhod(self) -> Record:
        """Create MHOD type 100 (playlist preferences, 648 bytes).

        This matches libgpod's default playlist column/view preferences.
        Every playlist (master and regular) must have one of these.
        """
        body = bytearray(624)
        # 16-byte preamble (zeros)
        # View settings at offset 16
        struct.pack_into("<I", body, 16, 0x00010084)
        struct.pack_into("<I", body, 20, 5)  # visible column count
        struct.pack_into("<I", body, 24, 9)  # total column count
        struct.pack_into("<I", body, 28, 3)  # default sort (artist)
        # Column entries at offset 32, each 16 bytes: field_id(2) + width(2) + flags(4) + pad(8)
        columns = [
            (1, 0x12, 0),
            (2, 0xC8, 0),
            (13, 0x3C, 0),
            (4, 0x7D, 0),
            (3, 0x7D, 0),
            (8, 0x64, 0),
            (23, 0x64, 1),
            (20, 0x50, 1),
            (21, 0x7D, 1),
        ]
        for i, (fid, width, flags) in enumerate(columns):
            off = 32 + i * 16
            struct.pack_into("<HH", body, off, fid, width)
            struct.pack_into("<I", body, off + 4, flags)
        return self._make_raw_mhod(MHOD_ID_PLAYLIST, bytes(body))

    def _make_mhip_order_mhod(self, position: int) -> Record:
        """Create MHOD type 100 for MHIP (track position index, 44 bytes).

        Each MHIP must contain one of these with its 0-based position.
        """
        body = bytearray(20)
        struct.pack_into("<I", body, 0, position)
        return self._make_raw_mhod(MHOD_ID_PLAYLIST, bytes(body))

    def _create_mhyp(
        self, name: str, playlist_id: int, is_master: bool = False, podcast: bool = False
    ) -> Record:
        """Create a new MHYP (playlist) record."""
        cfg = self._config
        header_len = DEFAULT_MHYP_HEADER_LEN
        header = bytearray(header_len)
        header[0:4] = MHYP_MAGIC
        put32lint(header, 4, header_len)

        put8int(header, 0x14, 1 if is_master else 0)
        put32lint(header, 0x18, now_mac())
        put64lint(header, 0x1C, playlist_id)

        # Config-driven MHYP fields
        if header_len > 0x2C:
            put32lint(header, 0x28, cfg.mhyp_unk_0x28)
            if podcast:
                put16lint(header, 0x2A, 1)  # podcast_flag
        if header_len > 0x30:
            put32lint(header, 0x2C, cfg.mhyp_sort_order)

        rec = Record(MHYP_MAGIC, header_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["playlist_type"] = 1 if is_master else 0
        rec.fields["podcast_flag"] = 1 if podcast else 0
        rec.fields["playlist_id"] = playlist_id
        rec.fields["num_mhods"] = 0
        rec.fields["num_mhips"] = 0
        rec.fields["timestamp"] = now_mac()

        # Add name MHOD
        name_mhod = make_string_mhod(MHOD_ID_TITLE, name, encoding=cfg.string_encoding)
        rec.children.append(name_mhod)

        # Add playlist preferences MHOD (type 100) - required by iPod firmware
        prefs_mhod = self._make_playlist_prefs_mhod()
        rec.children.append(prefs_mhod)

        return rec

    def _create_mhip(self, track_id: int, position: int = 0) -> Record:
        """Create a new MHIP (playlist item) record."""
        header_len = DEFAULT_MHIP_HEADER_LEN
        header = bytearray(header_len)
        header[0:4] = MHIP_MAGIC
        put32lint(header, 4, header_len)
        put32lint(header, 0x0C, 1)  # num_mhods = 1
        put32lint(header, 0x18, track_id)

        # Child MHOD type 100 with position index
        order_mhod = self._make_mhip_order_mhod(position)
        total_len = header_len + order_mhod.total_len

        put32lint(header, 8, total_len)

        rec = Record(MHIP_MAGIC, header_len, total_len)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header) + order_mhod.raw_bytes
        rec.fields["track_id"] = track_id
        rec.fields["num_mhods"] = 1
        rec.children.append(order_mhod)

        return rec

    def _create_podcast_group_mhip(self, group_id: int, group_name: str) -> Record:
        """Create a podcast group MHIP (parent node for album/show grouping in type 3)."""
        header_len = DEFAULT_MHIP_HEADER_LEN
        header = bytearray(header_len)
        header[0:4] = MHIP_MAGIC
        put32lint(header, 4, header_len)
        put32lint(header, 0x0C, 1)  # num_mhods = 1 (title child)
        put32lint(header, 0x10, 256)  # podcastgroupflag = 256
        put32lint(header, 0x14, group_id)  # podcastgroupid
        # trackid = 0, timestamp = 0, podcastgroupref = 0

        # Title MHOD child with group name
        name_mhod = make_string_mhod(
            MHOD_ID_TITLE, group_name, encoding=self._config.string_encoding
        )
        total_len = header_len + name_mhod.total_len
        put32lint(header, 8, total_len)

        rec = Record(MHIP_MAGIC, header_len, total_len)
        rec.raw_header = bytes(header)
        rec.fields["track_id"] = 0
        rec.fields["num_mhods"] = 1
        rec.fields["podcastgroupflag"] = 256
        rec.fields["podcastgroupid"] = group_id
        rec.children.append(name_mhod)
        return rec

    def _create_podcast_member_mhip(self, member_id: int, track_id: int, group_ref: int) -> Record:
        """Create a podcast member MHIP (child node referencing a track in type 3)."""
        header_len = DEFAULT_MHIP_HEADER_LEN
        header = bytearray(header_len)
        header[0:4] = MHIP_MAGIC
        put32lint(header, 4, header_len)
        put32lint(header, 0x0C, 1)  # num_mhods = 1
        put32lint(header, 0x10, 0)  # podcastgroupflag = 0
        put32lint(header, 0x14, member_id)  # podcastgroupid (unique item id)
        put32lint(header, 0x18, track_id)  # trackid
        # timestamp = 0
        put32lint(header, 0x20, group_ref)  # podcastgroupref -> parent group

        # MHOD type 100 (playlist order) child
        order_mhod = self._make_mhip_order_mhod(member_id)
        total_len = header_len + order_mhod.total_len
        put32lint(header, 8, total_len)

        rec = Record(MHIP_MAGIC, header_len, total_len)
        rec.raw_header = bytes(header)
        rec.fields["track_id"] = track_id
        rec.fields["num_mhods"] = 1
        rec.fields["podcastgroupflag"] = 0
        rec.fields["podcastgroupid"] = member_id
        rec.fields["podcastgroupref"] = group_ref
        rec.children.append(order_mhod)
        return rec

    def _add_to_master_playlists(self, track_id: int) -> None:
        """Add a track to the master playlist in all playlist MHSDs."""
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type in (2, 3) and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    if mhyp.fields.get("playlist_type") == 1:
                        pos = sum(1 for c in mhyp.children if c.magic == MHIP_MAGIC)
                        mhip = self._create_mhip(track_id, position=pos)
                        mhyp.children.append(mhip)
                        break

    def _mirror_to_type3(self, playlist_id: int, track_id: int) -> None:
        """Mirror a track addition to the MHSD type 3 playlist copy."""
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 3 and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    if mhyp.fields.get("playlist_id") == playlist_id:
                        pos = sum(1 for c in mhyp.children if c.magic == MHIP_MAGIC)
                        mhip = self._create_mhip(track_id, position=pos)
                        mhyp.children.append(mhip)
                        return

    def _unmirror_from_type3(self, playlist_id: int, track_id: int) -> None:
        """Mirror a track removal to the MHSD type 3 playlist copy."""
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 3 and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    if mhyp.fields.get("playlist_id") == playlist_id:
                        mhyp.children = [
                            c
                            for c in mhyp.children
                            if not (c.magic == MHIP_MAGIC and c.fields.get("track_id") == track_id)
                        ]
                        return

    def _remove_from_all_playlists(self, track_id: int) -> None:
        """Remove a track from all playlists."""
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type in (2, 3) and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    mhyp.children = [
                        c
                        for c in mhyp.children
                        if not (c.magic == MHIP_MAGIC and c.fields.get("track_id") == track_id)
                    ]

    def _make_basic_header(self, magic: bytes, header_len: int) -> bytes:
        """Create a minimal header for a record type."""
        header = bytearray(header_len)
        header[0:4] = magic
        put32lint(header, 4, header_len)
        return bytes(header)

    # ---- Display ----

    def __repr__(self) -> str:
        mp = self._mountpoint or "(standalone)"
        return f"<Database {mp}: {len(self._tracks)} tracks, {len(self._playlists)} playlists>"

    def summary(self) -> str:
        """Return a human-readable summary of the database."""
        lines = []
        if self._device:
            lines.append(f"Device: {self._device.model}")
            if self._device.firewire_guid:
                lines.append(f"FirewireGuid: {self._device.firewire_guid}")
            lines.append(f"Requires hash: {self._device.requires_hash}")
        lines.append(f"Tracks: {len(self._tracks)}")
        lines.append(f"Playlists: {len(self._playlists)}")
        if self._root:
            lines.append(f"DB version: {self._root.fields.get('db_version', '?')}")
        return "\n".join(lines)
