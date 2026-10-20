"""Crash-safe save: failures keep the last good state, interrupted commits recover."""

import os

import pytest

from pygpod.db import transaction
from pygpod.db.transaction import JOURNAL_NAME, STAGING_SUFFIX, Transaction, TransactionError
from pygpod.model.artwork_manager import ArtworkManager
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3, _make_minimal_png


def _db_path(mp):
    return os.path.join(mp, "iPod_Control", "iTunes", "iTunesDB")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _leftovers(mp):
    found = []
    for d in ("iTunes", "Artwork"):
        full = os.path.join(mp, "iPod_Control", d)
        if os.path.isdir(full):
            found += [
                n
                for n in os.listdir(full)
                if n.endswith(STAGING_SUFFIX) or n.startswith(JOURNAL_NAME)
            ]
    return found


def _thumbs(mgr, image_id):
    """Thumbnail bytes of one image, per format, read through its MHNI offsets."""
    from pygpod.db.artwork_parser import MHNI_MAGIC

    out = {}
    for mhii in mgr._get_or_create_mhli().children:
        if mhii.fields.get("image_id") != image_id:
            continue
        for child in mhii.children:
            for mhni in [child] + list(child.children):
                if mhni.magic != MHNI_MAGIC:
                    continue
                fmt = mhni.fields["format_id"]
                path = os.path.join(mgr._artwork_dir, f"F{fmt}_1.ithmb")
                with open(path, "rb") as f:
                    f.seek(mhni.fields["ithumb_offset"])
                    out[fmt] = f.read(mhni.fields["image_size"])
    return out


@pytest.fixture
def ipod(writable_ipod):
    Database(writable_ipod).save()
    return writable_ipod


def _rename_first(db, title="changed"):
    db.tracks[0].title = title
    db._modified = True


def test_normal_save_leaves_no_staging_files(ipod):
    db = Database(ipod)
    _rename_first(db)
    db.save()
    assert _leftovers(ipod) == []
    assert Database(ipod).tracks[0].title == "changed"


