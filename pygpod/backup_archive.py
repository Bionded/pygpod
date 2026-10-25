"""Backup and restore of an iPod: database files and media to/from a tar archive.

``backup()`` writes everything under ``iPod_Control`` (iTunesDB, Play Counts, ArtworkDB and
.ithmb files, SysInfo files in ``Device``, and with ``include_media`` the music files) and the
Photo Database with its thumbnails in ``<mountpoint>/Photos`` (with ``include_media`` also the
full resolution photos) into a tar archive (``.tar``, ``.tar.gz``/``.tgz``, ``.tar.bz2``,
``.tar.xz``; stdlib only) with a ``manifest.json`` (pygpod version, device identity,
database id, every file with size and SHA-1). ``restore()`` verifies the whole archive before
it touches the iPod, checks free space, refuses an archive made on another iPod (another
FirewireGuid: the iTunesDB checksum is tied to it) unless ``force=True``, and writes each file
under a temporary name first, renamed into place, the database files last.

Restoring onto a different iPod with ``force=True`` keeps that iPod's own ``Device`` files
(SysInfo) and re-signs the database for it.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import logging
import os
import pathlib
import posixpath
import shutil
import tarfile
import tempfile
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from .exceptions import PyGpodError

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
FORMAT_NAME = "pygpod-backup"
FORMAT_VERSION = 2  # 2: also <mountpoint>/Photos (archive root "Photos/")
ROOT = "iPod_Control"
PHOTOS_ROOT = "Photos"
# below iPod_Control: what include_media covers
MEDIA_DIRS = ("Music", "Photos/Full Resolution")
# below <mountpoint>/Photos
PHOTO_MEDIA_DIRS = ("Full Resolution",)
# never archived: pygpod's own lock / staging / temporary files
_SKIP_NAMES = {"pygpod.lock", "pygpod.journal"}
_SKIP_SUFFIXES = (".pygpod-new", ".pygpod-restore", ".tmp")
_JOURNAL = "iTunes/pygpod.journal"
_RESTORE_SUFFIX = ".pygpod-restore"
_FREE_SPACE_MARGIN = 16 * 1024 * 1024
_CHUNK = 1024 * 1024
_BACKUP_ATTEMPTS = 3
_WRITER_WAIT_S = 10.0

# progress(done_bytes, total_bytes, archive path of the current file)
Progress = Callable[[int, int, str], None]


class BackupError(PyGpodError):
    """A backup could not be written, or an archive cannot be restored."""


class BackupFile(NamedTuple):
    path: str  # archive path, e.g. "iPod_Control/iTunes/iTunesDB"
    size: int
    sha1: str


class BackupInfo(NamedTuple):
    """What a backup archive contains (returned by backup(), read_manifest(), restore())."""

    archive: str
    pygpod_version: str
    created: str  # ISO 8601, UTC
    firewire_guid: Optional[str]
    model: Optional[str]
    db_id: Optional[str]  # hex
    include_media: bool
    files: List[BackupFile]

    @property
    def total_size(self) -> int:
        """Sum of the archived files' sizes in bytes."""
        return sum(f.size for f in self.files)


class RestoreResult(NamedTuple):
    """Result of restore(): the archive and the files written (or planned, with dry_run)."""

    info: BackupInfo
    written: List[str]  # archive paths (media first, the iTunesDB last)
    skipped: List[str]  # archive paths not written (media without include_media, Device on force)
    bytes_written: int
    resigned: bool  # database re-signed for the target iPod (force onto another FirewireGuid)
    dry_run: bool


def _is_media(rel: str) -> bool:
    """rel: path below iPod_Control, posix."""
    return any(rel == d or rel.startswith(d + "/") for d in MEDIA_DIRS)


def _member_root(name: str) -> Tuple[str, str]:
    """An archive path split into its root (iPod_Control or Photos) and the path below it."""
    root, _, rel = name.partition("/")
    return root, rel


def _is_media_member(name: str) -> bool:
    root, rel = _member_root(name)
    if root == PHOTOS_ROOT:
        return any(rel == d or rel.startswith(d + "/") for d in PHOTO_MEDIA_DIRS)
    return _is_media(rel)


