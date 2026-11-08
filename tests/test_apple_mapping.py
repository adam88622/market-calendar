import logging
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from apple.mapping import (
    build_payload,
    build_payloads,
    trigger_to_seconds,
    uid_from_url,
    url_for_uid,
)
from core.config import load_config
from core.models import Event, RunContext
from storage.db import init_db
from writer.alarm import format_alarm_trigger
from writer.ics_writer import generate_calendar

TZ = ZoneInfo("Asia/Taipei")
SFX = "@market-calendar-ics"


@pytest.fixture
def cfg(tmp_path, fixtures_dir):
    return load_config(fixtures_dir / "config_test.yaml", tmp_path)


def allday(n="a", d=date(2026, 11, 20), **kw):
    return Event(f"tw-{n}{SFX}", "tw-settlement", f"結算 {n}", d, True, **kw)


def timed(n="t", h=14, m=0, **kw):
    return Event(f"tw-{n}{SFX}", "tw-earnings", f"法說 {n}", datetime(2026, 11, 13, h, m, tzinfo=TZ), False, **kw)


def test_url_roundtrip_and_invalid():
    uid = f"tw-abc-123{SFX}"
    assert url_for_uid(uid) == f"market-calendar-ics://{uid}"
    assert uid_from_url(url_for_uid(uid)) == uid
    assert uid_from_url("https://x") is None
    assert uid_from_url(None) is None
    assert uid_from_url("market-calendar-ics://bad uid") is None
    assert uid_from_url("market-calendar-ics://abc") is None
    assert uid_from_url(f"market-calendar-ics://{SFX}") is None
    assert uid_from_url(f"market-calendar-ics://a_b{SFX}") is None


@pytest.mark.parametrize(
    "s,v",
    [("-PT4H", -14400), ("-PT18H30M", -66600), ("PT0M", 0), ("PT8H", 28800), ("-PT30M", -1800), ("PT45M", 2700)],
)
def test_trigger_to_seconds(s, v):
    assert trigger_to_seconds(s) == v


@pytest.mark.parametrize("s", ["", "PT", "-PT", "P1D", "PT1S", "xPT1H", "PT1H2", "--PT1H"])
def test_trigger_invalid(s):
    with pytest.raises(ValueError):
        trigger_to_seconds(s)


def _base_all(d, rem_day, at):
    return datetime.combine(rem_day, at) - datetime(d.year, d.month, d.day)


def test_fn013_consistency():
    D = date(2026, 11, 20)

    def dt(h, m=0):
        return datetime(2026, 11, 20, h, m, tzinfo=TZ)

    cases = [
        (D, True, 1, time(20, 0)),
        (D, True, 2, time(9, 0)),
        (D, True, 0, time(8, 0)),
        (dt(14), False, 1, time(20, 0)),
        (dt(14, 30), False, 1, time(20, 0)),
        (dt(21), False, 1, time(20, 0)),
        (dt(8), False, 0, time(8, 0)),
        (dt(20, 30), False, 0, time(20, 0)),
        (datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc), False, 1, time(20, 0)),
    ]
    for start, all_day, days, at in cases:
        trig = format_alarm_trigger(start, all_day, days, at, TZ)
        if all_day:
            base = datetime(start.year, start.month, start.day)
        else:
            base = start.astimezone(TZ).replace(tzinfo=None)
        remind = datetime.combine(base.date() - timedelta(days=days), time(at.hour, at.minute))
        assert trigger_to_seconds(trig) == int((remind - base).total_seconds())


def test_allday_payload(cfg):
    p = build_payload(allday(), cfg)
    assert p.all_day and p.start == p.end == date(2026, 11, 20)
    assert p.start_date == date(2026, 11, 20) and p.cal_id == "tw-settlement"
    assert p.alarm_offset_sec == -14400
    assert p.url == url_for_uid(p.uid)
    multi = build_payload(allday(end=date(2026, 11, 22)), cfg)
    assert multi.start == date(2026, 11, 20) and multi.end == date(2026, 11, 22)


def test_timed_payload(cfg):
    p = build_payload(timed(), cfg)
    assert p.end - p.start == timedelta(minutes=60)
    assert p.start.tzinfo.key == "Asia/Taipei" and p.end.tzinfo.key == "Asia/Taipei"
    assert p.alarm_offset_sec == -18 * 3600
    # UTC 輸入換算到 Taipei，start_date 用當地日期
    utc = Event(f"tw-u{SFX}", "tw-earnings", "x", datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc), False)
    pu = build_payload(utc, cfg)
    assert pu.start_date == date(2026, 11, 13) and pu.start.tzinfo.key == "Asia/Taipei"
    e = timed("e", end=datetime(2026, 11, 13, 16, 30, tzinfo=TZ))
    assert build_payload(e, cfg).end == datetime(2026, 11, 13, 16, 30, tzinfo=TZ)


def test_notes_location_hash(cfg):
    a = build_payload(allday(description="desc", location="L1"), cfg)
    b = build_payload(allday(description="desc", location="L2"), cfg)
    assert a.notes == "desc" and a.location == "L1"
    assert a.content_hash != b.content_hash


def test_hash_matches_ics_state(cfg, tmp_path):
    conn = init_db(tmp_path / "c.db")
    ctx = RunContext(
        cfg=cfg, prj_root=tmp_path, today=date(2026, 11, 1),
        now_utc=datetime(2026, 11, 1, 1, tzinfo=timezone.utc),
        range_start=date(2026, 8, 1), range_end=date(2027, 11, 30),
        conn=conn, session=None, logger=logging.getLogger("t"),
    )
    evs = [allday("a", description="x"), allday("b", date(2026, 11, 21), location="台北")]
    generate_calendar(ctx, "tw-settlement", evs)
    rows = dict(conn.execute("SELECT uid, content_hash FROM ics_event_state").fetchall())
    payloads = build_payloads(evs, cfg)
    assert len(rows) == 2
    for p in payloads:
        assert rows[p.uid] == p.content_hash
    conn.close()


def test_build_payloads_duplicate(cfg):
    with pytest.raises(ValueError):
        build_payloads([allday("a"), allday("a")], cfg)
    assert build_payloads([], cfg) == []
