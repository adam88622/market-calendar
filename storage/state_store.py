"""FN-030 state_store：ICS 事件 SEQUENCE / DTSTAMP 狀態。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from core.models import CalendarId, EventState


def _check_utc(now_utc: datetime) -> None:
    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise ValueError("now_utc 必須是 UTC aware datetime")


def resolve_event_states(
    conn: sqlite3.Connection,
    calendar: CalendarId,
    hashes: dict[str, str],
    now_utc: datetime,
) -> dict[str, EventState]:
    """唯讀：依 hash 決定每個 uid 的 sequence / dtstamp。"""
    _check_utc(now_utc)
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    result: dict[str, EventState] = {}
    for uid, h in hashes.items():
        row = conn.execute(
            "SELECT content_hash, sequence, dtstamp FROM ics_event_state WHERE uid=?",
            (uid,),
        ).fetchone()
        if row is None:
            result[uid] = EventState(uid, 0, stamp, h, True)
        elif row["content_hash"] == h:
            result[uid] = EventState(uid, row["sequence"], row["dtstamp"], h, False)
        else:
            result[uid] = EventState(uid, row["sequence"] + 1, stamp, h, True)
    return result


def commit_event_states(
    conn: sqlite3.Connection,
    calendar: CalendarId,
    states: dict[str, EventState],
    now_utc: datetime,
) -> None:
    """只寫入 changed=True 的列，單一 transaction。"""
    _check_utc(now_utc)
    ts = now_utc.isoformat()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        for uid, s in states.items():
            if not s.changed:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO ics_event_state"
                " (uid, calendar, content_hash, sequence, dtstamp, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (uid, calendar, s.content_hash, s.sequence, s.dtstamp, ts),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
