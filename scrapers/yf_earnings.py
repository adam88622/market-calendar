"""FN-011 parse_yf_earnings / classify_session、FN-025 fetch_us_earnings。

yfinance 1.8.0 實測結構（NVDA）：
- ``Ticker.get_earnings_dates(limit)`` → DataFrame，index 名稱 "Earnings Date"，
  dtype ``datetime64[us, America/New_York]``（tz-aware ET，由新到舊），
  欄位 ``EPS Estimate``、``Reported EPS``、``Surprise(%)``（float，未公布為 NaN）。
- ``Ticker.calendar`` → dict；``'Earnings Date'`` 為 ``list[datetime.date]``（1 或 2 個），
  另有 Dividend Date、Ex-Dividend Date（date）與 Earnings/Revenue High/Low/Average。

本模組的 fetch_us_earnings 會在 worker 執行緒執行，不可碰 sqlite。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from core.models import FetchError, UsEarningRecord

logger = logging.getLogger("market_calendar.scrapers.yf_earnings")

ET = ZoneInfo("America/New_York")
TPE = ZoneInfo("Asia/Taipei")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def classify_session(ts_et: datetime | None) -> tuple[str | None, str]:
    """回傳 (time_et 'HH:MM' | None, session)。00:00 視為時間不明。"""
    if ts_et is None:
        return None, "時段未定"
    try:
        if pd.isna(ts_et):
            return None, "時段未定"
    except (TypeError, ValueError):
        pass
    if getattr(ts_et, "tzinfo", None) is not None:
        ts_et = ts_et.astimezone(ET)
    t = ts_et.time()
    if t.hour == 0 and t.minute == 0:
        return None, "時段未定"
    hhmm = f"{t.hour:02d}:{t.minute:02d}"
    if t < _OPEN:
        return hhmm, "盤前"
    if t >= _CLOSE:
        return hhmm, "盤後"
    return hhmm, "盤中"


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):  # 含 pd.Timestamp
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _calendar_dates(calendar: dict | None) -> list[date]:
    if not calendar:
        return []
    try:
        raw = calendar.get("Earnings Date")
    except AttributeError:
        return []
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    dates = [d for d in (_to_date(x) for x in raw) if d is not None]
    return sorted(dates)


def _row_timestamp(idx: Any) -> datetime | None:
    try:
        ts = pd.Timestamp(idx)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize(ET)
    else:
        ts = ts.tz_convert(ET)
    return ts.to_pydatetime()


def parse_yf_earnings(
    ticker: str,
    earnings_df: "pd.DataFrame | None",
    calendar: dict | None,
    today_et: date,
) -> list[UsEarningRecord]:
    """把 get_earnings_dates() DataFrame 與 .calendar dict 轉成 UsEarningRecord。"""
    cal_dates = _calendar_dates(calendar)
    by_date: dict[date, UsEarningRecord] = {}

    if earnings_df is not None and len(earnings_df) > 0:
        has_eps_col = "Reported EPS" in earnings_df.columns
        for idx, row in earnings_df.iterrows():
            ts = _row_timestamp(idx)
            if ts is None:
                continue
            d = ts.date()
            if d in by_date:
                continue
            time_et, session = classify_session(ts)
            reported = has_eps_col and pd.notna(row["Reported EPS"])
            if reported:
                status = "已公布"
            elif d >= today_et:
                # .calendar 的日期實測為本機時區（Asia/Taipei）換算：
                # 盤後 16:00 ET 的 calendar 日期 = 美東日期 + 1。兩種日期都接受。
                cands = {d, ts.astimezone(TPE).date()}
                if len(cal_dates) == 1:
                    status = "確認" if cal_dates[0] in cands else "未知"
                elif len(cal_dates) >= 2:
                    lo, hi = cal_dates[0], cal_dates[-1]
                    status = "預估" if any(lo <= c <= hi for c in cands) else "未知"
                else:
                    status = "未知"
            else:
                status = "未知"
            by_date[d] = UsEarningRecord(
                ticker=ticker, date_et=d, time_et=time_et, session=session, status=status
            )

    has_future = any(d >= today_et for d in by_date)
    if not has_future and cal_dates:
        d = cal_dates[0]
        if d >= today_et and d not in by_date:
            logger.debug(
                "%s: no future df row; using calendar date %s as date_et "
                "(calendar dates may be Asia/Taipei dates, possibly +1 day vs ET)",
                ticker, d,
            )
            by_date[d] = UsEarningRecord(
                ticker=ticker,
                date_et=d,
                time_et=None,
                session="時段未定",
                status="預估" if len(cal_dates) >= 2 else "未知",
            )

    return [by_date[d] for d in sorted(by_date)]


def fetch_us_earnings(ticker: str, limit: int, today_et: date) -> list[UsEarningRecord]:
    """抓單一 ticker（worker 執行緒內執行，不碰 DB）。df 與 calendar 皆失敗才丟 FetchError。"""
    try:
        t = yf.Ticker(ticker)
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"{ticker}: yf.Ticker failed: {e!r}") from e

    df = None
    cal = None
    df_err: Exception | None = None
    cal_err: Exception | None = None
    try:
        df = t.get_earnings_dates(limit=limit)
    except Exception as e:  # noqa: BLE001
        df_err = e
        logger.debug("%s get_earnings_dates failed: %r", ticker, e)
    try:
        cal = t.calendar
    except Exception as e:  # noqa: BLE001
        cal_err = e
        logger.debug("%s calendar failed: %r", ticker, e)

    if df_err is not None and cal_err is not None:
        raise FetchError(
            f"{ticker}: get_earnings_dates={df_err!r}; calendar={cal_err!r}"
        )
    return parse_yf_earnings(ticker, df, cal if isinstance(cal, dict) else None, today_et)
