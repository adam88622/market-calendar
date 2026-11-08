import sqlite3

import pytest

from storage.db import init_db


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_creates_tables_and_version(tmp_path):
    conn = init_db(tmp_path / "sub" / "calendar.db")
    assert {"tw_ir_events", "us_earnings", "ics_event_state", "apple_calendar_map", "apple_event_map"} <= _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.row_factory is sqlite3.Row
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_idempotent(tmp_path):
    p = tmp_path / "c.db"
    init_db(p).close()
    conn = init_db(p)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()


def test_newer_version_rejected(tmp_path):
    p = tmp_path / "c.db"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 3")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        init_db(p)


def test_corrupt_db_raises(tmp_path):
    p = tmp_path / "c.db"
    p.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        init_db(p)


def test_upgrade_v1_to_v2_keeps_data(tmp_path):
    p = tmp_path / "c.db"
    c = sqlite3.connect(p)
    c.executescript(
        """
        CREATE TABLE tw_ir_events (
            event_date TEXT NOT NULL, stock_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '未知', event_time TEXT, location TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '', source TEXT NOT NULL CHECK (source IN ('MOPS','Yahoo')),
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, PRIMARY KEY (event_date, stock_id));
        INSERT INTO tw_ir_events (event_date, stock_id, source, first_seen_at, last_seen_at)
            VALUES ('2026-11-10', '2330', 'MOPS', 'a', 'b');
        PRAGMA user_version = 1;
        """
    )
    c.commit()
    c.close()
    conn = init_db(p)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT stock_id FROM tw_ir_events").fetchone()[0] == "2330"
    assert {"apple_calendar_map", "apple_event_map"} <= _tables(conn)
    conn.close()


def test_version_3_raises(tmp_path):
    p = tmp_path / "c.db"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 3")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        init_db(p)
