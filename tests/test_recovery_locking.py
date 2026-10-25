"""Recovering an interrupted save happens only under the writer lock."""

import os

import pytest

from pygpod.db import transaction, writelock
from pygpod.db.transaction import JOURNAL_NAME, STAGING_SUFFIX, Transaction, TransactionError
from pygpod.exceptions import ConflictError
from pygpod.model.database import Database


def _itunes(mp):
    return os.path.join(mp, "iPod_Control", "iTunes")


def _db_path(mp):
    return os.path.join(_itunes(mp), "iTunesDB")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def ipod(writable_ipod, monkeypatch):
    Database(writable_ipod).save()
    monkeypatch.setattr(writelock, "OPEN_WAIT_S", 0.2)
    return writable_ipod


def _commit_interrupted(mp, monkeypatch, title):
    """A save of ``title`` that stopped right after its journal became durable."""
    db = Database(mp)
    db.tracks[0].title = title
    db._modified = True

    real_replace = os.replace

    def crash(src, dst):
        if dst.endswith(JOURNAL_NAME):
            return real_replace(src, dst)  # the journal becomes durable, then the power fails
        raise KeyboardInterrupt

    monkeypatch.setattr(transaction.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        db.save()
    monkeypatch.undo()
    monkeypatch.setattr(writelock, "OPEN_WAIT_S", 0.2)
    assert os.path.isfile(os.path.join(_itunes(mp), JOURNAL_NAME))


def test_open_keeps_the_staging_files_of_a_running_save(ipod):
    staged = _db_path(ipod) + STAGING_SUFFIX
    with writelock.writer_lock(_itunes(ipod)):  # another writer is staging its files
        with open(staged, "wb") as f:
            f.write(b"half written")
        db = Database(ipod)
        assert os.path.exists(staged)
        assert db.tracks
    assert os.path.exists(staged)
    Database(ipod)  # no writer any more: a leftover of a crashed save, removed
    assert not os.path.exists(staged)


def test_open_never_rolls_forward_the_journal_of_a_running_commit(ipod, monkeypatch):
    old = _read(_db_path(ipod))
    _commit_interrupted(ipod, monkeypatch, "Пісня про рушник")
    with writelock.writer_lock(_itunes(ipod)):  # the committing writer still holds the lock
        db = Database(ipod)
        assert _read(_db_path(ipod)) == old
        assert transaction.interrupted_commit(ipod)
        assert db.tracks[0].title != "Пісня про рушник"
    assert Database(ipod).tracks[0].title == "Пісня про рушник"  # rolled forward now
    assert not transaction.interrupted_commit(ipod)


def test_save_completes_a_leftover_commit_and_reports_the_conflict(ipod, monkeypatch):
    _commit_interrupted(ipod, monkeypatch, "Ой у лузі червона калина")
    with writelock.writer_lock(_itunes(ipod)):
        db = Database(ipod)  # read without recovering
    db.tracks[0].title = "Мої зміни"
    with pytest.raises(ConflictError):
        db.save()  # the crashed save is completed first: the database changed since it was read
    assert not transaction.interrupted_commit(ipod)
    assert Database(ipod).tracks[0].title == "Ой у лузі червона калина"


def test_journal_with_a_lost_staging_file_raises_and_replaces_nothing(ipod, monkeypatch):
    itunes = _itunes(ipod)
    a, b = os.path.join(itunes, "Перший.bin"), os.path.join(itunes, "Другий.bin")
    for p in (a, b):
        with open(p, "wb") as f:
            f.write(b"old")
    tx = Transaction(ipod)
    tx.stage_bytes(a, b"new-a")
    tx.stage_bytes(b, b"new-b")

    real_replace = os.replace

    def crash(src, dst):
        if dst.endswith(JOURNAL_NAME):
            return real_replace(src, dst)  # the journal becomes durable, then the power fails
        raise KeyboardInterrupt

    monkeypatch.setattr(transaction.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        tx.commit()
    monkeypatch.undo()
    os.unlink(b + STAGING_SUFFIX)  # lost, never renamed: b still has the old content
    with pytest.raises(TransactionError, match="cannot be completed"):
        transaction.recover(ipod)
    assert _read(a) == b"old" and _read(b) == b"old"
    assert os.path.exists(a + STAGING_SUFFIX)
    assert transaction.interrupted_commit(ipod)  # kept: the user decides


def test_journal_of_an_older_version_without_hashes_still_replays(ipod):
    itunes = _itunes(ipod)
    a = os.path.join(itunes, "A.bin")
    with open(a + STAGING_SUFFIX, "wb") as f:
        f.write(b"new")
    with open(os.path.join(itunes, JOURNAL_NAME), "w") as f:
        f.write(
            '{"replace": [["iPod_Control/iTunes/A.bin.pygpod-new", "iPod_Control/iTunes/A.bin"],'
            ' ["iPod_Control/iTunes/gone.pygpod-new", "iPod_Control/iTunes/gone"]], "delete": []}'
        )
    assert transaction.recover(ipod) == "rolled-forward"
    assert _read(a) == b"new"
