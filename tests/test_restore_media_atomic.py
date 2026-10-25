"""restore() replaces music files all or none together with the database."""

import os

import pytest

import pygpod
from pygpod import backup_archive
from pygpod.db import transaction
from pygpod.model.database import Database
from pygpod.utils.encoding import ipod_path_to_os

from .conftest import _make_minimal_mp3
from .test_backup_restore import _ipod, _snapshot


@pytest.fixture
def ipod(tmp_path):
    mp = _ipod(tmp_path)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    db.add_track(mp3, title="Без бою")
    db.add_track(mp3, title="Човен")
    db.save()
    return mp


def _changed_since_backup(mp, tmp_path):
    """Back the iPod up, then change a music file and the database: a restore must replace both."""
    archive = str(tmp_path / "резервна.tar")
    pygpod.backup(mp, archive)
    db = Database(mp)
    track = db.tracks[0]
    with open(ipod_path_to_os(track.ipod_path, mp), "ab") as f:
        f.write(b"\xff" * 4096)  # the file was re-encoded since the backup
    track.title = "Змінено після копії"
    db.save()
    return archive


def _orig_files(mp):
    return [p for p in _snapshot(mp) if p.endswith(".pygpod-orig")]


def test_failed_commit_leaves_media_and_database_as_before(ipod, tmp_path, monkeypatch):
    mp = ipod
    archive = _changed_since_backup(mp, tmp_path)
    before = _snapshot(mp)

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr(transaction.Transaction, "commit", fail)
    with pytest.raises(OSError):
        pygpod.restore(archive, mp)
    monkeypatch.undo()
    after = _snapshot(mp)
    assert {p: v for p, v in after.items() if "pygpod.lock" not in p} == {
        p: v for p, v in before.items() if "pygpod.lock" not in p
    }
    assert any(t.title == "Змінено після копії" for t in Database(mp).tracks)


def test_interrupted_restore_is_completed_by_running_it_again(ipod, tmp_path, monkeypatch):
    mp = ipod
    archive = _changed_since_backup(mp, tmp_path)

    def power_loss(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(transaction.Transaction, "commit", power_loss)
    monkeypatch.setattr(backup_archive, "_roll_back_media", lambda replaced: None)
    with pytest.raises(KeyboardInterrupt):
        pygpod.restore(archive, mp)
    monkeypatch.undo()
    kept = _orig_files(mp)
    assert kept  # the old database's file is still there
    report = Database(mp, apply_play_counts=False, recover=False).check()
    assert "interrupted_restore" in report.kinds()
    assert "orphan_file" not in report.kinds() or not any(
        i.subject.endswith(".pygpod-orig") for i in report.by_kind("orphan_file")
    )

    pygpod.restore(archive, mp)
    assert _orig_files(mp) == []
    assert not any(t.title == "Змінено після копії" for t in Database(mp).tracks)
    assert (
        not Database(mp, apply_play_counts=False, recover=False)
        .check()
        .by_kind("interrupted_restore")
    )


def test_identical_media_are_not_rewritten(ipod, tmp_path):
    mp = ipod
    archive = str(tmp_path / "копія.tar")
    pygpod.backup(mp, archive)
    music = [ipod_path_to_os(t.ipod_path, mp) for t in Database(mp).tracks]
    inodes = {p: os.stat(p).st_ino for p in music}
    pygpod.restore(archive, mp)
    assert {p: os.stat(p).st_ino for p in music} == inodes
    assert _orig_files(mp) == []
