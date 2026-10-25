"""Removed artwork leaves reusable .ithmb holes; compaction only when worth it and possible."""

import os

import pytest

from pygpod.db.transaction import Transaction
from pygpod.device.models import IpodGeneration
from pygpod.model.artwork_manager import ArtworkManager

from .conftest import _make_minimal_png
from .test_crash_safe_save import _thumbs


def _png(tmp_path, i):
    path = str(tmp_path / f"c{i}.png")
    _make_minimal_png(path, 64, 64, ((i * 40) % 256, (i * 90) % 256, (i * 20) % 256))
    with open(path, "rb") as f:
        return f.read()


def _mgr(root):
    return ArtworkManager(str(root), generation=IpodGeneration.CLASSIC_1)


@pytest.fixture
def art(tmp_path):
    root = tmp_path / "ipod"
    (root / "iPod_Control" / "iTunes").mkdir(parents=True)
    mgr = _mgr(root)
    ids = [mgr.add_artwork_data(dbid=i, art_data=_png(tmp_path, i), save=False) for i in range(10)]
    mgr.save()
    return root, ids


def _sizes(root):
    d = root / "iPod_Control" / "Artwork"
    return {p.name: p.stat().st_size for p in d.glob("*.ithmb")}


def _offsets(mgr, image_id):
    for mhii in mgr._get_or_create_mhli().children:
        if mhii.fields.get("image_id") == image_id:
            return {m.fields["format_id"]: m.fields["ithumb_offset"] for m in mgr._mhnis(mhii)}
    return None


@pytest.fixture
def staged(monkeypatch):
    targets = []
    real = Transaction.stage_bytes

    def spy(self, target, data):
        targets.append(os.path.basename(target))
        return real(self, target, data)

    monkeypatch.setattr(Transaction, "stage_bytes", spy)
    return targets


def test_one_removal_keeps_files_and_thumbnails(art, staged, monkeypatch):
    root, ids = art
    mgr = _mgr(root)
    keep = {i: _thumbs(mgr, i) for i in ids[1:]}
    sizes = _sizes(root)
    monkeypatch.setattr(ArtworkManager, "_free_space", lambda self: 10_000)  # nearly full disk
    assert mgr.remove_artwork(ids[0])
    mgr.save()
    assert [t for t in staged if t.endswith(".ithmb")] == []  # no thumbnail copies staged
    assert _sizes(root) == sizes
    again = _mgr(root)
    assert {i: _thumbs(again, i) for i in ids[1:]} == keep


def test_hole_reused_after_commit_not_before(art, tmp_path):
    root, ids = art
    mgr = _mgr(root)
    freed = _offsets(mgr, ids[3])
    mgr.remove_artwork(ids[3])
    same_session = mgr.add_artwork_data(dbid=50, art_data=_png(tmp_path, 50), save=False)
    mgr.save()
    # the on-disk ArtworkDB still referenced the freed slots until this commit: the new image
    # must not have overwritten them
    assert set(_offsets(mgr, same_session).values()).isdisjoint(freed.values())
    sizes = _sizes(root)

    mgr = _mgr(root)
    new_id = mgr.add_artwork_data(dbid=51, art_data=_png(tmp_path, 51), save=False)
    mgr.save()
    assert _offsets(mgr, new_id) == freed  # reused the hole in every format
    assert _sizes(root) == sizes
    again = _mgr(root)
    assert _thumbs(again, new_id) and all(_thumbs(again, i) for i in ids if i != ids[3])


def test_compaction_when_mostly_empty_and_space_allows(art, staged):
    root, ids = art
    mgr = _mgr(root)
    keep = {i: _thumbs(mgr, i) for i in ids[:3]}
    before = _sizes(root)
    for i in ids[3:]:
        mgr.remove_artwork(i)
    mgr.save()
    assert any(t.endswith(".ithmb") for t in staged)
    after = _sizes(root)
    assert all(after[n] < before[n] for n in before)
    assert {i: _thumbs(_mgr(root), i) for i in ids[:3]} == keep


def test_no_compaction_without_space_even_when_mostly_empty(art, staged, monkeypatch):
    root, ids = art
    mgr = _mgr(root)
    before = _sizes(root)
    monkeypatch.setattr(ArtworkManager, "_free_space", lambda self: 1000)
    for i in ids[3:]:
        mgr.remove_artwork(i)
    mgr.save()
    assert not any(t.endswith(".ithmb") for t in staged) and _sizes(root) == before
    assert len(_mgr(root)._get_or_create_mhli().children) == 3
