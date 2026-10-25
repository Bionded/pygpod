"""Photo database support - full CRUD for photos and photo albums.

Ported from libgpod's itdb_photoalbum.c. The PhotoDB is stored in
``<mountpoint>/Photos/Photo Database`` (thumbnails in ``Photos/Thumbs``; libgpod
itdb_get_photos_dir) and uses a similar MHXX binary format to the main ArtworkDB.
Older pygpod versions wrote ``iPod_Control/Photos`` instead: that is
still read when ``Photos`` has no database, and the next save moves it.

Photo IDs start at 0x40, Album IDs at 0x64.
Album types: 1 = Photo Library (master), 2 = normal album.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import struct
from typing import Dict, List, Optional, Union

from ..device.artwork_formats import (
    ArtworkFormatInfo,
    ThumbFormat,
    aligned_width,
    get_photo_formats,
)
from ..exceptions import ArtworkError
from ..utils.compat import put16lint, put32lint, put64lint
from ..utils.datetime import MAC_EPOCH_OFFSET
from .artwork_parser import parse_artworkdb
from .artwork_writer import write_artworkdb
from .parser import Record
from .transaction import Transaction, interrupted_commit
from .writelock import writer_lock

logger = logging.getLogger(__name__)


PHOTO_ID_START = 0x40
ALBUM_ID_START = 0x64
ALBUM_TYPE_MASTER = 1
ALBUM_TYPE_NORMAL = 2

# .ithmb file reference in a thumbnail's file name MHOD: ":F1024_1.ithmb" or ":Thumbs:F1024_1.ithmb"
_ITHMB_NAME = re.compile(r"F(\d+)_(\d+)\.ithmb$")


def _file_date(path: str, tz_shift: int = 0) -> int:
    """A file's modification time as a Mac timestamp in the iPod's local time (``tz_shift``
    seconds east of UTC); 0 if it does not fit in 32 bits."""
    mac = int(os.stat(path).st_mtime) + MAC_EPOCH_OFFSET + tz_shift
    return mac if 0 < mac <= 0xFFFFFFFF else 0


class Photo:
    """Represents a photo on the iPod."""

    def __init__(self) -> None:
        self.photo_id: int = 0
        self.song_id: int = 0  # Links to MHII via song_id field
        self.original_path: str = ""
        self.thumbnails: List[PhotoThumbnail] = []
        # MHII fields libgpod fills in (dates as raw Mac-epoch timestamps, kept as stored)
        self.rating: int = 0
        self.creation_date: int = 0
        self.digitized_date: int = 0
        self.artwork_size: int = 0  # size of the source image file

    def __repr__(self) -> str:
        return f"<Photo id={self.photo_id} thumbs={len(self.thumbnails)}>"


class PhotoThumbnail:
    """A thumbnail version of a photo."""

    def __init__(self) -> None:
        self.format_id: int = 0
        # as stored in the database (libgpod): the scaled image size plus the left/top padding
        self.width: int = 0
        self.height: int = 0
        self.image_offset: int = 0
        self.image_size: int = 0
        self.ithmb_file: int = 1  # the N of F<format>_<N>.ithmb
        # blank pixels left of / above the image in the thumbnail slot (letterboxing)
        self.horizontal_padding: int = 0
        self.vertical_padding: int = 0
        self._pixel_data: Optional[bytes] = None  # set for thumbnails not written yet

    def read_data(self, photos_dir: str) -> Optional[bytes]:
        """Read raw pixel data from .ithmb file."""
        filename = f"F{self.format_id}_{self.ithmb_file}.ithmb"
        filepath = os.path.join(photos_dir, "Thumbs", filename)
        if not os.path.isfile(filepath):
            filepath = os.path.join(photos_dir, filename)
            if not os.path.isfile(filepath):
                return None

        try:
            with open(filepath, "rb") as f:
                f.seek(self.image_offset)
                return f.read(self.image_size)
        except (OSError, IOError):
            logger.warning("Failed to read thumbnail from %s", filepath, exc_info=True)
            return None


class PhotoAlbum:
    """Represents a photo album on the iPod."""

    def __init__(self, name: str = "", album_type: int = ALBUM_TYPE_NORMAL) -> None:
        self.name = name
        self.album_id: int = 0
        self.album_type: int = album_type
        self.photo_ids: List[int] = []

    @property
    def is_master(self) -> bool:
        return self.album_type == ALBUM_TYPE_MASTER

    @property
    def photo_count(self) -> int:
        return len(self.photo_ids)

    def __repr__(self) -> str:
        return f"<PhotoAlbum '{self.name}' ({self.photo_count} photos)>"

    def __len__(self) -> int:
        return self.photo_count


class PhotoDB:
    """Photo database manager for iPod photos.

    Provides CRUD operations for photos and photo albums.
    """

    def __init__(
        self, mountpoint: Optional[str] = None, generation=None, timestamps: str = "local"
    ) -> None:
        """``generation`` (IpodGeneration) picks the photo formats; default: detected from the
        mount point. ``timestamps``: the zone of new photos' dates, as
        ``DatabaseConfig.timestamps`` ('local': the iPod's timezone like libgpod, 'utc')."""
        if timestamps not in ("local", "utc"):
            raise ValueError(f"timestamps must be 'local' or 'utc': {timestamps!r}")
        self._timestamps = timestamps
        self._tz_shift_value: Optional[int] = None
        self._mountpoint = mountpoint
        self._generation = generation
        self._formats: Optional[List[ArtworkFormatInfo]] = None
        self._mhif_order: List[int] = []  # format ids of the loaded database's file list
        self._mhif_sizes: Dict[int, int] = {}  # format id -> MHIF image_size as loaded
        self._read_dir: Optional[str] = None  # where existing thumbnails are read from
        self._photos: List[Photo] = []
        self._albums: List[PhotoAlbum] = []
        self._next_photo_id = PHOTO_ID_START
        self._next_album_id = ALBUM_ID_START
        self._root = None
        self._raw_data: Optional[bytes] = None
        self._merged_masters = 0  # extra master albums merged on load
        self._dropped_members = 0  # album members without a photo dropped on load
        self._master_missing = False  # the loaded database had no master album
        self._not_in_master = 0  # loaded photos missing from the master album, added on load

        if mountpoint:
            self._load(mountpoint)

    def _tz_shift(self) -> int:
        if self._timestamps == "utc":
            return 0
        if self._tz_shift_value is None:
            from ..utils.timezone import device_timezone_shift

            self._tz_shift_value = device_timezone_shift(self._mountpoint)
        return self._tz_shift_value

    def _load(self, mountpoint: str) -> None:
        """Load photo database from iPod."""
        photos_dir = photos_dir_for(mountpoint)
        db_path = os.path.join(photos_dir, "Photo Database")
        if not os.path.isfile(db_path) and os.path.isfile(
            os.path.join(_legacy_dir(mountpoint), "Photo Database")
        ):
            photos_dir = _legacy_dir(mountpoint)
            db_path = os.path.join(photos_dir, "Photo Database")
        self._read_dir = photos_dir

        if not os.path.isfile(db_path):
            return

        with open(db_path, "rb") as f:
            data = f.read()

        self._raw_data = data
        self._root = parse_artworkdb(data)
        self._build_model()

    def _build_model(self) -> None:
        """Build photo/album model from parsed artwork records."""
        if not self._root:
            return

        for mhsd in self._root.children:
            mhsd_type = mhsd.fields.get("mhsd_type", 0)
            if mhsd_type == 1 and mhsd.children:
                # Image list - MHLI with MHII children
                mhli = mhsd.children[0]
                for mhii in mhli.children:
                    photo = Photo()
                    photo.photo_id = mhii.fields.get("image_id", 0)
                    photo.song_id = mhii.fields.get("song_id", 0)
                    photo.rating = mhii.fields.get("rating", 0)
                    photo.creation_date = mhii.fields.get("creation_date", 0)
                    photo.digitized_date = mhii.fields.get("digitized_date", 0)
                    photo.artwork_size = mhii.fields.get("artwork_size", 0)

                    for child in mhii.children:
                        # MHNI can be direct child or wrapped in MHOD type=2
                        mhni_list = []
                        if child.magic == b"mhni":
                            mhni_list.append(child)
                        elif child.magic == b"mhod":
                            for sub in child.children:
                                if sub.magic == b"mhni":
                                    mhni_list.append(sub)
                        for mhni in mhni_list:
                            thumb = PhotoThumbnail()
                            thumb.format_id = mhni.fields.get("format_id", 0)
                            thumb.width = mhni.fields.get("image_width", 0)
                            thumb.height = mhni.fields.get("image_height", 0)
                            thumb.image_offset = mhni.fields.get("ithumb_offset", 0)
                            thumb.image_size = mhni.fields.get("image_size", 0)
                            thumb.vertical_padding = mhni.fields.get("vertical_padding", 0)
                            thumb.horizontal_padding = mhni.fields.get("horizontal_padding", 0)
                            for mhod in mhni.children:
                                name = mhod.fields.get("string", "")
                                m = _ITHMB_NAME.search(name) if mhod.magic == b"mhod" else None
                                if m:
                                    thumb.ithmb_file = int(m.group(2))
                            photo.thumbnails.append(thumb)

                    self._photos.append(photo)
                    if photo.photo_id >= self._next_photo_id:
                        self._next_photo_id = photo.photo_id + 1

            elif mhsd_type == 3 and mhsd.children:
                # File list: keep its order and sizes on re-save
                mhifs = [m for m in mhsd.children[0].children if m.magic == b"mhif"]
                self._mhif_order = [m.fields.get("format_id", 0) for m in mhifs]
                self._mhif_sizes = {
                    m.fields.get("format_id", 0): m.fields.get("image_size", 0) for m in mhifs
                }

            elif mhsd_type == 2 and mhsd.children:
                # Album list
                mhla = mhsd.children[0]
                for mhba in mhla.children:
                    album = PhotoAlbum()
                    album.album_id = mhba.fields.get("album_id", 0)
                    album.album_type = mhba.fields.get("album_type", ALBUM_TYPE_NORMAL)

                    # Get album name from MHOD children
                    for child in mhba.children:
                        if child.magic == b"mhod":
                            name = child.fields.get("string", "")
                            if name:
                                album.name = name
                                break

                    # Get photo references from MHIA children
                    for child in mhba.children:
                        if child.magic == b"mhia":
                            pid = child.fields.get("image_id", 0)
                            if pid:
                                album.photo_ids.append(pid)

                    self._albums.append(album)
                    if album.album_id >= self._next_album_id:
                        self._next_album_id = album.album_id + 1

        self._merge_master_albums()
        self._dropped_members = self._drop_members_without_photo()
        self._master_missing = self.master_album is None
        if not self._master_missing:
            self._not_in_master = self._add_missing_to_master()

    def _add_missing_to_master(self) -> int:
        """Append the photos the master album (Photo Library) lacks, in database order, and
        return how many: libgpod and iTunes keep every photo in it, and the iPod's Photo
        Library shows only its members."""
        master = self.master_album
        listed = set(master.photo_ids)
        missing = [p.photo_id for p in self._photos if p.photo_id not in listed]
        if missing:
            logger.warning(
                "Photo Library album: %d photo(s) of the database were missing, added",
                len(missing),
            )
            master.photo_ids.extend(missing)
        return len(missing)

    def _drop_members_without_photo(self) -> int:
        """Remove album members (MHIA) whose photo is not in the database and return how many
        were removed: libgpod crashes reading such a Photo Database and the iPod may show no
        photos at all."""
        ids = {p.photo_id for p in self._photos}
        dropped = 0
        for album in self._albums:
            kept = [pid for pid in album.photo_ids if pid in ids]
            if len(kept) != len(album.photo_ids):
                missing = [pid for pid in album.photo_ids if pid not in ids]
                logger.warning(
                    "photo album %r: members without a photo dropped (%s)",
                    album.name,
                    ", ".join(str(pid) for pid in missing),
                )
                dropped += len(missing)
                album.photo_ids = kept
        return dropped

    def _merge_master_albums(self) -> None:
        """Merge several master albums (Photo Library) into the first one, keeping the order of
        the photos: the iPod and libgpod expect exactly one. Older pygpod versions added a
        second master album when the first one was empty."""
        masters = [a for a in self._albums if a.is_master]
        self._merged_masters = len(masters) - 1 if masters else 0
        if len(masters) < 2:
            return
        first = masters[0]
        for extra in masters[1:]:
            for pid in extra.photo_ids:
                if pid not in first.photo_ids:
                    first.photo_ids.append(pid)
        self._albums = [a for a in self._albums if not a.is_master or a is first]
        logger.warning(
            "Photo Database has %d master photo albums: merged into album %d",
            len(masters),
            first.album_id,
        )

    def _build_root(self) -> Record:
        """Build a record tree from the in-memory photo/album model.

        Creates the MHFD → MHSD → MHLI/MHLA hierarchy needed by
        write_artworkdb(). Uses libgpod-compatible padded header sizes
        and field layouts.
        """
        self._ensure_master_album()  # e.g. a loaded database without one
        # -- MHII records (one per photo) --
        # Structure: MHII → MHOD(type=2) → MHNI → MHOD(type=3, filename)
        mhii_list = []
        for photo in self._photos:
            mhii_children = []

            for thumb in photo.thumbnails:
                # ":Thumbs:F{format_id}_{file}.ithmb", relative to the Photos folder like
                # libgpod writes it (":F..." alone would point to iPod_Control/Artwork)
                ithmb_name = f":Thumbs:F{thumb.format_id}_{thumb.ithmb_file}.ithmb"
                fname_bytes = ithmb_name.encode("utf-16-le")

                # MHOD type=3 (filename string, child of MHNI): UTF-16LE, encoding 2 (libgpod)
                mhod3_hlen = 0x18
                mhod3_body = struct.pack("<III", len(fname_bytes), 2, 0) + fname_bytes
                # Pad to 4-byte boundary
                pad = (4 - (mhod3_hlen + len(mhod3_body)) % 4) % 4
                mhod3_body += b"\x00" * pad
                mhod3_total = mhod3_hlen + len(mhod3_body)
                mhod3_h = bytearray(mhod3_hlen)
                mhod3_h[0:4] = b"mhod"
                put32lint(mhod3_h, 4, mhod3_hlen)
                put32lint(mhod3_h, 8, mhod3_total)
                put16lint(mhod3_h, 0x0C, 3)  # mhod_type = 3 (filename)
                mhod3_h[0x0F] = pad  # padding_len: the firmware needs it (libgpod, iTunes)
                mhod3_rec = Record(b"mhod", mhod3_hlen, mhod3_total)
                mhod3_rec.raw_header = bytes(mhod3_h)
                mhod3_rec.raw_bytes = bytes(mhod3_h) + bytes(mhod3_body)

                # MHNI (0x4C padded header, libgpod layout)
                mhni_hlen = 0x4C
                mhni_h = bytearray(mhni_hlen)
                mhni_h[0:4] = b"mhni"
                put32lint(mhni_h, 4, mhni_hlen)
                # total_len set below
                put32lint(mhni_h, 0x0C, 1)  # num_children (MHOD type=3)
                put32lint(mhni_h, 0x10, thumb.format_id)
                put32lint(mhni_h, 0x14, thumb.image_offset)  # ithmb_offset
                put32lint(mhni_h, 0x18, thumb.image_size)  # image_size
                put16lint(mhni_h, 0x1C, thumb.vertical_padding)
                put16lint(mhni_h, 0x1E, thumb.horizontal_padding)
                put16lint(mhni_h, 0x20, thumb.height)  # image_height
                put16lint(mhni_h, 0x22, thumb.width)  # image_width
                mhni_total = mhni_hlen + mhod3_total
                put32lint(mhni_h, 8, mhni_total)
                mhni_rec = Record(b"mhni", mhni_hlen, mhni_total)
                mhni_rec.raw_header = bytes(mhni_h)
                mhni_rec.raw_bytes = bytes(mhni_h)
                mhni_rec.children = [mhod3_rec]

                # MHOD type=2 (container wrapping MHNI)
                mhod2_hlen = 0x18
                mhod2_h = bytearray(mhod2_hlen)
                mhod2_h[0:4] = b"mhod"
                put32lint(mhod2_h, 4, mhod2_hlen)
                mhod2_total = mhod2_hlen + mhni_total
                put32lint(mhod2_h, 8, mhod2_total)
                put16lint(mhod2_h, 0x0C, 2)  # mhod_type = 2 (thumbnail)
                mhod2_rec = Record(b"mhod", mhod2_hlen, mhod2_total)
                mhod2_rec.raw_header = bytes(mhod2_h)
                mhod2_rec.raw_bytes = bytes(mhod2_h)
                mhod2_rec.children = [mhni_rec]

                mhii_children.append(mhod2_rec)

            num_children = len(mhii_children)
            hlen = 0x98  # libgpod padded MHII size
            h = bytearray(hlen)
            h[0:4] = b"mhii"
            put32lint(h, 4, hlen)
            put32lint(h, 8, hlen)  # total_len updated by writer
            put32lint(h, 0x0C, num_children)
            put32lint(h, 0x10, photo.photo_id)
            put64lint(h, 0x14, photo.song_id if photo.song_id else photo.photo_id + 2)
            put32lint(h, 0x20, photo.rating)
            put32lint(h, 0x28, photo.creation_date)
            put32lint(h, 0x2C, photo.digitized_date)
            put32lint(h, 0x30, photo.artwork_size)
            rec = Record(b"mhii", hlen, hlen)
            rec.raw_header = bytes(h)
            rec.raw_bytes = bytes(h)
            rec.children = mhii_children
            mhii_list.append(rec)

        # -- MHLI (image list, count-magic - offset 8 = num_items) --
        mhli_hlen = 0x5C  # libgpod padded size
        mhli_h = bytearray(mhli_hlen)
        mhli_h[0:4] = b"mhli"
        put32lint(mhli_h, 4, mhli_hlen)
        put32lint(mhli_h, 8, len(mhii_list))
        mhli = Record(b"mhli", mhli_hlen, len(mhii_list))
        mhli.raw_header = bytes(mhli_h)
        mhli.raw_bytes = bytes(mhli_h)
        mhli.children = mhii_list

        # -- MHSD type=1 (image list section) --
        mhsd1_hlen = 0x60
        mhsd1_h = bytearray(mhsd1_hlen)
        mhsd1_h[0:4] = b"mhsd"
        put32lint(mhsd1_h, 4, mhsd1_hlen)
        put16lint(mhsd1_h, 0x0C, 1)  # mhsd_type = 1 (16-bit in ArtworkDB)
        mhsd1 = Record(b"mhsd", mhsd1_hlen, 0)
        mhsd1.raw_header = bytes(mhsd1_h)
        mhsd1.raw_bytes = bytes(mhsd1_h)
        mhsd1.children = [mhli]

        # -- MHBA records (one per album) --
        # album ids follow the highest photo id, like libgpod (the iPod shares one id space)
        next_id = max((p.photo_id for p in self._photos), default=PHOTO_ID_START - 1) + 1
        for album in self._albums:
            album.album_id = next_id
            next_id += 1
        self._next_album_id = next_id
        mhba_list = []
        prev_album_id = 0
        for index, album in enumerate(self._albums):
            # "prev album id" as iTunes and libgpod write it: 100 for the master album, 101 for
            # the next one, then the previous value plus the previous album's photo count + 1
            if index < 2:
                prev_album_id = 100 + index
            else:
                prev_album_id += len(self._albums[index - 1].photo_ids) + 1
            children = []

            # MHOD - album name (UTF-8 string)
            name_bytes = album.name.encode("utf-8")
            mhod_hlen = 0x18
            # ArtworkDB MHOD string body: string_len(4), encoding(1), pad(3), unknown(4)
            mhod_body = struct.pack("<IbBhI", len(name_bytes), 1, 0, 0, 0) + name_bytes
            pad = (4 - (mhod_hlen + len(mhod_body)) % 4) % 4
            mhod_body += b"\x00" * pad
            mhod_total = mhod_hlen + len(mhod_body)
            mhod_h = bytearray(mhod_hlen)
            mhod_h[0:4] = b"mhod"
            put32lint(mhod_h, 4, mhod_hlen)
            put32lint(mhod_h, 8, mhod_total)
            put16lint(mhod_h, 0x0C, 1)  # mhod_type = 1 (album name)
            mhod_h[0x0F] = pad  # padding_len
            mhod_rec = Record(b"mhod", mhod_hlen, mhod_total)
            mhod_rec.raw_header = bytes(mhod_h)
            mhod_rec.raw_bytes = bytes(mhod_h) + bytes(mhod_body)
            mhod_rec.fields["mhod_type"] = 1
            mhod_rec.fields["string"] = album.name
            children.append(mhod_rec)

            # MHIA - photo references (leaf, fixed 40 bytes each)
            for pid in album.photo_ids:
                mhia_hlen = 0x28
                mhia_h = bytearray(mhia_hlen)
                mhia_h[0:4] = b"mhia"
                put32lint(mhia_h, 4, mhia_hlen)
                put32lint(mhia_h, 8, mhia_hlen)
                put32lint(mhia_h, 0x10, pid)
                mhia_rec = Record(b"mhia", mhia_hlen, mhia_hlen)
                mhia_rec.raw_header = bytes(mhia_h)
                mhia_rec.raw_bytes = bytes(mhia_h)
                mhia_rec.fields["image_id"] = pid
                children.append(mhia_rec)

            # MHBA record (0x94 padded header, libgpod layout)
            mhba_hlen = 0x94
            mhba_h = bytearray(mhba_hlen)
            mhba_h[0:4] = b"mhba"
            put32lint(mhba_h, 4, mhba_hlen)
            # offset 8 (total_len) set by writer
            put32lint(mhba_h, 0x0C, 1)  # num_mhods
            put32lint(mhba_h, 0x10, len(album.photo_ids))  # num_mhias
            put32lint(mhba_h, 0x14, album.album_id)
            # 0x18: unk024 = 0
            # 0x1C: unk028 = 0 (16-bit)
            mhba_h[0x1E] = album.album_type
            put32lint(mhba_h, 0x3C, prev_album_id)
            mhba_rec = Record(b"mhba", mhba_hlen, 0)
            mhba_rec.raw_header = bytes(mhba_h)
            mhba_rec.raw_bytes = bytes(mhba_h)
            mhba_rec.children = children
            mhba_list.append(mhba_rec)

        # -- MHLA (album list, count-magic) --
        mhla_hlen = 0x5C  # libgpod padded size
        mhla_h = bytearray(mhla_hlen)
        mhla_h[0:4] = b"mhla"
        put32lint(mhla_h, 4, mhla_hlen)
        put32lint(mhla_h, 8, len(mhba_list))
        mhla = Record(b"mhla", mhla_hlen, len(mhba_list))
        mhla.raw_header = bytes(mhla_h)
        mhla.raw_bytes = bytes(mhla_h)
        mhla.children = mhba_list

        # -- MHSD type=2 (album list section) --
        mhsd2_hlen = 0x60
        mhsd2_h = bytearray(mhsd2_hlen)
        mhsd2_h[0:4] = b"mhsd"
        put32lint(mhsd2_h, 4, mhsd2_hlen)
        put16lint(mhsd2_h, 0x0C, 2)  # mhsd_type = 2
        mhsd2 = Record(b"mhsd", mhsd2_hlen, 0)
        mhsd2.raw_header = bytes(mhsd2_h)
        mhsd2.raw_bytes = bytes(mhsd2_h)
        mhsd2.children = [mhla]

        # -- MHLF/MHIF (image file list, used for .ithmb file info) --
        format_ids = set()
        for photo in self._photos:
            for thumb in photo.thumbnails:
                format_ids.add(thumb.format_id)

        # in the device's format order like libgpod once known (photos added), else as loaded
        known = [f.format_id for f in self._formats] if self._formats else self._mhif_order
        order = [f for f in dict.fromkeys(known) if f in format_ids]
        mhif_list = []
        device_formats = {f.format_id: f for f in self._formats or ()}
        for fmt_id in order + sorted(format_ids - set(order)):
            fmt = device_formats.get(fmt_id)
            if fmt is not None:
                # libgpod's mhif image_size: width * height * 2 of the device's format, also
                # when older thumbnails of another size are still in the database
                total_size = fmt.width * fmt.height * 2
            elif fmt_id in self._mhif_sizes:  # database loaded, nothing added: as loaded
                total_size = self._mhif_sizes[fmt_id]
            else:  # as the first thumbnail
                total_size = next(
                    t.image_size
                    for p in self._photos
                    for t in p.thumbnails
                    if t.format_id == fmt_id
                )

            mhif_hlen = 0x7C  # libgpod padded size
            mhif_h = bytearray(mhif_hlen)
            mhif_h[0:4] = b"mhif"
            put32lint(mhif_h, 4, mhif_hlen)
            put32lint(mhif_h, 8, mhif_hlen)  # total_len
            # 0x0C: unknown1 = 0
            put32lint(mhif_h, 0x10, fmt_id)  # format_id
            put32lint(mhif_h, 0x14, total_size)  # image_size
            mhif_rec = Record(b"mhif", mhif_hlen, mhif_hlen)
            mhif_rec.raw_header = bytes(mhif_h)
            mhif_rec.raw_bytes = bytes(mhif_h)
            mhif_list.append(mhif_rec)

        mhlf_hlen = 0x5C  # libgpod padded size
        mhlf_h = bytearray(mhlf_hlen)
        mhlf_h[0:4] = b"mhlf"
        put32lint(mhlf_h, 4, mhlf_hlen)
        put32lint(mhlf_h, 8, len(mhif_list))  # count
        mhlf = Record(b"mhlf", mhlf_hlen, len(mhif_list))
        mhlf.raw_header = bytes(mhlf_h)
        mhlf.raw_bytes = bytes(mhlf_h)
        mhlf.children = mhif_list

        # MHSD type=3 (file list section)
        mhsd3_hlen = 0x60
        mhsd3_h = bytearray(mhsd3_hlen)
        mhsd3_h[0:4] = b"mhsd"
        put32lint(mhsd3_h, 4, mhsd3_hlen)
        put16lint(mhsd3_h, 0x0C, 3)  # mhsd_type = 3
        mhsd3 = Record(b"mhsd", mhsd3_hlen, 0)
        mhsd3.raw_header = bytes(mhsd3_h)
        mhsd3.raw_bytes = bytes(mhsd3_h)
        mhsd3.children = [mhlf]

        # -- MHFD (root, 0x84 padded header) --
        mhfd_hlen = 0x84
        mhfd_h = bytearray(mhfd_hlen)
        mhfd_h[0:4] = b"mhfd"
        put32lint(mhfd_h, 4, mhfd_hlen)
        # offset 8 (total_len) set by writer
        # 0x0C: unknown1
        put32lint(mhfd_h, 0x10, 2)  # unknown2 (must be 2, iTunes7+ requires this)
        put32lint(mhfd_h, 0x14, 3)  # num_children = 3
        # 0x1C: next_id, the highest photo or album id + 1 (libgpod)
        put32lint(mhfd_h, 0x1C, next_id)
        mhfd_h[0x30] = 2  # unknown_flag1 = 2 (as libgpod sets)
        root = Record(b"mhfd", mhfd_hlen, 0)
        root.raw_header = bytes(mhfd_h)
        root.raw_bytes = bytes(mhfd_h)
        root.children = [mhsd1, mhsd2, mhsd3]
        return root

    def _photo_formats(self) -> List[ArtworkFormatInfo]:
        """The device's photo formats in libgpod's write order (the reverse of the table /
        SysInfoExtended order)."""
        if self._formats is None:
            generation = self._generation
            formats = None
            if generation is None and self._mountpoint:
                from ..device.device import Device

                device = Device.from_mountpoint(self._mountpoint)
                generation = device.generation
                formats = device.get_photo_formats()
            elif generation is not None:
                formats = get_photo_formats(generation)
            if not formats:
                raise ArtworkError(f"no photo thumbnail formats known for this iPod ({generation})")
            self._formats = list(reversed(formats))
        return self._formats

    # ---- Public API ----

    @property
    def photos(self) -> List[Photo]:
        return list(self._photos)

    @property
    def albums(self) -> List[PhotoAlbum]:
        return list(self._albums)

    @property
    def master_album(self) -> Optional[PhotoAlbum]:
        for a in self._albums:
            if a.is_master:
                return a
        return None

    def _ensure_master_album(self) -> PhotoAlbum:
        """The master album (Photo Library), created first if there is none: libgpod's
        itdb_photodb_create() and iTunes always write it as the first album, listing every
        photo of the database."""
        master = self.master_album
        if master is None:  # not "if not master": an empty album is falsy (__len__)
            master = PhotoAlbum(name="Photo Library", album_type=ALBUM_TYPE_MASTER)
            master.album_id = self._next_album_id
            self._next_album_id += 1
            master.photo_ids = [p.photo_id for p in self._photos]
            self._albums.insert(0, master)
        return master

    def get_photo(self, photo_id: int) -> Optional[Photo]:
        for p in self._photos:
            if p.photo_id == photo_id:
                return p
        return None

    def add_photo(self, image_path: Union[str, bytes]) -> Photo:
        """Add a photo from an image file.

        Requires Pillow for image processing.

        Args:
            image_path: Path to image file, or the encoded image (e.g. JPEG/PNG bytes).
                The photo's ``artwork_size`` is the file size (the length of the bytes) and
                its ``creation_date`` the file's modification time (0 for bytes), like libgpod
                stores them.

        Returns:
            New Photo object.

        Raises:
            ArtworkError: If Pillow is not available or image can't be processed.
        """
        try:
            from PIL import Image  # type: ignore  # noqa: F401
        except ImportError:
            raise ArtworkError("Pillow is required for photo support")

        photo = Photo()
        if isinstance(image_path, (bytes, bytearray)):
            if not image_path:
                raise ArtworkError("empty image data")
            photo.artwork_size = len(image_path)
        else:
            if not os.path.isfile(image_path):
                raise ArtworkError(f"Image file not found: {image_path}")
            photo.original_path = image_path
            photo.artwork_size = os.path.getsize(image_path)
            photo.creation_date = _file_date(image_path, self._tz_shift())
        photo.photo_id = self._next_photo_id

        # One thumbnail per photo format of the device (libgpod tables)
        from ..model.artwork import PixelFormat, _open_rgb, render_fitted_thumbnail

        pixel = {
            ThumbFormat.RGB565_LE: PixelFormat.RGB565_LE,
            ThumbFormat.RGB565_BE: PixelFormat.RGB565_BE,
            ThumbFormat.I420_LE: PixelFormat.I420,
            ThumbFormat.UYVY_BE: PixelFormat.UYVY,
        }
        source = None
        for fmt in self._photo_formats():
            if fmt.pixel_format not in pixel:
                continue
            # scaled to fit and centred in the slot, like libgpod (cropped if the format says so)
            if source is None:
                source = _open_rgb(image_path)  # decoded once for all formats
            fitted = render_fitted_thumbnail(
                source,
                fmt.width,
                fmt.height,
                pixel[fmt.pixel_format],
                crop=fmt.crop,
                back_color=fmt.back_color,
                row_width=aligned_width(fmt),
            )
            if fitted is None or not fitted.data:
                continue
            thumb = PhotoThumbnail()
            thumb.format_id = fmt.format_id
            thumb.width = fitted.width
            thumb.height = fitted.height
            thumb.horizontal_padding = fitted.horizontal_padding
            thumb.vertical_padding = fitted.vertical_padding
            thumb.image_size = len(fitted.data)
            thumb._pixel_data = fitted.data
            photo.thumbnails.append(thumb)
        if not photo.thumbnails:
            source = "image data" if isinstance(image_path, (bytes, bytearray)) else image_path
            raise ArtworkError(f"no photo thumbnail could be made from {source}")
        self._next_photo_id += 1

        master = self._ensure_master_album()  # before the append: a new master lists every photo
        self._photos.append(photo)
        master.photo_ids.append(photo.photo_id)

        return photo

    def remove_photo(self, photo: Photo) -> None:
        """Remove a photo from the database and all albums.

        Args:
            photo: Photo to remove.
        """
        pid = photo.photo_id
        self._photos = [p for p in self._photos if p.photo_id != pid]

        for album in self._albums:
            album.photo_ids = [i for i in album.photo_ids if i != pid]

    def create_album(self, name: str) -> PhotoAlbum:
        """Create a new photo album.

        Args:
            name: Album name.

        Returns:
            New PhotoAlbum object.
        """
        logger.debug("Creating photo album: %s", name)
        album = PhotoAlbum(name=name, album_type=ALBUM_TYPE_NORMAL)
        album.album_id = self._next_album_id
        self._next_album_id += 1
        self._albums.append(album)
        return album

    def remove_album(self, album: PhotoAlbum) -> None:
        """Remove a photo album.

        The master album (Photo Library) cannot be removed.

        Args:
            album: Album to remove.

        Raises:
            ArtworkError: If trying to remove the master album.
        """
        if album.is_master:
            raise ArtworkError("Cannot remove the Photo Library album")
        self._albums = [a for a in self._albums if a.album_id != album.album_id]

    def add_photo_to_album(self, album: PhotoAlbum, photo: Photo) -> None:
        """Add a photo to an album.

        Args:
            album: Target album.
            photo: Photo to add; it must be in this database (added with :meth:`add_photo` or
                loaded with it), not a photo of another ``PhotoDB`` instance.

        Raises:
            ValueError: If no photo with ``photo.photo_id`` is in this database (the album would
                reference a missing photo: libgpod and the iPod cannot read that database).
        """
        if self.get_photo(photo.photo_id) is None:
            raise ValueError(f"photo {photo.photo_id} is not in this Photo Database")
        if photo.photo_id not in album.photo_ids:
            album.photo_ids.append(photo.photo_id)

    def remove_photo_from_album(self, album: PhotoAlbum, photo: Photo) -> None:
        """Remove a photo from an album.

        Args:
            album: Target album.
            photo: Photo to remove.
        """
        album.photo_ids = [i for i in album.photo_ids if i != photo.photo_id]

    def album_by_name(self, name: str) -> Optional[PhotoAlbum]:
        """Find album by name."""
        for a in self._albums:
            if a.name == name:
                return a
        return None

    def save(self) -> None:
        """Save the photo database back to the iPod.

        Every thumbnail (new ones and those already on the iPod) is read first, then written to
        fresh ``F<format>_<N>.ithmb`` files under a file index that no .ithmb file on the iPod
        has; these, the new database and the deletion of the .ithmb files nothing refers to any
        more are committed together (crash-safe, like ``Database.save()``). Existing images are
        never overwritten in place; removed photos' data is dropped. If the save fails, the
        files on the iPod and the photos' thumbnail references stay as they were, and saving
        again is safe. Nothing is written when nothing changed (no new thumbnails, the same
        database as on disk). The save holds the database writer lock (ConflictError while
        another process saves the iPod's databases), as the commit journal is shared.

        A Photo Database written by pygpod re-saves byte-identically. One written by iTunes or
        libgpod is rewritten in pygpod's layout on its first save, even without changes
        (thumbnails move to new .ithmb files, album ids are renumbered); libgpod and the iPod
        read the result.
        """
        if not self._mountpoint:
            raise ArtworkError("No mount point set")
        self._drop_members_without_photo()  # e.g. ids appended to album.photo_ids directly

        photos_dir = photos_dir_for(self._mountpoint)
        thumbs_dir = os.path.join(photos_dir, "Thumbs")
        read_dir = self._read_dir or photos_dir
        moving = read_dir != photos_dir
        if not moving and self._unchanged_on_disk(photos_dir):
            logger.debug("Photo Database unchanged, nothing written")
            return
        # the commit journal is shared with Database.save(): one writer at a time
        itunes_dir = os.path.join(self._mountpoint, "iPod_Control", "iTunes")
        os.makedirs(itunes_dir, exist_ok=True)
        with writer_lock(itunes_dir):
            self._save_locked(photos_dir, thumbs_dir, read_dir, moving)

    def _save_locked(self, photos_dir: str, thumbs_dir: str, read_dir: str, moving: bool) -> None:
        if interrupted_commit(self._mountpoint):  # its journal would be overwritten
            raise ArtworkError(
                "an interrupted save is not completed yet: open the iPod database with "
                "Database (it completes the save) before saving photos"
            )

        # 1. all pixel data in memory before any file changes
        old_thumbnails = [(photo, photo.thumbnails) for photo in self._photos]
        pixels: Dict[int, bytes] = {}  # id(thumb) -> data
        for photo in self._photos:
            kept = []
            for thumb in photo.thumbnails:
                data = thumb._pixel_data or thumb.read_data(read_dir)
                if data is None:
                    logger.warning(
                        "photo %d: thumbnail %d unreadable, dropped",
                        photo.photo_id,
                        thumb.format_id,
                    )
                    continue
                pixels[id(thumb)] = data
                kept.append(thumb)
            photo.thumbnails = kept

        # 2. new .ithmb files under an index neither the database on disk nor any file there
        # uses, staged with the database and committed together: a failed save leaves the
        # old files and the thumbnails' addresses as they were
        by_format: Dict[int, List[PhotoThumbnail]] = {}
        for photo in self._photos:
            for thumb in photo.thumbnails:
                by_format.setdefault(thumb.format_id, []).append(thumb)
        on_disk = _ithmb_indexes(thumbs_dir, photos_dir)
        old_root = self._root
        saved = [
            (thumb, thumb.ithmb_file, thumb.image_offset, thumb.image_size, thumb._pixel_data)
            for thumbs in by_format.values()
            for thumb in thumbs
        ]
        tx = Transaction(self._mountpoint)
        try:
            os.makedirs(thumbs_dir, exist_ok=True)
            blobs: Dict[str, bytes] = {}
            for fmt_id, thumbs in by_format.items():
                used = set(on_disk.get(fmt_id, ()))
                if not moving:
                    used |= {t.ithmb_file for t in thumbs if t._pixel_data is None}
                index = next(i for i in range(1, len(used) + 2) if i not in used)
                blob = bytearray()
                for thumb in thumbs:
                    thumb.image_offset = len(blob)
                    thumb.image_size = len(pixels[id(thumb)])
                    thumb.ithmb_file = index
                    thumb._pixel_data = None
                    blob += pixels[id(thumb)]
                blobs[f"F{fmt_id}_{index}.ithmb"] = bytes(blob)
            for name, blob in blobs.items():
                tx.stage_bytes(os.path.join(thumbs_dir, name), blob)

            # 3. the database; 4. .ithmb files nothing refers to are deleted on commit
            self._root = self._build_root()
            tx.stage_bytes(os.path.join(photos_dir, "Photo Database"), write_artworkdb(self._root))
            for name in os.listdir(thumbs_dir):
                if _ITHMB_NAME.fullmatch(name) and name not in blobs:
                    tx.delete_on_commit(os.path.join(thumbs_dir, name))
            tx.commit()
        except BaseException:
            tx.abort()
            if interrupted_commit(self._mountpoint):
                raise  # past the commit point: the new files take effect when it is completed
            self._root = old_root
            for photo, thumbnails in old_thumbnails:
                photo.thumbnails = thumbnails
            for thumb, ithmb_file, offset, size, data in saved:
                thumb.ithmb_file = ithmb_file
                thumb.image_offset = offset
                thumb.image_size = size
                thumb._pixel_data = data
            raise
        if moving:  # the database moved to Photos/: drop pygpod's old iPod_Control/Photos copy
            shutil.rmtree(read_dir, ignore_errors=True)
        self._read_dir = photos_dir

    def _unchanged_on_disk(self, photos_dir: str) -> bool:
        """True if no thumbnail waits to be written and the database would be written
        byte-identical to the one on disk."""
        if any(t._pixel_data is not None for p in self._photos for t in p.thumbnails):
            return False
        db_path = os.path.join(photos_dir, "Photo Database")
        try:
            with open(db_path, "rb") as f:
                on_disk = f.read()
        except OSError:
            return False
        return write_artworkdb(self._build_root()) == on_disk

    def __repr__(self) -> str:
        return f"<PhotoDB {len(self._photos)} photos, {len(self._albums)} albums>"


def _ithmb_indexes(*dirs: str) -> Dict[int, set]:
    """format id -> the N of every ``F<format>_<N>.ithmb`` file in ``dirs``."""
    out: Dict[int, set] = {}
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            m = _ITHMB_NAME.fullmatch(name)
            if m:
                out.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
    return out


def photos_dir_for(mountpoint: str) -> str:
    """``<mountpoint>/Photos``, matching an existing directory case-insensitively (FAT)."""
    for name in os.listdir(mountpoint) if os.path.isdir(mountpoint) else []:
        if name.lower() == "photos" and os.path.isdir(os.path.join(mountpoint, name)):
            return os.path.join(mountpoint, name)
    return os.path.join(mountpoint, "Photos")


def _legacy_dir(mountpoint: str) -> str:
    return os.path.join(mountpoint, "iPod_Control", "Photos")
