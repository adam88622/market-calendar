"""台股財報行事曆處理：FN-026 merge、FN-031 法定截止日、FN-032 法說事件。"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.business_calendar import BusinessCalendar
from core.config import AppConfig
from core.dates import iter_months
from core.models import Event, IrRecord
from core.uid import make_uid

_WEEKDAY = "一二三四五六日"


def _wd(d: date) -> str:
    return _WEEKDAY[d.weekday()]


# ---------------------------------------------------------------- FN-026
def merge_ir_records(mops: list[IrRecord], yahoo: list[IrRecord]) -> list[IrRecord]:
    """MOPS 全收；Yahoo 只收 MOPS 沒有的 (event_date, stock_id)。依鍵排序。"""
    merged: dict[tuple[date, str], IrRecord] = {}
    for r in mops or []:
        merged[(r.event_date, r.stock_id)] = r
    names: dict[str, str] = {}
    for r in mops or []:
        if r.name:
            names.setdefault(r.stock_id, r.name)
    for r in yahoo or []:
        key = (r.event_date, r.stock_id)
        if key in merged:
            continue
        if not r.name and r.stock_id in names:
            r = replace(r, name=names[r.stock_id])
        merged[key] = r
    return [merged[k] for k in sorted(merged)]


# ---------------------------------------------------------------- FN-031
def _parse_mmdd(s: str) -> tuple[int, int]:
    mm, dd = s.split("-")
    return int(mm), int(dd)


def build_tw_filing_events(cfg: AppConfig, start: date, end: date,
                           office_cal: BusinessCalendar) -> list[Event]:
    """季報、年報、月營收法定截止日（遇非上班日順延，以調整後日期判斷範圍）。"""
    months = iter_months(start, end)
    years = sorted({y for y, _ in months})
    f = cfg.tw_filing
    events: list[Event] = []

    def make(uid: str, title: str, orig: date) -> None:
        sd = office_cal.shift(orig, "next")
        adj = sd.adjusted
        if not (start <= adj <= end):
            return
        lines = [f"法定截止日：{orig.isoformat()}（{_wd(orig)}）"]
        if adj != orig:
            reason = office_cal.closed_reason(orig)
            if reason == "週末":
                reason = f"週{_wd(orig)}"
            lines.append(
                f"原定 {orig.isoformat()}（{_wd(orig)}）→ 調整 {adj.isoformat()}（{_wd(adj)}）"
                f"：{office_cal.name}（{reason}）"
            )
        lines.append("來源：法定申報期限（證券交易法）")
        events.append(Event(
            uid=uid, calendar="tw-earnings", title=title, start=adj, all_day=True,
            description="\n".join(lines), source="statute",
        ))

    for y in years:
        for q, s in (("1", f.q1), ("2", f.q2), ("3", f.q3)):
            m, d = _parse_mmdd(s)
            make(make_uid(f"tw-filing-q{q}", y), f"Q{q} 財報法定截止日（{y}Q{q}）",
                 date(y, m, d))
        m, d = _parse_mmdd(f.annual)
        make(make_uid("tw-filing-annual", y - 1), f"年報法定截止日（{y - 1} 年度）",
             date(y, m, d))
    for y, m in months:
        prev_m = 12 if m == 1 else m - 1
        make(make_uid("tw-revenue-deadline", f"{y:04d}{m:02d}"),
             f"月營收申報截止（申報 {prev_m} 月營收）",
             date(y, m, f.monthly_revenue_day))
    events.sort(key=lambda e: (e.start, e.uid))
    return events


# ---------------------------------------------------------------- FN-032
def _parse_hhmm(s: str | None) -> time | None:
    if not s:
        return None
    try:
        hh, mm = s.strip().split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def build_tw_ir_events(records: list[IrRecord], tz: ZoneInfo,
                       duration_minutes: int) -> list[Event]:
    """法說會 IrRecord → Event。"""
    out: list[Event] = []
    for r in records:
        title = f"{r.stock_id} {r.name} 法說會" if r.name else f"{r.stock_id} 法說會"
        t = _parse_hhmm(r.event_time)
        lines = [f"市場：{r.market}"]
        if t is not None:
            lines.append(f"時間：{t.strftime('%H:%M')}")
        else:
            lines.append("時間未公布")
        if r.location:
            lines.append(f"地點：{r.location}")
        if r.summary:
            lines.append(r.summary)
        lines.append("來源：MOPS 公開資訊觀測站" if r.source == "MOPS" else "來源：Yahoo 股市")
        uid = make_uid("tw-earnings", r.stock_id, r.event_date.strftime("%Y%m%d"))
        common = dict(uid=uid, calendar="tw-earnings", title=title,
                      description="\n".join(lines), location=r.location or "",
                      source=r.source)
        if t is not None:
            st = datetime.combine(r.event_date, t, tzinfo=tz)
            out.append(Event(start=st, end=st + timedelta(minutes=duration_minutes),
                             all_day=False, **common))
        else:
            out.append(Event(start=r.event_date, all_day=True, **common))
    return out