def _skipped_name(name: str) -> bool:
    return name in _SKIP_NAMES or name.endswith(_SKIP_SUFFIXES)


def _tar_mode(archive_path: str, write: bool) -> str:
    name = archive_path.lower()
    for suffixes, comp in (
        ((".tar.gz", ".tgz"), "gz"),
        ((".tar.bz2", ".tbz2"), "bz2"),
        ((".tar.xz", ".txz"), "xz"),
        ((".tar",), ""),
    ):
        if name.endswith(suffixes):
            return ("w:" if write else "r:") + comp
    if write:
        raise BackupError(f"unknown archive type (use .tar, .tar.gz, .tar.bz2, .tar.xz): {name}")
    return "r:*"


def _identity(mountpoint: str):
    """(FirewireGuid, ModelNumStr) from the SysInfo files, without any USB probing."""
    from .model.sysinfo import read_sysinfo

    try:
        sysinfo = read_sysinfo(mountpoint)
    except Exception:  # noqa: BLE001 - unreadable SysInfo = unknown identity
        logger.debug("SysInfo unreadable on %s", mountpoint, exc_info=True)
        return None, None
    guid = sysinfo.firewire_guid
    return (guid.upper() if guid else None), sysinfo.model_number or None


def _db_id(ipod_control: pathlib.Path) -> Optional[str]:
    """The iTunesDB's database id (mhbd 0x18, u64 LE) as hex."""
    from .device.mountpoint import _find_ci

    itunes = _find_ci(ipod_control, "iTunes")
    db = _find_ci(itunes, "iTunesDB") if itunes else None
    try:
        with open(str(db), "rb") as f:
            head = f.read(0x20)
    except (OSError, TypeError):
        return None
    if len(head) < 0x20 or head[:4] != b"mhbd":
        return None
    return "%016x" % int.from_bytes(head[0x18:0x20], "little")


def _walk(base: pathlib.Path, is_media: Callable[[str], bool], include_media: bool):
    for dirpath, dirnames, filenames in os.walk(str(base)):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, str(base)).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        if not include_media:
            dirnames[:] = [d for d in dirnames if not is_media(posixpath.join(rel_dir, d))]
        for name in sorted(filenames):
            path = pathlib.Path(dirpath, name)
            if _skipped_name(name) or path.is_symlink() or not path.is_file():
                continue
            yield posixpath.join(rel_dir, name), path


def _collect(
    ipod_control: pathlib.Path, include_media: bool, mountpoint: Optional[str] = None
) -> List[Tuple[str, pathlib.Path]]:
    """(archive path, file) of everything a backup holds: iPod_Control and, with
    ``mountpoint``, the Photo Database directory ``<mountpoint>/Photos``."""
    files = [(f"{ROOT}/{rel}", p) for rel, p in _walk(ipod_control, _is_media, include_media)]
    if mountpoint is not None:
        from .db.photodb import photos_dir_for

        photos = pathlib.Path(photos_dir_for(mountpoint))
        if photos.is_dir():
            files += [
                (f"{PHOTOS_ROOT}/{rel}", p)
                for rel, p in _walk(
                    photos, lambda r: _is_media_member(f"{PHOTOS_ROOT}/{r}"), include_media
                )
            ]
    return files


class _HashingReader(io.RawIOBase):
    def __init__(self, f, progress: Optional[Callable[[int], None]] = None):
        self._f = f
        self.sha1 = hashlib.sha1()
        self._progress = progress

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        self.sha1.update(data)
        if self._progress and data:
            self._progress(len(data))
        return data


