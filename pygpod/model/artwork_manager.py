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

import logging
import os
from typing import Dict, List, Optional

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
from ..device.artwork_formats import (
    ArtworkFormatInfo,
    ThumbFormat,
    get_cover_art_formats,
    image_data_size,
)
from ..device.models import IpodGeneration
from ..model.artwork import PixelFormat, create_thumbnail
from ..tags import extract_artwork
from ..utils.compat import put8int, put16lint, put32lint, put64lint

logger = logging.getLogger(__name__)

# Map ThumbFormat to PixelFormat for create_thumbnail
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


class ArtworkManager:
    """Manages artwork on an iPod mountpoint.

    Uses per-generation format tables from libgpod to create thumbnails
    in all formats the iPod expects.
    """

    def __init__(
        self,
        mountpoint: str,
        generation: IpodGeneration = IpodGeneration.UNKNOWN,
    ) -> None:
        self._mountpoint = mountpoint
        self._generation = generation
        self._artwork_dir = os.path.join(mountpoint, "iPod_Control", "Artwork")
        self._artworkdb_path = os.path.join(self._artwork_dir, "ArtworkDB")
        self._root: Optional[Record] = None
        self._next_image_id = 1
        self._ithmb_offsets: Dict[int, int] = {}  # format_id -> current write offset
        self._formats: Optional[List[ArtworkFormatInfo]] = None
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
        """Delete all artwork files and start fresh."""
        if os.path.isdir(self._artwork_dir):
            for fname in os.listdir(self._artwork_dir):
                fpath = os.path.join(self._artwork_dir, fname)
                if fname == "ArtworkDB" or fname.endswith(".ithmb"):
                    os.unlink(fpath)
                    logger.debug("Deleted %s", fpath)
        self._root = None
        self._next_image_id = 1
        self._ithmb_offsets.clear()

    def _load(self) -> None:
        """Load existing ArtworkDB if present."""
        if os.path.isfile(self._artworkdb_path):
            try:
                with open(self._artworkdb_path, "rb") as f:
                    data = f.read()
                self._root = parse_artworkdb(data)
                # Find max image_id
                for mhsd in self._root.children:
                    if mhsd.fields.get("mhsd_type") == 1 and mhsd.children:
                        mhli = mhsd.children[0]
                        for mhii in mhli.children:
                            iid = mhii.fields.get("image_id", 0)
                            if iid >= self._next_image_id:
                                self._next_image_id = iid + 1
                logger.debug("Loaded ArtworkDB, next_image_id=%d", self._next_image_id)
            except Exception:
                logger.debug("Failed to load ArtworkDB, creating new", exc_info=True)
                self._root = None

        # Scan existing .ithmb files for current sizes
        if os.path.isdir(self._artwork_dir):
            for fname in os.listdir(self._artwork_dir):
                if fname.startswith("F") and fname.endswith(".ithmb"):
                    parts = fname[1:].replace(".ithmb", "").split("_")
                    if len(parts) == 2:
                        try:
                            fmt_id = int(parts[0])
                            fpath = os.path.join(self._artwork_dir, fname)
                            size = os.path.getsize(fpath)
                            self._ithmb_offsets[fmt_id] = max(
                                self._ithmb_offsets.get(fmt_id, 0), size
                            )
                        except ValueError:
                            pass

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

        Args:
            dbid: Track database ID.
            art_data: Raw image bytes (JPEG/PNG).
            save: If True, write ArtworkDB to disk after adding. Set to False
                  when batch-adding artwork and call save() manually at the end.

        Returns:
            image_id if successful, None on failure.
        """
        try:
            from PIL import Image  # type: ignore  # noqa: F401
        except ImportError:
            logger.warning("Pillow not installed, cannot create artwork thumbnails")
            return None

        os.makedirs(self._artwork_dir, exist_ok=True)

        if self._root is None:
            self._root = self._create_empty_artworkdb()

        image_id = self._next_image_id
        self._next_image_id += 1

        # Save art_data to temp file for create_thumbnail
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(art_data)
            tmp_path = tmp.name

        try:
            mhni_records = []

            for fmt in self.formats:
                thumb_data = create_thumbnail(tmp_path, fmt.format_id, fmt.width, fmt.height)
                if thumb_data is None:
                    continue

                # Pad to block_size
                block_size = image_data_size(fmt)
                if len(thumb_data) < block_size:
                    thumb_data = thumb_data + b"\x00" * (block_size - len(thumb_data))

                # Write to .ithmb file
                ithmb_path = os.path.join(self._artwork_dir, f"F{fmt.format_id}_1.ithmb")
                offset = self._ithmb_offsets.get(fmt.format_id, 0)

                if os.path.isfile(ithmb_path):
                    with open(ithmb_path, "r+b") as f:
                        f.seek(offset)
                        f.write(thumb_data)
                else:
                    with open(ithmb_path, "wb") as f:
                        if offset > 0:
                            f.write(b"\x00" * offset)
                        f.write(thumb_data)

                self._ithmb_offsets[fmt.format_id] = offset + len(thumb_data)

                mhni = self._create_mhni(
                    fmt.format_id, offset, len(thumb_data), fmt.width, fmt.height
                )
                mhni_records.append(mhni)

            if not mhni_records:
                return None

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

        finally:
            os.unlink(tmp_path)

    def remove_artwork(self, image_id: int) -> bool:
        """Remove artwork entry by image_id.

        Returns True if an entry was found and removed.
        The .ithmb files are compacted the next time save() is called.
        """
        if self._root is None:
            return False
        mhli = self._get_or_create_mhli()
        before = len(mhli.children)
        mhli.children = [c for c in mhli.children if c.fields.get("image_id") != image_id]
        removed = len(mhli.children) < before
        if removed:
            self._needs_rebuild = True
            logger.debug("Removed artwork image_id=%d", image_id)
        return removed

    def save(self) -> None:
        """Write ArtworkDB to disk, compacting .ithmb files if needed."""
        if self._root is None:
            return
        if getattr(self, "_needs_rebuild", False):
            self._rebuild_ithmb()
            self._needs_rebuild = False
        data = write_artworkdb(self._root)
        with open(self._artworkdb_path, "wb") as f:
            f.write(data)

    def _rebuild_ithmb(self) -> None:
        """Rewrite .ithmb files keeping only data referenced by remaining MHII records.

        Updates MHNI offset fields in-place so the ArtworkDB stays consistent.
        """
        # Gather (mhni_record, old_offset, size) grouped by format_id
        by_format: Dict[int, list] = {}
        mhli = self._get_or_create_mhli()
        for mhii in mhli.children:
            for child in mhii.children:
                # Support both bare MHNI and MHOD-wrapped MHNI (parser accepts both)
                if child.magic == MHNI_MAGIC:
                    mhni_list = [child]
                elif child.magic == MHOD_MAGIC:
                    mhni_list = [c for c in child.children if c.magic == MHNI_MAGIC]
                else:
                    continue
                for mhni in mhni_list:
                    fmt_id = mhni.fields.get("format_id")
                    old_offset = mhni.fields.get("ithumb_offset", 0)
                    size = mhni.fields.get("image_size", 0)
                    if fmt_id is not None and size > 0:
                        by_format.setdefault(fmt_id, []).append((mhni, old_offset, size))

        new_offsets: Dict[int, int] = {}
        for fmt_id, entries in by_format.items():
            ithmb_path = os.path.join(self._artwork_dir, f"F{fmt_id}_1.ithmb")
            if not os.path.isfile(ithmb_path):
                continue

            chunks = []
            with open(ithmb_path, "rb") as f:
                for mhni, old_offset, size in entries:
                    f.seek(old_offset)
                    chunks.append((mhni, f.read(size)))

            new_offset = 0
            with open(ithmb_path, "wb") as f:
                for mhni, data in chunks:
                    f.write(data)
                    header = bytearray(mhni.raw_header)
                    put32lint(header, 0x14, new_offset)
                    mhni.raw_header = bytes(header)
                    mhni.fields["ithumb_offset"] = new_offset
                    new_offset += len(data)
            new_offsets[fmt_id] = new_offset

        # Remove .ithmb files that have no remaining entries
        if os.path.isdir(self._artwork_dir):
            for fname in os.listdir(self._artwork_dir):
                if fname.startswith("F") and fname.endswith(".ithmb"):
                    parts = fname[1:].replace(".ithmb", "").split("_")
                    if len(parts) == 2:
                        try:
                            if int(parts[0]) not in by_format:
                                os.unlink(os.path.join(self._artwork_dir, fname))
                        except ValueError:
                            pass

        self._ithmb_offsets = new_offsets
        logger.debug("Rebuilt .ithmb files, %d formats retained", len(new_offsets))

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
    ) -> Record:
        """Create MHNI with MHOD type=3 filename child."""
        hdr_len = 76
        header = bytearray(hdr_len)
        header[0:4] = MHNI_MAGIC
        put32lint(header, 4, hdr_len)
        # total_len at 8 - updated by writer
        put32lint(header, 0x0C, 1)  # num_children (1 MHOD filename)
        put32lint(header, 0x10, format_id)
        put32lint(header, 0x14, offset)  # ithumb_offset
        put32lint(header, 0x18, size)  # image_size
        put16lint(header, 0x20, height)
        put16lint(header, 0x22, width)

        rec = Record(MHNI_MAGIC, hdr_len, 0)
        rec.raw_header = bytes(header)
        rec.fields["format_id"] = format_id
        rec.fields["ithumb_offset"] = offset
        rec.fields["image_size"] = size
        rec.fields["image_width"] = width
        rec.fields["image_height"] = height

        filename = f":F{format_id}_1.ithmb"
        mhod = self._create_filename_mhod(filename)
        rec.children.append(mhod)
        return rec

    def _create_filename_mhod(self, filename: str) -> Record:
        """Create MHOD type=3 with UTF-16LE filename."""
        name_bytes = filename.encode("utf-16-le")
        hdr_len = 24
        body = bytearray(12 + len(name_bytes))
        put32lint(body, 0, len(name_bytes))  # string length
        put32lint(body, 4, 2)  # encoding = 2 (UTF-16LE)
        # body[8:12] = 0 (unknown)
        body[12 : 12 + len(name_bytes)] = name_bytes
        total_len = hdr_len + len(body)

        header = bytearray(hdr_len)
        header[0:4] = MHOD_MAGIC
        put32lint(header, 4, hdr_len)
        put32lint(header, 8, total_len)
        put32lint(header, 0x0C, 3)  # mhod_type = 3

        rec = Record(MHOD_MAGIC, hdr_len, total_len)
        rec.raw_header = bytes(header)
        rec.raw_bytes = bytes(header) + bytes(body)
        rec.fields["mhod_type"] = 3
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

        existing_formats = {c.fields.get("format_id") for c in mhlf.children}
        for fmt in self.formats:
            if fmt.format_id not in existing_formats:
                img_size = image_data_size(fmt)
                mhif = self._create_mhif(fmt.format_id, img_size)
                mhlf.children.append(mhif)

        # Update count in header
        header = bytearray(mhlf.raw_header)
        put32lint(header, 8, len(mhlf.children))
        mhlf.raw_header = bytes(header)
