"""Photo model - placeholder for photo album support."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Photo:
    """Represents a photo on the iPod."""

    def __init__(self) -> None:
        self.id: int = 0
        self.filename: str = ""
        self.width: int = 0
        self.height: int = 0


class PhotoAlbum:
    """Represents a photo album on the iPod."""

    def __init__(self) -> None:
        self.name: str = ""
        self.photos: list = []
