"""FN-064 測試（合成輸入）。"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.models import EarlyClose, HolidayName, MarketClosures
from processors.global_holidays import build_global_holiday_events, us_early_close_label

NY = ZoneInfo("America/New_York")
TPE = ZoneInfo("Asia/Taipei")
S, E = date(2026, 8, 1), date(2027, 11, 30)


def _build(closures=None, names=None, start=S, end=E):
    if closures is None:
        closures = [
            MarketClosures("us", (date(2026, 11, 26),),
                           (EarlyClose(date(2026, 11, 27), datetime(2026, 11, 27, 13, 0, tzinfo=NY)),)),
            MarketClosures("jp", (date(2026, 9, 22), date(2026, 9, 24)), ()),
            MarketClosures("kr", (date(2026, 9, 24),), ()),
        ]
    if names is None:
        names = {
            "us": {date(2026, 11, 26): HolidayName("感恩節", "Thanksgiving Day", "pandas_market_calendars")},
            "jp": {date(2026, 9, 22): HolidayName("国民の休日", "国民の休日", "holidays")},
            "kr": {date(2026, 9, 24): HolidayName("中秋（秋夕）前一日", "The day preceding Chuseok", "holidays")},
        }
    return build_global_holiday_events(start, end, closures, names, TPE)


def _by_uid(evs):
    return {e.uid: e for e in evs}


def test_uids_and_titles():
    m = _by_uid(_build())
    sfx = "@market-calendar-ics"
    assert m["gh-us-closed-20261126" + sfx].title == "美股休市：感恩節"
    assert m["gh-us-early-20261127" + sfx].title == "美股提早收盤（13:00 ET）：感恩節翌日"
    assert m["gh-jp-closed-20260922" + sfx].title == "日股休市：国民の休日"
    assert m["gh-kr-closed-20260924" + sfx].title.startswith("韓股休市：中秋")


def test_descriptions():
    m = _by_uid(_build())
    early = m["gh-us-early-20261127@market-calendar-ics"].description
    assert "提早收盤：13:00 ET（美東）＝ 台北時間 2026-11-28（六）02:00" in early.split("\n")
    closed = m["gh-us-closed-20261126@market-calendar-ics"].description
    assert "節日：感恩節（原文：Thanksgiving Day）" in closed
    assert "日期：2026-11-26（四）" in closed
    assert "節日名稱：pandas_market_calendars" in closed
    for e in m.values():
        assert "以交易所公告為準" in e.description and "pandas_market_calendars" in e.description
        assert e.all_day and e.calendar == "global-holidays" and e.end is None


def test_dst_taipei_time():
    c = [MarketClosures("us", (date(2026, 12, 25),),
                        (EarlyClose(date(2026, 7, 3), datetime(2026, 7, 3, 13, 0, tzinfo=NY)),))]
    evs = _build(c, {}, start=date(2026, 6, 1))
    early = [e for e in evs if "early" in e.uid][0]
    assert "台北時間 2026-07-04（六）01:00" in early.description


def test_same_day_two_markets():
    evs = [e for e in _build() if e.start == date(2026, 9, 24)]
    assert len(evs) == 2 and len({e.uid for e in evs}) == 2
    assert [e.uid[:5] for e in evs] == ["gh-jp", "gh-kr"]


def test_filters_weekend_and_range():
    c = [MarketClosures("jp", (date(2026, 9, 19), date(2026, 9, 22), date(2028, 1, 4)), ())]
    evs = _build(c, {})
    assert [e.start for e in evs] == [date(2026, 9, 22)]


def test_missing_names():
    c = [MarketClosures("kr", (date(2026, 10, 9),), ())]
    for names in ({}, {"kr": {}}):
        e = _build(c, names)[0]
        assert e.title == "韓股休市：休市"
        assert "未取得名稱" in e.description
        assert "節日名稱" not in e.description


def test_deterministic_and_sorted():
    a, b = _build(), _build()
    assert a == b
    keys = [(e.start, e.uid) for e in a]
    assert [e.start for e in a] == sorted(e.start for e in a)
    # 同日 closed 在 early 前
    c = [MarketClosures("us", (date(2026, 11, 27),),
                        (EarlyClose(date(2026, 11, 27), datetime(2026, 11, 27, 13, 0, tzinfo=NY)),))]
    evs = _build(c, {})
    assert "closed" in evs[0].uid and "early" in evs[1].uid and keys


def test_early_label():
    assert us_early_close_label(date(2026, 11, 27)) == "感恩節翌日"
    assert us_early_close_label(date(2026, 12, 24)) == "聖誕夜"
    assert us_early_close_label(date(2027, 7, 2)) == "提早收盤"
    assert us_early_close_label(date(2026, 7, 3)) == "獨立紀念日前夕"
