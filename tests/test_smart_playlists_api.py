"""Persisted smart playlists: create, update, refresh; libgpod agrees on rules and members."""

import os

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.exceptions import PlaylistError
from pygpod.model.database import Database
from pygpod.model.smartplaylist import (
    SPLAction,
    SPLField,
    SPLLimitSort,
    SPLLimitType,
    SPLMatch,
    SPLPrefs,
    SPLRule,
)

from .conftest import _make_minimal_mp3

SONGS = [
    # title, artist, year, rating, play_count, genre
    ("Там, де нас нема", "Океан Ельзи", 1988, 80, 5, "Rock"),
    ("Човен", "Океан Ельзи", 1990, 100, 2, "Rock"),
    ("Старі фотографії", "Скрябін", 1986, 60, 0, "Rock"),
    ("Мала", "Kalush", 2015, 40, 9, "Hip-Hop"),
    ("Додому", "Kalush", 2015, 0, 1, "Hip-Hop"),
]


@pytest.fixture
def ipod(tmp_path):
    mp = str(tmp_path / "ipod")
    os.makedirs(mp)
    init_ipod(
        mp, sysinfo_files={"SysInfo": b"ModelNumStr: xB029\nFirewireGuid: 0x000A2700213749FF\n"}
    )
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    db = Database(mp)
    for title, artist, year, rating, plays, genre in SONGS:
        db.add_track(
            mp3, title=title, artist=artist, year=year, rating=rating, play_count=plays, genre=genre
        )
    db.save()
    return mp


def _titles(pl):
    return [t.title for t in pl.tracks]


def test_create_persist_reopen_refresh(ipod):
    db = Database(ipod)
    okean = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Океан Ельзи")
    pl = db.create_smart_playlist("Океан Ельзи", [okean])
    assert _titles(pl) == ["Там, де нас нема", "Човен"]
    db.save()

    db = Database(ipod)
    pl = db.get_playlist("Океан Ельзи")
    assert pl.is_smart
    match, rules = pl.smart_rules
    assert match == SPLMatch.AND and len(rules) == 1 and rules[0].string == "Океан Ельзи"
    assert pl.smart_prefs.liveupdate and pl.smart_prefs.checkrules
    assert _titles(pl) == ["Там, де нас нема", "Човен"]

    newer = SPLRule(field=SPLField.YEAR, action=SPLAction.IS_GREATER_THAN, fromvalue=2000)
    db.update_smart_playlist(pl, rules=[okean, newer], match=SPLMatch.OR)
    assert _titles(pl) == ["Там, де нас нема", "Човен", "Мала", "Додому"]
    db.save()
    db = Database(ipod)
    assert _titles(db.get_playlist("Океан Ельзи")) == [
        "Там, де нас нема",
        "Човен",
        "Мала",
        "Додому",
    ]


def test_limits_and_live_refresh(ipod):
    db = Database(ipod)
    prefs = SPLPrefs()
    prefs.checklimits = True
    prefs.limittype = SPLLimitType.SONGS
    prefs.limitvalue = 2
    prefs.limitsort = SPLLimitSort.SONG_NAME
    pl = db.create_smart_playlist("Two", [], prefs=prefs)
    assert len(pl.tracks) == 2
    db.save()
    db = Database(ipod)
    db.tracks[0].rating = 20  # a change; refresh keeps working on the reopened playlist
    assert db.refresh_smart_playlists() == 1
    assert len(db.get_playlist("Two").tracks) == 2


def test_refresh_refuses_normal_playlist(ipod):
    db = Database(ipod)
    pl = db.create_playlist("plain")
    assert pl.smart_rules is None and pl.smart_prefs is None
    with pytest.raises(PlaylistError):
        db.refresh_smart_playlist(pl)


@pytest.mark.libgpod
def test_rules_and_members_match_libgpod(ipod, tool):
    import subprocess

    db = Database(ipod)
    rules = [
        SPLRule(field=SPLField.ARTIST, action=SPLAction.CONTAINS, string="Океан Ельзи"),
        SPLRule(field=SPLField.RATING, action=SPLAction.IS_GREATER_THAN, fromvalue=50),
    ]
    db.create_smart_playlist("Найкраще з Океану", rules)
    db.create_smart_playlist(
        "Нове або часте",
        [
            SPLRule(field=SPLField.YEAR, action=SPLAction.IS_GREATER_THAN, fromvalue=2000),
            SPLRule(field=SPLField.PLAYCOUNT, action=SPLAction.IS_GREATER_THAN, fromvalue=3),
        ],
        match=SPLMatch.OR,
    )
    db.create_smart_playlist(
        "Не рок",
        [SPLRule(field=SPLField.GENRE, action=SPLAction.IS_NOT, string="Rock")],
    )
    db.save()
    ours = {p.name: _titles(p) for p in Database(ipod).playlists if p.is_smart}

    out = subprocess.run(
        [tool, "dump-spl", ipod], capture_output=True, text=True, check=True
    ).stdout
    theirs, rules_seen = {}, {}
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "P" and f[1] in ours:
            theirs[f[1]] = f[5].split("|") if f[5] else []
        elif f[0] == "R":
            rules_seen.setdefault(f[1], []).append((int(f[2]), int(f[3]), f[4], int(f[5])))
        elif f[0] == "S" and f[1] == "Нове або часте":
            assert f[9] == "1"  # OR
    assert theirs == ours
    assert rules_seen["Найкраще з Океану"] == [
        (SPLField.ARTIST, SPLAction.CONTAINS, "Океан Ельзи", 0),
        (SPLField.RATING, SPLAction.IS_GREATER_THAN, "", 50),
    ]