def backup(
    mountpoint: str,
    archive_path: str,
    include_media: bool = True,
    progress: Optional[Progress] = None,
) -> BackupInfo:
    """Write a backup of the iPod at ``mountpoint`` to ``archive_path``.

    The archive type follows the name: ``.tar``, ``.tar.gz``/``.tgz``, ``.tar.bz2``,
    ``.tar.xz``. ``include_media=False`` leaves out the music files (``Music``) and full
    resolution photos: the database, artwork and photo thumbnails only. The Photo Database
    (``<mountpoint>/Photos``) is archived under ``Photos/``. The archive is written under a
    temporary name and renamed when complete. Nothing on the iPod is changed or created, so a
    read-only mount works: no writer lock is taken; while another pygpod process holds it the
    backup waits a few seconds for the save to finish, and if the database files change while
    they are archived the backup is made again.

    Raises:
        BackupError: no iPod_Control, an interrupted save (journal present), unknown archive
            type, a file could not be read, or the database kept changing during the backup.
    """
    from .device.mountpoint import _find_ipod_control

    mode = _tar_mode(archive_path, write=True)
    ipod_control = _find_ipod_control(pathlib.Path(mountpoint))
    if ipod_control is None or not ipod_control.is_dir():
        raise BackupError(f"not an iPod (no iPod_Control): {mountpoint}")
    # nothing is created on the iPod (a backup is often made from a read-only mount), so no
    # writer lock is taken: a save running meanwhile is waited for, and the database files are
    # compared before and after archiving; a backup that saw them change (or vanish, or
    # shrink while being read) is made again
    for _ in range(_BACKUP_ATTEMPTS):
        _wait_for_writer(ipod_control)
        before = _db_state(ipod_control, mountpoint)
        if before[0]:
            raise BackupError(
                "an interrupted save left a journal: open the database with pygpod once "
                "(it recovers) before making a backup"
            )
        try:
            info = _write_backup(
                mountpoint, archive_path, mode, ipod_control, include_media, progress
            )
        except (BackupError, OSError):
            if _db_state(ipod_control, mountpoint) == before:
                raise  # not a concurrent save: a real read or write error
        else:
            if _db_state(ipod_control, mountpoint) == before:
                return info
            os.unlink(archive_path)
        logger.warning("the database changed during the backup of %s, retrying", mountpoint)
    raise BackupError("the database kept changing during the backup: is a sync running?")


def _db_state(ipod_control: pathlib.Path, mountpoint: str):
    """Size and mtime of every database file (not media) and whether a save is in progress."""
    state = []
    for _, path in _collect(ipod_control, False, mountpoint):
        try:
            st = path.stat()
        except OSError:
            continue
        state.append((str(path), st.st_size, st.st_mtime_ns))
    return (ipod_control / _JOURNAL).exists(), state


def _wait_for_writer(ipod_control: pathlib.Path) -> None:
    """Wait (up to _WRITER_WAIT_S) while a live pygpod writer holds the lock; then go on."""
    from .db.writelock import writer_active

    itunes = str(ipod_control / "iTunes")
    deadline = time.monotonic() + _WRITER_WAIT_S
    while writer_active(itunes):
        if time.monotonic() >= deadline:
            logger.warning(
                "another process holds the database writer lock on %s; backing up anyway "
                "(the archive is checked against changes)",
                itunes,
            )
            return
        time.sleep(0.2)


def _write_backup(mountpoint, archive_path, mode, ipod_control, include_media, progress):
    from . import __version__

    entries = _collect(ipod_control, include_media, mountpoint)
    sizes = [p.stat().st_size for _, p in entries]
    total = sum(sizes)
    guid, model = _identity(mountpoint)
    created = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    out_dir = os.path.dirname(os.path.abspath(archive_path))
    fd, tmp = tempfile.mkstemp(prefix=".pygpod-backup-", dir=out_dir)
    os.close(fd)
    files: List[BackupFile] = []
    done = [0]
    try:
        with tarfile.open(tmp, mode) as tar:
            for (arc, path), size in zip(entries, sizes):

                def step(n: int, arc: str = arc) -> None:
                    done[0] += n
                    if progress:
                        progress(done[0], total, arc)

                ti = tarfile.TarInfo(arc)
                ti.size = size
                ti.mtime = int(path.stat().st_mtime)
                ti.mode = 0o644
                with open(str(path), "rb") as f:
                    reader = _HashingReader(f, step)
                    tar.addfile(ti, reader)
                files.append(BackupFile(arc, size, reader.sha1.hexdigest()))
            info = BackupInfo(
                archive_path,
                __version__,
                created,
                guid,
                model,
                _db_id(ipod_control),
                include_media,
                files,
            )
            data = json.dumps(_manifest_dict(info), indent=1, ensure_ascii=False).encode()
            ti = tarfile.TarInfo(MANIFEST_NAME)
            ti.size = len(data)
            ti.mtime = int(datetime.datetime.now().timestamp())
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))
        os.replace(tmp, archive_path)
    except (OSError, tarfile.TarError) as e:
        os.unlink(tmp)
        raise BackupError(f"backup failed: {e}") from e
    except BaseException:
        os.unlink(tmp)
        raise
    logger.info(
        "Backup of %s: %d files, %d bytes -> %s", mountpoint, len(files), total, archive_path
    )
    return info


