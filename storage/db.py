"""FN-015 init_db：開啟或建立 calendar.db，建表並設定 PRAGMA。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tw_ir_events (
    event_date     TEXT NOT NULL,
    stock_id       TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    market         TEXT NOT NULL DEFAULT '未知',
    event_time     TEXT,
    location       TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL CHECK (source IN ('MOPS','Yahoo')),
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    PRIMARY KEY (event_date, stock_id)
);
CREATE INDEX IF NOT EXISTS idx_ir_date ON tw_ir_events(event_date);

CREATE TABLE IF NOT EXISTS us_earnings (
    ticker         TEXT NOT NULL,
    date_et        TEXT NOT NULL,
    time_et        TEXT,
    session        TEXT NOT NULL,
    status         TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    PRIMARY KEY (ticker, date_et)
);

CREATE TABLE IF NOT EXISTS ics_event_state (
    uid            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    sequence       INTEGER NOT NULL DEFAULT 0,
    dtstamp        TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS apple_calendar_map (
    cal_id              TEXT PRIMARY KEY,
    calendar_identifier TEXT NOT NULL,
    title               TEXT NOT NULL,
    source_title        TEXT NOT NULL,
    source_type         INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS apple_event_map (
    uid                       TEXT PRIMARY KEY,
    cal_id                    TEXT NOT NULL,
    calendar_identifier       TEXT NOT NULL,
    event_identifier          TEXT,
    calendar_item_identifier  TEXT NOT NULL,
    content_hash              TEXT NOT NULL,
    map_version               INTEGER NOT NULL,
    start_date                TEXT NOT NULL,
    synced_at                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apple_event_cal ON apple_event_map(cal_id);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """開啟（必要時建立）資料庫並建表；冪等。

    user_version > 2 時丟 RuntimeError；sqlite3.DatabaseError 往外傳。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"calendar.db user_version={version} 高於支援的 {SCHEMA_VERSION}，不支援降版"
            )
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn
