# Maintenance: check, repair, backup, restore

## Check and repair

`Database.check()` is a read-only consistency check of the database and the files on the iPod;
`repair()` fixes what it can and saves once. Orphan media files are deleted only with
`delete_orphans=True`; `dry_run=True` lists the actions without doing them. The CLI equivalent is
`pygpod doctor` / `pygpod doctor --fix [--delete-orphans]` (exit code 1 when issues are found).
Open the database with `recover=False` (and `apply_play_counts=False`) for a check that changes
nothing at all.

When another program saved the database after it was loaded, `check()` reports
`database_changed` (and no orphan files: they may be that program's new tracks) and `repair()`
raises `ConflictError` without deleting or saving anything; `pygpod doctor --fix` then exits
with code 1. Reopen the database and check again.

```python
import pygpod

db = pygpod.Database(mountpoint, apply_play_counts=False, recover=False)
report = db.check()
for issue in report:
    print(issue.severity, issue.kind, issue.subject, issue.message, issue.fixable)
print(report.summary(), db.repair(report, dry_run=True))
```

| kind | severity | found | `repair()` |
|---|---|---|---|
| `interrupted_commit` | error | a save was interrupted | completes it |
| `database_changed` | error | another writer saved the database after it was loaded | - (`ConflictError`: reopen, check again) |
| `database_damaged` | error | the iTunesDB is damaged, only the part before it was read (`partial=True`) | - (restore a backup) |
| `orphan_file` | warning | a media file no track references | deletes it, only with `delete_orphans` |
| `missing_file` | error | a track's file is missing | removes the track |
| `empty_file` | error | a track's file is empty | removes the track |
| `size_mismatch` | warning | file size differs from the database | updates the size |
| `length_mismatch` | warning | length disagrees with the sample count by more than a second | reads length and bitrate from the file again |
| `unsafe_path` | error | a track's path points outside the mount point | removes the track |
| `duplicate_path` | error | several tracks share one file | removes the later tracks, keeps the file |
| `dangling_playlist_item` | error | a playlist item without its track | removes the item |
| `not_in_master` | error | a track missing from the master playlist | adds it |
| `artwork_db_unreadable` | error | the ArtworkDB cannot be parsed | - (the artwork checks are skipped; `Database.reset_artwork()` starts an empty one) |
| `artwork_missing` | warning | a track points at a cover that does not exist | clears the reference |
| `artwork_orphan` | warning | a cover no track uses | removes it |
| `artwork_out_of_range` | warning | a cover points outside its .ithmb file | removes it, clears the reference |
| `artwork_thumbnail_size` | warning | a cover in a size the iPod's formats do not list | - (`set_artwork()` again) |
| `photo_db_unreadable` | error | the Photo Database cannot be parsed | - (the photo checks are skipped) |
| `photo_master_albums` | error | several master photo albums | merges them into the first |
| `photo_master_album_missing` | error | no master photo album (Photo Library) | creates it first, with every photo |
| `photo_album_missing_photo` | error | an album member whose photo does not exist | drops it |
| `photo_not_in_master` | error | photos missing from the Photo Library album | adds them |
| `photo_thumbnail_size` | warning | photo thumbnails the iPod does not show | - (remove the photo and add it again) |
| `checksum_mismatch` | error | the database signature is wrong | re-signs the database |
| `checksum_unknown` | info | the signature cannot be verified (no FirewireGuid / HashInfo) | - |
| `model_unidentified` | warning | a signed database, but SysInfo names no known model | - (create SysInfo) |
| `low_space` | warning | little free space left | - |
| `temp_file` | warning | a leftover temporary file of pygpod | deletes it |
| `interrupted_restore` | warning | a file kept by a restore that did not finish | - (run the restore again) |
| `stale_lock` | warning | a write lock of a writer that is gone | deletes it |
| `write_in_progress` | info | another writer holds the lock right now | - |

## Backup and restore

`backup()` writes everything under `iPod_Control` (iTunesDB, play counts, ArtworkDB and .ithmb
files, SysInfo, the music unless `include_media=False`) and the Photo Database with its
thumbnails in `<mount>/Photos` (full-resolution photos only with `include_media`) to a `.tar`,
`.tar.gz`, `.tar.bz2` or `.tar.xz` archive with a manifest (sizes, SHA-1). It needs only the
standard library, writes nothing to the iPod and works on a read-only mount.

`restore()` verifies the whole archive before writing anything, checks free space, writes files
under temporary names and renames them (the iTunesDB last). Music files already as archived
are not rewritten; each one it replaces is kept as `<name>.pygpod-orig` until the database is
committed, so a failed restore puts the old files back. After an interrupted restore (power
loss) run it again: it completes and removes the kept files. A backup of another iPod (another
FirewireGuid) is refused unless `force=True`, which keeps this iPod's SysInfo and re-signs the
database for it. On an iPod signed with hash72 (nano 5G and later) the signing parameters come
from its `Device/HashInfo` or, without it, from its current iTunes-signed database; the restore
creates HashInfo from them together with the re-signed database, and refuses before writing
anything when neither exists. `dry_run=True` only plans. Every destination must lie inside the mount point:
a symbolic link on the target (possible on a directory, not on the iPod's FAT) that leads outside
makes `restore()` refuse with `BackupError` before anything is written.

```python
info = pygpod.backup(mountpoint, f"{workdir}/ipod.tar.gz",
                     progress=lambda done, total, path: None)
result = pygpod.restore(f"{workdir}/ipod.tar.gz", mountpoint, dry_run=True)
assert result.dry_run and len(result.written) == len(info.files)
```

<!-- reference:start -->

## Reference

_Generated by `docs/gen_reference.py` from the docstrings._

### `pygpod.backup(mountpoint: 'str', archive_path: 'str', include_media: 'bool' = True, progress: 'Optional[Progress]' = None) -> 'BackupInfo'`

```text
Write a backup of the iPod at ``mountpoint`` to ``archive_path``.

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
```

### `pygpod.restore(archive_path: 'str', mountpoint: 'str', include_media: 'bool' = True, dry_run: 'bool' = False, progress: 'Optional[Progress]' = None, force: 'bool' = False) -> 'RestoreResult'`

```text
Restore a backup archive onto the iPod at ``mountpoint``.

The whole archive is verified first (manifest, sizes, SHA-1); a corrupted archive raises
before anything is written. The target is the same iPod or a freshly initialised one
(``init_ipod``); an iPod with another FirewireGuid is refused unless ``force=True``, which
keeps the target's ``Device`` files and re-signs the database for it (a hash72 iPod's
signing parameters come from its HashInfo or its current iTunes-signed database, which is
replaced: HashInfo is created with the database; without either the restore is refused
before anything is written). Files not in the
archive are left alone, except play count and On-The-Go files (their entries are track
positions of the database being replaced): those are moved to ``<name>.pre-restore``.
The writer lock is held while files are replaced; the database files are committed
together through the same journal as Database.save(). Media files already as archived
(same size and SHA-1) are not rewritten; every media file the restore replaces is kept
as ``<name>.pygpod-orig`` until the database is committed, so a restore that fails puts
the old files back (the iPod stays as before). If the restore is interrupted (power
loss), run it again: it completes and removes the kept files. A target with an
interrupted save is refused. ``include_media=False`` restores the database and artwork only.
``dry_run=True`` verifies and plans but writes nothing.

Raises:
    BackupError: corrupted/unsupported archive, other iPod without force, not enough space,
        a target whose database signature cannot be computed (force), a destination
        outside the mount point (a symbolic link in place of a folder or
        file of the target; checked for every file before anything is written).
```

### `pygpod.BackupInfo`

Fields: `archive, pygpod_version, created, firewire_guid, model, db_id, include_media, files`

```text
What a backup archive contains (returned by backup(), read_manifest(), restore()).
```
| property | type | settable | description |
|---|---|---|---|
| `total_size` | `int` |  | Sum of the archived files' sizes in bytes. |


### `pygpod.RestoreResult`

Fields: `info, written, skipped, bytes_written, resigned, dry_run`

```text
Result of restore(): the archive and the files written (or planned, with dry_run).
```

### `pygpod.CheckReport`

`CheckReport(issues: 'List[Issue]')`

```text
The issues check() found; falsy when there are none.
```
| property | type | settable | description |
|---|---|---|---|
| `ok` | `bool` |  | True when no issue was found. |

#### `CheckReport.by_kind(self, kind: 'str') -> 'List[Issue]'`

```text
The issues of one kind.
```
#### `CheckReport.kinds(self) -> 'Set[str]'`

```text
The kinds of the issues found.
```
#### `CheckReport.summary(self) -> 'str'`

```text
One line: "no issues found" or "N issues found (M fixable)".
```

### `pygpod.Issue`

Fields: `kind, severity, subject, message, fixable, track_id, path, image_id`


### `pygpod.RepairAction`

Fields: `kind, subject, action, done`

<!-- reference:end -->
