"""FN-028 us_store：美股財報累積庫 upsert / load。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from core.models import UsEarningRecord


def upsert_us_earnings(
    conn: sqlite3.Connection,
    ticker: str,
    records: list[UsEarningRecord],
    now_utc: datetime,
    today_et: date,
    reconcile: bool,
) -> dict[str, int]:
    """以 (ticker, date_et) upsert；reconcile 時刪除未來未再出現的列。

    回傳 {"inserted","updated","deleted"}。sqlite3.Error 時 rollback 後往外丟。
    """
    stats = {"inserted": 0, "updated": 0, "deleted": 0}
    ts = now_utc.isoformat()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        for r in records:
            d = r.date_et.isoformat()
            exists = conn.execute(
                "SELECT 1 FROM us_earnings WHERE ticker=? AND date_et=?", (ticker, d)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE us_earnings SET time_et=?, session=?, status=?, last_seen_at=?"
                    " WHERE ticker=? AND date_et=?",
                    (r.time_et, r.session, r.status, ts, ticker, d),
                )
                stats["updated"] += 1
            else:
                conn.execute(
                    "INSERT INTO us_earnings (ticker, date_et, time_et, session, status,"
                    " first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
                    (ticker, d, r.time_et, r.session, r.status, ts, ts),
                )
                stats["inserted"] += 1

        if reconcile and any(r.date_et >= today_et for r in records):
            keep = {r.date_et.isoformat() for r in records}
            rows = conn.execute(
                "SELECT date_et FROM us_earnings WHERE ticker=? AND date_et>=?",
                (ticker, today_et.isoformat()),
            ).fetchall()
            for row in rows:
                if row["date_et"] not in keep:
                    conn.execute(
                        "DELETE FROM us_earnings WHERE ticker=? AND date_et=?",
                        (ticker, row["date_et"]),
                    )
                    stats["deleted"] += 1
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return stats


def load_us_earnings(
    conn: sqlite3.Connection, tickers: tuple[str, ...], start: date, end: date
) -> list[UsEarningRecord]:
    """載入 watchlist ticker 在 [start, end]（含）的紀錄。"""
    if not tickers:
        return []
    marks = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"SELECT * FROM us_earnings WHERE ticker IN ({marks}) AND date_et>=? AND date_et<=?"
        " ORDER BY date_et, ticker",
        (*tickers, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        UsEarningRecord(
            ticker=row["ticker"],
            date_et=date.fromisoformat(row["date_et"]),
            time_et=row["time_et"],
            session=row["session"],
            status=row["status"],
        )
        for row in rows
    ]
