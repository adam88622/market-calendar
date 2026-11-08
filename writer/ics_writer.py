"""FN-029 render_event_lines / event_content_hash、FN-037 serialize_calendar、FN-039 generate_calendar。"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.config import ReminderConfig
from core.models import CalendarId, Event, EventState, RunContext
from storage.state_store import commit_event_states, resolve_event_states
from writer.alarm import format_alarm_trigger
from writer.atomic import write_atomic
from writer.ics_text import escape_text, fold_line

_DT_FMT = "%Y%m%dT%H%M%S"


def render_event_lines(
    event: Event, reminder: ReminderConfig, tz: ZoneInfo, default_duration: timedelta
) -> list[str]:
    """Event → 未折行 content lines（不含 DTSTAMP/SEQUENCE/LAST-MODIFIED）。"""
    tzid = tz.key
    lines = [f"UID:{event.uid}"]
    if event.all_day:
        start: date = event.start  # type: ignore[assignment]
        end_incl: date = event.end if event.end is not None else start  # type: ignore[assignment]
        if end_incl < start:
            raise ValueError(f"end earlier than start: {event.uid}")
        lines.append(f"DTSTART;VALUE=DATE:{start:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{end_incl + timedelta(days=1):%Y%m%d}")
    else:
        start_dt = event.start.astimezone(tz)  # type: ignore[union-attr]
        if event.end is not None:
            end_dt = event.end.astimezone(tz)  # type: ignore[union-attr]
            if end_dt < start_dt:
                raise ValueError(f"end earlier than start: {event.uid}")
        else:
            end_dt = start_dt + default_duration
        lines.append(f"DTSTART;TZID={tzid}:{start_dt.strftime(_DT_FMT)}")
        lines.append(f"DTEND;TZID={tzid}:{end_dt.strftime(_DT_FMT)}")
    lines.append(f"SUMMARY:{escape_text(event.title)}")
    if event.description:
        lines.append(f"DESCRIPTION:{escape_text(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{escape_text(event.location)}")
    lines.append("TRANSP:TRANSPARENT")
    trigger = format_alarm_trigger(
        event.start, event.all_day, reminder.days_before, reminder.at, tz
    )
    lines += [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape_text(event.title)}",
        f"TRIGGER:{trigger}",
        "END:VALARM",
    ]
    return lines


def event_content_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _sort_key(event: Event, tz_name: str) -> tuple[str, str, str]:
    if event.all_day:
        return (f"{event.start:%Y%m%d}", "", event.uid)
    local = event.start.astimezone(ZoneInfo(tz_name))  # type: ignore[union-attr]
    return (local.strftime("%Y%m%d"), local.strftime("%H:%M"), event.uid)


def serialize_calendar(
    cal_name: str,
    tz_name: str,
    rendered: list[tuple[Event, list[str]]],
    states: dict[str, EventState],
) -> bytes:
    uids = [ev.uid for ev, _ in rendered]
    if len(set(uids)) != len(uids):
        dup = sorted({u for u in uids if uids.count(u) > 1})
        raise ValueError(f"duplicate UID: {dup}")
    for u in uids:
        if u not in states:
            raise ValueError(f"missing state for UID: {u}")

    out: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//market-calendar-ics//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(cal_name)}",
        f"X-WR-TIMEZONE:{tz_name}",
        "BEGIN:VTIMEZONE",
        f"TZID:{tz_name}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for ev, lines in sorted(rendered, key=lambda p: _sort_key(p[0], tz_name)):
        st = states[ev.uid]
        out.append("BEGIN:VEVENT")
        out.append(lines[0])
        out.append(f"DTSTAMP:{st.dtstamp}")
        out.append(f"SEQUENCE:{st.sequence}")
        out.append(f"LAST-MODIFIED:{st.dtstamp}")
        out.extend(lines[1:])
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return ("".join(fold_line(l) + "\r\n" for l in out)).encode("utf-8")


def generate_calendar(ctx: RunContext, cal_id: CalendarId, events: list[Event]) -> Path:
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    duration = timedelta(minutes=cfg.tw_ir.default_duration_minutes)
    meta = cfg.calendars[cal_id]

    rendered = [(ev, render_event_lines(ev, cfg.reminder, tz, duration)) for ev in events]
    hashes = {ev.uid: event_content_hash(lines) for ev, lines in rendered}
    states = resolve_event_states(ctx.conn, cal_id, hashes, ctx.now_utc)
    data = serialize_calendar(meta.name, cfg.timezone, rendered, states)
    path = Path(cfg.paths.output_dir) / meta.filename
    write_atomic(path, data)
    commit_event_states(ctx.conn, cal_id, states, ctx.now_utc)
    changed = sum(1 for s in states.values() if s.changed)
    ctx.logger.info("%s: %d events, %d changed → %s", cal_id, len(rendered), changed, path)
    return path
