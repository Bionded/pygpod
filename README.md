# pygpod

Pure-Python library and command line tool for iPods that mount as USB mass storage: it reads and
writes the iPod's music database (iTunesDB), cover art (ArtworkDB), photos (Photo Database) and the
shuffle's iTunesSD. A spiritual successor to [libgpod](https://sourceforge.net/projects/gtkpod/):
same file formats, same output, no C code.

[![License: LGPL v2.1+](https://img.shields.io/badge/License-LGPL_v2.1+-blue.svg)](https://www.gnu.org/licenses/lgpl-2.1.html)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org)

> **Beta.** The file formats are checked against libgpod and on real iPods (see
> [Supported iPods](#supported-ipods)), but not every model has been tried on hardware. Keep a
> backup (`pygpod backup`) before writing to an iPod you care about.

## Features

**Database**
- Parse and re-write iTunesDB and ArtworkDB byte-identically (also those written by iTunes or
  libgpod); new data is written the way libgpod writes it (checked against libgpod itself in the
  test suite). A Photo Database written by pygpod round-trips byte-identically too; one written by
  iTunes or libgpod is rewritten in pygpod's (libgpod-readable) layout on its first save.
- Checksums per model like libgpod: hash58 (Classic, nano 3G/4G), hash72, unsigned databases for
  older models; an unidentified iPod is signed like the database already on it.
- Crash-safe saves (all database files are committed together through a journal), one writer at a
  time (lock file, `ConflictError` when someone else saved meanwhile).
- iTunesSD for the iPod shuffle: version 1 (1G/2G) and version 2 (3G/4G), written on every save.

**Tracks**
- Tags read from MP3, AAC/ALAC (M4A/M4B), FLAC, Ogg and others (mutagen): titles, sort fields,
  compilation, rating, BPM, grouping, explicit/clean, release date, podcast fields, embedded cover.
  Keyword arguments of `add_track()` always win over the tags.
- The file's *content* is checked against what the model plays; `convert=True` converts the rest
  with ffmpeg (Apple Lossless where the iPod plays it, else AAC; video to H.264 Baseline + AAC).
- Sound Check from iTunNORM / ReplayGain tags, or measured with ffmpeg.
- Gapless playback data from LAME/Info tags, iTunSMPB and MP4 edit lists; an estimate for files
  without them (experimental).
- Chapters of audiobooks and podcasts (MP4 chapter track, Nero `chpl`, ID3 `CHAP`).
- Video: movies, music videos, TV shows (with show/season/episode fields).

**Playlists, play counts, artwork, photos**
- Playlists, podcast playlists and smart playlists (rules evaluated like libgpod, live ones
  refreshed on save).
- Play counts, skips, ratings and bookmarks recorded on the iPod are merged on open.
- On-The-Go playlists made on the iPod are imported as "OTG Playlist n" on save, like iTunes.
- Cover art and photos in the iPod's own formats (from its SysInfoExtended, else a per-model
  table), letterboxed like libgpod.
- Timestamps are the iPod's local time, like iTunes and libgpod.

**Maintenance**
- `doctor`: a read-only consistency check (missing/orphan files, broken artwork or playlist
  references, wrong lengths, photo database problems, checksum, free space ...) and a repair.
- Backup and restore of the whole iPod (database, artwork, photos, optionally the media) to a tar
  archive, verified before anything is written.
- `init_ipod()`: create the folder structure and an empty, correctly signed database.

Documentation: [docs/](docs/README.md) has one page per area with runnable examples and an API
reference generated from the docstrings. Changes: [CHANGELOG.md](CHANGELOG.md).

## Installation

```bash
pip install "pygpod[all]"
```

| extra | package | needed for |
|---|---|---|
| `tags` | [mutagen](https://mutagen.readthedocs.io/) | reading tags, cover art, chapters and gapless data from files |
| `artwork` | [Pillow](https://pillow.readthedocs.io/) | cover art and photos |
| `usb` | [pyusb](https://pyusb.github.io/pyusb/) | finding iPods on USB (`discover`, model/GUID when SysInfo lacks them) |
| `all` | all of the above, plus [tzdata](https://pypi.org/project/tzdata/) on Windows | |

Without the extras pygpod still reads and writes databases; you pass the metadata yourself and
artwork is not available (pygpod warns on import about what is missing). `ffmpeg` and `ffprobe`
are needed only for conversion (`convert=True`), Sound Check analysis and the gapless estimate.
Python 3.8 or newer; Linux, macOS and Windows. Windows has no time zone database of its own:
without `tzdata` (in `all`, or `pip install tzdata`; Python 3.9+) a city time zone set on a Classic
or nano 3G/4G cannot be resolved and pygpod uses the computer's UTC offset for the iPod's timestamps.

## Quick start

```python
import pygpod

db = pygpod.Database("/mnt/ipod")             # the iPod's mount point

for track in db.tracks:
    print(track.artist, "-", track.title, f"({track.album})")

# Tags are read from the file; keyword arguments override them.
track = db.add_track("/path/to/song.mp3", artist="Океан Ельзи", album="Земля", year=2013)
track.rating = 80                             # 0-100, 20 per star

road_trip = db.create_playlist("У дорогу")
db.add_track_to_playlist(road_trip, track)
db.set_playlist("Обране", [track])            # create or replace a playlist's members

db.save()                                     # nothing is written before save()
```

The context manager saves on a clean exit:

```python
with pygpod.Database("/mnt/ipod") as db:
    db.tracks[0].play_count = 3
```

Files the iPod cannot play, Sound Check measured with ffmpeg, the gapless estimate, explicit
chapters and video fields (these need ffmpeg or real media files):

```py
db.add_track("/path/to/song.flac", convert=True)                      # -> ALAC or AAC
db.add_track("/path/to/movie.mkv", convert=True, video_size="large")  # -> H.264 .m4v
db.add_track("/path/to/live.mp3", soundcheck="auto", gapless="estimate", gapless_album_flag=True)
db.add_track("/path/to/book.m4b", chapters=[(0, "Вступ"), (90000, "Розділ 1")])
db.add_track("/path/to/episode.m4v", tvshow="Show", season_number=1, episode_number=3)
```

Cover art, photos, smart playlists, check and backup:

```python
from pygpod import SPLAction, SPLField, SPLRule

db = pygpod.Database("/mnt/ipod")
db.set_artwork(db.tracks[0], open("/path/to/cover.png", "rb").read())
db.create_smart_playlist("Rock", [SPLRule(field=SPLField.GENRE, action=SPLAction.CONTAINS,
                                          string="Rock")])
db.save()

photos = pygpod.PhotoDB("/mnt/ipod")
album = photos.create_album("Літо")
photos.add_photo_to_album(album, photos.add_photo("/path/to/cover.png"))
photos.save()

report = pygpod.Database("/mnt/ipod").check()  # read-only; repair() fixes and saves
print(report.summary())
pygpod.backup("/mnt/ipod", "ipod.tar.gz", include_media=False)
```

More in [docs/](docs/README.md): [database](docs/database.md), [tracks](docs/tracks.md),
[playlists](docs/playlists.md), [device and models](docs/device.md),
[play counts and On-The-Go](docs/playcounts.md), [artwork and photos](docs/artwork.md),
[tags and conversion](docs/media.md), [check, backup, restore](docs/maintenance.md),
[errors](docs/errors.md), [command line](docs/cli.md).

## Command line

```bash
export PYGPOD_MOUNTPOINT=/mnt/ipod     # or pass -m /mnt/ipod to every command

pygpod info                            # device and database summary
pygpod track list                      # list tracks (track info <id>: every field)
pygpod track add ~/Music/album/*.mp3   # add files (tags read; --title, --artist ... override)
pygpod track remove <id>               # remove a track
pygpod track export <id> dest/         # copy a track's file off the iPod
pygpod playlist create "У дорогу"      # playlist list / add / remove / delete
pygpod playlist add "У дорогу" 1 2 3
pygpod doctor                          # read-only check (exit 1 = issues found)
pygpod doctor --fix                    # repair; --delete-orphans also deletes unreferenced files
pygpod backup ipod.tar.gz              # --no-media: database, artwork and photos only
pygpod restore ipod.tar.gz --dry-run   # verify and plan; without --dry-run: restore
pygpod init --model classic_6g         # create the folder structure and an empty database
pygpod discover                        # find connected iPods
pygpod dump                            # raw iTunesDB record tree
pygpod fix-checksums                   # re-sign the database
pygpod purge --yes                     # delete all music and the database
```

Every command and option: [docs/cli.md](docs/cli.md).

## Supported iPods

pygpod works with iPods that mount as USB mass storage. iPod touch, iPhone and iPad are not
supported (they do not expose the database as files).

| model | database | checksum | cover art / photos / video |
|---|---|---|---|
| iPod 1G-4G, mini 1G/2G | read, write | none | - / - / - |
| iPod Photo (4G color) | read, write | none | yes / yes / - |
| iPod Video 5G/5.5G | read, write | none (like libgpod and iTunes) | yes / yes (incl. TV-out) / yes |
| nano 1G/2G | read, write | none (like libgpod and iTunes) | yes / yes / - |
| nano 3G/4G | read, write | hash58 | yes / yes / yes |
| Classic 6G/6.5G/7G | read, write | hash58 | yes / yes / yes |
| shuffle 1G/2G, 3G/4G | read, write + iTunesSD v1 / v2 | none | - / - / - |
| nano 5G | **not supported** (see below) | hash72 | |
| nano 6G/7G | **not supported** | hashAB (nano 7G: unidentified model) | |

hash58 needs the iPod's FirewireGuid, which pygpod reads from `iPod_Control/Device/SysInfo` or,
when that file lacks it, from USB (`usb` extra). The model comes from SysInfo/SysInfoExtended the
same way; an iPod pygpod cannot identify is signed like the database already on it.

**Tested on real hardware:** iPod Classic 6G 80 GB and Classic 7G 160 GB (modded), on Linux. The
other models are tested on emulated volumes against libgpod. macOS and Windows support (mount
points, USB lookups) is implemented but little tested. Reports for other models are welcome at the
[issue tracker](https://github.com/Bionded/pygpod/issues).

## Known limitations

- **nano 5G**: the firmware reads its library from SQLite databases (`iTunesCDB`, `*.itdb`), which
  pygpod does not write; `save()` warns. The hash72 signature itself is implemented and needs
  `iPod_Control/Device/HashInfo` or a database signed by iTunes once.
- **nano 6G/7G** need the hashAB signature (and SQLite databases), which pygpod does not write.
  On a nano 6G `save()` raises `HashError`; pygpod has no model entry for the nano 7G, so it is
  handled like any unidentified iPod (signed like the database on it, else `HashError`).
- **Gapless estimate** (`gapless="estimate"`) is experimental; files with exact gapless data always
  use it.
- **On-The-Go**: by default a save imports the iPod's On-The-Go lists and deletes them from the
  iPod, like iTunes (`DatabaseConfig(otg_on_save="keep")` keeps them; the Classic empties a kept
  list anyway once the database changed).
- **Several iPods connected**: when SysInfo lacks the model or FirewireGuid, pygpod asks USB only
  for the iPod behind the mount point. If that mapping fails and several iPods are connected,
  nothing is guessed: a signed iPod's `save()` raises `HashError` (create SysInfo, or connect one
  iPod at a time). The macOS/Windows mapping is untested on real systems.
- **Real serial number**: reading it over SCSI (VPD page 0x80) needs read access to the block
  device (root, or the `disk` group on Linux). Tested on Linux only.
- **`init_ipod(reset=True)`** deletes everything under `iPod_Control/iTunes`, including iTunes'
  `IC-Info.sidb`; the SysInfo files are kept unless `keep_sysinfo=False`.

## Roadmap

- Confirm more models on real hardware (iPod Video, nano 3G/4G, shuffles, iPod Photo).
- iPod nano 5G-7G: the SQLite databases and the hashAB signature - see
  [docs/research/hashab-hash72.md](docs/research/hashab-hash72.md).
- *Maybe* games: only copying a user's own purchased games looks possible - see
  [docs/research/ipod-games.md](docs/research/ipod-games.md).

## Development

```bash
git clone https://github.com/Bionded/pygpod.git
cd pygpod
pip install -e ".[all]" pytest pytest-xdist ruff

python -m pytest tests -n 3              # tests (-m "not slow" skips the benchmarks)
PYGPOD_REQUIRE_LIBGPOD=1 python -m pytest tests -m libgpod   # libgpod interop: fail, not skip
ruff check pygpod tests && ruff format --check pygpod tests
python docs/gen_reference.py             # regenerate the API reference in docs/
```

The libgpod interop tests (marker `libgpod`) build a small C tool against libgpod
(`integration_tests/libgpod_tool.c`; needs gcc, pkg-config and libgpod-dev) and compare what
both libraries write and read; they are skipped when libgpod is missing.

## Acknowledgments

pygpod is a spiritual successor to [libgpod](https://sourceforge.net/projects/gtkpod/), originally
written by Jörg Schuler and made into a standalone library by Christophe Fergeau. The iPod model
database, hash algorithms, and binary format knowledge are all derived from libgpod's C
implementation.

## AI Disclosure

This project was developed with assistance from Claude (Anthropic) and ChatGPT (OpenAI). AI was used throughout the porting and rewriting process - sometimes for consulting on architecture decisions, sometimes for tracking down bugs. The hash/checksum implementation (hash58, hash72) relied heavily on AI assistance to port the cryptographic logic from libgpod's C code to Python. All AI-generated code was reviewed, tested, and verified against the original libgpod behavior. Maybe not perfect, but i really tried to make sure it was correct and secure.

## License

pygpod is licensed under the [GNU Lesser General Public License v2.1 or later (LGPL-2.1-or-later)](https://www.gnu.org/licenses/lgpl-2.1.html).
