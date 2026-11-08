"""FN-049：Event → EventPayload 純函式對應（架構 v4 V4-5）。"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apple.models import URL_SCHEME, EventPayload
from core.config import AppConfig
from core.models import UID_SUFFIX, Event
from writer.alarm import format_alarm_trigger
from writer.ics_writer import event_content_hash, render_event_lines

_UID_BODY_RE = re.compile(r"^[A-Za-z0-9-]+$")
_TRIGGER_RE = re.compile(r"^(-?)PT(?:(\d+)H)?(?:(\d+)M)?$")


def url_for_uid(uid: str) -> str:
    return f"{URL_SCHEME}{uid}"


def uid_from_url(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.startswith(URL_SCHEME):
        return None
    uid = url[len(URL_SCHEME):]
    if not uid.endswith(UID_SUFFIX):
        return None
    body = uid[: -len(UID_SUFFIX)]
    if not body or not _UID_BODY_RE.match(body):
        return None
    return uid


def trigger_to_seconds(trigger: str) -> int:
    m = _TRIGGER_RE.match(trigger) if isinstance(trigger, str) else None
    if not m or (m.group(2) is None and m.group(3) is None):
        raise ValueError(f"invalid trigger: {trigger!r}")
    secs = int(m.group(2) or 0) * 3600 + int(m.group(3) or 0) * 60
    return -secs if m.group(1) else secs


def build_payload(event: Event, cfg: AppConfig) -> EventPayload:
    tz = ZoneInfo(cfg.timezone)
    duration = timedelta(minutes=cfg.tw_ir.default_duration_minutes)
    lines = render_event_lines(event, cfg.reminder, tz, duration)
    content_hash = event_content_hash(lines)
    start: date | datetime
    end: date | datetime
    if event.all_day:
        start = event.start  # type: ignore[assignment]
        end = event.end if event.end is not None else start  # type: ignore[assignment]
        start_date = start  # type: ignore[assignment]
    else:
        start = event.start.astimezone(tz)  # type: ignore[union-attr]
        end = (event.end.astimezone(tz) if event.end is not None else start + duration)  # type: ignore[union-attr]
        start_date = start.date()
    trigger = format_alarm_trigger(
        event.start, event.all_day, cfg.reminder.days_before, cfg.reminder.at, tz
    )
    return EventPayload(
        uid=event.uid,
        cal_id=event.calendar,
        title=event.title,
        all_day=event.all_day,
        start=start,
        end=end,
        notes=event.description,
        location=event.location,
        url=url_for_uid(event.uid),
        alarm_offset_sec=trigger_to_seconds(trigger),
        content_hash=content_hash,
        start_date=start_date,
    )


def build_payloads(events: list[Event], cfg: AppConfig) -> list[EventPayload]:
    seen: set[str] = set()
    out: list[EventPayload] = []
    for ev in events:
        if ev.uid in seen:
            raise ValueError(f"duplicate uid: {ev.uid}")
        seen.add(ev.uid)
        out.append(build_payload(ev, cfg))
    return out
