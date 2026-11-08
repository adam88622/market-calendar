"""美股結算日計算與事件產生（FN-022、FN-035、FN-038）。"""
from __future__ import annotations

from datetime import date, timedelta

from core.business_calendar import BusinessCalendar
from core.dates import iter_months, nth_weekday
from core.models import Event, SettlementDate
from core.uid import make_uid

_WD = "一二三四五六日"
_QUARTER_MONTHS = {3, 6, 9, 12}


def _fmt(d: date) -> str:
    return f"{d.isoformat()}（{_WD[d.weekday()]}）"


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def compute_us_monthly_expiry(year: int, month: int, cal: BusinessCalendar) -> SettlementDate:
    """第三個週五；遇 NYSE 休市提前至前一交易日。"""
    return cal.shift(nth_weekday(year, month, 4, 3), "previous")


def compute_vix_settlement(year: int, month: int, cal: BusinessCalendar) -> SettlementDate:
    """VIX 合約月 (year, month) 的最終結算日。"""
    ny, nm = _next_month(year, month)
    spx = compute_us_monthly_expiry(ny, nm, cal)
    base = spx.adjusted
    raw = base - timedelta(days=30)
    shifted = cal.shift(raw, "previous")
    parts = []
    if spx.adjusted != spx.original:
        parts.append(f"參考 SPX 到期日 {base.isoformat()} 因 NYSE 休市提前")
    if shifted.reason:
        parts.append(shifted.reason)
    return SettlementDate(original=raw, adjusted=shifted.adjusted, reason="；".join(parts))


def build_us_settlement_events(start: date, end: date, cal: BusinessCalendar) -> list[Event]:
    """產生月選／四巫日／VIX 結算事件（以 adjusted 日期判斷範圍）。"""
    events: list[Event] = []

    for y, m in iter_months(start, end):
        sd = compute_us_monthly_expiry(y, m, cal)
        if not (start <= sd.adjusted <= end):
            continue
        quad = m in _QUARTER_MONTHS
        lines = [
            "規則：每月第三個週五；遇 NYSE 休市提前至前一交易日",
        ]
        if quad:
            lines.append("季月：股票/指數期貨與期權同日到期（四巫日），同日為月選擇權到期")
        if sd.adjusted != sd.original:
            lines.append(f"原定 {_fmt(sd.original)}→ 調整 {_fmt(sd.adjusted)}：{sd.reason}")
        lines.append("來源：規則計算（NYSE 休市表）")
        events.append(
            Event(
                uid=make_uid("us-quad-witching" if quad else "us-monthly-opex", f"{y}{m:02d}"),
                calendar="us-settlement",
                title="美股四巫日（季月期權到期）" if quad else "美股月選擇權到期",
                start=sd.adjusted,
                all_day=True,
                description="\n".join(lines),
                source="rule",
            )
        )

    # VIX 合約月：多掃前後各一個月以涵蓋跨月邊界
    for y, m in iter_months(start - timedelta(days=31), end + timedelta(days=31)):
        sd = compute_vix_settlement(y, m, cal)
        if not (start <= sd.adjusted <= end):
            continue
        ny, nm = _next_month(y, m)
        base = compute_us_monthly_expiry(ny, nm, cal).adjusted
        lines = [
            f"參考 {ny:04d}-{nm:02d} SPX 月選到期日 {base.isoformat()}，往前 30 天",
        ]
        if sd.adjusted != sd.original or sd.reason:
            lines.append(
                f"原定 {_fmt(sd.original)}→ 調整 {_fmt(sd.adjusted)}：{sd.reason}"
                if sd.adjusted != sd.original
                else f"註：{sd.reason}"
            )
        lines.append("來源：規則計算（NYSE 休市表）")
        events.append(
            Event(
                uid=make_uid("us-vix-settle", f"{y}{m:02d}"),
                calendar="us-settlement",
                title="VIX 期貨/選擇權結算",
                start=sd.adjusted,
                all_day=True,
                description="\n".join(lines),
                source="rule",
            )
        )
    return events
