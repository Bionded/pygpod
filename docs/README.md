# pygpod documentation

Pure-Python reading and writing of iPod databases (iTunesDB, ArtworkDB, Photo Database, iTunesSD),
a spiritual successor of libgpod. Start with the [project README](../README.md) for installation.

| page | covers |
|---|---|
| [database.md](database.md) | `Database` (open, add/remove tracks, save, concurrency, crash safety), `DatabaseConfig` |
| [tracks.md](tracks.md) | `Track` properties (editable and structural), chapters, Sound Check, gapless fields |
| [playlists.md](playlists.md) | `Playlist`, podcasts, smart playlists (`SPLRule`, `SPLPrefs`, evaluation) |
| [device.md](device.md) | `Device`, models and capabilities, `init_ipod`, `discover`, storage information |
| [playcounts.md](playcounts.md) | play counts recorded on the iPod, On-The-Go playlists |
| [artwork.md](artwork.md) | cover art (`set_artwork`, `Artwork`), photos (`PhotoDB`) |
| [media.md](media.md) | tag reading, audio/video conversion on import, Sound Check, gapless data |
| [maintenance.md](maintenance.md) | `check()` / `repair()` (`pygpod doctor`), backup and restore |
| [errors.md](errors.md) | exception hierarchy |
| [cli.md](cli.md) | the `pygpod` command line |

Conventions in these pages: ```` ```python ```` blocks are executed by `tests/test_docs.py` against an
emulated iPod (variables `mountpoint`, `song` = an MP3 file, `cover` = PNG bytes, `workdir` = a
scratch directory); ```` ```py ```` blocks are shown only (they need a real device). The
"Reference" sections are generated from the docstrings by `docs/gen_reference.py`.
