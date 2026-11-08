"""台股（期交所）與 NYSE 營業日曆建立（FN-018、FN-019）。"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from core.business_calendar import BusinessCalendar

if TYPE_CHECKING:  # pragma: no cover
    from core.config import AppConfig


def build_taifex_calendars(
    cfg: "AppConfig", range_start: date, range_end: date
) -> tuple[BusinessCalendar, BusinessCalendar, list[int]]:
    """回傳 (market_cal 含全部 kind, office_cal 只含 kind=holiday, missing_years)。

    同一日期重複時以 taifex_extra_holidays 為準。
    """
    merged: dict[date, tuple[str, str]] = {}
    for entry in cfg.taifex_holidays:
        merged[entry.date] = (entry.name, entry.kind)
    for entry in cfg.taifex_extra_holidays:
        merged[entry.date] = (entry.name, entry.kind)

    market_closed = {d: name for d, (name, _kind) in merged.items()}
    office_closed = {d: name for d, (name, kind) in merged.items() if kind == "holiday"}

    years_with_entries = {d.year for d in merged}
    missing = [
        y for y in range(range_start.year, range_end.year + 1) if y not in years_with_entries
    ]
    return (
        BusinessCalendar("期交所", market_closed),
        BusinessCalendar("非上班日", office_closed),
        missing,
    )


def _clean_holiday_name(name: str) -> str:
    name = re.sub(r"\s+Starting at \d{4}$", "", name)
    name = re.sub(r"\s+\d{4}\+$", "", name)
    return name.strip()


def build_nyse_calendar(start: date, end: date) -> BusinessCalendar:
    """以 pandas_market_calendars 建 NYSE 營業日曆（區間 [start-60, end+60]）。

    套件 import 或計算失敗時例外直接往外傳。
    """
    import pandas_market_calendars as mcal

    lo = start - timedelta(days=60)
    hi = end + timedelta(days=60)
    nyse = mcal.get_calendar("NYSE")
    valid = {ts.date() for ts in nyse.valid_days(start_date=lo.isoformat(), end_date=hi.isoformat())}

    names: dict[date, str] = {}
    try:
        series = nyse.regular_holidays.holidays(
            start=lo.isoformat(), end=hi.isoformat(), return_name=True
        )
        for ts, nm in series.items():
            names[ts.date()] = _clean_holiday_name(str(nm))
    except Exception:  # 名稱只是補充資訊，失敗不影響休市判斷
        names = {}

    closed: dict[date, str] = {}
    d = lo
    while d <= hi:
        if d.weekday() < 5 and d not in valid:
            closed[d] = names.get(d, "NYSE 休市")
        d += timedelta(days=1)
    return BusinessCalendar("NYSE", closed)
