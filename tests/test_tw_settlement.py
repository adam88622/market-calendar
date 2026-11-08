"""FN-020、FN-021、FN-036 測試（含規則日期驗收）。"""
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from core.business_calendar import BusinessCalendar
from core.config import WeeklySeriesConfig, load_config
from core.holidays import build_taifex_calendars
from processors.tw_settlement import (
    build_tw_settlement_events,
    compute_tw_monthly_settlement,
    compute_tw_weekly_settlements,
)

PRJ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cfg():
    return load_config(PRJ / "config.yaml", PRJ)


@pytest.fixture(scope="module")
def cal(cfg):
    return build_taifex_calendars(cfg, date(2026, 1, 1), date(2027, 12, 31))[0]


def wk(policy="next", enabled=True):
    return WeeklySeriesConfig(enabled=enabled, holiday_policy=policy)


# ---- FN-020 ----
def test_monthly_basic(cal):
    assert compute_tw_monthly_settlement(2026, 11, cal).adjusted == date(2026, 11, 18)
    assert compute_tw_monthly_settlement(2026, 11, cal).reason == ""
    assert compute_tw_monthly_settlement(2026, 10, cal).adjusted == date(2026, 10, 21)
    assert compute_tw_monthly_settlement(2026, 12, cal).adjusted == date(2026, 12, 16)


def test_monthly_cny(cal):
    sd = compute_tw_monthly_settlement(2026, 2, cal)
    assert sd.original == date(2026, 2, 18)
    assert sd.adjusted == date(2026, 2, 23)
    assert "春節" in sd.reason


def test_monthly_2027_09_provisional(cal):
    sd = compute_tw_monthly_settlement(2027, 9, cal)
    assert (sd.original, sd.adjusted) == (date(2027, 9, 15), date(2027, 9, 16))


# ---- FN-021 ----
def weekly(cal, start, end, wed="next", fri="next", monthly=None):
    return compute_tw_weekly_settlements(start, end, cal, wk(wed), wk(fri), monthly or set())


def test_weekly_2026_11(cal):
    res = weekly(cal, date(2026, 11, 1), date(2026, 11, 30), monthly={date(2026, 11, 18)})
    wed = [s.adjusted for k, s in res if k == "wed"]
    fri = [s.adjusted for k, s in res if k == "fri"]
    assert wed == [date(2026, 11, 4), date(2026, 11, 11), date(2026, 11, 25)]
    assert fri == [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27)]


@pytest.mark.parametrize(
    "policy,expected",
    [("next", [date(2026, 6, 22)]), ("previous", [date(2026, 6, 18)]), ("skip", [])],
)
def test_weekly_dragon_boat(cal, policy, expected):
    res = weekly(cal, date(2026, 6, 19), date(2026, 6, 19), fri=policy)
    assert [s.adjusted for k, s in res if k == "fri"] == expected


def test_weekly_disabled_series(cal):
    res = compute_tw_weekly_settlements(
        date(2026, 11, 1), date(2026, 11, 30), cal, wk(enabled=False), wk(), set()
    )
    assert {k for k, _ in res} == {"fri"}


def test_weekly_r3_same_series_dedup_previous(cal):
    res = weekly(cal, date(2026, 2, 1), date(2026, 2, 28), fri="previous", monthly={date(2026, 2, 23)})
    fri = [s for k, s in res if k == "fri"]
    on_211 = [s for s in fri if s.adjusted == date(2026, 2, 11)]
    assert len(on_211) == 1
    assert on_211[0].original == date(2026, 2, 13)
    assert "併入原定 2026-02-20" in on_211[0].reason
    assert len({s.adjusted for s in fri}) == len(fri)


def test_weekly_r3_next_removed_by_monthly(cal):
    res = weekly(cal, date(2026, 2, 1), date(2026, 2, 28), fri="next", monthly={date(2026, 2, 23)})
    fri = [s.adjusted for k, s in res if k == "fri"]
    assert date(2026, 2, 23) not in fri
    assert fri == [date(2026, 2, 6), date(2026, 3, 2)]


def test_weekly_dedup_against_monthly_adjusted():
    # 構造：週三 2026-11-11 休市順延到 11-12；若月結算調整日剛好是 11-12 則丟棄
    c = BusinessCalendar("期交所", {date(2026, 11, 11): "颱風"})
    res = compute_tw_weekly_settlements(
        date(2026, 11, 11), date(2026, 11, 11), c, wk(), wk(enabled=False), {date(2026, 11, 12)}
    )
    assert res == []
    res = compute_tw_weekly_settlements(
        date(2026, 11, 11), date(2026, 11, 11), c, wk(), wk(enabled=False), set()
    )
    assert [s.adjusted for _, s in res] == [date(2026, 11, 12)]


# ---- FN-036 ----
def test_events_2026_11(cfg, cal):
    ev = build_tw_settlement_events(cfg, date(2026, 11, 1), date(2026, 11, 30), cal)
    monthly = [e for e in ev if e.uid.startswith("tw-monthly")]
    wed = [e for e in ev if e.uid.startswith("tw-weekly-wed")]
    fri = [e for e in ev if e.uid.startswith("tw-weekly-fri")]
    assert [e.start for e in monthly] == [date(2026, 11, 18)]
    assert len(wed) == 3 and len(fri) == 4
    assert monthly[0].uid == "tw-monthly-settle-202611@market-calendar-ics"
    assert wed[0].uid == "tw-weekly-wed-settle-20261104@market-calendar-ics"
    assert len({e.uid for e in ev}) == len(ev)
    assert all(e.all_day and e.source == "rule" and e.calendar == "tw-settlement" for e in ev)
    assert monthly[0].description.splitlines()[-1] == "來源：規則計算（期交所休市表）"


def test_events_2026_02_adjusted_line(cfg, cal):
    ev = build_tw_settlement_events(cfg, date(2026, 2, 1), date(2026, 2, 28), cal)
    m = [e for e in ev if e.uid.startswith("tw-monthly")]
    assert m[0].start == date(2026, 2, 23)
    assert "原定 2026-02-18（三）→ 調整 2026-02-23（一）" in m[0].description
    assert "期交所休市（春節）" in m[0].description


def test_events_whole_range_invariants(cfg, cal):
    start, end = date(2026, 8, 1), date(2027, 11, 30)
    ev = build_tw_settlement_events(cfg, start, end, cal)
    assert len({e.uid for e in ev}) == len(ev)
    seen = set()
    for e in ev:
        key = (e.uid.rsplit("-", 1)[0].split("settle")[0], e.start)
        assert key not in seen
        seen.add(key)
    monthly_days = {e.start for e in ev if e.uid.startswith("tw-monthly")}
    assert all(e.start not in monthly_days for e in ev if "weekly" in e.uid)


def test_events_weekly_policy_from_config(cfg, cal):
    c2 = replace(cfg, tw_settlement=replace(cfg.tw_settlement, weekly_fri=wk("skip")))
    ev = build_tw_settlement_events(c2, date(2026, 6, 1), date(2026, 6, 30), cal)
    fri = {e.start for e in ev if "weekly-fri" in e.uid}
    assert fri == {date(2026, 6, 5), date(2026, 6, 12), date(2026, 6, 26)}
    c3 = replace(cfg, tw_settlement=replace(cfg.tw_settlement, weekly_fri=wk("previous")))
    ev = build_tw_settlement_events(c3, date(2026, 6, 1), date(2026, 6, 30), cal)
    fri = sorted(e.start for e in ev if "weekly-fri" in e.uid)
    assert fri == [date(2026, 6, 5), date(2026, 6, 12), date(2026, 6, 18), date(2026, 6, 26)]