def test_save_refreshes_live_playlists_by_default(ipod):
    """Design: save() refreshes live-updating smart playlists."""
    db = Database(ipod)
    okean = SPLRule(field=SPLField.ARTIST, action=SPLAction.IS_STRING, string="Океан Ельзи")
    db.create_smart_playlist("Live", [okean])
    frozen = SPLPrefs()
    frozen.liveupdate = False
    db.create_smart_playlist("Frozen", [okean], prefs=frozen)
    plain = db.create_playlist("Plain")
    db.add_track_to_playlist(plain, db.tracks[2])
    db.save()

    db = Database(ipod)
    db.tracks[2].artist = "Океан Ельзи"  # Старі фотографії now matches the rule
    db.save()  # no explicit refresh
    db = Database(ipod)
    assert _titles(db.get_playlist("Live")) == ["Там, де нас нема", "Човен", "Старі фотографії"]
    assert _titles(db.get_playlist("Frozen")) == ["Там, де нас нема", "Човен"]
    assert _titles(db.get_playlist("Plain")) == ["Старі фотографії"]

    db.tracks[0].artist = "Інший"
    db.save(refresh_smart_playlists=False)
    db = Database(ipod)
    assert _titles(db.get_playlist("Live")) == ["Там, де нас нема", "Човен", "Старі фотографії"]


def _limited(db, name, sort, limittype=SPLLimitType.SONGS, value=3, rules=()):
    prefs = SPLPrefs()
    prefs.checklimits = True
    prefs.limittype = limittype
    prefs.limitvalue = value
    prefs.limitsort = sort
    return db.create_smart_playlist(name, list(rules), prefs=prefs)


def test_playlist_membership_rules(ipod):
    """ "playlist is / is not" rules use the members of the playlist with id fromvalue."""
    db = Database(ipod)
    fav = db.create_playlist("Улюблені")
    for t in db.tracks[:2]:
        db.add_track_to_playlist(fav, t)
    inside = SPLRule(field=SPLField.PLAYLIST, action=SPLAction.IS_INT, fromvalue=fav.playlist_id)
    outside = SPLRule(
        field=SPLField.PLAYLIST, action=SPLAction.IS_NOT_INT, fromvalue=fav.playlist_id
    )
    unknown = SPLRule(field=SPLField.PLAYLIST, action=SPLAction.IS_INT, fromvalue=12345)
    assert _titles(db.create_smart_playlist("In", [inside])) == ["Там, де нас нема", "Човен"]
    assert _titles(db.create_smart_playlist("Out", [outside])) == [
        "Старі фотографії",
        "Мала",
        "Додому",
    ]
    assert _titles(db.create_smart_playlist("Nowhere", [unknown])) == []
    db.add_track_to_playlist(fav, db.tracks[4])  # membership follows the playlist on refresh
    db.save()
    assert _titles(Database(ipod).get_playlist("In")) == [
        "Там, де нас нема",
        "Човен",
        "Додому",
    ]


def test_limit_sorting_and_fitting(ipod):
    """Names ascending, most-* descending, least-* (high bit) ascending; a track that does not fit
    the limit is skipped and smaller ones after it are still taken (libgpod itdb_spl_update)."""
    db = Database(ipod)
    assert _titles(_limited(db, "AZ", SPLLimitSort.SONG_NAME)) == [
        "Додому",
        "Мала",
        "Старі фотографії",
    ]
    assert _titles(_limited(db, "Most", SPLLimitSort.MOST_OFTEN_PLAYED, value=2)) == [
        "Мала",
        "Там, де нас нема",
    ]
    assert _titles(_limited(db, "Least", SPLLimitSort.LEAST_OFTEN_PLAYED, value=2)) == [
        "Старі фотографії",
        "Додому",
    ]
    assert _titles(_limited(db, "Top", SPLLimitSort.HIGHEST_RATING, value=1)) == ["Човен"]
    for t, size in zip(db.tracks, (300_000, 2_000_000, 400_000, 300_000, 900_000)):
        t.record.fields["file_size"] = size
    small = _limited(db, "1MB", SPLLimitSort.SONG_NAME, limittype=SPLLimitType.MB, value=1)
    # by name: Додому (0.86 MB) fits, Мала (0.29) would exceed -> skipped,
    # Старі фотографії (0.38) too, Там, де нас нема (0.29) too, Човен (1.9) too
    assert _titles(small) == ["Додому"]


@pytest.mark.libgpod
def test_membership_and_limits_match_libgpod(ipod, tool):
    import subprocess

    db = Database(ipod)
    fav = db.create_playlist("Улюблені")
    for t in (db.tracks[0], db.tracks[3]):
        db.add_track_to_playlist(fav, t)
    db.create_smart_playlist(
        "Не улюблені",
        [SPLRule(field=SPLField.PLAYLIST, action=SPLAction.IS_NOT_INT, fromvalue=fav.playlist_id)],
    )
    _limited(db, "AZ3", SPLLimitSort.SONG_NAME)
    _limited(db, "Least2", SPLLimitSort.LEAST_OFTEN_PLAYED, value=2)
    _limited(db, "Top2", SPLLimitSort.HIGHEST_RATING, value=2)
    db.save()
    ours = {p.name: _titles(p) for p in Database(ipod).playlists if p.is_smart}
    out = subprocess.run([tool, "dump-spl", ipod], capture_output=True, text=True, check=True)
    theirs = {}
    for line in out.stdout.splitlines():
        f = line.split("\t")
        if f[0] == "P" and f[1] in ours:
            theirs[f[1]] = f[5].split("|") if f[5] else []
    assert theirs == ours
