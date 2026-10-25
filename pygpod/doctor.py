"""Consistency check and repair of an iPod database (``Database.check()`` / ``repair()``,
CLI ``pygpod doctor``).

check() is read-only. It finds media files no track references, tracks whose file is missing,
empty, of the wrong size or shared with another track, track lengths far from the gapless
sample count, playlist items pointing to unknown tracks, tracks missing from the master
playlist, broken artwork references, photo thumbnails in a size the iPod does not show
(written for another format table, e.g. by older pygpod versions: re-add those photos), more
than one master photo album, photo album members without a photo, a checksum that does not
match the database, too little free space for the next save, and pygpod's leftover temporary
files and stale write lock.

repair() fixes what can be fixed in the database and saves it once; orphan media files are only
deleted with ``delete_orphans=True``. Nothing outside ``iPod_Control`` is ever touched. Open the
database with ``Database(mountpoint, recover=False)`` for a check that writes nothing (a normal
open completes an interrupted save and removes its staging files). If a save was interrupted,
repair() only completes it: reopen the database and repair again.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Dict, Iterator, List, NamedTuple, Optional, Set

if TYPE_CHECKING:  # pragma: no cover
    from .model.database import Database

logger = logging.getLogger(__name__)

ERROR, WARNING, INFO = "error", "warning", "info"

# kind -> (severity, fixable)
KINDS = {
    "interrupted_commit": (ERROR, True),  # completed (rolled forward), then reopen
    "orphan_file": (WARNING, True),  # deleted only with delete_orphans
    "missing_file": (ERROR, True),  # track removed
    "empty_file": (ERROR, True),  # track removed
    "size_mismatch": (WARNING, True),  # file_size updated
    "length_mismatch": (WARNING, True),  # length and bitrate read from the file again
    "unsafe_path": (ERROR, True),  # track removed (path outside the mount point)
    "duplicate_path": (ERROR, True),  # the later tracks removed, the file kept
    "dangling_playlist_item": (ERROR, True),  # item removed
    "not_in_master": (ERROR, True),  # track added to the master playlist
    "artwork_db_unreadable": (ERROR, False),  # per-track artwork checks skipped
    "artwork_missing": (WARNING, True),  # track's artwork reference cleared
    "artwork_orphan": (WARNING, True),  # ArtworkDB entry removed
    "artwork_out_of_range": (WARNING, True),  # entry removed, track's reference cleared
    "artwork_thumbnail_size": (WARNING, False),  # older cover size: set the artwork again
    "photo_db_unreadable": (ERROR, False),  # photo checks skipped
    "photo_master_albums": (ERROR, True),  # merged into the first master album
    "photo_master_album_missing": (ERROR, True),  # a Photo Library album created first
    "photo_album_missing_photo": (ERROR, True),  # album members without a photo dropped
    "photo_not_in_master": (ERROR, True),  # the photos appended to the master album
    "photo_thumbnail_size": (WARNING, False),  # not shown by the iPod: re-add the photo
    "checksum_mismatch": (ERROR, True),  # database re-signed
    "checksum_unknown": (INFO, False),  # cannot be verified (missing GUID / HashInfo)
    "model_unidentified": (WARNING, False),  # signed DB, but SysInfo names no known model
    "low_space": (WARNING, False),
    "temp_file": (WARNING, True),  # deleted
    "stale_lock": (WARNING, True),  # deleted
    "write_in_progress": (INFO, False),  # a live writer holds the lock
}

_TEMP_SUFFIXES = (".pygpod-new", ".pygpod-restore")
_TEMP_NAMES = ("pygpod.journal.tmp",)


class Issue(NamedTuple):
    kind: str
    severity: str  # "error" | "warning" | "info"
    subject: str  # a path relative to the mount point, or "track <id>" / "playlist <name>"
    message: str
    fixable: bool
    track_id: Optional[int] = None
    path: Optional[str] = None  # absolute path of the file concerned
    image_id: Optional[int] = None  # ArtworkDB image id


class RepairAction(NamedTuple):
    kind: str  # the Issue kind
    subject: str
    action: str  # what was (or, with dry_run, would be) done
    done: bool  # False with dry_run, or when skipped (orphans without delete_orphans)


class CheckReport:
    """The issues check() found; falsy when there are none."""

    def __init__(self, issues: List[Issue]):
        self.issues = issues

    def __iter__(self) -> Iterator[Issue]:
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    def __bool__(self) -> bool:
        return bool(self.issues)

    @property
    def ok(self) -> bool:
        """True when no issue was found."""
        return not self.issues

    def kinds(self) -> Set[str]:
        """The kinds of the issues found."""
        return {i.kind for i in self.issues}

    def by_kind(self, kind: str) -> List[Issue]:
        """The issues of one kind."""
        return [i for i in self.issues if i.kind == kind]

    def summary(self) -> str:
        """One line: "no issues found" or "N issues found (M fixable)"."""
        if not self.issues:
            return "no issues found"
        n = len(self.issues)
        fixable = sum(i.fixable for i in self.issues)
        return f"{n} issue{'s' if n != 1 else ''} found ({fixable} fixable)"

    def __repr__(self) -> str:
        return f"<CheckReport {self.summary()}>"


def _issue(kind: str, subject: str, message: str, **kw) -> Issue:
    severity, fixable = KINDS[kind]
    return Issue(kind, severity, subject, message, fixable, **kw)


def _rel(mountpoint: str, path: str) -> str:
    return os.path.relpath(path, mountpoint)


def _ipod_control(mountpoint: str) -> str:
    """The iPod_Control directory (found case-insensitively), or where it would be."""
    import pathlib

    from .device.mountpoint import _find_ipod_control

    ctl = _find_ipod_control(pathlib.Path(mountpoint))
    return str(ctl) if ctl is not None else os.path.join(mountpoint, "iPod_Control")


def _find(parent: str, name: str) -> Optional[str]:
    import pathlib

    from .device.mountpoint import _find_ci

    found = _find_ci(pathlib.Path(parent), name)
    return str(found) if found is not None else None


def check(db: "Database") -> CheckReport:
    """Read-only consistency check of ``db`` and its iPod (see the module docstring)."""
    from .db.constants import MHIP_MAGIC
    from .exceptions import UnsafePathError
    from .utils.encoding import ipod_path_to_os

    mp = db.mountpoint
    issues: List[Issue] = []
    if not mp:
        return CheckReport(issues)
    ctl = _ipod_control(mp)
    from .db.transaction import interrupted_commit

    if interrupted_commit(mp):
        issues.append(
            _issue(
                "interrupted_commit",
                "iPod_Control/iTunes/pygpod.journal",
                "a save was interrupted; the database shown may be the one before it",
            )
        )

    # --- tracks and their files ---
    referenced: Dict[str, int] = {}
    for t in db.tracks:
        subject = f"track {t.track_id}"
        if not t.ipod_path:
            issues.append(
                _issue(
                    "missing_file", subject, f"{t.title!r} has no file path", track_id=t.track_id
                )
            )
            continue
        try:
            path = ipod_path_to_os(t.ipod_path, mp)
        except UnsafePathError as e:
            issues.append(_issue("unsafe_path", subject, str(e), track_id=t.track_id))
            continue
        key = os.path.normcase(os.path.realpath(path)).lower()
        if key in referenced:
            issues.append(
                _issue(
                    "duplicate_path",
                    subject,
                    f"{t.title!r} uses the file of track {referenced[key]}: {t.ipod_path}",
                    track_id=t.track_id,
                    path=path,
                )
            )
            continue
        referenced[key] = t.track_id
        if not os.path.isfile(path):
            issues.append(
                _issue(
                    "missing_file",
                    subject,
                    f"{t.title!r}: file missing: {t.ipod_path}",
                    track_id=t.track_id,
                    path=path,
                )
            )
            continue
        size = os.path.getsize(path)
        if size == 0:
            issues.append(
                _issue(
                    "empty_file",
                    subject,
                    f"{t.title!r}: empty file {t.ipod_path}",
                    track_id=t.track_id,
                    path=path,
                )
            )
        elif t.file_size != size:
            issues.append(
                _issue(
                    "size_mismatch",
                    subject,
                    f"{t.title!r}: database says {t.file_size} bytes, file has {size}",
                    track_id=t.track_id,
                    path=path,
                )
            )
        if size and t.sample_count and t.samplerate:
            # the gapless sample count is exact; a length far from it was estimated wrongly
            # (e.g. from the first frame of a VBR MP3 without Xing header)
            expected = t.sample_count * 1000 // t.samplerate
            if abs(t.duration_ms - expected) > 1000:
                issues.append(
                    _issue(
                        "length_mismatch",
                        subject,
                        f"{t.title!r}: length {t.duration_ms} ms, its {t.sample_count} samples "
                        f"last {expected} ms",
                        track_id=t.track_id,
                        path=path,
                    )
                )

    # --- orphan media files and pygpod's temporary files ---
    music = _find(ctl, "Music") if os.path.isdir(ctl) else None
    for dirpath, _, files in os.walk(ctl) if os.path.isdir(ctl) else ():
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            if name.endswith(_TEMP_SUFFIXES) or name in _TEMP_NAMES:
                issues.append(
                    _issue("temp_file", _rel(mp, path), "leftover temporary file", path=path)
                )
            elif music and os.path.commonpath([music, path]) == music:
                key = os.path.normcase(os.path.realpath(path)).lower()
                if key not in referenced:
                    issues.append(
                        _issue(
                            "orphan_file",
                            _rel(mp, path),
                            f"no track references this file ({os.path.getsize(path)} bytes)",
                            path=path,
                        )
                    )

    # --- write lock ---
    itunes = _find(ctl, "iTunes")
    lock = os.path.join(itunes, "pygpod.lock") if itunes else None
    if lock and os.path.isfile(lock):
        from .db.writelock import _stale

        if _stale(lock):
            issues.append(_issue("stale_lock", _rel(mp, lock), "stale write lock", path=lock))
        else:
            issues.append(
                _issue("write_in_progress", _rel(mp, lock), "another writer holds the lock")
            )

    # --- playlists ---
    known = {t.track_id for t in db.tracks}
    for pl in db.playlists:
        if not pl.record:
            continue
        for c in pl.record.children:
            if c.magic != MHIP_MAGIC or c.fields.get("podcastgroupflag"):
                continue
            tid = c.fields.get("track_id")
            if tid not in known:
                issues.append(
                    _issue(
                        "dangling_playlist_item",
                        f"playlist {pl.name}",
                        f"item points to unknown track {tid}",
                        track_id=tid,
                    )
                )
    mpl = db.master_playlist
    if mpl is not None:
        in_master = set(mpl.track_ids)
        for t in db.tracks:
            if t.track_id not in in_master:
                issues.append(
                    _issue(
                        "not_in_master",
                        f"track {t.track_id}",
                        f"{t.title!r} is not in the master playlist",
                        track_id=t.track_id,
                    )
                )

    issues += _check_artwork(db, ctl)
    issues += _check_photos(db)
    issues += _check_checksum(db)

    # --- space for the next save ---
    try:
        needed, free = db.estimate_db_size(), db.free_space()
        if free < needed:
            issues.append(
                _issue(
                    "low_space",
                    "iPod",
                    f"{free} bytes free, the next save needs about {needed}",
                )
            )
    except Exception:  # noqa: BLE001 - no disk information: nothing to report
        logger.debug("free space check failed", exc_info=True)
    return CheckReport(issues)


def _check_artwork(db: "Database", ctl: str) -> List[Issue]:
    from .db.artwork_parser import parse_artworkdb

    issues: List[Issue] = []
    artwork = _find(ctl, "Artwork") if os.path.isdir(ctl) else None
    adb = _find(artwork, "ArtworkDB") if artwork else None
    mhiis = {}
    if adb and os.path.isfile(adb):
        try:
            with open(adb, "rb") as f:
                root = parse_artworkdb(f.read())
        except Exception as e:  # noqa: BLE001 - unreadable: say so, judge no track by it
            logger.debug("ArtworkDB unreadable", exc_info=True)
            return [
                _issue(
                    "artwork_db_unreadable",
                    _rel(db.mountpoint, adb),
                    f"ArtworkDB could not be read ({e}); artwork references not checked",
                )
            ]
        for mhsd in root.children if root else ():
            if mhsd.fields.get("mhsd_type") == 1 and mhsd.children:
                for mhii in mhsd.children[0].children:
                    mhiis[mhii.fields.get("image_id")] = mhii
    by_dbid = {t.dbid: t for t in db.tracks}
    linked = set()
    for t in db.tracks:
        # has_artwork: 1 = yes, 2 = no (libgpod); only 1 promises an ArtworkDB entry
        if not (t.record is not None and t.record.fields.get("has_artwork") == 1 and t.mhii_link):
            continue
        linked.add(t.mhii_link)
        if t.mhii_link not in mhiis:
            issues.append(
                _issue(
                    "artwork_missing",
                    f"track {t.track_id}",
                    f"{t.title!r}: artwork {t.mhii_link} is not in the ArtworkDB",
                    track_id=t.track_id,
                    image_id=t.mhii_link,
                )
            )
    from .device.artwork_formats import image_data_size
    from .model.artwork_manager import ArtworkManager

    dev = db.device
    # only the device's own list is authoritative (iTunes writes it, the tables are older)
    formats = dev.get_sysinfo_cover_art_formats() if dev is not None else None
    sizes = {f.format_id: (f, image_data_size(f)) for f in formats or ()}
    for image_id, mhii in mhiis.items():
        track = by_dbid.get(mhii.fields.get("song_id"))
        if track is None or image_id not in linked:
            issues.append(
                _issue(
                    "artwork_orphan",
                    f"artwork {image_id}",
                    "ArtworkDB entry without a track",
                    image_id=image_id,
                )
            )
            continue
        for mhni in ArtworkManager._mhnis(mhii):
            name = f"F{mhni.fields.get('format_id')}_1.ithmb"
            for child in mhni.children:
                if child.fields.get("mhod_type") == 3 and child.fields.get("string"):
                    name = child.fields["string"].lstrip(":").split(":")[-1]
            path = _find(artwork, name) if artwork else None
            end = mhni.fields.get("ithumb_offset", 0) + mhni.fields.get("image_size", 0)
            if path is None or os.path.getsize(path) < end:
                issues.append(
                    _issue(
                        "artwork_out_of_range",
                        f"track {track.track_id}",
                        f"{track.title!r}: thumbnail ends at {end}, beyond {name}",
                        track_id=track.track_id,
                        image_id=image_id,
                    )
                )
                break
        wrong = []
        for mhni in ArtworkManager._mhnis(mhii):
            fmt, size = sizes.get(mhni.fields.get("format_id"), (None, 0))
            if fmt is not None and mhni.fields.get("image_size", 0) != size:
                wrong.append(
                    f"{fmt.format_id}: {mhni.fields.get('image_size', 0)} bytes, the iPod's "
                    f"{fmt.width}x{fmt.height} format needs {size}"
                )
        if wrong:
            issues.append(
                _issue(
                    "artwork_thumbnail_size",
                    f"track {track.track_id}",
                    f"{track.title!r}: cover thumbnail size does not match this iPod's cover "
                    "formats (" + "; ".join(wrong) + "): set the artwork again",
                    track_id=track.track_id,
                    image_id=image_id,
                )
            )
    return issues


def _check_photos(db: "Database") -> List[Issue]:
    """Several master photo albums, and photo thumbnails whose size does not match the device's
    photo format of the same id (libgpod cannot unpack them and the firmware does not show
    them)."""
    from .db.photodb import PhotoDB
    from .device.artwork_formats import image_data_size

    try:
        photodb = PhotoDB(db.mountpoint)
    except Exception as e:  # noqa: BLE001 - any parse error: report and skip the photo checks
        return [
            _issue(
                "photo_db_unreadable",
                "Photos/Photo Database",
                f"Photo Database could not be read ({e}); photo thumbnails not checked",
            )
        ]
    issues = []
    if photodb._merged_masters:
        issues.append(
            _issue(
                "photo_master_albums",
                "Photos/Photo Database",
                f"{photodb._merged_masters + 1} master photo albums (Photo Library), "
                "the iPod expects one",
            )
        )
    if photodb._master_missing:
        issues.append(
            _issue(
                "photo_master_album_missing",
                "Photos/Photo Database",
                "no master photo album (Photo Library); libgpod and iTunes always write one first",
            )
        )
    if photodb._not_in_master:
        issues.append(
            _issue(
                "photo_not_in_master",
                "Photos/Photo Database",
                f"{photodb._not_in_master} photo(s) missing from the Photo Library album "
                "(the iPod's Photo Library does not show them)",
            )
        )
    if photodb._dropped_members:
        issues.append(
            _issue(
                "photo_album_missing_photo",
                "Photos/Photo Database",
                f"{photodb._dropped_members} photo album member(s) reference no photo "
                "(libgpod and the iPod cannot read the albums)",
            )
        )
    dev = db.device
    formats = dev.get_photo_formats() if dev is not None else None
    if not formats:
        return issues
    sizes = {f.format_id: (f, image_data_size(f)) for f in formats}
    for photo in photodb.photos:
        wrong = []
        for thumb in photo.thumbnails:
            fmt, size = sizes.get(thumb.format_id, (None, 0))
            if fmt is not None and thumb.image_size != size:
                wrong.append(
                    f"{thumb.format_id}: {thumb.image_size} bytes, the iPod's "
                    f"{fmt.width}x{fmt.height} format needs {size}"
                )
        if wrong:
            issues.append(
                _issue(
                    "photo_thumbnail_size",
                    f"photo {photo.photo_id}",
                    "thumbnail size does not match this iPod's photo formats ("
                    + "; ".join(wrong)
                    + "): remove the photo and add it again",
                    image_id=photo.photo_id,
                )
            )
    return issues


def _check_checksum(db: "Database") -> List[Issue]:
    from .hash.checksum import detect_checksum_type, update_checksums

    dev = db.device
    if dev is None or (not dev.requires_hash and dev.checksum_known):
        return []
    path = str(dev.itunesdb_path())
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    subject = _rel(db.mountpoint, path)
    issues = []
    if dev.requires_hash:
        hash_type = dev.checksum_type.name
    else:  # model not identified: check the scheme the database carries
        hash_type = detect_checksum_type(data)
        if not hash_type:
            return []
        issues.append(
            _issue(
                "model_unidentified",
                _rel(db.mountpoint, str(dev.sysinfo_file_path)),
                f"iPod model not identified (no known ModelNumStr in SysInfo) but the iTunesDB "
                f"is signed with {hash_type}; pygpod signs it the same way on save, which needs "
                "the FirewireGuid",
            )
        )
    guid = dev.firewire_guid
    if not guid:
        return issues + [
            _issue("checksum_unknown", subject, "no FirewireGuid: checksum not verifiable")
        ]
    try:
        params = db._hash72_params(guid) if hash_type == "HASH72" else None
        expected = update_checksums(data, guid, hash_type, hash72_params=params)
    except Exception as e:  # noqa: BLE001 - scheme not computable here
        return issues + [_issue("checksum_unknown", subject, f"checksum not verifiable: {e}")]
    if expected != data:
        issues.append(
            _issue("checksum_mismatch", subject, f"{hash_type} does not match the database")
        )
    return issues


def repair(
    db: "Database",
    report: Optional[CheckReport] = None,
    delete_orphans: bool = False,
    dry_run: bool = False,
) -> List[RepairAction]:
    """Fix the issues of ``report`` (default: a fresh check()) and save the database once.

    Orphan media files are only deleted with ``delete_orphans=True`` (otherwise listed as
    not done). ``dry_run=True`` returns the actions without changing anything.
    """
    from .db.constants import MHIP_MAGIC

    if report is None:
        report = check(db)
    if "write_in_progress" in report.kinds():
        from .exceptions import ConflictError

        raise ConflictError(
            "another writer holds the database lock: repair after it has finished "
            "(its staging files must not be touched)"
        )
    actions: List[RepairAction] = []
    ctl = os.path.realpath(_ipod_control(db.mountpoint)) if db.mountpoint else ""
    save = False
    remove_tracks: Dict[int, None] = {}
    dangling: Set[int] = set()
    leftovers: List[str] = []
    stale = False
    photos = False

    def act(issue: Issue, action: str, done: bool = True) -> None:
        actions.append(RepairAction(issue.kind, issue.subject, action, done and not dry_run))

    def inside(path: Optional[str]) -> bool:
        return bool(path) and os.path.commonpath([ctl, os.path.realpath(path)]) == ctl

    if "interrupted_commit" in report.kinds():
        # complete the interrupted save first; everything else needs a fresh look afterwards
        from .db.transaction import recover

        issue = report.by_kind("interrupted_commit")[0]
        if not dry_run:
            recover(db.mountpoint)
        act(issue, "interrupted save completed: reopen the database and repair again")
        return actions

    for issue in report:
        k = issue.kind
        if not issue.fixable:
            continue
        if k == "orphan_file":
            if not delete_orphans:
                act(issue, "kept (use delete_orphans to delete it)", done=False)
            elif inside(issue.path):
                if not dry_run:
                    os.unlink(issue.path)
                act(issue, "deleted")
        elif k == "temp_file":
            if inside(issue.path):
                leftovers.append(issue.path)  # deleted below, under the writer lock
                act(issue, "deleted")
        elif k == "stale_lock":
            stale = True  # taken over (removed) by writer_lock below
            act(issue, "deleted")
        elif k in ("missing_file", "empty_file", "unsafe_path", "duplicate_path"):
            if issue.track_id not in remove_tracks:
                remove_tracks[issue.track_id] = None
                if k == "empty_file" and inside(issue.path):  # nothing to keep in it
                    if not dry_run:
                        os.unlink(issue.path)
                    act(issue, "track removed from the database, empty file deleted")
                else:
                    act(issue, "track removed from the database")
        elif k == "size_mismatch":
            t = db.get_track(issue.track_id)
            if t is not None and issue.track_id not in remove_tracks:
                if not dry_run:
                    size = os.path.getsize(issue.path)
                    t._set_field_and_header_u32("file_size", 0x24, size)
                act(issue, "file_size updated")
                save = True
        elif k == "length_mismatch":
            t = db.get_track(issue.track_id)
            if t is not None and issue.track_id not in remove_tracks:
                from .tags import read_tags

                tags = read_tags(issue.path)
                if tags.get("duration_ms"):
                    if not dry_run:
                        t._set_field_and_header_u32("tracklen", 0x28, tags["duration_ms"])
                        if tags.get("bitrate"):
                            t._set_field_and_header_u32("bitrate", 0x38, tags["bitrate"])
                    act(issue, f"length {tags['duration_ms']} ms read from the file")
                    save = True
                else:
                    act(issue, "length not readable from the file", done=False)
        elif k == "dangling_playlist_item":
            dangling.add(issue.track_id)
            act(issue, "item removed")
        elif k == "not_in_master":
            t = db.get_track(issue.track_id)
            if t is not None and issue.track_id not in remove_tracks:
                if not dry_run:
                    db.add_track_to_playlist(db.master_playlist, t)
                act(issue, "added to the master playlist")
                save = True
        elif k in ("artwork_missing", "artwork_out_of_range"):
            t = db.get_track(issue.track_id) if issue.track_id is not None else None
            if t is not None and not dry_run:
                if k == "artwork_out_of_range" and issue.image_id:
                    db._get_artwork_manager().remove_artwork(issue.image_id)
                db._set_artwork_fields(t.record, 0, 0, 0)
                t._touch()
            act(issue, "artwork reference cleared")
            save = True
        elif k == "artwork_orphan":
            if not dry_run:
                db._get_artwork_manager().remove_artwork(issue.image_id)
            act(issue, "ArtworkDB entry removed")
            save = True
        elif k == "checksum_mismatch":
            act(issue, "database re-signed")
            save = True
        elif k == "photo_master_albums":
            photos = True
            act(issue, "merged into the first master album")
        elif k == "photo_album_missing_photo":
            photos = True
            act(issue, "album members without a photo removed")
        elif k == "photo_master_album_missing":
            photos = True
            act(issue, "Photo Library album created with every photo")
        elif k == "photo_not_in_master":
            photos = True
            act(issue, "photos added to the Photo Library album")

    if dry_run:
        return actions
    if photos:
        from .db.photodb import PhotoDB

        PhotoDB(
            db.mountpoint
        ).save()  # loading merges/completes master albums, drops dangling members
    if leftovers or stale:
        # under the lock: no writer can be between staging and committing these files
        from .db.writelock import writer_lock

        with writer_lock(os.path.join(ctl, "iTunes")):
            for path in leftovers:
                if os.path.isfile(path):
                    os.unlink(path)
    if remove_tracks:
        tracks = [db.get_track(tid) for tid in remove_tracks]
        db.remove_tracks([t for t in tracks if t is not None], delete_files=False)
        save = True
    if dangling:
        for mhsd in db._root.children:  # both playlist lists (MHSD 2 and 3)
            if mhsd.fields.get("mhsd_type") not in (2, 3) or not mhsd.children:
                continue
            for mhyp in mhsd.children[0].children:
                mhyp.children = [
                    c
                    for c in mhyp.children
                    if not (
                        c.magic == MHIP_MAGIC
                        and not c.fields.get("podcastgroupflag")
                        and c.fields.get("track_id") in dangling
                    )
                ]
        db._modified = True
        save = True
    if save:
        db.save(force=True)
    return actions