def _manifest_dict(info: BackupInfo) -> dict:
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "pygpod_version": info.pygpod_version,
        "created": info.created,
        "firewire_guid": info.firewire_guid,
        "model": info.model,
        "db_id": info.db_id,
        "include_media": info.include_media,
        "files": [{"path": f.path, "size": f.size, "sha1": f.sha1} for f in info.files],
    }


def _info_from_manifest(archive_path: str, data: bytes) -> BackupInfo:
    try:
        m = json.loads(data.decode("utf-8"))
        if m.get("format") != FORMAT_NAME:
            raise ValueError("not a pygpod backup")
        if int(m.get("format_version", 0)) > FORMAT_VERSION:
            raise ValueError(f"format version {m['format_version']} is newer than this pygpod")
        files = [BackupFile(str(f["path"]), int(f["size"]), str(f["sha1"])) for f in m["files"]]
        return BackupInfo(
            archive_path,
            str(m.get("pygpod_version", "")),
            str(m.get("created", "")),
            m.get("firewire_guid"),
            m.get("model"),
            m.get("db_id"),
            bool(m.get("include_media")),
            files,
        )
    except (ValueError, KeyError, TypeError) as e:
        raise BackupError(f"{archive_path}: bad manifest: {e}") from e


def _safe_member_path(name: str) -> bool:
    if not name.startswith((ROOT + "/", PHOTOS_ROOT + "/")) or "\\" in name or "\x00" in name:
        return False
    parts = name.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def read_manifest(archive_path: str) -> BackupInfo:
    """The manifest of a backup archive (no verification of the file contents)."""
    try:
        with tarfile.open(archive_path, _tar_mode(archive_path, write=False)) as tar:
            member = tar.getmember(MANIFEST_NAME)
            f = tar.extractfile(member)
            if f is None:
                raise BackupError(f"{archive_path}: bad manifest entry")
            return _info_from_manifest(archive_path, f.read())
    except KeyError:
        raise BackupError(f"{archive_path}: no {MANIFEST_NAME} (not a pygpod backup)") from None
    except (OSError, tarfile.TarError, EOFError) as e:
        raise BackupError(f"{archive_path}: unreadable archive: {e}") from e


def verify(archive_path: str) -> BackupInfo:
    """Read the whole archive and check every file against the manifest (size and SHA-1).

    Raises:
        BackupError: unreadable or corrupted archive, unsafe or unexpected entries.
    """
    hashes: Dict[str, BackupFile] = {}
    manifest = None
    try:
        with tarfile.open(archive_path, _tar_mode(archive_path, write=False)) as tar:
            for member in tar:
                if member.name == MANIFEST_NAME and member.isfile():
                    f = tar.extractfile(member)
                    manifest = f.read() if f else b""
                    continue
                if not member.isfile() or not _safe_member_path(member.name):
                    raise BackupError(f"{archive_path}: unexpected entry {member.name!r}")
                if member.name in hashes:
                    raise BackupError(f"{archive_path}: duplicate entry {member.name!r}")
                f = tar.extractfile(member)
                sha1, size = hashlib.sha1(), 0
                while True:
                    chunk = f.read(_CHUNK) if f else b""
                    if not chunk:
                        break
                    sha1.update(chunk)
                    size += len(chunk)
                hashes[member.name] = BackupFile(member.name, size, sha1.hexdigest())
    except (OSError, tarfile.TarError, EOFError) as e:
        raise BackupError(f"{archive_path}: unreadable archive: {e}") from e
    if manifest is None:
        raise BackupError(f"{archive_path}: no {MANIFEST_NAME} (not a pygpod backup)")
    info = _info_from_manifest(archive_path, manifest)
    listed = {f.path: f for f in info.files}
    if set(listed) != set(hashes):
        missing = sorted(set(listed) - set(hashes))[:3]
        extra = sorted(set(hashes) - set(listed))[:3]
        raise BackupError(f"{archive_path}: files differ from the manifest {missing} {extra}")
    for path, f in listed.items():
        if hashes[path] != f:
            raise BackupError(f"{archive_path}: {path} is corrupted (size/SHA-1 mismatch)")
    return info


