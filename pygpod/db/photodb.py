"""Photo database support - full CRUD for photos and photo albums.

Ported from libgpod's itdb_photoalbum.c. The PhotoDB is stored in
iPod_Control/Photos/Photo Database and uses a similar MHXX binary
format to the main ArtworkDB.

Photo IDs start at 0x40, Album IDs at 0x64.
Album types: 1 = Photo Library (master), 2 = normal album.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import List, Optional

from ..exceptions import ArtworkError
from ..utils.compat import put16lint, put32lint, put64lint
from .artwork_parser import parse_artworkdb
from .artwork_writer import write_artworkdb
from .parser import Record

logger = logging.getLogger(__name__)


PHOTO_ID_START = 0x40
ALBUM_ID_START = 0x64
ALBUM_TYPE_MASTER = 1
ALBUM_TYPE_NORMAL = 2

# Photo thumbnail format IDs for iPod Classic
PHOTO_FORMAT_IDS = [1042, 1043, 1044]  # 320x240, 130x88, 56x56 - all RGB565_LE


class Photo:
    """Represents a photo on the iPod."""

    def __init__(self) -> None:
        self.photo_id: int = 0
        self.song_id: int = 0  # Links to MHII via song_id field
        self.original_path: str = ""
        self.thumbnails: List[PhotoThumbnail] = []

    def __repr__(self) -> str:
        return f"<Photo id={self.photo_id} thumbs={len(self.thumbnails)}>"


class PhotoThumbnail:
    """A thumbnail version of a photo."""

    def __init__(self) -> None:
        self.format_id: int = 0
        self.width: int = 0
        self.height: int = 0
        self.image_offset: int = 0
        self.image_size: int = 0
        self.ithmb_file: int = 0

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

    def __init__(self, mountpoint: Optional[str] = None) -> None:
        self._mountpoint = mountpoint
        self._photos: List[Photo] = []
        self._albums: List[PhotoAlbum] = []
        self._next_photo_id = PHOTO_ID_START
        self._next_album_id = ALBUM_ID_START
        self._root = None
        self._raw_data: Optional[bytes] = None

        if mountpoint:
            self._load(mountpoint)

    def _load(self, mountpoint: str) -> None:
        """Load photo database from iPod."""
        photos_dir = os.path.join(mountpoint, "iPod_Control", "Photos")
        db_path = os.path.join(photos_dir, "Photo Database")

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
                            thumb.ithmb_file = mhni.fields.get("format_id", 0)
                            photo.thumbnails.append(thumb)

                    self._photos.append(photo)
                    if photo.photo_id >= self._next_photo_id:
                        self._next_photo_id = photo.photo_id + 1

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

    def _build_root(self) -> Record:
        """Build a record tree from the in-memory photo/album model.

        Creates the MHFD → MHSD → MHLI/MHLA hierarchy needed by
        write_artworkdb(). Uses libgpod-compatible padded header sizes
        and field layouts.
        """
        # -- MHII records (one per photo) --
        # Structure: MHII → MHOD(type=2) → MHNI → MHOD(type=3, filename)
        mhii_list = []
        for photo in self._photos:
            mhii_children = []

            for thumb in photo.thumbnails:
                # Build ithmb filename: ":F{format_id}_{file}.ithmb"
                ithmb_name = f":F{thumb.format_id}_{thumb.ithmb_file}.ithmb"
                fname_bytes = ithmb_name.encode("utf-8")

                # MHOD type=3 (filename string, child of MHNI)
                mhod3_hlen = 0x18
                mhod3_body = struct.pack("<IbBhI", len(fname_bytes), 1, 0, 0, 0) + fname_bytes
                # Pad to 4-byte boundary
                pad = (4 - (mhod3_hlen + len(mhod3_body)) % 4) % 4
                mhod3_body += b"\x00" * pad
                mhod3_total = mhod3_hlen + len(mhod3_body)
                mhod3_h = bytearray(mhod3_hlen)
                mhod3_h[0:4] = b"mhod"
                put32lint(mhod3_h, 4, mhod3_hlen)
                put32lint(mhod3_h, 8, mhod3_total)
                put16lint(mhod3_h, 0x0C, 3)  # mhod_type = 3 (filename)
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
                # 0x1C: vertical_padding (16-bit) = 0
                # 0x1E: horizontal_padding (16-bit) = 0
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
        mhba_list = []
        prev_album_id = 0
        for album in self._albums:
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
            # 0x3C: prev_album_id
            put32lint(mhba_h, 0x3C, prev_album_id)
            prev_album_id = album.album_id
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

        mhif_list = []
        for fmt_id in sorted(format_ids):
            total_size = 0
            for photo in self._photos:
                for thumb in photo.thumbnails:
                    if thumb.format_id == fmt_id:
                        total_size += thumb.image_size

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
        # 0x1C: next_id
        put32lint(mhfd_h, 0x1C, self._next_photo_id)
        mhfd_h[0x30] = 2  # unknown_flag1 = 2 (as libgpod sets)
        root = Record(b"mhfd", mhfd_hlen, 0)
        root.raw_header = bytes(mhfd_h)
        root.raw_bytes = bytes(mhfd_h)
        root.children = [mhsd1, mhsd2, mhsd3]
        return root

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

    def get_photo(self, photo_id: int) -> Optional[Photo]:
        for p in self._photos:
            if p.photo_id == photo_id:
                return p
        return None

    def add_photo(self, image_path: str) -> Photo:
        """Add a photo from an image file.

        Requires Pillow for image processing.

        Args:
            image_path: Path to image file.

        Returns:
            New Photo object.

        Raises:
            ArtworkError: If Pillow is not available or image can't be processed.
        """
        try:
            from PIL import Image  # type: ignore  # noqa: F401
        except ImportError:
            raise ArtworkError("Pillow is required for photo support")

        if not os.path.isfile(image_path):
            raise ArtworkError(f"Image file not found: {image_path}")

        photo = Photo()
        photo.photo_id = self._next_photo_id
        self._next_photo_id += 1
        photo.original_path = image_path

        # Generate thumbnails for each photo format
        from ..model.artwork import ARTWORK_FORMATS, create_thumbnail

        for fmt_id in PHOTO_FORMAT_IDS:
            fmt_info = ARTWORK_FORMATS.get(fmt_id)
            if not fmt_info:
                continue
            w, h, pix_fmt, bpp = fmt_info
            pixel_data = create_thumbnail(image_path, fmt_id)
            if pixel_data:
                thumb = PhotoThumbnail()
                thumb.format_id = fmt_id
                thumb.width = w
                thumb.height = h
                thumb.image_size = len(pixel_data)
                thumb.image_offset = 0  # Will be set during save
                thumb.ithmb_file = 1  # File index
                thumb._pixel_data = pixel_data
                photo.thumbnails.append(thumb)

        self._photos.append(photo)

        # Add to master album (create if missing)
        master = self.master_album
        if not master:
            master = PhotoAlbum(name="Photo Library", album_type=ALBUM_TYPE_MASTER)
            master.album_id = self._next_album_id
            self._next_album_id += 1
            self._albums.insert(0, master)
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
            photo: Photo to add.
        """
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
        """Save the photo database back to the iPod."""
        if not self._mountpoint:
            raise ArtworkError("No mount point set")

        photos_dir = os.path.join(self._mountpoint, "iPod_Control", "Photos")
        thumbs_dir = os.path.join(photos_dir, "Thumbs")
        os.makedirs(thumbs_dir, exist_ok=True)

        # Compute offsets and write .ithmb files
        ithmb_offsets: dict = {}  # format_id -> current offset
        ithmb_data: dict = {}  # format_id -> bytearray

        for photo in self._photos:
            for thumb in photo.thumbnails:
                pixel_data = getattr(thumb, "_pixel_data", None)
                if not pixel_data:
                    continue
                fmt_id = thumb.format_id
                if fmt_id not in ithmb_data:
                    ithmb_data[fmt_id] = bytearray()
                    ithmb_offsets[fmt_id] = 0
                thumb.image_offset = ithmb_offsets[fmt_id]
                ithmb_data[fmt_id].extend(pixel_data)
                ithmb_offsets[fmt_id] += len(pixel_data)

        # Write .ithmb files
        for fmt_id, data in ithmb_data.items():
            ithmb_path = os.path.join(thumbs_dir, f"F{fmt_id}_1.ithmb")
            with open(ithmb_path, "wb") as f:
                f.write(data)

        # Always rebuild root to update offsets
        self._root = self._build_root()
        if self._root is None:
            return

        data = write_artworkdb(self._root)

        os.makedirs(photos_dir, exist_ok=True)
        db_path = os.path.join(photos_dir, "Photo Database")

        with open(db_path, "wb") as f:
            f.write(data)

    def __repr__(self) -> str:
        return f"<PhotoDB {len(self._photos)} photos, {len(self._albums)} albums>"
