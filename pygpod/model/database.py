"""Main Database class - high-level iPod database API.

This is the primary entry point for pygpod, equivalent to Itdb_iTunesDB in libgpod.
"""

from __future__ import annotations

import logging
import os
import pathlib
import random
import shutil
import stat
import struct
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

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
    MHOD_ID_SORT_ALBUM,
    MHOD_ID_SORT_ALBUMARTIST,
    MHOD_ID_SORT_ARTIST,
    MHOD_ID_SORT_COMPOSER,
    MHOD_ID_SORT_TITLE,
    MHOD_ID_SORT_TVSHOW,
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
from ..db.playcounts import (
    PLAY_COUNTS_COMMIT_FILE,
    OTGFile,
    OTGSkippedEntry,
    PendingPlayCount,
    PlayCountEntry,
    PlayCountSource,
    apply_matched_play_counts,
    file_sha256,
    read_commit_marker,
    read_otg_files,
    read_play_count_source,
    remove_consumed_files,
    remove_file,
    sha256_hex,
    write_commit_marker,
)
from ..db.sortindex import SortEntry, build_indexes, mhod52_body, mhod53_body
from ..db.transaction import Transaction, interrupted_commit
from ..db.transaction import recover as recover_interrupted_commit
from ..db.writelock import try_writer_lock, writer_lock
from ..db.writer import make_string_mhod, write_itunesdb
from ..device.device import Device
from ..device.fileutils import copy_track_to_ipod
from ..device.mountpoint import _build_empty_itunesdb, find_itunesdb, validate_mountpoint
from ..exceptions import (
    ArtworkDBError,
    ArtworkError,
    ConflictError,
    DatabaseError,
    DeviceError,
    HashError,
    MountPointError,
    PlaylistError,
    TrackError,
    UnsupportedFormatError,
)
from ..gapless import GAPLESS_FIELDS, apply_gapless_fields
from ..hash.checksum import update_checksums
from ..tags import extract_artwork, filetype_marker, read_tags, validate_media
from ..utils.compat import get64lint, put8int, put16lint, put32lint, put64lint, put_float

if TYPE_CHECKING:  # pragma: no cover
    from ..doctor import CheckReport, RepairAction
from ..utils.datetime import now_mac
from .config import DatabaseConfig
from .playlist import Playlist
from .track import Track

