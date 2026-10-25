"""Crash-safe multi-file commit for iTunesDB, ArtworkDB, Photo Database and .ithmb files.

A single rename is atomic, but a save touches several files (iTunesDB,
ArtworkDB, compacted .ithmb files, deleted .ithmb files). A small redo
journal makes the whole set atomic:

1. stage: every new file is written next to its target as ``<name>.pygpod-new``,
   flushed and fsynced. A failure here leaves the old files untouched.
2. journal: ``iPod_Control/iTunes/pygpod.journal`` lists the renames and deletions
   (paths relative to the mount point); written to a temp file, fsynced, renamed.
   This is the commit point.
3. apply: rename every staged file over its target, delete the listed files,
   fsync the directories, remove the journal.

On open, recover() replays a leftover journal (roll forward; every step is
idempotent) and removes staging files that never got a journal (roll back).
The journal holds the SHA-256 of every staged file: a staging file that is gone
is only accepted when its target already has that content (renamed before the
interruption); otherwise the replay raises before it changes anything. Callers
run recover() under the writer lock (db/writelock.py) so it never touches the
files of a save in progress.
os.replace() over an existing file works on FAT (vfat), HFS+ and NTFS-3G.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Callable, List, Optional, Tuple

from ..exceptions import DatabaseError

logger = logging.getLogger(__name__)

STAGING_SUFFIX = ".pygpod-new"
JOURNAL_NAME = "pygpod.journal"
# Directories (relative to the mount point) that may hold staged files: the iTunesDB and
# ArtworkDB folders and the Photo Database with its thumbnails (Photos, Photos/Thumbs).
STAGING_DIRS = (
    os.path.join("iPod_Control", "iTunes"),
    os.path.join("iPod_Control", "Artwork"),
    "Photos",
    os.path.join("Photos", "Thumbs"),
)


class TransactionError(DatabaseError):
    """A journal entry is invalid (e.g. points outside the mount point)."""


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # not supported on every platform/filesystem
    finally:
        os.close(fd)


def _journal_path(mountpoint: str) -> str:
    return os.path.join(mountpoint, "iPod_Control", "iTunes", JOURNAL_NAME)


def _resolve(mountpoint: str, rel: str) -> str:
    """Absolute path of a journal entry; refuses anything outside the mount point."""
    if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        raise TransactionError(f"invalid journal path: {rel!r}")
    root = os.path.realpath(mountpoint)
    path = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, path]) != root:
        raise TransactionError(f"journal path escapes the mount point: {rel!r}")
    return path


class Transaction:
    """Stage files, then commit them all or none."""

    def __init__(self, mountpoint: str) -> None:
        self._mountpoint = mountpoint
        self._staged: List[Tuple[str, str, str]] = []  # (staging, target, sha256), absolute
        self._deletes: List[str] = []
        self._on_commit: List[Callable[[], None]] = []
        self._on_abort: List[Callable[[], None]] = []
        self._done = False
        self._journal_written = False

    @property
    def journal_written(self) -> bool:
        """True once the commit point was reached: the changes are made by commit() or, if
        it failed after that, by the next recover()."""
        return self._journal_written

    def _rel(self, path: str) -> str:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(self._mountpoint))
        _resolve(self._mountpoint, rel)
        return rel

    def stage_with(self, target: str, write: Callable[[str], None]) -> None:
        """Stage a new version of ``target``; ``write(path)`` creates the file at ``path``."""
        self._rel(target)
        tmp = target + STAGING_SUFFIX
        self._rel(tmp)  # a symbolic link there must not redirect the write either
        self._staged.append((tmp, target, ""))
        write(tmp)
        digest = hashlib.sha256()
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        self._staged[-1] = (tmp, target, digest.hexdigest())

    def stage_bytes(self, target: str, data: bytes) -> None:
        def write(path: str) -> None:
            with open(path, "wb") as f:
                f.write(data)

        self.stage_with(target, write)

    def delete_on_commit(self, path: str) -> None:
        self._rel(path)
        self._deletes.append(path)

    def on_commit(self, callback: Callable[[], None]) -> None:
        self._on_commit.append(callback)

    def on_abort(self, callback: Callable[[], None]) -> None:
        self._on_abort.append(callback)

    def _write_journal(self) -> None:
        for d in {os.path.dirname(t) for t, _, _ in self._staged}:
            _fsync_dir(d)
        journal = {
            "replace": [[self._rel(t), self._rel(f), h] for t, f, h in self._staged],
            "delete": [self._rel(p) for p in self._deletes],
        }
        path = _journal_path(self._mountpoint)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(journal, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(os.path.dirname(path))

    def commit(self) -> None:
        """Make all staged changes visible atomically (with respect to recover())."""
        if self._done:
            raise TransactionError("transaction already finished")
        self._write_journal()
        self._done = True
        self._journal_written = True
        _apply_journal(self._mountpoint)
        self.run_commit_callbacks()

    def run_commit_callbacks(self) -> None:
        """Run the on_commit callbacks (once): by commit(), or by the caller after recover()
        completed this transaction's interrupted commit."""
        callbacks, self._on_commit = self._on_commit, []
        for cb in callbacks:
            cb()

    def abort(self) -> None:
        """Discard staged files; targets are untouched."""
        if self._done:
            return
        self._done = True
        for tmp, _, _ in self._staged:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        for cb in self._on_abort:
            cb()


