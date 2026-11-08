import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import icalendar
import pytest

import writer.ics_writer as W
from core.config import load_config
from core.models import Event, RunContext
from storage.db import init_db
from writer.ics_writer import (
    event_content_hash,
    generate_calendar,
    render_event_lines,
    serialize_calendar,
)

TZ = ZoneInfo("Asia/Taipei")
DUR = timedelta(minutes=60)
SFX = "@market-calendar-ics"
T1 = datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 2, 2, 30, 5, tzinfo=timezone.utc)


def rem(days=1, hh=20):
    from datetime import time

    return SimpleNamespace(days_before=days, at=time(hh, 0))


def allday(n="a", d=date(2026, 11, 20), **kw):
    return Event(f"tw-{n}{SFX}", "tw-settlement", f"結算 {n}", d, True, **kw)


def timed(n="t", **kw):
    st = datetime(2026, 11, 13, 14, 0, tzinfo=TZ)
    return Event(f"tw-{n}{SFX}", "tw-earnings", f"法說 {n}", st, False, **kw)


def build(events, states_seq=0):
    rendered = [(e, render_event_lines(e, rem(), TZ, DUR)) for e in events]
    states = {
        e.uid: SimpleNamespace(uid=e.uid, sequence=states_seq, dtstamp="20261101T010000Z")
        for e, _ in rendered
    }
    return serialize_calendar("測試,行事曆;x", "Asia/Taipei", rendered, states)


def test_render_allday_and_timed():
    a = render_event_lines(allday(), rem(), TZ, DUR)
    assert "DTSTART;VALUE=DATE:20261120" in a and "DTEND;VALUE=DATE:20261121" in a
    assert "TRIGGER:-PT4H" in a
    t = render_event_lines(timed(), rem(), TZ, DUR)
    assert "DTSTART;TZID=Asia/Taipei:20261113T140000" in t
    assert "DTEND;TZID=Asia/Taipei:20261113T150000" in t
    assert "TRIGGER:-PT18H" in t


def test_render_end_before_start():
    with pytest.raises(ValueError):
        render_event_lines(timed(end=datetime(2026, 11, 13, 13, 0, tzinfo=TZ)), rem(), TZ, DUR)


def test_hash_behaviour():
    l1 = render_event_lines(allday(description="x"), rem(), TZ, DUR)
    l2 = render_event_lines(allday(description="x"), rem(), TZ, DUR)
    l3 = render_event_lines(allday(description="y"), rem(), TZ, DUR)
    l4 = render_event_lines(allday(description="x"), rem(2, 9), TZ, DUR)
    assert event_content_hash(l1) == event_content_hash(l2)
    assert event_content_hash(l1) != event_content_hash(l3)
    assert event_content_hash(l1) != event_content_hash(l4)


def test_alarm_actual_time():
    # 全天 11/20 → TRIGGER -PT4H 相對 00:00 → 前一天 20:00
    ev = allday()
    cal = icalendar.Calendar.from_ical(build([ev]))
    vev = cal.walk("VEVENT")[0]
    trig = vev.walk("VALARM")[0]["TRIGGER"].dt
    assert trig == timedelta(hours=-4)
    start = datetime.combine(vev["DTSTART"].dt, datetime.min.time(), tzinfo=TZ)
    assert start + trig == datetime(2026, 11, 19, 20, 0, tzinfo=TZ)


def test_serialize_parse():
    evs = [timed("b"), allday("a", description="日,說;明\n第二行"), allday("c", location="台北")]
    data = build(evs)
    cal = icalendar.Calendar.from_ical(data)
    vevs = cal.walk("VEVENT")
    assert len(vevs) == 3
    assert str(cal["X-WR-CALNAME"]).replace("\\", "") == "測試,行事曆;x"  # X- 屬性不反跳脫
    assert str(cal["X-WR-TIMEZONE"]) == "Asia/Taipei"
    for v in vevs:
        for k in ("UID", "DTSTAMP", "DTSTART"):
            assert k in v
        assert len(v.walk("VALARM")) == 1


def test_crlf_and_line_length():
    ev = allday(description="很長的說明" * 60 + "x" * 200)
    data = build([ev])
    assert data.endswith(b"\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"\r" not in data.replace(b"\r\n", b"")
    for ln in data.split(b"\r\n")[:-1]:
        assert len(ln) <= 75
    icalendar.Calendar.from_ical(data)


def test_empty_calendar():
    cal = icalendar.Calendar.from_ical(build([]))
    assert cal.walk("VEVENT") == []


def test_deterministic_and_sorted():
    evs = [allday("z", date(2026, 11, 21)), timed("b"), allday("a", date(2026, 11, 13))]
    assert build(evs) == build(list(reversed(evs)))
    uids = [str(v["UID"]) for v in icalendar.Calendar.from_ical(build(evs)).walk("VEVENT")]
    assert uids == [f"tw-a{SFX}", f"tw-b{SFX}", f"tw-z{SFX}"]


def test_duplicate_uid_and_missing_state():
    with pytest.raises(ValueError):
        build([allday("a"), allday("a")])
    e = allday("a")
    with pytest.raises(ValueError):
        serialize_calendar("n", "Asia/Taipei", [(e, render_event_lines(e, rem(), TZ, DUR))], {})


# ---------------------------------------------------------------- generate_calendar
@pytest.fixture
def ctx(tmp_path, prj_root, fixtures_dir):
    cfg = load_config(fixtures_dir / "config_test.yaml", tmp_path)
    conn = init_db(tmp_path / "c.db")
    c = RunContext(
        cfg=cfg, prj_root=tmp_path, today=date(2026, 11, 1), now_utc=T1,
        range_start=date(2026, 8, 1), range_end=date(2027, 11, 30),
        conn=conn, session=None, logger=logging.getLogger("t"),
    )
    yield c
    conn.close()


def seqs(path):
    cal = icalendar.Calendar.from_ical(path.read_bytes())
    return {str(v["UID"]): int(v["SEQUENCE"]) for v in cal.walk("VEVENT")}


def test_generate_idempotent_and_change(ctx):
    evs = [allday("a"), allday("b", date(2026, 11, 21))]
    p = generate_calendar(ctx, "tw-settlement", evs)
    assert p == ctx.cfg.paths.output_dir / "tw-settlement.ics"
    b1 = p.read_bytes()
    n = ctx.conn.execute("SELECT COUNT(*) FROM ics_event_state WHERE sequence=0").fetchone()[0]
    assert n == 2
    ctx.now_utc = T2
    generate_calendar(ctx, "tw-settlement", evs)
    assert p.read_bytes() == b1
    evs2 = [allday("a", description="changed"), evs[1]]
    generate_calendar(ctx, "tw-settlement", evs2)
    assert seqs(p) == {f"tw-a{SFX}": 1, f"tw-b{SFX}": 0}
    assert b"DTSTAMP:20261102T023005Z" in p.read_bytes()


def test_generate_failure_keeps_old(ctx, monkeypatch):
    evs = [allday("a")]
    p = generate_calendar(ctx, "tw-settlement", evs)
    old = p.read_bytes()
    rows = ctx.conn.execute("SELECT * FROM ics_event_state").fetchall()
    snap = [tuple(r) for r in rows]

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(W, "serialize_calendar", boom)
    ctx.now_utc = T2
    with pytest.raises(RuntimeError):
        generate_calendar(ctx, "tw-settlement", [allday("a", description="new")])
    assert p.read_bytes() == old
    assert [tuple(r) for r in ctx.conn.execute("SELECT * FROM ics_event_state")] == snap
