"""PlayCounts.plist entries are matched to tracks by persistentID (= dbid), as libgpod does."""

import os
import plistlib

import pytest

from pygpod.db.playcounts import parse_playcounts_plist
from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    for title in ("a", "b", "c", "d"):
        db.add_track(mp3, title=title)
    db.save()
    return mp


def _write(mp, tracks):
    path = os.path.join(mp, "iPod_Control", "iTunes", "PlayCounts.plist")
    with open(path, "wb") as f:
        f.write(plistlib.dumps({"tracks": tracks}))


def _dbids(mp):
    return {t.title: t.dbid for t in Database(mp, apply_play_counts=False).tracks}


def test_libgpod_schema_parsed():
    data = plistlib.dumps(
        {
            "tracks": [
                {
                    "persistentID": 0x1234,
                    "playCount": 3,
                    "playMacOSDate": 3800000000,
                    "skipCount": 1,
                    "skipMacOSDate": 3800000100,
                    "userRating": 60,
                    "bookmarkTimeInMS": 5000,
                    "playedState": 1,
                },
                {"playCount": 9},  # no persistentID: cannot be matched
            ]
        }
    )
    (e,) = parse_playcounts_plist(data)
    assert (e.persistent_id, e.play_count, e.time_played, e.skip_count) == (
        0x1234,
        3,
        3800000000,
        1,
    )
    assert (e.time_skipped, e.rating, e.bookmark_time) == (3800000100, 60, 5000)


def test_reordered_sparse_unknown_and_duplicate(ipod):
    ids = _dbids(ipod)
    _write(
        ipod,
        [
            {"persistentID": ids["d"], "playCount": 4},  # reordered
            {"persistentID": ids["b"], "playCount": 2, "userRating": 80},
            {"persistentID": 0xDEADBEEF, "playCount": 99},  # unknown track
            {"persistentID": ids["b"], "playCount": 5, "userRating": 100},  # duplicate: last wins
        ],
    )
    db = Database(ipod, apply_play_counts=False)
    pending = {p.track.title: p for p in db.pending_play_counts()}
    assert set(pending) == {"b", "d"}  # sparse: a and c untouched
    assert (pending["b"].play_count, pending["b"].rating) == (5, 100)
    assert pending["d"].play_count == 4
    db.merge_play_counts()
    db.save()
    counts = {t.title: t.play_count for t in Database(ipod).tracks}
    assert counts == {"a": 0, "b": 5, "c": 0, "d": 4}
    assert not os.path.exists(os.path.join(ipod, "iPod_Control", "iTunes", "PlayCounts.plist"))
