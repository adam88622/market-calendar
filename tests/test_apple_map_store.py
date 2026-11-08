from datetime import date, datetime, timezone

import pytest

import sqlite3

from apple.models import APPLE_MAP_VERSION, CalendarInfo, EventMapRow
from storage.apple_map_store import (
    apply_event_map_changes,
    get_calendar_map,
    load_event_map,
    purge_stale_event_map,
    upsert_calendar_map,
)
from storage.db import init_db

NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    yield c
    c.close()


def row(uid, cal="tw-earnings", ident="C1", h="h1", item="i1", d=date(2026, 9, 1)):
    return EventMapRow(uid, cal, ident, None, item, h, APPLE_MAP_VERSION, d, "2026-08-01T00:00:00+00:00")


def info(ident="C1", title="T"):
    return CalendarInfo(ident, title, "s1", "iCloud", 2, 1, True)


def test_calendar_map_roundtrip_and_keep_created(conn):
    assert get_calendar_map(conn, "tw-earnings") is None
    upsert_calendar_map(conn, "tw-earnings", info(), NOW)
    m = get_calendar_map(conn, "tw-earnings")
    assert (m.calendar_identifier, m.title, m.source_title, m.source_type) == ("C1", "T", "iCloud", 2)
    assert m.created_at == m.updated_at == NOW.isoformat()
    later = datetime(2026, 8, 2, tzinfo=timezone.utc)
    upsert_calendar_map(conn, "tw-earnings", info("C2", "T2"), later)
    m = get_calendar_map(conn, "tw-earnings")
    assert m.calendar_identifier == "C2" and m.title == "T2"
    assert m.created_at == NOW.isoformat() and m.updated_at == later.isoformat()


def test_calendar_map_requires_utc(conn):
    with pytest.raises(ValueError):
        upsert_calendar_map(conn, "x", info(), datetime(2026, 8, 1))


def test_upsert_roundtrip_and_overwrite(conn):
    r = row("u1")
    apply_event_map_changes(conn, "tw-earnings", "C1", [r], [])
    assert load_event_map(conn, "tw-earnings") == {"u1": r}
    r2 = row("u1", h="h2", item="i2", d=date(2026, 10, 2))
    apply_event_map_changes(conn, "tw-earnings", "C1", [r2], [])
    got = load_event_map(conn, "tw-earnings")
    assert got == {"u1": r2}


def test_drops_only_same_cal(conn):
    apply_event_map_changes(conn, "a", "C1", [row("u1", cal="a")], [])
    apply_event_map_changes(conn, "b", "C2", [row("u2", cal="b", ident="C2")], [])
    apply_event_map_changes(conn, "a", "C1", [], ["u1", "u2"])
    assert load_event_map(conn, "a") == {}
    assert set(load_event_map(conn, "b")) == {"u2"}


def test_purge_only_mismatched(conn):
    apply_event_map_changes(conn, "a", "C1", [row("u1", cal="a")], [])
    apply_event_map_changes(conn, "a", "C1", [row("u2", cal="a")], [])
    conn.execute("UPDATE apple_event_map SET calendar_identifier='OLD' WHERE uid='u2'")
    conn.commit()
    apply_event_map_changes(conn, "b", "X", [row("u3", cal="b", ident="X")], [])
    assert purge_stale_event_map(conn, "a", "C1") == 1
    assert set(load_event_map(conn, "a")) == {"u1"}
    assert set(load_event_map(conn, "b")) == {"u3"}
    assert purge_stale_event_map(conn, "a", "C1") == 0


def test_rollback_on_error(conn):
    apply_event_map_changes(conn, "a", "C1", [row("u0", cal="a")], [])
    bad = row("u2", cal="a", h=None)  # NOT NULL 違反
    with pytest.raises(sqlite3.IntegrityError):
        apply_event_map_changes(conn, "a", "C1", [row("u1", cal="a"), bad], ["u0"])
    assert set(load_event_map(conn, "a")) == {"u0"}
    assert not conn.in_transaction
