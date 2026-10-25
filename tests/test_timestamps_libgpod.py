"""Track and playlist timestamps vs libgpod: the iPod keeps its local wall time.

libgpod converts iTunesDB times with the timezone of the iPod's Device/Preferences, else with
the host's current UTC offset; pygpod does the same by default (DatabaseConfig.timestamps).
"""

import datetime
import os
import struct
import subprocess
import time

import pytest

from pygpod.device.mountpoint import init_ipod
from pygpod.model.config import DatabaseConfig
from pygpod.model.database import Database

from .conftest import _make_minimal_mp3

GUID = "000A2700213749FF"
SYSINFO = f"ModelNumStr: xB029\nFirewireGuid: 0x{GUID}\n".encode()  # Classic: hash58
MAC_EPOCH = 2082844800
UTC = datetime.timezone.utc
ZONES = ["UTC", "Europe/Lisbon", "Asia/Tokyo", "America/New_York"]

PLAYED = datetime.datetime(2024, 6, 15, 12, 30, tzinfo=UTC)
RELEASED = datetime.datetime(2003, 5, 12, 0, 0, tzinfo=UTC)


def _ipod(tmp_path, name="ipod"):
    mp = str(tmp_path / name)
    os.makedirs(mp)
    init_ipod(mp, name="iPod", sysinfo_files={"SysInfo": SYSINFO})
    return mp


def _dump_times(tool, mp):
    out = subprocess.run(
        [tool, "dump-times", mp], capture_output=True, text=True, check=True
    ).stdout
    tracks, playlists = {}, {}
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "W":
            tracks[f[1]] = [int(v) for v in f[2:]]
        elif f[0] == "Y":
            playlists[f[1]] = int(f[2])
    return tracks, playlists


def _add(db, tmp_path, title):
    mp3 = str(tmp_path / f"{title}.mp3")
    _make_minimal_mp3(mp3)
    return db.add_track(mp3, title=title, artist="Океан Ельзи")


def _set_prefs_city(mp, city):
    """A Classic-format Device/Preferences (2952 bytes) with the iPod's city index."""
    data = bytearray(2952)
    struct.pack_into("<h", data, 0xB70, city)
    with open(os.path.join(mp, "iPod_Control", "Device", "Preferences"), "wb") as f:
        f.write(data)


@pytest.mark.libgpod
@pytest.mark.parametrize("zone", ZONES)
def test_times_written_by_pygpod_read_by_libgpod(tool, tmp_path, host_tz, zone):
    host_tz(zone)
    mp = _ipod(tmp_path)
    db = Database(mp)
    assert db.timezone_shift == time.localtime().tm_gmtoff
    before = int(time.time())
    t = _add(db, tmp_path, "Обійми")
    t.time_played = PLAYED
    t.time_released = RELEASED
    db.set_playlist("Найкраще", [t])
    db.save()
    after = int(time.time())

    tracks, playlists = _dump_times(tool, mp)
    added, modified, played, released, _ = tracks["Обійми"]
    assert played == int(PLAYED.timestamp())
    assert released == int(RELEASED.timestamp())
    assert before <= added <= after and before <= modified <= after
    assert before - 1 <= playlists["Найкраще"] <= after
    assert before - 5 <= playlists["iPod"] <= after  # created by init_ipod

    again = Database(mp).get_track(t.track_id)
    assert again.time_played == PLAYED and again.time_released == RELEASED
    assert before <= again.time_added.timestamp() <= after


@pytest.mark.libgpod
@pytest.mark.parametrize("zone", ZONES)
def test_times_written_by_libgpod_read_by_pygpod(tool, tmp_path, host_tz, zone):
    host_tz(zone)
    mp = str(tmp_path / "lg")
    for d in ("iTunes", "Device", "Music/F00"):
        os.makedirs(os.path.join(mp, "iPod_Control", d))
    with open(os.path.join(mp, "iPod_Control", "Device", "SysInfo"), "wb") as f:
        f.write(SYSINFO)
    mp3 = str(tmp_path / "a.mp3")
    _make_minimal_mp3(mp3)
    added, modified = 1700000000, 1700003600
    spec = tmp_path / "spec.tsv"
    spec.write_text(
        f"track\t{mp3}\tҐанок\tОкеан Ельзи\tЗемля\tRock\t1\t2013\t1\t0\t0\n"
        f"times\t0\t{added}\t{modified}\t{int(PLAYED.timestamp())}\t"
        f"{int(RELEASED.timestamp())}\n",
        encoding="utf-8",
    )
    subprocess.run([tool, "build", mp, str(spec)], check=True)
    assert _dump_times(tool, mp)[0]["Ґанок"][:4] == [
        added,
        modified,
        int(PLAYED.timestamp()),
        int(RELEASED.timestamp()),
    ]
    t = Database(mp).tracks[0]
    assert t.time_added == datetime.datetime.fromtimestamp(added, UTC)
    assert t.time_modified == datetime.datetime.fromtimestamp(modified, UTC)
    assert t.time_played == PLAYED
    assert t.time_released == RELEASED
    # the raw value is the local wall time
    shift = time.localtime().tm_gmtoff
    assert t.record.fields["time_played"] == int(PLAYED.timestamp()) + MAC_EPOCH + shift


