"""FN-013 VALARM TRIGGER 相對時長。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def format_alarm_trigger(
    start: date | datetime, all_day: bool, days_before: int, at: time, tz: ZoneInfo
) -> str:
    if all_day:
        base_day = start.date() if isinstance(start, datetime) else start
        base = datetime(base_day.year, base_day.month, base_day.day)
    else:
        if not isinstance(start, datetime) or start.tzinfo is None:
            raise ValueError("timed event start must be a tz-aware datetime")
        base = start.astimezone(tz).replace(tzinfo=None, microsecond=0)
    remind_day = base.date() - timedelta(days=days_before)
    remind = datetime.combine(remind_day, time(at.hour, at.minute))
    delta = remind - base
    total_min = int(delta.total_seconds() // 60)
    sign = "-" if total_min < 0 else ""
    total_min = abs(total_min)
    h, m = divmod(total_min, 60)
    if total_min == 0:
        return "PT0M"
    out = "PT"
    if h:
        out += f"{h}H"
    if m:
        out += f"{m}M"
    return sign + out
