"""Play count entries are bound to their tracks when the database is opened: removing
tracks before the merge never moves counts, ratings or bookmarks to other tracks."""

import os
import plistlib

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3
from .test_playcount_merge import _write_play_counts_file


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    mp3 = str(tmp_path / "пісня.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    for title in ("Смерека", "Черемшина", "Водограй"):
        db.add_track(mp3, title=title)
    db.save()
    return mp


def _by_title(db):
    return {t.title: t for t in db.tracks}


def test_binary_play_counts_stay_with_their_tracks(ipod):
    _write_play_counts_file(
        os.path.join(ipod, "iPod_Control", "iTunes"),
        [
            {"play_count": 3, "rating": 20, "bookmark_time": 1000},
            {"play_count": 7, "rating": 80, "bookmark_time": 7000},
            {"play_count": 1, "rating": 40, "bookmark_time": 4000},
        ],
    )
    db = Database(ipod, apply_play_counts=False)
    first = _by_title(db)["Смерека"]
    db.remove_track(first)
    pending = {p.track.title: p for p in db.pending_play_counts()}
    assert set(pending) == {"Черемшина", "Водограй"}
    assert pending["Черемшина"].play_count == 7
    assert db.merge_play_counts() == 2
    tracks = _by_title(db)
    assert tracks["Черемшина"].play_count == 7
    assert tracks["Черемшина"].rating == 80
    assert tracks["Черемшина"].bookmark_time == 7000
    assert tracks["Водограй"].play_count == 1
    assert tracks["Водограй"].rating == 40
    db.save()
    tracks = _by_title(Database(ipod))
    assert tracks["Черемшина"].play_count == 7 and "Смерека" not in tracks


def test_plist_play_counts_of_a_removed_track_are_dropped(ipod):
    dbids = {t.title: t.dbid for t in Database(ipod, apply_play_counts=False).tracks}
    with open(os.path.join(ipod, "iPod_Control", "iTunes", "PlayCounts.plist"), "wb") as f:
        f.write(
            plistlib.dumps(
                {
                    "tracks": [
                        {"persistentID": dbids["Смерека"], "playCount": 3},
                        {"persistentID": dbids["Водограй"], "playCount": 5},
                    ]
                }
            )
        )
    db = Database(ipod, apply_play_counts=False)
    db.remove_track(_by_title(db)["Смерека"])
    assert [(p.track.title, p.play_count) for p in db.pending_play_counts()] == [("Водограй", 5)]
    assert db.merge_play_counts() == 1
    assert _by_title(db)["Водограй"].play_count == 5
