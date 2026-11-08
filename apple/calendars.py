"""FN-052：選擇行事曆來源與確保行事曆存在（架構 v4 V4-5 + v4.1 R11）。

ensure_calendar 不呼叫 mark_managed；由 FN-053 在確定行事曆後呼叫。
接手掃描候選行事曆時只 fetch_events，不登記為受管。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from typing import Literal

from apple.adapter import CalendarAdapter
from apple.models import (
    CAL_TYPE_CALDAV,
    CAL_TYPE_LOCAL,
    SOURCE_CALDAV,
    SOURCE_LOCAL,
    AppleCalendarError,
    CalendarInfo,
    SourceInfo,
)
from storage.apple_map_store import get_calendar_map, upsert_calendar_map


def choose_source(
    sources: list[SourceInfo], prefer: Literal["iCloud", "local"]
) -> tuple[SourceInfo, bool]:
    """回傳 (source, fell_back)。同類多個時取 identifier 字典序最小者。"""
    icloud = sorted(
        (s for s in sources if s.source_type == SOURCE_CALDAV and s.title == "iCloud"),
        key=lambda s: s.identifier,
    )
    local = sorted(
        (s for s in sources if s.source_type == SOURCE_LOCAL), key=lambda s: s.identifier
    )
    first, second = (icloud, local) if prefer == "iCloud" else (local, icloud)
    if first:
        return first[0], False
    if second:
        return second[0], True
    raise AppleCalendarError("no iCloud or local calendar source")


def ensure_calendar(
    adapter: CalendarAdapter,
    conn: sqlite3.Connection,
    cal_id: str,
    title: str,
    prefer: Literal["iCloud", "local"],
    window: tuple[date, date],
    now_utc: datetime,
    logger: logging.Logger,
    exclude_ids: frozenset[str] = frozenset(),
) -> CalendarInfo:
    # 1. 對應表
    row = get_calendar_map(conn, cal_id)
    if row is not None:
        info = adapter.get_calendar(row.calendar_identifier)
        if info is not None and info.writable:
            if info.title != title:
                adapter.rename_calendar(info.identifier, title)
                info = adapter.get_calendar(info.identifier) or info
            upsert_calendar_map(conn, cal_id, info, now_utc)
            return info
        why = "not found (deleted by user?)" if info is None else "not writable"
        logger.warning(
            "apple %s: calendar '%s' (%s) %s; rebuilding",
            cal_id, row.title, row.calendar_identifier, why,
        )

    # 2. 接手
    same_name = [
        c
        for c in adapter.list_calendars()
        if c.title == title
        and c.writable
        and c.cal_type in (CAL_TYPE_LOCAL, CAL_TYPE_CALDAV)
        and c.identifier not in exclude_ids
    ]
    adoptable: list[CalendarInfo] = []
    for c in sorted(same_name, key=lambda c: c.identifier):
        events = adapter.fetch_events(c.identifier, window[0], window[1])
        if any(e.uid is not None for e in events):
            adoptable.append(c)
    if adoptable:
        chosen = adoptable[0]
        if len(adoptable) > 1:
            logger.warning(
                "apple %s: %d calendars named '%s' contain market-calendar-ics events; adopting %s",
                cal_id, len(adoptable), title, chosen.identifier,
            )
        upsert_calendar_map(conn, cal_id, chosen, now_utc)
        return chosen

    # 3. 新建
    src, fell_back = choose_source(adapter.list_sources(), prefer)
    if fell_back and prefer == "iCloud":
        logger.warning(
            "iCloud source not found; using '%s' (will not sync to iPhone)", src.title
        )
    if same_name:
        logger.warning(
            "calendar named '%s' exists but contains no market-calendar-ics events; "
            "creating a separate one — delete the old imported calendar to avoid duplicates",
            title,
        )
    info = adapter.create_calendar(title, src.identifier)
    upsert_calendar_map(conn, cal_id, info, now_utc)
    logger.info("apple %s: created calendar '%s' in %s", cal_id, title, src.title)
    return info
