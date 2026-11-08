from datetime import datetime, timedelta, timezone

import pytest

from storage.db import init_db
from storage.state_store import commit_event_states, resolve_event_states

CAL = "tw-earnings"
UID = "tw-earnings-2330-20261113@market-calendar-ics"
T1 = datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 2, 2, 30, 5, tzinfo=timezone.utc)
T3 = datetime(2026, 11, 3, 4, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "c.db")
    yield c
    c.close()


def test_first_seq_zero(conn):
    s = resolve_event_states(conn, CAL, {UID: "h1"}, T1)[UID]
    assert (s.sequence, s.dtstamp, s.changed) == (0, "20261101T010000Z", True)


def test_same_hash_keeps_dtstamp(conn):
    st = resolve_event_states(conn, CAL, {UID: "h1"}, T1)
    commit_event_states(conn, CAL, st, T1)
    s = resolve_event_states(conn, CAL, {UID: "h1"}, T2)[UID]
    assert s.dtstamp == "20261101T010000Z" and s.sequence == 0 and not s.changed


def test_changed_hash_bumps_seq(conn):
    commit_event_states(conn, CAL, resolve_event_states(conn, CAL, {UID: "h1"}, T1), T1)
    st = resolve_event_states(conn, CAL, {UID: "h2"}, T2)
    assert st[UID].sequence == 1 and st[UID].dtstamp == "20261102T023005Z" and st[UID].changed
    commit_event_states(conn, CAL, st, T2)
    s = resolve_event_states(conn, CAL, {UID: "h3"}, T3)[UID]
    assert s.sequence == 2


def test_resolve_readonly(conn):
    resolve_event_states(conn, CAL, {UID: "h1"}, T1)
    assert conn.execute("SELECT COUNT(*) FROM ics_event_state").fetchone()[0] == 0


def test_commit_only_changed(conn):
    st = resolve_event_states(conn, CAL, {UID: "h1"}, T1)
    commit_event_states(conn, CAL, st, T1)
    st2 = resolve_event_states(conn, CAL, {UID: "h1"}, T2)
    conn.execute("UPDATE ics_event_state SET updated_at='x'")
    commit_event_states(conn, CAL, st2, T2)
    assert conn.execute("SELECT updated_at FROM ics_event_state").fetchone()[0] == "x"


def test_non_utc_rejected(conn):
    with pytest.raises(ValueError):
        resolve_event_states(conn, CAL, {UID: "h"}, datetime(2026, 11, 1))
    tz8 = timezone(timedelta(hours=8))
    with pytest.raises(ValueError):
        resolve_event_states(conn, CAL, {UID: "h"}, datetime(2026, 11, 1, tzinfo=tz8))
    with pytest.raises(ValueError):
        commit_event_states(conn, CAL, {}, datetime(2026, 11, 1))
