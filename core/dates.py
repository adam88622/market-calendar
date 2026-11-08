"""日期工具（FN-003/004/005）。"""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """某年某月第 n 個星期 X（weekday 0=週一 … 6=週日）。"""
    if not 1 <= month <= 12:
        raise ValueError(f"month 超出範圍 1-12: {month}")
    if not 0 <= weekday <= 6:
        raise ValueError(f"weekday 超出範圍 0-6: {weekday}")
    if not 1 <= n <= 5:
        raise ValueError(f"n 超出範圍 1-5: {n}")
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (n - 1) * 7
    if day > calendar.monthrange(year, month)[1]:
        raise ValueError(f"{year}-{month:02d} 沒有第 {n} 個 weekday={weekday}")
    return date(year, month, day)


def _add_months(d: date, n: int) -> date:
    """加減月份，月底會截短（例如 1/31 + 1 月 = 2/28 或 2/29）。"""
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def compute_range(today: date, months_before: int, months_after: int,
                  align_to_month: bool = True) -> tuple[date, date]:
    """行事曆範圍（含兩端）。align_to_month 時起點為月初、終點為月底。"""
    if months_before < 0 or months_after < 0:
        raise ValueError("months_before / months_after 不可為負")
    start = _add_months(today, -months_before)
    end = _add_months(today, months_after)
    if align_to_month:
        start = start.replace(day=1)
        end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
    return start, end


def iter_months(start: date, end: date) -> list[tuple[int, int]]:
    """[start, end] 涵蓋的所有 (year, month)，含頭尾；start > end 回傳空 list。"""
    if start > end:
        return []
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out
