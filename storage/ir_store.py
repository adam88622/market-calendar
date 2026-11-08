"""FN-027 ir_store：台股法說會累積庫 upsert / load。"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

from core.models import IrRecord

logger = logging.getLogger("market_calendar_ics.ir_store")

_RECONCILE_RATIO = 0.5
_RECONCILE_MIN_DELETE = 3


def _iso(now_utc: datetime) -> str:
    return now_utc.isoformat()


def upsert_ir_records(
    conn: sqlite3.Connection,
    records: list[IrRecord],
    now_utc: datetime,
    today: date,
    reconcile_scopes: list[tuple[str, int, int]] | None = None,
    scope_counts: dict[tuple[str, int, int], int] | None = None,
) -> dict[str, int]:
    """upsert 法說會紀錄（MOPS 優先），並對 MOPS scope 做未來列對帳。

    回傳 {"inserted","updated","skipped_yahoo","deleted"}。
    sqlite3.Error 時 rollback 後往外丟。
    """
    stats = {"inserted": 0, "updated": 0, "skipped_yahoo": 0, "deleted": 0}
    ts = _iso(now_utc)
    today_s = today.isoformat()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        for r in records:
            d = r.event_date.isoformat()
            row = conn.execute(
                "SELECT source FROM tw_ir_events WHERE event_date=? AND stock_id=?",
                (d, r.stock_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO tw_ir_events (event_date, stock_id, name, market, event_time,"
                    " location, summary, source, first_seen_at, last_seen_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (d, r.stock_id, r.name, r.market, r.event_time, r.location,
                     r.summary, r.source, ts, ts),
                )
                stats["inserted"] += 1
            elif r.source == "Yahoo" and row["source"] == "MOPS":
                stats["skipped_yahoo"] += 1
            else:
                conn.execute(
                    "UPDATE tw_ir_events SET name=?, market=?, event_time=?, location=?,"
                    " summary=?, source=?, last_seen_at=? WHERE event_date=? AND stock_id=?",
                    (r.name, r.market, r.event_time, r.location, r.summary, r.source,
                     ts, d, r.stock_id),
                )
                stats["updated"] += 1

        if reconcile_scopes:
            if scope_counts is None:
                logger.warning("reconcile_scopes 已提供但 scope_counts 為 None，跳過對帳")
            else:
                stats["deleted"] = _reconcile(
                    conn, records, reconcile_scopes, scope_counts, today_s
                )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return stats


def _reconcile(conn, records, scopes, scope_counts, today_s) -> int:
    deleted = 0
    for scope in scopes:
        market, y, m = scope
        prefix = f"{y:04d}-{m:02d}"
        if scope not in scope_counts:
            logger.warning("scope %s 無 scope_counts，跳過對帳", scope)
            continue
        current = {
            (r.event_date.isoformat(), r.stock_id)
            for r in records
            if r.source == "MOPS" and r.market == market
            and r.event_date.isoformat()[:7] == prefix
        }
        future = [
            (row["event_date"], row["stock_id"])
            for row in conn.execute(
                "SELECT event_date, stock_id FROM tw_ir_events WHERE source='MOPS'"
                " AND market=? AND substr(event_date,1,7)=? AND event_date>=?",
                (market, prefix, today_s),
            )
        ]
        if scope_counts[scope] == 0 and future:
            logger.warning("scope %s 本次解析 0 筆但庫內有 %d 筆未來列，跳過對帳",
                           scope, len(future))
            continue
        stale = [k for k in future if k not in current]
        if (len(stale) > len(future) * _RECONCILE_RATIO
                and len(stale) >= _RECONCILE_MIN_DELETE):
            logger.warning("scope %s 待刪 %d/%d 筆比例過高，跳過對帳",
                           scope, len(stale), len(future))
            continue
        for d, sid in stale:
            conn.execute(
                "DELETE FROM tw_ir_events WHERE event_date=? AND stock_id=? AND source='MOPS'",
                (d, sid),
            )
        deleted += len(stale)
    return deleted


def load_ir_records(conn: sqlite3.Connection, start: date, end: date) -> list[IrRecord]:
    """載入 [start, end]（含）範圍內的紀錄，依日期、代號排序。"""
    rows = conn.execute(
        "SELECT * FROM tw_ir_events WHERE event_date>=? AND event_date<=?"
        " ORDER BY event_date, stock_id",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        IrRecord(
            stock_id=row["stock_id"],
            name=row["name"],
            event_date=date.fromisoformat(row["event_date"]),
            event_time=row["event_time"],
            market=row["market"],
            location=row["location"],
            summary=row["summary"],
            source=row["source"],
        )
        for row in rows
    ]
