"""台指結算日計算與事件產生（FN-020、FN-021、FN-036）。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

from core.business_calendar import BusinessCalendar
from core.dates import iter_months, nth_weekday
from core.models import Event, SettlementDate
from core.uid import make_uid

if TYPE_CHECKING:  # pragma: no cover
    from core.config import AppConfig, WeeklySeriesConfig

_WD = "一二三四五六日"
_POLICY_TEXT = {"next": "順延至次一營業日", "previous": "提前至前一營業日", "skip": "不產生"}


def _fmt(d: date) -> str:
    return f"{d.isoformat()}（{_WD[d.weekday()]}）"


def compute_tw_monthly_settlement(year: int, month: int, cal: BusinessCalendar) -> SettlementDate:
    """每月第三個週三；遇休市順延至次一市場營業日。"""
    return cal.shift(nth_weekday(year, month, 2, 3), "next")


def _iter_weekdays(start: date, end: date, weekday: int):
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    while d <= end:
        yield d
        d += timedelta(days=7)


def _series(
    start: date, end: date, cal: BusinessCalendar, cfg: "WeeklySeriesConfig",
    weekday: int, exclude_third: bool,
) -> list[SettlementDate]:
    if not cfg.enabled:
        return []
    policy = cfg.holiday_policy
    shifted: list[SettlementDate] = []
    for d in _iter_weekdays(start, end, weekday):
        if exclude_third and d == nth_weekday(d.year, d.month, weekday, 3):
            continue
        if policy == "skip":
            if cal.is_business_day(d):
                shifted.append(SettlementDate(d, d, ""))
            continue
        shifted.append(cal.shift(d, policy))

    # R3：同系列 adjusted 相同者只留 original 最早的一筆
    groups: dict[date, list[SettlementDate]] = {}
    for sd in shifted:
        groups.setdefault(sd.adjusted, []).append(sd)
    out: list[SettlementDate] = []
    for adj in sorted(groups):
        items = sorted(groups[adj], key=lambda s: s.original)
        first = items[0]
        if len(items) > 1:
            merged = "、".join(s.original.isoformat() for s in items[1:])
            note = f"併入原定 {merged}"
            first = SettlementDate(
                first.original, first.adjusted,
                f"{first.reason}；{note}" if first.reason else note,
            )
        out.append(first)
    out.sort(key=lambda s: s.original)
    return out


def compute_tw_weekly_settlements(
    start: date, end: date, cal: BusinessCalendar,
    wed_cfg: "WeeklySeriesConfig", fri_cfg: "WeeklySeriesConfig",
    monthly_adjusted: set[date],
) -> list[tuple[Literal["wed", "fri"], SettlementDate]]:
    """範圍內（以原定日判斷）週三／週五週選結算日。"""
    result: list[tuple[Literal["wed", "fri"], SettlementDate]] = []
    for kind, cfg, wd, excl in (("wed", wed_cfg, 2, True), ("fri", fri_cfg, 4, False)):
        for sd in _series(start, end, cal, cfg, wd, excl):
            if sd.adjusted in monthly_adjusted:
                continue
            result.append((kind, sd))  # type: ignore[arg-type]
    result.sort(key=lambda t: (t[1].original, t[0]))
    return result


def build_tw_settlement_events(
    cfg: "AppConfig", start: date, end: date, cal: BusinessCalendar
) -> list[Event]:
    events: list[Event] = []

    # 前後各多算一個月，只用來去重
    monthly_adjusted: set[date] = set()
    monthly: list[tuple[int, int, SettlementDate]] = []
    for y, m in iter_months(start - timedelta(days=31), end + timedelta(days=31)):
        sd = compute_tw_monthly_settlement(y, m, cal)
        monthly_adjusted.add(sd.adjusted)
        monthly.append((y, m, sd))

    for y, m, sd in monthly:
        if not (start <= sd.adjusted <= end):
            continue
        lines = ["規則：每月第三個週三；遇期交所休市順延至次一營業日"]
        if sd.adjusted != sd.original:
            lines.append(f"原定 {_fmt(sd.original)}→ 調整 {_fmt(sd.adjusted)}：{sd.reason}")
        lines.append("來源：規則計算（期交所休市表）")
        events.append(
            Event(
                uid=make_uid("tw-monthly-settle", f"{y}{m:02d}"),
                calendar="tw-settlement",
                title="台指期/選擇權 月結算",
                start=sd.adjusted,
                all_day=True,
                description="\n".join(lines),
                source="rule",
            )
        )

    ws = cfg.tw_settlement
    weekly = compute_tw_weekly_settlements(
        start, end, cal, ws.weekly_wed, ws.weekly_fri, monthly_adjusted
    )
    for kind, sd in weekly:
        if kind == "wed":
            title, uid_kind, series_cfg = "台指週選結算（週三）", "tw-weekly-wed-settle", ws.weekly_wed
            rule = "規則：每週三（不含當月第三個週三，該日為月結算）"
        else:
            title, uid_kind, series_cfg = "台指週選結算（週五）", "tw-weekly-fri-settle", ws.weekly_fri
            rule = "規則：每週五"
        rule += f"；遇期交所休市{_POLICY_TEXT[series_cfg.holiday_policy]}"
        lines = [rule]
        if sd.adjusted != sd.original:
            lines.append(f"原定 {_fmt(sd.original)}→ 調整 {_fmt(sd.adjusted)}：{sd.reason}")
        elif sd.reason:
            lines.append(f"註：{sd.reason}")
        lines.append("來源：規則計算（期交所休市表）")
        events.append(
            Event(
                uid=make_uid(uid_kind, sd.original.strftime("%Y%m%d")),
                calendar="tw-settlement",
                title=title,
                start=sd.adjusted,
                all_day=True,
                description="\n".join(lines),
                source="rule",
            )
        )
    return events