# read_tags keys that describe the audio data (taken from a converted file, not from its original)
_AUDIO_PROPERTIES = {"bitrate", "samplerate", "duration_ms", "file_size", "filetype"}

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

    Play counts: the iPod records plays, skips, ratings and bookmarks since the
    last sync in a separate file (Play Counts / iTunesStats / PlayCounts.plist).
    With ``apply_play_counts=True`` (default) they are merged into the tracks on
    open; with ``False`` they are only read, see pending_play_counts(),
    merge_play_counts() and clear_play_counts(). The file is deleted only after
    the next successful save(), so a session that does not save loses nothing.

    Opening completes or discards an interrupted save (``recover=True``, default), under the
    writer lock: while another process is saving, opening waits up to a few seconds for it and
    otherwise reads the files without recovering anything (save() completes a leftover save then).
    ``recover=False`` opens read-only-style without touching any file (used by check());
    save() then refuses while the journal of an interrupted save is still there.

    A damaged iTunesDB (a list that ends early at a broken record) raises ParseError: saving
    what is readable would drop every track or playlist after the damaged one. With
    ``partial=True`` the readable part is loaded instead and described in ``damage``;
    save() then refuses unless ``force=True`` (which keeps only the readable part).
    """

    def __init__(
        self,
        mountpoint: Optional[str] = None,
        config: Optional[DatabaseConfig] = None,
        apply_play_counts: bool = True,
        recover: bool = True,
        partial: bool = False,
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
        self._pc_source: Optional[PlayCountSource] = None
        # (track, entry) bound when the file was read: the entries are positional, so they
        # must not shift onto other tracks when tracks are removed before the merge
        self._pc_pending: List[Tuple[Track, PlayCountEntry]] = []
        self._pc_consume = False
        self._otg_files: List[OTGFile] = []
        self._otg_tracks: List[Track] = []
        self._otg_consume: List[OTGFile] = []
        self._otg_resolved: Dict[str, List[Track]] = {}
        self._otg_skipped: List[OTGSkippedEntry] = []
        self._ids: Optional[List[Any]] = None
        self._pending_deletes: List[Tuple[str, str]] = []  # (iPod path, file) to delete on save
        self._recover = recover
        self._partial = partial
        self._damage: List[str] = []
        self._tz_shift = self._timezone_shift(mountpoint)

        if mountpoint:
            logger.info("Opening database at %s", mountpoint)
            self._load_from_mountpoint(mountpoint, apply_play_counts)

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
        db._pc_source = None
        db._pc_pending = []
        db._pc_consume = False
        db._otg_files = []
        db._otg_tracks = []
        db._otg_consume = []
        db._otg_resolved = {}
        db._otg_skipped = []
        db._ids = None
        db._pending_deletes = []
        db._recover = True
        db._partial = False
        db._damage = []
        db._tz_shift = db._timezone_shift(None)

        with open(path, "rb") as f:
            data = f.read()

        db._raw_data = data
        db._root = parse_itunesdb(data)
        db._build_model()
        return db

    def _timezone_shift(self, mountpoint: Optional[str]) -> int:
        mode = self._config.timestamps
        if mode == "utc":
            return 0
        if mode != "local":
            raise ValueError(f"DatabaseConfig.timestamps must be 'local' or 'utc': {mode!r}")
        from ..utils.timezone import device_timezone_shift

        return device_timezone_shift(mountpoint)

    @property
    def timezone_shift(self) -> int:
        """The UTC offset (seconds east) the iPod's timestamps are read and written in.

        With ``DatabaseConfig.timestamps='local'`` (default) the timezone of the iPod's
        ``Device/Preferences``, else the host's UTC offset when the database was opened, as
        libgpod does; 0 with ``timestamps='utc'``.
        """
        return self._tz_shift

    def _load_from_mountpoint(self, mountpoint: str, apply_play_counts: bool = True) -> None:
        """Load database from iPod mount point."""
        if not validate_mountpoint(mountpoint):
            raise MountPointError(
                f"Not a valid iPod mount point: {mountpoint}\n"
                f"Expected to find iPod_Control/iTunes/ directory."
            )
        itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")
        if not self._recover:
            self._read_from_mountpoint(mountpoint, apply_play_counts, cleanup=False)
            return
        # recover and read under the writer lock: never touch the staging files or the journal
        # of a save in progress, and never read a half-applied commit
        with try_writer_lock(itunes_dir) as held:
            if held:
                recover_interrupted_commit(mountpoint)
            else:
                logger.info(
                    "Another writer is saving (or the iPod is read-only): reading %s without "
                    "recovering an interrupted save",
                    mountpoint,
                )
            self._read_from_mountpoint(mountpoint, apply_play_counts, cleanup=held)

    def _read_from_mountpoint(
        self, mountpoint: str, apply_play_counts: bool, cleanup: bool
    ) -> None:
        self._device = Device.from_mountpoint(mountpoint)
        db_path = find_itunesdb(mountpoint)
        if db_path is None:
            raise DatabaseError(f"iTunesDB not found at {mountpoint}/iPod_Control/iTunes/iTunesDB")

        with open(db_path, "rb") as f:
            data = f.read()

        self._raw_data = data
        damage: Optional[List[str]] = [] if self._partial else None
        self._root = parse_itunesdb(data, damage=damage)
        self._damage = damage or []
        self._build_model()
        logger.info("Loaded %d tracks, %d playlists", len(self._tracks), len(self._playlists))

        self._load_play_counts(mountpoint, cleanup)
        self._load_otg(mountpoint)
        if apply_play_counts:
            self.merge_play_counts()

    # ---- Play counts ----

    def _itunes_dir(self) -> str:
        assert self._mountpoint is not None
        return os.path.join(self._mountpoint, "iPod_Control", "iTunes")

    def _load_play_counts(self, mountpoint: str, cleanup: bool = True) -> None:
        """Read the iPod's play count file without consuming it.

        Finishes an interrupted consume first: if the commit marker says the
        current play count file is already merged into the current iTunesDB
        (a previous save() stopped before deleting it), the file is not applied
        again but deleted now.
        """
        itunes_dir = os.path.join(mountpoint, "iPod_Control", "iTunes")
        try:
            source = read_play_count_source(mountpoint)
        except Exception:
            logger.warning("Failed to read play counts", exc_info=True)
            source = None

        marker = read_commit_marker(itunes_dir)
        if marker is not None:
            committed = marker.get("itunesdb_sha256") == sha256_hex(self._raw_data or b"")
            if (
                committed
                and source is not None
                and marker.get("file") == source.name
                and marker.get("file_sha256") == source.sha256
            ):
                logger.info("Play counts in %s were already merged; finishing cleanup", source.name)
                source = None
                if cleanup and remove_consumed_files(
                    itunes_dir, marker["file"], marker["file_sha256"], marker["others"]
                ):
                    remove_file(os.path.join(itunes_dir, PLAY_COUNTS_COMMIT_FILE))
            elif cleanup:
                # Either the save never committed (old iTunesDB still in place) or
                # the play count file was rewritten since: the counts are not applied yet.
                # (Not recovering: an interrupted save may still commit - leave the marker.)
                remove_file(os.path.join(itunes_dir, PLAY_COUNTS_COMMIT_FILE))

        if source is None:
            return
        self._pc_source = source
        from ..db.playcounts import match_play_counts

        self._pc_pending = match_play_counts(self._tracks, list(source.entries))

    # ---- On-The-Go playlists ----

    def _load_otg(self, mountpoint: str) -> None:
        """Read the iPod's OTG playlist files (not imported until asked)."""
        try:
            self._otg_files = read_otg_files(mountpoint)
        except Exception:
            logger.warning("Failed to read OTG playlists", exc_info=True)
            self._otg_files = []
        self._otg_tracks = list(self._tracks)  # OTG entries are positions in this order

    def _otg_entry_problem(self, index: int, live: Set[int]) -> Optional[str]:
        """Why an OTG entry cannot be imported (None if it can)."""
        from ..exceptions import UnsafePathError
        from ..utils.encoding import ipod_path_to_os

        if index >= len(self._otg_tracks):
            return "no such track"
        track = self._otg_tracks[index]
        if id(track) not in live:
            return "track was removed"
        if not track.ipod_path or self._mountpoint is None:
            return "track has no file"
        try:
            path = ipod_path_to_os(track.ipod_path, self._mountpoint)
            st = os.stat(path)
        except UnsafePathError:
            return "unsafe file path"
        except FileNotFoundError:
            return "file missing"
        except OSError as e:
            return f"file unreadable ({e.strerror or e})"
        if not stat.S_ISREG(st.st_mode):
            return "not a regular file"
        if st.st_size == 0:
            return "file is empty"
        if track.file_size and st.st_size < track.file_size:
            return f"file truncated ({st.st_size} of {track.file_size} bytes)"
        if not os.access(path, os.R_OK):
            return "file unreadable"
        return None

    def _otg_resolve(self, f: OTGFile) -> List[Track]:
        """The importable tracks of an OTG file; bad entries are logged and left out once."""
        if f.name not in self._otg_resolved:
            tracks: List[Track] = []
            live = {id(t) for t in self._tracks}
            for pos, index in enumerate(f.indices):
                problem = self._otg_entry_problem(index, live)
                if problem is None:
                    tracks.append(self._otg_tracks[index])
                    continue
                self._otg_skipped.append(OTGSkippedEntry(f.name, pos, index, problem))
                logger.warning(
                    "%s entry %d (track %d) left out: %s", f.name, pos + 1, index, problem
                )
            self._otg_resolved[f.name] = tracks
        return self._otg_resolved[f.name]

    def pending_otg_playlists(self) -> List[List[Track]]:
        """On-The-Go playlists made on the iPod and not imported yet, as track lists.

        Entries whose track does not exist or whose file is missing or broken (empty,
        truncated, unreadable) are left out and logged; see otg_skipped_entries().
        Playlists with no valid entry are left out.
        """
        return [tracks for f in self._otg_files if (tracks := self._otg_resolve(f))]

    def otg_skipped_entries(self) -> List[OTGSkippedEntry]:
        """On-The-Go entries left out so far because their track or file is bad."""
        for f in self._otg_files:
            self._otg_resolve(f)
        return list(self._otg_skipped)

    def import_otg_playlists(self, name: str = "OTG Playlist {n}") -> List[Playlist]:
        """Create a playlist per On-The-Go playlist (``name`` formatted with n = 1, 2, ...).

        Bad entries (see pending_otg_playlists()) are left out and logged; the valid
        ones are imported. The OTG files are deleted by the next successful save(), in
        the same transaction as the iTunesDB, so a crash never imports them twice.
        Calling it again before the save imports nothing.
        """
        created: List[Playlist] = []
        for f in self._otg_files:
            tracks = self._otg_resolve(f)
            if tracks:
                pl = self.create_playlist(name.format(n=len(created) + 1))
                for t in tracks:
                    self.add_track_to_playlist(pl, t)
                created.append(pl)
            self._otg_consume.append(f)
        self._otg_files = []
        if self._otg_consume:
            self._modified = True
        return created

    def _otg_before_save(self) -> list:
        """Apply DatabaseConfig.otg_on_save to the pending OTG files: import or clear them,
        or ("keep") return (path, new bytes, file) for each file whose entries must follow
        the tracks to their new positions in the database being written."""
        mode = self._config.otg_on_save
        if mode not in ("keep", "import", "clear"):
            raise ValueError(f"DatabaseConfig.otg_on_save must be keep/import/clear: {mode!r}")
        if not self._otg_files or not self._mountpoint:
            return []
        if mode == "import":
            self.import_otg_playlists()
            return []
        if mode == "clear":
            self.clear_otg_playlists()
            return []
        from ..db.playcounts import remap_otg_data

        now = {id(t): i for i, t in enumerate(self._tracks)}
        mapping = {i: now.get(id(t)) for i, t in enumerate(self._otg_tracks)}
        out = []
        for f in self._otg_files:
            if all(mapping.get(i) == i for i in f.indices):
                continue  # every entry still points at its track
            path = os.path.join(self._itunes_dir(), f.name)
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if sha256_hex(data) != f.sha256:  # changed on the iPod since we read it
                continue
            out.append((path, remap_otg_data(data, mapping), f))
        return out

    def _otg_after_save(self, rewrites: list) -> None:
        """After a save in "keep" mode: OTG entries now refer to the saved track order."""
        from ..db.playcounts import OTGFile, parse_otg_playlists

        new = {f.name: data for _, data, f in rewrites}
        files = []
        for f in self._otg_files:
            if f.name in new:
                lists = parse_otg_playlists(new[f.name])
                f = OTGFile(f.name, sha256_hex(new[f.name]), lists[0] if lists else [])
            files.append(f)
        self._otg_files = files
        self._otg_tracks = list(self._tracks)
        self._otg_resolved = {}
        self._otg_skipped = []

    def clear_otg_playlists(self) -> None:
        """Drop the On-The-Go playlists without importing them (deleted by the next save())."""
        self._otg_consume += self._otg_files
        if self._otg_files:
            self._modified = True
        self._otg_files = []

    def pending_play_counts(self) -> List[PendingPlayCount]:
        """Play count updates read from the iPod that are not merged yet.

        Matched to tracks by position (the iPod writes one entry per track in
        database order) when the database was opened; entries of tracks removed since and
        entries without any change are skipped.
        """
        result: List[PendingPlayCount] = []
        for track, e in self._live_play_counts():
            if (
                e.play_count > 0
                or e.time_played > 0
                or e.rating >= 0
                or e.skip_count > 0
                or e.time_skipped > 0
                or e.bookmark_time > 0
            ):
                result.append(PendingPlayCount(track, *e[:6]))
        return result

    def merge_play_counts(self) -> int:
        """Merge pending play counts into the tracks (in memory).

        The play count file is deleted after the next successful save().

        Returns:
            Number of tracks updated.
        """
        if self._pc_source is None or not self._pc_pending:
            return 0
        updated = apply_matched_play_counts(self._live_play_counts())
        self._pc_pending = []
        self._pc_consume = True
        self._modified = True
        logger.info("Merged play counts from %s: %d tracks updated", self._pc_source.name, updated)
        return updated

    def _live_play_counts(self) -> List[Tuple[Track, PlayCountEntry]]:
        return [(t, e) for t, e in self._pc_pending if self._track_lookup.get(t.track_id) is t]

    def clear_play_counts(self) -> None:
        """Drop pending play counts without merging them.

        For callers that apply the values themselves. The play count file is
        deleted after the next successful save().
        """
        if self._pc_source is None:
            return
        self._pc_pending = []
        self._pc_consume = True
        self._modified = True

    def _finish_play_count_cleanup(self) -> None:
        """Delete all play count files seen at open and the commit marker (after save).

        Like libgpod, every variant (Play Counts, iTunesStats, PlayCounts.plist) is
        removed, not only the one that was read; a file the iPod rewrote since is kept.
        """
        source = self._pc_source
        if source is None:
            return
        itunes_dir = self._itunes_dir()
        if remove_consumed_files(itunes_dir, source.name, source.sha256, source.others):
            remove_file(os.path.join(itunes_dir, PLAY_COUNTS_COMMIT_FILE))
        self._pc_source = None
        self._pc_consume = False

    def _build_model(self) -> None:
        """Build high-level model objects from parsed records."""
        self._tracks = []
        self._playlists = []
        self._track_lookup = {}
        self._ids = None

        if not self._root:
            return

        # Find tracks (MHSD type 1)
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type == 1 and mhsd.children:
                mhlt = mhsd.children[0]
                for mhit_rec in mhlt.children:
                    track = Track.from_record(mhit_rec)
                    track._tz_shift = self._tz_shift
                    track._on_change = self._mark_modified
                    self._tracks.append(track)
                    self._track_lookup[track.track_id] = track

        # Find playlists (MHSD type 2 - primary playlists)
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type == 2 and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp_rec in mhlp.children:
                    pl = Playlist.from_record(mhyp_rec, self._track_lookup)
                    pl._on_change = self._mark_modified
                    self._playlists.append(pl)
        self._remember_derived_inputs()

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

    # Keyword arguments of add_track() applied through Track setters after the
    # record is created (the rest go into the MHIT/MHODs directly).
    _POST_SETTERS = (
        "rating",
        "play_count",
        "skip_count",
        "bookmark_time",
        "time_played",
        "remember_position",
        "skip_when_shuffling",
        "mark_unplayed",
        "bpm",
        "explicit_flag",
        "time_released",
    )

    def add_track(
        self,
        filepath: str,
        copy: bool = True,
        convert: bool = False,
        *,
        video_size: str = "small",
        **kwargs: Any,
    ) -> Track:
        """Add a track to the iPod.

        If mutagen is installed, tags are read automatically from the file.
        Additional/override metadata can be passed as keyword arguments and
        always wins over the file's tags.

        Args:
            filepath: Path to the audio file to add.
            copy: Copy the file to iPod_Control/Music/Fxx (default). False: the
                file is already on the iPod (must be inside the mount point's
                iPod_Control/Music) and is referenced in place.
            convert: Convert audio this iPod cannot play (FLAC, Ogg, Opus, WMA,
                > 48 kHz, > 2 channels ...) with ffmpeg instead of refusing it:
                lossless sources become Apple Lossless where the iPod plays it,
                everything else AAC (see pygpod.convert). Tags and artwork come
                from the original file. Needs copy=True. Video (on iPods that play
                it) that does not fit the iPod's H.264 profile (checked with
                ffprobe) becomes an .m4v with H.264 Baseline + AAC; video that fits
                is copied unchanged.
            video_size: Target of video conversion: "small" (inside 320x240,
                plays on every video iPod, default) or "large" (inside 640x480
                where the model plays it: 5.5G, nano 3G-5G, Classic).
            **kwargs: Metadata overrides: title, artist, album, albumartist
                (alias album_artist), genre, composer, comment, grouping, year,
                track_number, total_tracks, cd_number, total_cds, compilation,
                media_type, category, description, podcast_url, podcast_rss,
                rating (0-100), bpm, explicit_flag (1 explicit, 2 clean),
                time_released (datetime), sort_title, sort_artist, sort_album,
                sort_albumartist, sort_composer, sort_tvshow, keywords,
                play_count, skip_count, bookmark_time (ms),
                time_played (datetime), remember_position, skip_when_shuffling,
                mark_unplayed, soundcheck (MHIT value; default from the file's
                iTunNORM / ReplayGain tags, see tags.read_soundcheck; 'analyze'
                measures the audio with ffmpeg (the converted file with convert=True),
                'auto' measures only files without gain tags), pregap,
                postgap, sample_count, gapless_data, gapless_track_flag,
                gapless_album_flag (default from the LAME tag / iTunSMPB, see
                pygpod.gapless); gapless='estimate' (or 'auto') estimates them for
                MP3s without a LAME/Info tag and AAC without iTunSMPB or edit list
                (experimental, see pygpod.gapless.estimate_gapless), gapless={...}
                sets them,
                chapters (list of (start_ms, title) tuples or a ChapterData;
                default from the file: MP4 chapter track / Nero chpl, ID3 CHAP,
                see pygpod.chapters.read_chapters; [] = no chapters),
                cover_art (image path or bytes); for videos also
                tvshow, tvepisode, tvnetwork, season_number, episode_number.
                A video file (an MP4 with a video track) added without
                media_type becomes MEDIATYPE_VIDEO, or MUSICVIDEO/TVSHOW from its
                iTunes media kind atom; video tracks get movie_flag set.

        Returns:
            The new Track object.

        Raises:
            TrackError: If the file doesn't exist or can't be processed.
        """
        if not os.path.isfile(filepath):
            raise TrackError(f"File not found: {filepath}")

        if not self._mountpoint:
            raise TrackError("Cannot add tracks without a mount point")
        self._check_numeric_kwargs(kwargs)

        soundcheck_mode = kwargs.get("soundcheck")
        if isinstance(soundcheck_mode, str):
            if soundcheck_mode not in ("auto", "analyze"):
                raise TrackError(
                    f"soundcheck must be an int, 'auto' or 'analyze', not {soundcheck_mode!r}"
                )
            if soundcheck_mode == "analyze":
                from ..convert import find_ffmpeg

                find_ffmpeg()  # UnsupportedFormatError before anything is copied
        gapless = kwargs.get("gapless")
        if isinstance(gapless, str) and gapless not in ("auto", "estimate"):
            raise TrackError(f"gapless must be a dict, 'auto' or 'estimate', not {gapless!r}")
        if isinstance(gapless, dict) and not set(gapless) <= set(GAPLESS_FIELDS):
            unknown = sorted(set(gapless) - set(GAPLESS_FIELDS))
            raise TrackError(f"unknown gapless fields: {unknown}")

        # Check the content plays on this iPod before anything is copied or added
        generation = self._device.generation if self._device else None
        converted = None
        try:
            info = validate_media(filepath, generation)
        except UnsupportedFormatError:
            if not convert:
                raise
            if not copy:
                raise TrackError(f"convert=True needs copy=True: {filepath}") from None
            from ..convert import convert_for_ipod

            converted = convert_for_ipod(filepath, generation, video_size=video_size)
        else:
            if convert and copy and info.is_video:
                from ..convert import convert_video_for_ipod, video_needs_conversion

                if video_needs_conversion(filepath, generation):
                    converted = convert_video_for_ipod(filepath, generation, video_size)
        try:
            return self._add_track_file(filepath, converted, copy, kwargs)
        finally:
            if converted is not None:
                os.unlink(converted)

    def _add_track_file(
        self, filepath: str, converted: Optional[str], copy: bool, kwargs: dict
    ) -> Track:
        """add_track after validation: `converted` (or the original) is copied, tags come from
        the original."""
        source = converted or filepath
        if converted is not None:
            validate_media(converted, self._device.generation if self._device else None)

        # Read tags (the converted file's audio properties, the original's text tags)
        tags = read_tags(filepath)
        if converted is not None:
            converted_tags = read_tags(converted)
            tags.update({k: v for k, v in converted_tags.items() if k in _AUDIO_PROPERTIES})
            if "media_type" in converted_tags:  # e.g. .mkv -> .m4v: a video now
                tags.setdefault("media_type", converted_tags["media_type"])
            # gapless sample data belongs to the converted file (its own encoder delay)
            for key in GAPLESS_FIELDS:
                if key != "gapless_album_flag":
                    tags.pop(key, None)
                    if key in converted_tags:
                        tags[key] = converted_tags[key]
        if isinstance(kwargs.get("soundcheck"), str):
            self._measure_soundcheck(kwargs.pop("soundcheck"), source, tags)
        gapless = kwargs.pop("gapless", None)
        if isinstance(gapless, dict):
            kwargs.update(gapless)  # explicit values, like the separate kwargs
        elif gapless and "sample_count" not in tags:  # 'auto' / 'estimate': exact data wins
            from ..gapless import estimate_gapless

            tags.update(estimate_gapless(source) or {})
        if "album_artist" in kwargs:
            kwargs["albumartist"] = kwargs.pop("album_artist")
        # explicit chapters win (chapters=[] or None: none); else the original file's chapters
        if "chapters" in kwargs:
            chapters = kwargs.pop("chapters")
        else:
            from ..chapters import read_chapters

            chapters = read_chapters(filepath)
        post = {k: kwargs.pop(k) for k in self._POST_SETTERS if k in kwargs}
        tags.update(kwargs)  # User overrides
        for key in self._POST_SETTERS:  # rating, bpm ... read from the file
            if key not in post and key in tags:
                post[key] = tags[key]

        self._require_cover_writable(filepath, tags.get("cover_art"))
        num_dirs = self._device.music_dirs if self._device else 50
        cfg = self._config
        if copy:
            ipod_path = copy_track_to_ipod(
                source,
                self._mountpoint,
                num_dirs,
                prefix=cfg.filename_prefix,
                rand_len=cfg.filename_rand_len,
                charset=cfg.filename_charset,
                rng=self._rng,
            )
        else:
            from ..utils.encoding import os_path_to_ipod

            music = os.path.realpath(os.path.join(self._mountpoint, "iPod_Control", "Music"))
            real = os.path.realpath(filepath)
            if os.path.commonpath([music, real]) != music:
                raise TrackError(f"copy=False needs a file inside {music}: {filepath}")
            ipod_path = os_path_to_ipod(real, os.path.realpath(self._mountpoint))

        # Build and validate the whole track before it joins the database: a bad value
        # (e.g. rating='x') leaves neither a hidden MHIT nor the copied file behind
        try:
            track_id = self._next_track_id()
            dbid = self._next_dbid()
            mhit = self._create_mhit(track_id, dbid, tags, ipod_path, source)
            track = Track.from_record(mhit)
            track._tz_shift = self._tz_shift
            if chapters:
                track.chapters = chapters
            for key, value in post.items():
                setattr(track, key, value)
        except BaseException:
            if copy:
                from ..utils.encoding import ipod_path_to_os

                remove_file(ipod_path_to_os(ipod_path, self._mountpoint))
            raise

        # Add to track list MHSD
        self._get_or_create_mhlt().children.append(mhit)
        track._on_change = self._mark_modified
        self._tracks.append(track)
        self._track_lookup[track_id] = track
        index = self._id_index()
        index[0].add(track_id)
        index[1].add(dbid)
        index[2] = max(index[2], track_id)

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

    @staticmethod
    def _check_numeric_kwargs(kwargs: Dict[str, Any]) -> None:
        """ValueError for a numeric add_track() value that is no integer or does not fit its
        field (it would wrap around on save), before anything is copied."""
        from .track import NUMERIC_FIELD_BITS, checked_int

        for key, bits in NUMERIC_FIELD_BITS.items():
            if kwargs.get(key) is not None:
                checked_int(key, kwargs[key], bits)
        if kwargs.get("rating") is not None:
            checked_int("rating", kwargs["rating"], 32, signed=True)
        if kwargs.get("soundcheck") is not None and not isinstance(kwargs["soundcheck"], str):
            checked_int("soundcheck", kwargs["soundcheck"], 32)

    @staticmethod
    def _measure_soundcheck(mode: str, source: str, tags: Dict[str, Any]) -> None:
        """soundcheck='analyze' (always measure) or 'auto' (measure only without gain tags)."""
        if mode == "auto" and tags.get("soundcheck"):
            return
        from ..exceptions import UnsupportedFormatError
        from ..tags import analyze_soundcheck

        try:
            value = analyze_soundcheck(source)
        except UnsupportedFormatError:
            if mode == "analyze":
                raise
            logger.warning("No ffmpeg for the Sound Check analysis of %s: none set", source)
            value = None
        if value is not None:
            tags["soundcheck"] = value
        elif mode == "auto":
            tags["soundcheck"] = 0

    def _get_artwork_manager(self):
        """Lazily create and return the ArtworkManager instance."""
        if not hasattr(self, "_artwork_manager") or self._artwork_manager is None:
            from ..device.models import IpodGeneration
            from .artwork_manager import ArtworkManager

            generation = self._device.generation if self._device else None
            if generation is None:
                generation = IpodGeneration.UNKNOWN

            # the device's own cover formats (SysInfoExtended AlbumArt) win over the table;
            # libgpod writes them in reverse order
            formats = self._device.get_sysinfo_cover_art_formats() if self._device else None
            self._artwork_manager = ArtworkManager(
                self._mountpoint,
                generation=generation,
                formats=list(reversed(formats)) if formats else None,
            )
            if self._artwork_manager.created_new:
                self._clear_artwork_links()
        return self._artwork_manager

    def _clear_artwork_links(self) -> None:
        """Clear the artwork references of every track (their images are not in the
        ArtworkDB, which is missing or replaced by an empty one)."""
        for t in self._tracks:
            rec = t.record
            if rec is None:
                continue
            f = rec.fields
            if f.get("mhii_link") or f.get("artwork_count") or f.get("has_artwork") == 1:
                self._set_artwork_fields(rec, 0, 0, 0)
                t._touch()
                self._modified = True

    def _add_artwork_for_track(
        self, mhit: Record, dbid: int, filepath: str, cover_art: str = None
    ) -> None:
        """Extract artwork from audio file and render its thumbnails now.

        The thumbnails are rendered right away (save() does not have to do all the
        heavy lifting); they are written to the .ithmb files, with the ArtworkDB,
        by the next save(), after its conflict check.

        Args:
            mhit: The MHIT record for the track.
            dbid: Database ID of the track.
            filepath: Path to the audio file.
            cover_art: Optional fallback image path if no embedded artwork.
        """
        if not self._mountpoint:
            return
        if not self._shows_cover_art():
            logger.debug("%s shows no cover art: none added", self._device.model)
            return

        try:
            if isinstance(cover_art, (bytes, bytearray)):
                art_data = bytes(cover_art)  # explicit image wins over embedded art
            else:
                art_data = extract_artwork(filepath)
                if not art_data and cover_art and os.path.isfile(cover_art):
                    with open(cover_art, "rb") as f:
                        art_data = f.read()
            if not art_data:
                return
            self._attach_artwork(mhit, dbid, art_data)
            logger.info("Artwork processed for track dbid=%d (%d bytes)", dbid, len(art_data))
        except ArtworkDBError:
            raise
        except Exception:
            logger.debug("Failed to process artwork", exc_info=True)

    def _require_cover_writable(self, filepath: str, cover_art: Any) -> None:
        """ArtworkDBError before a track with a cover is added while the ArtworkDB on the
        iPod cannot be read (writing a new one would drop every cover it holds)."""
        if not self._mountpoint or not self._shows_cover_art():
            return
        mgr = getattr(self, "_artwork_manager", None)
        if mgr is not None and mgr.load_error is None:
            return  # the usual case: no second look at the file's cover
        if isinstance(cover_art, (bytes, bytearray)):
            has_cover = bool(cover_art)
        else:
            has_cover = bool(cover_art and os.path.isfile(cover_art)) or bool(
                extract_artwork(filepath)
            )
        if has_cover:
            self._get_artwork_manager().require_readable()

    def reset_artwork(self) -> None:
        """Start an empty ArtworkDB with the next save(): every track loses its cover.

        Meant for an ArtworkDB that cannot be read (check() reports
        ``artwork_db_unreadable``; adding or changing covers raises ArtworkDBError
        until then). The tracks' artwork references are cleared, so none points at a
        missing image; the old .ithmb files are deleted when the save commits.
        """
        if not self._mountpoint:
            raise DatabaseError("No mount point")
        self._get_artwork_manager().start_empty()
        self._clear_artwork_links()
        self._modified = True

    @staticmethod
    def _set_artwork_fields(mhit: Record, count: int, size: int, image_id: int) -> None:
        header = bytearray(mhit.raw_header)
        if len(header) > 0x7D:  # u16 at 0x7C needs bytes 0x7C-0x7D
            put16lint(header, 0x7C, count)  # artwork_count
        if len(header) > 0x83:  # u32 at 0x80 needs bytes 0x80-0x83
            put32lint(header, 0x80, size)  # artwork_size
        if len(header) > 0xA4:  # u8 at 0xA4 needs byte 0xA4
            put8int(header, 0xA4, 1 if count else 0)  # has_artwork
        if len(header) > 0x163:  # u32 at 0x160 needs bytes 0x160-0x163
            put32lint(header, 0x160, image_id)  # mhii_link
        mhit.raw_header = bytes(header)
        # Mirror into fields so Track properties reflect the new state
        # without requiring a reload (needed for same-session remove_track)
        mhit.fields["artwork_count"] = count
        mhit.fields["artwork_size"] = size
        mhit.fields["has_artwork"] = 1 if count else 0
        mhit.fields["mhii_link"] = image_id

    def _shows_cover_art(self) -> bool:
        """False for identified iPods without cover art (1G-4G, mini, shuffles): no
        ArtworkDB/thumbnails for them, like libgpod. Unidentified models get artwork (the
        artwork manager falls back to the Classic formats)."""
        from ..device.models import IpodGeneration

        generation = self._device.generation if self._device else None
        if generation is None or generation is IpodGeneration.UNKNOWN:
            return True
        return bool(self._device.get_cover_art_formats())

    def _attach_artwork(self, mhit: Record, dbid: int, art_data: bytes) -> bool:
        image_id = self._get_artwork_manager().add_artwork_data(dbid, art_data, save=False)
        if image_id is None:
            return False
        self._set_artwork_fields(mhit, 1, len(art_data), image_id)
        return True

    def set_artwork(self, track: Track, image: Optional[bytes]) -> None:
        """Replace a track's cover art with ``image`` (JPEG/PNG bytes); None removes it.

        Written with the next save(). On iPods without cover art (1G-4G, mini, shuffles)
        an image is ignored.

        Raises:
            ArtworkError: the image could not be converted (e.g. Pillow missing).
                Like any error decoding the image, the track keeps its current cover.
            ArtworkDBError: the ArtworkDB on the iPod cannot be read (see reset_artwork()).
            ValueError: the track belongs to another database (or was removed).
        """
        if not self._mountpoint or track.record is None:
            raise TrackError("Cannot set artwork without a mount point")
        self._check_own_tracks([track])
        mgr = self._get_artwork_manager()
        mgr.require_readable()
        old_link = track.mhii_link if track.has_artwork else 0
        if image and not self._shows_cover_art():
            logger.info("%s shows no cover art: image ignored", self._device.model)
            image = None
        if image:
            # the new thumbnails first: invalid data raises before the old cover is dropped
            image_id = mgr.add_artwork_data(track.dbid, image, save=False)
            if image_id is None:
                raise ArtworkError("could not create artwork thumbnails (is Pillow installed?)")
            if old_link:
                mgr.remove_artwork(old_link)
            self._set_artwork_fields(track.record, 1, len(image), image_id)
        else:
            if old_link:
                mgr.remove_artwork(old_link)
            self._set_artwork_fields(track.record, 0, 0, 0)
        self._modified = True

    def remove_track(self, track: Track, delete_file: bool = False) -> None:
        """Remove a track from the database.

        Args:
            track: Track to remove.
            delete_file: If True, also delete the file from the iPod - with the next
                successful save(), and only if no remaining track uses the same file.

        Raises:
            UnsafePathError: delete_file is set and the track's path points
                outside the mount point (nothing is changed).
            ValueError: the track belongs to another database (or was removed already).
        """
        self.remove_tracks([track], delete_files=delete_file)

    def remove_tracks(self, tracks: Iterable[Track], delete_files: bool = False) -> None:
        """Remove several tracks in one pass over the track list and the playlists.

        Same effect as remove_track() per track, but linear in the size of the database
        instead of per track (removing thousands of tracks one by one is quadratic).

        Raises:
            UnsafePathError: delete_files is set and a track's path points outside the
                mount point (nothing is changed).
            ValueError: a track belongs to another database (or was removed already);
                nothing is changed.
        """
        tracks = list(tracks)
        if not tracks:
            return
        self._check_own_tracks(tracks)
        # Validate every file path before changing anything (raises UnsafePathError)
        fpaths: List[Tuple[str, str]] = []
        if delete_files and self._mountpoint:
            from ..utils.encoding import ipod_path_to_os

            fpaths = [
                (t.ipod_path, ipod_path_to_os(t.ipod_path, self._mountpoint))
                for t in tracks
                if t.ipod_path
            ]

        ids = {t.track_id for t in tracks}
        self._remove_from_all_playlists(ids)
        mhlt = self._get_or_create_mhlt()
        mhlt.children = [
            c
            for c in mhlt.children
            if not (c.magic == MHIT_MAGIC and c.fields.get("track_id") in ids)
        ]
        self._tracks = [t for t in self._tracks if t.track_id not in ids]
        index = self._ids
        for t in tracks:
            self._track_lookup.pop(t.track_id, None)
            if index is not None:
                index[0].discard(t.track_id)
                index[1].discard(t.dbid)
            if self._mountpoint and t.has_artwork and t.mhii_link:
                self._get_artwork_manager().remove_artwork(t.mhii_link)
        # deleted by the next successful save (in its commit), unless a remaining track
        # still uses the file then
        self._pending_deletes.extend(fpaths)
        self._modified = True

    def _check_own_tracks(self, tracks: Iterable[Track]) -> None:
        """ValueError for a Track that is not (or no longer) part of this database: its
        track id may name another track here."""
        for t in tracks:
            if self._track_lookup.get(t.track_id) is not t:
                raise ValueError(f"{t!r} does not belong to this database")

    def _check_own_playlist(self, playlist: Playlist) -> None:
        if not any(p is playlist for p in self._playlists):
            raise ValueError(f"{playlist!r} does not belong to this database")

    # ---- Playlist Operations ----

    def get_playlist(self, name: str) -> Optional[Playlist]:
        """First non-master playlist with this name, or None."""
        for pl in self._playlists:
            if not pl.is_master and pl.name == name:
                return pl
        return None

    def set_playlist(self, name: str, tracks: List[Track]) -> Playlist:
        """Create the playlist ``name`` or replace its members with ``tracks`` (in order).

        Raises:
            ValueError: a track belongs to another database (or was removed).
        """
        self._check_own_tracks(tracks)
        pl = self.get_playlist(name)
        if pl is None:
            pl = self.create_playlist(name)
        current = [self._track_lookup[tid] for tid in pl.track_ids if tid in self._track_lookup]
        if [t.track_id for t in current] == [t.track_id for t in tracks]:
            return pl
        for t in current:
            self.remove_track_from_playlist(pl, t)
        for t in tracks:
            self.add_track_to_playlist(pl, t)
        self._modified = True
        return pl

    def delete_playlist_by_name(self, name: str) -> bool:
        """Delete every non-master playlist called ``name``; True if one existed."""
        found = [pl for pl in self._playlists if not pl.is_master and pl.name == name]
        for pl in found:
            self.delete_playlist(pl)
        return bool(found)

    # Average iTunesDB bytes per track, measured with typical metadata (MHIT,
    # string MHODs, master playlist entries in MHSD 2 and 3, sort indexes,
    # album list). Used when the database has no tracks to measure yet.
    DEFAULT_BYTES_PER_TRACK = 1450

    def check(self) -> "CheckReport":
        """Read-only consistency check of the database and its iPod (see pygpod.doctor):
        orphan/missing/empty/resized media files, duplicate paths, dangling playlist items,
        tracks missing from the master playlist, broken artwork references, checksum,
        free space, leftover temporary files and a stale write lock."""
        from ..doctor import check

        return check(self)

    def repair(
        self,
        report: "Optional[CheckReport]" = None,
        delete_orphans: bool = False,
        dry_run: bool = False,
    ) -> "List[RepairAction]":
        """Fix the issues of ``report`` (default: check()) and save once; orphan media files
        are deleted only with ``delete_orphans``. ``dry_run`` returns the actions only.

        Raises ConflictError, changing nothing, when another writer holds the lock or saved
        the database after this object loaded it: reopen it and check again."""
        from ..doctor import repair

        return repair(self, report, delete_orphans=delete_orphans, dry_run=dry_run)

    def estimate_db_size(self, extra_tracks: int = 0, extra_playlist_entries: int = 0) -> int:
        """Estimated iTunesDB size in bytes after adding tracks.

        Serialises the current database and extrapolates per track from it
        (or DEFAULT_BYTES_PER_TRACK for an empty database); playlist entries
        beyond the master playlist cost ~ 2 x 120 bytes each (MHIP + MHOD in
        both playlist sections). Use it to reserve space for the commit.
        """
        base = len(self._prepare_for_save())
        n = len(self._tracks)
        if n >= 200:  # small databases overstate it (one album/artist entry per track)
            entries = sum(len(pl.track_ids) for pl in self._playlists if not pl.is_master)
            per_track = (base - len(_build_empty_itunesdb()) - entries * 240) / n
            per_track = max(per_track, 600)
        else:
            per_track = self.DEFAULT_BYTES_PER_TRACK
        return int(base + extra_tracks * per_track + extra_playlist_entries * 240)

    def free_space(self) -> int:
        """Free bytes on the iPod's filesystem."""
        if not self._mountpoint:
            raise DatabaseError("No mount point")
        return shutil.disk_usage(self._mountpoint).free

    # ---- Smart playlists ----

    def _playlist_records(self, playlist_id: int) -> List[Record]:
        """The playlist's MHYP records in MHSD type 2 and its type 3 mirror."""
        out = []
        for mhsd in self._root.children if self._root else []:
            if mhsd.fields.get("mhsd_type") in (2, 3) and mhsd.children:
                for mhyp in mhsd.children[0].children:
                    if mhyp.magic == MHYP_MAGIC and mhyp.fields.get("playlist_id") == playlist_id:
                        out.append(mhyp)
        return out

    def _set_spl_mhods(self, playlist: Playlist, match, rules, prefs) -> None:
        from .smartplaylist import write_spl_prefs, write_spl_rules

        body50 = write_spl_prefs(prefs, padded_size=96)  # libgpod writes 96-byte prefs
        body51 = write_spl_rules(match, rules)
        for rec in self._playlist_records(playlist.playlist_id):
            rec.children = [
                c
                for c in rec.children
                if not (c.magic == MHOD_MAGIC and c.fields.get("mhod_type") in (50, 51))
            ]
            m50 = self._make_raw_mhod(50, body50)
            m50.fields["raw_body"] = body50
            m51 = self._make_raw_mhod(51, body51)
            m51.fields["raw_body"] = body51
            rec.children[1:1] = [m50, m51]  # after the title, as libgpod writes them
        self._modified = True

    def create_smart_playlist(
        self, name: str, rules: list, match=None, prefs=None, refresh: bool = True
    ) -> Playlist:
        """Create a smart playlist (rules in MHOD 51, preferences in MHOD 50).

        Args:
            name: Playlist name.
            rules: List of smartplaylist.SPLRule.
            match: SPLMatch.AND (default) or OR.
            prefs: SPLPrefs (default: live update, rules checked, no limit).
            refresh: Fill the members now (refresh_smart_playlist).
        """
        from .smartplaylist import SPLMatch, SPLPrefs

        pl = self.create_playlist(name)
        self._set_spl_mhods(
            pl, SPLMatch.AND if match is None else match, rules, prefs or SPLPrefs()
        )
        if refresh:
            self.refresh_smart_playlist(pl)
        return pl

    def update_smart_playlist(
        self, playlist: Playlist, rules=None, match=None, prefs=None, refresh: bool = True
    ) -> None:
        """Change rules, match operator and/or preferences of a (smart) playlist.

        Raises:
            ValueError: the playlist does not belong to this database.
        """
        from .smartplaylist import SPLMatch, SPLPrefs

        self._check_own_playlist(playlist)

        current = playlist.smart_rules or (SPLMatch.AND, [])
        self._set_spl_mhods(
            playlist,
            current[0] if match is None else match,
            current[1] if rules is None else rules,
            prefs or playlist.smart_prefs or SPLPrefs(),
        )
        if refresh:
            self.refresh_smart_playlist(playlist)

    def refresh_smart_playlist(self, playlist: Playlist) -> List[Track]:
        """Re-evaluate a smart playlist and replace its members (like libgpod's itdb_spl_update).

        Rules are applied when ``checkrules`` is set, unchecked tracks are skipped
        with ``matchcheckedonly``, then the limit (sort + count/size/time) when
        ``checklimits`` is set. Returns the new members.

        Raises:
            ValueError: the playlist does not belong to this database.
        """
        from .smartplaylist import apply_limit, evaluate_smart_playlist

        self._check_own_playlist(playlist)

        spl = playlist.smart_rules
        prefs = playlist.smart_prefs
        if spl is None or prefs is None:
            raise PlaylistError(f"{playlist.name!r} is not a smart playlist")
        match, rules = spl
        tracks = list(self._tracks)
        if prefs.matchcheckedonly:
            tracks = [t for t in tracks if t.checked]
        if prefs.checkrules and rules:
            members = {pl.playlist_id: set(pl.track_ids) for pl in self._playlists}
            tracks = evaluate_smart_playlist(rules, match, tracks, members)
        tracks = apply_limit(tracks, prefs)
        current = [self._track_lookup[i] for i in playlist.track_ids if i in self._track_lookup]
        if [t.track_id for t in current] != [t.track_id for t in tracks]:
            for t in current:
                self.remove_track_from_playlist(playlist, t)
            for t in tracks:
                self.add_track_to_playlist(playlist, t)
        return tracks

    def refresh_smart_playlists(self, live_only: bool = True) -> int:
        """Refresh every smart playlist (only live-updating ones by default); returns how many.

        save() and save_to_file() call this with live_only=True unless told not to.

        A smart playlist with a rule "Playlist is / is not <other smart playlist>" is refreshed
        after that playlist, so it sees its new members. Playlists that depend on each other in
        a cycle are refreshed in list order again until their members no longer change (at
        most once per playlist in the cycle; a warning is logged if they still change).
        """
        selected = [
            pl
            for pl in self._playlists
            if pl.smart_prefs is not None
            and pl.smart_rules is not None
            and (pl.smart_prefs.liveupdate or not live_only)
        ]
        order, cyclic = self._smart_refresh_order(selected)
        for pl in order:
            self.refresh_smart_playlist(pl)
        for _ in range(len(cyclic)):
            before = [list(pl.track_ids) for pl in cyclic]
            for pl in cyclic:
                self.refresh_smart_playlist(pl)
            if [list(pl.track_ids) for pl in cyclic] == before:
                break
        else:
            if cyclic:
                logger.warning(
                    "Smart playlists %s depend on each other and did not settle; their "
                    "members are those of the last refresh",
                    ", ".join(repr(pl.name) for pl in cyclic),
                )
        return len(selected)

    @staticmethod
    def _smart_refresh_order(selected: List[Playlist]) -> Tuple[List[Playlist], List[Playlist]]:
        """Refresh order (dependencies first, else list order) and the playlists that are
        part of a dependency cycle (in list order)."""
        from .smartplaylist import SPLFieldType, get_field_type

        by_id = {pl.playlist_id: pl for pl in selected}

        def deps(pl: Playlist) -> List[Playlist]:
            _, rules = pl.smart_rules  # type: ignore[misc]
            out = []
            for rule in rules:
                if get_field_type(rule.field) == SPLFieldType.PLAYLIST:
                    dep = by_id.get(rule.fromvalue & 0xFFFFFFFFFFFFFFFF)
                    if dep is not None and dep is not pl and dep not in out:
                        out.append(dep)
            return out

        order: List[Playlist] = []
        state: Dict[int, int] = {}  # id(playlist) -> 1 visiting, 2 done
        cyclic_ids: Set[int] = set()
        stack: List[Playlist] = []

        def visit(pl: Playlist) -> None:
            state[id(pl)] = 1
            stack.append(pl)
            for dep in deps(pl):
                if state.get(id(dep)) == 1:  # back edge: everything on the stack from dep
                    cyclic_ids.update(id(p) for p in stack[stack.index(dep) :])
                elif id(dep) not in state:
                    visit(dep)
            stack.pop()
            state[id(pl)] = 2
            order.append(pl)

        for pl in selected:
            if id(pl) not in state:
                visit(pl)
        return order, [pl for pl in selected if id(pl) in cyclic_ids]

    def create_playlist(self, name: str, podcast: bool = False) -> Playlist:
        """Create a new playlist.

        Args:
            name: Playlist name.
            podcast: If True, mark as a podcast playlist.

        Returns:
            The new Playlist object.
        """
        mhlp = self._get_or_create_mhlp(mhsd_type=2)
        playlist_id = self._next_playlist_id()

        mhyp = self._create_mhyp(name, playlist_id, is_master=False, podcast=podcast)
        mhlp.children.append(mhyp)

        # Also add to MHSD type 3 (podcast playlists mirror)
        mhlp3 = self._get_or_create_mhlp(mhsd_type=3)
        mhyp3 = self._create_mhyp(name, playlist_id, is_master=False, podcast=podcast)
        mhlp3.children.append(mhyp3)

        pl = Playlist.from_record(mhyp, self._track_lookup)
        pl._on_change = self._mark_modified
        self._playlists.append(pl)
        self._modified = True
        return pl

    def delete_playlist(self, playlist: Playlist) -> None:
        """Delete a playlist.

        Raises:
            ValueError: the playlist does not belong to this database (e.g. it was read from
                another one, or already deleted).
        """
        self._check_own_playlist(playlist)
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

    def analyze_soundcheck(
        self,
        tracks: Optional[Iterable[Track]] = None,
        only_missing: bool = True,
        progress: Optional[Callable[[int, int, Track], None]] = None,
    ) -> int:
        """Measure Sound Check for tracks already on the iPod (ffmpeg ``replaygain``).

        Args:
            tracks: Tracks to analyse (default: all audio tracks of the database).
            only_missing: Skip tracks that already have a Sound Check value.
            progress: Called as ``progress(done, total, track)`` after each track.

        Returns:
            Number of tracks that got a new value (call save() to write them).

        Raises:
            UnsupportedFormatError: ffmpeg is not installed.
        """
        from ..convert import find_ffmpeg
        from ..exceptions import UnsafePathError
        from ..tags import analyze_soundcheck
        from ..utils.encoding import ipod_path_to_os

        if not self._mountpoint:
            raise TrackError("Cannot analyse tracks without a mount point")
        find_ffmpeg()
        if tracks is None:
            tracks = [t for t in self._tracks if not t.is_video]
        todo = [t for t in tracks if not (only_missing and t.soundcheck)]
        changed = 0
        for done, track in enumerate(todo, 1):
            try:
                path = ipod_path_to_os(track.ipod_path, self._mountpoint)
            except UnsafePathError:
                logger.warning("Skipping %s: unsafe path %r", track, track.ipod_path)
                path = None
            value = analyze_soundcheck(path) if path and os.path.isfile(path) else None
            if value is not None and value != track.soundcheck:
                track.soundcheck = value
                changed += 1
            if progress is not None:
                progress(done, len(todo), track)
        return changed

    def add_track_to_playlist(self, playlist: Playlist, track: Track) -> None:
        """Add a track to a playlist.

        Raises:
            ValueError: the playlist or the track belongs to another database (or was removed).
        """
        self._check_own_playlist(playlist)
        self._check_own_tracks([track])
        if not playlist.record:
            return
        pos = self._mhip_count(playlist.record)
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
        """Remove a track from a playlist.

        Raises:
            ValueError: the playlist or the track belongs to another database (or was removed).
        """
        self._check_own_playlist(playlist)
        self._check_own_tracks([track])
        if not playlist.record:
            return
        playlist.record.children = [
            c
            for c in playlist.record.children
            if not (c.magic == MHIP_MAGIC and c.fields.get("track_id") == track.track_id)
        ]
        self._renumber_positions(playlist.record)
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
        # 0x30: hashing scheme. libgpod writes 0 for models without a signature (5G/5.5G,
        # nano 1G/2G, shuffles ...); signed and unidentified models keep pygpod's 1.
        dev = self._device
        unsigned = dev is not None and dev.checksum_known and not dev.requires_hash
        if len(header) > 0x34:
            if unsigned:
                if header[0x30:0x32] != b"\x00\x00":
                    put16lint(header, 0x30, 0)
                    changed = True
            elif header[0x30] == 0:
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

    def _master_mhyps(self) -> List[Record]:
        """The master playlist records of MHSD 2 and 3."""
        found = []
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") in (2, 3) and mhsd.children:
                for mhyp in mhsd.children[0].children:
                    if mhyp.fields.get("playlist_type") == 1:
                        found.append(mhyp)
        return found

    def _sort_entries(self, mhyp: Record) -> List[SortEntry]:
        """What the sort indexes of a master playlist are built from, in member order."""
        entries = []
        for child in mhyp.children:
            if child.magic != MHIP_MAGIC or child.fields.get("track_id") is None:
                continue
            t = self._track_lookup.get(child.fields["track_id"])
            if t is None:
                entries.append(SortEntry("", "", "", "", "", "", "", "", "", 0, 0))
                continue
            entries.append(
                SortEntry(
                    t.title or "",
                    t.artist or "",
                    t.album or "",
                    t.genre or "",
                    t.composer or "",
                    t.sort_title or "",
                    t.sort_artist or "",
                    t.sort_album or "",
                    t.sort_composer or "",
                    t.track_number or 0,
                    t.cd_number or 0,
                )
            )
        return entries

    @staticmethod
    def _has_sort_mhods(mhyp: Record) -> bool:
        return any(
            c.magic == MHOD_MAGIC and c.fields.get("mhod_type") == MHOD_ID_LIBPLAYLISTINDEX
            for c in mhyp.children
        )

    def _index_inputs(self) -> Optional[tuple]:
        """Everything the sort indexes depend on, or None when a master playlist with
        members has none (they must be built)."""
        inputs = []
        for mhyp in self._master_mhyps():
            entries = self._sort_entries(mhyp)
            if entries and not self._has_sort_mhods(mhyp):
                return None
            inputs.append(tuple(entries))
        return tuple(inputs)

    def _list_inputs(self) -> tuple:
        """Everything the album and artist lists (and the tracks' links to them) depend on.

        Holds the MHIT records themselves (compared with ``is``): the id() of a removed
        track's record can be reused by a new track's record."""
        return tuple((self._album_key(t), t.artist or "", t.record) for t in self._tracks)

    def _remember_derived_inputs(self) -> None:
        """After load or save: the sort indexes and album/artist lists in the records match
        the tracks. A save that changes none of their inputs keeps them byte for byte (also
        when another writer built them differently, e.g. libgpod's locale order)."""
        if not self._root:
            return
        self._saved_index_inputs = self._index_inputs()
        self._saved_list_inputs = self._list_inputs()

    def _rebuild_sort_indexes(self) -> None:
        """Rebuild sort index MHODs (type 52+53) on master playlists.

        Generates sorted track index and letter jump table for each sort key
        (title, artist, album, genre, composer; see db/sortindex.py). These are
        required by iPod firmware for browsing by category. Kept as they are when
        nothing they depend on changed since load or the last save.
        """
        current = self._index_inputs()
        if current is not None and current == getattr(self, "_saved_index_inputs", None):
            return
        for mhyp in self._master_mhyps():
            mhyp.children = [
                c
                for c in mhyp.children
                if not (
                    c.magic == MHOD_MAGIC
                    and c.fields.get("mhod_type")
                    in (MHOD_ID_LIBPLAYLISTINDEX, MHOD_ID_LIBPLAYLISTJUMPTABLE)
                )
            ]
            entries = self._sort_entries(mhyp)
            if not entries:
                continue

            # Insert sort MHODs BEFORE the first MHIP
            insert_idx = next(
                (i for i, c in enumerate(mhyp.children) if c.magic == MHIP_MAGIC),
                len(mhyp.children),
            )
            for sort_type, positions, jumps in build_indexes(entries):
                for mhod in (
                    self._make_raw_mhod(
                        MHOD_ID_LIBPLAYLISTINDEX, mhod52_body(sort_type, positions)
                    ),
                    self._make_raw_mhod(
                        MHOD_ID_LIBPLAYLISTJUMPTABLE, mhod53_body(sort_type, jumps)
                    ),
                ):
                    mhyp.children.insert(insert_idx, mhod)
                    insert_idx += 1

    # ---- Album / Artist List Generation ----

    @staticmethod
    def _album_key(track: Track) -> tuple:
        """Album identity: (album, album artist) when the album artist is set; a compilation is one
        album regardless of track artists; otherwise (album, track artist)."""
        album = track.album or ""
        if track.albumartist:
            return (album, track.albumartist, False)
        if track.compilation:
            return (album, "", True)
        return (album, track.artist or "", False)

    def _derived_list_kept(self, mhsd_type: int) -> bool:
        """The loaded album (4) / artist (8) list stays as it is: it exists and nothing it
        depends on changed since load or the last save."""
        saved = getattr(self, "_saved_list_inputs", None)
        current = self._list_inputs()
        if saved is None or len(saved) != len(current):
            return False
        if any(a[:2] != b[:2] or a[2] is not b[2] for a, b in zip(current, saved)):
            return False
        return any(
            m.fields.get("mhsd_type") == mhsd_type and m.children for m in self._root.children
        )

    @staticmethod
    def _loaded_sql_ids(mhsd: Record, magic: bytes, name_mhod: int, artist_mhod) -> dict:
        """(name, artist) -> the SQLite id of the album/artist items in a loaded list."""
        ids = {}
        for lst in mhsd.children:
            for item in lst.children:
                if item.magic != magic or len(item.raw_header) < 0x1C:
                    continue
                sql_id = get64lint(item.raw_header, 0x14)
                if sql_id:
                    name = item.get_mhod(name_mhod) or ""
                    artist = (item.get_mhod(artist_mhod) or "") if artist_mhod else ""
                    ids.setdefault((name, artist), sql_id)
        return ids

    def _rebuild_album_list(self) -> None:
        """Rebuild MHSD type 4 album list from track metadata.

        Creates MHIA records with MHOD types 200 (album name) and
        201 (album artist) for each album (see _album_key).
        Also updates MHIT offset 0x120 with album_id links.
        """
        if self._derived_list_kept(4):
            return
        enc = self._config.string_encoding

        albums: Dict[tuple, int] = {}  # album key -> 1-based album_id
        track_album: Dict[int, int] = {}  # id(mhit record) -> album_id
        for track in self._tracks:
            key = self._album_key(track)
            if key not in albums:
                albums[key] = len(albums) + 1
            if track.record is not None:
                track_album[id(track.record)] = albums[key]

        mhsd4 = self._get_or_create_mhsd(4)
        sql_ids = self._loaded_sql_ids(mhsd4, MHIA_MAGIC, MHOD_ID_ALBUM_ALBUM, MHOD_ID_ALBUM_ARTIST)

        # Create MHLA with MHIA children
        mhla = Record(MHLA_MAGIC, DEFAULT_MHLA_HEADER_LEN, 0)
        mhla.raw_header = self._make_basic_header(MHLA_MAGIC, DEFAULT_MHLA_HEADER_LEN)
        mhla.fields["num_albums"] = len(albums)

        # libgpod uses 88-byte MHIA headers for album list records
        ALBUM_MHIA_HEADER_LEN = 88

        for (album, artist, _compilation), album_id in albums.items():
            mhia = Record(MHIA_MAGIC, ALBUM_MHIA_HEADER_LEN, 0)
            header = bytearray(ALBUM_MHIA_HEADER_LEN)
            header[0:4] = MHIA_MAGIC
            put32lint(header, 4, ALBUM_MHIA_HEADER_LEN)
            put32lint(header, 0x10, album_id)
            # 64-bit id of the SQLite databases (libgpod: random per album); kept when loaded
            put64lint(header, 0x14, sql_ids.get((album, artist), 0))
            put32lint(header, 0x1C, 2)  # unknown, 2 in libgpod and iTunes
            mhia.raw_header = bytes(header)
            mhia.fields["album_id"] = album_id

            # MHOD type 200: album name
            if album:
                mhod200 = make_string_mhod(MHOD_ID_ALBUM_ALBUM, album, encoding=enc)
                mhia.children.append(mhod200)
            # MHOD type 201: album artist (none for a compilation without album artist)
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
            album_id = track_album.get(id(mhit), 0)
            if album_id and len(mhit.raw_header) > 0x124:
                header = bytearray(mhit.raw_header)
                put32lint(header, 0x120, album_id)
                mhit.raw_header = bytes(header)

    def _rebuild_artist_list(self) -> None:
        """Rebuild MHSD type 8 artist list from track metadata.

        Creates MHII records with MHOD type 300 (artist name) for each
        unique artist. Also updates MHIT offset 0x160 with artist_id links.
        """
        if self._derived_list_kept(8):
            return
        enc = self._config.string_encoding

        # Collect unique artists: artist -> artist_id
        artists = {}  # artist_name -> int
        for track in self._tracks:
            artist = track.artist or ""
            if artist and artist not in artists:
                artists[artist] = len(artists) + 1  # 1-based IDs

        mhsd8 = self._get_or_create_mhsd(8)
        sql_ids = self._loaded_sql_ids(mhsd8, MHII_MAGIC, MHOD_ID_ALBUM_ARTIST_MHII, None)

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
            put64lint(header, 0x14, sql_ids.get((artist_name, ""), 0))
            put32lint(header, 0x1C, 2)  # unknown, 2 in libgpod and iTunes
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
        self._remember_derived_inputs()

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

    @property
    def damage(self) -> List[str]:
        """Damaged places of the iTunesDB skipped by ``Database(..., partial=True)`` (empty
        for an intact database)."""
        return list(self._damage)

    def save(self, refresh_smart_playlists: bool = True, force: bool = False) -> None:
        """Save the database back to the iPod.

        Live-updating smart playlists are refreshed first (like libgpod's itdb_write,
        which calls itdb_spl_update_live); pass ``refresh_smart_playlists=False`` to keep
        their stored members. Non-live smart playlists and normal playlists are never touched.

        Concurrency: the save holds ``iPod_Control/iTunes/pygpod.lock`` (see db/writelock.py) and
        raises ConflictError when the iTunesDB on the iPod is no longer the one this object loaded
        or last saved (another writer saved meanwhile) - reopen and redo the changes, or pass
        ``force=True`` to overwrite. A database opened with ``partial=True`` that has
        ``damage`` raises DatabaseError unless ``force=True``.

        Crash-safe: checksum inputs are validated before anything is written,
        then iTunesDB, ArtworkDB and compacted .ithmb files are committed
        together (see db/transaction.py). On any failure nothing is replaced
        and the exception propagates.
        """
        logger.info("Saving database")
        if self._damage and not force:
            raise DatabaseError(
                "the iTunesDB was damaged and only partly loaded ("
                + "; ".join(self._damage)
                + "): saving would drop everything after the damage; restore a backup, or "
                "save(force=True) to keep only the readable part"
            )
        with self._writing(check=not force):
            self._save_locked(refresh_smart_playlists)

    @contextmanager
    def _writing(self, check: bool = True) -> Iterator[None]:
        """Hold the writer lock for a save (and, in repair(), the file deletions before it);
        with ``check``, ConflictError first when another writer saved since this was loaded."""
        if not self._mountpoint:
            raise DatabaseError("No mount point - use save_to_file() instead")
        if self._device is not None and self._device.sqlite_db:
            if not getattr(self, "_sqlite_warned", False):
                self._sqlite_warned = True
                logger.warning(
                    "%s reads its library from the SQLite databases (iTunesCDB, iTunes "
                    "Library.itlp), which pygpod does not write: new tracks may not show up",
                    self._device.model,
                )
        if not self._recover and interrupted_commit(self._mountpoint):
            raise DatabaseError(
                "an interrupted save is not recovered yet: reopen the database with "
                "recover=True (the default) before saving"
            )
        with writer_lock(self._itunes_dir()):
            if interrupted_commit(self._mountpoint):
                # a save that crashed after this database was read (or while it was read
                # without recovering): complete it first; the check below then sees its result
                recover_interrupted_commit(self._mountpoint)
            self._finish_own_interrupted_save()
            if check:
                self._check_not_modified()
            yield

    def _finish_own_interrupted_save(self) -> None:
        """A save of this object that failed after its commit point was completed by
        recover() (here or by another process): the iTunesDB on disk is this object's own,
        so finish that save's bookkeeping instead of reporting it as another writer's."""
        pending, self._interrupted_save = getattr(self, "_interrupted_save", None), None
        if pending is None:
            return
        tx, sha = pending
        path = (
            str(self._device.itunesdb_path())
            if self._device
            else os.path.join(self._itunes_dir(), "iTunesDB")
        )
        if not interrupted_commit(self._mountpoint) and file_sha256(path) == sha:
            # edits made since are not on disk: only the retry's own commit clears the flag
            modified = self._modified
            tx.run_commit_callbacks()
            self._modified = modified

    def _check_not_modified(self) -> None:
        """ConflictError when the iTunesDB on disk differs from the one loaded / last saved."""
        path = (
            str(self._device.itunesdb_path())
            if self._device
            else os.path.join(self._itunes_dir(), "iTunesDB")
        )
        on_disk = file_sha256(path)
        if self._raw_data is None:
            if on_disk is not None:
                raise ConflictError(
                    f"{path} was created by another writer since the database was opened"
                )
            return
        if on_disk is None:
            raise ConflictError(f"{path} was deleted since it was loaded")
        if on_disk != sha256_hex(self._raw_data):
            raise ConflictError(f"{path} was changed by another writer since it was loaded")

    def _save_locked(self, refresh_smart_playlists: bool) -> None:
        otg_rewrites = self._otg_before_save()
        if refresh_smart_playlists:
            self.refresh_smart_playlists(live_only=True)

        data = self._prepare_for_save()

        # Update checksums if needed
        hash_type = None
        if self._device and self._device.requires_hash:
            hash_type = self._device.checksum_type.name
        elif self._device and not self._device.checksum_known:
            # Model not identified (SysInfo missing or incomplete): sign like the database
            # on the iPod, so a signed iPod (e.g. a Classic) does not get a DB it rejects
            from ..hash.checksum import detect_checksum_type

            hash_type = detect_checksum_type(self._raw_data or b"")
            if hash_type:
                logger.warning(
                    "iPod model not identified (SysInfo without ModelNumStr); the iTunesDB on "
                    "it is signed with %s, signing the new one the same way",
                    hash_type,
                )
        if self._device and hash_type:
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
            params = self._hash72_params(guid) if hash_type == "HASH72" else None
            data = update_checksums(data, guid, hash_type, hash72_params=params)
        elif self._device and self._device.checksum_known and len(data) >= 0x72 + 46:
            # Unsigned model: no signature, like libgpod (pygpod <= 0.1.0 left a hash58 on
            # 5G/nano 1G-2G databases, which would go stale here). Only for identified
            # models: an unidentified one may be a signed iPod with an incomplete SysInfo.
            buf = bytearray(data)
            buf[0x58:0x6C] = bytes(20)  # hash58
            buf[0x72 : 0x72 + 46] = bytes(46)  # hash72
            data = bytes(buf)

        db_path = (
            self._device.itunesdb_path()
            if self._device
            else pathlib.Path(self._mountpoint) / "iPod_Control" / "iTunes" / "iTunesDB"
        )

        itunessd = self._shuffle_db()  # raises for shuffles whose format is not supported
        consume = self._pc_consume and self._pc_source is not None
        tx = Transaction(self._mountpoint)
        try:
            if getattr(self, "_artwork_manager", None) is not None:
                self._artwork_manager.stage(tx)
            tx.stage_with(str(db_path), lambda path: self._write_db_file(path, data))
            if itunessd is not None:
                assert self._device is not None
                tx.stage_bytes(str(self._device.itunessd_path()), itunessd)
            if consume:
                assert self._pc_source is not None
                write_commit_marker(self._itunes_dir(), self._pc_source, sha256_hex(data))
            for f in self._otg_consume:
                path = os.path.join(self._itunes_dir(), f.name)
                if file_sha256(path) == f.sha256:  # rewritten by the iPod since: keep it
                    tx.delete_on_commit(path)
            for path, new_data, _ in otg_rewrites:
                tx.stage_bytes(path, new_data)
            deleted = self._stage_track_file_deletions(tx)

            def saved() -> None:
                self._raw_data = data
                self._modified = False
                self._otg_consume = []
                self._pending_deletes = [d for d in self._pending_deletes if d not in deleted]
                if otg_rewrites or self._otg_files:
                    self._otg_after_save(otg_rewrites)
                logger.debug("Database saved to %s", db_path)
                if consume:
                    self._finish_play_count_cleanup()

            tx.on_commit(saved)
            tx.commit()
        except BaseException:
            if tx.journal_written:
                # past the commit point: recover() completes it; the next save() of this
                # object then recognises the result as its own (no false conflict)
                self._interrupted_save = (tx, sha256_hex(data))
            tx.abort()
            raise

    def _stage_track_file_deletions(self, tx: Transaction) -> List[Tuple[str, str]]:
        """Files of removed tracks (remove_track(delete_file=True)) that no remaining track
        uses: deleted in the commit."""
        if not self._pending_deletes:
            return []
        used = {t.ipod_path.lower() for t in self._tracks if t.ipod_path}
        staged = []
        for ipod_path, path in self._pending_deletes:
            if ipod_path.lower() not in used and os.path.lexists(path):
                tx.delete_on_commit(path)
            staged.append((ipod_path, path))
        return staged

    @staticmethod
    def _write_db_file(path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    def _shuffle_db(self) -> Optional[bytes]:
        """The iTunesSD for an iPod shuffle (written with the iTunesDB, like libgpod):
        version 1 for the shuffle 1G/2G, version 2 for the 3G/4G; None for other iPods.

        Raises:
            DeviceError: a shuffle of unknown generation - nothing is written.
        """
        from ..db.itunessd import ITunesSD, ITunesSDV2
        from ..device.models import IpodGeneration

        if not self._device or not self._device.is_shuffle:
            return None
        gen = self._device.generation
        if gen in (IpodGeneration.SHUFFLE_1, IpodGeneration.SHUFFLE_2):
            return ITunesSD.from_tracks(self._tracks).write()
        if gen in (IpodGeneration.SHUFFLE_3, IpodGeneration.SHUFFLE_4):
            return ITunesSDV2.from_database(self._tracks, self._playlists).write()
        raise DeviceError(
            f"{gen.name}: unknown iPod shuffle generation; the iTunesSD format is not known"
        )

    def _hash72_params(self, guid: str, persist: bool = True) -> tuple:
        """The device's hash72 IV and random part: from iPod_Control/Device/HashInfo, else
        extracted once from the database as read (signed by iTunes/libgpod) and, with
        ``persist`` (saving), stored in HashInfo, like libgpod does. Read-only callers
        (check()) pass ``persist=False``.

        Raises:
            HashError: neither is available (a random IV would make the iPod reject the database).
        """
        from ..hash.hash72 import extract_hash72_params, read_hash_info, write_hash_info

        assert self._device is not None
        path = str(self._device.device_dir / "HashInfo")
        params = read_hash_info(path, guid)
        if params is not None:
            return params
        params = extract_hash72_params(self._raw_data or b"")
        if params is None:
            raise HashError(
                "This iPod needs hash72, but there is no iPod_Control/Device/HashInfo and "
                "the database on it was not signed by iTunes. Sync it with iTunes once "
                "(or copy a HashInfo file)."
            )
        if not persist:
            return params
        try:
            write_hash_info(path, guid, *params)
        except OSError:
            logger.warning("could not write %s", path, exc_info=True)
        return params

    def _try_resolve_guid(self) -> Optional[str]:
        """Try to get FirewireGuid from USB and write SysInfo for future use.

        Only the iPod backing this mountpoint is used: with several iPods connected and no
        way to tell which one is mounted here, nothing is guessed (save() then raises).
        """
        if not self._device or not self._device.mountpoint:
            return None
        try:
            from ..device.usb import detect_ipod_usb_for_mountpoint

            usb_dev = detect_ipod_usb_for_mountpoint(self._device.mountpoint)
            if usb_dev is not None and usb_dev.firewire_guid:
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

    def save_to_file(self, path: str, refresh_smart_playlists: bool = True) -> None:
        """Write the iTunesDB to ``path`` (an export: nothing on the iPod is changed).

        Only the iTunesDB is written; the artwork (ArtworkDB, .ithmb files) is not exported
        and stays as on the iPod until save(). The file is not signed with the iPod's
        checksum. Refreshes live-updating smart playlists first, like save().
        """
        if refresh_smart_playlists:
            self.refresh_smart_playlists(live_only=True)
        data = self._prepare_for_save()
        with open(path, "wb") as f:
            f.write(data)

    # ---- Context Manager ----

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Save on a normal exit if anything changed; never save after an exception."""
        if self._modified and exc_type is None:
            self.save()

    @property
    def modified(self) -> bool:
        """True if there are unsaved changes (tracks, playlists, play counts)."""
        return self._modified

    def _mark_modified(self) -> None:
        self._modified = True

    # ---- Internal Helpers ----

    def _id_index(self) -> List[Any]:
        """[set of track ids, set of dbids, highest track id] - built once, kept up to date by
        add_track/remove_tracks, dropped when the model is rebuilt."""
        if self._ids is None:
            tids = {t.track_id for t in self._tracks}
            self._ids = [
                tids,
                {t.dbid for t in self._tracks},
                max(tids, default=self._config.track_id_start - 1),
            ]
        return self._ids

    def _next_track_id(self) -> int:
        """Generate the next unique track ID (above every ID used in this session)."""
        tids, _, highest = self._id_index()
        tid = max(highest + 1, self._config.track_id_start)
        while tid in tids:
            tid += 1
        return tid

    def _next_playlist_id(self) -> int:
        """A 64-bit playlist id no playlist of any list (MHSD 2, 3, ...) has: with a fixed
        DatabaseConfig.random_seed a reopened database draws the same sequence again."""
        used = {
            mhyp.fields.get("playlist_id")
            for mhsd in self._root.children
            if mhsd.magic == MHSD_MAGIC and mhsd.children
            for mhyp in mhsd.children[0].children
            if mhyp.magic == MHYP_MAGIC
        }
        while True:
            playlist_id = self._rng.getrandbits(64)
            if playlist_id and playlist_id not in used:
                return playlist_id

    def _next_dbid(self) -> int:
        """Generate a unique 64-bit DBID."""
        dbids = self._id_index()[1]
        while True:
            dbid = self._rng.getrandbits(64)
            if dbid not in dbids and dbid != 0:
                return dbid

    @staticmethod
    def _mhip_count(rec: Record) -> int:
        """Number of MHIP children of a playlist record: MHODs come first, then MHIPs."""
        children = rec.children
        k = 0
        while k < len(children) and children[k].magic != MHIP_MAGIC:
            k += 1
        return len(children) - k

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
            now = now_mac(self._tz_shift)

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

        put32lint(header, 0x4C, tags.get("soundcheck", 0))
        apply_gapless_fields(header, tags)
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

        # movie_flag (0xB1): set for video files, like iTunes and gtkpod
        is_video = bool(media_type & _video_types)
        if header_len > 0xB1:
            put8int(header, 0xB1, 1 if is_video else 0)

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
        rec.fields["compilation"] = 1 if tags.get("compilation") else 0
        # mirror the header flags set above, so Track properties are right before a reload
        if header_len > 0xA8 and is_podcast_or_audiobook:
            rec.fields["skip_when_shuffling"] = 1
            rec.fields["remember_position"] = 1
        if header_len > 0xB2:
            rec.fields["mark_unplayed"] = 0x02 if is_podcast_or_audiobook else cfg.mark_unplayed
        rec.fields["bitrate"] = tags.get("bitrate", 0)
        rec.fields["samplerate"] = (sr << 16) & 0xFFFFFFFF
        rec.fields["time_added"] = now
        rec.fields["time_modified"] = now
        rec.fields["media_type"] = media_type
        if header_len > 0xB1:
            rec.fields["movie_flag"] = 1 if is_video else 0
        rec.fields["play_count"] = 0
        rec.fields["rating"] = 0
        rec.fields["season_number"] = tags.get("season_number", 0)
        rec.fields["episode_number"] = tags.get("episode_number", 0)
        rec.fields["soundcheck"] = tags.get("soundcheck", 0)
        for key in GAPLESS_FIELDS:
            rec.fields[key] = int(tags.get(key, 0))
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
            ("sort_artist", MHOD_ID_SORT_ARTIST),
            ("sort_title", MHOD_ID_SORT_TITLE),
            ("sort_album", MHOD_ID_SORT_ALBUM),
            ("sort_albumartist", MHOD_ID_SORT_ALBUMARTIST),
            ("sort_composer", MHOD_ID_SORT_COMPOSER),
            ("sort_tvshow", MHOD_ID_SORT_TVSHOW),
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
        put32lint(header, 0x18, now_mac(self._tz_shift))
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
        rec.fields["timestamp"] = now_mac(self._tz_shift)

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
                        pos = self._mhip_count(mhyp)
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
                        pos = self._mhip_count(mhyp)
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
                        self._renumber_positions(mhyp)
                        return

    def _remove_from_all_playlists(self, track_ids: Set[int]) -> None:
        """Remove tracks from all playlists (one pass per playlist)."""
        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type")
            if mhsd_type in (2, 3) and mhsd.children:
                mhlp = mhsd.children[0]
                for mhyp in mhlp.children:
                    kept = [
                        c
                        for c in mhyp.children
                        if not (c.magic == MHIP_MAGIC and c.fields.get("track_id") in track_ids)
                    ]
                    if len(kept) != len(mhyp.children):
                        mhyp.children = kept
                        self._renumber_positions(mhyp)

    @staticmethod
    def _renumber_positions(mhyp: Record) -> None:
        """Position MHODs (type 100) of the playlist's items 0..n-1 in order, as libgpod
        writes them; after a removal the old numbers would leave gaps and, with the next
        addition, duplicates. Items that already have their number are left as they are."""
        position = 0
        for mhip in mhyp.children:
            if mhip.magic != MHIP_MAGIC:
                continue
            for mhod in mhip.children:
                if mhod.magic != MHOD_MAGIC or mhod.fields.get("mhod_type") != MHOD_ID_PLAYLIST:
                    continue
                raw = bytearray(mhod.raw_bytes)
                at = mhod.header_len
                if len(raw) >= at + 4 and struct.unpack_from("<I", raw, at)[0] != position:
                    struct.pack_into("<I", raw, at, position)
                    mhod.raw_bytes = bytes(raw)
                    if "raw_body" in mhod.fields:
                        mhod.fields["raw_body"] = bytes(raw[at:])
                break
            position += 1

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
