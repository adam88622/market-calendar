"""FN-050 apple_map_store：apple_calendar_map / apple_event_map 的讀寫（架構 v4）。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from apple.models import CalendarInfo, CalendarMapRow, EventMapRow


def _cal_row(r: sqlite3.Row) -> CalendarMapRow:
    return CalendarMapRow(
        cal_id=r["cal_id"],
        calendar_identifier=r["calendar_identifier"],
        title=r["title"],
        source_title=r["source_title"],
        source_type=r["source_type"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _event_row(r: sqlite3.Row) -> EventMapRow:
    return EventMapRow(
        uid=r["uid"],
        cal_id=r["cal_id"],
        calendar_identifier=r["calendar_identifier"],
        event_identifier=r["event_identifier"],
        calendar_item_identifier=r["calendar_item_identifier"],
        content_hash=r["content_hash"],
        map_version=r["map_version"],
        start_date=date.fromisoformat(r["start_date"]),
        synced_at=r["synced_at"],
    )


def get_calendar_map(conn: sqlite3.Connection, cal_id: str) -> CalendarMapRow | None:
    row = conn.execute("SELECT * FROM apple_calendar_map WHERE cal_id=?", (cal_id,)).fetchone()
    return _cal_row(row) if row else None


def upsert_calendar_map(
    conn: sqlite3.Connection, cal_id: str, info: CalendarInfo, now_utc: datetime
) -> None:
    """寫入或更新行事曆對應；已存在時保留原 created_at。自己 commit。"""
    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise ValueError("now_utc 必須是 UTC aware datetime")
    ts = now_utc.isoformat()
    try:
        old = conn.execute(
            "SELECT created_at FROM apple_calendar_map WHERE cal_id=?", (cal_id,)
        ).fetchone()
        created = old["created_at"] if old else ts
        if not conn.in_transaction:
            conn.execute("BEGIN")
        conn.execute(
            "INSERT OR REPLACE INTO apple_calendar_map"
            " (cal_id, calendar_identifier, title, source_title, source_type, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cal_id, info.identifier, info.title, info.source_title, info.source_type, created, ts),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def load_event_map(conn: sqlite3.Connection, cal_id: str) -> dict[str, EventMapRow]:
    rows = conn.execute("SELECT * FROM apple_event_map WHERE cal_id=?", (cal_id,)).fetchall()
    return {r["uid"]: _event_row(r) for r in rows}


def apply_event_map_changes(
    conn: sqlite3.Connection,
    cal_id: str,
    calendar_identifier: str,
    upserts: list[EventMapRow],
    drops: list[str],
) -> None:
    """單一 transaction：先刪 drops（僅限 cal_id 相符），再 INSERT OR REPLACE upserts。

    upserts 的 cal_id / calendar_identifier 一律以參數為準。sqlite3.Error 時 rollback 並往外丟。
    """
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        for uid in drops:
            conn.execute("DELETE FROM apple_event_map WHERE uid=? AND cal_id=?", (uid, cal_id))
        for r in upserts:
            conn.execute(
                "INSERT OR REPLACE INTO apple_event_map"
                " (uid, cal_id, calendar_identifier, event_identifier, calendar_item_identifier,"
                "  content_hash, map_version, start_date, synced_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.uid,
                    cal_id,
                    calendar_identifier,
                    r.event_identifier,
                    r.calendar_item_identifier,
                    r.content_hash,
                    r.map_version,
                    r.start_date.isoformat(),
                    r.synced_at,
                ),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def purge_stale_event_map(conn: sqlite3.Connection, cal_id: str, calendar_identifier: str) -> int:
    """刪掉該 cal_id 下 calendar_identifier 不一致的列，回傳刪除列數。"""
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        cur = conn.execute(
            "DELETE FROM apple_event_map WHERE cal_id=? AND calendar_identifier<>?",
            (cal_id, calendar_identifier),
        )
        n = cur.rowcount
        conn.commit()
        return n
    except sqlite3.Error:
        conn.rollback()
        raise
