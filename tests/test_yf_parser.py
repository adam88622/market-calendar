"""FN-011 classify_session / parse_yf_earnings。"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scrapers.yf_earnings import classify_session, parse_yf_earnings

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 8)


def _df(rows):
    """rows: [(datetime-ET, reported_eps)]"""
    idx = pd.DatetimeIndex([r[0] for r in rows], name="Earnings Date")
    return pd.DataFrame(
        {"EPS Estimate": [1.0] * len(rows), "Reported EPS": [r[1] for r in rows], "Surprise(%)": [np.nan] * len(rows)},
        index=idx,
    )


def dt(y, m, d, h=16, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


def test_classify_session():
    assert classify_session(dt(2026, 11, 25, 16, 20)) == ("16:20", "盤後")
    assert classify_session(dt(2026, 11, 25, 7, 0)) == ("07:00", "盤前")
    assert classify_session(dt(2026, 11, 25, 0, 0)) == (None, "時段未定")
    assert classify_session(dt(2026, 11, 25, 9, 30)) == ("09:30", "盤中")
    assert classify_session(dt(2026, 11, 25, 9, 29)) == ("09:29", "盤前")
    assert classify_session(dt(2026, 11, 25, 16, 0)) == ("16:00", "盤後")
    assert classify_session(dt(2026, 11, 25, 15, 59)) == ("15:59", "盤中")
    assert classify_session(None) == (None, "時段未定")
    assert classify_session(pd.NaT) == (None, "時段未定")


def test_classify_converts_other_tz_to_et():
    ts = datetime(2026, 11, 25, 21, 20, tzinfo=ZoneInfo("UTC"))  # = 16:20 EST
    assert classify_session(ts) == ("16:20", "盤後")


def test_reported_past_row():
    df = _df([(dt(2026, 8, 26), 2.22), (dt(2026, 11, 17), np.nan)])
    recs = parse_yf_earnings("NVDA", df, {"Earnings Date": [date(2026, 11, 17)]}, TODAY)
    assert [(r.date_et, r.status) for r in recs] == [
        (date(2026, 8, 26), "已公布"),
        (date(2026, 11, 17), "確認"),
    ]
    assert recs[1].time_et == "16:00" and recs[1].session == "盤後"


def test_status_estimate_when_two_calendar_dates():
    df = _df([(dt(2026, 11, 19), np.nan)])
    cal = {"Earnings Date": [date(2026, 11, 17), date(2026, 11, 21)]}
    assert parse_yf_earnings("X", df, cal, TODAY)[0].status == "預估"
    cal2 = {"Earnings Date": [date(2026, 11, 22), date(2026, 11, 23)]}
    assert parse_yf_earnings("X", df, cal2, TODAY)[0].status == "未知"


def test_nvda_calendar_is_taipei_date_confirmed():
    # 實測：df 2026-11-17 16:00 ET、calendar [2026-11-18]（台北日期）
    df = _df([(dt(2026, 11, 17), np.nan)])
    recs = parse_yf_earnings("NVDA", df, {"Earnings Date": [date(2026, 11, 18)]}, TODAY)
    assert len(recs) == 1
    r = recs[0]
    assert (r.date_et, r.time_et, r.session, r.status) == (date(2026, 11, 17), "16:00", "盤後", "確認")


def test_premarket_same_date_confirmed_and_taipei_range_estimate():
    df = _df([(dt(2026, 11, 12, 8, 0), np.nan)])  # 盤前：台北 11/12 21:00，同日
    assert parse_yf_earnings("NBIS", df, {"Earnings Date": [date(2026, 11, 12)]}, TODAY)[0].status == "確認"
    df2 = _df([(dt(2026, 11, 17), np.nan)])  # 台北 11/18 落在區間，ET 11/17 不在
    cal = {"Earnings Date": [date(2026, 11, 18), date(2026, 11, 20)]}
    assert parse_yf_earnings("X", df2, cal, TODAY)[0].status == "預估"


def test_status_unknown_when_calendar_differs_or_missing():
    df = _df([(dt(2026, 11, 17), np.nan)])
    assert parse_yf_earnings("X", df, {"Earnings Date": [date(2026, 11, 19)]}, TODAY)[0].status == "未知"
    assert parse_yf_earnings("X", df, {}, TODAY)[0].status == "未知"
    assert parse_yf_earnings("X", df, None, TODAY)[0].status == "未知"


def test_only_past_rows_calendar_two_dates_fills_estimate():
    df = _df([(dt(2026, 8, 26), 2.22)])
    cal = {"Earnings Date": [date(2026, 11, 17), date(2026, 11, 21)]}
    recs = parse_yf_earnings("NVDA", df, cal, TODAY)
    assert len(recs) == 2
    r = recs[-1]
    assert (r.date_et, r.time_et, r.session, r.status) == (date(2026, 11, 17), None, "時段未定", "預估")


def test_calendar_single_date_fill_is_unknown():
    recs = parse_yf_earnings("X", None, {"Earnings Date": [date(2026, 12, 1)]}, TODAY)
    assert [(r.date_et, r.status, r.session) for r in recs] == [(date(2026, 12, 1), "未知", "時段未定")]


def test_calendar_past_date_not_added():
    assert parse_yf_earnings("X", None, {"Earnings Date": [date(2026, 11, 1)]}, TODAY) == []


def test_calendar_accepts_datetime_and_scalar():
    recs = parse_yf_earnings("X", None, {"Earnings Date": datetime(2026, 12, 1, 5, 0)}, TODAY)
    assert recs[0].date_et == date(2026, 12, 1)


def test_none_and_empty_inputs():
    assert parse_yf_earnings("X", None, None, TODAY) == []
    assert parse_yf_earnings("X", pd.DataFrame(), {}, TODAY) == []
    assert parse_yf_earnings("X", pd.DataFrame(), {"Foo": 1}, TODAY) == []


def test_nat_row_skipped_and_dedup_and_sort():
    idx = pd.DatetimeIndex([dt(2026, 11, 17), pd.NaT, dt(2026, 11, 17, 7), dt(2026, 8, 1)], name="Earnings Date")
    df = pd.DataFrame({"Reported EPS": [np.nan, np.nan, np.nan, 1.0]}, index=idx)
    recs = parse_yf_earnings("X", df, None, TODAY)
    assert [r.date_et for r in recs] == [date(2026, 8, 1), date(2026, 11, 17)]
    assert recs[1].time_et == "16:00"  # df 內重複日期，先出現者優先


def test_tz_conversion_date_et():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-11-26 02:00", tz="UTC")], name="Earnings Date")  # = 11/25 21:00 ET
    df = pd.DataFrame({"Reported EPS": [np.nan]}, index=idx)
    r = parse_yf_earnings("X", df, None, TODAY)[0]
    assert r.date_et == date(2026, 11, 25) and r.session == "盤後"


def test_past_unreported_is_unknown():
    df = _df([(dt(2026, 11, 3), np.nan)])
    assert parse_yf_earnings("X", df, None, TODAY)[0].status == "未知"
