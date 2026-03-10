# pygpod

> **Pre-alpha release.** This project is under active development. APIs may change, bugs are expected, and not all features are implemented yet. Use at your own risk.

Pure Python library for iPod database management - a spiritual successor to [libgpod](https://sourceforge.net/projects/gtkpod/).

[![License: LGPL v2.1+](https://img.shields.io/badge/License-LGPL_v2.1+-blue.svg)](https://www.gnu.org/licenses/lgpl-2.1.html)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org)

## Features

- **Pure Python** - no C dependencies, no compilation step
- **Round-trip binary fidelity** - parse and re-write iTunesDB byte-identically
- **Full CLI** - 12 commands for managing tracks, playlists, and devices
- **Smart playlists** - rule evaluation engine with 40+ fields and 20+ operators
- **Artwork support** - read/write .ithmb cover art thumbnails (with Pillow)
- **Tag reading** - automatic metadata extraction from audio files (with mutagen)
- **USB detection** - discover connected iPods (with pyusb)
- **DatabaseConfig** - fine-grained control over binary output, including libgpod-compatible mode

## Installation

pygpod is not yet published on PyPI - a PyPI release is planned once the API stabilizes (0.1.0+). For now, install directly from GitHub:

```bash
pip install "pygpod[all] @ git+https://github.com/Bionded/pygpod.git"
```

Or add to your `requirements.txt`:

```
pygpod[all] @ git+https://github.com/Bionded/pygpod.git
```

Available extras: `tags` (mutagen), `artwork` (Pillow), `usb` (pyusb), or `all` for everything.

## Quick Start

### Open a database and list tracks

```python
import pygpod

db = pygpod.Database("/mnt/ipod")

for track in db.tracks:
    print(f"{track.artist} - {track.title} ({track.album})")
```

### Add tracks

```python
db = pygpod.Database("/mnt/ipod")

track = db.add_track("/path/to/song.mp3")
print(f"Added: {track}")

# With metadata overrides
db.add_track("/path/to/other.mp3", artist="Custom Artist", title="Custom Title")

db.save()
```

### Work with playlists

```python
db = pygpod.Database("/mnt/ipod")

# Create a playlist and add tracks
playlist = db.create_playlist("Road Trip")
for track in db.tracks:
    if "rock" in track.genre.lower():
        db.add_track_to_playlist(playlist, track)

db.save()
```

### Context manager (auto-saves on exit)

```python
with pygpod.Database("/mnt/ipod") as db:
    db.add_track("/path/to/song.mp3")
    # saved automatically
```

## CLI

```bash
pygpod -m /mnt/ipod <command>          # specify mountpoint with -m flag
export PYGPOD_MOUNTPOINT=/mnt/ipod     # or set it via environment variable
```

### Commands

```
pygpod info                            # device info and database summary (alias: i)
pygpod track list                      # list all tracks (aliases: tr ls)
pygpod track add file1.mp3 file2.mp3   # add audio files to iPod
pygpod track remove <id>               # remove track by ID (aliases: tr rm)
pygpod track export <id> dest/         # export track to local path
pygpod track info <id>                 # show full track details
pygpod playlist list                   # list all playlists (aliases: pl ls)
pygpod playlist create "Name"          # create a new playlist
pygpod playlist add "Name" <id> ...    # add tracks to playlist by ID
pygpod playlist remove "Name" <id>     # remove track from playlist (aliases: pl rm)
pygpod playlist delete "Name"          # delete a playlist
pygpod init --model classic_6g         # initialize iPod directory structure
pygpod dump                            # dump raw iTunesDB record tree
pygpod fix-checksums                   # recalculate database checksums
pygpod purge --yes                     # delete all data from iPod
pygpod discover                        # auto-detect connected iPods
```

### Examples

```bash
# Add an album and create a playlist for it
pygpod -m /mnt/ipod track add ~/Music/album/*.mp3
pygpod -m /mnt/ipod playlist create "Road Trip"
pygpod -m /mnt/ipod playlist add "Road Trip" 1 2 3

# Initialize a fresh iPod volume
pygpod -m /mnt/ipod init --model classic_6g
```

## API Overview

### Database

The main entry point. Opens an iPod mount point, parses the iTunesDB, and provides methods for managing tracks and playlists.

```python
db = pygpod.Database("/mnt/ipod")
db.tracks          # list of Track objects
db.playlists       # list of Playlist objects
db.master_playlist # the library playlist
db.device          # Device info (model, capabilities)
db.add_track(path, **metadata_overrides)
db.remove_track(track, delete_file=False)
db.create_playlist(name)
db.delete_playlist(playlist)
db.add_track_to_playlist(playlist, track)
db.remove_track_from_playlist(playlist, track)
db.save()
db.save_to_file(path)
```

Use `Database.from_file(path)` to parse a standalone iTunesDB file without a mount point.

### Track

Read-only properties on a parsed MHIT record - covers 100+ fields.

```python
track.track_id       # unique ID
track.title          # string metadata (also: artist, album, genre, composer, comment, ...)
track.duration       # seconds (float)
track.duration_ms    # milliseconds (int)
track.bitrate        # kbps
track.samplerate     # Hz
track.file_size      # bytes
track.year           # release year
track.track_number   # track number in album
track.play_count     # number of plays
track.rating         # 0-100 (each star = 20)
track.ipod_path      # colon-separated iPod path
track.is_podcast     # bool
track.is_audiobook   # bool
```

### Playlist

```python
playlist.name         # playlist name
playlist.tracks       # list of Track objects
playlist.track_count  # number of tracks
playlist.is_master    # True for the library playlist
playlist.track_ids    # list of track IDs
```

### Device

Detected automatically from the iPod mount point.

```python
device = pygpod.Device.from_mountpoint("/mnt/ipod")
device.model            # "iPod Classic 6G (160GB)"
device.generation       # IpodGeneration enum
device.supports_artwork # bool
device.supports_video   # bool
device.requires_hash    # bool
device.checksum_type    # ChecksumType enum
device.firewire_guid    # GUID for hash computation
```

### DatabaseConfig

Fine-grained control over binary output format.

```python
# Default config - matches pygpod's own output
config = pygpod.DatabaseConfig()

# libgpod-compatible config - binary-identical to libgpod output
config = pygpod.DatabaseConfig.libgpod_compat()

# Custom config
config = pygpod.DatabaseConfig(random_seed=42, track_id_start=1000)

db = pygpod.Database("/mnt/ipod", config=config)
```

### Smart Playlists

Build and evaluate smart playlist rules programmatically.

```python
from pygpod import SPLRule, SPLField, SPLAction, SPLMatch, evaluate_smart_playlist

rule = SPLRule(
    field=SPLField.GENRE,
    action=SPLAction.CONTAINS,
    string="Rock",
)

matches = evaluate_smart_playlist([rule], SPLMatch.AND, db.tracks)
```

### Artwork

Requires `Pillow` (`pip install pygpod[artwork]`).

```python
artwork = pygpod.Artwork.from_image("/path/to/cover.jpg")
```

### Auto-discovery

```python
for mountpoint, device in pygpod.discover():
    print(f"{device.model} at {mountpoint}")
```

## Optional Dependencies

| Package | Extra | What it enables |
|---------|-------|-----------------|
| [mutagen](https://mutagen.readthedocs.io/) | `tags` | Read ID3/MP4/Vorbis tags when adding tracks |
| [Pillow](https://pillow.readthedocs.io/) | `artwork` | Cover art thumbnail encoding/decoding |
| [pyusb](https://pyusb.github.io/pyusb/) | `usb` | USB device detection for `discover` |

Without these, pygpod still works - you just need to provide metadata manually and artwork features are unavailable.

## Supported iPods

pygpod supports iPods that mount as **USB mass storage** devices:

- **iPod 1G-4G** - original through 4th gen click-wheel
- **iPod Photo** - color display model
- **iPod Mini** - 1G and 2G
- **iPod Shuffle** - 1G through 4G (uses iTunesSD)
- **iPod Video** - 5G and 5.5G
- **iPod Nano** - 1G through 5G
- **iPod Classic** - 1G through 3G (6G-7G overall)

Hash/checksum support:

| Checksum | Models |
|----------|--------|
| None | iPod 1G-4G, Photo, Mini, Shuffle 1G |
| hash58 (HMAC-SHA1) | iPod Video, Nano 1-4, Classic 1-3 |
| hash72 (AES-CBC) | Nano 5G |

### Tested Hardware

So far, pygpod has been tested only on **iPod Classic 1st Gen (6G) 80GB** and **iPod Classic 3st Gen (7G) 160GB (Modded)** running on **Linux**. macOS and Windows support is implemented but not tested well. If you have other iPod models and can test, please report any issues at the [issue tracker](https://github.com/Bionded/pygpod/issues).

## Known Issues

- **OTG playlists don't respond to clicks** - On-The-Go playlists created on the iPod itself (`OTGPlaylistInfo`) can be parsed via `pygpod.read_otg_playlists(mountpoint)`, but they are not fully functional. The menu items appear but clicking them does nothing on the iPod.
- **Photo management is not stable** - PhotoDB (ArtworkDB) writing works for basic cases but may produce incorrect results on some iPod models. Photos may not display correctly or at all. This feature is experimental.
- **macOS/Windows USB detection is simplified** - When pyusb is unavailable, `discover` falls back to `system_profiler` (macOS) or WMI (Windows). These parsers are simplified and may miss some devices.
- **Real serial number detection requires block device access** - The SCSI VPD page 0x80 method for reading the actual device serial (as opposed to the FireWire GUID exposed via USB descriptors) requires read access to the block device (e.g., `/dev/sdb`). You can either run `pygpod discover` as root, or add your user to the `disk` group for persistent access:
  ```bash
  sudo usermod -aG disk $USER
  # Log out and back in for the change to take effect
  ```
  This method is tested and confirmed working on Linux. macOS (`ioreg`/`diskutil`) and Windows (`wmic`/PowerShell) implementations are provided but untested - they may not return the correct serial on all devices.
- **Optional dependency warnings** - On import, pygpod warns about missing optional dependencies (mutagen, Pillow, pyusb).

## Roadmap

- [ ] Stabilize photo/artwork management
- [ ] Expand hardware testing to more iPod models
- [ ] Improve tag reading for edge cases
- [ ] Fix OTG playlist support (make them clickable and functional on device)
- [ ] Backup and restore - full database + media backup/restore to/from archive
- [ ] Auto-encode media files to iPod-compatible formats (MP3, AAC, ALAC)
- [ ] iTunesSD support improvements for Shuffle models
- [ ] Config manager - persistent per-device settings (default playlist, sync rules)
- [ ] Stable API with no breaking changes
- [ ] Comprehensive documentation
- [ ] Publish to PyPI
- [ ] *Probably* add support of apps\games, but that may be a post-1.0 feature depending on demand and complexity.

## Development

```bash
git clone https://github.com/Bionded/pygpod.git
cd pygpod

# Install in development mode
pip install -e ".[all]"

# Run tests
python -m pytest tests/

# Lint and format
ruff check pygpod/ tests/
ruff format pygpod/ tests/
```

Contributing guide and bug report templates will be added in a future release.

## Acknowledgments

pygpod is a spiritual successor to [libgpod](https://sourceforge.net/projects/gtkpod/), originally written by Jörg Schuler and made into a standalone library by Christophe Fergeau. The iPod model database, hash algorithms, and binary format knowledge are all derived from libgpod's C implementation.

## AI Disclosure

This project was developed with assistance from Claude (Anthropic) and ChatGPT (OpenAI). AI was used throughout the porting and rewriting process - sometimes for consulting on architecture decisions, sometimes for tracking down bugs. The hash/checksum implementation (hash58, hash72) relied heavily on AI assistance to port the cryptographic logic from libgpod's C code to Python. All AI-generated code was reviewed, tested, and verified against the original libgpod behavior. Maybe not perfect, but i really tried to make sure it was correct and secure.

## License

pygpod is licensed under the [GNU Lesser General Public License v2.1 or later (LGPL-2.1-or-later)](https://www.gnu.org/licenses/lgpl-2.1.html).