def _apply_journal(mountpoint: str) -> None:
    path = _journal_path(mountpoint)
    with open(path, "r", encoding="utf-8") as f:
        journal = json.load(f)
    replace = []
    for entry in journal.get("replace", []):
        tmp, target = _resolve(mountpoint, entry[0]), _resolve(mountpoint, entry[1])
        if not os.path.exists(tmp):
            # renamed before the interruption - or lost: then the commit cannot be completed
            # (journals of older versions carry no hash and are trusted as before)
            if len(entry) > 2 and entry[2] and _sha256(target) != entry[2]:
                raise TransactionError(
                    f"the interrupted save cannot be completed: {entry[0]} is missing and "
                    f"{entry[1]} does not hold its content; nothing was replaced (remove "
                    f"{_journal_path(mountpoint)} to keep the current files, or restore a backup)"
                )
        replace.append((tmp, target))
    dirs = {os.path.dirname(target) for _, target in replace}
    for tmp, target in replace:
        if os.path.exists(tmp):
            os.replace(tmp, target)
    for rel in journal.get("delete", []):
        target = _resolve(mountpoint, rel)
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        dirs.add(os.path.dirname(target))
    for d in dirs:
        _fsync_dir(d)
    os.unlink(path)
    _fsync_dir(os.path.dirname(path))


def _sha256(path: str) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def interrupted_commit(mountpoint: str) -> bool:
    """True if an interrupted commit left its journal (recover() completes it)."""
    return os.path.isfile(_journal_path(mountpoint))


def _child_dir(parent: str, name: str) -> Optional[str]:
    """The directory of ``parent`` named ``name``, matched case-insensitively (FAT keeps the case it
    was created with), spelled as it is on disk, else None. The listing comes first: on a
    case-insensitive file system ``parent/name`` exists in any spelling."""
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    if name in names and os.path.isdir(os.path.join(parent, name)):
        return os.path.join(parent, name)
    for n in names:
        if n.lower() == name.lower() and os.path.isdir(os.path.join(parent, n)):
            return os.path.join(parent, n)
    return None


def _staging_dirs(mountpoint: str) -> List[str]:
    """The existing STAGING_DIRS as absolute paths, matched case-insensitively."""
    out = []
    for rel_dir in STAGING_DIRS:
        d: Optional[str] = mountpoint
        for part in rel_dir.split(os.sep):
            d = _child_dir(d, part) if d is not None else None
        if d is not None and d not in out:
            out.append(d)
    return out


def leftover_files(mountpoint: str) -> list:
    """Staging files of an interrupted commit that recover() would remove (absolute paths)."""
    out = []
    for d in _staging_dirs(mountpoint):
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(STAGING_SUFFIX) or name == JOURNAL_NAME + ".tmp":
                    out.append(os.path.join(d, name))
    return out


def recover(mountpoint: str) -> Optional[str]:
    """Finish or discard an interrupted commit.

    Returns:
        "rolled-forward", "rolled-back" or None (nothing to do).
    """
    result: Optional[str] = None
    if os.path.isfile(_journal_path(mountpoint)):
        logger.warning("Completing an interrupted database commit at %s", mountpoint)
        _apply_journal(mountpoint)
        result = "rolled-forward"
    for d in _staging_dirs(mountpoint):
        for name in os.listdir(d):
            if name.endswith(STAGING_SUFFIX) or name == JOURNAL_NAME + ".tmp":
                try:
                    os.unlink(os.path.join(d, name))
                    result = result or "rolled-back"
                    logger.warning("Removed uncommitted %s", os.path.join(d, name))
                except OSError:
                    logger.warning("Failed to remove %s", name, exc_info=True)
    return result
