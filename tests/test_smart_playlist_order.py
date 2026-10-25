"""Smart playlists that use other smart playlists are refreshed after them."""

import logging
import os

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.model.database import Database
from pygpod.model.smartplaylist import SPLAction, SPLField, SPLRule

from .conftest import _make_minimal_mp3


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(mp)
    mp3 = str(tmp_path / "пісня.mp3")
    _make_minimal_mp3(mp3)
    return mp, mp3


def _in(pl):
    return SPLRule(field=SPLField.PLAYLIST, action=SPLAction.IS_INT, fromvalue=pl.playlist_id)


def _titled(text):
    return SPLRule(field=SPLField.SONG_NAME, action=SPLAction.CONTAINS, string=text)


def test_dependent_playlist_listed_first_sees_the_new_members(ipod):
    mp, mp3 = ipod
    db = Database(mp)
    a = db.create_smart_playlist("Залежний", [_titled("—")])
    b = db.create_smart_playlist("Кобзарі", [_titled("Кобза")])
    db.update_smart_playlist(a, rules=[_in(b)])
    db.add_track(mp3, title="Кобза грає")
    db.save()
    db = Database(mp)
    assert [t.title for t in db.get_playlist("Кобзарі").tracks] == ["Кобза грає"]
    assert [t.title for t in db.get_playlist("Залежний").tracks] == ["Кобза грає"]


def test_cycle_terminates_with_a_deterministic_result(ipod, caplog):
    mp, mp3 = ipod
    db = Database(mp)
    db.add_track(mp3, title="Трембіта")
    a = db.create_smart_playlist("Перший", [_titled("—")])
    b = db.create_smart_playlist("Другий", [_titled("—")])
    db.update_smart_playlist(a, rules=[_in(b)], refresh=False)
    # B holds what A does not: never settles
    no = SPLRule(field=SPLField.PLAYLIST, action=SPLAction.IS_NOT_INT, fromvalue=a.playlist_id)
    db.update_smart_playlist(b, rules=[no], refresh=False)
    with caplog.at_level(logging.WARNING):
        assert db.refresh_smart_playlists() == 2
    assert "did not settle" in caplog.text
    first = ([t.title for t in a.tracks], [t.title for t in b.tracks])
    db2 = Database(mp)  # nothing saved: the same start, the same result
    db2.add_track(mp3, title="Трембіта")
    a2 = db2.create_smart_playlist("Перший", [_titled("—")])
    b2 = db2.create_smart_playlist("Другий", [_titled("—")])
    db2.update_smart_playlist(a2, rules=[_in(b2)], refresh=False)
    no2 = SPLRule(field=SPLField.PLAYLIST, action=SPLAction.IS_NOT_INT, fromvalue=a2.playlist_id)
    db2.update_smart_playlist(b2, rules=[no2], refresh=False)
    db2.refresh_smart_playlists()
    assert ([t.title for t in a2.tracks], [t.title for t in b2.tracks]) == first


def test_settling_cycle_logs_nothing(ipod, caplog):
    mp, mp3 = ipod
    db = Database(mp)
    db.add_track(mp3, title="Сопілка")
    a = db.create_smart_playlist("Один", [_titled("—")])
    b = db.create_smart_playlist("Два", [_titled("—")])
    db.update_smart_playlist(a, rules=[_in(b)], refresh=False)
    db.update_smart_playlist(b, rules=[_in(a)], refresh=False)
    with caplog.at_level(logging.WARNING):
        db.refresh_smart_playlists()
    assert "did not settle" not in caplog.text
    assert a.tracks == [] and b.tracks == []
