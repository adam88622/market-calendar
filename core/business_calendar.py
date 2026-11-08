"""營業日曆（FN-006）。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from core.models import SettlementDate

_MAX_SEARCH_DAYS = 30
_POLICIES = ("next", "previous", "none")


class BusinessCalendar:
    def __init__(self, name: str, closed: dict[date, str], weekend_closed: bool = True) -> None:
        self.name = name
        self.closed = dict(closed)
        self.weekend_closed = weekend_closed

    def is_business_day(self, d: date) -> bool:
        if d in self.closed:
            return False
        if self.weekend_closed and d.weekday() >= 5:
            return False
        return True

    def closed_reason(self, d: date) -> str:
        """表內 → 名稱；週末 → "週末"；營業日 → ""。"""
        if d in self.closed:
            return self.closed[d]
        if self.weekend_closed and d.weekday() >= 5:
            return "週末"
        return ""

    def shift(self, d: date, policy: Literal["next", "previous", "none"]) -> SettlementDate:
        if policy not in _POLICIES:
            raise ValueError(f"policy 不合法: {policy!r}（須為 next/previous/none）")
        if self.is_business_day(d) or policy == "none":
            return SettlementDate(original=d, adjusted=d, reason="")
        step = timedelta(days=1 if policy == "next" else -1)
        cur = d
        for _ in range(_MAX_SEARCH_DAYS):
            cur += step
            if self.is_business_day(cur):
                return SettlementDate(
                    original=d, adjusted=cur,
                    reason=f"{self.name}休市（{self.closed_reason(d)}）",
                )
        raise RuntimeError(f"{self.name}: 從 {d} 起 {_MAX_SEARCH_DAYS} 天內找不到營業日")
