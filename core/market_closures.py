"""FN-062：以 pandas_market_calendars 計算各市場休市日與提早收盤。

任何異常都往外丟，讓整個 global-holidays 行事曆失敗，避免只產出部分市場後
F-008 把其他市場的未來事件刪掉。
"""
from __future__ import annotations

import importlib
from datetime import date, timedelta
from types import ModuleType

from core.models import EarlyClose, MarketClosures, MarketSpec

_MAX_CLOSED_RATIO = 0.25
_COVERAGE_MIN_WEEKDAYS = 60


def _weekdays(lo: date, hi: date) -> int:
    n = 0
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def compute_market_closures(spec: MarketSpec, start: date, end: date, *,
                            mcal_module: ModuleType | None = None) -> MarketClosures:
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    mcal = mcal_module or importlib.import_module("pandas_market_calendars")
    cal = mcal.get_calendar(spec.mcal_name)
    valid = {
        ts.date()
        for ts in cal.valid_days(start_date=start.isoformat(), end_date=end.isoformat())
    }

    closed: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in valid:
            closed.append(d)
        d += timedelta(days=1)

    total_weekdays = _weekdays(start, end)
    if not closed:
        raise RuntimeError(
            f"{spec.code}: no closures in {start}..{end}; calendar data suspicious"
        )
    if len(closed) > total_weekdays * _MAX_CLOSED_RATIO:
        raise RuntimeError(
            f"{spec.code}: {len(closed)} closures out of {total_weekdays} weekdays in "
            f"{start}..{end}; calendar data suspicious"
        )
    # R17 資料涵蓋防護：每個區間內平日數 >= 60 的年份至少要有 1 個休市日
    closed_years = {c.year for c in closed}
    for year in range(start.year, end.year + 1):
        lo = max(start, date(year, 1, 1))
        hi = min(end, date(year, 12, 31))
        if _weekdays(lo, hi) >= _COVERAGE_MIN_WEEKDAYS and year not in closed_years:
            raise RuntimeError(
                f"{spec.code}: no closures in {year} within {start}..{end}; "
                "calendar data may not cover this year"
            )

    early: list[EarlyClose] = []
    if spec.early_closes:
        sched = cal.schedule(start_date=start.isoformat(), end_date=end.isoformat())
        ec = cal.early_closes(sched)
        for idx, row in ec.iterrows():
            day = idx.date()
            if day < start or day > end or day.weekday() >= 5:
                continue
            close_local = row["market_close"].tz_convert(spec.tz).to_pydatetime()
            early.append(EarlyClose(day=day, close_local=close_local))
        early.sort(key=lambda e: e.day)

    return MarketClosures(market=spec.code, closed=tuple(sorted(closed)), early=tuple(early))
