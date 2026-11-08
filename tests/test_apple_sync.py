"""FN-053 sync_calendar、FN-054 sync_apple_calendars（Fake adapter + tmp DB）。"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from apple.models import APPLE_MAP_VERSION, AppleCalendarError
from apple.sync import sync_apple_calendars, sync_calendar
from core.config import load_config
from core.models import Event, RunContext
from storage.apple_map_store import get_calendar_map, load_event_map
from storage.db import init_db
from tests.fake_calendar_adapter import FakeCalendarAdapter

TZ = ZoneInfo("Asia/Taipei")
SFX = "@market-calendar-ics"
TODAY = date(2026, 11, 8)
NOW = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
RS, RE = date(2026, 8, 1), date(2027, 11, 30)
CAL = "tw-earnings"
CALS = ["tw-settlement", "us-settlement", "tw-earnings", "us-earnings"]
LOG = logging.getLogger("t-sync")


@pytest.fixture
def cfg(tmp_path, fixtures_dir):
    return load_config(fixtures_dir / "config_test_apple.yaml", tmp_path)


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def fake():
    return FakeCalendarAdapter()


def ev(n, d, cal=CAL, location="", **kw):
    return Event(f"{cal}-{n}{SFX}", cal, f"事件 {n}", d, True, location=location,
                 description=f"desc {n}", **kw)


def timed_ev(n="t", cal=CAL):
    return Event(f"{cal}-{n}{SFX}", cal, f"法說 {n}",
                 datetime(2026, 11, 13, 14, 0, tzinfo=TZ), False, location="台北")


def make_events(cal=CAL):
    # 6 未來全天 + 2 過去全天 + 1 未來 timed = 9
    evs = [ev(f"f{i}", date(2026, 11, 10 + i), cal) for i in range(6)]
    evs += [ev("p1", date(2026, 10, 20), cal), ev("p2", date(2026, 10, 21), cal)]
    evs.append(timed_ev("t", cal))
    return evs


def sync(fake, conn, cfg, events, *, cal_id=CAL, range_end=RE, **kw):
    return sync_calendar(
        fake, conn, cfg, cal_id, events, today=TODAY, now_utc=NOW,
        range_start=RS, range_end=range_end, logger=LOG, **kw,
    )


def cal_ident(conn, cal_id=CAL):
    return get_calendar_map(conn, cal_id).calendar_identifier


def count(fake, conn, cal_id=CAL):
    return len(fake.events_of(cal_ident(conn, cal_id)))


# ------------------------------------------------------------ FN-053
def test_1_first_sync(fake, conn, cfg):
    evs = make_events()
    r = sync(fake, conn, cfg, evs)
    assert r.created == 9 and r.ok and r.calendar_identifier == cal_ident(conn)
    stored = {e["uid"]: e for e in fake.events_of(cal_ident(conn))}
    assert len(stored) == 9
    e = stored[f"tw-earnings-f0{SFX}"]
    assert e["url"] == f"market-calendar-ics://tw-earnings-f0{SFX}"
    assert e["notes"] == "desc f0" and e["all_day"] is True
    assert e["alarm_offset_sec"] < 0
    t = stored[f"tw-earnings-t{SFX}"]
    assert t["location"] == "台北" and t["all_day"] is False
    assert len(load_event_map(conn, CAL)) == 9
    assert fake.op_names().count("commit") == 1
    assert all(r.map_version == APPLE_MAP_VERSION and r.event_identifier
               for r in load_event_map(conn, CAL).values())
    # mark_managed 先於任何 create
    names = fake.op_names()
    assert names.index("mark_managed") < names.index("create")


def test_2_second_sync_noop(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    fake.ops.clear()
    r = sync(fake, conn, cfg, evs)
    assert (r.created, r.updated, r.deleted) == (0, 0, 0) and r.unchanged == 9
    assert fake.op_names().count("commit") == 1
    assert not {"create", "update", "delete"} & set(fake.op_names())
    assert count(fake, conn) == 9


def test_3_update_location(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    evs[0] = ev("f0", date(2026, 11, 10), location="新地點")
    r = sync(fake, conn, cfg, evs)
    assert r.updated == 1 and r.created == 0
    stored = {e["uid"]: e for e in fake.events_of(cal_ident(conn))}
    assert stored[f"tw-earnings-f0{SFX}"]["location"] == "新地點"
    assert count(fake, conn) == 9


def test_4_remove_future_and_past(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    evs2 = [e for e in evs if not e.uid.startswith("tw-earnings-f5")]
    r = sync(fake, conn, cfg, evs2)
    assert r.deleted == 1 and r.kept_past == 0
    assert count(fake, conn) == 8
    evs3 = [e for e in evs2 if not e.uid.startswith("tw-earnings-p1")]
    r = sync(fake, conn, cfg, evs3)
    assert r.deleted == 0 and r.kept_past == 1
    assert count(fake, conn) == 8
    assert f"tw-earnings-p1{SFX}" in {e["uid"] for e in fake.events_of(cal_ident(conn))}


def test_5_user_deleted_event_recreated(fake, conn, cfg, caplog):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    assert fake.user_delete_event(f"tw-earnings-f1{SFX}") == 1
    with caplog.at_level(logging.INFO, logger="t-sync"):
        r = sync(fake, conn, cfg, evs)
    assert r.created == 1 and r.recreated == 1
    assert count(fake, conn) == 9
    assert any("recreated uids" in m for m in caplog.messages)


def test_6_user_deleted_calendar(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    old = cal_ident(conn)
    fake.user_delete_calendar(old)
    r = sync(fake, conn, cfg, evs)
    new = cal_ident(conn)
    assert new != old and r.created == 9
    assert len(fake.events_of(new)) == 9
    rows = load_event_map(conn, CAL)
    assert len(rows) == 9 and all(x.calendar_identifier == new for x in rows.values())


def test_7_map_cleared(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    conn.execute("DELETE FROM apple_event_map")
    conn.commit()
    r = sync(fake, conn, cfg, evs)
    assert r.created == 0 and r.updated == 9
    assert count(fake, conn) == 9
    assert len(load_event_map(conn, CAL)) == 9


def test_8_duplicates(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    ident = cal_ident(conn)
    src = fake.events_of(ident)[0]
    item = fake.add_foreign_event(ident, url=src["url"], start_date=src["start_date"])
    assert item
    assert count(fake, conn) == 10
    r = sync(fake, conn, cfg, evs)
    assert r.duplicates_deleted == 1
    assert count(fake, conn) == 9


def test_9_foreign_untouched(fake, conn, cfg):
    other = fake.add_calendar("家庭")
    fake.add_foreign_event(other.identifier, start_date=date(2026, 11, 20))
    fake.add_foreign_event(other.identifier, start_date=date(2026, 12, 20),
                           url=f"market-calendar-ics://zz-1{SFX}")  # 他處的本專案 URL 也不碰
    evs = make_events()
    sync(fake, conn, cfg, evs)
    ident = cal_ident(conn)
    mine = fake.add_foreign_event(ident, title="手動加", start_date=date(2026, 11, 25))
    fake.ops.clear()
    r = sync(fake, conn, cfg, evs[:6] + evs[6:])
    assert r.deleted == 0 and r.ok
    assert mine in fake.events[ident]
    assert len(fake.events[other.identifier]) == 2
    assert not any(other.identifier in str(o) for o in fake.ops)


def test_10_commit_fail(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    before_events = fake.events_of(cal_ident(conn))
    before_map = load_event_map(conn, CAL)
    evs[0] = ev("f0", date(2026, 11, 10), location="X")
    fake.fail_on["commit"] = {"*"}
    fake.ops.clear()
    with pytest.raises(AppleCalendarError):
        sync(fake, conn, cfg, evs)
    assert "rollback" in fake.op_names()
    assert fake.staged_count == 0
    assert fake.events_of(cal_ident(conn)) == before_events
    assert load_event_map(conn, CAL) == before_map


def test_11_single_create_fail(fake, conn, cfg):
    evs = make_events()
    bad = evs[2].uid
    fake.fail_on["create"] = {bad}
    r = sync(fake, conn, cfg, evs)
    assert len(r.errors) == 1 and bad in r.errors[0] and not r.ok
    assert r.created == 8
    assert count(fake, conn) == 8
    assert len(load_event_map(conn, CAL)) == 8 and bad not in load_event_map(conn, CAL)
    fake.fail_on.clear()
    r2 = sync(fake, conn, cfg, evs)
    assert r2.ok and r2.created == 1 and count(fake, conn) == 9


def test_12_deadline(fake, conn, cfg, caplog):
    evs = make_events()
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        return 0.0 if calls["n"] <= 2 else 100.0

    with caplog.at_level(logging.WARNING, logger="t-sync"):
        r = sync(fake, conn, cfg, evs, deadline=50.0, clock=clock)
    assert r.created == 2 and r.deferred == 7
    assert fake.op_names().count("commit") == 1
    assert count(fake, conn) == 2 and len(load_event_map(conn, CAL)) == 2
    assert any("deferred" in m for m in caplog.messages)
    r2 = sync(fake, conn, cfg, evs)
    assert r2.created == 7 and r2.deferred == 0 and count(fake, conn) == 9


def test_12b_deferred_delete_not_dropped(fake, conn, cfg):
    evs = make_events()
    sync(fake, conn, cfg, evs)
    evs2 = evs[:5] + evs[6:]  # 少 f5
    r = sync(fake, conn, cfg, evs2, deadline=1.0, clock=lambda: 5.0)
    assert r.deferred == 1 and r.deleted == 0
    assert f"tw-earnings-f5{SFX}" in load_event_map(conn, CAL)
    r2 = sync(fake, conn, cfg, evs2)
    assert r2.deleted == 1 and f"tw-earnings-f5{SFX}" not in load_event_map(conn, CAL)


def test_13_guard(fake, conn, cfg, caplog):
    evs = [ev(f"g{i}", date(2026, 11, 10 + i)) for i in range(10)]
    sync(fake, conn, cfg, evs)
    with caplog.at_level(logging.WARNING, logger="t-sync"):
        r = sync(fake, conn, cfg, evs[:3])
    assert r.guarded and r.deleted == 0
    assert count(fake, conn) == 10
    assert len(load_event_map(conn, CAL)) == 10
    assert any("guard" in m and "7" in m for m in caplog.messages)


def test_14_window_shrink(fake, conn, cfg):
    far = ev("far", date(2027, 6, 1))
    evs = make_events() + [far]
    sync(fake, conn, cfg, evs, range_end=date(2027, 11, 30))
    assert count(fake, conn) == 10
    r = sync(fake, conn, cfg, make_events(), range_end=date(2027, 3, 31))
    assert r.deleted == 1
    assert count(fake, conn) == 9


def test_r11_unadopted_same_name_candidate_untouched(fake, conn, cfg):
    name = cfg.calendars[CAL].name
    a = fake.add_calendar(name, identifier="fake-cal-a")
    b = fake.add_calendar(name, identifier="fake-cal-b")
    url = f"market-calendar-ics://old-1{SFX}"
    ia = fake.add_foreign_event(a.identifier, url=url, start_date=date(2026, 11, 30))
    ib = fake.add_foreign_event(b.identifier, url=url, start_date=date(2026, 11, 30))
    sync(fake, conn, cfg, make_events())
    assert cal_ident(conn) == "fake-cal-a"
    assert fake.managed == {"fake-cal-a"}
    # 接手的 A 內舊事件（未來、已不在來源）被刪；B 完全不動
    assert ia not in fake.events["fake-cal-a"]
    assert ib in fake.events["fake-cal-b"]
    rem_b = fake.fetch_events("fake-cal-b", RS, RE)[0]
    payload = __import__("apple.mapping", fromlist=["x"]).build_payloads(make_events(), cfg)[0]
    with pytest.raises(AppleCalendarError):
        fake.delete_event(rem_b)
    with pytest.raises(AppleCalendarError):
        fake.update_event(rem_b, payload)
    with pytest.raises(AppleCalendarError):
        fake.create_event("fake-cal-b", payload)
    fake.commit()
    assert list(fake.events["fake-cal-b"]) == [ib]


def test_ensure_failure_propagates(fake, conn, cfg):
    fake.sources = []
    with pytest.raises(AppleCalendarError):
        sync(fake, conn, cfg, make_events())


# ------------------------------------------------------------ FN-054
@pytest.fixture
def ctx(tmp_path, cfg, conn):
    return RunContext(cfg=cfg, prj_root=tmp_path, today=TODAY, now_utc=NOW,
                      range_start=RS, range_end=RE, conn=conn, session=None, logger=LOG)


def produced_all():
    return {c: make_events(c) for c in CALS}


def test_54_all_ok(fake, ctx):
    assert sync_apple_calendars(ctx, produced_all(), fake) == []
    for c in CALS:
        assert count(fake, ctx.conn, c) == 9
    # 4 個行事曆互不相同
    assert len({cal_ident(ctx.conn, c) for c in CALS}) == 4


def test_54_denied(fake, ctx, caplog):
    fake.set_status("denied")
    with caplog.at_level(logging.ERROR, logger="t-sync"):
        assert sync_apple_calendars(ctx, produced_all(), fake) == ["apple:authorization"]
    assert any(r.levelno == logging.ERROR and "--authorize" in r.getMessage()
               and "MarketCalendarSync.app" in r.getMessage() for r in caplog.records)
    assert not {"create", "update", "delete", "commit", "create_calendar"} & set(fake.op_names())


def test_54_missing_produced_untouched(fake, ctx):
    assert sync_apple_calendars(ctx, produced_all(), fake) == []
    us = cal_ident(ctx.conn, "us-settlement")
    fake.add_foreign_event(us, title="owned", url=f"market-calendar-ics://xx-1{SFX}",
                           start_date=date(2026, 12, 1))
    before = fake.events_of(us)
    fake.ops.clear()
    p = produced_all()
    del p["us-settlement"]
    assert sync_apple_calendars(ctx, p, fake) == []
    assert fake.events_of(us) == before
    assert not any(us in str(o) for o in fake.ops)
    assert sum(1 for o in fake.ops if o[0] == "commit") == 3


def test_54_fetch_failure_isolated(fake, ctx):
    assert sync_apple_calendars(ctx, produced_all(), fake) == []
    fake.fail_on["fetch"] = {cal_ident(ctx.conn, "tw-earnings")}
    fake.ops.clear()
    assert sync_apple_calendars(ctx, produced_all(), fake) == ["apple:tw-earnings"]
    assert sum(1 for o in fake.ops if o[0] == "commit") == 3


def test_54_single_error_marks_failure(fake, ctx):
    p = produced_all()
    fake.fail_on["create"] = {p["us-earnings"][0].uid}
    assert sync_apple_calendars(ctx, p, fake) == ["apple:us-earnings"]


def test_54_factory_importerror(ctx):
    def boom():
        raise ImportError("no EventKit")

    assert sync_apple_calendars(ctx, produced_all(), None, adapter_factory=boom) == ["apple:init"]


def test_54_default_factory_disabled_under_tests(ctx):
    assert sync_apple_calendars(ctx, produced_all(), None) == ["apple:init"]


def test_54_factory_used(fake, ctx):
    assert sync_apple_calendars(ctx, produced_all(), None, adapter_factory=lambda: fake) == []
    assert count(fake, ctx.conn, "tw-settlement") == 9


def test_54_deadline_skips(fake, ctx, caplog):
    with caplog.at_level(logging.WARNING, logger="t-sync"):
        assert sync_apple_calendars(ctx, produced_all(), fake, deadline=1.0,
                                    clock=lambda: 2.0) == []
    assert "create" not in fake.op_names()
    assert any("time budget exhausted" in m for m in caplog.messages)


def test_54_exclude_ids_prevents_cross_adoption(fake, ctx):
    # tw-settlement 已有行事曆；tw-earnings 不得接手它（即使同名）
    assert sync_apple_calendars(ctx, produced_all(), fake) == []
    ids = {c: cal_ident(ctx.conn, c) for c in CALS}
    assert len(set(ids.values())) == 4
