#!/usr/bin/env python3
"""Hardware check of a pygpod release on a real iPod: write a small test set, look at it on
the device, remove it again.

    python scripts/hardware_check.py MOUNTPOINT --backup ARCHIVE [--full-backup] [--yes]
        back up the iPod to ARCHIVE (refuses to go on if that fails), then add the test set
        (a playlist "pygpod check" and photo album "pygpod check") and print what to check
    python scripts/hardware_check.py MOUNTPOINT --otg-step [--yes]
        after 'OTG 2' and 'OTG 4' were put on the On-The-Go playlist on the device: remove
        'OTG 1' and 'OTG 3' (the remaining tracks move) and save; the save imports On-The-Go
        as the playlist 'OTG Playlist 1', which must play 2 beeps, then 4 beeps
    python scripts/hardware_check.py MOUNTPOINT --cleanup [--yes]
        remove the test tracks, their files, the playlist and the test photos
    python scripts/hardware_check.py MOUNTPOINT --restore ARCHIVE [--yes]
        put the backed-up database back and delete the test files
    python scripts/hardware_check.py MOUNTPOINT --doctor
        read-only consistency check of the database

Needs ffmpeg (with libmp3lame, and libx264 for the video tracks) and Pillow. All test sound,
video and pictures are generated (ffmpeg lavfi, Pillow); no existing media file is used. Every
test track has the grouping "pygpod-check"; nothing else on the iPod is changed. The backup holds
the database files and photo thumbnails (with --full-backup also the music and full
resolution photos). An iPod that had no photos keeps an empty Photo Database after --cleanup
or --restore.
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, List, Optional, Tuple

import pygpod
from pygpod.db.constants import MEDIATYPE_AUDIOBOOK, MEDIATYPE_PODCAST
from pygpod.gapless import GAPLESS_FIELDS

MARKER = "pygpod-check"
CREATED_PODCASTS = "pygpod hardware check (created the Podcasts playlist)"
PLAYLIST = "pygpod check"
ARTIST = "Перевірка pygpod"
RATE = 44100
OTG_ALBUM = "OTG тест"
OTG_TRACKS = 5
OTG_STEP_REMOVE = ("OTG 1", "OTG 3")
OTG_STEP_KEEP = ("OTG 2", "OTG 4")
BEEP_HZ = 880
BEEP_ON = 0.15  # seconds of tone per beep
BEEP_PERIOD = 0.5  # 0.15 s beep + 0.35 s silence
BEEP_TRACK_SECONDS = 6.0
NO_GAPLESS = {key: 0 for key in GAPLESS_FIELDS}
MIN_FREE_IPOD = 200 * 1024 * 1024
MARGIN = 32 * 1024 * 1024


class CheckError(Exception):
    """The check refuses to go on (nothing was written to the iPod after the backup failed)."""


# ---- media generation (ffmpeg, Pillow) ----


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=600)


def _encoders() -> str:
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _chirp(path: str, seconds: float, volume: float = 0.4) -> str:
    """A slowly rising tone: any gap or click between tracks cut from it is audible."""
    expr = f"{volume}*sin(2*PI*(220*t+4*t*t))"
    _ffmpeg("-f", "lavfi", "-i", f"aevalsrc={expr}:s={RATE}:d={seconds}", "-ac", "2", path)
    return path


def _split(wav: str, parts: int, out: Callable[[int], str], *codec: str) -> List[str]:
    """Cut ``wav`` into ``parts`` sample-exact pieces encoded with ``codec``."""
    import wave

    with wave.open(wav) as w:
        total = w.getnframes()
    bounds = [total * i // parts for i in range(parts + 1)]
    files = []
    for i in range(parts):
        path = out(i)
        trim = f"atrim=start_sample={bounds[i]}:end_sample={bounds[i + 1]},asetpts=N/SR/TB"
        _ffmpeg("-i", wav, "-af", trim, *codec, path)
        files.append(path)
    return files


def _tone(path: str, seconds: float, db: float, *codec: str) -> str:
    """A 440 Hz tone peaking at ``db`` dBFS."""
    expr = f"{10 ** (db / 20):.6f}*sin(2*PI*440*t)"
    _ffmpeg("-f", "lavfi", "-i", f"aevalsrc={expr}:s={RATE}:d={seconds}", "-ac", "2", *codec, path)
    return path


def _beeps(path: str, count: int, *codec: str) -> str:
    """``count`` short 880 Hz beeps, then silence: the track number is audible."""
    expr = (
        f"0.5*sin(2*PI*{BEEP_HZ}*t)*lt(mod(t\\,{BEEP_PERIOD})\\,{BEEP_ON})"
        f"*lt(t\\,{count * BEEP_PERIOD})"
    )
    _ffmpeg(
        "-f", "lavfi", "-i", f"aevalsrc={expr}:s={RATE}:d={BEEP_TRACK_SECONDS}",
        "-ac", "2", *codec, path,
    )  # fmt: skip
    return path


def _image(path: str, size: Tuple[int, int], frame=(220, 40, 40)) -> str:
    """A test picture: a coloured frame (shows cropping or letterboxing), diagonals and a
    centred square (shows distortion), a gradient (shows colour/byte order)."""
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), 128)
    d = ImageDraw.Draw(img)
    b = max(4, min(w, h) // 30)
    d.rectangle([0, 0, w - 1, h - 1], outline=frame, width=b)
    d.line([0, 0, w - 1, h - 1], fill=(255, 255, 255), width=b // 2)
    d.line([0, h - 1, w - 1, 0], fill=(255, 255, 255), width=b // 2)
    s = min(w, h) // 3
    d.rectangle([(w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2], fill=(20, 20, 20))
    img.save(path)
    return path


def _video(path: str, seconds: float, size: str, *codec: str) -> str:
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=660:sample_rate={RATE}:duration={seconds}",
        *codec, "-c:a", "aac", "-shortest", path,
    )  # fmt: skip
    return path


# ---- the test set ----


def _add_test_set(
    db: "pygpod.Database", work: str, seconds: float, log: Callable[[str], None]
) -> List[str]:
    """Add the test tracks; return the checklist lines for them."""
    device = db.device
    encoders = _encoders()
    lame = ("-c:a", "libmp3lame", "-q:a", "4")
    checks: List[str] = []
    tracks = []
    had_podcasts = any(pl.is_podcast for pl in db.playlists)

    def add(path, **kw):
        kw.setdefault("artist", ARTIST)
        t = db.add_track(path, grouping=MARKER, comment="pygpod hardware check", **kw)
        tracks.append(t)
        log(f"  added: {t.album} / {t.title}")
        return t

    def p(name):
        return os.path.join(work, name)

    wide = _image(p("wide.png"), (600, 300))
    square = _image(p("square.png"), (500, 500), frame=(40, 160, 40))

    # 1. gapless album, MP3 with LAME tags; the first track has a non-square cover
    whole = _chirp(p("gapless.wav"), 3 * seconds)
    album = "Безшовний альбом (MP3)"
    for i, f in enumerate(_split(whole, 3, lambda i: p(f"lame{i}.mp3"), *lame)):
        add(f, title=f"Обійми, частина {i + 1}", album=album, track_number=i + 1,
            total_tracks=3, gapless_album_flag=True,
            cover_art=wide if i == 0 else square)  # fmt: skip
    checks.append(
        f"Album '{album}': the 3 tracks play as one continuous rising tone, no gap or click."
    )
    checks.append(
        f"Cover of '{album}' track 1: a 2:1 picture shown whole with black bars above and "
        "below (letterboxed), not cropped or stretched; tracks 2-3: a square cover."
    )

    # 1b. control: the same cut, but no gapless data, so the gap the albums above must not
    # have is audible here
    control = "Контроль: з паузою (MP3)"
    for i, f in enumerate(_split(whole, 3, lambda i: p(f"control{i}.mp3"), *lame)):
        add(f, title=f"Обійми з паузою, частина {i + 1}", album=control, track_number=i + 1,
            total_tracks=3, gapless=NO_GAPLESS)  # fmt: skip
    checks.append(
        f"Album '{control}' (no gapless data on purpose): play it right after '{album}'; "
        "here a short gap or click between the tracks is expected, there it must not be."
    )

    # 2. the same as MP3s without LAME/Info tag: gapless='estimate'
    album = "Безшовний альбом (оцінка)"
    raw = lame + ("-write_xing", "0")
    for i, f in enumerate(_split(whole, 3, lambda i: p(f"raw{i}.mp3"), *raw)):
        add(f, title=f"Ґанок, частина {i + 1}", album=album, track_number=i + 1,
            total_tracks=3, gapless="estimate", gapless_album_flag=True)  # fmt: skip
    checks.append(f"Album '{album}' (untagged MP3, estimated): continuous, at most a faint click.")

    # 3. FLAC converted (ALAC where the iPod plays it, else AAC)
    album = "Безшовний альбом (FLAC)"
    for i, f in enumerate(_split(whole, 3, lambda i: p(f"flac{i}.flac"), "-c:a", "flac")):
        add(f, title=f"Зимородок, частина {i + 1}", album=album, track_number=i + 1,
            total_tracks=3, convert=True, gapless_album_flag=True)  # fmt: skip
    checks.append(f"Album '{album}' (FLAC converted on add): continuous, no gap.")

    # 3b. numbered beep tracks for the On-The-Go step
    for n in range(1, OTG_TRACKS + 1):
        add(_beeps(p(f"otg{n}.mp3"), n, *lame), title=f"OTG {n}", album=OTG_ALBUM,
            track_number=n, total_tracks=OTG_TRACKS)  # fmt: skip
    checks.append(
        f"Album '{OTG_ALBUM}': 'OTG n' plays n short beeps, then silence. On-The-Go: add "
        f"'{OTG_STEP_KEEP[0]}' and '{OTG_STEP_KEEP[1]}' to the On-The-Go playlist on the "
        "device, connect the iPod again, run --otg-step, eject; Playlists > 'OTG Playlist 1' "
        "must play 2 beeps, then 4 beeps (On-The-Go itself is empty: the save imported it)."
    )

    # 4. Sound Check: the same tone 20 dB apart, measured with ffmpeg
    album = "Sound Check"
    quiet = _tone(p("quiet.m4a"), seconds, -26, "-c:a", "aac")
    loud = _tone(p("loud.m4a"), seconds, -6, "-c:a", "aac")
    add(quiet, title="Тихо (-26 dB)", album=album, track_number=1, soundcheck="analyze")
    add(loud, title="Голосно (-6 dB)", album=album, track_number=2, soundcheck="analyze")
    checks.append(
        f"Album '{album}': with Settings > Sound Check ON both tracks sound about equally "
        "loud; with it OFF the first is much quieter."
    )

    # 5. audiobook with chapters
    book = p("book.m4b")
    _chirp(p("book.wav"), 3 * seconds, 0.3)
    _ffmpeg("-i", p("book.wav"), "-c:a", "aac", "-f", "ipod", book)
    step = int(seconds * 1000)
    add(book, title="Аудіокнига з розділами", album="Аудіокнига", media_type=MEDIATYPE_AUDIOBOOK,
        chapters=[(0, "Розділ 1"), (step, "Розділ 2"), (2 * step, "Розділ 3")])  # fmt: skip
    checks.append(
        "Audiobooks: 'Аудіокнига з розділами' shows chapters Розділ 1-3 (centre button / "
        "chapter list); it resumes where it was stopped."
    )

    # 6. podcast with URLs and dates
    pod = _tone(p("podcast.mp3"), seconds, -12, *lame)
    released = datetime.datetime.now() - datetime.timedelta(days=2)
    t = add(pod, title="Випуск 1: перевірка", album="Подкаст pygpod",
            media_type=MEDIATYPE_PODCAST, podcast_url="https://example.org/podcast/1.mp3",
            podcast_rss="https://example.org/podcast.rss",
            description="Тестовий випуск подкасту.", time_released=released)  # fmt: skip
    t.time_played = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    checks.append(
        "Podcasts: 'Подкаст pygpod' / 'Випуск 1: перевірка' with its description; release "
        f"date {released:%Y-%m-%d}."
    )
    checks.append(
        "Times (local time of the iPod): the new tracks are on top of Recently Added (if the "
        "model has it) with today's date; 'Випуск 1' last played about one hour before the "
        "script ran."
    )

    # 7. video
    if device is not None and device.supports_video and "libx264" in encoders:
        fits = _video(p("video.m4v"), seconds, "320x240", "-c:v", "libx264", "-profile:v",
                      "baseline", "-level", "3.0", "-pix_fmt", "yuv420p")  # fmt: skip
        add(fits, title="Відео: тестова таблиця", album="Відео pygpod")
        big = _video(p("big.mkv"), seconds, "1280x720", "-c:v", "libx264", "-pix_fmt",
                     "yuv420p")  # fmt: skip
        add(big, title="Відео: конвертоване", album="Відео pygpod", convert=True)
        checks.append(
            "Videos > Movies: 'Відео: тестова таблиця' and 'Відео: конвертоване' play with "
            "picture (moving test pattern) and sound."
        )
    elif device is not None and device.supports_video:
        log("  (no libx264 in ffmpeg: video tracks skipped)")

    if not had_podcasts and any(pl.is_podcast for pl in db.playlists):
        t.comment = CREATED_PODCASTS  # cleanup removes the Podcasts playlist again
    db.set_playlist(PLAYLIST, tracks)
    checks.append(f"Playlists > '{PLAYLIST}': all {len(tracks)} test tracks, in order, play.")
    return checks


def _add_test_photos(mountpoint: str, work: str, log: Callable[[str], None]) -> List[str]:
    from pygpod.device.artwork_formats import get_photo_formats

    device = pygpod.Device.from_mountpoint(mountpoint)
    if not get_photo_formats(device.generation):
        return []
    photos = pygpod.PhotoDB(mountpoint)
    album = photos.album_by_name(PLAYLIST)
    if album is None:  # an empty album is falsy
        album = photos.create_album(PLAYLIST)
    for name, size in (("Краєвид", (1200, 800)), ("Портрет", (800, 1200)), ("Квадрат", (900, 900))):
        photo = photos.add_photo(_image(os.path.join(work, f"{name}.jpg"), size))
        photos.add_photo_to_album(album, photo)
        log(f"  added photo: {name} {size[0]}x{size[1]}")
    photos.save()
    return [
        f"Photos > '{PLAYLIST}': 3 pictures (landscape, portrait, square) with red frame, "
        "white diagonals, dark centre square: not stretched, colours a red-green gradient "
        "over blue, thumbnails and full-screen view match."
    ]


# ---- marker lookup, cleanup ----


def _test_tracks(db: "pygpod.Database"):
    return [t for t in db.tracks if t.grouping == MARKER]


def _track_file(db: "pygpod.Database", track) -> str:
    from pygpod.utils.encoding import ipod_path_to_os

    return ipod_path_to_os(track.ipod_path, db.mountpoint) if track.ipod_path else ""


def _remove_test_photos(mountpoint: str, log: Callable[[str], None]) -> int:
    from pygpod.db.photodb import photos_dir_for

    if not os.path.isdir(photos_dir_for(mountpoint)):
        return 0
    photos = pygpod.PhotoDB(mountpoint)
    album = photos.album_by_name(PLAYLIST)
    if album is None:
        return 0
    others = {
        pid for a in photos.albums if a is not album and not a.is_master for pid in a.photo_ids
    }
    removed = 0
    for pid in list(album.photo_ids):
        photo = photos.get_photo(pid)
        if photo is not None and pid not in others:
            photos.remove_photo(photo)
            removed += 1
    photos.remove_album(album)
    photos.save()
    log(f"removed {removed} test photos and the photo album '{PLAYLIST}'")
    return removed


def otg_step(mountpoint: str, yes: bool = False, log: Callable[[str], None] = print):
    """Remove 'OTG 1' and 'OTG 3' (so the later tracks move) and save with the default
    DatabaseConfig.otg_on_save ('import'); return the titles of the playlists the save made
    from the On-The-Go playlists."""
    mountpoint = _check_mountpoint(mountpoint)
    db = pygpod.Database(mountpoint)
    pending = [[t.title for t in pl] for pl in db.pending_otg_playlists()]
    if not pending:
        raise CheckError(
            f"no On-The-Go playlist on the iPod: add '{OTG_STEP_KEEP[0]}' and "
            f"'{OTG_STEP_KEEP[1]}' to On-The-Go on the device first"
        )
    for i, titles in enumerate(pending, 1):
        log(f"On-The-Go {i} before: {', '.join(titles)}")
    remove = [t for t in _test_tracks(db) if t.album == OTG_ALBUM and t.title in OTG_STEP_REMOVE]
    if not remove:
        raise CheckError(f"{' and '.join(OTG_STEP_REMOVE)} are not on the iPod (step done?)")
    _confirm(f"Remove {', '.join(t.title for t in remove)} from {mountpoint}?", yes)
    existing = {pl.name for pl in db.playlists}
    db.remove_tracks(remove, delete_files=True)
    db.save()
    imported = [pl for pl in pygpod.Database(mountpoint).playlists if pl.name not in existing]
    for pl in imported:
        log(f"{pl.name} (imported from On-The-Go): {', '.join(t.title for t in pl.tracks)}")
    names = " / ".join(f"'{pl.name}'" for pl in imported) or "the imported playlist"
    log(f"Eject the iPod safely; Playlists > {names} must play 2 beeps, then 4 beeps.")
    return [[t.title for t in pl.tracks] for pl in imported]


def cleanup(mountpoint: str, log: Callable[[str], None] = print) -> int:
    """Remove the test tracks (and files), the playlist and the test photos."""
    db = pygpod.Database(mountpoint)
    tracks = _test_tracks(db)
    podcasts = any(t.comment == CREATED_PODCASTS for t in tracks)
    if tracks:
        db.remove_tracks(tracks, delete_files=True)
    db.delete_playlist_by_name(PLAYLIST)
    for pl in db.playlists:
        if podcasts and pl.is_podcast and not pl.tracks:
            db.delete_playlist(pl)
        elif pl.name.startswith("OTG Playlist") and not pl.tracks:  # imported by --otg-step
            db.delete_playlist(pl)
    db.save()
    log(f"removed {len(tracks)} test tracks and the playlist '{PLAYLIST}'")
    _remove_test_photos(mountpoint, log)
    return len(tracks)


# ---- safety checks, backup ----


def _check_mountpoint(mountpoint: str) -> str:
    mountpoint = os.path.abspath(mountpoint)
    if not pygpod.validate_mountpoint(mountpoint):
        raise CheckError(f"not an iPod mount point (no iPod_Control/iTunes): {mountpoint}")
    return mountpoint


def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    return os.path.commonpath([path, directory]) == directory


def _tree_size(path: str, skip_media: bool) -> int:
    from pygpod.backup_archive import _is_media

    total = 0
    for d, dirs, files in os.walk(path):
        rel = os.path.relpath(d, path).replace(os.sep, "/")
        if skip_media:
            dirs[:] = [x for x in dirs if not _is_media(x if rel == "." else f"{rel}/{x}")]
        total += sum(os.path.getsize(os.path.join(d, f)) for f in files)
    return total


def make_backup(mountpoint: str, archive: str, full: bool, log: Callable[[str], None]):
    from pygpod.backup_archive import BackupError, verify
    from pygpod.device.mountpoint import _find_ipod_control

    if _inside(archive, mountpoint):
        raise CheckError("the backup archive must not be on the iPod itself")
    if os.path.exists(archive):
        raise CheckError(f"the backup archive exists already: {archive}")
    out_dir = os.path.dirname(os.path.abspath(archive))
    if not os.path.isdir(out_dir):
        raise CheckError(f"no such directory for the backup: {out_dir}")
    ctl = _find_ipod_control(pathlib.Path(mountpoint))
    from pygpod.db.photodb import photos_dir_for

    photos = photos_dir_for(mountpoint)
    needed = _tree_size(str(ctl), skip_media=not full) + MARGIN
    if os.path.isdir(photos):  # archived too (full resolution photos only with --full-backup)
        needed += _tree_size(photos, skip_media=False)
    free = shutil.disk_usage(out_dir).free
    if needed > free:
        raise CheckError(
            f"not enough space for the backup in {out_dir}: need {needed}, {free} free"
        )
    log(f"backing up {mountpoint} to {archive} ({'with' if full else 'without'} music) ...")
    try:
        info = pygpod.backup(mountpoint, archive, include_media=full)
        checked = verify(archive)
    except (BackupError, OSError) as e:
        raise CheckError(f"backup failed, nothing was written to the iPod: {e}") from e
    if checked.files != info.files:
        raise CheckError("the backup does not verify, nothing was written to the iPod")
    log(f"backup verified: {len(info.files)} files, {info.total_size} bytes")
    return info


def _confirm(question: str, yes: bool) -> None:
    if yes:
        return
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        raise CheckError("cancelled")


def _doctor(db: "pygpod.Database", log: Callable[[str], None]) -> bool:
    report = db.check()
    log(f"doctor: {report.summary()}")
    for issue in list(report)[:20]:
        log(f"  {issue}")
    return report.ok


# ---- commands ----


def write_test_set(
    mountpoint: str,
    archive: str,
    full_backup: bool = False,
    seconds: float = 8.0,
    yes: bool = False,
    log: Callable[[str], None] = print,
) -> List[str]:
    """Back up, then write the test set. Returns the checklist."""
    mountpoint = _check_mountpoint(mountpoint)
    if shutil.which("ffmpeg") is None:
        raise CheckError("ffmpeg is needed to make the test files")
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise CheckError("Pillow is needed (pip install 'pygpod[artwork]')") from None
    db = pygpod.Database(mountpoint)
    if _test_tracks(db) or db.get_playlist(PLAYLIST):
        raise CheckError("the test set is on the iPod already: run --cleanup first")
    device = db.device
    model = device.generation.name if device is not None else "unknown model"
    log(f"iPod at {mountpoint}: {model}, {len(db.tracks)} tracks")
    if shutil.disk_usage(mountpoint).free < MIN_FREE_IPOD:
        raise CheckError("less than 200 MB free on the iPod")
    _doctor(db, log)
    _confirm(f"Back up and write the test set to {mountpoint}?", yes)
    make_backup(mountpoint, archive, full_backup, log)

    work = tempfile.mkdtemp(prefix="pygpod-check-")
    try:
        db = pygpod.Database(mountpoint)
        log("making and adding the test tracks ...")
        checks = _add_test_set(db, work, seconds, log)
        db.save()
        log("adding test photos ...")
        checks += _add_test_photos(mountpoint, work, log)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _doctor(pygpod.Database(mountpoint), log)
    log("")
    log("Eject the iPod safely, then check on the device:")
    for i, line in enumerate(checks, 1):
        log(f"  {i}. {line}")
    log("")
    log(f"Afterwards: --cleanup removes the test set, --restore {archive} puts the database back.")
    return checks


def _format_version(archive: str) -> int:
    import json
    import tarfile

    from pygpod.backup_archive import MANIFEST_NAME

    with tarfile.open(archive) as tar:
        f = tar.extractfile(MANIFEST_NAME)
        return int(json.loads(f.read()).get("format_version", 1)) if f else 1


def restore_backup(mountpoint: str, archive: str, yes: bool = False, log=print) -> None:
    """Put the backed-up database back; delete the test tracks' files and thumbnails."""
    from pygpod.backup_archive import verify

    mountpoint = _check_mountpoint(mountpoint)
    info = verify(archive)
    log(f"backup {archive}: {len(info.files)} files, made {info.created}")
    _confirm(f"Restore it onto {mountpoint}?", yes)
    try:
        db = pygpod.Database(mountpoint)
        ours = [_track_file(db, t) for t in _test_tracks(db)]
    except pygpod.PyGpodError:
        ours = []
    _remove_test_photos(mountpoint, log)
    pygpod.restore(archive, mountpoint, include_media=info.include_media)
    db = pygpod.Database(mountpoint)
    kept = {os.path.normcase(_track_file(db, t)) for t in db.tracks}
    removed = 0
    for path in ours:
        if path and os.path.normcase(path) not in kept and os.path.isfile(path):
            os.unlink(path)
            removed += 1
    # thumbnails of the test covers and photos: .ithmb files the backup does not have
    from pygpod.db.photodb import photos_dir_for
    from pygpod.device.mountpoint import _find_ci, _find_ipod_control

    ctl = _find_ipod_control(pathlib.Path(mountpoint))
    artwork = _find_ci(ctl, "Artwork") if ctl else None
    # only archives of format 2 hold <mount>/Photos; with an older one the Photo Database was
    # not restored and still refers to its thumbnails
    thumbs = (
        _find_ci(pathlib.Path(photos_dir_for(mountpoint)), "Thumbs")
        if _format_version(archive) >= 2
        else None
    )
    archived = {f.path.lower() for f in info.files}
    for directory, prefix in ((artwork, "ipod_control/artwork/"), (thumbs, "photos/thumbs/")):
        if directory is None or not directory.is_dir():
            continue
        for name in sorted(os.listdir(str(directory))):
            if name.lower().endswith(".ithmb") and prefix + name.lower() not in archived:
                os.unlink(os.path.join(str(directory), name))
                removed += 1
    log(f"restored; deleted {removed} test files")
    _doctor(db, log)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mountpoint", help="the iPod's mount point (required, never guessed)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backup", metavar="ARCHIVE", help="back up, then write the test set")
    mode.add_argument(
        "--otg-step", action="store_true", help="remove OTG 1 and OTG 3 (On-The-Go check)"
    )
    mode.add_argument("--cleanup", action="store_true", help="remove the test set")
    mode.add_argument("--restore", metavar="ARCHIVE", help="restore the backup")
    mode.add_argument("--doctor", action="store_true", help="read-only database check")
    ap.add_argument("--full-backup", action="store_true", help="back up the music files too")
    ap.add_argument("--seconds", type=float, default=8.0, help="length of each test track")
    ap.add_argument("--yes", action="store_true", help="do not ask before writing")
    args = ap.parse_args(argv)
    try:
        if args.backup:
            write_test_set(args.mountpoint, args.backup, args.full_backup, args.seconds, args.yes)
        elif args.otg_step:
            otg_step(args.mountpoint, args.yes)
        elif args.cleanup:
            mp = _check_mountpoint(args.mountpoint)
            _confirm(f"Remove the test set from {mp}?", args.yes)
            cleanup(mp)
            _doctor(pygpod.Database(mp), print)
        elif args.restore:
            restore_backup(args.mountpoint, args.restore, args.yes)
        else:
            ok = _doctor(pygpod.Database(_check_mountpoint(args.mountpoint)), print)
            return 0 if ok else 1
    except CheckError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
