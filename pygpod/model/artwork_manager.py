"""Artwork manager - writes cover art to iPod ArtworkDB and .ithmb files.

Handles creating thumbnails in iPod-native pixel formats and maintaining
the ArtworkDB index. Supports all disk-mounted iPod generations by querying
per-generation format tables ported from libgpod.

MHFD header layout (132 bytes, from libgpod db-itunes-parser.h):
  0x00: header_id[4]    "mhfd"
  0x04: header_len      132 (0x84)
  0x08: total_len       total file size
  0x0C: unknown1        0
  0x10: unknown2        2 (required by iTunes 4.9+)
  0x14: num_children    number of MHSD sections (3)
  0x18: unknown3        0
  0x1C: next_id         next available image_id
  0x20: unknown5        0 (int64)
  0x28: unknown6        0 (int64)
  0x30: unknown_flag1   2
  0x31: unknown_flag2   0
  0x32: unknown_flag3   0
  0x33: unknown_flag4   0
  0x34-0x83: zeros
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import shutil
import tempfile
from typing import IO, Dict, List, Optional, Set, Tuple

from ..db.artwork_parser import (
    MHFD_MAGIC,
    MHIF_MAGIC,
    MHII_MAGIC,
    MHLA_MAGIC,
    MHNI_MAGIC,
    MHOD_MAGIC,
    MHSD_MAGIC,
    parse_artworkdb,
)
from ..db.artwork_writer import write_artworkdb
from ..db.parser import Record
from ..db.transaction import Transaction
from ..device.artwork_formats import (
    ArtworkFormatInfo,
    ThumbFormat,
    aligned_width,
    get_cover_art_formats,
    image_data_size,
)
from ..device.models import IpodGeneration
from ..exceptions import ArtworkDBError
from ..model.artwork import PixelFormat, _open_rgb, render_fitted_thumbnail
from ..tags import extract_artwork
from ..utils.compat import put8int, put16lint, put32lint, put64lint

logger = logging.getLogger(__name__)

# Map ThumbFormat to PixelFormat for render_fitted_thumbnail
_THUMB_TO_PIXEL: Dict[ThumbFormat, PixelFormat] = {
    ThumbFormat.RGB565_LE: PixelFormat.RGB565_LE,
    ThumbFormat.RGB565_BE: PixelFormat.RGB565_BE,
    ThumbFormat.RGB555_LE: PixelFormat.RGB555_LE,
    ThumbFormat.RGB555_BE: PixelFormat.RGB555_BE,
    ThumbFormat.UYVY_LE: PixelFormat.UYVY,
    ThumbFormat.UYVY_BE: PixelFormat.UYVY,
    ThumbFormat.I420_LE: PixelFormat.I420,
    ThumbFormat.I420_BE: PixelFormat.I420,
}


class _IthmbFile:
    """Where stage() puts new thumbnails in one .ithmb file."""

    def __init__(self, name: str, path: str, old_size: Optional[int]) -> None:
        self.name = name
        self.path = path
        self.old_size = old_size  # None: the file is new (or written anew), staged whole
        self.end = old_size or 0
        self.holes: Optional[List[int]] = None
        self.block = 0
        self.writes: List[Tuple[int, Record, int, int]] = []  # (offset, mhni, spool pos, size)


class ArtworkManager:
    """Manages artwork on an iPod mountpoint.

    Creates thumbnails in all formats the iPod expects: ``formats`` (in write order, e.g. the
    device's SysInfoExtended AlbumArt list) when given, else the generation's table from
    libgpod.
    """

    def __init__(
        self,
        mountpoint: str,
        generation: IpodGeneration = IpodGeneration.UNKNOWN,
        formats: Optional[List[ArtworkFormatInfo]] = None,
    ) -> None:
        self._mountpoint = mountpoint
        self._generation = generation
        self._artwork_dir = os.path.join(mountpoint, "iPod_Control", "Artwork")
        self._artworkdb_path = os.path.join(self._artwork_dir, "ArtworkDB")
        self._root: Optional[Record] = None
        self._next_image_id = 1
        self._formats: Optional[List[ArtworkFormatInfo]] = list(formats) if formats else None
        # Thumbnails added since the last save: (mhni, spool offset, size). The bytes wait in
        # a spool (memory, a temporary file for big batches) and get their .ithmb slot only
        # in stage(), i.e. after the caller's conflict check and under its write lock, so
        # adding a cover changes no file on the iPod.
        self._pending: List[Tuple[Record, int, int]] = []
        self._spool: Optional[IO[bytes]] = None
        # Slots the ArtworkDB on disk references, per .ithmb file (lower-case name): what a
        # save must not overwrite before its commit. And the hash of that ArtworkDB, to
        # notice when another writer replaced it meanwhile.
        self._disk_slots: Dict[str, Set[Tuple[int, int]]] = {}
        self._disk_sha: Optional[str] = None
        self._fresh = False  # start_empty(): the next save writes every .ithmb file anew
        self._needs_rebuild = False
        # Set when the ArtworkDB exists but cannot be read: nothing is added to or written
        # over it (that would drop every cover it holds) until start_empty() is called.
        self.load_error: Optional[ArtworkDBError] = None
        # No ArtworkDB on disk: image ids start at 1 again, so the caller clears the tracks'
        # references to images of a lost ArtworkDB (they would show the new covers).
        self.created_new = not os.path.exists(self._artworkdb_path)
        self._load()

    @property
    def formats(self) -> List[ArtworkFormatInfo]:
        """Get artwork formats for this device."""
        if self._formats is None:
            self._formats = get_cover_art_formats(self._generation) or []
            if not self._formats:
                logger.warning(
                    "No artwork formats for generation %s, falling back to iPod Classic formats",
                    self._generation,
                )
                from ..device.artwork_formats import _CLASSIC_COVER_ART

                self._formats = _CLASSIC_COVER_ART
        return self._formats

    def reset(self) -> None:
        """Delete all artwork files and start fresh (immediately, not with a save)."""
        if os.path.isdir(self._artwork_dir):
            for fname in os.listdir(self._artwork_dir):
                fpath = os.path.join(self._artwork_dir, fname)
                if fname == "ArtworkDB" or fname.endswith(".ithmb"):
                    os.unlink(fpath)
                    logger.debug("Deleted %s", fpath)
        self._root = None
        self._next_image_id = 1
        self._drop_pending()
        self._disk_slots = {}
        self._disk_sha = None
        self._fresh = False
        self.load_error = None

    def start_empty(self) -> None:
        """Replace the ArtworkDB by an empty one with the next save (also an unreadable one).

        Every image is dropped; the .ithmb files are written anew and the ones no new image
        uses are deleted when the save commits. The caller clears the tracks' artwork
        references.
        """
        self.load_error = None
        self._root = self._create_empty_artworkdb()
        self._drop_pending()
        self._fresh = True
        self._needs_rebuild = True

    def require_readable(self) -> None:
        """Raise ArtworkDBError when the ArtworkDB on disk could not be read."""
        if self.load_error is not None:
            raise self.load_error

    def _load(self) -> None:
        """Load the existing ArtworkDB. Only a missing file means "create a new one": an
        unreadable one sets ``load_error`` (see require_readable())."""
        if not os.path.exists(self._artworkdb_path):
            return
        try:
            with open(self._artworkdb_path, "rb") as f:
                data = f.read()
            self._root = parse_artworkdb(data)
        except Exception as e:  # noqa: BLE001 - any parse or read error: keep the file
            logger.warning("ArtworkDB %s cannot be read: %s", self._artworkdb_path, e)
            logger.debug("ArtworkDB read error", exc_info=True)
            self._root = None
            self.load_error = ArtworkDBError(
                f"{self._artworkdb_path} cannot be read ({e}); covers cannot be changed "
                "until Database.reset_artwork() replaces it with an empty one"
            )
            return
        self._disk_sha = hashlib.sha256(data).hexdigest()
        self._disk_slots = self._slots(self._root)
        for mhii in self._images(self._root):
            iid = mhii.fields.get("image_id", 0)
            if iid >= self._next_image_id:
                self._next_image_id = iid + 1
        logger.debug("Loaded ArtworkDB, next_image_id=%d", self._next_image_id)

    def add_artwork(self, dbid: int, filepath: str) -> Optional[int]:
        """Extract artwork from audio file and add to ArtworkDB.

        Returns:
            image_id if artwork was added, None if no artwork found.
        """
        art_data = extract_artwork(filepath)
        if not art_data:
            logger.debug("No embedded artwork in %s", filepath)
            return None
        return self.add_artwork_data(dbid, art_data)

    def add_artwork_data(self, dbid: int, art_data: bytes, save: bool = True) -> Optional[int]:
        """Add raw image data as artwork for a track.

        The thumbnails are rendered now and written to the .ithmb files by the next
        save() / stage(); until then no file on the iPod changes.

        Args:
            dbid: Track database ID.
            art_data: Raw image bytes (JPEG/PNG).
            save: If True, write ArtworkDB to disk after adding. Set to False
                  when batch-adding artwork and call save() manually at the end.

        Returns:
            image_id if successful, None on failure.

        Raises:
            ArtworkDBError: the ArtworkDB on disk cannot be read (nothing is changed).
        """
        self.require_readable()
        try:
            from PIL import Image  # type: ignore  # noqa: F401
        except ImportError:
            logger.warning("Pillow not installed, cannot create artwork thumbnails")
            return None

        # Decode and render every format first: an invalid image raises (or yields None)
        # before anything in the ArtworkDB changed.
        rendered = self._render(art_data)
        if not rendered:
            return None

        if self._root is None:
            self._root = self._create_empty_artworkdb()

        image_id = self._next_image_id
        self._next_image_id += 1

        mhni_records = []
        for fmt, thumb_data, geometry in rendered:
            mhni = self._create_mhni(fmt.format_id, 0, len(thumb_data), *geometry)
            self._pending.append((mhni, self._spool_add(thumb_data), len(thumb_data)))
            mhni_records.append(mhni)

        # Create MHII record
        mhii = self._create_mhii(image_id, dbid, mhni_records, source_size=len(art_data))

        # Add to MHLI in MHSD type 1
        mhli = self._get_or_create_mhli()
        mhli.children.append(mhii)

        # Update MHLF format entries
        self._update_mhlf()

        # Update MHFD next_id
        self._update_mhfd_next_id()

        # Save ArtworkDB (can be deferred for batch operations)
        if save:
            self._save()

        logger.info(
            "Added artwork id=%d for dbid=%d (%d formats)",
            image_id,
            dbid,
            len(mhni_records),
        )
        return image_id

    def _render(self, art_data: bytes) -> List[Tuple[ArtworkFormatInfo, bytes, Tuple[int, ...]]]:
        """(format, thumbnail bytes padded to the format's block, geometry) per format.

        Raises what Pillow raises for data that is not an image; changes no state.
        """
        out: List[Tuple[ArtworkFormatInfo, bytes, Tuple[int, ...]]] = []
        source = None
        for fmt in self.formats:
            pixel_fmt = _THUMB_TO_PIXEL.get(fmt.pixel_format)
            if pixel_fmt is None:
                continue
            # scaled to fit and centred in the slot like libgpod, or cropped to fill it when
            # the format says so
            if source is None:
                source = _open_rgb(art_data)  # decoded once for all formats
            fitted = render_fitted_thumbnail(
                source,
                fmt.width,
                fmt.height,
                pixel_fmt,
                crop=fmt.crop,
                back_color=fmt.back_color,
                row_width=aligned_width(fmt),
            )
            if fitted is None or not fitted.data:
                continue
            thumb_data = fitted.data
            block_size = image_data_size(fmt)
            if len(thumb_data) < block_size:
                thumb_data = thumb_data + b"\x00" * (block_size - len(thumb_data))
            geometry = (
                fitted.width,
                fitted.height,
                fitted.horizontal_padding,
                fitted.vertical_padding,
            )
            out.append((fmt, thumb_data, geometry))
        return out

    # Pending thumbnails stay in memory up to this size, beyond it in a temporary file.
    SPOOL_MEMORY = 16 * 1024 * 1024

    def _spool_add(self, data: bytes) -> int:
        if self._spool is None:
            self._spool = tempfile.SpooledTemporaryFile(max_size=self.SPOOL_MEMORY)
        self._spool.seek(0, os.SEEK_END)
        pos = self._spool.tell()
        self._spool.write(data)
        return pos

    def _spool_read(self, pos: int, size: int) -> bytes:
        assert self._spool is not None
        self._spool.seek(pos)
        return self._spool.read(size)

    def _drop_pending(self) -> None:
        self._pending = []
        if self._spool is not None:
            self._spool.close()
            self._spool = None

    def remove_artwork(self, image_id: int) -> bool:
        """Remove artwork entry by image_id.

        Returns True if an entry was found and removed. Its thumbnail slots become
        reusable after the next save(); the .ithmb files are compacted only when
        most of them is unused and the disk has room for it (see stage()). False
        without a change when the ArtworkDB cannot be read.
        """
        if self._root is None:
            return False
        mhli = self._get_or_create_mhli()
        dropped = set()
        for mhii in mhli.children:
            if mhii.fields.get("image_id") == image_id:
                dropped.update(id(mhni) for mhni in self._mhnis(mhii))
        before = len(mhli.children)
        mhli.children = [c for c in mhli.children if c.fields.get("image_id") != image_id]
        removed = len(mhli.children) < before
        if removed:
            # thumbnails not saved yet are simply forgotten
            self._pending = [p for p in self._pending if id(p[0]) not in dropped]
            self._needs_rebuild = True
            logger.debug("Removed artwork image_id=%d", image_id)
        return removed

    def save(self) -> None:
        """Write ArtworkDB to disk, compacting .ithmb files if needed (crash-safe)."""
        if self._root is None:
            return
        tx = Transaction(self._mountpoint)
        try:
            self.stage(tx)
            tx.commit()
        except BaseException:
            tx.abort()
            raise

    def stage(self, tx: Transaction) -> None:
        """Stage the ArtworkDB, new thumbnails and compacted .ithmb files into a transaction.

        Runs after the caller's conflict check, under its write lock. New thumbnails go into
        slots the ArtworkDB on disk does not reference - free slots of an .ithmb file or
        beyond its end - written in place and flushed to disk before the commit point: the
        committed ArtworkDB never points there, so its state stays valid until the new one
        is committed, and a big .ithmb file is not copied for one cover. A new .ithmb file
        and compacted files are staged whole. If the transaction aborts, the new
        thumbnails stay pending (a later save places them again) and data appended beyond
        the old end of a file is cut off again.

        Raises:
            ArtworkDBError: the ArtworkDB on disk was replaced since it was loaded and
                cannot be read (nothing is written over it).
        """
        if self.load_error is not None or self._root is None:
            return
        disk_slots = self._current_disk_slots()
        self._place_pending(tx, disk_slots)
        if self._needs_rebuild:
            by_file = self._by_file()
            keys = [] if self._fresh else self._files_to_compact(by_file)
            if keys:
                self._rebuild_ithmb(tx, keys, by_file)
            self._stage_unused_file_deletions(tx, by_file)
        data = write_artworkdb(self._root)
        tx.stage_bytes(self._artworkdb_path, data)
        tx.on_commit(lambda: self._after_commit(data))

    def _after_commit(self, data: bytes) -> None:
        self._drop_pending()
        self._needs_rebuild = False
        self._fresh = False
        self._disk_sha = hashlib.sha256(data).hexdigest()
        self._disk_slots = self._slots(self._root)

    def _current_disk_slots(self) -> Dict[str, Set[Tuple[int, int]]]:
        """Slots the ArtworkDB on disk references now (re-read only when it changed)."""
        if self._fresh:
            return {}  # every .ithmb file is written anew
        try:
            with open(self._artworkdb_path, "rb") as f:
                data: Optional[bytes] = f.read()
        except FileNotFoundError:
            data = None
        sha = hashlib.sha256(data).hexdigest() if data is not None else None
        if sha == self._disk_sha:
            return self._disk_slots
        if data is None:
            return {}
        try:
            return self._slots(parse_artworkdb(data))
        except Exception as e:  # noqa: BLE001 - replaced by something unreadable
            raise ArtworkDBError(
                f"{self._artworkdb_path} was replaced since it was loaded and cannot be read "
                f"({e}); nothing was written"
            ) from e

    # Like libgpod, a format's thumbnails go to F<format>_1.ithmb until it reaches this size,
    # then to F<format>_2.ithmb and so on.
    ITHMB_MAX_SIZE = 256 * 1000 * 1000

    def _place_pending(self, tx: Transaction, disk_slots: Dict[str, Set[Tuple[int, int]]]) -> None:
        """Give every pending thumbnail its .ithmb slot and write it (see stage()).

        Free slots of any existing file of the format come first, then the end of the
        first file below ITHMB_MAX_SIZE (a new file when all are full), like libgpod.
        """
        if not self._pending:
            return
        os.makedirs(self._artwork_dir, exist_ok=True)
        pending_ids = {id(mhni) for mhni, _, _ in self._pending}
        used = self._slots(self._root, skip=pending_ids)
        for name, slots in disk_slots.items():
            used.setdefault(name, set()).update(slots)
        by_format: Dict[int, List[Tuple[Record, int, int]]] = {}
        for item in self._pending:
            by_format.setdefault(item[0].fields.get("format_id", 0), []).append(item)
        for fmt_id, items in by_format.items():
            files: Dict[int, _IthmbFile] = {}

            def file(n: int) -> _IthmbFile:
                if n not in files:
                    name = f"F{fmt_id}_{n}.ithmb"
                    path = self._ithmb_path(name)
                    exists = not self._fresh and os.path.isfile(path)
                    files[n] = _IthmbFile(name, path, os.path.getsize(path) if exists else None)
                return files[n]

            existing = [] if self._fresh else self._file_indices(fmt_id)
            for mhni, pos, size in items:
                target: Optional[_IthmbFile] = None
                for n in existing:
                    f = file(n)
                    if f.holes is None:
                        f.block = size
                        f.holes = self._free_slots(
                            used.get(f.name.lower(), set()), f.old_size or 0, size
                        )
                    if f.holes and f.block == size:
                        target, offset = f, f.holes.pop(0)
                        break
                if target is None:
                    n = 1
                    while file(n).end >= self.ITHMB_MAX_SIZE:
                        n += 1
                    target, offset = file(n), file(n).end
                    target.end += size
                target.writes.append((offset, mhni, pos, size))
            for f in files.values():
                self._write_placed(tx, f)

    def _write_placed(self, tx: Transaction, f: _IthmbFile) -> None:
        if not f.writes:
            return
        f.writes.sort(key=lambda w: w[0])
        for offset, mhni, _, _ in f.writes:
            self._set_slot(mhni, f.name, offset)
        if f.old_size is None:  # a new file (or one written anew): staged whole
            tx.stage_bytes(f.path, b"".join(self._spool_read(p, s) for _, _, p, s in f.writes))
            return
        tx.on_abort(functools.partial(self._truncate, f.path, f.old_size))
        with open(f.path, "r+b") as fh:
            for offset, _, pos, size in f.writes:
                fh.seek(offset)
                fh.write(self._spool_read(pos, size))
            fh.flush()
            os.fsync(fh.fileno())

    def _file_indices(self, fmt_id: int) -> List[int]:
        """Indices n of the existing F<fmt_id>_<n>.ithmb files, ascending."""
        out = []
        prefix = f"f{fmt_id}_"
        if os.path.isdir(self._artwork_dir):
            for name in os.listdir(self._artwork_dir):
                low = name.lower()
                if low.startswith(prefix) and low.endswith(".ithmb"):
                    index = low[len(prefix) : -len(".ithmb")]
                    if index.isdigit() and os.path.isfile(os.path.join(self._artwork_dir, name)):
                        out.append(int(index))
        return sorted(out)

    @staticmethod
    def _free_slots(used: Set[Tuple[int, int]], end: int, block: int) -> List[int]:
        """Unreferenced ``block``-sized slots below ``end`` (fixed-size, aligned files only)."""
        if any(size != block or offset % block for offset, size in used):
            return []  # mixed sizes or unaligned: no reuse in this file
        taken = {offset for offset, _ in used}
        return [o for o in range(0, end - block + 1, block) if o not in taken]

    @staticmethod
    def _truncate(path: str, size: int) -> None:
        try:
            if os.path.getsize(path) > size:
                os.truncate(path, size)
        except OSError:
            logger.debug("could not cut %s back to %d bytes", path, size, exc_info=True)

    def _ithmb_path(self, name: str) -> str:
        """Path of the .ithmb file ``name`` (an existing one matched case-insensitively,
        like FAT and HFS+ do)."""
        path = os.path.join(self._artwork_dir, name)
        if not os.path.exists(path) and os.path.isdir(self._artwork_dir):
            want = name.lower()
            for n in os.listdir(self._artwork_dir):
                if n.lower() == want:
                    return os.path.join(self._artwork_dir, n)
        return path

    def _set_slot(self, mhni: Record, name: str, offset: int) -> None:
        header = bytearray(mhni.raw_header)
        put32lint(header, 0x14, offset)
        mhni.raw_header = bytes(header)
        mhni.fields["ithumb_offset"] = offset
        if self._mhni_file(mhni).lower() != name.lower():
            mhni.children = [c for c in mhni.children if c.fields.get("mhod_type") != 3]
            mhni.children.insert(0, self._create_filename_mhod(f":{name}"))

    @staticmethod
    def _mhni_file(mhni: Record) -> str:
        """The .ithmb file name of a thumbnail, from its MHOD type 3 (":F1060_2.ithmb")."""
        for child in mhni.children:
            if child.fields.get("mhod_type") == 3 and child.fields.get("string"):
                return child.fields["string"].lstrip(":").split(":")[-1]
        return f"F{mhni.fields.get('format_id')}_1.ithmb"

    @staticmethod
    def _images(root: Optional[Record]) -> List[Record]:
        """The MHII records of an ArtworkDB root."""
        for mhsd in root.children if root is not None else ():
            if mhsd.fields.get("mhsd_type") == 1 and mhsd.children:
                return list(mhsd.children[0].children)
        return []

    @classmethod
    def _slots(
        cls, root: Optional[Record], skip: Optional[Set[int]] = None
    ) -> Dict[str, Set[Tuple[int, int]]]:
        """(offset, size) of every thumbnail of ``root`` by lower-case .ithmb file name;
        MHNIs whose id() is in ``skip`` are left out."""
        out: Dict[str, Set[Tuple[int, int]]] = {}
        for mhii in cls._images(root):
            for mhni in cls._mhnis(mhii):
                size = mhni.fields.get("image_size", 0)
                if size <= 0 or (skip and id(mhni) in skip):
                    continue
                name = cls._mhni_file(mhni).lower()
                out.setdefault(name, set()).add((mhni.fields.get("ithumb_offset", 0), size))
        return out

    # Compact a file only when at least this share of it is unused...
    COMPACT_WASTE_RATIO = 0.5
    # ...and the disk has room for a copy of the used part plus this margin.
    COMPACT_SPACE_MARGIN = 1024 * 1024

    def _free_space(self) -> int:
        return shutil.disk_usage(self._artwork_dir).free

    def _files_to_compact(self, by_file: Dict[str, List[Tuple[Record, int, int]]]) -> List[str]:
        """The .ithmb files (lower-case names) worth compacting; the others stay untouched."""
        out: List[str] = []
        live_total = 0
        for key, entries in by_file.items():
            path = self._ithmb_path(self._mhni_file(entries[0][0]))
            if not os.path.isfile(path):
                continue
            total = os.path.getsize(path)
            live = sum(size for _, size in {(o, s) for _, o, s in entries})
            waste = total - live
            if waste <= 0 or waste < self.COMPACT_WASTE_RATIO * total:
                continue
            out.append(key)
            live_total += live
        if out and self._free_space() < live_total + self.COMPACT_SPACE_MARGIN:
            logger.info("Not compacting .ithmb files: not enough free space for the copy")
            return []
        return out

    @staticmethod
    def _mhnis(mhii: Record) -> List[Record]:
        out: List[Record] = []
        for child in mhii.children:
            # Support both bare MHNI and MHOD-wrapped MHNI (parser accepts both)
            if child.magic == MHNI_MAGIC:
                out.append(child)
            elif child.magic == MHOD_MAGIC:
                out += [c for c in child.children if c.magic == MHNI_MAGIC]
        return out

    def _by_file(self) -> Dict[str, List[Tuple[Record, int, int]]]:
        """(mhni, offset, size) of every referenced thumbnail, by lower-case .ithmb file name
        (a format's thumbnails may be spread over F<format>_1.ithmb, _2 ...)."""
        by_file: Dict[str, List[Tuple[Record, int, int]]] = {}
        for mhii in self._images(self._root):
            for mhni in self._mhnis(mhii):
                size = mhni.fields.get("image_size", 0)
                if size > 0:
                    offset = mhni.fields.get("ithumb_offset", 0)
                    key = self._mhni_file(mhni).lower()
                    by_file.setdefault(key, []).append((mhni, offset, size))
        return by_file

    def _stage_unused_file_deletions(self, tx: Transaction, by_file: Dict[str, list]) -> None:
        """Remove the .ithmb files no thumbnail references any more (when the save commits)."""
        if not os.path.isdir(self._artwork_dir):
            return
        for fname in os.listdir(self._artwork_dir):
            low = fname.lower()
            if low.startswith("f") and low.endswith(".ithmb") and low not in by_file:
                tx.delete_on_commit(os.path.join(self._artwork_dir, fname))

    def _rebuild_ithmb(
        self, tx: Transaction, keys: List[str], by_file: Dict[str, List[Tuple[Record, int, int]]]
    ) -> None:
        """Rewrite the .ithmb files ``keys`` keeping only the referenced thumbnails.

        Each file keeps its name; the MHNI offsets are updated so the ArtworkDB stays
        consistent. The new files are staged in ``tx`` and the offsets are restored if it
        aborts. Thumbnails sharing one slot keep sharing it.
        """
        old_state = [
            (mhni, mhni.raw_header, mhni.fields.get("ithumb_offset", 0))
            for key in keys
            for mhni, _, _ in by_file[key]
        ]

        def restore() -> None:
            for mhni, raw, off in old_state:
                mhni.raw_header = raw
                mhni.fields["ithumb_offset"] = off

        tx.on_abort(restore)
        for key in keys:
            entries = sorted(by_file[key], key=lambda e: e[1])
            path = self._ithmb_path(self._mhni_file(entries[0][0]))
            moved: Dict[Tuple[int, int], int] = {}
            chunks = []
            new_offset = 0
            with open(path, "rb") as f:
                for mhni, old_offset, size in entries:
                    if (old_offset, size) not in moved:
                        f.seek(old_offset)
                        chunk = f.read(size)
                        chunks.append(chunk + b"\x00" * (size - len(chunk)))
                        moved[(old_offset, size)] = new_offset
                        new_offset += size
                    header = bytearray(mhni.raw_header)
                    put32lint(header, 0x14, moved[(old_offset, size)])
                    mhni.raw_header = bytes(header)
                    mhni.fields["ithumb_offset"] = moved[(old_offset, size)]
            tx.stage_bytes(path, b"".join(chunks))
        logger.debug("Compacted %d .ithmb files", len(keys))

    # Keep private alias for backwards compatibility within this class
    _save = save

    # ========================================================================
    # MHFD (ArtworkDB root)
    # ========================================================================
    def _create_empty_artworkdb(self) -> Record:
        """Create an empty ArtworkDB matching libgpod's output."""
        mhfd_hdr_len = 132  # 0x84
        header = bytearray(mhfd_hdr_len)
        header[0:4] = MHFD_MAGIC
        put32lint(header, 0x04, mhfd_hdr_len)
        # 0x08: total_len - updated by writer
        # 0x0C: unknown1 = 0 (libgpod leaves this 0)
        put32lint(header, 0x10, 2)  # unknown2 = 2 (required by iTunes 4.9+)
        # 0x14: num_children - updated by writer
        # 0x18: unknown3 = 0
        put32lint(header, 0x1C, self._next_image_id)  # next_id
        # 0x20-0x2F: zeros
        put8int(header, 0x30, 2)  # unknown_flag1 = 2

        root = Record(MHFD_MAGIC, mhfd_hdr_len, 0)
        root.raw_header = bytes(header)

        # MHSD type 1 (image list)
        mhsd1 = self._create_mhsd(1)
        mhli = self._create_mhli()
        mhsd1.children.append(mhli)
        root.children.append(mhsd1)

        # MHSD type 2 (album list)
        mhsd2 = self._create_mhsd(2)
        mhla = self._create_mhla()
        mhsd2.children.append(mhla)
        root.children.append(mhsd2)

        # MHSD type 3 (file list)
        mhsd3 = self._create_mhsd(3)
        mhlf = self._create_mhlf()
        mhsd3.children.append(mhlf)
        root.children.append(mhsd3)

        return root

    def _update_mhfd_next_id(self) -> None:
        """Update the next_id field in MHFD header."""
        if self._root is None:
            return
        header = bytearray(self._root.raw_header)
        if len(header) > 0x20:
            put32lint(header, 0x1C, self._next_image_id)
            self._root.raw_header = bytes(header)

    # ========================================================================
    # Section records
    # ========================================================================
    def _create_mhsd(self, mhsd_type: int) -> Record:
        hdr_len = 96
        header = bytearray(hdr_len)
        header[0:4] = MHSD_MAGIC
        put32lint(header, 4, hdr_len)
        put32lint(header, 0x0C, mhsd_type)
        rec = Record(MHSD_MAGIC, hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["mhsd_type"] = mhsd_type
        return rec

    def _create_mhli(self) -> Record:
        hdr_len = 92
        header = bytearray(hdr_len)
        header[0:4] = b"mhli"
        put32lint(header, 4, hdr_len)
        rec = Record(b"mhli", hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header)
        return rec

    def _create_mhla(self) -> Record:
        hdr_len = 92
        header = bytearray(hdr_len)
        header[0:4] = MHLA_MAGIC
        put32lint(header, 4, hdr_len)
        rec = Record(MHLA_MAGIC, hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header)
        return rec

    def _create_mhlf(self) -> Record:
        hdr_len = 92
        header = bytearray(hdr_len)
        header[0:4] = b"mhlf"
        put32lint(header, 4, hdr_len)
        rec = Record(b"mhlf", hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header)
        return rec

    # ========================================================================
    # Image records (MHII, MHNI, MHOD, MHIF)
    # ========================================================================
    def _create_mhii(
        self,
        image_id: int,
        song_id: int,
        mhni_list: List[Record],
        source_size: int = 0,
    ) -> Record:
        """Create MHII with MHOD type=2 wrappers around MHNI children."""
        hdr_len = 152
        header = bytearray(hdr_len)
        header[0:4] = MHII_MAGIC
        put32lint(header, 4, hdr_len)
        # total_len at 8 - updated by writer
        put32lint(header, 0x0C, len(mhni_list))  # num_children
        put32lint(header, 0x10, image_id)
        put64lint(header, 0x14, song_id)
        if source_size:
            put32lint(header, 0x30, source_size)

        wrapped = [self._create_mhod_wrapper(mhni) for mhni in mhni_list]

        rec = Record(MHII_MAGIC, hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["image_id"] = image_id
        rec.fields["song_id"] = song_id
        rec.fields["num_children"] = len(wrapped)
        rec.children = wrapped
        return rec

    def _create_mhod_wrapper(self, mhni: Record) -> Record:
        """Create MHOD type=2 container wrapping an MHNI."""
        hdr_len = 24
        header = bytearray(hdr_len)
        header[0:4] = MHOD_MAGIC
        put32lint(header, 4, hdr_len)
        # total_len at 8 - updated by writer
        put16lint(header, 0x0C, 2)  # mhod_type = 2

        rec = Record(MHOD_MAGIC, hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["mhod_type"] = 2
        rec.children.append(mhni)
        return rec

    def _create_mhni(
        self,
        format_id: int,
        offset: int,
        size: int,
        width: int,
        height: int,
        horizontal_padding: int = 0,
        vertical_padding: int = 0,
    ) -> Record:
        """Create MHNI with MHOD type=3 filename child.

        ``width`` / ``height`` as libgpod stores them: the scaled image plus the left/top padding.
        """
        hdr_len = 76
        header = bytearray(hdr_len)
        header[0:4] = MHNI_MAGIC
        put32lint(header, 4, hdr_len)
        # total_len at 8 - updated by writer
        put32lint(header, 0x0C, 1)  # num_children (1 MHOD filename)
        put32lint(header, 0x10, format_id)
        put32lint(header, 0x14, offset)  # ithumb_offset
        put32lint(header, 0x18, size)  # image_size
        put16lint(header, 0x1C, vertical_padding)
        put16lint(header, 0x1E, horizontal_padding)
        put16lint(header, 0x20, height)
        put16lint(header, 0x22, width)

        rec = Record(MHNI_MAGIC, hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["format_id"] = format_id
        rec.fields["ithumb_offset"] = offset
        rec.fields["image_size"] = size
        rec.fields["vertical_padding"] = vertical_padding
        rec.fields["horizontal_padding"] = horizontal_padding
        rec.fields["image_width"] = width
        rec.fields["image_height"] = height

        filename = f":F{format_id}_1.ithmb"
        mhod = self._create_filename_mhod(filename)
        rec.children.append(mhod)
        return rec

    def _create_filename_mhod(self, filename: str) -> Record:
        """Create MHOD type=3 with UTF-16LE filename, padded to 4 bytes like libgpod."""
        name_bytes = filename.encode("utf-16-le")
        hdr_len = 24
        pad = (4 - (hdr_len + 12 + len(name_bytes)) % 4) % 4
        body = bytearray(12 + len(name_bytes) + pad)
        put32lint(body, 0, len(name_bytes))  # string length
        put32lint(body, 4, 2)  # encoding = 2 (UTF-16LE)
        # body[8:12] = 0 (unknown)
        body[12 : 12 + len(name_bytes)] = name_bytes
        total_len = hdr_len + len(body)

        header = bytearray(hdr_len)
        header[0:4] = MHOD_MAGIC
        put32lint(header, 4, hdr_len)
        put32lint(header, 8, total_len)
        put16lint(header, 0x0C, 3)  # mhod_type = 3
        header[0x0F] = pad  # padding_len

        rec = Record(MHOD_MAGIC, hdr_len, total_len)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header) + bytes(body)
        rec.fields["mhod_type"] = 3
        rec.fields["string"] = filename
        return rec

    def _create_mhif(self, format_id: int, img_size: int) -> Record:
        """Create MHIF (image file info) record."""
        hdr_len = 124
        header = bytearray(hdr_len)
        header[0:4] = MHIF_MAGIC
        put32lint(header, 4, hdr_len)
        put32lint(header, 8, hdr_len)  # total_len = header_len (no children)
        # 0x0C: corr_id = 0
        put32lint(header, 0x10, format_id)
        put32lint(header, 0x14, img_size)

        rec = Record(MHIF_MAGIC, hdr_len, hdr_len)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header)
        rec.fields["format_id"] = format_id
        rec.fields["image_size"] = img_size
        return rec

    # ========================================================================
    # Helpers
    # ========================================================================
    def _get_or_create_mhli(self) -> Record:
        """Get the MHLI record from MHSD type 1, creating if needed."""
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 1 and mhsd.children:
                return mhsd.children[0]
        mhsd = self._create_mhsd(1)
        mhli = self._create_mhli()
        mhsd.children.append(mhli)
        self._root.children.insert(0, mhsd)
        return mhli

    def _update_mhlf(self) -> None:
        """Update MHLF with format entries for all active formats."""
        mhlf = None
        for mhsd in self._root.children:
            if mhsd.fields.get("mhsd_type") == 3 and mhsd.children:
                mhlf = mhsd.children[0]
                break

        if mhlf is None:
            mhsd = self._create_mhsd(3)
            mhlf = self._create_mhlf()
            mhsd.children.append(mhlf)
            self._root.children.append(mhsd)

        # libgpod's mhif image_size: width * height * 2 of the format (without row
        # alignment), also when older thumbnails of another size are still in the database
        wanted = [(f.format_id, f.padding or f.width * f.height * 2) for f in self.formats]
        existing = {c.fields.get("format_id"): c for c in mhlf.children}
        if any(
            fid not in existing or existing[fid].fields.get("image_size") != size
            for fid, size in wanted
        ):
            ids = {fid for fid, _ in wanted}
            children = [
                existing[fid]
                if fid in existing and existing[fid].fields.get("image_size") == size
                else self._create_mhif(fid, size)
                for fid, size in wanted
            ]
            mhlf.children = children + [
                c for c in mhlf.children if c.fields.get("format_id") not in ids
            ]

        # Update count in header
        header = bytearray(mhlf.raw_header)
        put32lint(header, 8, len(mhlf.children))
        mhlf.raw_header = bytes(header)
