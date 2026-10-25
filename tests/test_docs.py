"""docs/: every public name is documented, the generated reference sections are up to
date, and every ```python block runs against an emulated iPod."""

import glob
import os
import re
import subprocess
import sys

import pytest

import pygpod
from pygpod.device.mountpoint import init_ipod

from .conftest import PROJECT_ROOT, _make_minimal_mp3, _make_minimal_png

DOCS = os.path.join(PROJECT_ROOT, "docs")
PAGES = sorted(glob.glob(os.path.join(DOCS, "*.md")))
BLOCK = re.compile(r"^```python\n(.*?)^```$", re.S | re.M)


def _all_docs():
    return "".join(open(p, encoding="utf-8").read() for p in PAGES)


def test_every_public_name_is_documented():
    text = _all_docs()
    missing = [n for n in pygpod.__all__ if n != "__version__" and f"`pygpod.{n}" not in text]
    assert not missing, missing


def test_reference_sections_are_up_to_date():
    proc = subprocess.run(
        [sys.executable, os.path.join(DOCS, "gen_reference.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_readme_links_every_page():
    with open(os.path.join(DOCS, "README.md"), encoding="utf-8") as f:
        index = f.read()
    for page in PAGES:
        name = os.path.basename(page)
        assert name == "README.md" or f"({name})" in index, name


def _emulated_ipod(tmp_path, monkeypatch):
    """An emulated Classic 6G, an MP3, a PNG cover and a scratch working directory."""
    mountpoint = str(tmp_path / "ipod")
    os.makedirs(mountpoint)
    sysinfo = b"ModelNumStr: xB029\nFirewireGuid: 0x000A2700213749FF\n"  # Classic 6G
    init_ipod(mountpoint, name="Docs", sysinfo_files={"SysInfo": sysinfo})
    song = str(tmp_path / "song.mp3")
    _make_minimal_mp3(song)
    png = str(tmp_path / "cover.png")
    _make_minimal_png(png, 120, 120)
    with open(png, "rb") as f:
        cover = f.read()
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return {
        "mountpoint": mountpoint,
        "song": song,
        "png": png,
        "cover": cover,
        "workdir": str(workdir),
    }


@pytest.mark.parametrize("page", [os.path.basename(p) for p in PAGES])
def test_code_blocks_run(page, tmp_path, monkeypatch):
    with open(os.path.join(DOCS, page), encoding="utf-8") as f:
        blocks = BLOCK.findall(f.read())
    if not blocks:
        pytest.skip("no runnable examples")
    env = _emulated_ipod(tmp_path, monkeypatch)
    ns = {k: env[k] for k in ("mountpoint", "song", "cover", "workdir")}
    for i, code in enumerate(blocks):
        exec(compile(code, f"{page}[block {i + 1}]", "exec"), ns)  # noqa: S102


def test_readme_code_blocks_run(tmp_path, monkeypatch):
    """The README's ```python blocks run as written, with its example paths pointing at the
    emulated iPod and the test files."""
    with open(os.path.join(PROJECT_ROOT, "README.md"), encoding="utf-8") as f:
        blocks = BLOCK.findall(f.read())
    assert blocks
    env = _emulated_ipod(tmp_path, monkeypatch)
    paths = {
        '"/mnt/ipod"': repr(env["mountpoint"]),
        '"/path/to/song.mp3"': repr(env["song"]),
        '"/path/to/cover.png"': repr(env["png"]),
    }
    ns = {}
    for i, code in enumerate(blocks):
        for example, real in paths.items():
            code = code.replace(example, real)
        assert "/path/to/" not in code, f"README block {i + 1} uses a file the test does not have"
        exec(compile(code, f"README.md[block {i + 1}]", "exec"), ns)  # noqa: S102
    db = pygpod.Database(env["mountpoint"])
    assert [t.title for t in db.get_playlist("Обране").tracks] == [db.tracks[0].title]
    assert db.tracks[0].play_count == 3 and db.tracks[0].has_artwork
