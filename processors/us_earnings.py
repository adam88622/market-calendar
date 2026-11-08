"""FN-033：美股財報事件。"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from core.models import Event, UsEarningRecord
from core.uid import make_uid

_ET = ZoneInfo("America/New_York")
_TPE = ZoneInfo("Asia/Taipei")


def _parse_hhmm(s: str | None) -> time | None:
    if not s:
        return None
    try:
        hh, mm = s.strip().split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def build_us_earnings_events(records: list[UsEarningRecord],
                             group_of: dict[str, str]) -> list[Event]:
    """全天事件，日期為 date_et；說明含美東/台北時間換算。"""
    out: list[Event] = []
    for r in records:
        session = r.session or "時段未定"
        d = r.date_et
        t = _parse_hhmm(r.time_et)
        if t is not None:
            et = datetime.combine(d, t, tzinfo=_ET)
            tpe = et.astimezone(_TPE)
            et_line = f"美東時間：{d.isoformat()} {t.strftime('%H:%M')} ET（{session}）"
            tpe_line = f"台北時間：{tpe.strftime('%Y-%m-%d %H:%M')}"
        else:
            et_line = f"美東時間：{d.isoformat()}（{session}）"
            if session == "盤後":
                tpe_line = "台北時間：時段未定（盤後通常為台北次日清晨）"
            elif session == "盤前":
                tpe_line = "台北時間：時段未定（盤前通常為台北當日晚間）"
            else:
                tpe_line = "台北時間：時段未定"
        lines = [et_line, tpe_line]
        if r.status != "未知":
            lines.append(f"狀態：{r.status}")
        grp = (group_of or {}).get(r.ticker)
        if grp:
            lines.append(f"分組：{grp}")
        lines.append("來源：yfinance（非官方，日期可能為預估）")
        out.append(Event(
            uid=make_uid("us-earnings", r.ticker, d.strftime("%Y%m%d")),
            calendar="us-earnings", title=f"{r.ticker} 財報（{session}）",
            start=d, all_day=True, description="\n".join(lines), source="yfinance",
        ))
    return out