def _write_order(name: str) -> tuple:
    """Media first, then artwork/photos/device, iTunes files, the iTunesDB/iTunesCDB last."""
    root, rel = _member_root(name)
    low = rel.lower()
    if _is_media_member(name):
        return (0, name)
    if root == ROOT and low.startswith("itunes/"):
        base = low.split("/", 1)[1]
        return (3 if base in ("itunesdb", "itunescdb") else 2, name)
    return (1, name)


def _target_path(ipod_control: pathlib.Path, rel: str) -> pathlib.Path:
    """rel below iPod_Control (or another base directory) mapped onto existing directories
    case-insensitively (FAT)."""
    from .device.mountpoint import _find_ci

    path = ipod_control
    for part in rel.split("/"):
        found = _find_ci(path, part) if path.is_dir() else None
        path = found if found is not None else path / part
    return path


_ASIDE_SUFFIX = ".pre-restore"


def _position_indexed(name: str) -> bool:
    """Play count / OTG files: their entries are positions in the iTunesDB track list."""
    low = name.lower()
    return low in ("play counts", "itunesstats", "playcounts.plist") or (
        low.startswith("otgplaylistinfo") and not low.endswith(_ASIDE_SUFFIX)
    )


def _copy_member(src, path: str, expected_sha1: str, archive_path: str, name: str, step) -> None:
    sha1 = hashlib.sha1()
    with open(path, "wb") as out:
        while True:
            chunk = src.read(_CHUNK) if src else b""
            if not chunk:
                break
            sha1.update(chunk)
            out.write(chunk)
            step(len(chunk), name)
        out.flush()
        os.fsync(out.fileno())
    if sha1.hexdigest() != expected_sha1:  # the archive changed since verify()
        raise BackupError(f"{archive_path}: {name} is corrupted")


