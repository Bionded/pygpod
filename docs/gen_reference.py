"""Regenerate the reference sections of the docs/*.md pages from pygpod's docstrings.

Each page keeps its hand-written text; the part between ``<!-- reference:start -->`` and
``<!-- reference:end -->`` is generated here (signatures, docstrings, property tables, enum
members, CLI help). Every documented module uses ``from __future__ import annotations``, so
signatures look the same on every Python version. Only argparse formats ``--help`` differently
before Python 3.13: there ``--check`` skips cli.md. Run from the repository root:

    python docs/gen_reference.py           # rewrite the pages
    python docs/gen_reference.py --check   # exit 1 if a page is out of date (tests use it)
"""

import contextlib
import enum
import inspect
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["COLUMNS"] = "100"  # argparse wraps --help output to the terminal width

import pygpod  # noqa: E402
from pygpod import chapters, convert, gapless, tags  # noqa: E402

START, END = "<!-- reference:start -->", "<!-- reference:end -->"

PAGES = {
    "database.md": [pygpod.Database, pygpod.DatabaseConfig],
    "tracks.md": [pygpod.Track, pygpod.ChapterData, pygpod.Chapter],
    "playlists.md": [
        pygpod.Playlist,
        pygpod.SPLRule,
        pygpod.SPLPrefs,
        pygpod.SPLMatch,
        pygpod.SPLField,
        pygpod.SPLAction,
        pygpod.SPLLimitType,
        pygpod.SPLLimitSort,
        pygpod.evaluate_smart_playlist,
        pygpod.apply_limit,
    ],
    "device.md": [pygpod.Device, pygpod.discover, pygpod.init_ipod, pygpod.validate_mountpoint],
    "playcounts.md": [
        pygpod.read_play_counts,
        pygpod.PendingPlayCount,
        pygpod.read_otg_playlists,
        pygpod.OTGSkippedEntry,
    ],
    "artwork.md": [pygpod.Artwork, pygpod.PhotoDB, pygpod.Photo, pygpod.PhotoAlbum],
    "media.md": [
        convert.convert_for_ipod,
        convert.convert_video_for_ipod,
        convert.video_profile,
        convert.VideoProfile,
        convert.probe_video,
        convert.video_problems,
        convert.video_needs_conversion,
        tags.read_tags,
        tags.read_soundcheck,
        tags.soundcheck_from_gain,
        tags.analyze_soundcheck,
        tags.analyze_gain_db,
        tags.mp4_has_video,
        gapless.read_gapless,
        gapless.estimate_mp3_gapless,
        gapless.mp3_stream_info,
        gapless.estimate_mp4_gapless,
        gapless.estimate_gapless,
        chapters.read_chapters,
    ],
    "maintenance.md": [
        pygpod.backup,
        pygpod.restore,
        pygpod.BackupInfo,
        pygpod.RestoreResult,
        pygpod.CheckReport,
        pygpod.Issue,
        pygpod.RepairAction,
    ],
    "errors.md": [
        pygpod.PyGpodError,
        pygpod.DatabaseError,
        pygpod.ParseError,
        pygpod.WriteError,
        pygpod.ConflictError,
        pygpod.DeviceError,
        pygpod.MountPointError,
        pygpod.HashError,
        pygpod.TrackError,
        pygpod.PlaylistError,
        pygpod.ArtworkError,
        pygpod.ArtworkDBError,
        pygpod.DependencyError,
        pygpod.UnsupportedFormatError,
        pygpod.UnsafePathError,
        pygpod.BackupError,
    ],
}
CLI_COMMANDS = [
    [],
    ["info"],
    ["track", "list"],
    ["track", "add"],
    ["track", "remove"],
    ["track", "export"],
    ["track", "info"],
    ["playlist", "list"],
    ["playlist", "create"],
    ["playlist", "add"],
    ["playlist", "remove"],
    ["playlist", "delete"],
    ["init"],
    ["dump"],
    ["fix-checksums"],
    ["purge"],
    ["discover"],
    ["doctor"],
    ["backup"],
    ["restore"],
]


def _qualname(obj) -> str:
    mod = obj.__module__
    if obj is getattr(pygpod, obj.__name__, None):
        mod = "pygpod"
    return f"{mod}.{obj.__qualname__}"


def _doc(obj) -> str:
    return inspect.cleandoc(obj.__doc__) if obj.__doc__ else ""


