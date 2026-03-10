"""Plist parsing utilities for SysInfoExtended."""

from __future__ import annotations

import logging
import plistlib
from typing import Any, Dict

logger = logging.getLogger(__name__)


def read_plist(path: str) -> Dict[str, Any]:
    """Read a plist file and return its contents as a dictionary.

    Args:
        path: Path to the plist file.

    Returns:
        Dictionary of plist contents.
    """
    logger.debug("Reading plist: %s", path)
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        logger.warning("Failed to read plist: %s", path, exc_info=True)
        return {}
