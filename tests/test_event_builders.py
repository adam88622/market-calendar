from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.models import IrRecord, UsEarningRecord
from processors.tw_earnings import build_tw_ir_events
from processors.us_earnings import build_us_earnings_events

TZ = ZoneInfo("Asia/Taipei")


def ir(**kw):
    base = dict(stock_id="2330", name="台積電", event_date=date(2026, 11, 13),
                event_time="14:00", market="上市", location="台北", summary="（日期：115/11/13）",
                source="MOPS")
    base.update(kw)
    return IrRecord(**base)


def test_ir_timed():
    (e,) = build_tw_ir_events([ir()], TZ, 60)
    assert not e.all_day
    assert e.start == datetime(2026, 11, 13, 14, 0, tzinfo=TZ)
    assert e.end == datetime(2026, 11, 13, 15, 0, tzinfo=TZ)
    assert e.title == "2330 台積電 法說會"
    assert e.uid == "tw-earnings-2330-20261113@market-calendar-ics"
    assert e.location == "台北"
    lines = e.description.split("\n")
    assert lines == ["市場：上市", "時間：14:00", "地點：台北", "（日期：115/11/13）",
                     "來源：MOPS 公開資訊觀測站"]


def test_ir_allday_yahoo_noname():
    (e,) = build_tw_ir_events([ir(event_time=None, name="", location="", summary="",
                                  source="Yahoo")], TZ, 60)
    assert e.all_day and e.start == date(2026, 11, 13)
    assert e.title == "2330 法說會"
    assert e.description.split("\n") == ["市場：上市", "時間未公布", "來源：Yahoo 股市"]


def test_ir_deterministic():
    r = [ir(), ir(stock_id="2454", event_time=None)]
    assert build_tw_ir_events(r, TZ, 60) == build_tw_ir_events(r, TZ, 60)


def us(**kw):
    base = dict(ticker="NVDA", date_et=date(2026, 11, 25), time_et="16:20",
                session="盤後", status="確認")
    base.update(kw)
    return UsEarningRecord(**base)


def test_us_est():
    (e,) = build_us_earnings_events([us()], {"NVDA": "AI晶片/GPU"})
    assert e.all_day and e.start == date(2026, 11, 25)
    assert e.title == "NVDA 財報（盤後）"
    assert e.uid == "us-earnings-NVDA-20261125@market-calendar-ics"
    assert e.description.split("\n") == [
        "美東時間：2026-11-25 16:20 ET（盤後）", "台北時間：2026-11-26 05:20",
        "狀態：確認", "分組：AI晶片/GPU", "來源：yfinance（非官方，日期可能為預估）"]


def test_us_edt():
    (e,) = build_us_earnings_events(
        [us(date_et=date(2026, 7, 29), time_et="16:00")], {})
    assert "台北時間：2026-07-30 04:00" in e.description
    assert "分組" not in e.description


def test_us_unknown_session():
    (e,) = build_us_earnings_events([us(time_et=None, session="時段未定", status="未知")], {})
    assert "時段未定" in e.title
    assert e.description.split("\n")[:2] == ["美東時間：2026-11-25（時段未定）", "台北時間：時段未定"]
    assert "狀態" not in e.description


def test_us_no_time_after_before():
    (a,) = build_us_earnings_events([us(time_et=None)], {})
    assert "盤後通常為台北次日清晨" in a.description
    (b,) = build_us_earnings_events([us(time_et=None, session="盤前")], {})
    assert "盤前通常為台北當日晚間" in b.description


def test_us_group_line():
    (e,) = build_us_earnings_events([us(ticker="CRWV")], {"CRWV": "Neocloud"})
    assert "分組：Neocloud" in e.description
    (e2,) = build_us_earnings_events([us(ticker="CRWV")], {"CRWV": "Other"})
    assert e.description != e2.description