@pytest.mark.libgpod
@pytest.mark.parametrize("city,shift", [(0xC2, 9 * 3600), (0x4F, None)])  # Tokyo, London
def test_the_ipods_timezone_wins_over_the_host(tool, tmp_path, host_tz, city, shift):
    host_tz("America/New_York")
    mp = _ipod(tmp_path)
    _set_prefs_city(mp, city)
    db = Database(mp)
    if shift is None:  # London: GMT or BST, whichever is current
        shift = int(
            datetime.datetime.now(UTC)
            .astimezone(_zone("Europe/London"))
            .utcoffset()
            .total_seconds()
        )
    assert db.timezone_shift == shift
    t = _add(db, tmp_path, "Човен")
    t.time_played = PLAYED
    db.save()
    assert t.record.fields["time_played"] == int(PLAYED.timestamp()) + MAC_EPOCH + shift
    assert _dump_times(tool, mp)[0]["Човен"][2] == int(PLAYED.timestamp())
    assert Database(mp).get_track(t.track_id).time_played == PLAYED


def _zone(name):
    try:
        import zoneinfo
    except ImportError:
        pytest.skip("zoneinfo needs Python 3.9")
    return zoneinfo.ZoneInfo(name)


@pytest.mark.libgpod
def test_utc_mode_keeps_the_old_values(tool, tmp_path, host_tz):
    host_tz("Asia/Tokyo")
    mp = _ipod(tmp_path)
    db = Database(mp, config=DatabaseConfig(timestamps="utc"))
    assert db.timezone_shift == 0
    t = _add(db, tmp_path, "Без бою")
    t.time_played = PLAYED
    db.save()
    assert t.record.fields["time_played"] == int(PLAYED.timestamp()) + MAC_EPOCH
    assert Database(mp, config=DatabaseConfig(timestamps="utc")).tracks[0].time_played == PLAYED
    # read in the default local mode the same value is the iPod's (Tokyo) wall time
    local = Database(mp).tracks[0].time_played
    assert local == PLAYED - datetime.timedelta(hours=9)


def test_unknown_timestamps_mode_is_refused(tmp_path):
    mp = _ipod(tmp_path)
    with pytest.raises(ValueError, match="timestamps"):
        Database(mp, config=DatabaseConfig(timestamps="device"))


@pytest.mark.parametrize("zone", ["Asia/Tokyo", "America/New_York"])
def test_smart_playlist_in_the_last_uses_the_ipods_time(tmp_path, host_tz, zone):
    from pygpod.model.smartplaylist import SPLAction, SPLField, SPLRule, eval_rule

    host_tz(zone)
    db = Database(_ipod(tmp_path))
    t = _add(db, tmp_path, "Обійми")
    t.time_played = datetime.datetime.now(UTC) - datetime.timedelta(hours=2)

    def in_last(hours):
        rule = SPLRule(
            field=SPLField.LAST_PLAYED, action=SPLAction.IS_IN_THE_LAST, fromvalue=hours * 3600
        )
        return eval_rule(rule, t)

    assert in_last(3) and not in_last(1)


@pytest.mark.parametrize(
    "when",
    [
        datetime.datetime(1904, 1, 1, 1, 0, tzinfo=UTC),  # before 1904 in New York time
        datetime.datetime(1903, 12, 31, tzinfo=UTC),
    ],
)
def test_dates_before_1904_are_clamped_not_wrapped(tmp_path, host_tz, caplog, when):
    host_tz("America/New_York")
    mp = _ipod(tmp_path)
    db = Database(mp)
    assert db.timezone_shift < 0
    t = _add(db, tmp_path, "Ґанок")
    with caplog.at_level("WARNING", logger="pygpod.utils.datetime"):
        t.time_released = when
        t.time_played = when
    assert "outside the iPod's timestamp range" in caplog.text
    assert t.record.fields["time_released"] == 1
    assert t.record.fields["time_played"] == 1
    db.save()
    back = Database(mp).get_track(t.track_id)
    for value in (back.time_released, back.time_played):
        assert value == datetime.datetime.min or value.year in (1903, 1904)
    # datetime.min still means "not set"
    t.time_released = datetime.datetime.min
    assert t.record.fields["time_released"] == 0
