"""Custom exceptions for pygpod."""

from __future__ import annotations


class PyGpodError(Exception):
    """Base exception for all pygpod errors."""


class DatabaseError(PyGpodError):
    """Error parsing or writing an iPod database."""


class ParseError(DatabaseError):
    """Error parsing a binary database file."""


class WriteError(DatabaseError):
    """Error writing a binary database file."""


class DeviceError(PyGpodError):
    """Error related to iPod device detection or configuration."""


class MountPointError(DeviceError):
    """Error with iPod mount point (not found, invalid structure, etc.)."""


class HashError(PyGpodError):
    """Error computing or verifying iPod database hashes."""


class TrackError(PyGpodError):
    """Error related to track operations."""


class PlaylistError(PyGpodError):
    """Error related to playlist operations."""


class ArtworkError(PyGpodError):
    """Error related to artwork operations."""


class DependencyError(PyGpodError):
    """A required optional dependency is not installed."""


class UnsupportedFormatError(TrackError):
    """Audio/video format not supported by iPod.

    The file must be converted to a supported format (MP3, AAC, ALAC, WAV, AIFF)
    before adding to the iPod. Auto-conversion is not yet implemented.
    """