def test_write_failure_mid_itunesdb_keeps_old_db(ipod, monkeypatch):
    old = _read(_db_path(ipod))
    db = Database(ipod)
    _rename_first(db)

    def partial(path, data):
        with open(path, "wb") as f:
            f.write(data[: len(data) // 2])
        raise OSError("I/O error")

    monkeypatch.setattr(Database, "_write_db_file", staticmethod(partial))
    with pytest.raises(OSError):
        db.save()
    assert _read(_db_path(ipod)) == old
    assert _leftovers(ipod) == []
    assert Database(ipod).tracks[0].title != "changed"


def test_checksum_failure_writes_nothing(ipod, monkeypatch, tmp_path):
    from pygpod.model import database as dbmod

    db = Database(ipod)
    png = str(tmp_path / "c.png")
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_png(png)
    _make_minimal_mp3(mp3)
    db.add_track(mp3, title="new", cover_art=png)
    art_dir = os.path.join(ipod, "iPod_Control", "Artwork")
    before = (
        {n: _read(os.path.join(art_dir, n)) for n in os.listdir(art_dir)}
        if os.path.isdir(art_dir)
        else {}
    )
    old = _read(_db_path(ipod))

    def bad_checksum(*a, **k):
        raise ValueError("checksum input invalid")

    monkeypatch.setattr(dbmod, "update_checksums", bad_checksum)
    monkeypatch.setattr(type(db._device), "requires_hash", property(lambda self: True))
    monkeypatch.setattr(
        type(db._device), "firewire_guid", property(lambda self: "000A2700213749FF")
    )
    with pytest.raises(ValueError):
        db.save()
    assert _read(_db_path(ipod)) == old
    if before:
        assert _read(os.path.join(art_dir, "ArtworkDB")) == before.get("ArtworkDB")
    assert _leftovers(ipod) == []


def test_artwork_failure_is_raised_and_db_untouched(ipod, monkeypatch, tmp_path):
    db = Database(ipod)
    png = str(tmp_path / "c.png")
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_png(png)
    _make_minimal_mp3(mp3)
    db.add_track(mp3, title="new", cover_art=png)
    assert db._artwork_manager is not None
    old = _read(_db_path(ipod))

    def boom(self, tx):
        raise OSError("artwork write failed")

    monkeypatch.setattr(ArtworkManager, "stage", boom)
    with pytest.raises(OSError):
        db.save()
    assert _read(_db_path(ipod)) == old
    assert _leftovers(ipod) == []
    monkeypatch.undo()
    db.save()
    assert any(t.title == "new" for t in Database(ipod).tracks)


def test_crash_after_commit_point_rolls_forward(ipod, monkeypatch):
    db = Database(ipod)
    _rename_first(db, "rolled forward")
    real_replace = os.replace
    calls = []

    def crash(src, dst):
        calls.append(dst)
        if dst.endswith("iTunesDB"):
            raise KeyboardInterrupt  # power loss after the journal is durable
        real_replace(src, dst)

    monkeypatch.setattr(transaction.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        db.save()
    monkeypatch.undo()
    assert os.path.isfile(os.path.join(ipod, "iPod_Control", "iTunes", JOURNAL_NAME))

    assert Database(ipod).tracks[0].title == "rolled forward"
    assert _leftovers(ipod) == []


def test_partial_multi_file_commit_completes(ipod, monkeypatch):
    itunes = os.path.join(ipod, "iPod_Control", "iTunes")
    a, b = os.path.join(itunes, "A.bin"), os.path.join(itunes, "B.bin")
    for p in (a, b):
        with open(p, "wb") as f:
            f.write(b"old")
    tx = Transaction(ipod)
    tx.stage_bytes(a, b"new-a")
    tx.stage_bytes(b, b"new-b")
    real_replace = os.replace

    def crash(src, dst):
        if dst == b:
            raise KeyboardInterrupt
        real_replace(src, dst)

    monkeypatch.setattr(transaction.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        tx.commit()
    monkeypatch.undo()
    assert _read(a) == b"new-a" and _read(b) == b"old"
    assert transaction.recover(ipod) == "rolled-forward"
    assert _read(a) == b"new-a" and _read(b) == b"new-b"
    assert transaction.recover(ipod) is None


def test_crash_before_journal_rolls_back(ipod):
    old = _read(_db_path(ipod))
    tx = Transaction(ipod)
    tx.stage_bytes(_db_path(ipod), b"garbage")
    # no commit: process died after staging
    assert Database(ipod).tracks is not None
    assert _read(_db_path(ipod)) == old
    assert _leftovers(ipod) == []


def test_journal_path_escape_refused(ipod, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    tx = Transaction(ipod)
    with pytest.raises(TransactionError):
        tx.delete_on_commit(str(outside))
    journal = os.path.join(ipod, "iPod_Control", "iTunes", JOURNAL_NAME)
    with open(journal, "w") as f:
        f.write('{"replace": [], "delete": ["../outside.txt"]}')
    with pytest.raises(TransactionError):
        transaction.recover(ipod)
    assert outside.read_text() == "keep"


def test_aborted_compaction_restores_offsets(ipod, monkeypatch, tmp_path):
    from pygpod.device.models import IpodGeneration

    red, green = str(tmp_path / "r.png"), str(tmp_path / "g.png")
    _make_minimal_png(red, color=(255, 0, 0))
    _make_minimal_png(green, color=(0, 255, 0))
    mgr = ArtworkManager(ipod, generation=IpodGeneration.CLASSIC_1)
    first = mgr.add_artwork_data(dbid=1, art_data=_read(red), save=False)
    second = mgr.add_artwork_data(dbid=2, art_data=_read(green), save=False)
    mgr.save()
    n_images = len(mgr._get_or_create_mhli().children)
    kept = _thumbs(ArtworkManager(ipod, generation=IpodGeneration.CLASSIC_1), second)
    assert kept
    art_dir = os.path.join(ipod, "iPod_Control", "Artwork")
    files = {n: _read(os.path.join(art_dir, n)) for n in os.listdir(art_dir)}

    monkeypatch.setattr(ArtworkManager, "COMPACT_WASTE_RATIO", 0.0)  # compact even for 1 removal
    mgr.remove_artwork(first)  # .ithmb compaction on the next save
    real_commit = Transaction.commit

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr(Transaction, "commit", fail)
    with pytest.raises(OSError):
        mgr.save()
    monkeypatch.setattr(Transaction, "commit", real_commit)
    assert {n: _read(os.path.join(art_dir, n)) for n in os.listdir(art_dir)} == files

    mgr.save()  # retry compacts from the untouched files using the restored offsets
    mgr2 = ArtworkManager(ipod, generation=IpodGeneration.CLASSIC_1)
    mhli = mgr2._get_or_create_mhli()
    assert len(mhli.children) == n_images - 1
    assert _thumbs(mgr2, second) == kept
    for name in os.listdir(art_dir):
        if name.endswith(".ithmb"):
            assert os.path.getsize(os.path.join(art_dir, name)) < len(files[name])
