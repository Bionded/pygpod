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

Properties on a parsed MHIT record - covers 100+ fields. Most metadata fields have setters.

#### Editable string properties

```python
track.title = "New Title"
track.artist = "New Artist"
track.album = "New Album"
track.genre = "Rock"
track.composer = "Someone"
track.comment = "My note"
track.albumartist = "Various Artists"
track.grouping = "Group"
track.description = "Desc"
track.subtitle = "Sub"
track.category = "Category"
track.keywords = "tag1 tag2"
track.podcast_url = "https://..."
track.podcast_rss = "https://..."
track.tvshow = "Show Name"
track.tvepisode = "S01E01"
track.tvnetwork = "Network"
# Sort keys (override default sort order)
track.sort_title = "title for sorting"
track.sort_artist = "artist for sorting"
track.sort_album = "album for sorting"
track.sort_albumartist = "albumartist for sorting"
track.sort_composer = "composer for sorting"
track.sort_tvshow = "tvshow for sorting"
```

#### Editable numeric properties

```python
track.rating = 80             # 0-100 (each star = 20)
track.rating_stars = 4        # 0-5 (convenience setter)
track.play_count = 10
track.track_number = 3
track.total_tracks = 12
track.year = 2024
track.cd_number = 1
track.total_cds = 2
track.bpm = 120
track.volume = 50             # -255 to 255
track.start_time = 5000       # ms (partial playback start)
track.stop_time = 180000      # ms (partial playback end)
track.season_number = 1
track.episode_number = 5
track.media_type = 0x0004     # auto-updates type1/type2/podcast flags
track.compilation = True
track.skip_when_shuffling = True
track.remember_position = True
track.mark_unplayed = True
```

#### Read-only properties (structural)

```python
track.track_id       # unique ID assigned on add
track.dbid           # database ID
track.ipod_path      # colon-separated iPod path
track.filetype_marker # 4-byte file type code
track.filetype_str   # "MPEG audio file" etc.
track.file_size      # bytes
track.bitrate        # kbps
track.samplerate     # Hz
track.duration       # seconds (float)
track.duration_ms    # milliseconds (int)
track.is_podcast     # bool (derived from media_type)
track.is_audiobook   # bool
track.is_video       # bool
track.has_artwork    # bool
track.time_added     # datetime
track.time_modified  # datetime
track.time_played    # datetime
```

### Playlist

```python
playlist.name = "New Name"  # editable
playlist.tracks             # list of Track objects
playlist.track_count        # number of tracks
playlist.track_ids          # list of track IDs
playlist.is_master          # True for the library playlist (read-only)
playlist.is_podcast         # bool (read-only)
playlist.is_smart           # bool (read-only)
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

#### Storage info

```python
# Quick check (no filesystem scan)
si = device.storage_info(full=False)
print(f"Free: {si.free / 1e9:.1f} GB of {si.total / 1e9:.1f} GB")

# Full breakdown (scans Music, Artwork, etc.)
si = device.storage_info()  # full=True by default
print(si)
# Total:              74.4 GB
# Used:               35.2 GB
# Free:               39.2 GB
#
#   Music files:      28.5 GB
#   Artwork (.ithmb): 1.9 GB
#   ArtworkDB:        5.9 MB
#   iTunesDB:         61.0 MB
#   Other/System:     4.8 GB

# Or scan later
si = device.storage_info(full=False)
si.scan(device.mountpoint)  # populate categories on demand
```

### DatabaseConfig

Fine-grained control over binary output format.

```python
# Default config - libgpod-compatible with auto date_added
config = pygpod.DatabaseConfig()

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