def _restore_files(archive_path, ipod_control, wanted, tx, total, progress) -> None:
    """Media files: written and renamed one by one. Database files: staged in ``tx``."""
    done = [0]

    def step(n: int, name: str) -> None:
        done[0] += n
        if progress:
            progress(done[0], total, name)

    with tarfile.open(archive_path, _tar_mode(archive_path, write=False)) as tar:
        for member in tar:
            if member.name not in wanted:
                continue
            f, base, rel = wanted[member.name]
            target = _target_path(base, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if _is_media_member(member.name):
                tmp = str(target) + _RESTORE_SUFFIX
                try:
                    _copy_member(src, tmp, f.sha1, archive_path, member.name, step)
                    os.replace(tmp, str(target))
                except BaseException:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
            else:
                tx.stage_with(
                    str(target),
                    lambda path, src=src, f=f, name=member.name: _copy_member(
                        src, path, f.sha1, archive_path, name, step
                    ),
                )


def restore(
    archive_path: str,
    mountpoint: str,
    include_media: bool = True,
    dry_run: bool = False,
    progress: Optional[Progress] = None,
    force: bool = False,
) -> RestoreResult:
    """Restore a backup archive onto the iPod at ``mountpoint``.

    The whole archive is verified first (manifest, sizes, SHA-1); a corrupted archive raises
    before anything is written. The target is the same iPod or a freshly initialised one
    (``init_ipod``); an iPod with another FirewireGuid is refused unless ``force=True``, which
    keeps the target's ``Device`` files and re-signs the database for it. Files not in the
    archive are left alone, except play count and On-The-Go files (their entries are track
    positions of the database being replaced): those are moved to ``<name>.pre-restore``.
    The writer lock is held while files are replaced; the database files are committed
    together through the same journal as Database.save(). A target with an interrupted
    save is refused. ``include_media=False`` restores the database and artwork only.
    ``dry_run=True`` verifies and plans but writes nothing.

    Raises:
        BackupError: corrupted/unsupported archive, other iPod without force, not enough space.
    """
    from .db.transaction import interrupted_commit
    from .device.mountpoint import _find_ipod_control

    info = verify(archive_path)
    if not os.path.isdir(mountpoint):
        raise BackupError(f"mount point does not exist: {mountpoint}")
    if interrupted_commit(mountpoint):
        raise BackupError(
            "an interrupted save left a journal on the target: open the database with pygpod "
            "once (it recovers) before restoring"
        )
    ipod_control = _find_ipod_control(pathlib.Path(mountpoint)) or pathlib.Path(mountpoint, ROOT)
    target_guid, _ = _identity(mountpoint)
    other_ipod = bool(
        info.firewire_guid and target_guid and info.firewire_guid.upper() != target_guid.upper()
    )
    if other_ipod and not force:
        raise BackupError(
            f"the backup is of another iPod (FirewireGuid {info.firewire_guid}, this one is "
            f"{target_guid}); pass force=True to restore it here and re-sign the database"
        )

    from .db.photodb import photos_dir_for

    photos = pathlib.Path(photos_dir_for(mountpoint))
    plan, skipped = [], []
    for f in info.files:
        root, rel = _member_root(f.path)
        if (not include_media and _is_media_member(f.path)) or (
            other_ipod and root == ROOT and rel.lower().startswith("device/")
        ):
            skipped.append(f.path)
            continue
        plan.append((_write_order(f.path), f, ipod_control if root == ROOT else photos, rel))
    plan.sort(key=lambda x: x[0])

    # media files go into place one by one (one temporary copy at a time); database files are
    # staged together and renamed at the end
    needed = largest = 0
    for _, f, base, rel in plan:
        if _is_media_member(f.path):
            target = _target_path(base, rel)
            existing = target.stat().st_size if target.is_file() else 0
            needed += max(0, f.size - existing)
            largest = max(largest, f.size)
        else:
            needed += f.size
    needed += largest
    free = shutil.disk_usage(mountpoint).free
    if needed + _FREE_SPACE_MARGIN > free:
        raise BackupError(f"not enough free space: need {needed} bytes, {free} free")

    written = [f.path for _, f, _, _ in plan]
    if dry_run:
        return RestoreResult(info, written, skipped, 0, False, True)

    wanted = {f.path: (f, base, rel) for _, f, base, rel in plan}
    total = sum(f.size for f, _, _ in wanted.values())
    archived = {
        rel.lower() for root, rel in map(_member_root, (f.path for f in info.files)) if root == ROOT
    }
    itunes = _target_path(ipod_control, "iTunes")
    itunes.mkdir(parents=True, exist_ok=True)

    from .db.transaction import Transaction
    from .db.writelock import writer_lock

    with writer_lock(str(itunes)):  # no save may run while the files are replaced
        tx = Transaction(mountpoint)
        try:
            _restore_files(archive_path, ipod_control, wanted, tx, total, progress)
            # files indexed by track position that the archive does not replace would point
            # at the wrong tracks of the restored database: moved aside, not deleted
            for name in sorted(os.listdir(str(itunes))):
                path = itunes / name
                if (
                    path.is_file()
                    and _position_indexed(name)
                    and f"itunes/{name.lower()}" not in archived
                ):
                    data = path.read_bytes()
                    tx.stage_bytes(str(path) + _ASIDE_SUFFIX, data)
                    tx.delete_on_commit(str(path))
                    skipped.append(f"{ROOT}/iTunes/{name} (moved to {name}{_ASIDE_SUFFIX})")
            tx.commit()  # database files all or none (journal), like Database.save()
        except BaseException:
            tx.abort()
            raise

    resigned = False
    if other_ipod:
        from .model.database import Database

        Database(mountpoint, apply_play_counts=False).save(force=True)
        resigned = True
    logger.info("Restored %s onto %s: %d files", archive_path, mountpoint, len(written))
    return RestoreResult(info, written, skipped, total, resigned, False)
