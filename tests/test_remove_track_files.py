"""remove_track(delete_file=True) deletes the file with the save, and only an unused one;
mutating calls refuse Track and Playlist objects of another database."""

import os
import shutil

import pytest

from pygpod.model.database import Database
from pygpod.utils.encoding import ipod_path_to_os


def _add(db, mp3, **kw):
    return db.add_track(mp3, **kw)


def test_file_is_deleted_only_by_a_successful_save(writable_ipod_with_mp3, monkeypatch):
    mp, mp3 = writable_ipod_with_mp3
    db = Database(mp)
    track = _add(db, mp3, title="Тиха вода")
    db.save()
    fpath = ipod_path_to_os(track.ipod_path, mp)
    db.remove_track(track, delete_file=True)
    assert os.path.isfile(fpath)  # the database on the iPod still lists the track

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "_write_db_file", staticmethod(boom))
    with pytest.raises(OSError):
        db.save()
    assert os.path.isfile(fpath)  # failed save: database and file as before
    assert any(t.title == "Тиха вода" for t in Database(mp).tracks)
    monkeypatch.undo()
    db.save()
    assert not os.path.exists(fpath)
    assert not any(t.title == "Тиха вода" for t in Database(mp).tracks)


def test_shared_file_is_kept_while_another_track_uses_it(writable_ipod_with_mp3):
    mp, mp3 = writable_ipod_with_mp3
    dst = os.path.join(mp, "iPod_Control", "Music", "F01", "спільна.mp3")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(mp3, dst)
    db = Database(mp)
    a = db.add_track(dst, copy=False, title="А")
    b = db.add_track(dst, copy=False, title="Б")
    db.save()
    db.remove_track(a, delete_file=True)
    db.save()
    assert os.path.isfile(dst)
    assert [t.title for t in Database(mp).tracks if t.title in ("А", "Б")] == ["Б"]
    db.remove_track(b, delete_file=True)
    db.save()
    assert not os.path.exists(dst)


def test_foreign_tracks_and_playlists_are_refused(writable_ipod_with_mp3, tmp_path):
    mp, mp3 = writable_ipod_with_mp3
    other = str(tmp_path / "other")
    shutil.copytree(mp, other)
    db_a, db_b = Database(mp), Database(other)
    a = _add(db_a, mp3, title="Перша А")
    b = _add(db_b, mp3, title="Перша Б")
    assert a.track_id == b.track_id  # same id, different libraries
    with pytest.raises(ValueError):
        db_a.remove_track(b)
    assert a in db_a.tracks
    pl_a, pl_b = db_a.create_playlist("Список А"), db_b.create_playlist("Список Б")
    with pytest.raises(ValueError):
        db_a.add_track_to_playlist(pl_a, b)
    with pytest.raises(ValueError):
        db_a.add_track_to_playlist(pl_b, a)
    with pytest.raises(ValueError):
        db_a.set_playlist("Список А", [b])
    db_a.add_track_to_playlist(pl_a, a)
    with pytest.raises(ValueError):
        db_a.remove_track_from_playlist(pl_a, b)
    assert [t.title for t in pl_a.tracks] == ["Перша А"]
    with pytest.raises(ValueError):
        db_a.set_artwork(b, None)
    db_a.remove_track(a)
    with pytest.raises(ValueError):  # removed already: stale
        db_a.remove_track(a)


def test_foreign_playlists_are_not_deleted_or_changed(writable_ipod_with_mp3, tmp_path):
    from pygpod.model.config import DatabaseConfig

    mp, mp3 = writable_ipod_with_mp3
    other = str(tmp_path / "other")
    shutil.copytree(mp, other)
    db_a = Database(mp, config=DatabaseConfig(random_seed=7))
    db_b = Database(other, config=DatabaseConfig(random_seed=7))
    pl_a, pl_b = db_a.create_playlist("Наш"), db_b.create_playlist("Чужий")
    assert pl_a.playlist_id == pl_b.playlist_id  # same id, different libraries
    smart_a = db_a.create_smart_playlist("Розумний наш", [])
    smart_b = db_b.create_smart_playlist("Розумний чужий", [])
    before = [(p.name, p.playlist_id, p.smart_rules) for p in db_a.playlists]
    with pytest.raises(ValueError):
        db_a.delete_playlist(pl_b)
    with pytest.raises(ValueError):
        db_a.update_smart_playlist(smart_b, rules=[])
    with pytest.raises(ValueError):
        db_a.refresh_smart_playlist(smart_b)
    assert [(p.name, p.playlist_id, p.smart_rules) for p in db_a.playlists] == before
    db_a.update_smart_playlist(smart_a, rules=[])  # own playlists still work
    db_a.refresh_smart_playlist(smart_a)
    db_a.delete_playlist(pl_a)
    assert db_a.get_playlist("Наш") is None
    with pytest.raises(ValueError):  # deleted already: stale
        db_a.delete_playlist(pl_a)
