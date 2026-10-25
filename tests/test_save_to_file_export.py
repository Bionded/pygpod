"""save_to_file() exports the iTunesDB and changes nothing on the iPod."""

import hashlib
import os

from pygpod.model.database import Database

from .conftest import _make_minimal_mp3, _make_minimal_png


def _snapshot(mp):
    out = {}
    for root, _, files in os.walk(os.path.join(mp, "iPod_Control")):
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, mp)] = hashlib.sha256(f.read()).hexdigest()
    return out


def test_export_leaves_the_device_files_untouched(writable_ipod, tmp_path):
    mp3, png = str(tmp_path / "пісня.mp3"), str(tmp_path / "обкладинка.png")
    _make_minimal_mp3(mp3)
    _make_minimal_png(png)
    db = Database(writable_ipod)
    added = [db.add_track(mp3, title=f"Пісня {i}", cover_art=png) for i in range(3)]
    db.save()
    before = _snapshot(writable_ipod)
    db = Database(writable_ipod)
    db.remove_tracks([t for t in db.tracks if t.title in ("Пісня 0", "Пісня 1")])
    export = str(tmp_path / "експорт.itdb")
    db.save_to_file(export)
    assert _snapshot(writable_ipod) == before
    assert len(added) == 3
    titles = [t.title for t in Database.from_file(export).tracks]
    assert "Пісня 2" in titles and "Пісня 0" not in titles