def _sig(obj) -> str:
    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"
    return re.sub(r"\"'([^']*)'\"", r"'\1'", sig)  # "'Track'" (string annotation) -> 'Track'


def _code(text: str) -> str:
    return f"```text\n{text}\n```\n" if text else ""


def _function(obj, level: str = "###") -> str:
    out = f"{level} `{_qualname(obj)}{_sig(obj)}`\n\n"
    return out + _code(_doc(obj))


def _class(cls) -> str:
    out = f"### `{_qualname(cls)}`\n\n"
    if issubclass(cls, enum.Enum):
        # Python < 3.11 gives enums without a docstring "An enumeration."
        default = cls.__doc__ in (enum.Enum.__doc__, "An enumeration.")
        out += _code(_doc(cls)) if not default else ""
        out += "| member | value |\n|---|---|\n"
        out += "".join(f"| `{m.name}` | `{m.value!r}` |\n" for m in cls)
        return out + "\n"
    if issubclass(cls, BaseException):
        bases = ", ".join(f"`{b.__name__}`" for b in cls.__bases__)
        return out + f"Base: {bases}\n\n" + _code(_doc(cls))
    if hasattr(cls, "_fields"):  # NamedTuple
        out += f"Fields: `{', '.join(cls._fields)}`\n\n"
        doc = _doc(cls)
        if doc and not doc.startswith(cls.__name__ + "("):
            out += _code(doc)
    else:
        out += f"`{cls.__name__}{_sig(cls)}`\n\n" + _code(_doc(cls))
    props, methods = [], []
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            props.append((name, member))
        elif isinstance(member, (staticmethod, classmethod)):
            methods.append((name, member.__func__))
        elif inspect.isfunction(member):
            methods.append((name, member))
    fields = getattr(cls, "__dataclass_fields__", None)
    if fields:
        out += "| field | type | default |\n|---|---|---|\n"
        for f in fields.values():
            default = f.default if f.default is not inspect._empty else ""
            out += f"| `{f.name}` | `{f.type}` | `{default!r}` |\n"
        out += "\n"
    if props:
        out += "| property | type | settable | description |\n|---|---|---|---|\n"
        for name, p in props:
            ret = ""
            if p.fget is not None:
                ann = getattr(p.fget, "__annotations__", {}).get("return", "")
                ret = ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))
            first = (_doc(p.fget).splitlines() or [""])[0] if p.fget else ""
            first = first.replace("|", "\\|")
            out += f"| `{name}` | `{ret}` | {'yes' if p.fset else ''} | {first} |\n"
        out += "\n"
    for name, fn in methods:
        if hasattr(cls, "_fields") and name in ("count", "index"):
            continue
        out += _function(fn, "####").replace(f"{fn.__module__}.", "", 1)
    return out


def _cli() -> str:
    from pygpod.cli import main

    out = ""
    for cmd in CLI_COMMANDS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
            main([*cmd, "--help"])
        text = re.sub(r"usage: \S+", "usage: pygpod", buf.getvalue().rstrip())
        out += (
            f"### `pygpod {' '.join(cmd)}`\n\n```text\n{text}\n```\n\n"
            if cmd
            else (f"### `pygpod`\n\n```text\n{text}\n```\n\n")
        )
    return out


def generate(page: str) -> str:
    if page == "cli.md":
        body = _cli()
    else:
        body = ""
        for obj in PAGES[page]:
            body += _class(obj) if inspect.isclass(obj) else _function(obj)
            body += "\n"
    note = "_Generated by `docs/gen_reference.py` from the docstrings._"
    return f"{START}\n\n## Reference\n\n{note}\n\n{body.rstrip()}\n\n{END}"


def update(path: str, check: bool) -> bool:
    """Rewrite the reference section of ``path``; True if it was already up to date."""
    page = os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if START not in text or END not in text:
        raise SystemExit(f"{page}: no {START} / {END} markers")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new = head + generate(page) + tail
    if new == text:
        return True
    if not check:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    return False


def main(argv) -> int:
    check = "--check" in argv
    docs = os.path.join(ROOT, "docs")
    pages = [*PAGES, "cli.md"]
    if check and sys.version_info < (3, 13):
        pages.remove("cli.md")  # argparse --help formatting changed in 3.10 and 3.13
    stale = [p for p in pages if not update(os.path.join(docs, p), check)]
    if stale and check:
        print("out of date (run python docs/gen_reference.py):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
